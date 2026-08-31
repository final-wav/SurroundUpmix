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

    # ------------------------------------------------------------ layout
    def _build(self):
        head = ttk.Frame(self.root, style="Bg.TFrame")
        head.pack(fill="x", padx=18, pady=(16, 6))
        ttk.Label(head, text="SurroundUpmix", style="Header.TLabel").pack(anchor="w")
        ttk.Label(head, text="stereo → 5.1 / 7.1 / 7.1.2 or Dolby Atmos  ·  "
                  "direct stays front, ambient wraps",
                  style="Sub.TLabel").pack(anchor="w")

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
        self._build_actions(body)
        self._build_log(body)

    def _on_wheel(self, event):
        c = getattr(self, "_canvas", None)
        if c is not None:
            c.yview_scroll(int(-event.delta / 120), "units")

    # ---- input / queue
    def _build_input(self, body):
        ic = ttk.Labelframe(body, text="  INPUT  ", style="Card.TLabelframe",
                            padding=12)
        ic.pack(fill="both", expand=True, pady=(0, 10))
        bar = ttk.Frame(ic)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="＋ Add files…", style="Ghost.TButton",
                   command=self._add_files).pack(side="left")
        ttk.Button(bar, text="＋ Add folder…", style="Ghost.TButton",
                   command=self._add_folder).pack(side="left", padx=(8, 0))
        b = ttk.Button(bar, text="📁", style="Icon.TButton", command=self._open_input)
        b.pack(side="left", padx=(8, 0))
        ToolTip(b, "Open the folder of the selected (or first) input in Explorer")
        ttk.Button(bar, text="Remove", style="Ghost.TButton",
                   command=self._remove_sel).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=self._clear_queue).pack(side="left", padx=(8, 0))
        hint = ("drag songs or folders here" if HAVE_DND
                else "tip: pip install tkinterdnd2  for drag & drop")
        ttk.Label(bar, text=hint, style="Muted.TLabel").pack(side="right")

        tw = ttk.Frame(ic)
        tw.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tw, style="Queue.Treeview", show="headings",
                                 columns=("name", "kind", "status"), height=6,
                                 selectmode="extended")
        self.tree.heading("name", text="File", anchor="w")
        self.tree.heading("kind", text="Type", anchor="w")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("name", anchor="w", width=380)
        self.tree.column("kind", anchor="w", width=90, stretch=False)
        self.tree.column("status", anchor="w", width=140, stretch=False)
        self.tree.tag_configure("queued", foreground=MUTED)
        self.tree.tag_configure("running", foreground=ACCENT)
        self.tree.tag_configure("done", foreground=OK)
        self.tree.tag_configure("failed", foreground=STOP)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb = ttk.Scrollbar(tw, command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)

        if HAVE_DND:
            for w in (self.root, self.tree):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)

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
        self.device = self._reg("device", self._combo(
            grid, "Device", DEVICES, "auto", 0, 2, TIPS["Device"]))

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
        self.model = self._reg("model", self._combo(
            pc, "Demucs model", MODELS, "htdemucs_ft", 0, 0, TIPS["Demucs model"]))
        self.split = self._reg("split", self._combo(
            pc, "Split vocals", SPLIT, "auto", 0, 1, TIPS["Split vocals"]))
        self.vocal = self._reg("vocal", self._combo(
            pc, "Vocal mode", VOCAL, "auto", 0, 2, TIPS["Vocal mode"]))
        self.decorr = self._reg("decorr", self._combo(
            pc, "Rear decorrelation", ["on", "off"], "on", 1, 0,
            TIPS["Rear decorrelation"]))
        self.vroles = self._reg("vroles", self._combo(
            pc, "Vocal roles", VROLES, "auto", 1, 1, TIPS["Vocal roles"]))
        self.recover = self._reg("recover", self._combo(
            pc, "Detail recovery", ["on", "off"], "on", 1, 2, TIPS["Detail recovery"]))

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

        # placement row
        plc = ttk.Labelframe(self.adv, text="  Placement per stem  (auto = song-adaptive)  ",
                             style="Card.TLabelframe", padding=12)
        plc.pack(fill="x", pady=(0, 8))
        for c in range(len(PLACE_STEMS)):
            plc.columnconfigure(c, weight=1, uniform="pl")
        self.place = {}
        for i, stem in enumerate(PLACE_STEMS):
            var = self._combo(plc, stem, ["auto", "front", "side", "rear"],
                              "auto", 0, i, PLACE_TIP)
            self.place[stem] = self._reg("place_" + stem, var)

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.adv.pack(fill="x", before=self.act, pady=(0, 4))
            self.adv_btn.configure(text="▾  Advanced  (model, split, tuning, placement)")
        else:
            self.adv.pack_forget()
            self.adv_btn.configure(text="▸  Advanced  (model, split, tuning, placement)")

    # ---- actions
    def _build_actions(self, body):
        self.act = ttk.Frame(body, style="Bg.TFrame")
        self.act.pack(fill="x", pady=(6, 8))
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
        lw = ttk.Frame(body, style="Bg.TFrame")
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
        sel = self.tree.selection() or self.tree.get_children()
        if not sel:
            self._append("! no input to open (add a file/folder first)\n")
            return
        p = self.jobs.get(sel[0], {}).get("path", "")
        self._open_folder(p if os.path.isdir(p) else os.path.dirname(p))

    def _open_output(self):
        od = self.outdir.get().strip()
        if od:
            self._open_folder(od)
        else:
            self._append("! output folder is blank (files go into an 'Output' folder "
                         "folder next to each song)\n")

    # ------------------------------------------------------------ queue ops
    def _add_paths(self, paths):
        jobs = expand_inputs(paths)
        added = 0
        for path, kind in jobs:
            if any(j["path"] == path for j in self.jobs.values()):
                continue
            iid = self.tree.insert("", "end", tags=("queued",),
                                   values=(os.path.basename(path.rstrip(os.sep)),
                                           kind, "⏳ queued"))
            self.jobs[iid] = {"path": path, "kind": kind}
            added += 1
        self.status.set("%d in queue" % len(self.jobs))
        if added == 0 and paths:
            self._append("! nothing addable in that drop/selection\n")

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

    def _remove_sel(self):
        if self.running:
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.jobs.pop(iid, None)
        self.status.set("%d in queue" % len(self.jobs))

    def _clear_queue(self):
        if self.running:
            return
        for iid in list(self.jobs):
            self.tree.delete(iid)
        self.jobs.clear()
        self.status.set("Ready")

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
        for stem, var in self.place.items():
            v = var.get()
            if v and v != "auto":
                cmd += ["--place-%s" % stem, v]
        if atmos:
            cmd += ["--adm"]
        elif admr:
            cmd += ["--adm", "--adm-order", "renderer"]
        return cmd

    def _start(self):
        if self.running:
            return
        self._save_cfg()
        pending = [iid for iid in self.tree.get_children()
                   if self.tree.set(iid, "status").endswith("queued")
                   or self.tree.set(iid, "status").endswith("failed")]
        if not pending:
            self._append("! queue is empty (add songs or a folder first)\n")
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
            job = self.jobs.get(iid)
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
                    if self.tree.exists(iid):
                        self.tree.set(iid, "status", self._STATUS_TEXT[state])
                        self.tree.item(iid, tags=(state,))
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
