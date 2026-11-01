"""PC and USB copies of a workbook (1.5): tell when the other copy has work this one doesn't.

The owner keeps a workbook on the PC (Documents\\TimeBooks) and on a portable USB copy
(<drive>\\TimeBooks Portable\\Data\\Workbooks), moving it between them by hand. Every save stamps the workbook (Company
sheet) with its own id and the ids of its last saves, newest first. Comparing two copies with the same workbook id:

  same         both end with the same save
  other_newer  this copy's last save is in the other's history: the other was saved since (open that one)
  this_newer   the other copy's last save is in this one's history: this one is ahead
  split        neither: both were changed separately since they were last the same (TimeBooks can't merge them)

Nothing here changes a workbook; the app decides what to offer.
"""
from __future__ import annotations

import os
import socket
import string
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import paths

if TYPE_CHECKING:
    from .models import Company

KEEP_SAVES = 1000   # ~13,000 characters, well under a cell's limit; a long day of automatic saves fits


def stamp_save(company: "Company", now: datetime | None = None) -> None:
    """Record a save on the workbook: a new save id first in its history, and where / when it was saved."""
    if not company.workbook_id:
        company.workbook_id = uuid.uuid4().hex[:12]
    ids = [company.workbook_id[:4] + uuid.uuid4().hex[:8]] + save_history(company.save_ids)
    company.save_ids = ",".join(ids[:KEEP_SAVES])
    company.last_saved_on = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "")[:40]
    company.last_saved_at = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")


def save_history(text: str) -> list[str]:
    return [s for s in (text or "").split(",") if s]


@dataclass
class Stamp:
    workbook_id: str
    saves: list[str]
    saved_on: str
    saved_at: str


@dataclass
class OtherCopy:
    path: Path
    status: str            # other_newer | this_newer | split | same
    saved_on: str
    saved_at: str


def read_stamp(path: str | Path) -> Stamp | None:
    """The save stamp of a workbook file (from its Company sheet), or None if it can't be read or has none."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:   # locked, damaged, not a workbook: nothing to compare
        return None
    try:
        names = {n.lower(): n for n in wb.sheetnames}
        if "company" not in names:
            return None
        values = {}
        for row in wb[names["company"]].iter_rows(min_row=2, max_col=2, values_only=True):
            if row and row[0]:
                values[str(row[0]).strip()] = "" if row[1] is None else str(row[1])
    finally:
        wb.close()
    if not values.get("workbook_id"):
        return None
    return Stamp(values["workbook_id"], save_history(values.get("save_ids", "")), values.get("last_saved_on", ""),
                 values.get("last_saved_at", ""))


def compare(mine: list[str], other: list[str], mine_at: str = "", other_at: str = "") -> str:
    if not mine or not other:
        return "same"
    if mine[0] == other[0]:
        return "same"
    if mine[0] in other:
        return "other_newer"
    if other[0] in mine:
        return "this_newer"
    if (len(mine) >= KEEP_SAVES or len(other) >= KEEP_SAVES) and mine_at and other_at and mine_at != other_at:
        # one copy is so far ahead that the other's last save has dropped off its history: the later save wins
        return "other_newer" if other_at > mine_at else "this_newer"
    return "split"


def _drives() -> list[Path]:
    """Local and removable drives (not network or CD drives, which can be slow or ask for a disc)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        out = []
        for i, letter in enumerate(string.ascii_uppercase):
            if mask >> i & 1:
                root = f"{letter}:\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) in (2, 3):   # removable, fixed
                    out.append(Path(root))
        return out
    except (OSError, AttributeError):
        return []


def candidate_paths(workbook: str | Path, drives: list[Path] | None = None,
                    pc_folder: Path | None = None) -> list[Path]:
    """Where another copy of `workbook` would be: the PC's workbook folder and each drive's portable copy, with the
    same file name. The workbook itself is left out."""
    me = Path(workbook)
    folders = [pc_folder or paths.default_workbook_dir(portable=False)]
    folders += [d / paths.PORTABLE_FOLDER / "Data" / "Workbooks" for d in (_drives() if drives is None else drives)]
    out: list[Path] = []
    for folder in folders:
        p = folder / me.name
        try:
            if p.is_file() and not os.path.samefile(p, me) and p not in out:
                out.append(p)
        except OSError:
            continue
    return out


def other_copies(workbook: str | Path, company: "Company", drives: list[Path] | None = None,
                 pc_folder: Path | None = None) -> list[OtherCopy]:
    """Other copies of this workbook (same workbook id) that aren't the same as it, newest-work-first."""
    mine = save_history(company.save_ids)
    if not company.workbook_id or not mine:
        return []
    found = []
    for p in candidate_paths(workbook, drives, pc_folder):
        s = read_stamp(p)
        if s is None or s.workbook_id != company.workbook_id:
            continue
        status = compare(mine, s.saves, company.last_saved_at, s.saved_at)
        if status != "same":
            found.append(OtherCopy(p, status, s.saved_on, s.saved_at))
    order = {"other_newer": 0, "split": 1, "this_newer": 2}
    return sorted(found, key=lambda c: order[c.status])
