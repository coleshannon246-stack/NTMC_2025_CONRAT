import tkinter as tk          # GUI
import json                   # Load Connections data
import random                 # Shuffle groups/tiles
from pathlib import Path      # Safer filesystem paths
import os

# ---------------------------------------------------------
# Optional: set LSL environment variables (if using later)
# ---------------------------------------------------------
os.environ["LSL_LIB_PATH"] = "labstreaminglayer/build/LSL/liblsl/liblsl.dylib"
os.environ["DYLD_LIBRARY_PATH"] = (
    "labstreaminglayer/build/LSL/liblsl/" + os.environ.get("DYLD_LIBRARY_PATH", "")
)
try:
    import pylsl
except ImportError:
    pylsl = None


# ======================================================================
# 🎯 GLOBAL VISUAL CONSTANTS (AESTHETIC TUNING)
# ======================================================================
APP_TITLE = "Connections + RAT Experiment"
WINDOW_SIZE = "900x900"

FONT_TITLE = ("Arial", 28, "bold")
FONT_SUBTITLE = ("Arial", 18)
FONT_TILE = ("Arial", 18, "bold")
FONT_STATUS = ("Arial", 14, "bold")
FONT_LABEL = ("Arial", 12)

# RAT fonts
FONT_RAT_PROMPT   = ("Arial", 24)          # normal weight during think phase
FONT_RAT_TIMER    = ("Arial", 16)
FONT_RAT_ENTRY    = ("Arial", 18)
FONT_RAT_FEEDBACK = ("Arial", 16)
FONT_RAT_ANSWER   = ("Arial", 26, "bold")  # answer is slightly larger + bold


# ======================================================================
# 📂 LOAD CONNECTIONS PUZZLE DATA (LEVEL 2)
# ======================================================================
script_dir = Path(__file__).resolve().parent
level2_json_path = script_dir / "connections_level2.json"

if not level2_json_path.exists():
    raise FileNotFoundError(
        f"{level2_json_path} not found. Please run the filter script first."
    )

with open(level2_json_path, "r", encoding="utf-8") as f:
    all_groups = json.load(f)  # list of dicts: {"group": "...", "members": ["...", "...", "...", "..."]}


# ======================================================================
# 🧠 RAT DATA (10 PROMPTS + ANSWERS)
# ======================================================================
RAT_PROMPTS = [
    "cottage / swiss / cake",
    "cream / skate / water",
    "rocking / wheel / high",
    "show / life / row",
    "fountain / baking / pop",
    "duck / fold / dollar",
    "sleeping / bean / trash",
    "dew / comb / bee",
    "night / wrist / stop",
    "loser / throat / spot",
]
RAT_ANSWERS = [
    "cheese",
    "ice",
    "chair",
    "boat",
    "soda",
    "bill",
    "bag",
    "honey",
    "watch",
    "sore",
]


# ======================================================================
# 🧭 EXPERIMENT APP (ORCHESTRATES ALL SCREENS)
# ======================================================================
class ExperimentApp:
    """
    Flow:
      Player ID → Intro → Connections Instructions
      → 15× Connections puzzles (each ends after first correct group)
         • After each: 15s Rest + spontaneity rating (1–5)
      → RAT Instructions → 10× RAT items (10s think → reveal → Y/N → 15s rest)
      → Congratulations → Post-Questions (5× "test" shown one-at-a-time) → Final thanks/exit

    Master Skip '.':
       - On message screens: go next
       - During Connections puzzle: treat as “puzzle completed” (advance into rest)
       - During Connections REST: skip rest immediately (rating NoResponse if not chosen)
       - During RAT: delegated to RAT.force_advance()
       - During Post-Questions: skips current question (records NoResponse) and advances
    """

    def __init__(self, root):
        self.root = root
        self.root.geometry(WINDOW_SIZE)
        self.root.title(APP_TITLE)

        # LSL outlet (if pylsl available)
        self.outlet = None
        if pylsl is not None:
            try:
                info = pylsl.StreamInfo(
                    name="ConnectionsRAT_Markers",
                    type="Markers",
                    channel_count=1,
                    nominal_srate=0,           # irregular markers
                    channel_format='string',
                    source_id=f"connrat_markers_{os.getpid()}"
                )
                self.outlet = pylsl.StreamOutlet(info)
            except Exception as e:
                print("WARNING: Could not initialize LSL outlet:", e)
                self.outlet = None

        # Player ID (captured on first screen)
        self.player_id = None

        # Main container; all screens render inside this frame
        self.main_frame = tk.Frame(root)
        self.main_frame.pack(expand=True, fill="both")

        # Track how many Connections puzzles have been completed in this session
        self.connections_puzzles_completed = 0  # ⭐ 15-puzzle cycle

        # Store per-puzzle spontaneity ratings (1..5 or None if skipped/no response)
        self.connections_spontaneity = []

        # Remember last navigation callback (so '.' can advance intro/instructions screens etc.)
        self._last_on_next = None

        # Bind master skip '.' globally (works everywhere)
        self.root.bind("<KeyPress-period>", self._master_skip)

        # ---------- Connections REST state/timer members ----------
        self._conn_rest_active = False            # True while the rest UI is on screen
        self._conn_rest_remaining = 0
        self._conn_rest_after_id = None          # after() handle for countdown ticks
        self._conn_rest_frame = None
        self._conn_rest_label = None
        self._conn_rest_buttons = []             # 1..5 rating buttons
        self._conn_choice_status = None
        self._conn_rating_choice = None          # first click wins (int 1..5)
        self._conn_rest_for_puzzle_index = None  # which puzzle index (1..10) just finished

        # Start at Player ID screen
        self.show_player_id_screen()

    # ---------- LSL: unified marker sender ----------
    def send_marker(self, label):
        """Prefix every marker with Player ID and push via LSL if available."""
        pid = self.player_id if self.player_id not in (None, "") else "NA"
        msg = f"Player{pid}_{label}"
        if self.outlet is not None:
            try:
                self.outlet.push_sample([msg])
            except Exception as e:
                print("LSL push_sample failed:", e)
        # Also print to console for debugging/trace
        print("[MARKER]", msg)

    # ---------- Utility to clear the current screen ----------
    def clear_screen(self):
        for w in self.main_frame.winfo_children():
            w.destroy()

    # ---------- Safe widget config (prevents invalid-command errors) ----------
    @staticmethod
    def _safe_config(widget, **kwargs):
        if widget is None:
            return
        try:
            if widget.winfo_exists():
                widget.config(**kwargs)
        except Exception:
            pass

    # ---------- Centered title/subtitle screen helper ----------
    def center_message_screen(self, title, subtitle, on_next, next_label="Press Enter/Return to continue."):
        """
        Draw a centered message with a big title and a wrapped subtitle.
        Binds Enter/Return to on_next so participants can proceed with the keyboard.

        Also stores on_next so the master skip '.' can invoke it.
        """
        self.clear_screen()

        frame = tk.Frame(self.main_frame)
        frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(frame, text=title, font=FONT_TITLE, justify="center").pack(pady=20)
        tk.Label(frame, text=subtitle, font=FONT_SUBTITLE, wraplength=700, justify="center").pack(pady=20)
        if next_label:
            tk.Label(frame, text=next_label, font=FONT_LABEL).pack(pady=8)

        # Rebind Enter to advance to the next screen
        self._last_on_next = on_next
        self.root.bind("<Return>", lambda e: on_next())

    # ---------- Player ID screen ----------
    def show_player_id_screen(self):
        self.clear_screen()
        frame = tk.Frame(self.main_frame)
        frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(frame, text="ENTER PLAYER ID", font=FONT_TITLE).pack(pady=20)
        tk.Label(frame, text="Please enter your Participant/Player ID and press Enter.", font=FONT_SUBTITLE, wraplength=700).pack(pady=10)

        entry = tk.Entry(frame, font=("Arial", 18))
        entry.pack(pady=12)
        entry.focus_set()

        def submit():
            pid = entry.get().strip()
            if pid == "":
                return
            self.player_id = pid
            self.send_marker("Session_Start")  # optional session start
            self.show_intro()

        submit_btn = tk.Button(frame, text="Continue", font=("Arial", 16), command=submit)
        submit_btn.pack(pady=10)

        self._last_on_next = submit
        self.root.bind("<Return>", lambda e: submit())

    # ---------- Screens ----------
    def show_intro(self):
        self.center_message_screen(
            "WELCOME TO THE EXPERIMENT",
            "You will complete two tasks: a Connections game and a Remote Associates Test (RAT).",
            self.show_connections_instructions
        )

    def show_connections_instructions(self):
        self.center_message_screen(
            "CONNECTIONS GAME — INSTRUCTIONS",
            "You will see 16 tiles, each with a word. Your goal is to find one correct group of four "
            "words that belong together.\n\n"
            "Select exactly four tiles. If they form a correct group, the category will be shown and "
            "the puzzle will end.\n\n"
            "You will complete 10 puzzles in a row.\n"
            "Between puzzles, there will be a 15-second rest period. During rest, please rate how "
            "spontaneous your answer felt (1 = deliberate, 5 = spontaneous).",
            self.start_connections_game
        )

    def show_rat_instructions(self):
        self.center_message_screen(
            "REMOTE ASSOCIATES TEST (RAT) — INSTRUCTIONS",
            "For each item you will see 3 words.\n\n"
            "• You will have 10 seconds to silently think of a fourth word that relates to all three.\n"
            "• After 10 seconds, the correct answer will be revealed (slightly emphasized).\n"
            "• Press Y if you already knew the answer before it was revealed, or N if you did not.\n"
            "• After your Y/N, there is a 15-second rest period before the next item.\n"
            "• There are 10 items in total.",
            self.start_rat_task
        )

    def show_congratulations(self):
        # Keep the “Congrats” screen, then proceed to post-questions
        self.center_message_screen(
            "CONGRATULATIONS!",
            "You’ve completed the tasks. A researcher will now ask a few follow-up questions.",
            self.show_post_questions,
            next_label="Press Enter/Return to proceed to the questions."
        )

    def show_post_questions(self):
        """Five 1–5 'test' questions, one at a time. Markers: PostSurvey_Start/End and PostQ#_<rating>."""
        self.clear_screen()
        self.send_marker("PostSurvey_Start")
        self.postq = PostQuestionnaire(
            parent=self.main_frame,
            on_complete=self.final_thanks_and_exit,
            send_marker=self.send_marker,
            app=self  # for '.' skip hookup
        )

    def final_thanks_and_exit(self):
        self.send_marker("PostSurvey_End")
        self.center_message_screen(
            "THANK YOU!",
            "You’ve completed everything. You may now close the window.",
            lambda: self.root.destroy(),
            next_label="Press Enter/Return to exit."
        )

    # ---------- Task starters ----------
    def start_connections_game(self):
        """Begin the 10-puzzle Connections stage (with 15s REST + spontaneity rating between puzzles)."""
        self.clear_screen()
        self.root.unbind("<Return>")           # Prevent Enter from skipping the game
        self._last_on_next = None              # message screen callback no longer relevant
        self.connections_puzzles_completed = 0 # reset counter at stage start

        # Start the first puzzle instance (puzzle index is 1-based)
        puzzle_index = self.connections_puzzles_completed + 1
        self.conn = ConnectionsGame(
            parent=self.main_frame,
            puzzle_index=puzzle_index,
            on_complete=self.on_connections_puzzle_complete,
            on_marker=self.send_marker
        )

    def on_connections_puzzle_complete(self):
        """
        Called by ConnectionsGame after a successful group (after the short success delay).
        Show the 15s REST screen (with spontaneity rating) after EVERY puzzle, including the 10th.
        After the final rest, proceed to RAT instructions.
        """
        self.connections_puzzles_completed += 1
        # Launch the rest screen for the puzzle that just finished
        self._start_connections_rest(self.connections_puzzles_completed)

    def start_rat_task(self):
        """Begin ERP-style RAT (10s think → reveal → Y/N → 15s rest)."""
        self.clear_screen()
        self.root.unbind("<Return>")
        self._last_on_next = None
        self.rat = RATGame(
            parent=self.main_frame,
            on_complete=self.show_congratulations,
            on_marker=self.send_marker
        )

    # ---------- Master skip ('.') ----------
    def _master_skip(self, event=None):
        """
        Global '.' shortcut to progress to the next “thing”:
          - On message screens (and Player ID): call the stored on_next callback.
          - During Connections puzzle: behave like completing the current puzzle instantly (jump to REST).
          - During Connections REST: skip countdown and go to next (rating NoResponse if not chosen).
          - During RAT: delegate to RAT.force_advance().
          - During Post-Questions: skip current question (record NoResponse) and advance.
        """
        # If a center_message_screen is active
        if hasattr(self, "_last_on_next") and self._last_on_next:
            cb = self._last_on_next
            self._last_on_next = None
            cb()
            return

        # If currently in a Connections REST screen, end it now
        if self._conn_rest_active:
            self._end_connections_rest()
            return

        # If a Connections game exists, move to rest immediately (mimic "puzzle complete")
        if hasattr(self, "conn") and isinstance(self.conn, ConnectionsGame):
            self.on_connections_puzzle_complete()
            return

        # If a RAT game exists, delegate skip logic to it
        if hasattr(self, "rat") and isinstance(self.rat, RATGame):
            self.rat.force_advance()
            return

        # If Post-Questions exist, skip current question as NoResponse
        if hasattr(self, "postq") and isinstance(self.postq, PostQuestionnaire):
            self.postq.skip_current()
            return

    # ==================================================================
    # 🔵 CONNECTIONS — 15s REST WITH SPONTANEITY RATING (1..5 BUTTONS)
    # ==================================================================
    def _start_connections_rest(self, just_finished_index):
        """
        Show a 15s rest screen between Connections puzzles.
        Includes a 1–5 spontaneity rating recorded for the puzzle that just ended.

        LSL markers:
          PlayerX_Connections#_Rest_Start
          PlayerX_ConnectionQ#_<value> (on click)  OR PlayerX_ConnectionQ#_NoResponse (if none picked)
          PlayerX_Connections#_Rest_End
        """
        # Clear any running ConnectionsGame UI
        self.clear_screen()

        # Initialize REST state
        self._conn_rest_active = True
        self._conn_rest_remaining = 15
        self._conn_rating_choice = None
        self._conn_rest_for_puzzle_index = just_finished_index

        # Marker: rest start
        self.send_marker(f"Connections{just_finished_index}_Rest_Start")

        # Build REST UI
        self._conn_rest_frame = tk.Frame(self.main_frame)
        self._conn_rest_frame.place(relx=0.5, rely=0.5, anchor="center")

        # Title + countdown
        tk.Label(self._conn_rest_frame, text="Rest", font=FONT_TITLE).pack(pady=10)
        self._conn_rest_label = tk.Label(
            self._conn_rest_frame, text=f"Next step in {self._conn_rest_remaining}s", font=FONT_RAT_TIMER
        )
        self._conn_rest_label.pack(pady=5)

        # Spontaneity prompt + buttons
        tk.Label(
            self._conn_rest_frame,
            text="How spontaneous was your answer?\n(1 = very deliberate, 5 = very spontaneous)",
            font=FONT_RAT_FEEDBACK,
            justify="center",
        ).pack(pady=12)

        buttons_row = tk.Frame(self._conn_rest_frame)
        buttons_row.pack(pady=6)

        self._conn_rest_buttons = []
        for val in range(1, 6):
            b = tk.Button(
                buttons_row,
                text=str(val),
                font=("Arial", 16, "bold"),
                width=3,
                command=lambda v=val: self._conn_select_rating(v),
            )
            b.pack(side="left", padx=6)
            self._conn_rest_buttons.append(b)

        # Small status line to confirm the selection
        self._conn_choice_status = tk.Label(self._conn_rest_frame, text="", font=FONT_LABEL)
        self._conn_choice_status.pack(pady=6)

        # Allow '.' to skip rest (record NoResponse if not selected)
        self._last_on_next = self._end_connections_rest

        # Begin countdown
        self._tick_connections_rest()

    def _tick_connections_rest(self):
        """Count down once per second; on zero → proceed (record rating or NoResponse)."""
        if not self._conn_rest_active:
            return

        if self._conn_rest_remaining > 0:
            self._safe_config(self._conn_rest_label, text=f"Next step in {self._conn_rest_remaining}s")
            self._conn_rest_remaining -= 1
            self._conn_rest_after_id = self.root.after(1000, self._tick_connections_rest)
        else:
            self._end_connections_rest()

    def _conn_select_rating(self, value):
        """
        Handle a click on 1..5. First click wins; disable all buttons and record the choice.
        Also sends LSL marker: PlayerX_ConnectionQ#_<value>
        """
        if not self._conn_rest_active:
            return
        if self._conn_rating_choice is not None:
            return  # already chosen (first click wins)

        self._conn_rating_choice = int(value)

        # Disable buttons and highlight the chosen one
        for i, b in enumerate(self._conn_rest_buttons, start=1):
            try:
                if b.winfo_exists():
                    if i == value:
                        b.config(state="disabled", relief="sunken")
                    else:
                        b.config(state="disabled")
            except Exception:
                pass

        # Show a confirmation
        self._safe_config(self._conn_choice_status, text=f"Recorded: {value}/5")

        # Marker for this puzzle's rating
        pidx = self._conn_rest_for_puzzle_index
        self.send_marker(f"ConnectionQ{pidx}_{value}")

    def _end_connections_rest(self):
        """
        Finish the REST screen:
          - Cancel timer
          - Record rating (or NoResponse if none selected)
          - Start next Connections puzzle OR proceed to RAT instructions after 10th.
        """
        # Stop ticking
        if self._conn_rest_after_id:
            try:
                self.root.after_cancel(self._conn_rest_after_id)
            except Exception:
                pass
            self._conn_rest_after_id = None

        # If no rating was selected, emit a NoResponse marker
        if self._conn_rating_choice is None:
            pidx = self._conn_rest_for_puzzle_index
            self.send_marker(f"ConnectionQ{pidx}_NoResponse")

        # Record rating (append one entry per puzzle)
        self.connections_spontaneity.append(self._conn_rating_choice)

        # Tear down REST UI
        self._conn_rest_active = False
        try:
            if self._conn_rest_frame and self._conn_rest_frame.winfo_exists():
                self._conn_rest_frame.destroy()
        except Exception:
            pass
        self._conn_rest_frame = None
        self._conn_rest_label = None
        self._conn_rest_buttons = []
        self._conn_choice_status = None

        # Marker: rest end
        self.send_marker(f"Connections{self._conn_rest_for_puzzle_index}_Rest_End")

        # Decide next step
        if self.connections_puzzles_completed < 15:
            # Start the next Connections puzzle as a fresh instance
            puzzle_index = self.connections_puzzles_completed + 1
            self.conn = ConnectionsGame(
                parent=self.main_frame,
                puzzle_index=puzzle_index,
                on_complete=self.on_connections_puzzle_complete,
                on_marker=self.send_marker
            )
        else:
            # All 10 puzzles done → proceed to RAT instructions
            self.show_rat_instructions()


# ======================================================================
# 🧩 CONNECTIONS GAME (EACH PUZZLE ENDS AFTER FIRST CORRECT GROUP)
# ======================================================================
class ConnectionsGame:
    """
    Displays a 4x4 grid of word tiles. Participant selects up to 4.
    If the 4 selected tiles share the same 'group' label → success (puzzle ends).

    LSL markers:
      PlayerX_Connections#_Start at puzzle show
      PlayerX_Connections#_Guess#_Correct / _Incorrect on each group-of-4 submission
      PlayerX_Connections#_End on success
    """

    def __init__(self, parent, puzzle_index, on_complete, on_marker):
        self.parent = parent
        self.on_complete = on_complete
        self.on_marker = on_marker
        self.puzzle_index = puzzle_index

        # --- UI: labels + grid container + deselect button ---
        self.selected_label = tk.Label(parent, text="Selected: None", font=FONT_LABEL)
        self.selected_label.pack(pady=5)

        self.status_label = tk.Label(parent, text="", font=FONT_STATUS, fg="black")
        self.status_label.pack(pady=5)

        self.grid_frame = tk.Frame(parent)
        self.grid_frame.pack(expand=True, fill="both", padx=8, pady=8)

        self.deselect_button = tk.Button(parent, text="❌ Deselect All", font=("Arial", 14), command=self.deselect_all)
        self.deselect_button.pack(pady=5)

        # State
        self.selected = []     # indices of currently selected tiles
        self.found_groups = 0  # (kept for parity; we end after first success)
        self.guess_count = 0   # count of submitted 4-tile guesses in this puzzle

        # Build the puzzle
        self.start_new_puzzle()

    def start_new_puzzle(self):
        """Pick 4 random groups and lay out 16 tiles (4×4)."""
        self.selected.clear()

        self.groups = random.sample(all_groups, 4)  # choose 4 random groups
        # Flatten into tile dicts: {"text": word, "group": label, "matched": False}
        self.tiles = [{"text": m, "group": g["group"], "matched": False}
                      for g in self.groups for m in g["members"]]
        random.shuffle(self.tiles)

        self.draw_tiles()
        self.status_label.config(text="Find a correct group to win!", fg="black")
        self.update_selected_label()

        # Marker: puzzle start
        self.on_marker(f"Connections{self.puzzle_index}_Start")

    def draw_tiles(self):
        """Draw a uniform 4×4 grid. All cells are the same size; buttons fill cells."""
        # Clear any existing children
        for w in self.grid_frame.winfo_children():
            w.destroy()

        self.buttons = []

        # Create 16 buttons and place them in a 4×4 grid
        for i, tile in enumerate(self.tiles):
            btn = tk.Button(
                self.grid_frame,
                text=tile["text"],
                font=FONT_TILE,
                wraplength=160,                          # Wrap long words nicely in the cell
                bg="white" if not tile["matched"] else "#b3e6b3",
                relief="raised",
                state="disabled" if tile["matched"] else "normal",
            )

            # Bind mousedown for snappy click response
            btn.bind("<Button-1>", lambda e, idx=i: self.toggle_tile(idx))
            # Simple hover feedback
            btn.bind("<Enter>",    lambda e, idx=i: self.on_hover(idx))
            btn.bind("<Leave>",    lambda e, idx=i: self.on_leave(idx))

            # Place in grid; sticky makes it fill the entire cell
            btn.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="nsew")
            self.buttons.append(btn)

        # Make every row/column expand evenly (uniform size boxes)
        for r in range(4):
            self.grid_frame.grid_rowconfigure(r, weight=1, uniform="rows")
        for c in range(4):
            self.grid_frame.grid_columnconfigure(c, weight=1, uniform="cols")

    def update_selected_label(self):
        """Show the currently selected words (or 'None' if empty)."""
        if self.selected:
            text = ", ".join(self.tiles[i]["text"] for i in self.selected)
            self.selected_label.config(text=f"Selected: {text}")
        else:
            self.selected_label.config(text="Selected: None")

    def toggle_tile(self, idx):
        """Select or deselect a tile, allowing up to 4 selections, then check."""
        if self.tiles[idx]["matched"]:
            return

        if idx in self.selected:
            self.selected.remove(idx)
        else:
            if len(self.selected) < 4:
                self.selected.append(idx)

        self.update_tile_styles()
        self.update_selected_label()

        if len(self.selected) == 4:
            self.check_selection()

    def deselect_all(self):
        """Clear all selections quickly."""
        self.selected.clear()
        self.update_tile_styles()
        self.update_selected_label()

    def update_tile_styles(self):
        """Visual feedback: matched=green, selected=dark gray, normal=white."""
        for i, btn in enumerate(self.buttons):
            if self.tiles[i]["matched"]:
                btn.config(bg="#b3e6b3", fg="black", relief="raised", state="disabled")
            elif i in self.selected:
                btn.config(bg="#666666", fg="black", relief="sunken", state="normal")
            else:
                btn.config(bg="white", fg="black", relief="raised", state="normal")

    def check_selection(self):
        """If the 4 selected tiles share the same group label → success."""
        # Increment guess counter for this puzzle (a 'guess' == attempting 4 tiles)
        self.guess_count += 1

        groups = [self.tiles[i]["group"] for i in self.selected]
        is_correct = all(g == groups[0] for g in groups)

        if is_correct:
            group_name = groups[0]
            for idx in self.selected:
                self.tiles[idx]["matched"] = True

            # Marker: guess correct
            self.on_marker(f"Connections{self.puzzle_index}_Guess{self.guess_count}_Correct")

            # Show category name on success
            self.status_label.config(text=f"🎉 Correct! Group: {group_name} 🎉", fg="green")
            self.flash_correct_group(self.selected)

            # Marker: puzzle end
            self.on_marker(f"Connections{self.puzzle_index}_End")

            # End this puzzle after a brief success display (no separate countdown screen)
            self.parent.after(2500, self.on_complete)
        else:
            # Marker: guess incorrect
            self.on_marker(f"Connections{self.puzzle_index}_Guess{self.guess_count}_Incorrect")
            self.status_label.config(text="Wrong group! Try again.", fg="red")

        self.selected.clear()
        self.update_tile_styles()
        self.update_selected_label()

    def flash_correct_group(self, indexes):
        """Quick yellow flash before settling to green for matched tiles."""
        for _ in range(2):
            for idx in indexes:
                self.buttons[idx].config(bg="yellow")
            self.parent.update()
            self.parent.after(150)
            for idx in indexes:
                self.buttons[idx].config(bg="#b3e6b3")
            self.parent.update()
            self.parent.after(150)

    def on_hover(self, idx):
        """Highlight a tile on hover if it is selectable and not already selected."""
        if not self.tiles[idx]["matched"] and idx not in self.selected:
            self.buttons[idx].config(bg="#cce6ff")

    def on_leave(self, idx):
        """Restore normal color on hover exit if the tile is not selected/matched."""
        if not self.tiles[idx]["matched"] and idx not in self.selected:
            self.buttons[idx].config(bg="white")


# ======================================================================
# 🧪 RAT GAME (ERP: 10s THINK → REVEAL (Y/N) → 15s REST)
# ======================================================================
class RATGame:
    """
    Presents 10 RAT items:

      Per item:
        1) THINK (10s): show the 3-word prompt + countdown.  → Marker: RAT#_Start
        2) REVEAL: show the correct answer; await Y/N.       → Marker: RAT#_Response_Y or _N, then RAT#_End
        3) REST (15s): neutral screen with countdown.        → Markers: RAT#_Rest_Start / RAT#_Rest_End

      Master skip '.':
        - In THINK → reveal immediately.
        - In REVEAL → treat as 'No' and move to REST immediately.
        - In REST → skip countdown and go to next item.
    """

    def __init__(self, parent, on_complete, on_marker):
        self.parent = parent
        self.root = parent.winfo_toplevel()  # bind/unbind at the window level
        self.on_complete = on_complete
        self.on_marker = on_marker

        # Progress & state
        self.index = 0                # which RAT item we’re on (0..9)
        self.phase = None             # 'think' | 'reveal' | 'rest' | 'done'
        self.awaiting_yes_no = False  # ensure Y/N only processed once per item

        # Timings
        self.think_seconds = 10
        self.rest_seconds_default = 15

        # Tk after() handles (so they can be canceled safely)
        self._think_after_id = None
        self._rest_after_id  = None

        # UI members (set per phase; set to None when screen changes)
        self.think_label  = None
        self.rest_label   = None
        self.answer_label = None
        self.prompt_label = None

        # Start first item
        self.start_next_item()

    # ---------- Utilities ----------
    def clear_screen(self):
        """Destroy all widgets inside this RAT container frame."""
        for w in self.parent.winfo_children():
            w.destroy()

    def _cancel_after(self, attr_name):
        """Cancel a pending Tk after() callback by attribute name if present."""
        aid = getattr(self, attr_name, None)
        if aid:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
            setattr(self, attr_name, None)

    def _safe_config(self, widget, **kwargs):
        """Safely config() only if widget still exists."""
        if widget is None:
            return
        try:
            if widget.winfo_exists():
                widget.config(**kwargs)
        except Exception:
            pass

    # ---------- Flow control ----------
    def start_next_item(self):
        """Advance to the next RAT item or end if done."""
        # Cancel any pending timers from previous item (paranoia cleanup)
        self._cancel_after("_think_after_id")
        self._cancel_after("_rest_after_id")

        if self.index >= len(RAT_PROMPTS):
            self.phase = "done"
            self.on_complete()
            return

        self.show_think_phase()

    # ---------- THINK PHASE ----------
    def show_think_phase(self):
        """Show the 3-word prompt and a 10s countdown."""
        # Clean up any reveal/rest binds/timers from previous item
        self._cancel_after("_think_after_id")
        self._cancel_after("_rest_after_id")
        self._unbind_yes_no_keys()

        self.clear_screen()
        self.phase = "think"
        self.awaiting_yes_no = False

        # Header: progress
        tk.Label(self.parent,
                 text=f"RAT Item {self.index + 1} of {len(RAT_PROMPTS)}",
                 font=FONT_RAT_TIMER).pack(pady=10)

        # Prompt: three words
        tk.Label(self.parent,
                 text=RAT_PROMPTS[self.index],
                 font=FONT_RAT_PROMPT,
                 wraplength=800,
                 justify="center").pack(pady=20)

        # Timer label
        self._think_remaining = self.think_seconds
        self.think_label = tk.Label(self.parent, text=f"Thinking: {self._think_remaining}s", font=FONT_RAT_TIMER)
        self.think_label.pack(pady=5)

        # Instruction
        tk.Label(self.parent,
                 text="Think of a single word that relates to all three. No typing yet.",
                 font=FONT_RAT_FEEDBACK).pack(pady=10)

        # Marker: RAT trial start
        self.on_marker(f"RAT{self.index + 1}_Start")

        # Start countdown
        self._tick_think()

    def _tick_think(self):
        """Count down once per second; on zero → reveal."""
        if self.phase != "think":
            return  # Phase changed (skip/advance/interrupt)

        if self._think_remaining > 0:
            self._safe_config(self.think_label, text=f"Thinking: {self._think_remaining}s")
            self._think_remaining -= 1
            self._think_after_id = self.root.after(1000, self._tick_think)
        else:
            self.reveal_phase()

    # ---------- REVEAL PHASE ----------
    def reveal_phase(self):
        """Reveal the correct answer and capture a Y/N judgment from the participant."""
        # Cancel any residual THINK timer
        self._cancel_after("_think_after_id")

        # If we already moved on, don't double-render
        if self.phase == "rest" or self.phase == "done":
            return

        self.phase = "reveal"
        self.awaiting_yes_no = True

        # Keep existing prompt on screen; append the answer + instructions
        self.answer_label = tk.Label(
            self.parent,
            text=f"Answer: {RAT_ANSWERS[self.index].upper()}",
            font=FONT_RAT_ANSWER
        )
        self.answer_label.pack(pady=15)

        self.prompt_label = tk.Label(
            self.parent,
            text="Did you already know this answer before it was revealed?\nPress Y for Yes, N for No.",
            font=FONT_RAT_FEEDBACK
        )
        self.prompt_label.pack(pady=10)

        # Bind to TOP-LEVEL so keypresses are captured reliably regardless of focus
        self._bind_yes_no_keys()

    def _bind_yes_no_keys(self):
        """Bind Y/N globally to the Toplevel so a focused widget is not required."""
        try:
            self.root.bind_all("<KeyPress-y>", self._on_yes)
            self.root.bind_all("<KeyPress-Y>", self._on_yes)
            self.root.bind_all("<KeyPress-n>", self._on_no)
            self.root.bind_all("<KeyPress-N>", self._on_no)
        except Exception:
            pass

    def _unbind_yes_no_keys(self):
        """Remove the global Y/N bindings (safe to call multiple times)."""
        try:
            self.root.unbind_all("<KeyPress-y>")
            self.root.unbind_all("<KeyPress-Y>")
            self.root.unbind_all("<KeyPress-n>")
            self.root.unbind_all("<KeyPress-N>")
        except Exception:
            pass

    def _on_yes(self, event=None):
        """User indicated they knew the answer before reveal (Y)."""
        if not (self.phase == "reveal" and self.awaiting_yes_no):
            return
        self.awaiting_yes_no = False
        self._unbind_yes_no_keys()

        # Marker: response + trial end
        self.on_marker(f"RAT{self.index + 1}_Response_Y")
        self.on_marker(f"RAT{self.index + 1}_End")

        self.start_rest_phase()

    def _on_no(self, event=None):
        """User indicated they did not know the answer before reveal (N)."""
        if not (self.phase == "reveal" and self.awaiting_yes_no):
            return
        self.awaiting_yes_no = False
        self._unbind_yes_no_keys()

        # Marker: response + trial end
        self.on_marker(f"RAT{self.index + 1}_Response_N")
        self.on_marker(f"RAT{self.index + 1}_End")

        self.start_rest_phase()

    # ---------- REST PHASE ----------
    def start_rest_phase(self):
        """Show a 15s rest countdown, then advance to the next item."""
        # Always unbind Y/N and cancel any running think timer
        self._unbind_yes_no_keys()
        self._cancel_after("_think_after_id")

        # Clear reveal UI
        self.clear_screen()
        self.phase = "rest"

        rest_frame = tk.Frame(self.parent)
        rest_frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(rest_frame, text="Rest", font=FONT_TITLE).pack(pady=10)
        self._rest_remaining = self.rest_seconds_default
        self.rest_label = tk.Label(rest_frame, text=f"Next item in {self._rest_remaining}s", font=FONT_RAT_TIMER)
        self.rest_label.pack(pady=5)

        # Marker: RAT rest start
        self.on_marker(f"RAT{self.index + 1}_Rest_Start")

        self._tick_rest()

    def _tick_rest(self):
        """Count down once per second; on zero → next item."""
        if self.phase != "rest":
            return  # Phase changed (skip/advance)

        if self._rest_remaining > 0:
            self._safe_config(self.rest_label, text=f"Next item in {self._rest_remaining}s")
            self._rest_remaining -= 1
            self._rest_after_id = self.root.after(1000, self._tick_rest)
        else:
            # Marker: RAT rest end
            self._cancel_after("_rest_after_id")
            self.on_marker(f"RAT{self.index + 1}_Rest_End")

            # Advance to next item
            self.index += 1
            self.start_next_item()

    # ---------- Master skip integration ----------
    def force_advance(self):
        """
        Called by the app-level '.' handler:
          - THINK → immediately reveal
          - REVEAL → treat as 'No' (once) and start rest
          - REST → cancel countdown and go to next item
        """
        if self.phase == "think":
            self._cancel_after("_think_after_id")
            self.reveal_phase()
        elif self.phase == "reveal":
            if self.awaiting_yes_no:
                self._on_no()
        elif self.phase == "rest":
            self._cancel_after("_rest_after_id")
            # Close rest immediately (emit rest end marker)
            self.on_marker(f"RAT{self.index + 1}_Rest_End")
            self.index += 1
            self.start_next_item()
        elif self.phase == "done":
            pass  # nothing to do


# ======================================================================
# 📝 POST-EXPERIMENT QUESTIONNAIRE (5 TEST QUESTIONS)
# ======================================================================
class PostQuestionnaire:
    """
    Shows 5 Likert (1–5) items, one at a time, labeled 'test' as placeholders.
    Markers:
      PlayerX_PostSurvey_Start / PlayerX_PostSurvey_End  (sent by ExperimentApp)
      PlayerX_PostQ#_<rating> on each response
      If skipped via '.' → PlayerX_PostQ#_NoResponse
    """

    def __init__(self, parent, on_complete, send_marker, app):
        self.parent = parent
        self.on_complete = on_complete
        self.send_marker = send_marker
        self.app = app

        # Placeholder questions — replace text later in code as needed
        self.questions = [
            "test 1",
            "test 2",
            "test 3",
            "test 4",
            "test 5",
        ]
        self.index = 0  # 0..4

        self.frame = tk.Frame(self.parent)
        self.frame.pack(expand=True, fill="both")

        self.show_current()

    def clear(self):
        for w in self.frame.winfo_children():
            w.destroy()

    def show_current(self):
        self.clear()
        if self.index >= len(self.questions):
            # Done
            self.on_complete()
            return

        # Hook '.' to skip this question (records NoResponse)
        self.app._last_on_next = self.skip_current

        qnum = self.index + 1
        tk.Label(self.frame, text=f"Question {qnum} of {len(self.questions)}", font=FONT_RAT_TIMER).pack(pady=10)
        tk.Label(self.frame, text=self.questions[self.index], font=FONT_RAT_PROMPT, wraplength=800, justify="center").pack(pady=20)
        tk.Label(self.frame, text="Please rate from 1 to 5.", font=FONT_RAT_FEEDBACK).pack(pady=10)

        row = tk.Frame(self.frame)
        row.pack(pady=6)

        for val in range(1, 6):
            tk.Button(
                row,
                text=str(val),
                font=("Arial", 16, "bold"),
                width=3,
                command=lambda v=val: self.record_response(v)
            ).pack(side="left", padx=6)

    def record_response(self, value):
        qnum = self.index + 1
        self.send_marker(f"PostQ{qnum}_{value}")
        self.index += 1
        self.show_current()

    def skip_current(self):
        """Record NoResponse for current question and advance (used by '.' master skip)."""
        if self.index < len(self.questions):
            qnum = self.index + 1
            self.send_marker(f"PostQ{qnum}_NoResponse")
            self.index += 1
            self.show_current()


# ======================================================================
# 🖥 RUN THE APP
# ======================================================================
if __name__ == "__main__":
    root = tk.Tk()
    root.geometry(WINDOW_SIZE)
    root.title(APP_TITLE)
    app = ExperimentApp(root)
    root.mainloop()
