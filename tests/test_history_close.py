"""1.5: change history written at each save, and closing the books."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import money
from timecard.models import Company, Expense, PayrollRecord
from timecard.storage import TimecardBook

D = Decimal


class HistoryTests(unittest.TestCase):
    def test_every_money_change_is_logged_when_saved(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.xlsx"
            b = TimecardBook.new(Company(company_name="Shop", year=2026))
            b.expenses.append(Expense("X0001", date(2026, 3, 1), "Shell", "Fuel", D("40")))
            b.record_payroll([PayrollRecord(1001, date(2026, 3, 6), date(2026, 3, 1), date(2026, 3, 5), "E1", "Sam",
                                            net_pay=D("800"), gross_pay=D("800"))])
            b.save(p)
            self.assertIn(("Added", "Expense X0001"), [(c.action, c.record) for c in b.history])
            b = TimecardBook.load(p)
            n = len(b.history)
            b.save()                                       # nothing changed: nothing logged
            self.assertEqual(len(b.history), n)
            b.expenses[0].amount = D("45")
            b.void_check(1001)
            b.save()
            new = {c.record: c for c in b.history[n:]}
            self.assertEqual(new["Expense X0001"].action, "Changed")
            self.assertIn("amount 40.00 -> 45.00", new["Expense X0001"].details)
            self.assertIn("voided no -> yes", new["Check #1001"].details)
            self.assertTrue(new["Check #1001"].who)
            b.expenses.clear()
            b.save()
            again = TimecardBook.load(p)                   # the history is kept in the workbook
            self.assertEqual(again.history[-1].action, "Removed")
            self.assertEqual(again.history[-1].record, "Expense X0001")
            self.assertEqual(len(again.history), len(b.history))


class CloseBooksTests(unittest.TestCase):
    def test_closed_through(self) -> None:
        b = TimecardBook.new(Company(company_name="Shop", year=2026))
        self.assertFalse(money.is_closed(b, date(2026, 1, 1)))
        b.company.closed_through = "2026-09-30"
        self.assertTrue(money.is_closed(b, date(2026, 9, 30)))
        self.assertTrue(money.is_closed(b, date(2026, 10, 2), date(2026, 3, 1)))   # any of them
        self.assertFalse(money.is_closed(b, date(2026, 10, 1), None))
        self.assertIn("09/30/2026", money.closed_message(b))
