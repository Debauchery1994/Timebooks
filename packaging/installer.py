"""TimeCard Pro per-user installer / uninstaller.

Built by build.py into a single Setup.exe that carries the app as an embedded zip (payload.zip).
Always installs to its own folder, %LOCALAPPDATA%\\Programs\\TimeCardPro (no admin rights needed), no matter
where Setup.exe is run from. Optional Start Menu and Desktop shortcuts. Registers an entry in Settings > Apps so it
can be uninstalled.
Workbooks live wherever the user saves them (Documents by default) and are never touched.

Portable mode instead copies the program to "<drive>\\TimeCard Pro Portable" (e.g. a USB drive) with a
portable.txt marker. That copy keeps its workbooks, PIN and settings in its own Data folder and writes
nothing to the computer: no shortcuts, no registry entry, no uninstaller (delete the folder to remove it;
Data holds the workbooks).

  Setup.exe                 interactive install
  Setup.exe --silent [--pin 1234] [--no-desktop] [--no-start-menu]
                            install without UI (without --pin the app asks for a PIN on first use)
  Setup.exe --silent --portable E:\\   make a portable copy in E:\\TimeCard Pro Portable
  Setup.exe --update <TimeCardPro.exe> [--wait-pid N]
                            started by the app's Check for Updates: waits for the app to close, updates that copy
                            (installed or portable, keeping shortcut choices, PIN and settings) and restarts it
  Uninstall.exe --uninstall remove (Uninstall.exe is a copy of Setup.exe placed in the install dir)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import ctypes
import tempfile
import threading
import time
import winreg
import zipfile
from pathlib import Path

from timecard import __version__, paths, security

APP_NAME = "TimeCard Pro"
APP_EXE = "TimeCardPro.exe"
VERSION = __version__
PUBLISHER = "TimeCard Pro"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TimeCardPro"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "TimeCardPro"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"
DESKTOP = Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk"
NO_WINDOW = 0x08000000
DRIVE_REMOVABLE = 2


def resource(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / name


def _ps(script: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, creationflags=NO_WINDOW)


def make_shortcut(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    q = lambda p: str(p).replace("'", "''")
    _ps(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(link)}');"
        f"$s.TargetPath='{q(target)}';$s.WorkingDirectory='{q(target.parent)}';"
        f"$s.IconLocation='{q(target)},0';$s.Description='{APP_NAME}';$s.Save()")


def dir_size_kb(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) // 1024


def install(desktop_shortcut: bool = True, start_menu_shortcut: bool = True) -> Path:
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    INSTALL_DIR.mkdir(parents=True)
    with zipfile.ZipFile(resource("payload.zip")) as z:
        z.extractall(INSTALL_DIR)
    shutil.copy2(sys.executable, INSTALL_DIR / "Uninstall.exe")
    exe = INSTALL_DIR / APP_EXE

    for link, wanted in ((START_MENU, start_menu_shortcut), (DESKTOP, desktop_shortcut)):
        if wanted:
            make_shortcut(link, exe)
        else:
            link.unlink(missing_ok=True)  # an upgrade drops a shortcut the user no longer wants

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        for name, value in {
            "DisplayName": APP_NAME,
            "DisplayVersion": VERSION,
            "Publisher": PUBLISHER,
            "DisplayIcon": f"{exe},0",
            "InstallLocation": str(INSTALL_DIR),
            "UninstallString": f'"{INSTALL_DIR / "Uninstall.exe"}" --uninstall',
            "QuietUninstallString": f'"{INSTALL_DIR / "Uninstall.exe"}" --uninstall --silent',
        }.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, dir_size_kb(INSTALL_DIR))
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
    return exe


def uninstall() -> None:
    for link in (START_MENU, DESKTOP):
        link.unlink(missing_ok=True)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except FileNotFoundError:
        pass
    # Uninstall.exe is running from INSTALL_DIR, so remove the folder after this process exits.
    if INSTALL_DIR.exists():
        bat = Path(tempfile.gettempdir()) / "timecardpro_cleanup.cmd"
        bat.write_text(f'@echo off\r\nping 127.0.0.1 -n 3 >nul\r\nrmdir /s /q "{INSTALL_DIR}"\r\ndel "%~f0"\r\n')
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=NO_WINDOW | 0x00000008)  # DETACHED_PROCESS


def use_settings_dir(folder: Path) -> None:
    """Point the PIN/settings store at `folder` (the computer's AppData, or a portable copy's Data\\Settings)."""
    os.environ["TIMECARDPRO_SETTINGS_DIR"] = str(folder)


def installed_settings_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "TimeCardPro"


def removable_drives() -> list[str]:
    """Root paths of ready removable drives (USB sticks, card readers), e.g. ['E:\\']."""
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    drives = []
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(65 + i)}:\\"
            if k32.GetDriveTypeW(root) == DRIVE_REMOVABLE and os.path.isdir(root):
                drives.append(root)
    return drives


def portable_target(chosen: str | Path) -> Path:
    p = Path(chosen)
    return p if p.name.lower() == paths.PORTABLE_FOLDER.lower() else p / paths.PORTABLE_FOLDER


def portable_problem(target: Path) -> str | None:
    """Why a portable copy can't go in `target`, or None."""
    if str(target.resolve()).lower().startswith(str(INSTALL_DIR.resolve()).lower()):
        return "Choose a USB or external drive, not the installed program folder."
    if target.exists() and any(target.iterdir()) and not paths.is_portable(target):
        return f"{target} already contains other files. Choose another drive or folder."
    anchor = target if target.exists() else target.parent
    if not anchor.exists():
        return f"{anchor} is not available. Is the drive plugged in?"
    try:
        probe = anchor / ".timecardpro-write-test"
        probe.write_text("x")
        probe.unlink()
    except OSError:
        return f"Can't write to {anchor}. Is the drive write-protected?"
    return None


def install_portable(target: Path) -> Path:
    """Create or update a portable copy. Program files are replaced; the Data folder is never touched."""
    problem = portable_problem(target)
    if problem:
        raise ValueError(problem)
    target.mkdir(parents=True, exist_ok=True)
    old_lib = target / "_internal"
    if old_lib.is_dir():
        shutil.rmtree(old_lib)
    (target / APP_EXE).unlink(missing_ok=True)
    with zipfile.ZipFile(resource("payload.zip")) as z:
        z.extractall(target)
    (target / paths.PORTABLE_MARKER).write_text(
        "This copy of TimeCard Pro is portable: workbooks, PIN and settings are kept in the Data folder on this "
        "drive and nothing is saved on the computer.\r\nDo not delete the Data folder - it holds your workbooks.\r\n",
        encoding="utf-8")
    for sub in ("Workbooks", "Settings"):
        (paths.portable_data_dir(target) / sub).mkdir(parents=True, exist_ok=True)
    return target / APP_EXE


def wait_for_exit(pid: int, timeout_s: float = 120) -> bool:
    """Wait for process `pid` to end (True), or give up after timeout_s (False)."""
    synchronize, wait_object_0 = 0x00100000, 0
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(synchronize, False, pid)
    if not handle:
        return True  # already gone
    try:
        return k32.WaitForSingleObject(handle, int(timeout_s * 1000)) == wait_object_0
    finally:
        k32.CloseHandle(handle)


def update(app_exe: Path, retries: int = 10, delay_s: float = 1.0) -> Path:
    """Update the copy `app_exe` belongs to: a portable copy in place, otherwise the installed copy
    (keeping whichever shortcuts exist). Retries while Windows still has the old files locked."""
    folder = Path(app_exe).resolve().parent
    portable = paths.is_portable(folder)
    use_settings_dir(paths.portable_data_dir(folder) / "Settings" if portable else installed_settings_dir())
    for attempt in range(retries):
        try:
            return install_portable(folder) if portable else install(DESKTOP.exists(), START_MENU.exists())
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay_s)
    raise AssertionError("unreachable")


def _arg(name: str) -> str | None:
    """Value following `name` on the command line, or None."""
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv[:-1] else None


def update_gui(app_exe: Path, wait_pid: int | None) -> None:
    """Small 'Updating…' window around update(); restarts the app when done."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} Update")
    root.resizable(False, False)
    try:
        root.iconbitmap(str(resource("timecard.ico")))
    except tk.TclError:
        pass
    frm = ttk.Frame(root, padding=20)
    frm.pack(fill="both", expand=True)
    msg = tk.StringVar(value=f"Waiting for {APP_NAME} to close…")
    ttk.Label(frm, textvariable=msg, width=46).pack(anchor="w")
    bar = ttk.Progressbar(frm, mode="indeterminate", length=320)
    bar.pack(fill="x", pady=(10, 0))
    bar.start(12)
    root.update_idletasks()
    root.geometry(f"+{(root.winfo_screenwidth() - root.winfo_reqwidth()) // 2}"
                  f"+{(root.winfo_screenheight() - root.winfo_reqheight()) // 3}")
    result: dict = {}

    def work() -> None:
        try:
            if wait_pid and not wait_for_exit(wait_pid):
                raise RuntimeError(f"{APP_NAME} did not close. Close it and run the update again.")
            result["stage"] = f"Installing version {VERSION}…"
            result["exe"] = update(app_exe)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            result["error"] = exc

    def poll() -> None:
        if "stage" in result:
            msg.set(result.pop("stage"))
        if worker.is_alive():
            root.after(150, poll)
            return
        bar.stop()
        if "error" in result:
            messagebox.showerror(f"{APP_NAME} Update", f"The update could not be installed:\n{result['error']}\n\n"
                                 "Your workbooks and settings were not changed.", parent=root)
            root.destroy()
            return
        exe = result["exe"]
        env = {k: v for k, v in os.environ.items() if k != "TIMECARDPRO_SETTINGS_DIR"}
        subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
        root.destroy()

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    root.after(150, poll)
    root.mainloop()


def gui(mode: str) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} Setup")
    root.resizable(False, False)
    try:
        root.iconbitmap(str(resource("timecard.ico")))
    except tk.TclError:
        pass
    frm = ttk.Frame(root, padding=20)
    frm.pack(fill="both", expand=True)

    if mode == "uninstall":
        use_settings_dir(installed_settings_dir())
        if messagebox.askyesno(f"{APP_NAME} Setup",
                               f"Remove {APP_NAME} from this computer?\n\n"
                               "Your timecard workbooks (.xlsx) will NOT be deleted."):
            uninstall()
            messagebox.showinfo(f"{APP_NAME} Setup", f"{APP_NAME} was removed.")
        root.destroy()
        return

    ttk.Label(frm, text=f"Install {APP_NAME} {VERSION}", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(frm, text="Employee time cards, yearly time log, payroll and check printing.\n"
                        "Data is stored in GridCraft-compatible Excel workbooks.",
              justify="left").pack(anchor="w", pady=(6, 10))

    kind = tk.StringVar(value="computer")
    drives = removable_drives()
    drive_var = tk.StringVar(value=drives[0] if drives else "")
    where = ttk.LabelFrame(frm, text="Where to install", padding=10)
    where.pack(fill="x")
    ttk.Radiobutton(where, text="On this computer", value="computer", variable=kind).grid(
        row=0, column=0, columnspan=3, sticky="w")
    ttk.Label(where, text=str(INSTALL_DIR), foreground="#555").grid(row=1, column=0, columnspan=3, sticky="w",
                                                                   padx=(22, 0))
    ttk.Radiobutton(where, text="Portable copy on a USB or external drive", value="portable",
                    variable=kind).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
    ttk.Label(where, text="Drive:").grid(row=3, column=0, sticky="w", padx=(22, 6), pady=(4, 0))
    drive_box = ttk.Combobox(where, textvariable=drive_var, values=drives, width=30)
    drive_box.grid(row=3, column=1, sticky="w", pady=(4, 0))
    browse_btn = ttk.Button(where, text="Browse\u2026")
    browse_btn.grid(row=3, column=2, sticky="w", padx=(6, 0), pady=(4, 0))
    hint = (f"Creates a \"{paths.PORTABLE_FOLDER}\" folder there. Workbooks, PIN and settings stay\n"
            "on the drive, so nothing is left on the computer. Run TimeCardPro.exe from that folder."
            if drives else "No USB drive found. Plug one in, then click Browse or type the drive (e.g. E:\\).")
    ttk.Label(where, text=hint, foreground="#555", justify="left").grid(row=4, column=0, columnspan=3, sticky="w",
                                                                       padx=(22, 0), pady=(4, 0))

    pin_box = ttk.LabelFrame(frm, text="Employee records PIN", padding=10)
    pin_box.pack(fill="x", pady=(12, 0))
    pin_var, confirm_var = tk.StringVar(), tk.StringVar()
    question_var = tk.StringVar(value=security.SECURITY_QUESTIONS[0])
    answer_var, answer_confirm_var = tk.StringVar(), tk.StringVar()
    state = {"keep_pin": False, "keep_recovery": False}

    def target_settings_dir() -> Path | None:
        if kind.get() == "computer":
            return installed_settings_dir()
        if not drive_var.get().strip():
            return None
        return paths.portable_data_dir(portable_target(drive_var.get().strip())) / "Settings"

    def build_pin_box(*_args) -> None:
        """Show PIN fields for where the PIN will live; an existing PIN there is kept."""
        for w in pin_box.winfo_children():
            w.destroy()
        folder = target_settings_dir()
        if folder is not None:
            use_settings_dir(folder)
        state["keep_pin"] = folder is not None and security.has_pin()
        state["keep_recovery"] = folder is not None and security.has_recovery()
        row = 0

        def field(label: str, var: tk.StringVar, **entry_opts: object) -> ttk.Entry:
            nonlocal row
            ttk.Label(pin_box, text=label).grid(row=row, column=0, sticky="w", pady=(4, 0), padx=(0, 8))
            entry = ttk.Entry(pin_box, textvariable=var, **entry_opts)
            entry.grid(row=row, column=1, sticky="w", pady=(4, 0))
            row += 1
            return entry

        where_txt = "this computer" if kind.get() == "computer" else "this drive"
        if state["keep_pin"]:
            ttk.Label(pin_box, text=f"A PIN is already set on {where_txt} and will be kept.\n"
                                    "Use Change PIN in the app to change it.").grid(row=row, column=0, columnspan=2,
                                                                                   sticky="w")
            row += 1
        else:
            ttk.Label(pin_box, text=f"Create a {security.MIN_LEN}-{security.MAX_LEN} digit PIN. "
                                    "It is required to open the Employees tab.").grid(row=row, column=0,
                                                                                     columnspan=2, sticky="w")
            row += 1
            field("PIN:", pin_var, show="\u2022", width=14).focus_set()
            field("Confirm PIN:", confirm_var, show="\u2022", width=14)
        if not state["keep_recovery"]:
            ttk.Label(pin_box, text="Security question (used if you forget the PIN):").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
            row += 1
            ttk.Combobox(pin_box, textvariable=question_var, values=security.SECURITY_QUESTIONS,
                         width=44).grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
            row += 1
            ttk.Label(pin_box, text="Pick one or type your own.", foreground="#666").grid(
                row=row, column=0, columnspan=2, sticky="w")
            row += 1
            field("Answer:", answer_var, show="\u2022", width=30)
            field("Confirm answer:", answer_confirm_var, show="\u2022", width=30)

    want_desktop = tk.BooleanVar(value=True)
    want_start_menu = tk.BooleanVar(value=True)
    launch = tk.BooleanVar(value=True)
    shortcut_label = ttk.Label(frm, text="Shortcuts:")
    shortcut_label.pack(anchor="w", pady=(12, 0))
    cb_desktop = ttk.Checkbutton(frm, text="Add a shortcut to the Desktop", variable=want_desktop)
    cb_desktop.pack(anchor="w", padx=(12, 0))
    cb_start = ttk.Checkbutton(frm, text="Add a shortcut to the Start Menu", variable=want_start_menu)
    cb_start.pack(anchor="w", padx=(12, 0))
    ttk.Checkbutton(frm, text=f"Launch {APP_NAME} when finished", variable=launch).pack(anchor="w")

    def on_kind(*_args) -> None:
        portable = kind.get() == "portable"
        for w in (drive_box, browse_btn):
            w.state(["!disabled"] if portable else ["disabled"])
        for w in (cb_desktop, cb_start):
            w.state(["disabled"] if portable else ["!disabled"])
        shortcut_label.configure(text="Shortcuts (not used for a portable copy):" if portable else "Shortcuts:")
        build_pin_box()

    def browse() -> None:
        d = filedialog.askdirectory(parent=root, title="Choose the USB or external drive",
                                    initialdir=drive_var.get() or "C:\\")
        if d:
            drive_var.set(str(Path(d)))
            build_pin_box()

    browse_btn.configure(command=browse)
    drive_box.bind("<<ComboboxSelected>>", build_pin_box)
    drive_box.bind("<FocusOut>", build_pin_box)
    kind.trace_add("write", on_kind)
    on_kind()

    def do_install() -> None:
        portable = kind.get() == "portable"
        target = portable_target(drive_var.get().strip()) if portable and drive_var.get().strip() else None
        if portable:
            if target is None:
                messagebox.showerror(f"{APP_NAME} Setup", "Choose the USB or external drive for the portable copy.")
                return
            problem = portable_problem(target)
            if problem:
                messagebox.showerror(f"{APP_NAME} Setup", problem)
                return
        build_pin_box()  # make sure the PIN check below matches the folder actually chosen
        keep_pin, keep_recovery = state["keep_pin"], state["keep_recovery"]
        problem = None
        if not keep_pin:
            problem = security.validate_pin(pin_var.get())
            if not problem and pin_var.get() != confirm_var.get():
                problem = "The PINs do not match."
        if not problem and not keep_recovery:
            if not question_var.get().strip():
                problem = "Choose or type a security question."
            else:
                problem = security.validate_answer(answer_var.get())
            if not problem and answer_var.get().strip().lower() != answer_confirm_var.get().strip().lower():
                problem = "The security answers do not match."
        if problem:
            messagebox.showerror(f"{APP_NAME} Setup", problem)
            return
        root.config(cursor="watch")
        root.update()
        try:
            exe = install_portable(target) if portable else install(want_desktop.get(), want_start_menu.get())
            if not keep_pin:
                security.set_pin(pin_var.get())
            if not keep_recovery:
                security.set_recovery(question_var.get(), answer_var.get())
        except Exception as exc:  # noqa: BLE001 - show any failure to the user
            root.config(cursor="")
            messagebox.showerror(f"{APP_NAME} Setup", f"Installation failed:\n{exc}")
            return
        if portable:
            messagebox.showinfo(f"{APP_NAME} Setup", f"The portable copy is ready in:\n{exe.parent}\n\n"
                                "On any computer, open that folder and run TimeCardPro.exe.\n"
                                "Save workbooks in its Data\\Workbooks folder so they stay on the drive.")
        else:
            places = [n for n, on in (("the Start Menu", want_start_menu.get()), ("the Desktop", want_desktop.get()))
                      if on]
            messagebox.showinfo(f"{APP_NAME} Setup", f"{APP_NAME} is installed in:\n{INSTALL_DIR}\n\n"
                                + (f"Shortcuts were added to {' and '.join(places)}." if places else
                                   f"No shortcuts were added. Run {APP_EXE} from the folder above."))
        if launch.get():
            env = {k: v for k, v in os.environ.items() if k != "TIMECARDPRO_SETTINGS_DIR"}
            subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
        root.destroy()

    btns = ttk.Frame(frm)
    btns.pack(anchor="e", pady=(16, 0))
    ttk.Button(btns, text="Install", command=do_install).pack(side="left", padx=4)
    ttk.Button(btns, text="Cancel", command=root.destroy).pack(side="left")
    root.mainloop()


def main() -> None:
    args = set(sys.argv[1:])
    mode = "uninstall" if "--uninstall" in args else "install"
    if "--update" in args:
        pid = _arg("--wait-pid")
        update_gui(Path(_arg("--update") or INSTALL_DIR / APP_EXE), int(pid) if pid and pid.isdigit() else None)
    elif "--silent" in args:
        if mode == "uninstall":
            uninstall()
        elif "--portable" in sys.argv[:-1]:
            target = portable_target(sys.argv[sys.argv.index("--portable") + 1])
            use_settings_dir(paths.portable_data_dir(target) / "Settings")
            install_portable(target)
        else:
            use_settings_dir(installed_settings_dir())
            install("--no-desktop" not in args, "--no-start-menu" not in args)
            if "--pin" in sys.argv[:-1]:
                security.set_pin(sys.argv[sys.argv.index("--pin") + 1])
    else:
        gui(mode)


if __name__ == "__main__":
    main()
