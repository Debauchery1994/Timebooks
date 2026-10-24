"""Where TimeBooks keeps its settings and workbooks.

Installed mode: settings (PIN, recent files, backup choices, log) in %APPDATA%\\TimeBooks; workbooks in
Documents\\TimeBooks (created automatically; the real Documents folder, even when it is redirected to OneDrive).
Portable mode (a `portable.txt` file next to TimeBooks.exe, e.g. on a USB drive): everything stays on that drive,
in the `Data` folder beside the program, and nothing is written to the computer. Paths on the program's own drive
are remembered relative to the program folder so the copy still works when the drive letter changes.

The app used to be called TimeCard Pro. Its settings folder (%APPDATA%\\TimeCardPro: PIN, recent files, the last
workbook) is copied to the new one the first time it's needed, and folders the owner already has
(Documents\\TimeCard Pro, "TimeCard Pro Output", "TimeCard Pro Backups") keep being used: nothing is moved.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path

PORTABLE_MARKER = "portable.txt"
PORTABLE_FOLDER = "TimeBooks Portable"
WORKBOOK_FOLDER = "TimeBooks"   # inside Documents
SETTINGS_FOLDER = "TimeBooks"   # inside %APPDATA%
SETTINGS_ENV = "TIMEBOOKS_SETTINGS_DIR"
# Names from when the app was TimeCard Pro, still honoured so nothing the owner already has goes missing.
OLD_WORKBOOK_FOLDER = "TimeCard Pro"
OLD_SETTINGS_FOLDER = "TimeCardPro"
_REL_PREFIX = "portable:"
_migrated = False


def app_dir() -> Path:
    """Folder holding TimeBooks.exe (frozen) or the project root (running from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable(base: Path | None = None) -> bool:
    return ((base or app_dir()) / PORTABLE_MARKER).is_file()


def portable_data_dir(base: Path | None = None) -> Path:
    return (base or app_dir()) / "Data"


def settings_dir() -> Path:
    override = os.environ.get(SETTINGS_ENV)
    if override:
        return Path(override)
    if is_portable():
        return portable_data_dir() / "Settings"
    return installed_settings_dir()


def installed_settings_dir() -> Path:
    """%APPDATA%\\TimeBooks, first copying %APPDATA%\\TimeCardPro into it if this computer still has only that."""
    global _migrated
    appdata = Path(os.environ.get("APPDATA") or Path.home())
    new = appdata / SETTINGS_FOLDER
    if not _migrated:
        _migrated = True
        migrate_settings(appdata / OLD_SETTINGS_FOLDER, new)
    return new


def migrate_settings(old: Path, new: Path) -> bool:
    """Copy the TimeCard Pro settings folder (PIN, recent files, backup choices, log) to the TimeBooks one, once:
    only when `old` exists and `new` has no PIN or settings yet. The old folder is left as it was. True if copied."""
    if not old.is_dir() or (new / "pin.json").exists() or (new / "settings.json").exists():
        return False
    try:
        shutil.copytree(old, new, dirs_exist_ok=True)
    except OSError as exc:
        logging.getLogger("timecard").warning("Could not copy settings from %s to %s: %s", old, new, exc)
        return False
    logging.getLogger("timecard").info("Copied settings from %s to %s (renamed to TimeBooks)", old, new)
    return True


def kept_folder(parent: Path, name: str, old_name: str) -> Path:
    """parent/name, unless only the folder with the old (TimeCard Pro) name exists there: then keep using that one."""
    new, old = parent / name, parent / old_name
    return old if old.is_dir() and not new.exists() else new


def windows_documents() -> Path | None:
    """The user's real Documents folder from Windows (it may be redirected, e.g. to OneDrive), or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")   # FOLDERID_Documents
        guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value) if out.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except (OSError, AttributeError, ValueError):
        return None


def default_workbook_dir(base: Path | None = None, portable: bool | None = None) -> Path:
    """The drive's Data\\Workbooks for a portable copy, else Documents\\TimeBooks, or the Documents\\TimeCard Pro
    folder the owner already has (not created here).
    `base` is the program folder (default: this program's); `portable` overrides detecting it from the marker."""
    if is_portable(base) if portable is None else portable:
        return portable_data_dir(base) / "Workbooks"
    return kept_folder(windows_documents() or Path.home() / "Documents", WORKBOOK_FOLDER, OLD_WORKBOOK_FOLDER)


def documents_dir(base: Path | None = None) -> Path:
    """Default folder for workbooks (see default_workbook_dir), created if missing."""
    d = default_workbook_dir(base)
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return d.parent if d.parent.is_dir() else Path.home()


def workbook_filename(company_name: str, year: int) -> str:
    """'<Company> Timecards <year>.xlsx' with characters Windows can't use in file names replaced."""
    name = re.sub(r'[<>:"/\\|?*]+', "-", f"{company_name.strip()} Timecards {year}").strip(" .")
    return f"{name or 'Timecards'}.xlsx"


def _same_drive(a: Path, b: Path) -> bool:
    return os.path.splitdrive(str(a))[0].lower() == os.path.splitdrive(str(b))[0].lower()


def store_path(path: str | Path, base: Path | None = None) -> str:
    """Path text to save in settings: relative to the program folder (`base`, default this program's) when
    portable and on the same drive."""
    if not path:
        return ""
    p = Path(path)
    base = base or app_dir()
    if is_portable(base) and p.is_absolute() and _same_drive(p, base):
        return _REL_PREFIX + os.path.relpath(p, base)
    return str(p)


def resolve_path(text: str, base: Path | None = None) -> str:
    """Inverse of store_path."""
    if text and text.startswith(_REL_PREFIX):
        return str(((base or app_dir()) / text[len(_REL_PREFIX):]).resolve())
    return text or ""
