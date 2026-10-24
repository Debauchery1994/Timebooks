"""Getting receipts in: from a scanner (Windows' own WIA scan dialog), a scanner's save-to folder, or a file, and
turning what the receipt reader found (timecard.receipts_ocr) into a Draft expense for a person to approve."""
from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import expenses
from .models import Expense
from .receipts_ocr import ReceiptFields

NO_WINDOW = 0x08000000
# Windows' own scan dialog (works with any scanner that has a Windows driver); saves a PNG, or exits 3 if cancelled.
_WIA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  $dialog = New-Object -ComObject WIA.CommonDialog
  $image = $dialog.ShowAcquireImage(1, 1, 131072, '{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}', $false, $true, $false)
} catch {
  if ($_.Exception.Message -match '0x80210015|no .*device') { exit 4 }
  [Console]::Error.WriteLine($_.Exception.Message); exit 5
}
if ($null -eq $image) { exit 3 }
$image.SaveFile($args[0])
"""


class ScanError(Exception):
    """A scan could not be made; the message is for the owner."""


def acquire_from_scanner(dest_dir: str | Path) -> Path | None:
    """Show Windows' scan dialog and save the scanned page as a PNG in dest_dir. None if the owner cancelled."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"Scan {datetime.now():%Y-%m-%d %H%M%S}.png"
    script = Path(tempfile.gettempdir()) / "timebooks_scan.ps1"
    script.write_text(_WIA_SCRIPT, encoding="utf-8")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", str(script), str(out)], capture_output=True, text=True, timeout=600,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScanError(f"The scanner could not be started ({exc}).") from exc
    if r.returncode == 3:
        return None
    if r.returncode == 4:
        raise ScanError("No scanner was found. Check that it is turned on and connected, or save scans to a folder "
                        "and use Check Scanner Folder, or import the file.")
    if r.returncode != 0 or not out.is_file():
        raise ScanError(f"The scan didn't work: {r.stderr.strip() or 'no picture came back from the scanner'}")
    return out


def scan_key(p: Path) -> str:
    """How a scanner-folder file is remembered as already brought in (name, size and time, so a re-saved file with
    the same name counts as new)."""
    st = p.stat()
    return f"{p.name}|{st.st_size}|{int(st.st_mtime)}"


def new_scans(folder: str | Path, seen: set[str]) -> list[Path]:
    """Receipt files (pictures, PDFs) in the scanner folder that haven't been brought in yet, oldest first."""
    f = Path(folder)
    if not f.is_dir():
        return []
    files = [p for p in f.iterdir() if p.is_file() and p.suffix.lower() in expenses.RECEIPT_TYPES]
    return sorted((p for p in files if scan_key(p) not in seen), key=lambda p: p.stat().st_mtime)


def expense_from_receipt(fields: ReceiptFields, expense_id: str, existing: list[Expense],
                         today: date | None = None) -> Expense:
    """A Draft expense filled in from what the reader found; anything missing is left blank for the review screen.
    The category is the one used last for that vendor."""
    vendor = str(fields.vendor.value or "")
    d = fields.date.value if isinstance(fields.date.value, date) else (today or date.today())
    items = ", ".join(i.description for i in fields.items[:6])
    method = fields.payment_method.value if fields.payment_method.value in expenses.PAYMENT_METHODS else "Card"
    return Expense(
        expense_id=expense_id, expense_date=d, vendor=vendor,
        category=expenses.last_category_for(existing, vendor) if vendor else "",
        amount=fields.total.value if isinstance(fields.total.value, Decimal) else Decimal("0"),
        tax=fields.tax.value if isinstance(fields.tax.value, Decimal) else Decimal("0"),
        payment_method=method, memo=items[:200], status="Draft",
        created=datetime.now().isoformat(timespec="seconds"))


def scans_folder() -> Path:
    """Where scans from the scanner are saved before they are filed with an expense."""
    return Path(os.environ.get("TEMP") or tempfile.gettempdir()) / "TimeBooks Scans"
