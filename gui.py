#!/usr/bin/env python3
"""SurroundUpmix Studio - Flat Modern Dark Studio GUI.

Features:
- Flat Modern Studio Aesthetic: Zero harsh 1px Windows-XP borders, smooth surfaces,
  modern typography, and clean pill badges.
- Unified Smart Queue: Auto-classifies songs vs. stems without tab switching.
- Interactive Glow Soundstage: Top-down studio listening room displaying speaker rigs
  (5.1, 7.1, 7.1.2 Atmos) and acoustic instrument radiation nodes.
- Structured 4-Tab Advanced Engine: 100% of fine-tuning knobs preserved.
- Real-time Multi-Stage Progress: Demucs AI split ➔ Vocal roles ➔ 3D Atmos mastering.
"""
import json
import math
import os
import queue
import re
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

# ---- Flat Modern Studio Dark Palette ----------------------------------------
BG = "#0f1013"              # Deep obsidian base
PANEL = "#17181c"           # Elevated surface
CARD = "#1f2026"            # Inner card background
INPUT_BG = "#272930"        # Input fields & dropdowns
BORDER = "#2e3038"          # Ultra-subtle border
FG = "#f2f3f5"              # Crisp modern text
MUTED = "#8e929b"           # Secondary text
ACCENT = "#5865f2"          # Modern vibrant indigo/blurple
ACCENT_HI = "#6d78f5"
OK = "#57f287"              # Vibrant green
STOP = "#ed4245"            # Vibrant red
SEL = "#32353e"             # Selection highlight
LOG_BG = "#0b0c0e"          # Clean dark terminal console

# Soundstage visualizer colors
STAGE_BG = "#131418"
STAGE_RING = "#1d1f25"
SPK_OFF = "#2b2d35"
SPK_ON = "#5865f2"
SPK_HEIGHT = "#a855f7"
COL_VOCAL = "#ff6b6b"
COL_BASS = "#57f287"
COL_OTHER = "#f1c40f"
COL_BACKING = "#ec4899"

FORMATS = ["5.1", "7.1", "7.1.2"]
PRESETS = ["focus", "immersive", "concert", "envelop"]
DEVICES = ["auto", "cuda", "cpu"]
SPLIT = ["auto", "on", "off"]
VOCAL = ["auto", "spread", "forward"]
MODELS = ["htdemucs_ft", "htdemucs_6s", "htdemucs"]
VROLES = ["auto", "keep", "swap"]
OUTPUTS = ["Dolby Atmos (20ch Objects)", "7.1.2", "Dolby Atmos", "ADM BWF", "7.1", "5.1"]
PLACE_STEMS = ("vocals", "bass", "drums", "other", "guitar", "piano")
INSTRUMENTS = ("bass", "drums", "vocals", "other", "guitar", "piano", "backing")

OUTPUT_DESC = {
    "Dolby Atmos (20ch Objects)": "Modern Studio One & Atmos Renderer master: 14 fixed speaker anchors (7.1.6) + 6 dynamic moving 3D objects. Pure objects architecture (0 bed channels).",
    "7.1.2": "10-channel 7.1.2 discrete WAV with stereo height speakers. Plays directly on Atmos-capable hardware.",
    "Dolby Atmos": "Self-contained 7.1.2 ADM BWF master (48 kHz) with moving 3D objects (Playback speaker bed order).",
    "ADM BWF": "Certified 7.1.2 ADM BWF master for Dolby Atmos Renderer, Studio One, Pro Tools, and DaVinci Resolve.",
    "7.1": "8-channel FLAC/WAV - full panoramic surround wrap for 7.1 theater setups.",
    "5.1": "6-channel FLAC/WAV - standard surround sound compatible with all 5.1 receivers.",
}

PRESET_METERS = {
    "focus":     [("Surround wrap", 1), ("Vocal center", 5), ("Height presence", 1)],
    "immersive": [("Surround wrap", 3), ("Vocal center", 3), ("Height presence", 2)],
    "concert":   [("Surround wrap", 4), ("Vocal center", 2), ("Height presence", 3)],
    "envelop":   [("Surround wrap", 5), ("Vocal center", 1), ("Height presence", 4)],
}

PRESET_DESC = {
    "focus":     "Vocal-forward, subtle surround wrap. The lead voice is firmly anchored in the center.",
    "immersive": "Balanced 3D reference - hand-tuned studio master character. Fits 95% of tracks.",
    "concert":   "Spacious acoustics: wider wrap to sides and ceiling heights. Live arena sensation.",
    "envelop":   "Maximum wrap: ambient music wraps completely around you, vocals open and free.",
}

TIPS = {
    "Output": "Target output format. 5.1/7.1/7.1.2 are multi-channel files; Dolby Atmos / ADM BWF write official ADM masters.",
    "Preset": "Overall acoustic room profile and surround wrap intensity.",
    "Device": "auto uses your NVIDIA GPU if available (CUDA acceleration), otherwise CPU fallback.",
    "Split vocals": "Splits lead vocals from backing vocals. Lead stays front; backing wraps in 3D around you.",
    "Vocal mode": "auto detects doubled vocals to prevent comb-filtering. forward/spread overrides.",
    "Rear gain (dB)": "Master volume offset for the rear surround field.",
    "Rear below front": "Target dB attenuation for rear speakers compared to the front soundstage.",
    "Backing gain": "Volume of backing vocals ('auto' or custom dB).",
    "Demucs model": "htdemucs_ft = 4 stems (best quality); htdemucs_6s adds discrete guitar and piano.",
    "Rear decorrelation": "Phase-safe decorrelation feeding side and rear speakers for wide enveloping ambience.",
    "Detail recovery": "Reinjects subtle micro-transients, air, and room detail lost during neural separation.",
    "Binaural depth": "Translates binaural dummy-head ITD cues into dynamic front/back leaning.",
    "Vocal roles": "auto verifies which stem is true lead vs backing. keep/swap overrides.",
    "Atmos objects": "Exports backing vocals as discrete moving 3D audio objects with dynamic pan-tracking & 360° orbit!",
}

PLACE_TIP = ("Where each instrument is placed in the room.\n"
             "auto = song-adaptive positioning based on direct/ambient analysis.\n"
             "front / side / rear forces that whole stem into the chosen zone.")


class ToolTip:
    """Minimal flat modern dark hover tooltip."""
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
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        f = tk.Frame(tw, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        f.pack()
        tk.Label(f, text=self.text, justify="left", bg=CARD, fg=FG,
                 padx=10, pady=7, wraplength=380, font=("Segoe UI", 9)).pack()

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


# ---- Modern App -------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.running = False
        self.stop_flag = False
        self.q = queue.Queue()
        self.jobs = {}          # iid -> {"path": ..., "kind": ...}
        self._cfg_vars = {}     # name -> tk.StringVar
        self.cfg = self._load_cfg()

        root.title("SurroundUpmix Studio")
        root.configure(bg=BG)
        root.geometry("940x880")
        root.minsize(840, 600)

        self._style()
        self._build()
        self._apply_cfg()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._drain)

    # ------------------------------------------------------------ config
    def _load_cfg(self):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self):
        try:
            cfg = {name: var.get() for name, var in self._cfg_vars.items()}
            try:
                cfg["geometry"] = self.root.geometry()
            except Exception:
                pass
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _apply_cfg(self):
        geom = self.cfg.get("geometry")
        if geom:
            try:
                self.root.geometry(geom)
            except Exception:
                pass
        for name, var in self._cfg_vars.items():
            if name in self.cfg:
                var.set(self.cfg[name])
        self._on_output()
        self._on_preset()
        self._on_binaural()
        self._update_context()
        self._draw_stage()

    def _reg(self, name, var):
        self._cfg_vars[name] = var
        return var

    def _on_close(self):
        self._save_cfg()
        self.root.destroy()

    # ------------------------------------------------------------ flat styling
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")

        # Global defaults: NO 1px solid borders, clean flat surfaces
        s.configure(".", background=PANEL, foreground=FG, fieldbackground=INPUT_BG,
                    bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL,
                    troughcolor=CARD, focuscolor=ACCENT, borderwidth=0)
        s.configure("TFrame", background=PANEL, borderwidth=0)
        s.configure("Bg.TFrame", background=BG, borderwidth=0)
        s.configure("Card.TFrame", background=CARD, borderwidth=0)

        s.configure("TLabel", background=PANEL, foreground=FG, font=("Segoe UI", 9))
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Card.TLabel", background=CARD, foreground=FG, font=("Segoe UI", 9))

        s.configure("Header.TLabel", background=BG, foreground=FG, font=("Segoe UI", 16, "bold"))
        s.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("CardTitle.TLabel", background=PANEL, foreground=FG, font=("Segoe UI", 10, "bold"))

        # Flat inputs
        s.configure("TEntry", fieldbackground=INPUT_BG, foreground=FG,
                    insertcolor=FG, bordercolor=BORDER, padding=(8, 6), borderwidth=0)
        s.configure("TCombobox", fieldbackground=INPUT_BG, background=INPUT_BG,
                    foreground=FG, arrowcolor=FG, bordercolor=BORDER, padding=(8, 5), borderwidth=0)
        s.map("TCombobox", fieldbackground=[("readonly", INPUT_BG)], foreground=[("readonly", FG)],
              selectbackground=[("", INPUT_BG)], selectforeground=[("", FG)])
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.borderwidth", "0")

        # Flat Treeview (Queue)
        s.configure("Queue.Treeview", background=CARD, fieldbackground=CARD,
                    foreground=FG, bordercolor=CARD, borderwidth=0, rowheight=32,
                    font=("Segoe UI", 9))
        s.map("Queue.Treeview", background=[("selected", SEL)], foreground=[("selected", FG)])
        s.configure("Queue.Treeview.Heading", background=INPUT_BG, foreground=MUTED,
                    bordercolor=INPUT_BG, relief="flat", font=("Segoe UI", 9, "bold"), padding=(8, 6))
        s.map("Queue.Treeview.Heading", background=[("active", INPUT_BG)])

        # Flat Buttons (Pill-like, no harsh borders)
        s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    bordercolor=ACCENT, focusthickness=0, padding=(18, 9),
                    font=("Segoe UI", 10, "bold"), borderwidth=0)
        s.map("Accent.TButton", background=[("active", ACCENT_HI), ("disabled", BORDER)],
              foreground=[("disabled", MUTED)])

        s.configure("Stop.TButton", background=STOP, foreground="#ffffff",
                    bordercolor=STOP, padding=(16, 9), font=("Segoe UI", 10, "bold"), borderwidth=0)
        s.map("Stop.TButton", background=[("active", "#c03537"), ("disabled", BORDER)],
              foreground=[("disabled", MUTED)])

        s.configure("Pill.TButton", background=INPUT_BG, foreground=FG,
                    bordercolor=INPUT_BG, padding=(12, 6), font=("Segoe UI", 9), borderwidth=0)
        s.map("Pill.TButton", background=[("active", CARD), ("pressed", BORDER)])

        s.configure("Icon.TButton", background=INPUT_BG, foreground=FG,
                    bordercolor=INPUT_BG, padding=(8, 5), font=("Segoe UI", 10), borderwidth=0)
        s.map("Icon.TButton", background=[("active", CARD)])

        s.configure("Link.TButton", background=PANEL, foreground=MUTED,
                    bordercolor=PANEL, focusthickness=0, padding=(2, 4),
                    font=("Segoe UI", 9, "bold"), anchor="w", borderwidth=0)
        s.map("Link.TButton", foreground=[("active", ACCENT)])

        # Flat Notebook tabs
        s.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab", background=CARD, foreground=MUTED,
                    padding=(16, 7), borderwidth=0, font=("Segoe UI", 9, "bold"))
        s.map("TNotebook.Tab", background=[("selected", PANEL)],
              foreground=[("selected", ACCENT)])

    # ------------------------------------------------------------ layout
    def _build(self):
        # Header (Flat & Clean)
        head = ttk.Frame(self.root, style="Bg.TFrame")
        head.pack(fill="x", padx=20, pady=(16, 6))

        hrow = ttk.Frame(head, style="Bg.TFrame")
        hrow.pack(fill="x")
        ttk.Label(hrow, text="SurroundUpmix Studio", style="Header.TLabel").pack(side="left")

        # Studio Version Badge
        vbadge = tk.Label(hrow, text="v2.5 · 3D ATMOS", bg="#212430", fg=ACCENT,
                          font=("Segoe UI", 8, "bold"), padx=8, pady=3)
        vbadge.pack(side="left", padx=(10, 0))

        ttk.Label(head, text="Intelligent Stereo ➔ 5.1 / 7.1 / 7.1.2 Dolby Atmos Master Suite · Neural Audio Engineering",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        # Top Actions Bar
        self._build_actions(self.root)

        # Scrollable Body Canvas (Zero borders, clean scrolling)
        outer = ttk.Frame(self.root, style="Bg.TFrame")
        outer.pack(fill="both", expand=True)
        self._canvas = canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        body = ttk.Frame(canvas, style="Bg.TFrame", padding=(20, 6))
        self._body_win = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._body_win, width=e.width))
        canvas.bind_all("<MouseWheel>", self._on_wheel)

        # Section 1: Unified Smart Queue Card
        self._build_smart_queue(body)

        # Section 2: Spatial Target & 2D Studio Soundstage
        self._build_spatial_section(body)

        # Section 3: Advanced Settings (4-Tab Flat Notebook)
        self._build_advanced_section(body)

        # Section 4: Live Console Monitor
        self._build_log_section(body)

    def _on_wheel(self, event):
        c = getattr(self, "_canvas", None)
        if c is not None:
            c.yview_scroll(int(-event.delta / 120), "units")

    # ------------------------------------------------------------ flat card wrapper
    def _create_card(self, parent, title, hint=""):
        """Create a modern borderless flat card container."""
        card = ttk.Frame(parent)
        card.pack(fill="x", pady=(0, 14))

        # Header line
        hdr = ttk.Frame(card)
        hdr.pack(fill="x", padx=4, pady=(0, 6))
        ttk.Label(hdr, text=title, style="CardTitle.TLabel").pack(side="left")
        if hint:
            ttk.Label(hdr, text=hint, style="Muted.TLabel").pack(side="right")

        content = ttk.Frame(card, style="TFrame", padding=14)
        content.pack(fill="both", expand=True)
        return content

    # ------------------------------------------------------------ top actions
    def _build_actions(self, parent):
        self.act = ttk.Frame(parent, style="Bg.TFrame")
        self.act.pack(fill="x", padx=20, pady=(6, 10))

        self.start_btn = ttk.Button(self.act, text="▶  Start Queue",
                                    style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(self.act, text="■  Stop", style="Stop.TButton",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        # Right-side progress and status
        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.act, textvariable=self.status, style="BgMuted.TLabel",
                  font=("Segoe UI", 9, "bold")).pack(side="right")

        self.prog = ttk.Progressbar(self.act, orient="horizontal", mode="determinate", length=180)
        self.prog.pack(side="right", padx=(0, 16))

        self.stage_lbl = tk.StringVar(value="")
        ttk.Label(self.act, textvariable=self.stage_lbl, style="Header.TLabel",
                  font=("Segoe UI", 9)).pack(side="right", padx=(0, 12))

    # ------------------------------------------------------------ 1. Smart Queue
    def _build_smart_queue(self, body):
        qc = self._create_card(body, "1. SMART INPUT QUEUE",
                               "Drag songs, albums, or stems folders here" if HAVE_DND else "")

        # Action Toolbar (Modern Flat Pills)
        tb = ttk.Frame(qc)
        tb.pack(fill="x", pady=(0, 8))

        ttk.Button(tb, text="＋ Add Songs…", style="Pill.TButton",
                   command=self._add_files).pack(side="left")
        ttk.Button(tb, text="＋ Add Folder…", style="Pill.TButton",
                   command=self._add_folder).pack(side="left", padx=(6, 0))
        ttk.Button(tb, text="＋ Add Stems Folder…", style="Pill.TButton",
                   command=self._add_stems_folder).pack(side="left", padx=(6, 0))

        ob = ttk.Button(tb, text="📁", style="Icon.TButton", command=self._open_input)
        ob.pack(side="left", padx=(10, 0))
        ToolTip(ob, "Open selected item in Explorer")

        ttk.Button(tb, text="▲ Up", style="Pill.TButton",
                   command=self._move_up).pack(side="left", padx=(14, 0))
        ttk.Button(tb, text="▼ Down", style="Pill.TButton",
                   command=self._move_down).pack(side="left", padx=(4, 0))
        ttk.Button(tb, text="Remove", style="Pill.TButton",
                   command=self._remove_sel).pack(side="left", padx=(10, 0))
        ttk.Button(tb, text="Clear", style="Pill.TButton",
                   command=self._clear_queue).pack(side="left", padx=(6, 0))

        # Flat Treeview
        tw = ttk.Frame(qc)
        tw.pack(fill="both", expand=True)

        tree = ttk.Treeview(tw, style="Queue.Treeview", show="headings",
                            columns=("name", "type", "details", "status"),
                            height=5, selectmode="extended")
        tree.heading("name", text="Input Item", anchor="w")
        tree.heading("type", text="Kind", anchor="w")
        tree.heading("details", text="Processing / Stems Details", anchor="w")
        tree.heading("status", text="Status", anchor="w")

        tree.column("name", anchor="w", width=340)
        tree.column("type", anchor="w", width=110, stretch=False)
        tree.column("details", anchor="w", width=280)
        tree.column("status", anchor="w", width=120, stretch=False)

        for tg, col in (("queued", MUTED), ("running", ACCENT),
                        ("done", OK), ("failed", STOP)):
            tree.tag_configure(tg, foreground=col)
        tree.tag_configure("stems_badge", foreground=OK)
        tree.tag_configure("song_badge", foreground=ACCENT_HI)

        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tw, command=tree.yview)
        sb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=sb.set)
        self.tree = tree

        if HAVE_DND:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
            tree.drop_target_register(DND_FILES)
            tree.dnd_bind("<<Drop>>", self._on_drop)

    # ------------------------------------------------------------ 2. Spatial & Soundstage
    def _build_spatial_section(self, body):
        sc = self._create_card(body, "2. SPATIAL TARGET & STUDIO SOUNDSTAGE")

        row = ttk.Frame(sc)
        row.pack(fill="both", expand=True)

        # Left Column: Format, Preset, Hardware & Output Folder
        left = ttk.Frame(row)
        left.pack(side="left", fill="both", expand=True, padx=(0, 16))

        grid = ttk.Frame(left)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="sp")
        grid.columnconfigure(1, weight=1, uniform="sp")
        grid.columnconfigure(2, weight=1, uniform="sp")

        self.output, self._output_cb = self._combo(
            grid, "Output Format", OUTPUTS, "7.1.2", 0, 0, TIPS["Output"], cb=True)
        self._reg("output", self.output)
        self._output_cb.bind("<<ComboboxSelected>>", lambda e: self._on_output())

        self.preset, self._preset_cb = self._combo(
            grid, "Spatial Preset", PRESETS, "immersive", 0, 1, TIPS["Preset"], cb=True)
        self._reg("preset", self.preset)
        self._preset_cb.bind("<<ComboboxSelected>>", lambda e: self._on_preset())

        self.device, self._device_box = self._combo(
            grid, "Hardware Acceleration", DEVICES, "auto", 0, 2, TIPS["Device"], cb=True)
        self._reg("device", self.device)

        self.output_desc = tk.StringVar(value=OUTPUT_DESC["7.1.2"])
        ttk.Label(left, textvariable=self.output_desc, style="Hint.TLabel",
                  wraplength=430, justify="left").pack(fill="x", pady=(8, 4))

        self.preset_desc = tk.StringVar(value=PRESET_DESC["immersive"])
        ttk.Label(left, textvariable=self.preset_desc, style="Hint.TLabel",
                  wraplength=430, justify="left").pack(fill="x", pady=(4, 6))

        # Flat character meters
        self._meters = []
        mf = ttk.Frame(left)
        mf.pack(fill="x", pady=(0, 6))
        for _ in range(3):
            mr = ttk.Frame(mf)
            mr.pack(fill="x", pady=2)
            lab = ttk.Label(mr, text="", style="Muted.TLabel", width=16, anchor="w")
            lab.pack(side="left")
            cv = tk.Canvas(mr, height=7, width=150, bg=INPUT_BG, highlightthickness=0)
            cv.pack(side="left")
            self._meters.append((lab, cv))

        # Output folder picker
        ttk.Label(left, text="Output Directory (blank = 'Output' folder next to each input)",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        of = ttk.Frame(left)
        of.pack(fill="x", pady=(3, 0))
        self.outdir = self._reg("outdir", tk.StringVar())
        ttk.Entry(of, textvariable=self.outdir).pack(side="left", fill="x", expand=True)
        ttk.Button(of, text="Browse…", style="Pill.TButton",
                   command=self._browse_out).pack(side="left", padx=(6, 0))
        ob = ttk.Button(of, text="📁", style="Icon.TButton", command=self._open_output)
        ob.pack(side="left", padx=(4, 0))
        ToolTip(ob, "Open the output folder in Explorer")

        # Right Column: Glow Soundstage Visualizer Canvas
        right = ttk.Frame(row)
        right.pack(side="right", fill="both")

        r_hdr = ttk.Frame(right)
        r_hdr.pack(fill="x", pady=(0, 4))
        ttk.Label(r_hdr, text="STUDIO SOUNDSTAGE", style="CardTitle.TLabel",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(r_hdr, text="2D Live Map", style="Muted.TLabel",
                  font=("Segoe UI", 8)).pack(side="right")

        self.stage = tk.Canvas(right, width=290, height=220, bg=STAGE_BG,
                               highlightthickness=0)
        self.stage.pack()
        ToolTip(self.stage, "Studio Soundstage (Live Acoustic Preview):\n"
                            "• Speakers adapt to 5.1, 7.1, or 7.1.2 Atmos layout\n"
                            "• Colored nodes show instrument positions live\n"
                            "• Backing orbitals show Atmos 3D motion envelope")

    def _draw_stage(self):
        """Draw flat modern studio soundstage with speaker nodes and glowing instruments."""
        cv = getattr(self, "stage", None)
        if cv is None:
            return
        cv.delete("all")
        w, h = 290, 220
        cx, cy = w // 2, h // 2 + 10

        # Studio acoustic perimeter rings
        cv.create_oval(cx - 85, cy - 85, cx + 85, cy + 85, outline=STAGE_RING, width=1)
        cv.create_oval(cx - 50, cy - 50, cx + 50, cy + 50, outline=STAGE_RING, width=1)

        # Listener head icon in center (Modern flat circle with directional marker)
        cv.create_oval(cx - 10, cy - 10, cx + 10, cy + 10, fill="#343741", outline="")
        cv.create_polygon(cx - 3, cy - 10, cx + 3, cy - 10, cx, cy - 15, fill="#5865f2")

        out = self.output.get() if hasattr(self, "output") else "7.1.2"
        is_all_obj = (out == "Dolby Atmos (20ch Objects)")
        is_51 = (out == "5.1")
        is_71 = (out in ("7.1", "7.1.2", "Dolby Atmos", "ADM BWF", "Dolby Atmos (20ch Objects)"))
        is_height = (out in ("7.1.2", "Dolby Atmos", "ADM BWF", "Dolby Atmos (20ch Objects)"))
        preset = self.preset.get() if hasattr(self, "preset") else "immersive"

        # Speakers: [(name, x, y, active, color)]
        if is_all_obj:
            speakers = [
                ("C", cx, 24, True, SPK_ON),
                ("L", 36, 32, True, SPK_ON),
                ("R", w - 36, 32, True, SPK_ON),
                ("Sub", 74, 26, True, OK),
                ("Lss", 26, cy, True, SPK_ON),
                ("Rss", w - 26, cy, True, SPK_ON),
                ("Lrs", 50, h - 26, True, SPK_ON),
                ("Rrs", w - 50, h - 26, True, SPK_ON),
                ("Tfl", cx - 44, cy - 50, True, SPK_HEIGHT),
                ("Tfr", cx + 44, cy - 50, True, SPK_HEIGHT),
                ("Tml", cx - 44, cy, True, SPK_HEIGHT),
                ("Tmr", cx + 44, cy, True, SPK_HEIGHT),
                ("Trl", cx - 44, cy + 46, True, SPK_HEIGHT),
                ("Trr", cx + 44, cy + 46, True, SPK_HEIGHT),
            ]
        else:
            speakers = [
                ("C", cx, 24, True, SPK_ON),
                ("L", 36, 32, True, SPK_ON),
                ("R", w - 36, 32, True, SPK_ON),
                ("Sub", 74, 26, True, OK),
                ("Lss", 26, cy, True, SPK_ON),
                ("Rss", w - 26, cy, True, SPK_ON),
                ("Lrs", 50, h - 26, is_71, SPK_ON if is_71 else SPK_OFF),
                ("Rrs", w - 50, h - 26, is_71, SPK_ON if is_71 else SPK_OFF),
                ("Ltm", cx - 45, cy - 38, is_height, SPK_HEIGHT if is_height else SPK_OFF),
                ("Rtm", cx + 45, cy - 38, is_height, SPK_HEIGHT if is_height else SPK_OFF),
            ]

        for nm, sx, sy, active, col in speakers:
            sz = 6 if nm.startswith("T") or nm.startswith("Ltm") or nm.startswith("Rtm") else 8
            fill = col if active else SPK_OFF
            # Pill speaker
            cv.create_rectangle(sx - sz, sy - sz, sx + sz, sy + sz, fill=fill, outline="")
            cv.create_text(sx, sy + sz + 6, text=nm, fill=MUTED if active else "#2e3038",
                           font=("Segoe UI", 7, "bold"))

        # Instrument nodes based on preset
        spread_mult = {"focus": 0.55, "immersive": 1.0, "concert": 1.25, "envelop": 1.45}.get(preset, 1.0)

        # 1. Lead Vocal (Coral Glow) - Center Front
        # Soft outer halo
        cv.create_oval(cx - 10, cy - 56 - 10, cx + 10, cy - 56 + 10, fill="#3d2125", outline="")
        cv.create_oval(cx - 6, cy - 56 - 6, cx + 6, cy - 56 + 6, fill=COL_VOCAL, outline="")
        cv.create_text(cx, cy - 68, text="Lead", fill=COL_VOCAL, font=("Segoe UI", 8, "bold"))

        # 2. Bass & Drums (Emerald Glow) - Front/Sub
        cv.create_oval(cx - 26 - 5, cy - 36 - 5, cx - 26 + 5, cy - 36 + 5, fill=COL_BASS, outline="")
        cv.create_oval(cx + 26 - 5, cy - 36 - 5, cx + 26 + 5, cy - 36 + 5, fill=COL_BASS, outline="")
        cv.create_text(cx, cy - 37, text="Drums / Bass", fill=COL_BASS, font=("Segoe UI", 7))

        # 3. Other / Guitars / Synths (Golden Glow) - Side Spread
        dx_other = int(58 * min(1.3, spread_mult))
        cv.create_oval(cx - dx_other - 6, cy - 8 - 6, cx - dx_other + 6, cy - 8 + 6, fill=COL_OTHER, outline="")
        cv.create_oval(cx + dx_other - 6, cy - 8 - 6, cx + dx_other + 6, cy - 8 + 6, fill=COL_OTHER, outline="")
        cv.create_text(cx - dx_other, cy + 5, text="Gtr/Syn", fill=COL_OTHER, font=("Segoe UI", 7))
        cv.create_text(cx + dx_other, cy + 5, text="Gtr/Syn", fill=COL_OTHER, font=("Segoe UI", 7))

        # 4. Backing / 3D Objects (Pink/Magenta Glow) - Rear Envelope / Orbit
        dx_back = int(50 * spread_mult)
        dy_back = int(44 * spread_mult)
        # Halos
        cv.create_oval(cx - dx_back - 8, cy + dy_back - 8, cx - dx_back + 8, cy + dy_back + 8, fill="#3b1b2f", outline="")
        cv.create_oval(cx + dx_back - 8, cy + dy_back - 8, cx + dx_back + 8, cy + dy_back + 8, fill="#3b1b2f", outline="")
        cv.create_oval(cx - dx_back - 5, cy + dy_back - 5, cx - dx_back + 5, cy + dy_back + 5, fill=COL_BACKING, outline="")
        cv.create_oval(cx + dx_back - 5, cy + dy_back - 5, cx + dx_back + 5, cy + dy_back + 5, fill=COL_BACKING, outline="")
        cv.create_text(cx, cy + dy_back + 10, text="3D Backing / Objects", fill=COL_BACKING, font=("Segoe UI", 8, "bold"))

        # 360° Orbit trajectory circle when Atmos objects active
        if hasattr(self, "adm_objects") and self.adm_objects.get() == "on":
            cv.create_oval(cx - 68, cy - 68, cx + 68, cy + 68, outline=COL_BACKING, dash=(3, 4), width=1)

    # ------------------------------------------------------------ 3. Advanced 4-Tab Section
    def _build_advanced_section(self, body):
        self._adv_open = False
        self.adv_btn = ttk.Button(body, text="▸  Advanced Settings  (AI models, 3D Atmos, acoustic balance, routing)",
                                  style="Link.TButton", command=self._toggle_adv)
        self.adv_btn.pack(fill="x", pady=(0, 4))

        self.adv = ttk.Frame(body, style="TFrame")

        # 4-Tab Flat Notebook
        self.adv_tabs = ttk.Notebook(self.adv)
        self.adv_tabs.pack(fill="both", expand=True, pady=(4, 8))

        # Tab 1: AI & Demucs
        self._build_tab_ai(self.adv_tabs)

        # Tab 2: 3D Atmos & Space
        self._build_tab_atmos(self.adv_tabs)

        # Tab 3: Balance & Levels
        self._build_tab_balance(self.adv_tabs)

        # Tab 4: Per-Instrument Matrix
        self._build_tab_routing(self.adv_tabs)

    def _toggle_adv(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.adv.pack(fill="x", before=self.logframe, pady=(0, 8))
            self.adv_btn.configure(text="▾  Advanced Settings  (AI models, 3D Atmos, acoustic balance, routing)")
        else:
            self.adv.pack_forget()
            self.adv_btn.configure(text="▸  Advanced Settings  (AI models, 3D Atmos, acoustic balance, routing)")

    def _build_tab_ai(self, notebook):
        page = ttk.Frame(notebook, style="TFrame", padding=14)
        notebook.add(page, text=" 🎛️ AI & Demucs ")

        for c in range(3):
            page.columnconfigure(c, weight=1, uniform="ai")

        self.model, self._model_box = self._combo(
            page, "Demucs Model", MODELS, "htdemucs_ft", 0, 0, TIPS["Demucs model"], cb=True)
        self._reg("model", self.model)
        self._model_box.bind("<<ComboboxSelected>>", lambda e: self._update_context())

        self.split, self._split_box = self._combo(
            page, "Split Vocals (Karaoke)", SPLIT, "auto", 0, 1, TIPS["Split vocals"], cb=True)
        self._reg("split", self.split)
        self._split_box.bind("<<ComboboxSelected>>", lambda e: self._update_context())

        self.vocal = self._reg("vocal", self._combo(
            page, "Vocal Haas/Double Mode", VOCAL, "auto", 0, 2, TIPS["Vocal mode"]))

        self.vroles = self._reg("vroles", self._combo(
            page, "Vocal Roles (Swap/Detect)", VROLES, "auto", 1, 0, TIPS["Vocal roles"]))

        self.recover = self._reg("recover", self._combo(
            page, "Detail Recovery (Master)", ["on", "off"], "on", 1, 1, TIPS["Detail recovery"]))

    def _build_tab_atmos(self, notebook):
        page = ttk.Frame(notebook, style="TFrame", padding=14)
        notebook.add(page, text=" 🌌 3D Space & Atmos ")

        for c in range(3):
            page.columnconfigure(c, weight=1, uniform="at")

        self.adm_objects, self._adm_obj_cb = self._combo(
            page, "Discrete 3D Audio Objects", ["off", "on"], "off", 0, 0, TIPS["Atmos objects"], cb=True)
        self._reg("adm_objects", self.adm_objects)
        self._adm_obj_cb.bind("<<ComboboxSelected>>", lambda e: self._draw_stage())

        self.decorr = self._reg("decorr", self._combo(
            page, "Rear Decorrelation", ["on", "off"], "on", 0, 1, TIPS["Rear decorrelation"]))

        # Smart 3D indicator pill
        stat_box = ttk.Frame(page)
        stat_box.grid(row=0, column=2, sticky="ew", padx=6, pady=4)
        ttk.Label(stat_box, text="Smart 3D Motion Engine", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(stat_box, text="● 360° Orbit & Whisper Active", foreground=OK,
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(5, 0))

        # Binaural depth slider
        brow = ttk.Frame(page)
        brow.grid(row=1, column=0, columnspan=3, sticky="ew", padx=6, pady=(14, 2))
        blab = ttk.Label(brow, text="Binaural Depth:", style="Muted.TLabel")
        blab.pack(side="left")
        self.binaural = self._reg("binaural", tk.DoubleVar(value=0))
        self.binaural_lbl = tk.StringVar(value="off")
        ttk.Label(brow, textvariable=self.binaural_lbl, style="Muted.TLabel", width=6).pack(side="right")
        bsc = ttk.Scale(brow, from_=0, to=100, orient="horizontal",
                        variable=self.binaural, command=self._on_binaural)
        bsc.pack(side="left", fill="x", expand=True, padx=8)
        ToolTip(blab, TIPS["Binaural depth"])
        ToolTip(bsc, TIPS["Binaural depth"])

    def _build_tab_balance(self, notebook):
        page = ttk.Frame(notebook, style="TFrame", padding=14)
        notebook.add(page, text=" ⚖️ Acoustic Balance ")

        for c in range(3):
            page.columnconfigure(c, weight=1, uniform="bal")

        self.rear_gain = self._reg("rear_gain", self._entry(
            page, "Rear Gain (dB)", "0", 0, 0, TIPS["Rear gain (dB)"]))
        self.rear_below = self._reg("rear_below", self._entry(
            page, "Rear Below Front (dB)", "", 0, 1, TIPS["Rear below front"]))
        self.backing = self._reg("backing", self._entry(
            page, "Backing Choir Gain (dB / auto)", "auto", 0, 2, TIPS["Backing gain"]))

    def _build_tab_routing(self, notebook):
        page = ttk.Frame(notebook, style="TFrame", padding=12)
        notebook.add(page, text=" 📍 Instrument Routing ")

        for c, txt in enumerate(("", "Zone", "Level dB", "Spread", "Ctr/LFE", "Mute", "Solo")):
            ttk.Label(page, text=txt, style="Muted.TLabel").grid(
                row=0, column=c, sticky="w", padx=6, pady=(0, 4))

        self.place, self.level, self.mute, self.solo = {}, {}, {}, {}
        self.spread, self.lfe, self.center = {}, {}, None
        self._instr_widgets = {}

        for i, stem in enumerate(INSTRUMENTS, start=1):
            w = []
            nl = ttk.Label(page, text=stem.capitalize(), style="Muted.TLabel", width=10)
            nl.grid(row=i, column=0, sticky="w", padx=6, pady=3)
            w.append(nl)

            zv = tk.StringVar(value="auto")
            zb = ttk.Combobox(page, textvariable=zv, state="readonly", width=8,
                              values=["auto", "front", "side", "rear"])
            zb.grid(row=i, column=1, sticky="w", padx=6)
            zb.bind("<MouseWheel>", lambda e: "break")
            zb.bind("<<ComboboxSelected>>", lambda e: self._draw_stage())
            self.place[stem] = self._reg("place_" + stem, zv)
            w.append(zb)

            lv = tk.StringVar(value="0")
            le = ttk.Entry(page, textvariable=lv, width=6)
            le.grid(row=i, column=2, sticky="w", padx=6)
            self.level[stem] = self._reg("level_" + stem, lv)
            w.append(le)

            if stem != "bass":
                spv = tk.StringVar(value="")
                se = ttk.Entry(page, textvariable=spv, width=6)
                se.grid(row=i, column=3, sticky="w", padx=6)
                self.spread[stem] = self._reg("spread_" + stem, spv)
                w.append(se)

            if stem == "vocals":
                cv = tk.StringVar(value="")
                ce = ttk.Entry(page, textvariable=cv, width=6)
                ce.grid(row=i, column=4, sticky="w", padx=6)
                self.center = self._reg("center_vocals", cv)
                w.append(ce)
            elif stem in ("bass", "drums"):
                fv = tk.StringVar(value="")
                fe = ttk.Entry(page, textvariable=fv, width=6)
                fe.grid(row=i, column=4, sticky="w", padx=6)
                self.lfe[stem] = self._reg("lfe_" + stem, fv)
                w.append(fe)

            mv = tk.BooleanVar(value=False)
            mc = ttk.Checkbutton(page, variable=mv)
            mc.grid(row=i, column=5, padx=12)
            self.mute[stem] = self._reg("mute_" + stem, mv)
            w.append(mc)

            sv = tk.BooleanVar(value=False)
            sc = ttk.Checkbutton(page, variable=sv)
            sc.grid(row=i, column=6, padx=12)
            self.solo[stem] = self._reg("solo_" + stem, sv)
            w.append(sc)

            self._instr_widgets[stem] = w

        foot = ttk.Frame(page)
        foot.grid(row=len(INSTRUMENTS) + 1, column=0, columnspan=7, sticky="ew", pady=(10, 0))
        self.instr_detected = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.instr_detected, style="Muted.TLabel").pack(side="left")
        ttk.Button(foot, text="Reset Instruments", style="Link.TButton",
                   command=self._reset_instruments).pack(side="right")

        ToolTip(page, PLACE_TIP)

    # ------------------------------------------------------------ 4. Live Log
    def _build_log_section(self, body):
        lw = self.logframe = ttk.Frame(body, style="Bg.TFrame")
        lw.pack(fill="both", expand=True, pady=(4, 0))

        log_head = ttk.Frame(lw, style="Bg.TFrame")
        log_head.pack(fill="x", pady=(0, 6))
        ttk.Label(log_head, text="LIVE CONSOLE & MONITOR", style="Header.TLabel",
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(log_head, text="Clear Log", style="Link.TButton",
                   command=self._clear_log).pack(side="right")

        self.log = tk.Text(lw, bg=LOG_BG, fg="#c8ccd2", insertbackground=FG,
                           relief="flat", height=6, wrap="word", padx=12, pady=10,
                           font=("Cascadia Mono", 10), highlightthickness=0, borderwidth=0)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(lw, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=lsb.set, state="disabled")

    # ------------------------------------------------------------ helpers
    def _combo(self, parent, label, values, default, r, c, tip=None, cb=False):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, sticky="ew", padx=6, pady=4)
        lab = ttk.Label(f, text=label, style="Muted.TLabel")
        lab.pack(anchor="w")
        var = tk.StringVar(value=default)
        box = ttk.Combobox(f, textvariable=var, values=values, state="readonly")
        box.pack(fill="x", pady=(2, 0))
        box.bind("<MouseWheel>", lambda e: "break")
        if tip:
            ToolTip(lab, tip)
            ToolTip(box, tip)
        return (var, box) if cb else var

    def _entry(self, parent, label, default, r, c, tip=None):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, sticky="ew", padx=6, pady=4)
        lab = ttk.Label(f, text=label, style="Muted.TLabel")
        lab.pack(anchor="w")
        var = tk.StringVar(value=default)
        e = ttk.Entry(f, textvariable=var)
        e.pack(fill="x", pady=(2, 0))
        if tip:
            ToolTip(lab, tip)
            ToolTip(e, tip)
        return var

    def _on_output(self):
        self.output_desc.set(OUTPUT_DESC.get(self.output.get(), ""))
        self._draw_stage()

    def _on_preset(self):
        self.preset_desc.set(PRESET_DESC.get(self.preset.get(), ""))
        self._update_meters()
        self._draw_stage()

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
                                    fill=ACCENT if i < val else INPUT_BG, outline="")

    def _on_binaural(self, _=None):
        v = int(float(self.binaural.get()))
        self.binaural_lbl.set("off" if v < 1 else "%d%%" % v)

    # ------------------------------------------------------------ folders
    def _open_folder(self, path):
        if path and os.path.isdir(path):
            try:
                os.startfile(path)
            except Exception:
                self._append("! could not open %s\n" % path)

    def _open_input(self):
        sel = self.tree.selection() or self.tree.get_children()
        if not sel:
            self._append("! nothing to open (add something first)\n")
            return
        p = self.jobs.get(sel[0], {}).get("path", "")
        self._open_folder(p if os.path.isdir(p) else os.path.dirname(p))

    def _open_output(self):
        od = self.outdir.get().strip()
        if od:
            self._open_folder(od)
        else:
            self._append("! output folder is blank (files go into an 'Output' folder next to each song)\n")

    def _browse_out(self):
        p = filedialog.askdirectory(title="Choose an output folder")
        if p:
            self.outdir.set(p)

    def _refresh_counts(self):
        n = len(self.jobs)
        songs = sum(1 for j in self.jobs.values() if j["kind"] == "song")
        stems = sum(1 for j in self.jobs.values() if j["kind"] == "stems")
        if n == 0:
            self.status.set("Ready")
        else:
            details = []
            if songs: details.append(f"{songs} song" + ("s" if songs != 1 else ""))
            if stems: details.append(f"{stems} stems folder" + ("s" if stems != 1 else ""))
            self.status.set(f"{n} in queue ({', '.join(details)})")
        self._update_context()

    # ------------------------------------------------------------ queue ops
    def _detect_instr_text(self, folder):
        from surroundupmix.stemnames import folder_stem_map
        from surroundupmix.io import find_stem
        det = [nm for nm in INSTRUMENTS if find_stem(folder, nm)]
        for nm in folder_stem_map(folder):
            if nm not in det:
                det.append(nm)
        return ", ".join(det)

    def _add_paths(self, paths):
        added = 0
        for path, kind in expand_inputs(paths):
            if any(j["path"] == path for j in self.jobs.values()):
                continue
            base = os.path.basename(path.rstrip(os.sep))
            if kind == "stems":
                instr = self._detect_instr_text(path)
                iid = self.tree.insert("", "end", tags=("queued", "stems_badge"),
                                       values=(base, "🎛️ Stems", instr or "Stems", "⏳ queued"))
            else:
                iid = self.tree.insert("", "end", tags=("queued", "song_badge"),
                                       values=(base, "🎵 Song", "Demucs AI Split", "⏳ queued"))
            self.jobs[iid] = {"path": path, "kind": kind}
            added += 1
        self._refresh_counts()
        if added == 0 and paths:
            self._append("! nothing addable in that drop/selection\n")

    def _add_one_stems_folder(self, folder):
        if not folder or any(j["path"] == folder for j in self.jobs.values()):
            return
        base = os.path.basename(folder.rstrip(os.sep))
        instr = self._detect_instr_text(folder)
        iid = self.tree.insert("", "end", tags=("queued", "stems_badge"),
                                values=(base, "🎛️ Stems", instr or "Stems", "⏳ queued"))
        self.jobs[iid] = {"path": folder, "kind": "stems"}
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

    def _on_drop(self, event):
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        self._add_paths(paths)

    def _move_up(self):
        if self.running:
            return
        sel = self.tree.selection()
        if not sel:
            return
        for iid in sel:
            idx = self.tree.index(iid)
            if idx > 0:
                self.tree.move(iid, "", idx - 1)

    def _move_down(self):
        if self.running:
            return
        sel = self.tree.selection()
        if not sel:
            return
        for iid in reversed(sel):
            idx = self.tree.index(iid)
            children = self.tree.get_children()
            if idx < len(children) - 1:
                self.tree.move(iid, "", idx + 1)

    def _remove_sel(self):
        if self.running:
            return
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.jobs.pop(iid, None)
        self._refresh_counts()

    def _clear_queue(self):
        if self.running:
            return
        for iid in list(self.jobs):
            self.tree.delete(iid)
        self.jobs.clear()
        self._refresh_counts()

    # ------------------------------------------------------------ per-instrument
    def _set_enabled(self, w, on):
        try:
            w.configure(state=("readonly" if isinstance(w, ttk.Combobox) else "normal")
                        if on else "disabled")
        except Exception:
            pass

    def _target_instruments(self):
        det = {"bass", "drums", "vocals", "other"}
        if self.model.get() == "htdemucs_6s":
            det |= {"guitar", "piano"}
        if self.split.get() != "off":
            det.add("backing")
        return det

    def _update_context(self):
        try:
            det = self._target_instruments()
        except Exception:
            return
        for stem, widgets in getattr(self, "_instr_widgets", {}).items():
            on = stem in det
            for wdg in widgets:
                if isinstance(wdg, ttk.Label):
                    wdg.configure(foreground=(FG if on else BORDER))
                else:
                    self._set_enabled(wdg, on)
        if hasattr(self, "instr_detected"):
            live = ", ".join(s for s in INSTRUMENTS if s in det)
            self.instr_detected.set("Active stems in session: " + (live or "-"))

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
        self._draw_stage()

    def _overrides_dict(self):
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

    # ------------------------------------------------------------ command generation
    def _cmd_for(self, job):
        py = sys.executable
        path, kind = job["path"], job["kind"]
        out = self.output.get()
        all_obj = (out == "Dolby Atmos (20ch Objects)")
        atmos = (out == "Dolby Atmos")
        admr = (out == "ADM BWF")
        fmt = "7.1.2" if (atmos or admr or all_obj) else out

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
            cmd = [py, os.path.join(HERE, "upmix.py"), path,
                   "--split-vocals", self.split.get()] + common

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
        if all_obj:
            cmd += ["--adm", "--all-objects", "--adm-objects"]
        elif atmos:
            cmd += ["--adm"]
        elif admr:
            cmd += ["--adm", "--adm-order", "renderer"]
        if (atmos or admr) and hasattr(self, "adm_objects") and self.adm_objects.get() == "on":
            cmd += ["--adm-objects"]

        return cmd

    # ------------------------------------------------------------ worker & execution
    def _start(self):
        if self.running:
            return
        self._save_cfg()
        self._write_overrides()

        pending = [iid for iid in self.tree.get_children()
                   if self.tree.set(iid, "status").endswith("queued")
                   or self.tree.set(iid, "status").endswith("failed")]
        if not pending:
            self._append("! Queue is empty (add songs or stems first)\n")
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

            base = os.path.basename(job["path"].rstrip(os.sep))
            self.q.put(("status", iid, "running"))
            self.q.put(("stage", f"[{i}/{total}] {base}"))

            cmd = self._cmd_for(job)
            self.q.put(("log", f"\n=== [{i}/{total}] {base} ===\n$ {' '.join(cmd)}\n\n"))

            code = self._run_one(cmd, base)
            if self.stop_flag and code != 0:
                self.q.put(("status", iid, "queued"))
                break
            if code == 0:
                self.q.put(("status", iid, "done"))
                done += 1
            else:
                self.q.put(("status", iid, "failed"))

        self.q.put(("done_all", done, total))

    def _run_one(self, cmd, base):
        try:
            self.q.put(("progress", 0))
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=HERE)

            current_step = "Processing"
            for line in self.proc.stdout:
                self.q.put(("log", line))

                # Multi-stage parsing
                if "separating" in line.lower() or "demucs" in line.lower():
                    current_step = "AI Separation"
                elif "karaoke" in line.lower() or "vocal" in line.lower():
                    current_step = "Vocal Splitting"
                elif "motion" in line.lower() or "adm" in line.lower() or "upmix" in line.lower():
                    current_step = "3D Mastering"

                self.q.put(("stage", f"{base} · {current_step}"))

                m = re.search(r"(\d{1,3})%", line)
                if m:
                    pct = int(m.group(1))
                    if 0 <= pct <= 100:
                        self.q.put(("progress", pct))

            self.proc.wait()
            self.q.put(("progress", 100 if self.proc.returncode == 0 else 0))
            return self.proc.returncode
        except Exception as e:
            self.q.put(("log", f"\n! failed to launch: {e}\n"))
            self.q.put(("progress", 0))
            return -1
        finally:
            self.proc = None

    def _stop(self):
        self.stop_flag = True
        if self.proc is not None:
            try:
                self.proc.terminate()
                self._append("\n! Stopping queue after current task…\n")
            except Exception:
                pass

    # ------------------------------------------------------------ ui drain loop
    _STATUS_TEXT = {"queued": "⏳ queued", "running": "▶ running",
                    "done": "✓ done", "failed": "✗ failed"}

    def _drain(self):
        try:
            while True:
                item = self.q.get_nowait()
                tag = item[0]
                if tag == "log":
                    self._append(item[1])
                elif tag == "stage":
                    self.stage_lbl.set(item[1])
                elif tag == "progress":
                    _, val = item
                    if hasattr(self, "prog"):
                        self.prog["value"] = val
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
                    if hasattr(self, "prog"):
                        self.prog["value"] = 0
                    self.stage_lbl.set("")
                    self.status.set(f"Finished: {done}/{total} done")
                    self._append(f"\n✓ Queue finished: {done}/{total} succeeded\n")
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
