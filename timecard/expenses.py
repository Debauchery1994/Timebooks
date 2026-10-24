"""Expenses and their receipts: categories, filing receipt files next to the workbook, filters and totals.

Receipt files are never stored inside the .xlsx (it would grow huge and slow to save). They are copied into
`Receipts\\<year>\\<vendor>` next to the workbook and the expense keeps the path relative to the workbook's folder, so
the workbook and its receipts can move together (another folder, a USB drive, a backup). When an expense's vendor,
date or amount is corrected, its receipt is moved and renamed to match (`refile_receipt`).
"""
from __future__ import annotations

import calendar
import re
import shutil
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import Expense, Recurring, Trip

# IRS Schedule C expense lines, so the tax professional can use the totals directly. The owner can add more.
DEFAULT_CATEGORIES = (
    "Advertising", "Car & truck", "Commissions & fees", "Contract labor", "Insurance", "Legal & professional",
    "Office expense", "Rent (equipment)", "Rent (property)", "Repairs & maintenance", "Supplies",
    "Taxes & licenses", "Travel", "Meals", "Utilities", "Other",
)
PAYMENT_METHODS = ("Cash", "Card", "Check", "Bank transfer", "Other")
REPEATS = ("Monthly", "Quarterly", "Yearly")
_MONTHS_PER = {"Monthly": 1, "Quarterly": 3, "Yearly": 12}
STATUSES = ("Approved", "Draft")   # Draft = scanned, not reviewed yet (1.3.0 part C)
RECEIPTS_FOLDER = "Receipts"
RECEIPT_TYPES = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".pdf")


def next_expense_id(expenses: list[Expense]) -> str:
    """'X0001', 'X0002', ... one more than the highest used."""
    nums = [int(m[1]) for e in expenses if (m := re.fullmatch(r"X(\d+)", e.expense_id))]
    return f"X{max(nums, default=0) + 1:04d}"


UNKNOWN_VENDOR = "Unknown vendor"   # folder for receipts the reader couldn't name (moved once the vendor is set)


def _safe(text: str) -> str:
    """Text Windows accepts in a file or folder name."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", text).strip(" .")[:40]


def receipt_name(e: Expense, suffix: str) -> str:
    """'2026-10-24 Home Depot 84.17.jpg' with characters Windows can't use in file names removed."""
    return f"{e.expense_date:%Y-%m-%d} {_safe(e.vendor) or 'Receipt'} {e.amount:.2f}{suffix.lower()}"


def receipt_folder(e: Expense, workbook: str | Path) -> Path:
    """Receipts\\<year>\\<vendor> next to the workbook."""
    return Path(workbook).parent / RECEIPTS_FOLDER / str(e.expense_date.year) / (_safe(e.vendor) or UNKNOWN_VENDOR)


def _free_name(folder: Path, name: str) -> Path:
    """folder/name, or 'name (2)', 'name (3)', ... so another receipt is never overwritten."""
    target, n, stem, suffix = folder / name, 2, Path(name).stem, Path(name).suffix
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def file_receipt(source: str | Path, e: Expense, workbook: str | Path) -> str:
    """Copy `source` into Receipts\\<year>\\<vendor> next to `workbook` and return the path to store on the expense
    (relative to the workbook's folder). Never overwrites another receipt."""
    src = Path(source)
    if src.suffix.lower() not in RECEIPT_TYPES:
        raise ValueError(f"{src.name} isn't a picture or PDF. Attach a photo, a scan or a PDF of the receipt.")
    base = Path(workbook).parent
    folder = receipt_folder(e, workbook)
    folder.mkdir(parents=True, exist_ok=True)
    target = _free_name(folder, receipt_name(e, src.suffix))
    shutil.copy2(src, target)
    return str(target.relative_to(base))


def refile_receipt(e: Expense, workbook: str | Path) -> str:
    """After the vendor, date or amount of `e` changed: move its receipt to the matching Receipts\\<year>\\<vendor>
    folder and name, and return the new relative path. A receipt outside the workbook's Receipts folder, or a missing
    file, is left where it is (the stored path is returned unchanged)."""
    base = Path(workbook).parent
    current = receipt_path(e, workbook)
    receipts = (base / RECEIPTS_FOLDER).resolve()
    if current is not None and not current.is_file():
        # The file is already where these details say it belongs (a save that moved it but failed before the
        # expense was updated, in 1.3.0): link to it again.
        moved = receipt_folder(e, workbook) / receipt_name(e, current.suffix)
        return str(moved.relative_to(base)) if moved.is_file() else e.receipt_file
    if current is None or receipts not in current.resolve().parents:
        return e.receipt_file
    folder = receipt_folder(e, workbook)
    wanted = receipt_name(e, current.suffix)
    if current.parent.resolve() == folder.resolve() and (current.name == wanted or current.stem.startswith(
            Path(wanted).stem + " (")):
        return e.receipt_file   # already in place
    folder.mkdir(parents=True, exist_ok=True)
    target = _free_name(folder, wanted)
    old_folder = current.parent
    shutil.move(str(current), str(target))
    try:
        if old_folder.resolve() != receipts and not any(old_folder.iterdir()):
            old_folder.rmdir()   # e.g. an emptied "Unknown vendor" folder
    except OSError:
        pass
    return str(target.relative_to(base))


def receipt_path(e: Expense, workbook: str | Path | None) -> Path | None:
    """The receipt file on disk for `e` (None when it has none, or the workbook isn't saved yet)."""
    if not e.receipt_file or workbook is None:
        return None
    p = Path(e.receipt_file)
    return p if p.is_absolute() else Path(workbook).parent / p


def matches(e: Expense, month: int | None = None, category: str = "", vendor: str = "") -> bool:
    """Filter used by the Expenses tab: month 1-12 (None = all), exact category, vendor contains (any case)."""
    return ((month is None or e.expense_date.month == month)
            and (not category or e.category == category)
            and (not vendor or vendor.strip().lower() in e.vendor.lower()))


def totals_by_category(expenses: list[Expense]) -> list[tuple[str, Decimal]]:
    """(category, total amount) largest first; drafts (not reviewed yet) are left out."""
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for e in expenses:
        if e.status != "Draft":
            totals[e.category or "Other"] += e.amount
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def _vendor_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def possible_duplicates(e: Expense, items: list[Expense], days: int = 3) -> list[Expense]:
    """Other expenses that look like the same purchase: the same amount within `days` days, and the same or a
    similar vendor (one name contains the other, ignoring case and punctuation), or either has no vendor yet."""
    if e.amount <= 0:
        return []
    mine = _vendor_key(e.vendor)
    out = []
    for o in items:
        if o.expense_id == e.expense_id or o.amount != e.amount:
            continue
        if abs((o.expense_date - e.expense_date).days) > days:
            continue
        theirs = _vendor_key(o.vendor)
        if not mine or not theirs or mine in theirs or theirs in mine:
            out.append(o)
    return out


def vendors(expenses: list[Expense]) -> list[str]:
    """Vendor names already used, for typing suggestions (most used first)."""
    count: dict[str, int] = defaultdict(int)
    for e in expenses:
        if e.vendor.strip():
            count[e.vendor.strip()] += 1
    return sorted(count, key=lambda v: (-count[v], v.lower()))


def last_category_for(expenses: list[Expense], vendor: str) -> str:
    """The category used last time for this vendor (so the next expense from them suggests it), or ''."""
    same = [e for e in expenses if e.vendor.strip().lower() == vendor.strip().lower() and e.category]
    return max(same, key=lambda e: (e.expense_date, e.expense_id)).category if same else ""


# ---- recurring expenses

def add_period(d: date, every: str, anchor_day: int | None = None) -> date:
    """The next due date: one month / quarter / year on, keeping the original day of the month where it exists
    (the 31st becomes the last day of shorter months, then goes back to the 31st)."""
    months = _MONTHS_PER.get(every, 1)
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    day = min(anchor_day or d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_recurring_id(items: list[Recurring]) -> str:
    nums = [int(m[1]) for r in items if (m := re.fullmatch(r"R(\d+)", r.recurring_id))]
    return f"R{max(nums, default=0) + 1:03d}"


def recurring_from(e: Expense, every: str, recurring_id: str) -> Recurring:
    """Make `e` repeat: the next one is due one period after it."""
    return Recurring(recurring_id=recurring_id, vendor=e.vendor, category=e.category, amount=e.amount,
                     payment_method=e.payment_method, memo=e.memo, every=every,
                     next_date=add_period(e.expense_date, every), active=True, day=e.expense_date.day)


def due_drafts(items: list[Recurring], existing: list[Expense], today: date) -> list[Expense]:
    """Draft expenses for every recurring expense due on or before `today` (several if the workbook wasn't opened for
    a while), moving each one's next date on. A period that already has an expense from it is never added twice."""
    out: list[Expense] = []
    taken = {(e.recurring_id, e.expense_date) for e in existing if e.recurring_id}
    for r in items:
        if not r.active or r.next_date is None:
            continue
        anchor = r.day or r.next_date.day
        while r.next_date <= today:
            if (r.recurring_id, r.next_date) not in taken:
                out.append(Expense(expense_id=next_expense_id(existing + out), expense_date=r.next_date,
                                   vendor=r.vendor, category=r.category, amount=r.amount,
                                   payment_method=r.payment_method, memo=r.memo, status="Draft",
                                   recurring_id=r.recurring_id, created=today.isoformat()))
            r.next_date = add_period(r.next_date, r.every, anchor)
    return out


# ---- mileage and accounts

def next_trip_id(trips: list[Trip]) -> str:
    nums = [int(m[1]) for t in trips if (m := re.fullmatch(r"T(\d+)", t.trip_id))]
    return f"T{max(nums, default=0) + 1:04d}"


def account_totals(trips: list[Trip], items: list[Expense], year: int) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
    """Per person ("" = the business): (miles, mileage amount, expenses on their account) in `year`; drafts left out."""
    out: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0"), Decimal("0")])
    for t in trips:
        if t.trip_date.year == year:
            out[t.person_id][0] += t.miles
            out[t.person_id][1] += t.amount
    for e in items:
        if e.expense_date.year == year and e.status != "Draft" and e.person_id:
            out[e.person_id][2] += e.amount
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}

