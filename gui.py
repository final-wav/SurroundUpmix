#!/usr/bin/env python3
"""SurroundUpmix - dark-mode GUI.

A small Tkinter front-end for the v2 engine. It drives allinone.py (a song
file -> Demucs -> surround) or upmix.py (an existing Demucs stems folder ->
surround) in a background thread and streams their output into a live log,
so the window stays responsive while a track renders.

Tkinter ships with Python, so this needs no extra dependency beyond the
engine's own (numpy/scipy/soundfile, and Demucs for the full chain).

    python gui.py
"""
import os
import queue
import subprocess
import sys
import threading

import tkinter as tk
from tkinter import filedialog, ttk

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- dark palette -----------------------------------------------------------
BG = "#1e1f22"       # window background
PANEL = "#2b2d31"    # card / section background
CARD = "#313338"     # input background
FG = "#dbdee1"       # primary text
MUTED = "#949ba4"    # secondary text
ACCENT = "#5865f2"   # primary action
ACCENT_HI = "#4752c4"
BORDER = "#3f4147"
OK = "#3ba55d"
STOP = "#ed4245"
LOG_BG = "#131416"

FORMATS = ["5.1", "7.1", "7.1.2"]
PRESETS = ["focus", "immersive", "concert", "envelop"]
DEVICES = ["auto", "cuda", "cpu"]
SPLIT = ["auto", "on", "off"]
VOCAL = ["auto", "spread", "forward"]


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.q = queue.Queue()
        root.title("SurroundUpmix")
        root.configure(bg=BG)
        root.minsize(720, 640)
        self._style()
        self._build()
        self.root.after(80, self._drain_log)

    # ---------------------------------------------------------------- styling
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
        s.configure("Header.TLabel", background=BG, foreground=FG,
                    font=("Segoe UI", 17, "bold"))
        s.configure("Sub.TLabel", background=BG, foreground=MUTED,
                    font=("Segoe UI", 10))
        s.configure("Card.TLabelframe", background=PANEL, bordercolor=BORDER,
                    relief="solid", borderwidth=1)
        s.configure("Card.TLabelframe.Label", background=PANEL, foreground=MUTED,
                    font=("Segoe UI", 10, "bold"))
        # entries / combos
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
        # radios
        s.configure("TRadiobutton", background=PANEL, foreground=FG)
        s.map("TRadiobutton", background=[("active", PANEL)],
              indicatorcolor=[("selected", ACCENT), ("", CARD)])
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

    # ---------------------------------------------------------------- layout
    def _build(self):
        head = ttk.Frame(self.root, style="Bg.TFrame")
        head.pack(fill="x", padx=18, pady=(16, 6))
        ttk.Label(head, text="SurroundUpmix", style="Header.TLabel").pack(anchor="w")
        ttk.Label(head, text="stereo → 5.1 / 7.1 / 7.1.2  ·  direct stays "
                  "front, ambient wraps", style="Sub.TLabel").pack(anchor="w")

        body = ttk.Frame(self.root, style="Bg.TFrame")
        body.pack(fill="both", expand=True, padx=18, pady=8)

        # -- input card
        inp = ttk.Labelframe(body, text="  INPUT  ", style="Card.TLabelframe",
                             padding=12)
        inp.pack(fill="x", pady=(0, 10))
        self.mode = tk.StringVar(value="song")
        row = ttk.Frame(inp)
        row.pack(fill="x")
        ttk.Radiobutton(row, text="Song file", value="song", variable=self.mode,
                        command=self._on_mode).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(row, text="Stems folder", value="stems",
                        variable=self.mode, command=self._on_mode).pack(side="left")
        prow = ttk.Frame(inp)
        prow.pack(fill="x", pady=(10, 0))
        self.path = tk.StringVar()
        ttk.Entry(prow, textvariable=self.path).pack(side="left", fill="x",
                                                     expand=True)
        ttk.Button(prow, text="Browse…", style="Ghost.TButton",
                   command=self._browse_input).pack(side="left", padx=(8, 0))

        # -- options card
        opt = ttk.Labelframe(body, text="  OPTIONS  ", style="Card.TLabelframe",
                             padding=12)
        opt.pack(fill="x", pady=(0, 10))
        for c in range(4):
            opt.columnconfigure(c, weight=1, uniform="opt")
        self.format, _ = self._combo(opt, "Format", FORMATS, "7.1.2", 0, 0)
        self.preset, _ = self._combo(opt, "Preset", PRESETS, "immersive", 0, 1)
        self.device, self.device_cb = self._combo(opt, "Device", DEVICES, "auto", 0, 2)
        self.split, self.split_cb = self._combo(opt, "Split vocals", SPLIT, "auto", 0, 3)
        self.vocal, _ = self._combo(opt, "Vocal mode", VOCAL, "auto", 1, 0)
        self.rear_gain = self._entry(opt, "Rear gain (dB)", "0", 1, 1)
        self.rear_below = self._entry(opt, "Rear below front", "", 1, 2)
        self.backing = self._entry(opt, "Backing gain", "auto", 1, 3)

        orow = ttk.Frame(opt)
        orow.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Label(orow, text="Output folder", style="Muted.TLabel").pack(anchor="w")
        prow2 = ttk.Frame(opt)
        prow2.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(2, 0))
        self.outdir = tk.StringVar()
        ttk.Entry(prow2, textvariable=self.outdir).pack(side="left", fill="x",
                                                        expand=True)
        ttk.Button(prow2, text="Browse…", style="Ghost.TButton",
                   command=self._browse_out).pack(side="left", padx=(8, 0))

        # -- actions
        act = ttk.Frame(body, style="Bg.TFrame")
        act.pack(fill="x", pady=(0, 8))
        self.start_btn = ttk.Button(act, text="▶  Start", style="Accent.TButton",
                                    command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="■  Stop", style="Stop.TButton",
                                   command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.status = tk.StringVar(value="Ready")
        ttk.Label(act, textvariable=self.status, style="Sub.TLabel").pack(
            side="right")

        # -- log
        logwrap = ttk.Frame(body, style="Bg.TFrame")
        logwrap.pack(fill="both", expand=True)
        self.log = tk.Text(logwrap, bg=LOG_BG, fg="#c8ccd2", insertbackground=FG,
                           relief="flat", height=12, wrap="word", padx=10, pady=8,
                           font=("Cascadia Mono", 10), highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logwrap, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set, state="disabled")

        self._on_mode()

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

    # ---------------------------------------------------------------- actions
    def _on_mode(self):
        song = self.mode.get() == "song"
        # device / split only apply to the full song chain (Demucs)
        state = "readonly" if song else "disabled"
        self.device_cb.configure(state=state)
        self.split_cb.configure(state=state)
        self.status.set("Song → Demucs → surround" if song
                        else "Stems folder → surround")

    def _browse_input(self):
        if self.mode.get() == "song":
            p = filedialog.askopenfilename(
                title="Choose a stereo song",
                filetypes=[("Audio", "*.flac *.wav *.mp3 *.m4a *.aac *.ogg"),
                           ("All files", "*.*")])
        else:
            p = filedialog.askdirectory(title="Choose a Demucs stems folder")
        if p:
            self.path.set(p)

    def _browse_out(self):
        p = filedialog.askdirectory(title="Choose an output folder")
        if p:
            self.outdir.set(p)

    def _build_cmd(self):
        src = self.path.get().strip()
        if not src:
            return None, "Pick an input first."
        py = sys.executable
        if self.mode.get() == "song":
            cmd = [py, os.path.join(HERE, "allinone.py"), src,
                   "--format", self.format.get(), "--preset", self.preset.get(),
                   "--device", self.device.get(),
                   "--split-vocals", self.split.get(),
                   "--vocal-mode", self.vocal.get(),
                   "--backing-gain", self.backing.get().strip() or "auto"]
        else:
            cmd = [py, os.path.join(HERE, "upmix.py"), src,
                   "--format", self.format.get(), "--preset", self.preset.get(),
                   "--vocal-mode", self.vocal.get(),
                   "--backing-gain", self.backing.get().strip() or "auto"]
        rg = self.rear_gain.get().strip()
        if rg:
            cmd += ["--rear-gain", rg]
        rb = self.rear_below.get().strip()
        if rb:
            cmd += ["--rear-below-front", rb]
        od = self.outdir.get().strip()
        if od:
            cmd += ["--out-dir", od]
        return cmd, None

    def _start(self):
        if self.proc is not None:
            return
        cmd, err = self._build_cmd()
        if err:
            self._append("! " + err + "\n")
            return
        self._clear_log()
        self._append("$ " + " ".join(cmd) + "\n\n")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.set("Running…")
        t = threading.Thread(target=self._run, args=(cmd,), daemon=True)
        t.start()

    def _run(self, cmd):
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=HERE)
            for line in self.proc.stdout:
                self.q.put(line)
            self.proc.wait()
            code = self.proc.returncode
        except Exception as e:
            self.q.put("\n! failed to launch: %s\n" % e)
            code = -1
        finally:
            self.proc = None
            self.q.put(("__done__", code))

    def _stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self._append("\n! stopped\n")
            except Exception:
                pass

    # ---------------------------------------------------------------- log pump
    def _drain_log(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__done__":
                    code = item[1]
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    if code == 0:
                        self.status.set("Done ✓")
                        self._append("\n✓ done\n")
                    else:
                        self.status.set("Finished (exit %s)" % code)
                else:
                    self._append(item)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_log)

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
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
