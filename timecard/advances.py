"""Cash advances (1.5): interest-free money advanced to a person and paid back in full by a set amount taken off each
paycheck (Employee.advance_per_check).

What a person owes is always worked out, never stored: their advances minus what their checks paid back
(PayrollRecord.advance_repaid, voided checks left out). So voiding a check puts its repayment back, and nothing has to
be kept in step by hand. A repayment never takes more than is owed, or more than the check.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from . import calc
from .models import Advance, Employee, PayrollRecord

if TYPE_CHECKING:
    from .storage import TimecardBook

ZERO = Decimal("0")
CARRIED = "Carried over"   # memo of the opening advance made in a new year's workbook


def next_advance_id(advances: list[Advance]) -> str:
    nums = [int(a.advance_id[1:]) for a in advances if a.advance_id[1:].isdigit()]
    return f"A{max(nums, default=0) + 1:04d}"


def advanced(book: "TimecardBook", employee_id: str) -> Decimal:
    return sum((a.amount for a in book.advances if a.employee_id == employee_id), ZERO)


def repaid(book: "TimecardBook", employee_id: str) -> Decimal:
    return sum((r.advance_repaid for r in book.payroll if r.employee_id == employee_id and not r.voided), ZERO)


def balance(book: "TimecardBook", employee_id: str) -> Decimal:
    """What the person still owes (never below zero)."""
    return max(calc.money(advanced(book, employee_id) - repaid(book, employee_id)), ZERO)


def year_totals(book: "TimecardBook", employee_id: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(carried over from last year, advanced this year, paid back, still owed) for the workbook's year. Advances
    are never deleted when paid off, so this is the running log for the year."""
    mine = [a for a in book.advances if a.employee_id == employee_id]
    carried = sum((a.amount for a in mine if a.carried_over), ZERO)
    given = sum((a.amount for a in mine if not a.carried_over), ZERO)
    return carried, given, repaid(book, employee_id), balance(book, employee_id)


def balance_after(book: "TimecardBook", rec: PayrollRecord) -> Decimal:
    """What was still owed once check `rec` was written (for its stub): advances up to its date, less what it and the
    checks before it paid back."""
    before = [r for r in book.payroll if r.employee_id == rec.employee_id and not r.voided
              and (r.check_date, r.check_number) <= (rec.check_date, rec.check_number)]
    owed = sum((a.amount for a in book.advances if a.employee_id == rec.employee_id
                and a.advance_date <= rec.check_date), ZERO)
    return max(calc.money(owed - sum((r.advance_repaid for r in before), ZERO)), ZERO)


def planned_repayment(per_check: Decimal, owed: Decimal, check_amount: Decimal) -> Decimal:
    """What to take off a check: the per-check amount, but never more than is owed or than the check itself
    (e.g. 25.00 a check with 10.00 owed takes 10.00)."""
    return max(calc.money(min(per_check or ZERO, owed or ZERO, check_amount or ZERO)), ZERO)


def repayment_for(book: "TimecardBook", emp: Employee, check_amount: Decimal) -> Decimal:
    return planned_repayment(emp.advance_per_check, balance(book, emp.employee_id), check_amount)


def add_advance(book: "TimecardBook", employee_id: str, amount: Decimal, when: date, memo: str = "") -> Advance:
    if amount <= 0:
        raise ValueError("The advance must be more than zero.")
    a = Advance(next_advance_id(book.advances), employee_id, when, calc.money(amount), memo.strip())
    book.advances.append(a)
    book.dirty = True
    return a


def carry_over(old: "TimecardBook", new: "TimecardBook") -> None:
    """New year's workbook: each person's unpaid balance starts it as one advance dated Jan 1."""
    for eid in new.employees:
        owed = balance(old, eid)
        if owed:
            new.advances.append(Advance(next_advance_id(new.advances), eid, date(new.company.year, 1, 1), owed,
                                        f"{CARRIED} from {old.company.year}", carried_over=True))
