#!/usr/bin/env python3
"""SurroundUpmix - dark-mode GUI with a batch queue and drag & drop.

Drop songs or whole folders onto the window (or use Add files / Add folder),
and they queue up as jobs that render one after another. Each input is
auto-detected: a folder that holds Demucs stems (bass/drums/vocals/other) is
treated as one stems job; any other folder is scanned for audio files and each
becomes a song job; individual audio files become song jobs.

Song jobs run allinone.py (Demucs -> split -> surround); stems jobs run
upmix.py. Everything streams into a live log while the window stays responsive.

Tkinter ships with Python. OS drag & drop additionally needs `tkinterdnd2`
(`pip install tkinterdnd2`); without it the queue still works via the buttons.

    python gui.py
"""
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
WARN = "#e5a44b"
LOG_BG = "#131416"
SEL = "#3a3d44"

FORMATS = ["5.1", "7.1", "7.1.2"]
PRESETS = ["focus", "immersive", "concert", "envelop"]
DEVICES = ["auto", "cuda", "cpu"]
SPLIT = ["auto", "on", "off"]
VOCAL = ["auto", "spread", "forward"]

# ---- GUI --------------------------------------------------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.running = False
        self.stop_flag = False
        self.q = queue.Queue()
        self.jobs = {}          # iid -> {"path","kind"}
        root.title("SurroundUpmix")
        root.configure(bg=BG)
        root.minsize(760, 720)
        self._style()
        self._build()
        self.root.after(80, self._drain)

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

    # ------------------------------------------------------------ layout
    def _build(self):
        head = ttk.Frame(self.root, style="Bg.TFrame")
        head.pack(fill="x", padx=18, pady=(16, 6))
        ttk.Label(head, text="SurroundUpmix", style="Header.TLabel").pack(anchor="w")
        ttk.Label(head, text="stereo → 5.1 / 7.1 / 7.1.2  ·  batch queue  ·  "
                  "direct stays front, ambient wraps",
                  style="Sub.TLabel").pack(anchor="w")

        body = ttk.Frame(self.root, style="Bg.TFrame")
        body.pack(fill="both", expand=True, padx=18, pady=8)

        # -- queue card
        qc = ttk.Labelframe(body, text="  QUEUE  ", style="Card.TLabelframe",
                            padding=12)
        qc.pack(fill="both", expand=True, pady=(0, 10))
        bar = ttk.Frame(qc)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="＋ Add files…", style="Ghost.TButton",
                   command=self._add_files).pack(side="left")
        ttk.Button(bar, text="＋ Add folder…", style="Ghost.TButton",
                   command=self._add_folder).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Remove", style="Ghost.TButton",
                   command=self._remove_sel).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=self._clear_queue).pack(side="left", padx=(8, 0))
        hint = ("drag songs or folders here" if HAVE_DND
                else "tip: pip install tkinterdnd2  for drag & drop")
        ttk.Label(bar, text=hint, style="Muted.TLabel").pack(side="right")

        tw = ttk.Frame(qc)
        tw.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tw, style="Queue.Treeview", show="headings",
                                 columns=("name", "kind", "status"), height=7,
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

        # -- options card
        opt = ttk.Labelframe(body, text="  OPTIONS  (applied to every job)  ",
                             style="Card.TLabelframe", padding=12)
        opt.pack(fill="x", pady=(0, 10))
        for c in range(4):
            opt.columnconfigure(c, weight=1, uniform="opt")
        self.format, _ = self._combo(opt, "Format", FORMATS, "7.1.2", 0, 0)
        self.preset, _ = self._combo(opt, "Preset", PRESETS, "immersive", 0, 1)
        self.device, _ = self._combo(opt, "Device", DEVICES, "auto", 0, 2)
        self.split, _ = self._combo(opt, "Split vocals", SPLIT, "auto", 0, 3)
        self.vocal, _ = self._combo(opt, "Vocal mode", VOCAL, "auto", 1, 0)
        self.rear_gain = self._entry(opt, "Rear gain (dB)", "0", 1, 1)
        self.rear_below = self._entry(opt, "Rear below front", "", 1, 2)
        self.backing = self._entry(opt, "Backing gain", "auto", 1, 3)
        orow = ttk.Frame(opt)
        orow.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Label(orow, text="Output folder  (blank = next to each song)",
                  style="Muted.TLabel").pack(anchor="w")
        prow = ttk.Frame(opt)
        prow.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(2, 0))
        self.outdir = tk.StringVar()
        ttk.Entry(prow, textvariable=self.outdir).pack(side="left", fill="x",
                                                       expand=True)
        ttk.Button(prow, text="Browse…", style="Ghost.TButton",
                   command=self._browse_out).pack(side="left", padx=(8, 0))

        # -- actions
        act = ttk.Frame(body, style="Bg.TFrame")
        act.pack(fill="x", pady=(0, 8))
        self.start_btn = ttk.Button(act, text="▶  Start queue",
                                    style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="■  Stop", style="Stop.TButton",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(act, textvariable=self.status, style="BgMuted.TLabel").pack(
            side="right")

        # -- log
        lw = ttk.Frame(body, style="Bg.TFrame")
        lw.pack(fill="both", expand=True)
        self.log = tk.Text(lw, bg=LOG_BG, fg="#c8ccd2", insertbackground=FG,
                           relief="flat", height=9, wrap="word", padx=10, pady=8,
                           font=("Cascadia Mono", 10), highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER)
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(lw, command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=lsb.set, state="disabled")

    def _combo(self, parent, label, values, default, r, c):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, sticky="ew", padx=4, pady=4)
        ttk.Label(f, text=label, style="Muted.TLabel").pack(anchor="w")
        var = tk.StringVar(value=default)
        cb = ttk.Combobox(f, textvariable=var, values=values, state="readonly")
        cb.pack(fill="x")
        return var, cb

    def _entry(self, parent, label, default, r, c):
        f = ttk.Frame(parent)
        f.grid(row=r, column=c, sticky="ew", padx=4, pady=4)
        ttk.Label(f, text=label, style="Muted.TLabel").pack(anchor="w")
        var = tk.StringVar(value=default)
        ttk.Entry(f, textvariable=var).pack(fill="x")
        return var

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
        common = ["--format", self.format.get(), "--preset", self.preset.get(),
                  "--vocal-mode", self.vocal.get(),
                  "--backing-gain", self.backing.get().strip() or "auto"]
        if kind == "song":
            cmd = [py, os.path.join(HERE, "allinone.py"), path,
                   "--device", self.device.get(),
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
        return cmd

    def _start(self):
        if self.running:
            return
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
