"""Dated backup copies of the workbook, e.g. to a Google Drive for desktop folder.

Backups go to <backup folder>\\TimeBooks Backups\\<workbook name>\\<workbook name> YYYY-MM-DD HHMMSS.xlsx (or the
"TimeCard Pro Backups" folder a backup folder already has, so older copies keep rotating together) and
only the newest `keep` copies are kept. Any synced folder works (Google Drive, OneDrive, Dropbox) as does a
second USB drive.
"""
from __future__ import annotations

import os
import shutil
import string
from datetime import datetime
from pathlib import Path

from . import paths

BACKUP_ROOT_NAME = "TimeBooks Backups"
OLD_BACKUP_ROOT_NAME = "TimeCard Pro Backups"
DEFAULT_KEEP = 30


def google_drive_folders() -> list[Path]:
    """'My Drive' folders from Google Drive for desktop (usually G:\\My Drive), most likely first."""
    candidates = [Path(f"{letter}:\\My Drive") for letter in "G" + string.ascii_uppercase.replace("G", "")
                  if letter not in "AB"]
    home = Path.home()
    candidates += [home / "Google Drive" / "My Drive", home / "My Drive", home / "Google Drive"]
    found: list[Path] = []
    for c in candidates:
        try:
            if c.is_dir() and c not in found:
                found.append(c)
        except OSError:  # disconnected network / card-reader drives
            continue
    return found


def backup_folder(dest_root: str | Path, workbook: str | Path) -> Path:
    return backup_root(dest_root) / Path(workbook).stem


def backup_root(dest_root: str | Path) -> Path:
    """<backup folder>\\TimeBooks Backups, or the TimeCard Pro Backups folder that is already there."""
    return paths.kept_folder(Path(dest_root), BACKUP_ROOT_NAME, OLD_BACKUP_ROOT_NAME)


def list_backups(dest_root: str | Path, workbook: str | Path) -> list[Path]:
    """Backups of `workbook`, newest first."""
    folder = backup_folder(dest_root, workbook)
    stem = Path(workbook).stem
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.glob(f"{stem} ????-??-?? ??????.xlsx") if p.is_file()),
                  key=lambda p: p.name, reverse=True)


def backup_workbook(workbook: str | Path, dest_root: str | Path, keep: int = DEFAULT_KEEP,
                    now: datetime | None = None) -> Path:
    """Copy the saved workbook into the backup folder and prune old copies. Returns the new copy.

    Raises FileNotFoundError if the workbook isn't saved yet and OSError if the backup folder can't be written
    (for example the Google Drive or USB drive isn't connected).
    """
    src = Path(workbook)
    if not src.is_file():
        raise FileNotFoundError(f"The workbook has not been saved yet: {src}")
    if not Path(dest_root).is_dir():
        raise OSError(f"The backup folder is not available: {dest_root}")
    folder = backup_folder(dest_root, src)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H%M%S")
    target = folder / f"{src.stem} {stamp}.xlsx"
    tmp = target.with_suffix(".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, target)
    for old in list_backups(dest_root, src)[max(1, keep):]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def backup_receipts(workbook: str | Path, dest_root: str | Path) -> int:
    """Copy the workbook's receipt files (Receipts folder next to it) to "<workbook name> Receipts" in the backup root,
    only the ones not copied yet (a receipt file never changes once filed). Returns how many were copied."""
    src = Path(workbook).parent / "Receipts"
    if not src.is_dir():
        return 0
    target_root = backup_root(dest_root) / f"{Path(workbook).stem} Receipts"
    copied = 0
    for f in src.rglob("*"):
        target = target_root / f.relative_to(src)
        if f.is_file() and not (target.is_file() and target.stat().st_size == f.stat().st_size):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            copied += 1
    return copied
