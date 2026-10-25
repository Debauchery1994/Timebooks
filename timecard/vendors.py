"""Vendors, accounts, vendor 1099 totals and the positive-pay file (1.4).

Vendors: a list of who the business buys from. Expenses carry the vendor's name (and, once linked, its VendorID); the
Unknown vendors list shows expenses with no vendor yet, and assigning one also moves their receipts into that vendor's
folder. Accounts: the bank accounts / cards money is paid from; each expense and check can say which one.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from . import expenses
from .models import Account, Expense, Vendor

if TYPE_CHECKING:
    from .storage import TimecardBook

ACCOUNT_KINDS = ("Checking", "Savings", "Credit card", "Cash", "Other")
ZERO = Decimal("0")


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def next_vendor_id(vendors: dict[str, Vendor]) -> str:
    nums = [int(m[1]) for v in vendors if (m := re.fullmatch(r"V(\d+)", v))]
    return f"V{max(nums, default=0) + 1:04d}"


def next_account_id(accounts: dict[str, Account]) -> str:
    nums = [int(m[1]) for a in accounts if (m := re.fullmatch(r"A(\d+)", a))]
    return f"A{max(nums, default=0) + 1:02d}"


def find_vendor(vendors: dict[str, Vendor], name: str) -> Vendor | None:
    """The vendor whose name matches `name` (ignoring case, spaces and punctuation), if any."""
    k = _key(name)
    return next((v for v in vendors.values() if k and _key(v.name) == k), None) if k else None


# ---- unknown vendors

def unknown_vendor_expenses(book: "TimecardBook", blank_only: bool = False) -> list[Expense]:
    """Expenses with no vendor (blank name), or, unless `blank_only`, with a name that isn't on the Vendors list yet."""
    out = []
    for e in book.expenses:
        if not e.vendor.strip():
            out.append(e)
        elif not blank_only and not e.vendor_id and find_vendor(book.vendors, e.vendor) is None:
            out.append(e)
    return sorted(out, key=lambda e: (e.expense_date, e.expense_id))


def assign_vendor(book: "TimecardBook", items: list[Expense], vendor: Vendor) -> list[str]:
    """Give `items` this vendor (name and VendorID, and its default category where they have none) and move their
    receipts into Receipts\\<year>\\<vendor>. Returns notes about receipts that couldn't be moved."""
    problems = []
    for e in items:
        e.vendor, e.vendor_id = vendor.name, vendor.vendor_id
        if not e.category and vendor.default_category:
            e.category = vendor.default_category
        if e.receipt_file and book.path is not None:
            try:
                e.receipt_file = expenses.refile_receipt(e, book.path)
            except OSError as exc:
                problems.append(f"{e.expense_id}: {exc}")
    return problems


def link_same_name(book: "TimecardBook", vendor: Vendor) -> int:
    """Link expenses already typed with this vendor's name (no VendorID yet) to it. Returns how many."""
    n = 0
    for e in book.expenses:
        if not e.vendor_id and e.vendor.strip() and _key(e.vendor) == _key(vendor.name):
            e.vendor_id = vendor.vendor_id
            n += 1
    return n


# ---- accounts

@dataclass
class AccountTotals:
    expenses: Decimal = ZERO          # approved expenses paid from the account (not by check: those are in checks)
    checks: Decimal = ZERO            # payroll and vendor checks written from it (not voided)

    @property
    def money_out(self) -> Decimal:
        return self.expenses + self.checks


def account_totals(book: "TimecardBook", year: int) -> dict[str, AccountTotals]:
    """Money out per account in `year` ("" = not said). Expenses paid by check aren't counted twice: the check is."""
    out: dict[str, AccountTotals] = {}
    paid_by_check = {p.check_number for p in book.vendor_payments if not p.voided}
    for e in book.expenses:
        if e.expense_date.year != year or e.status == "Draft":
            continue
        if e.check_number and e.check_number in paid_by_check:
            continue
        out.setdefault(e.account_id, AccountTotals()).expenses += e.amount
    checks_acct = book.company.checks_account_id
    for r in book.payroll:
        if not r.voided and r.check_date.year == year:
            out.setdefault(checks_acct, AccountTotals()).checks += r.net_pay
    for p in book.vendor_payments:
        if not p.voided and p.check_date.year == year:
            out.setdefault(p.account_id or checks_acct, AccountTotals()).checks += p.amount
    return out


# ---- 1099 totals for vendors

def vendor_1099_totals(book: "TimecardBook", year: int) -> list[tuple[Vendor, Decimal]]:
    """What each vendor marked "send a 1099" was paid in `year` by check, cash or bank transfer (card payments are
    reported by the card companies, not on a 1099-NEC), largest first. Confirm each year with a tax professional."""
    totals: dict[str, Decimal] = {}
    by_check: set[int] = set()
    for p in book.vendor_payments:
        if not p.voided and p.check_date.year == year:
            totals[p.vendor_id] = totals.get(p.vendor_id, ZERO) + p.amount
            by_check.add(p.check_number)
    for e in book.expenses:
        if (e.expense_date.year != year or e.status == "Draft" or e.payment_method == "Card"
                or (e.check_number and e.check_number in by_check)):
            continue
        v = book.vendors.get(e.vendor_id) or find_vendor(book.vendors, e.vendor)
        if v is not None:
            totals[v.vendor_id] = totals.get(v.vendor_id, ZERO) + e.amount
    rows = [(book.vendors[vid], amt) for vid, amt in totals.items()
            if vid in book.vendors and book.vendors[vid].is_1099]
    return sorted(rows, key=lambda r: (-r[1], r[0].name.lower()))


# ---- positive pay

POSITIVE_PAY_COLUMNS = ("AccountNumber", "CheckNumber", "IssueDate", "Amount", "Payee", "Void")


@dataclass
class IssuedCheck:
    check_number: int
    check_date: date
    amount: Decimal
    payee: str
    voided: bool
    kind: str                 # "Payroll" | "Vendor"


def issued_checks(book: "TimecardBook") -> list[IssuedCheck]:
    """Every check written (payroll and vendor), by number: the one register."""
    out = [IssuedCheck(r.check_number, r.check_date, r.net_pay, r.employee_name, r.voided, "Payroll")
           for r in book.payroll]
    out += [IssuedCheck(p.check_number, p.check_date, p.amount, p.payee, p.voided, "Vendor")
            for p in book.vendor_payments]
    return sorted(out, key=lambda c: (c.check_number, c.kind))


def write_positive_pay(book: "TimecardBook", start: date, end: date, path: str | Path) -> int:
    """A positive-pay file (CSV) of the checks issued (or voided) between `start` and `end`, for uploading to the bank
    so it only pays checks the business wrote. Banks differ: this is the common column set; the account number comes
    from Company Settings (Bank). Returns how many checks were written to the file."""
    rows = [c for c in issued_checks(book) if start <= c.check_date <= end]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(POSITIVE_PAY_COLUMNS)
        for c in rows:
            payee = ("'" + c.payee) if c.payee[:1] in "=+-@" else c.payee   # never read as a formula
            w.writerow([book.company.account_number, c.check_number, c.check_date.strftime("%m/%d/%Y"),
                        f"{c.amount:.2f}", payee, "V" if c.voided else ""])
    return len(rows)
