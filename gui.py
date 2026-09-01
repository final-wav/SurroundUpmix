#!/usr/bin/env python3
"""SurroundUpmix - dark-mode GUI with a batch queue and drag & drop.

Drop songs or whole folders onto the window (or use Add files / Add folder),
and they queue up as jobs that render one after another. Each input is
auto-detected: a folder that holds Demucs stems (bass/drums/vocals/other) is
treated as one stems job; any other folder is scanned for audio files and each
becomes a song job; individual audio files become song jobs.

Song jobs run allinone.py (Demucs -> split -> surround); stems jobs run
upmix.py. Everything streams into a live log while the window stays responsive.

The two everyday choices - Surround vs. Atmos, and the preset - are up top and
explained inline; the fine-tuning knobs live under a collapsible "Advanced"
section. Your settings are remembered between runs.

Tkinter ships with Python. OS drag & drop additionally needs `tkinterdnd2`
(`pip install tkinterdnd2`); without it the queue still works via the buttons.

    python gui.py
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading

import tkinter as tk
from tkinter import filedialog, ttk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAVE_DND = True
except Exception:
    HAVE_DND = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from surroundupmix.inputs import AUDIO_EXT, expand_inputs  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(os.path.expanduser("~"), ".surroundupmix_gui.json")

# ---- dark palette -----------------------------------------------------------
BG = "#1e1f22"
PANEL = "#2b2d31"
CARD = "#313338"
FG = "#dbdee1"
MUTED = "#949ba4"
ACCENT = "#5865f2"
ACCENT_HI = "#4752c4"
BORDER = "#3f4147"
OK = "#3ba55d"
STOP = "#ed4245"
LOG_BG = "#131416"
SEL = "#3a3d44"

FORMATS = ["5.1", "7.1", "7.1.2"]
PRESETS = ["focus", "immersive", "concert", "envelop"]
DEVICES = ["auto", "cuda", "cpu"]
SPLIT = ["auto", "on", "off"]
VOCAL = ["auto", "spread", "forward"]
MODELS = ["htdemucs_ft", "htdemucs_6s", "htdemucs"]
VROLES = ["auto", "keep", "swap"]
OUTPUTS = ["5.1", "7.1", "7.1.2", "Dolby Atmos", "ADM BWF"]
PLACE_STEMS = ("vocals", "bass", "drums", "other", "guitar", "piano")
INSTRUMENTS = ("bass", "drums", "vocals", "other", "guitar", "piano", "backing")

OUTPUT_DESC = {
    "5.1": "6-channel FLAC - plays on any 5.1 system.",
    "7.1": "8-channel FLAC - plays on any 7.1 system.",
    "7.1.2": "10-channel WAV with two height channels. The height only reaches "
             "real speakers on an Atmos-aware setup.",
    "Dolby Atmos": "A self-contained 7.1.2 ADM BWF master (48 kHz), bed order for "
                   "PLAYBACK on your speaker rig (rears at 5/6). Correct on the "
                   "system - but the Dolby Atmos Renderer maps this side<->rear wrong.",
    "ADM BWF": "Same 7.1.2 ADM BWF master, but bed in the Dolby Atmos RENDERER's "
               "order (sides at 5/6). Use this one when you import into the Renderer "
               "in Studio One so it maps correctly; use 'Dolby Atmos' for the rig.",
}

# a rough 0-5 character profile per preset, drawn as little bars so you can SEE
# how the presets differ before rendering
PRESET_METERS = {
    "focus":     [("Surround wrap", 1), ("Vocal centred", 5), ("Height", 1)],
    "immersive": [("Surround wrap", 3), ("Vocal centred", 3), ("Height", 2)],
    "concert":   [("Surround wrap", 4), ("Vocal centred", 2), ("Height", 3)],
    "envelop":   [("Surround wrap", 5), ("Vocal centred", 1), ("Height", 4)],
}

PRESET_DESC = {
    "focus":     "Vocal-forward, subtle wrap. Lead sits firmly in the centre, rears quietest. "
                 "Best for vocal / dialogue-led tracks (rap, singer-songwriter, podcasts).",
    "immersive": "Balanced all-rounder - the reference the project was hand-tuned to by ear. "
                 "Fits most songs.  (Default)",
    "concert":   "Roomier: more to the sides / backs / heights, vocal a touch looser. "
                 "Best for live or spacious recordings - 'sit in the room'.",
    "envelop":   "Maximum wrap - most of the ambience all around you, vocal least anchored. "
                 "Best for ambient / electronic / wide mixes (can be too much on dry recordings).",
}

TIPS = {
    "Output": "What to write. 5.1 / 7.1 / 7.1.2 are plain FLAC / WAV that play anywhere; "
              "Dolby Atmos writes a 7.1.2 ADM BWF master for Studio One / the Dolby Atmos "
              "Renderer. The line below describes the selected one.",
    "Preset": "Overall character - how much wraps around you and how firmly the vocal is "
              "centred. The line below describes the selected one.",
    "Device": "auto uses your NVIDIA GPU if present (much faster), otherwise the CPU.",
    "Split vocals": "Split the vocal into LEAD + BACKING (Roformer karaoke): the lead stays "
                    "front, the backing wraps behind. auto = split when the splitter is installed.",
    "Vocal mode": "auto detects a short-delay-DOUBLED vocal and keeps it forward (spreading it "
                  "would comb-filter into 'many voices'). forward / spread override.",
    "Rear gain (dB)": "Taste offset on the WHOLE rear field, on top of the auto-balance. "
                      "+2 = a bit more surround; negative = less.",
    "Rear below front": "Auto-balance target: keep the rear field this many dB under the front, "
                        "every song. Blank = use the preset. Smaller number = louder rear.",
    "Backing gain": "Level of the split-out backing choir. auto sits it a set amount under the "
                    "lead per song; or type a dB value.",
    "Demucs model": "htdemucs_ft = 4 stems (bass/drums/vocals/other), best quality (default). "
                    "htdemucs_6s also separates guitar + piano for individual placement "
                    "(slower, no _ft, weaker piano).",
    "Rear decorrelation": "Feed the back (and height) speakers a phase-safe, flat-magnitude "
                          "decorrelated copy of the surround ambience so the rear field "
                          "envelops you instead of collapsing onto the sides. on = new "
                          "default; off = the previous behaviour (same signal on sides + "
                          "backs). 7.1 / 7.1.2 only. Use off/on to A/B it on your rig.",
    "Detail recovery": "Demucs never fully rebuilds the mix - a quiet broadband layer "
                       "(air, transients, breaths, room) is lost. This reinjects exactly "
                       "that (residual = original minus the stems) from the master, placed "
                       "by the same rule (coherent detail up front, diffuse air wraps). "
                       "on = new default; off = old behaviour (HF-air restore only). "
                       "Song mode only.",
    "Binaural depth": "For BINAURAL recordings (dummy-head, or an HRTF/binaural panner) that "
                      "carry a real front/back cue. Leans the diffuse field front/back per the "
                      "detected cue. It's multiplied by a measured binaural confidence, so a "
                      "normal pan-pot song scores ~0 and stays untouched even at 100%. 0 = off.",
    "Vocal roles": "The karaoke split only LABELS one part 'lead' and the other 'backing'. "
                   "auto checks from the signal which is really the main vocal and swaps "
                   "them if the model got it backwards (e.g. a wet/filtered lead wrongly "
                   "sent behind you), or skips the split entirely for a wide/wet vocal "
                   "wash. keep = trust the model; swap = force a swap.",
}

PLACE_TIP = ("Where each instrument goes.\n"
             "auto = song-adaptive: a dry, centred part stays front; a diffuse or clearly "
             "panned part wraps to the sides/back by itself.\n"
             "front / side / rear force this whole stem (its dry source AND its ambient) "
             "into that zone.")


class ToolTip:
    """Minimal dark hover tooltip."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        tk.Label(tw, text=self.text, justify="left", bg="#20242b", fg="#e6e6e8",
                 relief="solid", borderwidth=1, padx=8, pady=6, wraplength=360,
                 font=("Segoe UI", 9)).pack()

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ---- GUI --------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.running = False
        self.stop_flag = False
        self.q = queue.Queue()
        self.jobs = {}          # iid -> {"path","kind"}
        self._cfg_vars = {}     # name -> tk.StringVar (for remembering settings)
        self.cfg = self._load_cfg()
        root.title("SurroundUpmix")
        root.configure(bg=BG)
        root.geometry("860x800")
        root.minsize(760, 520)
        self._style()
        self._build()
        self._apply_cfg()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._drain)

    # ------------------------------------------------------------ settings
    def _load_cfg(self):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self):
        try:
            cfg = {name: var.get() for name, var in self._cfg_vars.items()}
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _apply_cfg(self):
        for name, var in self._cfg_vars.items():
            if name in self.cfg:
                var.set(self.cfg[name])
        self._on_output()
        self._on_preset()
        self._on_binaural()
        self._update_context()

    def _reg(self, name, var):
        self._cfg_vars[name] = var
        return var

    def _on_close(self):
        self._save_cfg()
        self.root.destroy()

    # ------------------------------------------------------------ styling
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=PANEL, foreground=FG, fieldbackground=CARD,
                    bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL,
                    troughcolor=CARD, focuscolor=ACCENT)
        s.configure("TFrame", background=PANEL)
        s.configure("Bg.TFrame", background=BG)
        s.configure("TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        s.configure("Hint.TLabel", background=PANEL, foreground=MUTED,
                    font=("Segoe UI", 9))
        s.configure("BgMuted.TLabel", background=BG, foreground=MUTED)
        s.configure("Header.TLabel", background=BG, foreground=FG,
                    font=("Segoe UI", 17, "bold"))
        s.configure("Sub.TLabel", background=BG, foreground=MUTED,
                    font=("Segoe UI", 10))
        s.configure("Card.TLabelframe", background=PANEL, bordercolor=BORDER,
                    relief="solid", borderwidth=1)
        s.configure("Card.TLabelframe.Label", background=PANEL, foreground=MUTED,
                    font=("Segoe UI", 10, "bold"))
        s.configure("TEntry", fieldbackground=CARD, foreground=FG,
                    insertcolor=FG, bordercolor=BORDER, padding=5)
        s.configure("TCombobox", fieldbackground=CARD, background=CARD,
                    foreground=FG, arrowcolor=FG, bordercolor=BORDER, padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", CARD)],
              foreground=[("readonly", FG)], selectbackground=[("", CARD)],
              selectforeground=[("", FG)])
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        # segmented (Surround / Atmos) toggle
        s.configure("Seg.Toolbutton", background=CARD, foreground=MUTED,
                    bordercolor=BORDER, focusthickness=0, padding=(18, 9),
                    font=("Segoe UI", 10, "bold"), anchor="center")
        s.map("Seg.Toolbutton",
              background=[("selected", ACCENT), ("active", BORDER)],
              foreground=[("selected", "#ffffff")])
        # treeview (queue)
        s.configure("Queue.Treeview", background=LOG_BG, fieldbackground=LOG_BG,
                    foreground=FG, bordercolor=BORDER, borderwidth=0, rowheight=26)
        s.map("Queue.Treeview", background=[("selected", SEL)],
              foreground=[("selected", FG)])
        s.configure("Queue.Treeview.Heading", background=PANEL, foreground=MUTED,
                    bordercolor=BORDER, relief="flat", font=("Segoe UI", 9, "bold"))
        s.map("Queue.Treeview.Heading", background=[("active", PANEL)])
        # buttons
        s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    bordercolor=ACCENT, focusthickness=0, padding=(16, 9),
                    font=("Segoe UI", 11, "bold"))
        s.map("Accent.TButton", background=[("active", ACCENT_HI),
              ("disabled", BORDER)], foreground=[("disabled", MUTED)])
        s.configure("Stop.TButton", background=STOP, foreground="#ffffff",
                    bordercolor=STOP, padding=(16, 9), font=("Segoe UI", 11, "bold"))
        s.map("Stop.TButton", background=[("active", "#c03537"),
              ("disabled", BORDER)], foreground=[("disabled", MUTED)])
        s.configure("Ghost.TButton", background=CARD, foreground=FG,
                    bordercolor=BORDER, padding=(10, 6))
        s.map("Ghost.TButton", background=[("active", BORDER)])
        # font 12 + ypad 3 measures to exactly the Ghost button height (33 px), so
        # the folder icon is clearly visible AND flush with the other buttons
        s.configure("Icon.TButton", background=CARD, foreground=FG,
                    bordercolor=BORDER, padding=(10, 3), font=("Segoe UI", 12),
                    anchor="center")
        s.map("Icon.TButton", background=[("active", BORDER)])
        s.map("Icon.TButton", background=[("active", BORDER)])
        s.configure("Link.TButton", background=PANEL, foreground=FG,
                    bordercolor=PANEL, focusthickness=0, padding=(2, 4),
                    font=("Segoe UI", 10, "bold"), anchor="w")
        s.map("Link.TButton", background=[("active", PANEL)],
              foreground=[("active", ACCENT)])
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(2, 4, 2, 0))
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                    padding=(18, 7), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", CARD)],
              foreground=[("selected", FG)])

    # ------------------------------------------------------------ layout
    def _build(self):
        head = ttk.Frame(self.root, style="Bg.TFrame")
        head.pack(fill="x", padx=18, pady=(16, 6))
        ttk.Label(head, text="SurroundUpmix", style="Header.TLabel").pack(anchor="w")
        ttk.Label(head, text="stereo → 5.1 / 7.1 / 7.1.2 or Dolby Atmos  ·  "
                  "direct stays front, ambient wraps",
                  style="Sub.TLabel").pack(anchor="w")

        # Start / Stop sit at the top (fixed, always visible) above the scroll body
        self._build_actions(self.root)

        # Scrollable body: the window stays a fixed size and the content scrolls,
        # so expanding "Advanced" or a longer mode description never grows the
        # window off-screen (and never makes it jump around).
        outer = ttk.Frame(self.root, style="Bg.TFrame")
        outer.pack(fill="both", expand=True)
        self._canvas = canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(canvas, style="Bg.TFrame", padding=(18, 8))
        self._body_win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(self._body_win, width=e.width))
        canvas.bind_all("<MouseWheel>", self._on_wheel)

        self._build_input(body)
        self._build_output(body)
        self._build_advanced(body)
        self._build_log(body)

    def _on_wheel(self, event):
        c = getattr(self, "_canvas", None)
        if c is not None:
            c.yview_scroll(int(-event.delta / 120), "units")

    # ---- input: two tabs (Songs / Stems), each its own queue
    def _build_input(self, body):
        self.jobs, self.sjobs = {}, {}          # songs queue, stems queue
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill="both", expand=True, pady=(0, 10))
        self.songs_tab = self._build_queue_tab(songs=True)
        self.stems_tab = self._build_queue_tab(songs=False)
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._update_context())
        if HAVE_DND:                             # a drop on the window goes to Songs
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)

    def _build_queue_tab(self, songs):
        page = ttk.Frame(self.tabs, style="Bg.TFrame", padding=12)
        self.tabs.add(page, text="  Songs  " if songs else "  Stems  ")
        bar = ttk.Frame(page, style="Bg.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        if songs:
            ttk.Button(bar, text="＋ Add files…", style="Ghost.TButton",
                       command=self._add_files).pack(side="left")
            ttk.Button(bar, text="＋ Add folder…", style="Ghost.TButton",
                       command=self._add_folder).pack(side="left", padx=(8, 0))
        else:
            ttk.Button(bar, text="＋ Add stems folder…", style="Ghost.TButton",
                       command=self._add_stems_folder).pack(side="left")
        ob = ttk.Button(bar, text="📁", style="Icon.TButton", command=self._open_input)
        ob.pack(side="left", padx=(8, 0))
        ToolTip(ob, "Open the selected item's folder in Explorer")
        ttk.Button(bar, text="Remove", style="Ghost.TButton",
                   command=self._remove_sel).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=self._clear_queue).pack(side="left", padx=(8, 0))
        if not HAVE_DND:
            hint = "tip: pip install tkinterdnd2  for drag & drop"
        elif songs:
            hint = "drag songs or folders here"
        else:
            hint = "drag a stems folder here  (one folder = one song's stems)"
        ttk.Label(bar, text=hint, style="Muted.TLabel").pack(side="right")

        tw = ttk.Frame(page, style="Bg.TFrame")
        tw.pack(fill="both", expand=True)
        col1 = "kind" if songs else "instr"
        tree = ttk.Treeview(tw, style="Queue.Treeview", show="headings",
                            columns=("name", col1, "status"), height=6,
                            selectmode="extended")
        tree.heading("name", text="File" if songs else "Stems folder", anchor="w")
        tree.heading(col1, text="Type" if songs else "Instruments", anchor="w")
        tree.heading("status", text="Status", anchor="w")
        tree.column("name", anchor="w", width=330)
        tree.column(col1, anchor="w", width=170, stretch=False)
        tree.column("status", anchor="w", width=120, stretch=False)
        for tg, col in (("queued", MUTED), ("running", ACCENT),
                        ("done", OK), ("failed", STOP)):
            tree.tag_configure(tg, foreground=col)
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tw, command=tree.yview)
        sb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=sb.set)
        if songs:
            self.tree = tree
        else:
            self.stree = tree
        if HAVE_DND:
            tree.drop_target_register(DND_FILES)
            tree.dnd_bind("<<Drop>>", self._on_drop if songs else self._on_drop_stems)
        return page

    def _active(self):
        """(tree, jobs_dict, kind) for the tab the user is on."""
        try:
            if self.tabs.select() == str(self.stems_tab):
                return self.stree, self.sjobs, "stems"
        except Exception:
            pass
        return self.tree, self.jobs, "song"

    def _detect_instr_text(self, folder):
        from surroundupmix.stemnames import folder_stem_map
        from surroundupmix.io import find_stem
        det = [nm for nm in INSTRUMENTS if find_stem(folder, nm)]
        for nm in folder_stem_map(folder):
            if nm not in det:
                det.append(nm)
        return ", ".join(det)

    # ---- output / the everyday choices
    def _build_output(self, body):
        oc = ttk.Labelframe(body, text="  OUTPUT  (applied to every job)  ",
                            style="Card.TLabelframe", padding=12)
        oc.pack(fill="x", pady=(0, 10))

        # one compact row: Output | Preset | Device
        grid = ttk.Frame(oc)
        grid.pack(fill="x")
        for c in range(3):
            grid.columnconfigure(c, weight=1, uniform="o")
        self.output, self._output_cb = self._combo(
            grid, "Output", OUTPUTS, "7.1.2", 0, 0, TIPS["Output"], cb=True)
        self._reg("output", self.output)
        self._output_cb.bind("<<ComboboxSelected>>", lambda e: self._on_output())
        self.preset, self._preset_cb = self._combo(
            grid, "Preset", PRESETS, "immersive", 0, 1, TIPS["Preset"], cb=True)
        self._reg("preset", self.preset)
        self._preset_cb.bind("<<ComboboxSelected>>", lambda e: self._on_preset())
        self.device, self._device_box = self._combo(
            grid, "Device", DEVICES, "auto", 0, 2, TIPS["Device"], cb=True)
        self._reg("device", self.device)

        self.output_desc = tk.StringVar(value=OUTPUT_DESC["7.1.2"])
        ttk.Label(oc, textvariable=self.output_desc, style="Hint.TLabel",
                  wraplength=700, justify="left").pack(fill="x", pady=(6, 4))

        # preset: one line + little character bars so you SEE what it does
        self.preset_desc = tk.StringVar(value=PRESET_DESC["immersive"])
        ttk.Label(oc, textvariable=self.preset_desc, style="Hint.TLabel",
                  wraplength=700, justify="left").pack(fill="x", pady=(6, 6))
        self._meters = []
        mf = ttk.Frame(oc)
        mf.pack(fill="x", pady=(0, 8))
        for _ in range(3):
            row = ttk.Frame(mf)
            row.pack(fill="x", pady=1)
            lab = ttk.Label(row, text="", style="Muted.TLabel", width=15, anchor="w")
            lab.pack(side="left")
            cv = tk.Canvas(row, height=10, width=170, bg=PANEL, highlightthickness=0)
            cv.pack(side="left")
            self._meters.append((lab, cv))

        # output folder + open icon
        ttk.Label(oc, text="Output folder  (blank = an 'Output' folder next to each song)",
                  style="Muted.TLabel").pack(anchor="w")
        frow = ttk.Frame(oc)
        frow.pack(fill="x", pady=(2, 0))
        self.outdir = self._reg("outdir", tk.StringVar())
        ttk.Entry(frow, textvariable=self.outdir).pack(side="left", fill="x",
                                                       expand=True)
        ttk.Button(frow, text="Browse…", style="Ghost.TButton",
                   command=self._browse_out).pack(side="left", padx=(8, 0))
        b = ttk.Button(frow, text="📁", style="Icon.TButton",
                       command=self._open_output)
        b.pack(side="left", padx=(6, 0))
        ToolTip(b, "Open the output folder in Explorer")

    # ---- advanced (collapsible)
    def _build_advanced(self, body):
        self._adv_open = False
        self.adv_btn = ttk.Button(body, text="▸  Advanced  (model, split, tuning, placement)",
                                  style="Link.TButton", command=self._toggle_adv)
        self.adv_btn.pack(fill="x", pady=(0, 4))

        self.adv = ttk.Frame(body, style="TFrame")
        # processing row
        pc = ttk.Labelframe(self.adv, text="  Processing  ", style="Card.TLabelframe",
                            padding=12)
        pc.pack(fill="x", pady=(0, 8))
        for c in range(3):
            pc.columnconfigure(c, weight=1, uniform="p")
        self.model, self._model_box = self._combo(
            pc, "Demucs model", MODELS, "htdemucs_ft", 0, 0, TIPS["Demucs model"], cb=True)
        self._reg("model", self.model)
        self._model_box.bind("<<ComboboxSelected>>", lambda e: self._update_context())
        self.split, self._split_box = self._combo(
            pc, "Split vocals", SPLIT, "auto", 0, 1, TIPS["Split vocals"], cb=True)
        self._reg("split", self.split)
        self._split_box.bind("<<ComboboxSelected>>", lambda e: self._update_context())
        self.vocal = self._reg("vocal", self._combo(
            pc, "Vocal mode", VOCAL, "auto", 0, 2, TIPS["Vocal mode"]))
        self.decorr = self._reg("decorr", self._combo(
            pc, "Rear decorrelation", ["on", "off"], "on", 1, 0,
            TIPS["Rear decorrelation"]))
        self.vroles = self._reg("vroles", self._combo(
            pc, "Vocal roles", VROLES, "auto", 1, 1, TIPS["Vocal roles"]))
        self.recover = self._reg("recover", self._combo(
            pc, "Detail recovery", ["on", "off"], "on", 1, 2, TIPS["Detail recovery"]))
        # binaural depth slider (spans the row)
        brow = ttk.Frame(pc)
        brow.grid(row=2, column=0, columnspan=3, sticky="ew", padx=4, pady=(8, 2))
        blab = ttk.Label(brow, text="Binaural depth", style="Muted.TLabel")
        blab.pack(side="left")
        self.binaural = self._reg("binaural", tk.DoubleVar(value=0))
        self.binaural_lbl = tk.StringVar(value="off")
        ttk.Label(brow, textvariable=self.binaural_lbl, style="Muted.TLabel",
                  width=5).pack(side="right")
        bsc = ttk.Scale(brow, from_=0, to=100, orient="horizontal",
                        variable=self.binaural, command=self._on_binaural)
        bsc.pack(side="left", fill="x", expand=True, padx=8)
        ToolTip(blab, TIPS["Binaural depth"])
        ToolTip(bsc, TIPS["Binaural depth"])

        # tuning row
        tc = ttk.Labelframe(self.adv, text="  Balance (blank = preset default)  ",
                            style="Card.TLabelframe", padding=12)
        tc.pack(fill="x", pady=(0, 8))
        for c in range(3):
            tc.columnconfigure(c, weight=1, uniform="t")
        self.rear_gain = self._reg("rear_gain", self._entry(
            tc, "Rear gain (dB)", "0", 0, 0, TIPS["Rear gain (dB)"]))
        self.rear_below = self._reg("rear_below", self._entry(
            tc, "Rear below front", "", 0, 1, TIPS["Rear below front"]))
        self.backing = self._reg("backing", self._entry(
            tc, "Backing gain", "auto", 0, 2, TIPS["Backing gain"]))

        # per-instrument panel: zone + level + spread + centre/LFE + mute/solo
        pi = ttk.Labelframe(self.adv, text="  Per instrument  (on top of the preset)  ",
                            style="Card.TLabelframe", padding=12)
        pi.pack(fill="x", pady=(0, 8))
        for c, txt in enumerate(("", "Zone", "Level dB", "Spread", "Ctr/LFE", "Mute", "Solo")):
            ttk.Label(pi, text=txt, style="Muted.TLabel").grid(
                row=0, column=c, sticky="w", padx=6, pady=(0, 3))
        self.place, self.level, self.mute, self.solo = {}, {}, {}, {}
        self.spread, self.lfe, self.center = {}, {}, None
        self._instr_widgets = {}          # stem -> [label + control widgets] (phase 3)
        for i, stem in enumerate(INSTRUMENTS, start=1):
            w = []
            nl = ttk.Label(pi, text=stem, style="Muted.TLabel")
            nl.grid(row=i, column=0, sticky="w", padx=6, pady=2)
            w.append(nl)
            zv = tk.StringVar(value="auto")
            zb = ttk.Combobox(pi, textvariable=zv, state="readonly", width=7,
                              values=["auto", "front", "side", "rear"])
            zb.grid(row=i, column=1, sticky="w", padx=6)
            zb.bind("<MouseWheel>", lambda e: "break")
            self.place[stem] = self._reg("place_" + stem, zv)
            w.append(zb)
            lv = tk.StringVar(value="0")
            le = ttk.Entry(pi, textvariable=lv, width=6)
            le.grid(row=i, column=2, sticky="w", padx=6)
            self.level[stem] = self._reg("level_" + stem, lv)
            w.append(le)
            if stem != "bass":                       # bass never wraps -> no spread
                spv = tk.StringVar(value="")
                se = ttk.Entry(pi, textvariable=spv, width=6)
                se.grid(row=i, column=3, sticky="w", padx=6)
                self.spread[stem] = self._reg("spread_" + stem, spv)
                w.append(se)
            if stem == "vocals":                     # centre amount 0..100
                cv = tk.StringVar(value="")
                ce = ttk.Entry(pi, textvariable=cv, width=6)
                ce.grid(row=i, column=4, sticky="w", padx=6)
                self.center = self._reg("center_vocals", cv)
                w.append(ce)
            elif stem in ("bass", "drums"):          # LFE send 0..100
                fv = tk.StringVar(value="")
                fe = ttk.Entry(pi, textvariable=fv, width=6)
                fe.grid(row=i, column=4, sticky="w", padx=6)
                self.lfe[stem] = self._reg("lfe_" + stem, fv)
                w.append(fe)
            mv = tk.BooleanVar(value=False)
            mc = ttk.Checkbutton(pi, variable=mv)
            mc.grid(row=i, column=5, padx=12)
            self.mute[stem] = self._reg("mute_" + stem, mv)
            w.append(mc)
            sv = tk.BooleanVar(value=False)
            sc = ttk.Checkbutton(pi, variable=sv)
            sc.grid(row=i, column=6, padx=12)
            self.solo[stem] = self._reg("solo_" + stem, sv)
            w.append(sc)
            self._instr_widgets[stem] = w
        # footer: which instruments the settings will act on + reset
        foot = ttk.Frame(pi)
        foot.grid(row=len(INSTRUMENTS) + 1, column=0, columnspan=7, sticky="ew", pady=(8, 0))
        self.instr_detected = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.instr_detected, style="Muted.TLabel").pack(side="left")
        ttk.Button(foot, text="Reset instruments", style="Link.TButton",
                   command=self._reset_instruments).pack(side="right")
        ToolTip(pi, PLACE_TIP + "\n\nLevel = dB trim. Spread (blank = auto, 0-100) = "
                    "how far that instrument's ambient wraps; 0 holds it fully front. "
                    "Ctr/LFE = vocal centre amount (vocals) or LFE send (bass/drums), "
                    "0-100. Mute drops it; Solo plays only the soloed instruments. "
                    "Greyed rows aren't in the current queue/model, so they do nothing.")

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.adv.pack(fill="x", before=self.logframe, pady=(0, 4))
            self.adv_btn.configure(text="▾  Advanced  (model, split, tuning, placement)")
        else:
            self.adv.pack_forget()
            self.adv_btn.configure(text="▸  Advanced  (model, split, tuning, placement)")

    # ---- actions
    def _build_actions(self, parent):
        self.act = ttk.Frame(parent, style="Bg.TFrame")
        self.act.pack(fill="x", padx=18, pady=(2, 8))
        self.start_btn = ttk.Button(self.act, text="▶  Start queue",
                                    style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(self.act, text="■  Stop", style="Stop.TButton",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.act, textvariable=self.status, style="BgMuted.TLabel").pack(
            side="right")

    # ---- log
    def _build_log(self, body):
        lw = self.logframe = ttk.Frame(body, style="Bg.TFrame")
        lw.pack(fill="both", expand=True)
        self.log = tk.Text(lw, bg=LOG_BG, fg="#c8ccd2", insertbackground=FG,
                           relief="flat", height=7, wrap="word", padx=10, pady=8,
                           font=("Cascadia Mono", 10), highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(lw, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=lsb.set, state="disabled")

    # ---- widget helpers
    def _combo(self, parent, label, values, default, r, c, tip=None, cb=False):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, sticky="ew", padx=4, pady=4)
        lab = ttk.Label(f, text=label, style="Muted.TLabel")
        lab.pack(anchor="w")
        var = tk.StringVar(value=default)
        box = ttk.Combobox(f, textvariable=var, values=values, state="readonly")
        box.pack(fill="x")
        # don't let the mouse wheel change the selection while scrolling the page
        box.bind("<MouseWheel>", lambda e: "break")     # Windows / macOS
        box.bind("<Button-4>", lambda e: "break")       # Linux wheel up
        box.bind("<Button-5>", lambda e: "break")       # Linux wheel down
        if tip:
            ToolTip(lab, tip)
            ToolTip(box, tip)
        return (var, box) if cb else var

    def _entry(self, parent, label, default, r, c, tip=None):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, sticky="ew", padx=4, pady=4)
        lab = ttk.Label(f, text=label, style="Muted.TLabel")
        lab.pack(anchor="w")
        var = tk.StringVar(value=default)
        e = ttk.Entry(f, textvariable=var)
        e.pack(fill="x")
        if tip:
            ToolTip(lab, tip)
            ToolTip(e, tip)
        return var

    # ---- reactive descriptions
    def _on_output(self):
        self.output_desc.set(OUTPUT_DESC.get(self.output.get(), ""))

    def _on_binaural(self, _=None):
        v = int(float(self.binaural.get()))
        self.binaural_lbl.set("off" if v < 1 else "%d%%" % v)

    # ---- phase 3: context reactivity (which instruments are actually live)
    def _set_enabled(self, w, on):
        try:
            w.configure(state=("readonly" if isinstance(w, ttk.Combobox) else "normal")
                        if on else "disabled")
        except Exception:
            pass

    def _target_instruments(self):
        """The stems the ACTIVE tab will produce (drives the greying)."""
        tree, jd, kind = self._active()
        det = set()
        if kind == "stems":
            from surroundupmix.stemnames import folder_stem_map
            from surroundupmix.io import find_stem
            for j in jd.values():
                for nm in INSTRUMENTS:
                    if find_stem(j["path"], nm):
                        det.add(nm)
                det |= set(folder_stem_map(j["path"]).keys())
            if not jd:                       # empty stems tab: let all rows be set
                det |= set(INSTRUMENTS)
        else:
            det |= {"bass", "drums", "vocals", "other"}
            if self.model.get() == "htdemucs_6s":
                det |= {"guitar", "piano"}
            if self.split.get() != "off":
                det.add("backing")
        return det, (kind == "song")

    def _update_context(self):
        """Grey the song-only controls on the Stems tab, and grey the
        per-instrument rows that won't exist for the active tab."""
        try:
            det, is_song = self._target_instruments()
        except Exception:
            return
        self._set_enabled(self._model_box, is_song)
        self._set_enabled(self._device_box, is_song)
        for stem, widgets in getattr(self, "_instr_widgets", {}).items():
            on = stem in det
            for wdg in widgets:
                if isinstance(wdg, ttk.Label):
                    wdg.configure(foreground=(FG if on else BORDER))
                else:
                    self._set_enabled(wdg, on)
        if hasattr(self, "instr_detected"):
            live = ", ".join(s for s in INSTRUMENTS if s in det)
            self.instr_detected.set("acts on: " + (live or "-"))

    def _reset_instruments(self):
        for stem in INSTRUMENTS:
            self.place[stem].set("auto")
            self.level[stem].set("0")
            self.mute[stem].set(False)
            self.solo[stem].set(False)
            if stem in self.spread:
                self.spread[stem].set("")
            if stem in self.lfe:
                self.lfe[stem].set("")
        if self.center is not None:
            self.center.set("")
        self._update_context()

    def _on_preset(self):
        self.preset_desc.set(PRESET_DESC.get(self.preset.get(), ""))
        self._update_meters()

    def _update_meters(self):
        prof = PRESET_METERS.get(self.preset.get(), [])
        for (lab, cv), (name, val) in zip(self._meters, prof):
            lab.configure(text=name)
            cv.delete("all")
            w, h, gap = int(cv["width"]), int(cv["height"]), 3
            seg = (w - 4 * gap) / 5.0
            for i in range(5):
                x0 = i * (seg + gap)
                cv.create_rectangle(x0, 0, x0 + seg, h,
                                    fill=ACCENT if i < val else CARD, outline="")

    # ------------------------------------------------------------ folders
    def _open_folder(self, path):
        if path and os.path.isdir(path):
            try:
                os.startfile(path)          # Windows
            except Exception:
                self._append("! could not open %s\n" % path)

    def _open_input(self):
        tree, jd, _ = self._active()
        sel = tree.selection() or tree.get_children()
        if not sel:
            self._append("! nothing to open (add something first)\n")
            return
        p = jd.get(sel[0], {}).get("path", "")
        self._open_folder(p if os.path.isdir(p) else os.path.dirname(p))

    def _open_output(self):
        od = self.outdir.get().strip()
        if od:
            self._open_folder(od)
        else:
            self._append("! output folder is blank (files go into an 'Output' folder "
                         "next to each song)\n")

    def _refresh_counts(self):
        n = len(self.jobs) + len(self.sjobs)
        self.status.set(("%d in queue" % n) if n else "Ready")
        self._update_context()

    # ------------------------------------------------------------ queue ops
    def _add_paths(self, paths):
        """Songs-tab / window drop: auto-classify and file each job under its
        own tab (a stems folder lands in Stems even if dropped on Songs)."""
        added = 0
        for path, kind in expand_inputs(paths):
            tree, jd = (self.stree, self.sjobs) if kind == "stems" else (self.tree, self.jobs)
            if any(j["path"] == path for j in jd.values()):
                continue
            col1 = self._detect_instr_text(path) if kind == "stems" else kind
            iid = tree.insert("", "end", tags=("queued",),
                              values=(os.path.basename(path.rstrip(os.sep)),
                                      col1 or "?", "⏳ queued"))
            jd[iid] = {"path": path, "kind": kind}
            added += 1
        self._refresh_counts()
        if added == 0 and paths:
            self._append("! nothing addable in that drop/selection\n")

    def _add_one_stems_folder(self, folder):
        if not folder or any(j["path"] == folder for j in self.sjobs.values()):
            return
        instr = self._detect_instr_text(folder)
        iid = self.stree.insert("", "end", tags=("queued",),
                                values=(os.path.basename(folder.rstrip(os.sep)),
                                        instr or "?", "⏳ queued"))
        self.sjobs[iid] = {"path": folder, "kind": "stems"}
        self._refresh_counts()

    def _add_files(self):
        ps = filedialog.askopenfilenames(
            title="Choose songs",
            filetypes=[("Audio", " ".join("*" + e for e in sorted(AUDIO_EXT))),
                       ("All files", "*.*")])
        if ps:
            self._add_paths(list(ps))

    def _add_folder(self):
        p = filedialog.askdirectory(title="Choose a folder (songs or stems)")
        if p:
            self._add_paths([p])

    def _add_stems_folder(self):
        p = filedialog.askdirectory(title="Choose a folder of stems (one song)")
        if p:
            self._add_one_stems_folder(p)

    def _browse_out(self):
        p = filedialog.askdirectory(title="Choose an output folder")
        if p:
            self.outdir.set(p)

    def _on_drop(self, event):
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        self._add_paths(paths)

    def _on_drop_stems(self, event):
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        seen = set()
        for p in paths:                          # a folder (or a file's folder) = one job
            folder = p if os.path.isdir(p) else os.path.dirname(p)
            if folder and folder not in seen:
                seen.add(folder)
                self._add_one_stems_folder(folder)

    def _remove_sel(self):
        if self.running:
            return
        tree, jd, _ = self._active()
        for iid in tree.selection():
            tree.delete(iid)
            jd.pop(iid, None)
        self._refresh_counts()

    def _clear_queue(self):
        if self.running:
            return
        tree, jd, _ = self._active()
        for iid in list(jd):
            tree.delete(iid)
        jd.clear()
        self._refresh_counts()

    # ------------------------------------------------------------ per-instrument
    def _overrides_dict(self):
        """Collect the per-instrument rows into the overrides structure. Solo is
        resolved here: if anything is soloed, every non-soloed stem is muted."""
        soloed = [s for s in INSTRUMENTS if self.solo[s].get()]

        def num(var):
            try:
                return float(var.get().strip().replace(",", "."))
            except (ValueError, AttributeError):
                return None

        out = {}
        for stem in INSTRUMENTS:
            d = {}
            z = self.place[stem].get()
            if z and z != "auto":
                d["zone"] = z
            lv = num(self.level[stem])
            if lv:
                d["level"] = lv
            if stem in self.spread:
                sp = num(self.spread[stem])
                if sp is not None:
                    d["spread"] = sp
            if stem == "vocals" and self.center is not None:
                c = num(self.center)
                if c is not None:
                    d["center"] = c
            if stem in self.lfe:
                lf = num(self.lfe[stem])
                if lf is not None:
                    d["lfe"] = lf
            if self.mute[stem].get() or (soloed and stem not in soloed):
                d["mute"] = True
            if d:
                out[stem] = d
        return out

    def _write_overrides(self):
        """Write the overrides JSON for this run; sets self._ovr_path (or None)."""
        ov = self._overrides_dict()
        if not ov:
            self._ovr_path = None
            return
        try:
            path = os.path.join(tempfile.gettempdir(), "surroundupmix_overrides.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ov, f)
            self._ovr_path = path
        except Exception:
            self._ovr_path = None

    # ------------------------------------------------------------ run
    def _cmd_for(self, job):
        py = sys.executable
        path, kind = job["path"], job["kind"]
        out = self.output.get()
        atmos = (out == "Dolby Atmos")
        admr = (out == "ADM BWF")
        fmt = "7.1.2" if (atmos or admr) else out
        common = ["--format", fmt, "--preset", self.preset.get(),
                  "--vocal-mode", self.vocal.get(),
                  "--backing-gain", self.backing.get().strip() or "auto"]
        if self.decorr.get() == "off":
            common += ["--decorrelate", "off"]
        if self.vroles.get() != "auto":
            common += ["--vocal-roles", self.vroles.get()]
        if self.recover.get() == "off":
            common += ["--recover-detail", "off"]
        if int(float(self.binaural.get())) > 0:
            common += ["--binaural", str(int(float(self.binaural.get())))]
        if kind == "song":
            cmd = [py, os.path.join(HERE, "allinone.py"), path,
                   "--device", self.device.get(), "--model", self.model.get(),
                   "--split-vocals", self.split.get()] + common
        else:
            cmd = [py, os.path.join(HERE, "upmix.py"), path] + common
        rg = self.rear_gain.get().strip()
        if rg:
            cmd += ["--rear-gain", rg]
        rb = self.rear_below.get().strip()
        if rb:
            cmd += ["--rear-below-front", rb]
        od = self.outdir.get().strip()
        if od:
            cmd += ["--out-dir", od]
        if getattr(self, "_ovr_path", None):
            cmd += ["--overrides", self._ovr_path]
        if atmos:
            cmd += ["--adm"]
        elif admr:
            cmd += ["--adm", "--adm-order", "renderer"]
        return cmd

    def _start(self):
        if self.running:
            return
        self._save_cfg()
        self._write_overrides()
        tree, jd, kind = self._active()
        self._run_tree, self._run_jobs = tree, jd
        pending = [iid for iid in tree.get_children()
                   if tree.set(iid, "status").endswith("queued")
                   or tree.set(iid, "status").endswith("failed")]
        if not pending:
            self._append("! this tab's queue is empty (add %s first)\n"
                         % ("stems folders" if kind == "stems" else "songs"))
            return
        self.running = True
        self.stop_flag = False
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._clear_log()
        threading.Thread(target=self._worker, args=(pending,), daemon=True).start()

    def _worker(self, pending):
        total = len(pending)
        done = 0
        for i, iid in enumerate(pending, 1):
            if self.stop_flag:
                break
            job = self._run_jobs.get(iid)
            if not job:
                continue
            self.q.put(("status", iid, "running"))
            self.q.put(("st", "Job %d/%d  ·  %s" %
                        (i, total, os.path.basename(job["path"]))))
            cmd = self._cmd_for(job)
            self.q.put(("log", "\n=== [%d/%d] %s ===\n$ %s\n\n" %
                        (i, total, os.path.basename(job["path"]), " ".join(cmd))))
            code = self._run_one(cmd)
            if self.stop_flag and code != 0:
                self.q.put(("status", iid, "queued"))
                break
            if code == 0:
                self.q.put(("status", iid, "done"))
                done += 1
            else:
                self.q.put(("status", iid, "failed"))
        self.q.put(("done_all", done, total))

    def _run_one(self, cmd):
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=HERE)
            for line in self.proc.stdout:
                self.q.put(("log", line))
            self.proc.wait()
            return self.proc.returncode
        except Exception as e:
            self.q.put(("log", "\n! failed to launch: %s\n" % e))
            return -1
        finally:
            self.proc = None

    def _stop(self):
        self.stop_flag = True
        if self.proc is not None:
            try:
                self.proc.terminate()
                self._append("\n! stopping after the current job…\n")
            except Exception:
                pass

    # ------------------------------------------------------------ ui pump
    _STATUS_TEXT = {"queued": "⏳ queued", "running": "▶ running",
                    "done": "✓ done", "failed": "✗ failed"}

    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                tag = item[0]
                if tag == "log":
                    self._append(item[1])
                elif tag == "st":
                    self.status.set(item[1])
                elif tag == "status":
                    _, iid, state = item
                    tree = getattr(self, "_run_tree", self.tree)
                    if tree.exists(iid):
                        tree.set(iid, "status", self._STATUS_TEXT[state])
                        tree.item(iid, tags=(state,))
                elif tag == "done_all":
                    _, done, total = item
                    self.running = False
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.status.set("Finished — %d/%d done" % (done, total))
                    self._append("\n✓ queue finished: %d/%d succeeded\n"
                                 % (done, total))
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main():
    root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
