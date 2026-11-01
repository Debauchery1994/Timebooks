"""1.5: cash advances paid back out of checks (never more than owed or than the check; voids put it back)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import advances, calc, checks, statements
from timecard.models import Company, Employee, HoursSummary, PayrollRecord
from timecard.storage import TimecardBook

D = Decimal


def _book() -> TimecardBook:
    b = TimecardBook.new(Company(company_name="Shop", year=2026, check_layout="voucher"))
    b.upsert_employee(Employee("E001", "Sam", "Mechanic", pay_rate=D("20"), advance_per_check=D("25")))
    return b


def _check(b: TimecardBook, num: int, d: date, net_before: str = "800", advance: str = "0") -> PayrollRecord:
    hours = HoursSummary(regular=D(net_before) / 20)
    return calc.compute_payroll(b.employees["E001"], hours, b.company, check_number=num, check_date=d,
                                period_start=d, period_end=d, advance_repaid=D(advance))


class RepaymentTests(unittest.TestCase):
    def test_never_more_than_owed_or_the_check(self) -> None:
        self.assertEqual(advances.planned_repayment(D("25"), D("10"), D("800")), D("10.00"))   # owes only 10
        self.assertEqual(advances.planned_repayment(D("25"), D("100"), D("800")), D("25.00"))
        self.assertEqual(advances.planned_repayment(D("25"), D("100"), D("15")), D("15.00"))   # small check
        self.assertEqual(advances.planned_repayment(D("0"), D("100"), D("800")), D("0.00"))
        self.assertEqual(advances.planned_repayment(D("25"), D("0"), D("800")), D("0.00"))

    def test_25_a_check_with_10_owed_takes_10_and_clears_it(self) -> None:
        b = _book()
        advances.add_advance(b, "E001", D("60"), date(2026, 1, 5), "Rent")
        b.record_payroll([_check(b, 1001, date(2026, 1, 9), advance="25"),
                          _check(b, 1002, date(2026, 1, 16), advance="25")])
        self.assertEqual(advances.balance(b, "E001"), D("10.00"))
        take = advances.repayment_for(b, b.employees["E001"], D("800"))
        self.assertEqual(take, D("10.00"))
        rec = _check(b, 1003, date(2026, 1, 23), advance=str(take))
        self.assertEqual((rec.gross_pay, rec.advance_repaid, rec.net_pay), (D("800.00"), D("10.00"), D("790.00")))
        b.record_payroll([rec])
        self.assertEqual(advances.balance(b, "E001"), D("0"))
        self.assertEqual(advances.repayment_for(b, b.employees["E001"], D("800")), D("0.00"))   # nothing more

    def test_several_advances_are_one_balance_taken_once_a_check(self) -> None:
        b = _book()
        advances.add_advance(b, "E001", D("60"), date(2026, 1, 5))
        advances.add_advance(b, "E001", D("40"), date(2026, 1, 6))
        self.assertEqual(advances.balance(b, "E001"), D("100.00"))
        take = advances.repayment_for(b, b.employees["E001"], D("800"))
        self.assertEqual(take, D("25.00"))                       # once, not 25 per advance
        b.record_payroll([_check(b, 1001, date(2026, 1, 9), advance=str(take))])
        self.assertEqual(advances.balance(b, "E001"), D("75.00"))
        self.assertEqual(b.payroll[0].net_pay, D("775.00"))

    def test_void_puts_the_repayment_back(self) -> None:
        b = _book()
        advances.add_advance(b, "E001", D("100"), date(2026, 1, 5))
        rec = _check(b, 1001, date(2026, 1, 9), advance="25")
        b.record_payroll([rec])
        self.assertEqual(advances.balance(b, "E001"), D("75.00"))
        rec.voided = True
        self.assertEqual(advances.balance(b, "E001"), D("100.00"))


class RecordsTests(unittest.TestCase):
    def test_saved_and_read_back(self) -> None:
        b = _book()
        advances.add_advance(b, "E001", D("100"), date(2026, 1, 5), "Rent")
        b.record_payroll([_check(b, 1001, date(2026, 1, 9), advance="25")])
        with tempfile.TemporaryDirectory() as d:
            b.save(Path(d) / "b.xlsx")
            again = TimecardBook.load(Path(d) / "b.xlsx")
        self.assertEqual(again.employees["E001"].advance_per_check, D("25.00"))
        self.assertEqual([(a.amount, a.memo) for a in again.advances], [(D("100.00"), "Rent")])
        self.assertEqual(again.payroll[0].advance_repaid, D("25.00"))
        self.assertEqual(advances.balance(again, "E001"), D("75.00"))

    def test_new_year_starts_with_what_is_still_owed(self) -> None:
        old = _book()
        advances.add_advance(old, "E001", D("100"), date(2026, 3, 1))
        old.record_payroll([_check(old, 1001, date(2026, 3, 6), advance="25")])
        new = TimecardBook.new(Company(company_name="Shop", year=2027))
        new.upsert_employee(old.employees["E001"])
        advances.carry_over(old, new)
        self.assertEqual(advances.balance(new, "E001"), D("75.00"))
        self.assertEqual(new.advances[0].advance_date, date(2027, 1, 1))
        advances.add_advance(new, "E001", D("40"), date(2027, 2, 1))
        new.record_payroll([_check(new, 2001, date(2027, 2, 5), advance="25")])
        # carried over is kept apart from what was advanced this year; paid-off advances stay in the log
        self.assertEqual(advances.year_totals(new, "E001"), (D("75.00"), D("40.00"), D("25.00"), D("90.00")))
        with tempfile.TemporaryDirectory() as d:
            new.save(Path(d) / "n.xlsx")
            again = TimecardBook.load(Path(d) / "n.xlsx")
        self.assertEqual([a.carried_over for a in again.advances], [True, False])
        self.assertEqual(advances.year_totals(again, "E001"), (D("75.00"), D("40.00"), D("25.00"), D("90.00")))

    def test_pay_for_the_1099_is_unchanged_and_the_stub_shows_it(self) -> None:
        b = _book()
        advances.add_advance(b, "E001", D("100"), date(2026, 1, 5))
        rec = _check(b, 1001, date(2026, 1, 9), advance="25")
        b.record_payroll([rec])
        s = statements.build_statements(b)[0]
        self.assertEqual((s.gross, s.adjustments, s.paid), (D("800.00"), D("-25.00"), D("775.00")))
        old = checks.PAGE_COMPRESSION
        checks.PAGE_COMPRESSION = 0
        try:
            with tempfile.TemporaryDirectory() as d:
                text = checks.render_checks([rec], b, Path(d) / "c.pdf").read_bytes().decode("latin-1")
        finally:
            checks.PAGE_COMPRESSION = old
        self.assertIn("Advance Repaid", text)
        self.assertIn("Advance Balance", text)
        self.assertIn("75.00", text)
