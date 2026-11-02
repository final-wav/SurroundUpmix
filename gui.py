#!/usr/bin/env python3
"""
gui.py - Professional Studio GUI for SurroundUpmix v2.0
Features:
  - Interactive 3D Perspective Soundstage (Click-drag Orbit, Pitch, Pan & Zoom)
  - Granular Stem Placement & Acoustic Routing Matrix (Zone, Level, Spread, Ctr/LFE, Mute, Solo)
  - Acoustic Presets & 3D Motion Dynamics
  - Neural Separation Dispatch (Demucs 6-stem, Vocal Split, DrumSep Sub-drums)
  - Binaural Render Mode Matrix (Off, Near, Mid, Far per object)
  - Clean Output Management (Custom Directory or Output/ next to input)
  - Live Console & Multi-Stage Production Monitor
"""

import os
import sys
import time
import math
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Set UTF-8 encoding
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from pipeline import AtmosPipeline, extract_metadata
from binaural_matrix import OBJECT_KEYS, PRESET_MATRICES, get_default_matrix
import config
from surroundupmix.overrides import STEMS as OVERRIDE_STEMS, ZONES as OVERRIDE_ZONES, normalize as normalize_overrides
from surroundupmix.nugen import is_nugen_available

# Optional PIL for cover art rendering
try:
    from PIL import Image, ImageTk
    import io
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

# Optional drag and drop
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAVE_DND = True
except Exception:
    HAVE_DND = False

# Professional Dark Studio Palette
BG = "#0f1013"
PANEL = "#16171b"
CARD = "#1d1e23"
INPUT_BG = "#26282f"
BORDER = "#2d3038"
FG = "#f0f2f5"
MUTED = "#888c96"
ACCENT = "#5865f2"
ACCENT_HOVER = "#6975f5"
GREEN = "#45c970"
RED = "#ed4245"
YELLOW = "#f59e0b"
CONSOLE_BG = "#0b0c0e"

PRESETS = ["immersive", "focus", "concert", "envelop"]
MOTION_MODES = ["dynamic", "subtle", "expressive"]
VOCAL_MODES = ["auto", "spread", "forward"]
SPLIT_OPTIONS = ["auto", "on", "off"]
BITRATES = ["768", "640", "576", "448", "384"]
DRC_MODES = ["music_light", "music_standard", "none"]
BINAURAL_MODES = ["off", "near", "mid", "far"]

STEM_ROUTING_KEYS = ["vocals", "backing", "bass", "drums", "guitar", "piano", "other"]
STEM_DISPLAY_NAMES = {
    "vocals": "Lead Vocal",
    "backing": "Backing Vocals",
    "bass": "Bass",
    "drums": "Drums",
    "guitar": "Guitar",
    "piano": "Piano / Keys",
    "other": "Synth / Other"
}


class Interactive3DSoundstage(tk.Canvas):
    """
    3D Perspective Listening Room & Spatial Audio Soundstage.
    Supports Click-and-Drag Orbit Rotation (Yaw & Pitch), Pan & Scroll-to-Zoom.
    Displays physical 7.1.6 speakers and stem placement positions.
    """
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#111216", highlightthickness=0, **kwargs)
        self.yaw = -222.0
        self.pitch = -26.0
        self.zoom = 1.0
        self.cam_dist = 3.8
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.auto_rotate = False
        self.anim_step = 0.0
        self.stem_zones = {}

        self.last_mouse_x = 0
        self.last_mouse_y = 0
        self.last_right_x = 0
        self.last_right_y = 0

        self.bind("<ButtonPress-1>", self._on_mouse_down)
        self.bind("<B1-Motion>", self._on_mouse_drag)
        self.bind("<ButtonPress-3>", self._on_right_down)
        self.bind("<B3-Motion>", self._on_right_drag)
        self.bind("<ButtonPress-2>", self._on_right_down)
        self.bind("<B2-Motion>", self._on_right_drag)
        self.bind("<MouseWheel>", self._on_mouse_wheel)
        self.bind("<Configure>", lambda e: self.redraw())

        self._animate_loop()

    def update_stem_zones(self, zones_dict):
        self.stem_zones = dict(zones_dict)
        self.redraw()

    def _on_mouse_down(self, event):
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y

    def _on_mouse_drag(self, event):
        dx = event.x - self.last_mouse_x
        dy = event.y - self.last_mouse_y
        self.yaw += dx * 0.65
        self.pitch = max(-88.0, min(88.0, self.pitch - dy * 0.65))
        self.last_mouse_x = event.x
        self.last_mouse_y = event.y
        self.redraw()

    def _on_right_down(self, event):
        self.last_right_x = event.x
        self.last_right_y = event.y

    def _on_right_drag(self, event):
        dx = event.x - self.last_right_x
        dy = event.y - self.last_right_y
        self.pan_x += dx
        self.pan_y += dy
        self.last_right_x = event.x
        self.last_right_y = event.y
        self.redraw()

    def _on_mouse_wheel(self, event):
        if event.delta > 0:
            self.zoom = min(2.5, self.zoom * 1.1)
        else:
            self.zoom = max(0.4, self.zoom * 0.9)
        self.redraw()

    def set_view(self, view_name):
        self.pan_x = 0.0
        self.pan_y = 0.0
        if view_name == "3d":
            self.yaw = -222.0
            self.pitch = -26.0
            self.zoom = 1.0
        elif view_name == "top":
            self.yaw = 0.0
            self.pitch = 88.0
            self.zoom = 1.15
        elif view_name == "front":
            self.yaw = 0.0
            self.pitch = 0.0
            self.zoom = 1.05
        elif view_name == "side":
            self.yaw = 90.0
            self.pitch = 0.0
            self.zoom = 1.05
        self.redraw()

    def toggle_auto_rotate(self):
        self.auto_rotate = not self.auto_rotate
        return self.auto_rotate

    def _animate_loop(self):
        if self.auto_rotate:
            self.yaw = (self.yaw + 0.45) % 360.0
        self.anim_step = (self.anim_step + 0.04) % (2.0 * math.pi)
        self.redraw()
        self.after(40, self._animate_loop)

    def project_3d(self, x, y, z, cx, cy):
        zc = z - 0.35
        yaw_rad = math.radians(self.yaw)
        pitch_rad = math.radians(self.pitch)

        x1 = x * math.cos(yaw_rad) - y * math.sin(yaw_rad)
        y1 = x * math.sin(yaw_rad) + y * math.cos(yaw_rad)
        z1 = zc

        x2 = x1
        y2 = y1 * math.cos(pitch_rad) - z1 * math.sin(pitch_rad)
        z2 = y1 * math.sin(pitch_rad) + z1 * math.cos(pitch_rad)

        dist = self.cam_dist - y2
        if dist < 0.2:
            dist = 0.2
        scale = (210.0 * self.zoom) / dist
        u = cx + x2 * scale
        v = cy - z2 * scale
        return u, v, dist

    def redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        cx = (w / 2.0) + self.pan_x
        cy = (h / 2.0) + self.pan_y + 6.0

        # Floor Grid
        for gx in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            p1 = self.project_3d(gx, -1.0, 0.0, cx, cy)
            p2 = self.project_3d(gx, 1.0, 0.0, cx, cy)
            self.create_line(p1[0], p1[1], p2[0], p2[1], fill="#1c1f26", width=1)

        for gy in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            p1 = self.project_3d(-1.0, gy, 0.0, cx, cy)
            p2 = self.project_3d(1.0, gy, 0.0, cx, cy)
            self.create_line(p1[0], p1[1], p2[0], p2[1], fill="#1c1f26", width=1)

        # Room Bounding Box
        corners = [
            (-1.1, -1.1, 0.0), (1.1, -1.1, 0.0), (1.1, 1.1, 0.0), (-1.1, 1.1, 0.0),
            (-1.1, -1.1, 0.70), (1.1, -1.1, 0.70), (1.1, 1.1, 0.70), (-1.1, 1.1, 0.70),
        ]
        cp = [self.project_3d(x, y, z, cx, cy) for x, y, z in corners]
        self.create_polygon(cp[0][0], cp[0][1], cp[1][0], cp[1][1], cp[2][0], cp[2][1], cp[3][0], cp[3][1], fill="#14161c", outline="#242832", width=1)
        self.create_line(cp[4][0], cp[4][1], cp[5][0], cp[5][1], cp[6][0], cp[6][1], cp[7][0], cp[7][1], cp[4][0], cp[4][1], fill="#1f232c", width=1, dash=(3, 3))
        for i in range(4):
            self.create_line(cp[i][0], cp[i][1], cp[i+4][0], cp[i+4][1], fill="#1b1e25", width=1)

        # Orbit Path
        orbit_pts = []
        for a_deg in range(0, 365, 15):
            rad = math.radians(a_deg)
            ox = 0.85 * math.cos(rad)
            oy = -0.40 + 0.35 * math.sin(rad)
            oz = 0.15 + 0.10 * math.sin(rad * 2.0)
            pu, pv, _ = self.project_3d(ox, oy, oz, cx, cy)
            orbit_pts.extend([pu, pv])
        if len(orbit_pts) >= 4:
            self.create_line(*orbit_pts, fill="#ec4899", width=1, dash=(2, 2))

        # Listener Position
        lu, lv, ld = self.project_3d(0.0, 0.0, 0.0, cx, cy)
        nu, nv, _ = self.project_3d(0.0, 0.18, 0.0, cx, cy)
        self.create_line(lu, lv, nu, nv, fill=ACCENT, width=2)
        self.create_oval(lu - 8, lv - 8, lu + 8, lv + 8, fill="#252936", outline=ACCENT, width=2)
        self.create_text(lu, lv + 14, text="LISTENER", fill="#6b7280", font=("Segoe UI", 7, "bold"))

        # Physical 7.1.6 Anchors
        elements = [
            (-1.0,  1.0, 0.0, "L",   "#5865f2", False),
            ( 1.0,  1.0, 0.0, "R",   "#5865f2", False),
            ( 0.0,  1.0, 0.0, "C",   "#45c970", False),
            ( 0.0,  0.8, 0.0, "LFE", "#f59e0b", False),
            (-1.0,  0.0, 0.0, "Lss", "#3b82f6", False),
            ( 1.0,  0.0, 0.0, "Rss", "#3b82f6", False),
            (-1.0, -1.0, 0.0, "Lrs", "#6366f1", False),
            ( 1.0, -1.0, 0.0, "Rrs", "#6366f1", False),
            (-1.0,  1.0, 0.70, "TFL", "#8b5cf6", True),
            ( 1.0,  1.0, 0.70, "TFR", "#8b5cf6", True),
            (-1.0,  0.0, 0.70, "TML", "#8b5cf6", True),
            ( 1.0,  0.0, 0.70, "TMR", "#8b5cf6", True),
            (-1.0, -1.0, 0.70, "TRL", "#a78bfa", True),
            ( 1.0, -1.0, 0.70, "TRR", "#a78bfa", True),
        ]

        # Dynamic Stem Overlay Nodes based on stem placement settings
        stem_default_coords = {
            "vocals": (0.0, 0.60, 0.10, "#ff6b6b"),
            "backing": (0.85 * math.cos(self.anim_step), -0.40 + 0.35 * math.sin(self.anim_step), 0.15 + 0.10 * math.sin(self.anim_step * 2.0), "#ec4899"),
            "bass": (0.0, 0.40, 0.0, "#f59e0b"),
            "drums": (0.0, 0.75, 0.05, "#38bdf8"),
            "guitar": (-0.65, 0.70, 0.0, "#34d399"),
            "piano": (0.65, 0.70, 0.0, "#a3e635"),
            "other": (0.0, 0.10, 0.35, "#f472b6")
        }

        for stem_key, def_cfg in stem_default_coords.items():
            user_zone = self.stem_zones.get(stem_key, "auto")
            col = def_cfg[3]
            s_name = STEM_DISPLAY_NAMES.get(stem_key, stem_key.capitalize())
            if user_zone == "front":
                x = -0.5 if stem_key in ("guitar", "backing") else (0.5 if stem_key == "piano" else 0.0)
                elements.append((x, 0.85, 0.0, s_name, col, False))
            elif user_zone == "side":
                x = -0.95 if stem_key in ("guitar", "backing") else 0.95
                elements.append((x, 0.0, 0.0, s_name, col, False))
            elif user_zone == "rear":
                x = -0.85 if stem_key in ("guitar", "backing") else 0.85
                elements.append((x, -0.85, 0.0, s_name, col, False))
            elif user_zone in ("heights", "ceiling"):
                elements.append((0.0, 0.20, 0.70, s_name, col, True))
            elif user_zone == "orbit":
                ox = 0.85 * math.cos(self.anim_step)
                oy = -0.40 + 0.35 * math.sin(self.anim_step)
                oz = 0.15 + 0.10 * math.sin(self.anim_step * 2.0)
                elements.append((ox, oy, oz, s_name, col, False))
            else:
                elements.append((def_cfg[0], def_cfg[1], def_cfg[2], s_name, col, (def_cfg[2] > 0.4)))

        # Depth sort and render
        projected = []
        for x, y, z, label, color, is_height in elements:
            u, v, dist = self.project_3d(x, y, z, cx, cy)
            drop_info = None
            if is_height:
                fu, fv, _ = self.project_3d(x, y, 0.0, cx, cy)
                drop_info = (fu, fv)
            projected.append((dist, u, v, label, color, is_height, drop_info))

        projected.sort(key=lambda item: item[0], reverse=True)

        for dist, u, v, label, color, is_height, drop_info in projected:
            if drop_info:
                fu, fv = drop_info
                self.create_line(u, v, fu, fv, fill="#374151", width=1, dash=(2, 2))
                self.create_oval(fu - 3, fv - 2, fu + 3, fv + 2, fill="#232731", outline="")

            r = 7 if not is_height else 6
            self.create_oval(u - r, v - r, u + r, v + r, fill=color, outline="#111216", width=1)
            self.create_text(u, v - 11, text=label, fill="#d1d5db", font=("Segoe UI", 7, "bold"))


class ModernAtmosGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SurroundUpmix Studio v2.0")
        self.root.geometry("1180x840")
        self.root.minsize(1040, 720)
        self.root.configure(bg=BG)

        self.pipeline = AtmosPipeline()
        self.queue_items = []
        self.current_index = -1
        self.is_processing = False
        self.cancel_requested = False
        self.msg_queue = queue.Queue()

        self._init_variables()
        self._setup_styles()
        self._build_ui()
        self._start_message_pump()

    def _init_variables(self):
        # Preset & Engine
        self.var_preset = tk.StringVar(value="immersive")
        self.var_motion = tk.StringVar(value="dynamic")
        self.var_intensity = tk.DoubleVar(value=1.0)
        self.var_vocal_mode = tk.StringVar(value="auto")
        self.var_decorr = tk.BooleanVar(value=True)
        self.var_detail = tk.BooleanVar(value=True)
        self.var_binaural = tk.IntVar(value=0)

        # AI & Separation
        self.var_sep_model = tk.StringVar(value="htdemucs_6s")
        self.var_split = tk.StringVar(value="auto")
        self.var_force_vocal_split = tk.BooleanVar(value=True)
        self.var_split_drums = tk.BooleanVar(value=True)

        # External Tools & Plugins
        cfg_dee = config.get_config_value("dee_path", "")
        self.var_dee_path = tk.StringVar(value=cfg_dee)
        self.var_dee_status = tk.StringVar(value="")

        self.var_vst3_mode = tk.StringVar(value="Standard DSP (Built-in)")
        cfg_vst3 = config.get_config_value("custom_vst3_path", "")
        self.var_vst3_path = tk.StringVar(value=cfg_vst3)
        self.var_export_ec3 = tk.BooleanVar(value=True)
        self.var_export_adm = tk.BooleanVar(value=True)
        self.var_export_712 = tk.BooleanVar(value=False)
        self.var_export_51 = tk.BooleanVar(value=False)

        # Format & Output
        self.var_target_format = tk.StringVar(value="30ch_adm")
        self.var_bitrate = tk.StringVar(value="768")
        self.var_drc = tk.StringVar(value="music_light")
        self.var_device = tk.StringVar(value="auto")
        self.var_custom_output = tk.StringVar(value="")

        # Granular Stem Routing Variables: stem -> dict of vars
        self.stem_vars = {}
        for stem in STEM_ROUTING_KEYS:
            self.stem_vars[stem] = {
                "zone": tk.StringVar(value="auto"),
                "level": tk.StringVar(value="0.0"),
                "spread": tk.StringVar(value="auto"),
                "ctrlfe": tk.StringVar(value=""),
                "mute": tk.BooleanVar(value=False),
                "solo": tk.BooleanVar(value=False)
            }

        # Binaural Render Mode Matrix
        self.binaural_vars = {}
        def_matrix = get_default_matrix("punchy_direct")
        for item in OBJECT_KEYS:
            key = item[0] if isinstance(item, (tuple, list)) else item
            self.binaural_vars[key] = tk.StringVar(value=def_matrix.get(key, "near"))

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")

        s.configure(".", background=PANEL, foreground=FG, fieldbackground=INPUT_BG, bordercolor=BORDER, darkcolor=PANEL, lightcolor=PANEL)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=[12, 6], font=("Segoe UI", 9))
        s.map("TNotebook.Tab",
              background=[("selected", CARD), ("active", INPUT_BG)],
              foreground=[("selected", FG), ("active", FG)])

        s.configure("TCombobox", fieldbackground=INPUT_BG, background=INPUT_BG, foreground=FG, arrowcolor=FG, borderwidth=1, relief="flat")
        s.map("TCombobox", fieldbackground=[("readonly", INPUT_BG)], selectbackground=[("readonly", ACCENT)])

        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=INPUT_BG, bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)

    def _build_ui(self):
        # 1. Header Bar
        hdr = tk.Frame(self.root, bg=PANEL, height=48)
        hdr.pack(fill=tk.X)

        title_box = tk.Frame(hdr, bg=PANEL)
        title_box.pack(side=tk.LEFT, padx=16, pady=10)
        tk.Label(title_box, text="SURROUND UPMIX STUDIO", fg=FG, bg=PANEL, font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        tk.Label(title_box, text="v2.0", fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(8, 0))

        # 2. Splitter Body
        main_body = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=BG, sashwidth=4, bd=0)
        main_body.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left_frame = tk.Frame(main_body, bg=BG)
        main_body.add(left_frame, minsize=580, width=640)

        right_frame = tk.Frame(main_body, bg=BG)
        main_body.add(right_frame, minsize=460, width=540)

        self._build_left_panel(left_frame)
        self._build_right_panel(right_frame)

    def _build_left_panel(self, parent):
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        tab_queue = tk.Frame(notebook, bg=CARD)
        tab_routing = tk.Frame(notebook, bg=CARD)
        tab_spatial = tk.Frame(notebook, bg=CARD)
        tab_separation = tk.Frame(notebook, bg=CARD)
        tab_binaural = tk.Frame(notebook, bg=CARD)
        tab_output = tk.Frame(notebook, bg=CARD)

        notebook.add(tab_queue, text="Queue")
        notebook.add(tab_routing, text="Stem Placement")
        notebook.add(tab_spatial, text="Spatial Engine")
        notebook.add(tab_separation, text="Separation & AI")
        notebook.add(tab_binaural, text="Binaural Matrix")
        notebook.add(tab_output, text="Output & Settings")

        self._build_tab_queue(tab_queue)
        self._build_tab_routing(tab_routing)
        self._build_tab_spatial(tab_spatial)
        self._build_tab_separation(tab_separation)
        self._build_tab_binaural(tab_binaural)
        self._build_tab_output(tab_output)

    # ---------------------------------------------------- TAB 1: QUEUE
    def _build_tab_queue(self, parent):
        btn_bar = tk.Frame(parent, bg=CARD)
        btn_bar.pack(fill=tk.X, padx=12, pady=10)

        tk.Button(btn_bar, text="+ Add Files", bg=ACCENT, fg=FG, activebackground=ACCENT_HOVER, bd=0, padx=12, pady=5, font=("Segoe UI", 9, "bold"), command=self._add_files).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_bar, text="+ Add Folder", bg=INPUT_BG, fg=FG, bd=0, padx=12, pady=5, font=("Segoe UI", 9), command=self._add_directory).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_bar, text="+ Add Multitrack", bg=INPUT_BG, fg=FG, bd=0, padx=12, pady=5, font=("Segoe UI", 9), command=self._add_multitrack).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(btn_bar, text="Clear Queue", bg=INPUT_BG, fg=MUTED, bd=0, padx=10, pady=5, command=self._clear_queue).pack(side=tk.LEFT)

        list_frame = tk.Frame(parent, bg=PANEL, bd=1, relief=tk.FLAT)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        self.queue_listbox = tk.Listbox(list_frame, bg=PANEL, fg=FG, selectbackground=ACCENT, selectforeground=FG, bd=0, highlightthickness=0, font=("Segoe UI", 9))
        self.queue_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.queue_listbox.bind("<<ListboxSelect>>", self._on_select_queue_item)

        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.queue_listbox.yview, bg=PANEL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.queue_listbox.config(yscrollcommand=sb.set)

        # Metadata Card
        meta_card = tk.LabelFrame(parent, text="Track Metadata", font=("Segoe UI", 9, "bold"), fg=FG, bg=CARD, bd=1, relief=tk.SOLID)
        meta_card.pack(fill=tk.X, padx=12, pady=(0, 10))

        meta_body = tk.Frame(meta_card, bg=CARD)
        meta_body.pack(fill=tk.X, padx=10, pady=8)

        fields_frame = tk.Frame(meta_body, bg=CARD)
        fields_frame.pack(fill=tk.X, expand=True)

        f1 = tk.Frame(fields_frame, bg=CARD)
        f1.pack(fill=tk.X, pady=2)
        tk.Label(f1, text="Artist:", fg=MUTED, bg=CARD, width=8, anchor="w").pack(side=tk.LEFT)
        self.ent_artist = tk.Entry(f1, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0)
        self.ent_artist.pack(side=tk.LEFT, fill=tk.X, expand=True)

        f2 = tk.Frame(fields_frame, bg=CARD)
        f2.pack(fill=tk.X, pady=2)
        tk.Label(f2, text="Title:", fg=MUTED, bg=CARD, width=8, anchor="w").pack(side=tk.LEFT)
        self.ent_title = tk.Entry(f2, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0)
        self.ent_title.pack(side=tk.LEFT, fill=tk.X, expand=True)

        f3 = tk.Frame(fields_frame, bg=CARD)
        f3.pack(fill=tk.X, pady=2)
        tk.Label(f3, text="Album:", fg=MUTED, bg=CARD, width=8, anchor="w").pack(side=tk.LEFT)
        self.ent_album = tk.Entry(f3, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0)
        self.ent_album.pack(side=tk.LEFT, fill=tk.X, expand=True)

        f4 = tk.Frame(fields_frame, bg=CARD)
        f4.pack(fill=tk.X, pady=2)
        tk.Label(f4, text="Track #:", fg=MUTED, bg=CARD, width=8, anchor="w").pack(side=tk.LEFT)
        self.ent_track = tk.Entry(f4, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0, width=5)
        self.ent_track.pack(side=tk.LEFT)
        tk.Label(f4, text="Year:", fg=MUTED, bg=CARD, width=5, anchor="e").pack(side=tk.LEFT, padx=(8, 4))
        self.ent_year = tk.Entry(f4, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0, width=7)
        self.ent_year.pack(side=tk.LEFT)
        tk.Button(f4, text="Save Metadata", bg=INPUT_BG, fg=FG, bd=0, padx=8, pady=2, command=self._save_meta_to_selected).pack(side=tk.RIGHT)

    # ---------------------------------------------------- TAB 2: STEM PLACEMENT (ROUTING MATRIX)
    def _build_tab_routing(self, parent):
        box = tk.Frame(parent, bg=CARD)
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        hdr_frame = tk.Frame(box, bg=CARD)
        hdr_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Label(hdr_frame, text="Acoustic Zone & Stem Routing Matrix", font=("Segoe UI", 10, "bold"), fg=FG, bg=CARD).pack(side=tk.LEFT)
        tk.Button(hdr_frame, text="Reset Defaults", bg=INPUT_BG, fg=MUTED, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=self._reset_stem_routing).pack(side=tk.RIGHT)

        desc = tk.Label(box, text="Assign acoustic placement, gain trim, surround width, and speaker anchors per instrument stem.", fg=MUTED, bg=CARD, font=("Segoe UI", 8))
        desc.pack(anchor="w", pady=(0, 10))

        # Single Grid Table Container ensures pixel-perfect column alignment
        tbl = tk.Frame(box, bg=CARD)
        tbl.pack(fill=tk.X, pady=(0, 8))

        tbl.columnconfigure(0, weight=2)
        tbl.columnconfigure(1, weight=2)
        tbl.columnconfigure(2, weight=1)
        tbl.columnconfigure(3, weight=1)
        tbl.columnconfigure(4, weight=1)
        tbl.columnconfigure(5, weight=1)
        tbl.columnconfigure(6, weight=1)

        # Table Header
        headers = [("Stem", "w"), ("Acoustic Zone", "w"), ("Gain dB", "w"), ("Width", "w"), ("Ctr / LFE", "w"), ("Mute", "center"), ("Solo", "center")]
        for c, (txt, anch) in enumerate(headers):
            h_lbl = tk.Label(tbl, text=txt, fg=MUTED, bg=PANEL, font=("Segoe UI", 8, "bold"), anchor=anch, padx=6, pady=4)
            h_lbl.grid(row=0, column=c, sticky="ew", padx=2, pady=(0, 6))

        zone_choices = ["auto", "front", "side", "rear", "heights", "orbit"]

        # Table Rows
        for r_idx, stem in enumerate(STEM_ROUTING_KEYS, start=1):
            s_label = STEM_DISPLAY_NAMES.get(stem, stem.capitalize())
            tk.Label(tbl, text=s_label, fg=FG, bg=CARD, font=("Segoe UI", 9), anchor="w").grid(row=r_idx, column=0, sticky="w", padx=6, pady=3)

            # Zone Combobox
            cb_zone = ttk.Combobox(tbl, textvariable=self.stem_vars[stem]["zone"], values=zone_choices, state="readonly", width=10)
            cb_zone.grid(row=r_idx, column=1, sticky="ew", padx=3, pady=3)
            cb_zone.bind("<<ComboboxSelected>>", lambda e: self._on_stem_zone_change())

            # Level dB Entry
            ent_lvl = tk.Entry(tbl, textvariable=self.stem_vars[stem]["level"], bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0, width=6)
            ent_lvl.grid(row=r_idx, column=2, sticky="ew", padx=3, pady=3)

            # Spread Entry
            ent_spd = tk.Entry(tbl, textvariable=self.stem_vars[stem]["spread"], bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0, width=6)
            ent_spd.grid(row=r_idx, column=3, sticky="ew", padx=3, pady=3)

            # Center or LFE
            ent_ctrlfe = tk.Entry(tbl, textvariable=self.stem_vars[stem]["ctrlfe"], bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0, width=6)
            ent_ctrlfe.grid(row=r_idx, column=4, sticky="ew", padx=3, pady=3)

            # Mute Checkbox
            cb_mute = tk.Checkbutton(tbl, variable=self.stem_vars[stem]["mute"], bg=CARD, selectcolor=INPUT_BG, activebackground=CARD)
            cb_mute.grid(row=r_idx, column=5, sticky="", padx=3, pady=3)

            # Solo Checkbox
            cb_solo = tk.Checkbutton(tbl, variable=self.stem_vars[stem]["solo"], bg=CARD, selectcolor=INPUT_BG, activebackground=CARD)
            cb_solo.grid(row=r_idx, column=6, sticky="", padx=3, pady=3)

    def _on_stem_zone_change(self):
        zones = {stem: self.stem_vars[stem]["zone"].get() for stem in STEM_ROUTING_KEYS}
        self.canvas_3d.update_stem_zones(zones)

    def _reset_stem_routing(self):
        for stem in STEM_ROUTING_KEYS:
            self.stem_vars[stem]["zone"].set("auto")
            self.stem_vars[stem]["level"].set("0.0")
            self.stem_vars[stem]["spread"].set("auto")
            self.stem_vars[stem]["ctrlfe"].set("")
            self.stem_vars[stem]["mute"].set(False)
            self.stem_vars[stem]["solo"].set(False)
        self._on_stem_zone_change()

    def _compile_overrides(self):
        raw = {}
        for stem in STEM_ROUTING_KEYS:
            sv = self.stem_vars[stem]
            entry = {}
            z = sv["zone"].get().strip().lower()
            if z in OVERRIDE_ZONES and z != "auto":
                entry["zone"] = z
            try:
                lv = float(sv["level"].get().strip())
                if lv != 0.0:
                    entry["level"] = lv
            except ValueError:
                pass
            if sv["mute"].get():
                entry["mute"] = True
            sp = sv["spread"].get().strip().lower()
            if sp and sp != "auto":
                try:
                    entry["spread"] = float(sp)
                except ValueError:
                    pass
            cl = sv["ctrlfe"].get().strip()
            if cl:
                try:
                    val = float(cl)
                    if stem == "vocals":
                        entry["center"] = val
                    elif stem in ("bass", "drums"):
                        entry["lfe"] = val
                except ValueError:
                    pass
            if entry:
                raw[stem] = entry
        return normalize_overrides(raw)

    # ---------------------------------------------------- TAB 3: SPATIAL ENGINE
    def _build_tab_spatial(self, parent):
        box = tk.Frame(parent, bg=CARD)
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # Acoustic Preset
        p_row = tk.Frame(box, bg=CARD)
        p_row.pack(fill=tk.X, pady=6)
        tk.Label(p_row, text="Acoustic Preset:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        cb_preset = ttk.Combobox(p_row, textvariable=self.var_preset, values=PRESETS, state="readonly", width=18)
        cb_preset.pack(side=tk.LEFT)

        # 3D Motion Mode
        m_row = tk.Frame(box, bg=CARD)
        m_row.pack(fill=tk.X, pady=6)
        tk.Label(m_row, text="3D Motion Mode:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        cb_motion = ttk.Combobox(m_row, textvariable=self.var_motion, values=MOTION_MODES, state="readonly", width=18)
        cb_motion.pack(side=tk.LEFT)

        # Motion Intensity
        i_row = tk.Frame(box, bg=CARD)
        i_row.pack(fill=tk.X, pady=6)
        tk.Label(i_row, text="Motion Intensity:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        scale_intensity = tk.Scale(i_row, variable=self.var_intensity, from_=0.2, to=2.0, resolution=0.1, orient=tk.HORIZONTAL, bg=CARD, fg=FG, highlightthickness=0, bd=0, length=200)
        scale_intensity.pack(side=tk.LEFT)

        tk.Frame(box, height=1, bg=BORDER).pack(fill=tk.X, pady=12)

        # Decorrelation
        cb_decorr = tk.Checkbutton(box, text="Phase-Safe Surround & Height Decorrelation (enveloping room acoustics)", variable=self.var_decorr, fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG)
        cb_decorr.pack(anchor="w", pady=3)

        # Detail Recovery
        cb_detail = tk.Checkbutton(box, text="Detail Recovery (Reinjects high-frequency air & micro-transients)", variable=self.var_detail, fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG)
        cb_detail.pack(anchor="w", pady=3)

        # Binaural Depth
        b_row = tk.Frame(box, bg=CARD)
        b_row.pack(fill=tk.X, pady=8)
        tk.Label(b_row, text="Binaural Cue Depth:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        scale_bin = tk.Scale(b_row, variable=self.var_binaural, from_=0, to=100, resolution=5, orient=tk.HORIZONTAL, bg=CARD, fg=FG, highlightthickness=0, bd=0, length=200)
        scale_bin.pack(side=tk.LEFT)

    # ---------------------------------------------------- TAB 4: SEPARATION & AI
    def _build_tab_separation(self, parent):
        box = tk.Frame(parent, bg=CARD)
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # Stem Model
        s_row = tk.Frame(box, bg=CARD)
        s_row.pack(fill=tk.X, pady=6)
        tk.Label(s_row, text="Demucs Stem Model:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        cb_smodel = ttk.Combobox(s_row, textvariable=self.var_sep_model, values=["htdemucs_6s", "htdemucs_ft"], state="readonly", width=16)
        cb_smodel.pack(side=tk.LEFT)
        tk.Label(s_row, text="htdemucs_6s = 6 stems (drums, bass, guitar, piano, vocals, other)", fg=MUTED, bg=CARD, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=10)

        # Vocal Split
        v_row = tk.Frame(box, bg=CARD)
        v_row.pack(fill=tk.X, pady=6)
        tk.Label(v_row, text="Vocal Separation:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        cb_split = ttk.Combobox(v_row, textvariable=self.var_split, values=SPLIT_OPTIONS, state="readonly", width=16)
        cb_split.pack(side=tk.LEFT)
        tk.Label(v_row, text="Separates lead vocals from backing harmonies", fg=MUTED, bg=CARD, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=10)

        # Multitrack Vocal Split
        mv_row = tk.Frame(box, bg=CARD)
        mv_row.pack(fill=tk.X, pady=4)
        tk.Label(mv_row, text="Multitrack Vocals:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        cb_force = tk.Checkbutton(mv_row, text="Split single multitrack vocal stems into Lead & Backing", variable=self.var_force_vocal_split, fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG)
        cb_force.pack(side=tk.LEFT)

        # Sub-Drum Separation
        d_row = tk.Frame(box, bg=CARD)
        d_row.pack(fill=tk.X, pady=6)
        tk.Label(d_row, text="Sub-Drum Separation:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold"), width=18, anchor="w").pack(side=tk.LEFT)
        cb_drums = tk.Checkbutton(d_row, text="DrumSep (Separate Kick, Snare, Cymbals & Toms)", variable=self.var_split_drums, fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG)
        cb_drums.pack(side=tk.LEFT)

    # ---------------------------------------------------- TAB 5: BINAURAL MATRIX
    def _build_tab_binaural(self, parent):
        top_bar = tk.Frame(parent, bg=CARD)
        top_bar.pack(fill=tk.X, padx=12, pady=10)

        tk.Label(top_bar, text="Binaural Matrix Preset:", fg=FG, bg=CARD, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.var_bin_preset = tk.StringVar(value="punchy_direct")
        cb_bpreset = ttk.Combobox(top_bar, textvariable=self.var_bin_preset, values=list(PRESET_MATRICES.keys()), state="readonly", width=18)
        cb_bpreset.pack(side=tk.LEFT, padx=8)
        cb_bpreset.bind("<<ComboboxSelected>>", self._apply_binaural_preset)

        desc = tk.Label(parent, text="Configures Dolby Atmos headphone binaural render mode (Off, Near, Mid, Far) per 3D audio object.", fg=MUTED, bg=CARD, font=("Segoe UI", 8))
        desc.pack(anchor="w", padx=12, pady=(0, 8))

        matrix_frame = tk.Frame(parent, bg=CARD)
        matrix_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        cols = 2
        for i, item in enumerate(OBJECT_KEYS):
            r = i // cols
            c = (i % cols) * 2

            if isinstance(item, (tuple, list)):
                key, lbl_text = item[0], item[1]
            else:
                key = item
                lbl_text = key.replace("_", " ").title()

            tk.Label(matrix_frame, text=lbl_text, fg=FG, bg=CARD, font=("Segoe UI", 9), anchor="w").grid(row=r, column=c, sticky="w", padx=8, pady=4)

            cb = ttk.Combobox(matrix_frame, textvariable=self.binaural_vars[key], values=BINAURAL_MODES, state="readonly", width=8)
            cb.grid(row=r, column=c+1, sticky="w", padx=4, pady=4)

    def _apply_binaural_preset(self, event=None):
        preset_name = self.var_bin_preset.get()
        m = get_default_matrix(preset_name)
        for k, v in m.items():
            if k in self.binaural_vars:
                self.binaural_vars[k].set(v)

    def _get_current_binaural_matrix(self):
        res = {}
        for item in OBJECT_KEYS:
            key = item[0] if isinstance(item, (tuple, list)) else item
            if key in self.binaural_vars:
                res[key] = self.binaural_vars[key].get()
        return res

    # ---------------------------------------------------- TAB 6: OUTPUT & SETTINGS
    def _build_tab_output(self, parent):
        box = tk.Frame(parent, bg=CARD)
        box.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # Output Folder Card
        out_card = tk.LabelFrame(box, text="Destination Output Folder", font=("Segoe UI", 9, "bold"), fg=FG, bg=CARD, bd=1, relief=tk.SOLID)
        out_card.pack(fill=tk.X, pady=(0, 10))

        f_row = tk.Frame(out_card, bg=CARD)
        f_row.pack(fill=tk.X, padx=10, pady=8)

        self.ent_custom_out = tk.Entry(f_row, textvariable=self.var_custom_output, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0)
        self.ent_custom_out.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        tk.Button(f_row, text="Browse...", bg=ACCENT, fg=FG, bd=0, padx=10, pady=3, font=("Segoe UI", 8, "bold"), command=self._browse_output_dir).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(f_row, text="Open Folder", bg=INPUT_BG, fg=FG, bd=0, padx=10, pady=3, font=("Segoe UI", 8), command=self._open_output_dir).pack(side=tk.LEFT)

        out_hint = tk.Label(out_card, text="Leave blank to automatically save into an 'Output' folder next to the source audio file.", fg=MUTED, bg=CARD, font=("Segoe UI", 8))
        out_hint.pack(anchor="w", padx=10, pady=(0, 8))

        # Master Output Formats Card (DAW Checkbox Style - Weg B)
        fmt_card = tk.LabelFrame(box, text="Master Output Formats", font=("Segoe UI", 9, "bold"), fg=FG, bg=CARD, bd=1, relief=tk.SOLID)
        fmt_card.pack(fill=tk.X, pady=(0, 10))

        fmt_body = tk.Frame(fmt_card, bg=CARD)
        fmt_body.pack(fill=tk.X, padx=12, pady=8)

        # 1. E-AC-3 JOC
        self.cb_format_ec3 = tk.Checkbutton(
            fmt_body,
            text="Dolby Atmos E-AC-3 JOC (.m4a Bitstream for AVR, Apple TV & Streaming)",
            variable=self.var_export_ec3,
            fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG,
            font=("Segoe UI", 9)
        )
        self.cb_format_ec3.pack(anchor="w", pady=3)

        # 2. ADM BWF Master
        self.cb_format_adm = tk.Checkbutton(
            fmt_body,
            text="30-Channel Dolby Atmos ADM BWF Master (.wav for Studio One, Pro Tools, Logic Pro)",
            variable=self.var_export_adm,
            fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG,
            font=("Segoe UI", 9)
        )
        self.cb_format_adm.pack(anchor="w", pady=3)

        # 3. 7.1.2 Discrete
        self.cb_format_712 = tk.Checkbutton(
            fmt_body,
            text="7.1.2 Discrete Multichannel (.wav with 2 Height Channels)",
            variable=self.var_export_712,
            fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG,
            font=("Segoe UI", 9)
        )
        self.cb_format_712.pack(anchor="w", pady=3)

        # 4. 5.1 Surround
        self.cb_format_51 = tk.Checkbutton(
            fmt_body,
            text="5.1 Standard Surround Sound (.wav)",
            variable=self.var_export_51,
            fg=FG, bg=CARD, selectcolor=INPUT_BG, activebackground=CARD, activeforeground=FG,
            font=("Segoe UI", 9)
        )
        self.cb_format_51.pack(anchor="w", pady=3)

        # External Tools & Engines (Optional)
        ext_card = tk.LabelFrame(box, text="External Tools & Engines (Optional)", font=("Segoe UI", 9, "bold"), fg=FG, bg=CARD, bd=1, relief=tk.SOLID)
        ext_card.pack(fill=tk.X, pady=(0, 10))

        # 1. Dolby DEE Engine
        dee_frame = tk.Frame(ext_card, bg=CARD)
        dee_frame.pack(fill=tk.X, padx=10, pady=(8, 2))
        tk.Label(dee_frame, text="Dolby DEE Engine:", fg=FG, bg=CARD, font=("Segoe UI", 9), width=16, anchor="w").pack(side=tk.LEFT)
        self.ent_dee = tk.Entry(dee_frame, textvariable=self.var_dee_path, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0)
        self.ent_dee.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(dee_frame, text="Browse dee.exe...", bg=INPUT_BG, fg=FG, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=self._browse_dee_exe).pack(side=tk.LEFT)

        self.lbl_dee_status = tk.Label(ext_card, textvariable=self.var_dee_status, fg=MUTED, bg=CARD, font=("Segoe UI", 8))
        self.lbl_dee_status.pack(anchor="w", padx=10, pady=(0, 2))
        self._update_dee_status()

        dee_hint = tk.Label(
            ext_card,
            text="Note: When dee.exe is provided, direct encoding to Dolby Digital Plus with Atmos (E-AC-3 JOC .m4a / .ec3) is enabled.",
            fg=MUTED, bg=CARD, font=("Segoe UI", 8)
        )
        dee_hint.pack(anchor="w", padx=10, pady=(0, 8))

        # 2. Spatial VST3 Plugin
        vst_frame = tk.Frame(ext_card, bg=CARD)
        vst_frame.pack(fill=tk.X, padx=10, pady=(4, 2))
        tk.Label(vst_frame, text="Spatial VST3 Insert:", fg=FG, bg=CARD, font=("Segoe UI", 9), width=16, anchor="w").pack(side=tk.LEFT)
        self.cb_vst3_mode = ttk.Combobox(vst_frame, textvariable=self.var_vst3_mode, values=["Standard DSP (Built-in)", "Custom VST3 Plugin..."], state="readonly", width=24)
        self.cb_vst3_mode.pack(side=tk.LEFT, padx=(0, 6))
        self.cb_vst3_mode.bind("<<ComboboxSelected>>", self._on_vst3_mode_change)

        self.ent_vst3 = tk.Entry(vst_frame, textvariable=self.var_vst3_path, bg=INPUT_BG, fg=FG, insertbackground=FG, bd=0)
        self.ent_vst3.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_browse_vst3 = tk.Button(vst_frame, text="Browse .vst3...", bg=INPUT_BG, fg=FG, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=self._browse_vst3)
        self.btn_browse_vst3.pack(side=tk.LEFT)

        vst_hint = tk.Label(ext_card, text="Default: Standard DSP. Point to any 64-bit spatial VST3 plugin (Halo Upmix, Penteo, etc.) for texture stems.", fg=MUTED, bg=CARD, font=("Segoe UI", 8))
        vst_hint.pack(anchor="w", padx=10, pady=(0, 8))

        # Hardware & Engine Card
        hw_card = tk.LabelFrame(box, text="Processing Hardware", font=("Segoe UI", 9, "bold"), fg=FG, bg=CARD, bd=1, relief=tk.SOLID)
        hw_card.pack(fill=tk.X, pady=(0, 10))

        hw_row = tk.Frame(hw_card, bg=CARD)
        hw_row.pack(fill=tk.X, padx=10, pady=8)
        tk.Label(hw_row, text="Compute Device:", fg=FG, bg=CARD, font=("Segoe UI", 9), width=16, anchor="w").pack(side=tk.LEFT)
        cb_dev = ttk.Combobox(hw_row, textvariable=self.var_device, values=["auto", "cuda", "cpu"], state="readonly", width=12)
        cb_dev.pack(side=tk.LEFT)
        tk.Label(hw_row, text="Auto selects NVIDIA CUDA GPU when available, with clean CPU fallback.", fg=MUTED, bg=CARD, font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=10)

    def _update_dee_status(self):
        dp = self.var_dee_path.get().strip()
        dee_found = (dp and Path(dp).is_file()) or config.is_dee_available()
        if dp and Path(dp).is_file():
            self.var_dee_status.set("Status: DEE detected -> Direct E-AC-3 JOC (.m4a / .ec3) bitstream export enabled")
        elif config.is_dee_available():
            self.var_dee_status.set("Status: DEE detected (local/system PATH) -> E-AC-3 JOC export enabled")
        else:
            self.var_dee_status.set("Status: Not connected -> Default 30-Channel ADM BWF Master (.wav for all DAWs) active")

        if hasattr(self, "cb_format_ec3"):
            if dee_found:
                self.cb_format_ec3.config(state=tk.NORMAL)
            else:
                self.cb_format_ec3.config(state=tk.DISABLED)
                self.var_export_ec3.set(False)

    def _browse_dee_exe(self):
        f = filedialog.askopenfilename(
            title="Select Dolby Encoding Engine (dee.exe)",
            filetypes=[("Executable Files", "*.exe"), ("All Files", "*.*")]
        )
        if f:
            p = str(Path(f).resolve())
            self.var_dee_path.set(p)
            config.set_config_value("dee_path", p)
            self._update_dee_status()

    def _browse_vst3(self):
        f = filedialog.askopenfilename(
            title="Select 64-bit Spatial VST3 Plugin",
            filetypes=[("VST3 Plugins", "*.vst3"), ("All Files", "*.*")]
        )
        if f:
            p = str(Path(f).resolve())
            self.var_vst3_path.set(p)
            config.set_config_value("custom_vst3_path", p)
            self.var_vst3_mode.set("Custom VST3 Plugin...")

    def _on_vst3_mode_change(self, event=None):
        pass

    def _browse_output_dir(self):
        d = filedialog.askdirectory(title="Select Output Folder")
        if d:
            self.var_custom_output.set(str(Path(d).resolve()))

    def _open_output_dir(self):
        p = self.var_custom_output.get().strip()
        if not p:
            p = str(BASE_DIR / "output")
        Path(p).mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(p)

    # ---------------------------------------------------- RIGHT PANEL (3D STAGE & LOGS)
    def _build_right_panel(self, parent):
        # 3D Soundstage Card
        stage_card = tk.LabelFrame(parent, text="Studio Soundstage Preview (7.1.6 Anchors & Dynamic Stems)", font=("Segoe UI", 9, "bold"), fg=FG, bg=CARD, bd=1, relief=tk.SOLID)
        stage_card.pack(fill=tk.BOTH, expand=False, pady=(0, 6))

        # View Toolbar
        view_bar = tk.Frame(stage_card, bg=CARD)
        view_bar.pack(fill=tk.X, padx=8, pady=(4, 2))

        tk.Button(view_bar, text="3D View", bg=INPUT_BG, fg=FG, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=lambda: self.canvas_3d.set_view("3d")).pack(side=tk.LEFT, padx=2)
        tk.Button(view_bar, text="Top 2D", bg=INPUT_BG, fg=FG, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=lambda: self.canvas_3d.set_view("top")).pack(side=tk.LEFT, padx=2)
        tk.Button(view_bar, text="Front", bg=INPUT_BG, fg=FG, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=lambda: self.canvas_3d.set_view("front")).pack(side=tk.LEFT, padx=2)
        tk.Button(view_bar, text="Side", bg=INPUT_BG, fg=FG, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=lambda: self.canvas_3d.set_view("side")).pack(side=tk.LEFT, padx=2)

        self.btn_autorotate = tk.Button(view_bar, text="Auto-Rotate: OFF", bg=INPUT_BG, fg=MUTED, bd=0, padx=8, pady=2, font=("Segoe UI", 8), command=self._toggle_autorotate)
        self.btn_autorotate.pack(side=tk.RIGHT, padx=2)

        self.canvas_3d = Interactive3DSoundstage(stage_card, height=270)
        self.canvas_3d.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # Action Execution Bar
        act_bar = tk.Frame(parent, bg=PANEL)
        act_bar.pack(fill=tk.X, pady=(0, 6))

        self.btn_start = tk.Button(act_bar, text="Start Production", bg=GREEN, fg="#0b0c0e", activebackground="#3eb363", bd=0, padx=16, pady=8, font=("Segoe UI", 10, "bold"), command=self._start_processing)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_stop = tk.Button(act_bar, text="Stop", bg=INPUT_BG, fg=MUTED, bd=0, padx=12, pady=8, font=("Segoe UI", 9), state=tk.DISABLED, command=self._stop_processing)
        self.btn_stop.pack(side=tk.LEFT)

        # Progress Indicators
        prog_box = tk.Frame(act_bar, bg=PANEL)
        prog_box.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(10, 0))

        status_line = tk.Frame(prog_box, bg=PANEL)
        status_line.pack(fill=tk.X)
        self.lbl_stage = tk.Label(status_line, text="Ready", fg=FG, bg=PANEL, font=("Segoe UI", 8, "bold"))
        self.lbl_stage.pack(side=tk.LEFT)
        self.lbl_percent = tk.Label(status_line, text="0%", fg=MUTED, bg=PANEL, font=("Segoe UI", 8))
        self.lbl_percent.pack(side=tk.RIGHT)

        self.prog_bar = ttk.Progressbar(prog_box, style="Horizontal.TProgressbar", mode="determinate")
        self.prog_bar.pack(fill=tk.X, pady=(2, 0))

        # Console & Log Window
        log_frame = tk.Frame(parent, bg=CONSOLE_BG, bd=1, relief=tk.SOLID)
        log_frame.pack(fill=tk.BOTH, expand=True)

        log_head = tk.Frame(log_frame, bg=CONSOLE_BG)
        log_head.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(log_head, text="LIVE CONSOLE & PRODUCTION MONITOR", fg=MUTED, bg=CONSOLE_BG, font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        tk.Button(log_head, text="Clear", bg=CONSOLE_BG, fg=MUTED, bd=0, font=("Segoe UI", 7), command=self._clear_log).pack(side=tk.RIGHT)

        self.txt_log = tk.Text(log_frame, bg=CONSOLE_BG, fg="#cfd3dc", insertbackground=FG, bd=0, font=("Consolas", 8), wrap=tk.WORD)
        self.txt_log.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

    def _toggle_autorotate(self):
        active = self.canvas_3d.toggle_auto_rotate()
        if active:
            self.btn_autorotate.config(text="Auto-Rotate: ON", fg=GREEN, bg="#1a2e22")
        else:
            self.btn_autorotate.config(text="Auto-Rotate: OFF", fg=MUTED, bg=INPUT_BG)

    def _clear_log(self):
        self.txt_log.delete("1.0", tk.END)

    # ---------------------------------------------------- QUEUE MANAGEMENT
    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="Select Audio Files",
            filetypes=[("Audio Files", "*.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus *.aif *.aiff"), ("All Files", "*.*")]
        )
        for f in files:
            p = Path(f).resolve()
            meta = extract_metadata(p)
            self.queue_items.append({"path": p, "meta": meta, "status": "Pending"})
            self.queue_listbox.insert(tk.END, f"[Pending] {meta['artist']} - {meta['title']} ({p.name})")

    def _add_directory(self):
        d = filedialog.askdirectory(title="Select Folder with Audio Files")
        if d:
            valid_exts = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".aif", ".aiff"}
            for f in sorted(Path(d).glob("*.*")):
                if f.is_file() and f.suffix.lower() in valid_exts:
                    meta = extract_metadata(f)
                    self.queue_items.append({"path": f.resolve(), "meta": meta, "status": "Pending"})
                    self.queue_listbox.insert(tk.END, f"[Pending] {meta['artist']} - {meta['title']} ({f.name})")

    def _add_multitrack(self):
        d = filedialog.askdirectory(title="Select Multitrack Stem Folder")
        if d:
            p = Path(d).resolve()
            meta = extract_metadata(p)
            self.queue_items.append({"path": p, "meta": meta, "status": "Pending"})
            self.queue_listbox.insert(tk.END, f"[Pending] Multitrack: {p.name}")

    def _clear_queue(self):
        if not self.is_processing:
            self.queue_items.clear()
            self.queue_listbox.delete(0, tk.END)

    def _on_select_queue_item(self, event):
        sel = self.queue_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        meta = self.queue_items[idx]["meta"]
        self.ent_artist.delete(0, tk.END)
        self.ent_artist.insert(0, meta.get("artist", ""))
        self.ent_title.delete(0, tk.END)
        self.ent_title.insert(0, meta.get("title", ""))
        self.ent_album.delete(0, tk.END)
        self.ent_album.insert(0, meta.get("album", ""))
        self.ent_track.delete(0, tk.END)
        self.ent_track.insert(0, str(meta.get("track", 1)))
        self.ent_year.delete(0, tk.END)
        self.ent_year.insert(0, str(meta.get("year", "")))

    def _save_meta_to_selected(self):
        sel = self.queue_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self.queue_items[idx]["meta"]["artist"] = self.ent_artist.get().strip() or "Unknown Artist"
        self.queue_items[idx]["meta"]["title"] = self.ent_title.get().strip() or "Unknown Title"
        self.queue_items[idx]["meta"]["album"] = self.ent_album.get().strip() or "Dolby Atmos"
        try:
            self.queue_items[idx]["meta"]["track"] = int(self.ent_track.get().strip())
        except ValueError:
            self.queue_items[idx]["meta"]["track"] = 1
        self.queue_items[idx]["meta"]["year"] = self.ent_year.get().strip()
        status = self.queue_items[idx]["status"]
        p = self.queue_items[idx]["path"]
        m = self.queue_items[idx]["meta"]
        self.queue_listbox.delete(idx)
        self.queue_listbox.insert(idx, f"[{status}] {m['artist']} - {m['title']} ({p.name})")

    # ---------------------------------------------------- PRODUCTION WORKER
    def _start_processing(self):
        if not self.queue_items:
            messagebox.showinfo("Queue Empty", "Please add audio files or a multitrack folder first.")
            return
        if self.is_processing:
            return

        if not (self.var_export_ec3.get() or self.var_export_adm.get() or self.var_export_712.get() or self.var_export_51.get()):
            messagebox.showwarning("No Format Selected", "Please select at least one output deliverable format.")
            return

        self.is_processing = True
        self.cancel_requested = False
        self.btn_start.config(state=tk.DISABLED, bg=BORDER)
        self.btn_stop.config(state=tk.NORMAL)

        custom_out = self.var_custom_output.get().strip()
        custom_out_dir = Path(custom_out) if custom_out else None
        binaural_matrix = self._get_current_binaural_matrix()
        overrides = self._compile_overrides()

        threading.Thread(
            target=self._worker_loop,
            args=(custom_out_dir, binaural_matrix, overrides),
            daemon=True
        ).start()

    def _stop_processing(self):
        if self.is_processing:
            self.cancel_requested = True
            self.btn_stop.config(text="Stopping...", state=tk.DISABLED)

    def _worker_loop(self, custom_out_dir, binaural_matrix, overrides):
        for idx, item in enumerate(self.queue_items):
            if self.cancel_requested:
                break
            if item["status"] == "Done":
                continue

            self.current_index = idx
            item["status"] = "Processing"
            self.msg_queue.put(("queue_status", (idx, "Processing")))

            def prog_cb(stage, pct, msg):
                self.msg_queue.put(("progress", (stage, pct, msg)))

            try:
                force_vocal = self.var_force_vocal_split.get()
                out_p = self.pipeline.run(
                    input_file=item["path"],
                    preset=self.var_preset.get(),
                    motion_mode=self.var_motion.get(),
                    motion_intensity=self.var_intensity.get(),
                    vocal_mode=self.var_vocal_mode.get(),
                    split_vocals=("on" if (item["path"].is_dir() and force_vocal) else self.var_split.get()),
                    split_drums=self.var_split_drums.get(),
                    separation_model=self.var_sep_model.get(),
                    decorrelate=self.var_decorr.get(),
                    recover_detail=self.var_detail.get(),
                    binaural=self.var_binaural.get(),
                    binaural_matrix=binaural_matrix,
                    bitrate=self.var_bitrate.get(),
                    drc=self.var_drc.get(),
                    output_dir=custom_out_dir,
                    custom_metadata=item["meta"],
                    overrides=overrides,
                    dee_path=(self.var_dee_path.get().strip() or None),
                    export_adm=self.var_export_adm.get(),
                    export_ec3=self.var_export_ec3.get(),
                    export_712=self.var_export_712.get(),
                    export_51=self.var_export_51.get(),
                    use_nugen=(self.var_vst3_mode.get() == "Custom VST3 Plugin..." or bool(self.var_vst3_path.get().strip())),
                    progress_cb=prog_cb
                )
                item["status"] = "Done"
                self.msg_queue.put(("queue_status", (idx, "Done")))
                self.msg_queue.put(("log", f"SUCCESS: {item['path'].name} -> {out_p}\n"))
            except Exception as e:
                item["status"] = "Failed"
                self.msg_queue.put(("queue_status", (idx, "Failed")))
                self.msg_queue.put(("log", f"ERROR on {item['path'].name}: {e}\n"))

        self.msg_queue.put(("finished", None))

    def _start_message_pump(self):
        try:
            while not self.msg_queue.empty():
                mtype, data = self.msg_queue.get_nowait()
                if mtype == "progress":
                    stage, pct, msg = data
                    self.lbl_stage.config(text=stage)
                    self.lbl_percent.config(text=f"{pct:.1f}%")
                    self.prog_bar["value"] = pct
                    self.txt_log.insert(tk.END, f"[{stage}] {msg}\n")
                    self.txt_log.see(tk.END)
                elif mtype == "log":
                    self.txt_log.insert(tk.END, data)
                    self.txt_log.see(tk.END)
                elif mtype == "queue_status":
                    idx, status = data
                    meta = self.queue_items[idx]["meta"]
                    fname = self.queue_items[idx]["path"].name
                    self.queue_listbox.delete(idx)
                    self.queue_listbox.insert(idx, f"[{status}] {meta['artist']} - {meta['title']} ({fname})")
                elif mtype == "finished":
                    self.is_processing = False
                    self.btn_start.config(state=tk.NORMAL, bg=GREEN)
                    self.btn_stop.config(state=tk.DISABLED, text="Stop")
                    self.lbl_stage.config(text="Finished")
                    self.lbl_percent.config(text="100%")
                    self.prog_bar["value"] = 100
                    self.txt_log.insert(tk.END, "[Finished] Batch production complete.\n")
                    self.txt_log.see(tk.END)
        except Exception:
            pass
        finally:
            self.root.after(80, self._start_message_pump)


def main():
    if HAVE_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = ModernAtmosGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
