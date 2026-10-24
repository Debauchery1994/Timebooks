"""Check for Updates dialogs."""
from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

from .. import APP_NAME, __version__, updates
from .common import F, apply_tk_colors, log


def _place_over(win: tk.Toplevel, master) -> None:
    win.update_idletasks()
    top = master.winfo_toplevel()
    x = top.winfo_rootx() + max(0, (top.winfo_width() - win.winfo_reqwidth()) // 2)
    y = top.winfo_rooty() + max(0, (top.winfo_height() - win.winfo_reqheight()) // 3)
    win.geometry(f"+{x}+{y}")


class UpdateDialog(tk.Toplevel):
    """'Version X is available' with the release notes. Result: "install", "skip" or None (later)."""

    def __init__(self, master, info: updates.ReleaseInfo):
        super().__init__(master)
        self.withdraw()
        self.title("Update Available")
        self.transient(master.winfo_toplevel())
        self.info, self.result = info, None
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{APP_NAME} {info.version} is available", style="Bold.TLabel").pack(anchor="w")
        ttk.Label(f, text=f"You have version {__version__}. Updating keeps your workbooks, PIN and settings.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        ttk.Label(f, text="What's new:").pack(anchor="w")
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True, pady=(2, 0))
        notes = tk.Text(box, width=64, height=12, font=F("base"), relief="flat", bd=1, wrap="word")
        sb = ttk.Scrollbar(box, orient="vertical", command=notes.yview)
        notes.configure(yscrollcommand=sb.set)
        notes.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        notes.insert("1.0", info.notes or "No release notes were published for this version.")
        notes.configure(state="disabled")
        ttk.Button(f, text="View on GitHub", command=lambda: webbrowser.open(info.page_url)).pack(anchor="w",
                                                                                             pady=(8, 0))
        btns = ttk.Frame(f, padding=(0, 12, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Later", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Skip This Version", command=lambda: self._done("skip")).pack(side="right",
                                                                                          padx=(0, 8))
        install = ttk.Button(btns, text="Download and Install", style="Accent.TButton",
                             command=lambda: self._done("install"))
        install.pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        apply_tk_colors(self)
        _place_over(self, master)
        self.deiconify()
        self.grab_set()
        install.focus_set()

    def _done(self, result: str) -> None:
        self.result = result
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result


class DownloadDialog(tk.Toplevel):
    """Downloads the release's Setup.exe with a progress bar. Result: the downloaded Path, or None."""

    def __init__(self, master, info: updates.ReleaseInfo, dest_dir: Path):
        super().__init__(master)
        self.withdraw()
        self.title("Downloading Update")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.result: Path | None = None
        self._state: dict = {"done": 0, "total": info.setup_size, "cancel": False}
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Downloading {APP_NAME} {info.version}…", style="Bold.TLabel").pack(anchor="w")
        self.v_msg = tk.StringVar(value="Starting…")
        ttk.Label(f, textvariable=self.v_msg, style="Muted.TLabel", width=44).pack(anchor="w", pady=(2, 8))
        self.bar = ttk.Progressbar(f, mode="determinate", length=360, maximum=1000)
        self.bar.pack(fill="x")
        btns = ttk.Frame(f, padding=(0, 12, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", command=self.cancel).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        apply_tk_colors(self)
        _place_over(self, master)
        self.deiconify()
        self.grab_set()
        self._worker = threading.Thread(target=self._work, args=(info, dest_dir), daemon=True)
        self._worker.start()
        self.after(150, self._poll)

    def _progress(self, done: int, total: int) -> None:
        if self._state["cancel"]:
            raise updates.UpdateError("cancelled")
        self._state.update(done=done, total=total)

    def _work(self, info, dest_dir) -> None:
        try:
            self._state["path"] = updates.download(info, dest_dir, self._progress)
        except updates.UpdateError as exc:
            self._state["error"] = exc
        except Exception as exc:  # noqa: BLE001 - reported below
            log.exception("Update download failed")
            self._state["error"] = updates.UpdateError(f"The download failed: {exc}")

    def _poll(self) -> None:
        st = self._state
        mb = lambda n: f"{n / 1_048_576:.1f}"
        if st["total"]:
            self.bar["value"] = min(1000, st["done"] * 1000 // st["total"])
            self.v_msg.set(f"{mb(st['done'])} of {mb(st['total'])} MB")
        else:
            self.v_msg.set(f"{mb(st['done'])} MB")
        if self._worker.is_alive():
            self.after(150, self._poll)
            return
        if "error" in st and not st["cancel"]:
            messagebox.showwarning("Update", str(st["error"]), parent=self)
        self.result = st.get("path") if not st["cancel"] else None
        self.destroy()

    def cancel(self) -> None:
        self._state["cancel"] = True
        self.v_msg.set("Cancelling…")

    def show(self):
        self.wait_window(self)
        return self.result
