"""Mileage log, owners as a pay group (no checks, no 1099), and expenses on a person's account."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import calc, expenses, statements
from timecard.models import Company, Employee, Expense, PayrollRecord, TimeEntry, Trip
from timecard.storage import TimecardBook

D = Decimal


class MileageTests(unittest.TestCase):
    def test_trip_amount_and_account_totals(self) -> None:
        trips = [Trip("T0001", date(2026, 3, 1), "O1", miles=D("12.5"), rate=D("0.70")),
                 Trip("T0002", date(2026, 3, 2), "O1", miles=D("3.3"), rate=D("0.70")),
                 Trip("T0003", date(2025, 12, 30), "O1", miles=D("100"), rate=D("0.70"))]
        self.assertEqual(trips[0].amount, D("8.75"))
        self.assertEqual(trips[1].amount, D("2.31"))
        items = [Expense("X0001", date(2026, 3, 5), vendor="Shell", amount=D("40.00"), person_id="O1"),
                 Expense("X0002", date(2026, 3, 6), vendor="Shell", amount=D("9.99"), person_id="O1", status="Draft"),
                 Expense("X0003", date(2026, 3, 7), vendor="Staples", amount=D("5.00"))]
        self.assertEqual(expenses.account_totals(trips, items, 2026), {"O1": (D("15.8"), D("11.06"), D("40.00"))})
        self.assertEqual(expenses.next_trip_id(trips), "T0004")


class OwnerTests(unittest.TestCase):
    def test_owner_round_trips_and_gets_no_1099_statement(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            b = TimecardBook.new(Company(year=2026, mileage_rate=0.725))
            b.upsert_employee(Employee("O1", "Olive", "Owner", pay_type="Owner"))
            b.upsert_employee(Employee("E1", "Ed", "Hourly", pay_rate=D("20")))
            b.set_entry(TimeEntry(date(2026, 3, 2), "O1", salary_hours=D("8")))
            b.set_entry(TimeEntry(date(2026, 3, 2), "E1", salary_hours=D("8")))
            b.record_payroll([PayrollRecord(1001, date(2026, 3, 6), date(2026, 3, 2), date(2026, 3, 8), "E1", "Ed",
                                            gross_pay=D("160"), net_pay=D("160"))])
            b.trips.append(Trip("T0001", date(2026, 3, 1), "O1", "Supplies run", "Shop", "Depot", D("12.5"), D("0.725")))
            b.expenses.append(Expense("X0001", date(2026, 3, 5), vendor="Shell", amount=D("40"), person_id="O1"))
            b.save(path)
            got = TimecardBook.load(path)
            self.assertTrue(calc.is_owner(got.employees["O1"]))
            self.assertEqual(got.company.mileage_rate, 0.725)
            t = got.trips[0]
            self.assertEqual((t.person_id, t.miles, t.rate, t.amount, t.purpose), ("O1", D("12.50"), D("0.725"),
                                                                                     D("9.06"), "Supplies run"))
            self.assertEqual(got.expenses[0].person_id, "O1")
            self.assertEqual([s.employee.employee_id for s in statements.build_statements(got)], ["E1"])


if __name__ == "__main__":
    unittest.main()
