"""TimeBooks per-user installer / uninstaller.

Built by build.py into a single Setup.exe that carries the app as an embedded zip (payload.zip).
Always installs to its own folder, %LOCALAPPDATA%\\Programs\\TimeBooks (no admin rights needed), no matter
where Setup.exe is run from. Optional Start Menu and Desktop shortcuts. Registers an entry in Settings > Apps so it
can be uninstalled.
Setup asks for the company name and creates this year's workbook in Documents\\TimeBooks, which the app then opens
first. Workbooks live wherever the user saves them and are never touched by updates or uninstall.

Portable mode instead copies the program to "<drive>\\TimeBooks Portable" (e.g. a USB drive) with a
portable.txt marker. That copy keeps its workbooks, PIN and settings in its own Data folder and writes
nothing to the computer: no shortcuts, no registry entry, no uninstaller (delete the folder to remove it;
Data holds the workbooks).

Settings and the PIN from the TimeCard Pro days (%APPDATA%\\TimeCardPro) are copied to the TimeBooks
folder by timecard.paths the first time it's needed. Uninstall TimeCard Pro itself from Settings > Apps.

  Setup.exe                 interactive install
  Setup.exe --silent [--pin 1234] [--company "Acme LLC"] [--no-desktop] [--no-start-menu]
                     [--no-timecards] [--no-payroll]
                            install without UI (--no-timecards / --no-payroll turn those features off in the new
                            workbook; without --pin the app asks for a PIN on first use; --company
                            creates this year's workbook in Documents\\TimeBooks unless one is already set up)
  Setup.exe --silent --portable E:\\   make a portable copy in E:\\TimeBooks Portable
  Setup.exe --update <TimeBooks.exe> [--wait-pid N]
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
import json
import tempfile
import threading
import time
import winreg
import zipfile
from datetime import date
from pathlib import Path

from timecard import __version__, paths, security

APP_NAME = "TimeBooks"
APP_EXE = "TimeBooks.exe"
VERSION = __version__
PUBLISHER = "TimeBooks"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\TimeBooks"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "TimeBooks"
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
        bat = Path(tempfile.gettempdir()) / "timebooks_cleanup.cmd"
        bat.write_text(f'@echo off\r\nping 127.0.0.1 -n 3 >nul\r\nrmdir /s /q "{INSTALL_DIR}"\r\ndel "%~f0"\r\n')
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=NO_WINDOW | 0x00000008)  # DETACHED_PROCESS


def _settings_file(settings_dir: Path) -> Path:
    return settings_dir / "settings.json"


def existing_workbook(settings_dir: Path, app_base: Path) -> Path | None:
    """The workbook the app already opens first on this computer/drive, if that file still exists."""
    try:
        data = json.loads(_settings_file(settings_dir).read_text(encoding="utf-8"))
        p = Path(paths.resolve_path(str(data.get("last_workbook") or ""), app_base))
    except (OSError, ValueError, AttributeError):
        return None
    return p if p.suffix and p.is_file() else None


def planned_workbook(company_name: str, app_base: Path, portable: bool) -> Path:
    """Where Setup will create the workbook: Documents\\TimeBooks (or the portable drive's Data\\Workbooks)."""
    return (paths.default_workbook_dir(app_base, portable)
            / paths.workbook_filename(company_name, date.today().year))


def remember_workbook(settings_dir: Path, workbook: Path, app_base: Path) -> None:
    """Make `workbook` the one the app opens first (settings.json last_workbook + top of Recent Files)."""
    f = _settings_file(settings_dir)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    stored = paths.store_path(workbook, app_base)
    recent = [r for r in data.get("recent") or [] if isinstance(r, str) and r.lower() != stored.lower()]
    data["last_workbook"] = stored
    data["recent"] = [stored] + recent[:7]
    settings_dir.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def create_workbook(company_name: str, settings_dir: Path, app_base: Path, use_timecards: bool = True,
                    use_payroll: bool = True) -> Path:
    """Create this year's workbook for `company_name` in the default folder, which is created too (an existing
    file with that name is kept, never overwritten), and make it the workbook the app opens first. `use_timecards`
    / `use_payroll` are the answers to "What does your business need?" (Company Settings > Features).
    Call after installing, so a portable copy's marker is in place."""
    from timecard.models import Company
    from timecard.storage import TimecardBook

    wb = planned_workbook(company_name, app_base, paths.is_portable(app_base))
    wb.parent.mkdir(parents=True, exist_ok=True)
    if not wb.exists():
        TimecardBook.new(Company(company_name=company_name.strip(), year=date.today().year,
                                 use_timecards=use_timecards, use_payroll=use_payroll)).save(wb)
    remember_workbook(settings_dir, wb, app_base)
    return wb


def use_settings_dir(folder: Path) -> None:
    """Point the PIN/settings store at `folder` (the computer's AppData, or a portable copy's Data\\Settings)."""
    os.environ[paths.SETTINGS_ENV] = str(folder)


def installed_settings_dir() -> Path:
    """%APPDATA%\\TimeBooks (a TimeCard Pro settings folder, PIN included, is copied there first)."""
    return paths.installed_settings_dir()


def app_env() -> dict:
    """The environment to start the app with: without the settings override Setup uses for itself."""
    return {k: v for k, v in os.environ.items() if k != paths.SETTINGS_ENV}


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
        probe = anchor / ".timebooks-write-test"
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
        "This copy of TimeBooks is portable: workbooks, PIN and settings are kept in the Data folder on this "
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
        subprocess.Popen([str(exe)], cwd=str(exe.parent), env=app_env())
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
            f"on the drive, so nothing is left on the computer. Run {APP_EXE} from that folder."
            if drives else "No USB drive found. Plug one in, then click Browse or type the drive (e.g. E:\\).")
    ttk.Label(where, text=hint, foreground="#555", justify="left").grid(row=4, column=0, columnspan=3, sticky="w",
                                                                       padx=(22, 0), pady=(4, 0))

    book_box = ttk.LabelFrame(frm, text="Timecard workbook", padding=10)
    book_box.pack(fill="x", pady=(12, 0))
    company_var, book_hint = tk.StringVar(), tk.StringVar()
    want_timecards, want_payroll = tk.BooleanVar(value=True), tk.BooleanVar(value=True)

    pin_box = ttk.LabelFrame(frm, text="PIN (for Company Settings and any tabs you lock)", padding=10)
    pin_box.pack(fill="x", pady=(12, 0))
    pin_var, confirm_var = tk.StringVar(), tk.StringVar()
    question_var = tk.StringVar(value=security.SECURITY_QUESTIONS[0])
    answer_var, answer_confirm_var = tk.StringVar(), tk.StringVar()
    state: dict = {"keep_pin": False, "keep_recovery": False, "keep_book": None}

    def app_base() -> Path | None:
        """Folder the program will be installed in."""
        if kind.get() == "computer":
            return INSTALL_DIR
        return portable_target(drive_var.get().strip()) if drive_var.get().strip() else None

    def target_settings_dir() -> Path | None:
        if kind.get() == "computer":
            return installed_settings_dir()
        if not drive_var.get().strip():
            return None
        return paths.portable_data_dir(portable_target(drive_var.get().strip())) / "Settings"

    def update_book_hint(*_args) -> None:
        base = app_base()
        if base is None:
            book_hint.set("Choose the drive above first.")
            return
        name = company_var.get().strip()
        portable = kind.get() == "portable"
        wb = (planned_workbook(name, base, portable) if name else
              paths.default_workbook_dir(base, portable) / f"<Company> Timecards {date.today().year}.xlsx")
        book_hint.set(f"This year's workbook is created and saved as:\n{wb}")

    def build_book_box() -> None:
        """Ask for the company name, unless the app already has a workbook there (it is kept)."""
        for w in book_box.winfo_children():
            w.destroy()
        base, folder = app_base(), target_settings_dir()
        state["keep_book"] = existing_workbook(folder, base) if base and folder else None
        if state["keep_book"]:
            ttk.Label(book_box, text=f"Your workbook is kept and opens when the app starts:\n{state['keep_book']}",
                      justify="left").grid(row=0, column=0, sticky="w")
            return
        ttk.Label(book_box, text="Company name:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(book_box, textvariable=company_var, width=36).grid(row=0, column=1, sticky="w")
        ttk.Label(book_box, textvariable=book_hint, foreground="#555", justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(book_box, text="What does your business need? (You can change this later in Company Settings.)"
                  ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 2))
        ttk.Checkbutton(book_box, text="Time cards: track the hours people work", variable=want_timecards).grid(
            row=3, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(book_box, text="Payroll checks: pay people and print checks", variable=want_payroll).grid(
            row=4, column=0, columnspan=2, sticky="w")
        book_box.columnconfigure(1, weight=1)   # the long path line widens column 1, not the label column
        update_book_hint()

    company_var.trace_add("write", update_book_hint)

    def build_pin_box(*_args) -> None:
        """Show PIN fields for where the PIN will live; an existing PIN there is kept."""
        build_book_box()
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
        keep_pin, keep_recovery, keep_book = state["keep_pin"], state["keep_recovery"], state["keep_book"]
        problem = None
        if not keep_book and not company_var.get().strip():
            problem = "Enter the company name for the timecard workbook."
        if not problem and not keep_pin:
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
        try:
            book = keep_book or create_workbook(company_var.get(), target_settings_dir(), exe.parent,
                                                use_timecards=want_timecards.get(), use_payroll=want_payroll.get())
            book_note = f"\n\nYour timecard workbook:\n{book}"
        except Exception as exc:  # noqa: BLE001 - the program itself is installed; say what to do instead
            book_note = (f"\n\nThe workbook could not be created ({exc}).\n"
                         "Use New Year Workbook in the app to create it.")
        root.config(cursor="")
        if portable:
            messagebox.showinfo(f"{APP_NAME} Setup", f"The portable copy is ready in:\n{exe.parent}\n\n"
                                f"On any computer, open that folder and run {APP_EXE}.\n"
                                "Save workbooks in its Data\\Workbooks folder so they stay on the drive."
                                + book_note)
        else:
            places = [n for n, on in (("the Start Menu", want_start_menu.get()), ("the Desktop", want_desktop.get()))
                      if on]
            messagebox.showinfo(f"{APP_NAME} Setup", f"{APP_NAME} is installed in:\n{INSTALL_DIR}\n\n"
                                + (f"Shortcuts were added to {' and '.join(places)}." if places else
                                   f"No shortcuts were added. Run {APP_EXE} from the folder above.")
                                + book_note)
        if launch.get():
            subprocess.Popen([str(exe)], cwd=str(exe.parent), env=app_env())
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
            settings = paths.portable_data_dir(target) / "Settings"
            use_settings_dir(settings)
            exe = install_portable(target)
        else:
            settings = installed_settings_dir()
            use_settings_dir(settings)
            exe = install("--no-desktop" not in args, "--no-start-menu" not in args)
            if "--pin" in sys.argv[:-1]:
                security.set_pin(sys.argv[sys.argv.index("--pin") + 1])
        if mode != "uninstall" and _arg("--company") and not existing_workbook(settings, exe.parent):
            create_workbook(_arg("--company"), settings, exe.parent, use_timecards="--no-timecards" not in args,
                            use_payroll="--no-payroll" not in args)
    else:
        gui(mode)


if __name__ == "__main__":
    main()
