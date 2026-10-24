"""Where TimeCard Pro keeps its settings and workbooks.

Installed mode: settings (PIN, recent files, backup choices, log) in %APPDATA%\\TimeCardPro; workbooks in Documents.
Portable mode (a `portable.txt` file next to TimeCardPro.exe, e.g. on a USB drive): everything stays on that drive,
in the `Data` folder beside the program, and nothing is written to the computer. Paths on the program's own drive
are remembered relative to the program folder so the copy still works when the drive letter changes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PORTABLE_MARKER = "portable.txt"
PORTABLE_FOLDER = "TimeCard Pro Portable"
_REL_PREFIX = "portable:"


def app_dir() -> Path:
    """Folder holding TimeCardPro.exe (frozen) or the project root (running from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable(base: Path | None = None) -> bool:
    return ((base or app_dir()) / PORTABLE_MARKER).is_file()


def portable_data_dir(base: Path | None = None) -> Path:
    return (base or app_dir()) / "Data"


def settings_dir() -> Path:
    override = os.environ.get("TIMECARDPRO_SETTINGS_DIR")
    if override:
        return Path(override)
    if is_portable():
        return portable_data_dir() / "Settings"
    return Path(os.environ.get("APPDATA") or Path.home()) / "TimeCardPro"


def documents_dir() -> Path:
    """Default folder for workbooks: the drive's Data\\Workbooks when portable, else Documents."""
    if is_portable():
        d = portable_data_dir() / "Workbooks"
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            pass
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()


def _same_drive(a: Path, b: Path) -> bool:
    return os.path.splitdrive(str(a))[0].lower() == os.path.splitdrive(str(b))[0].lower()


def store_path(path: str | Path) -> str:
    """Path text to save in settings: relative to the program folder when portable and on the same drive."""
    if not path:
        return ""
    p = Path(path)
    if is_portable() and p.is_absolute() and _same_drive(p, app_dir()):
        return _REL_PREFIX + os.path.relpath(p, app_dir())
    return str(p)


def resolve_path(text: str) -> str:
    """Inverse of store_path."""
    if text and text.startswith(_REL_PREFIX):
        return str((app_dir() / text[len(_REL_PREFIX):]).resolve())
    return text or ""
