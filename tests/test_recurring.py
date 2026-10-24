"""Recurring expenses: due dates, drafts when they come due (never twice), and the Recurring sheet."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import expenses
from timecard.models import Company, Expense, Recurring
from timecard.storage import TimecardBook


class DueDateTests(unittest.TestCase):
    def test_month_end_days_and_periods(self) -> None:
        self.assertEqual(expenses.add_period(date(2026, 1, 31), "Monthly"), date(2026, 2, 28))
        self.assertEqual(expenses.add_period(date(2026, 2, 28), "Monthly", 31), date(2026, 3, 31))   # back to the 31st
        self.assertEqual(expenses.add_period(date(2026, 11, 15), "Quarterly"), date(2027, 2, 15))
        self.assertEqual(expenses.add_period(date(2028, 2, 29), "Yearly"), date(2029, 2, 28))


class DueDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        rent = Expense("X0001", date(2026, 7, 31), vendor="Landlord", category="Rent (property)",
                       amount=Decimal("1200.00"), payment_method="Check")
        self.rec = expenses.recurring_from(rent, "Monthly", "R001")
        self.existing = [rent]

    def test_catches_up_once_per_period_as_drafts(self) -> None:
        drafts = expenses.due_drafts([self.rec], self.existing, date(2026, 10, 30))
        self.assertEqual([d.expense_date for d in drafts], [date(2026, 8, 31), date(2026, 9, 30)])
        self.assertTrue(all(d.status == "Draft" and d.recurring_id == "R001" for d in drafts))
        self.assertEqual(self.rec.next_date, date(2026, 10, 31))
        self.assertEqual(len({d.expense_id for d in drafts} | {"X0001"}), 3)
        self.assertEqual(expenses.due_drafts([self.rec], self.existing + drafts, date(2026, 10, 30)), [])

    def test_never_added_twice_for_a_period_already_there(self) -> None:
        already = Expense("X0009", date(2026, 8, 31), vendor="Landlord", recurring_id="R001")
        drafts = expenses.due_drafts([self.rec], self.existing + [already], date(2026, 9, 1))
        self.assertEqual(drafts, [])
        self.assertEqual(self.rec.next_date, date(2026, 9, 30))

    def test_stopped_ones_add_nothing(self) -> None:
        self.rec.active = False
        self.assertEqual(expenses.due_drafts([self.rec], self.existing, date(2027, 1, 1)), [])


class RecurringStorageTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            b = TimecardBook.new(Company(year=2026))
            b.recurring.append(Recurring("R001", "Landlord", "Rent (property)", Decimal("1200.00"), "Check", "",
                                         "Monthly", date(2026, 11, 30), True, 31))
            b.expenses.append(Expense("X0001", date(2026, 10, 31), vendor="Landlord", recurring_id="R001"))
            b.save(path)
            got = TimecardBook.load(path)
            r = got.recurring[0]
            self.assertEqual((r.vendor, r.amount, r.every, r.next_date, r.active, r.day),
                             ("Landlord", Decimal("1200.00"), "Monthly", date(2026, 11, 30), True, 31))
            self.assertEqual(got.expenses[0].recurring_id, "R001")


if __name__ == "__main__":
    unittest.main()
