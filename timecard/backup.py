"""Dated backup copies of the workbook, e.g. to a Google Drive for desktop folder.

Backups go to <backup folder>\\TimeBooks Backups\\<workbook name>\\<workbook name> YYYY-MM-DD HHMMSS.xlsx (or the
"TimeCard Pro Backups" folder a backup folder already has, so older copies keep rotating together) and
only the newest `keep` copies are kept. Any synced folder works (Google Drive, OneDrive, Dropbox) as does a
second USB drive.

Encrypted backups (1.5, for cloud folders): with a backup password, each copy is written as "... .tbk" instead
of ".xlsx" (and receipts as "<name>.tbk"): a header, the random salt, then the file encrypted with Fernet (AES +
HMAC) under a key made from the password with scrypt. Without the password nobody can open them, including us.
Restoring reads either kind back (restore_bytes).
"""
from __future__ import annotations

import base64
import os
import re
import secrets
import shutil
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

BACKUP_ROOT_NAME = "TimeBooks Backups"
OLD_BACKUP_ROOT_NAME = "TimeCard Pro Backups"
DEFAULT_KEEP = 30
ENCRYPTED_SUFFIX = ".tbk"   # an encrypted backup (workbook or receipt)
MAGIC = b"TimeBooks encrypted backup 1\n"
_SALT_LEN = 16


class BackupPasswordError(ValueError):
    """The backup password doesn't open this backup (or the file is damaged)."""


@dataclass(frozen=True)
class BackupKey:
    salt: bytes
    key: bytes    # the Fernet key made from the password and salt


def make_key(password: str, salt: bytes | None = None) -> BackupKey:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    salt = salt or secrets.token_bytes(_SALT_LEN)
    raw = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))
    return BackupKey(salt, base64.urlsafe_b64encode(raw))


def encrypt(data: bytes, key: BackupKey) -> bytes:
    from cryptography.fernet import Fernet
    return MAGIC + key.salt + Fernet(key.key).encrypt(data)


def is_encrypted(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def decrypt(blob: bytes, password: str = "", key: BackupKey | None = None) -> bytes:
    """The original file from an encrypted backup, with the password (or the saved key when its salt matches).
    Raises BackupPasswordError if it doesn't open."""
    from cryptography.fernet import Fernet, InvalidToken
    if not blob.startswith(MAGIC):
        raise BackupPasswordError("This isn't a TimeBooks encrypted backup.")
    salt = blob[len(MAGIC):len(MAGIC) + _SALT_LEN]
    token = blob[len(MAGIC) + _SALT_LEN:]
    k = key if key is not None and key.salt == salt else (make_key(password, salt) if password else None)
    if k is None:
        raise BackupPasswordError("This backup is encrypted: enter the backup password.")
    try:
        return Fernet(k.key).decrypt(token)
    except InvalidToken:
        raise BackupPasswordError("That password doesn't open this backup.") from None


def restore_bytes(path: str | Path, password: str = "", key: BackupKey | None = None) -> bytes:
    """A backup's workbook (.xlsx) contents, decrypting a .tbk. Raises BackupPasswordError / OSError."""
    data = Path(path).read_bytes()
    return decrypt(data, password, key) if data.startswith(MAGIC) else data


def _write_atomic(target: Path, data: bytes) -> None:
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


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
    found = [p for ext in (".xlsx", ENCRYPTED_SUFFIX) for p in folder.glob(f"{stem} ????-??-?? ??????{ext}")
             if p.is_file()]
    return sorted(found, key=lambda p: p.stem, reverse=True)


def backup_workbook(workbook: str | Path, dest_root: str | Path, keep: int = DEFAULT_KEEP,
                    now: datetime | None = None, key: BackupKey | None = None) -> Path:
    """Copy the saved workbook into the backup folder and prune old copies. Returns the new copy. With `key`, the
    copy is encrypted (.tbk).

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
    when = now or datetime.now()
    taken = {p.stem for p in list_backups(dest_root, src)}
    while f"{src.stem} {when:%Y-%m-%d %H%M%S}" in taken:   # two copies in one second: never replace the first
        when += timedelta(seconds=1)
    stamp = when.strftime("%Y-%m-%d %H%M%S")
    if key is not None:
        target = folder / f"{src.stem} {stamp}{ENCRYPTED_SUFFIX}"
        _write_atomic(target, encrypt(src.read_bytes(), key))
    else:
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


def backup_receipts(workbook: str | Path, dest_root: str | Path, key: BackupKey | None = None) -> int:
    """Copy the workbook's receipt files (Receipts folder next to it) to "<workbook name> Receipts" in the backup root,
    only the ones not copied yet (a receipt file never changes once filed). With `key` they're encrypted
    ("<name>.tbk"). Returns how many were copied."""
    src = Path(workbook).parent / "Receipts"
    if not src.is_dir():
        return 0
    target_root = backup_root(dest_root) / f"{Path(workbook).stem} Receipts"
    copied = 0
    for f in src.rglob("*"):
        target = target_root / f.relative_to(src)
        try:
            if not f.is_file():
                continue
            if key is not None:
                target = target.with_name(target.name + ENCRYPTED_SUFFIX)
                if not target.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_atomic(target, encrypt(f.read_bytes(), key))
                    copied += 1
            elif not (target.is_file() and target.stat().st_size == f.stat().st_size):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                copied += 1
        except OSError:   # a locked or unreadable receipt: skip it, the next backup tries again
            continue
    return copied


def restore_receipts(root: str | Path, stem: str, workbook: str | Path, password: str = "",
                     key: BackupKey | None = None) -> tuple[int, int]:
    """Copy the receipts backed up for workbook `stem` (in "<root>\\<stem> Receipts", root being the TimeBooks
    Backups folder) into the Receipts folder next to `workbook`, only the ones missing there (decrypting .tbk ones).
    Returns (copied, not opened)."""
    src = Path(root) / f"{stem} Receipts"
    if not src.is_dir():
        return 0, 0
    target_root = Path(workbook).parent / "Receipts"
    copied = failed = 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        if f.suffix == ENCRYPTED_SUFFIX:
            rel = rel.with_name(rel.name[:-len(ENCRYPTED_SUFFIX)])
        target = target_root / rel
        if target.exists():
            continue
        try:
            data = restore_bytes(f, password, key)
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, data)
            copied += 1
        except (OSError, BackupPasswordError):
            failed += 1
    return copied, failed


def backup_stem(path: str | Path) -> str:
    """The workbook name a backup file was made from ("Shop Timecards 2026 2026-11-01 093000.tbk" -> "Shop Timecards
    2026"), or the file's own name for any other file."""
    m = re.fullmatch(r"(.+) \d{4}-\d{2}-\d{2} \d{6}", Path(path).stem)
    return m.group(1) if m else Path(path).stem


def all_backups(dest_root: str | Path) -> list[Path]:
    """Every workbook backup in the backup folder (all workbooks), newest first."""
    root = backup_root(dest_root)
    if not root.is_dir():
        return []
    found = [p for d in root.iterdir() if d.is_dir() for p in list_backups(dest_root, d.name)]
    return sorted(found, key=lambda p: p.stem[-17:], reverse=True)
