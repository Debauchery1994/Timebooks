"""Salary hours typed on the Time Card, and PTO / Holiday / Sick time off."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from timecard import calc
from timecard.models import Company, Employee, TimeEntry
from timecard.storage import TimecardBook

D = Decimal
MON = date(2026, 10, 19)


class SalaryHoursCalcTests(unittest.TestCase):
    def test_typed_hours_replace_punches(self) -> None:
        e = TimeEntry(MON, "E1", time(9), time(17), salary_hours=D("6"))
        self.assertEqual(calc.day_hours(e), D("6.00"))
        self.assertEqual(calc.entry_hours(TimeEntry(MON, "E1", time(8), time(16, 30), lunch_deduct_min=30)),
                         D("8.00"))

    def test_time_off_is_not_hours_worked(self) -> None:
        for kind in ("PTO", "Holiday", "Sick", "pto"):
            e = TimeEntry(MON, "E1", salary_hours=D("8"), hours_type=kind)
            self.assertEqual(calc.day_hours(e), D("0"), kind)
            self.assertEqual(calc.leave_hours(e), D("8.00"), kind)
            self.assertTrue(calc.is_leave(e))
        worked = TimeEntry(MON, "E1", salary_hours=D("8"))
        self.assertEqual((calc.day_hours(worked), calc.leave_hours(worked)), (D("8.00"), D("0")))
        self.assertEqual(calc.hours_type(TimeEntry(MON, "E1", hours_type="vacation??")), "Worked")

    def test_parse_hours(self) -> None:
        self.assertEqual(calc.parse_hours("8"), D("8.00"))
        self.assertEqual(calc.parse_hours(" 7.5 "), D("7.50"))
        self.assertEqual(calc.parse_hours("7:30"), D("7.50"))
        self.assertEqual(calc.parse_hours("0"), D("0.00"))
        self.assertIsNone(calc.parse_hours(""))
        for bad in ("abc", "-1", "25", "8:75", "nan", "inf"):
            with self.assertRaises(ValueError, msg=bad):
                calc.parse_hours(bad)

    def test_week_and_period_totals_count_worked_hours_only(self) -> None:
        c = Company(pay_period="Weekly", week_start="Monday", overtime_enabled=True)
        entries = [TimeEntry(date(2026, 10, 19 + i), "E1", salary_hours=D("10")) for i in range(4)]
        entries.append(TimeEntry(date(2026, 10, 23), "E1", salary_hours=D("8"), hours_type="PTO"))
        s = calc.summarize_hours(entries, c, date(2026, 10, 19), date(2026, 10, 25))
        self.assertEqual((s.regular, s.overtime), (D("40.00"), D("0")))   # PTO doesn't push into overtime
        g = calc.period_log(entries, c)[0]
        self.assertEqual((g.hours, g.leave_hours, g.leave_text()), (D("40.00"), D("8.00"), "PTO 8.00"))

    def test_salaried_pay_unchanged_by_time_off(self) -> None:
        emp = Employee("E1", "Ann", "Lee", pay_type="Salary", pay_rate=D("52000"))
        hours = calc.summarize_hours([TimeEntry(MON, "E1", salary_hours=D("8"), hours_type="PTO")],
                                     Company(), MON, date(2026, 10, 25))
        rec = calc.compute_payroll(emp, hours, Company(pay_period="Weekly"), check_number=1, check_date=MON,
                                   period_start=MON, period_end=date(2026, 10, 25))
        self.assertEqual(rec.gross_pay, D("1000.00"))

    def test_is_empty(self) -> None:
        self.assertFalse(TimeEntry(MON, "E1", salary_hours=D("0")).is_empty())
        self.assertTrue(TimeEntry(MON, "E1", hours_type="PTO").is_empty())   # a type with no hours is nothing


class SalaryHoursStorageTests(unittest.TestCase):
    def test_round_trip_and_older_workbooks(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            b = TimecardBook.new(Company(year=2026))
            b.upsert_employee(Employee("E1", "Ann", "Lee", pay_type="Salary"))
            b.set_entry(TimeEntry(MON, "E1", salary_hours=D("7.5")))
            b.set_entry(TimeEntry(date(2026, 10, 20), "E1", salary_hours=D("8"), hours_type="Holiday"))
            b.set_entry(TimeEntry(date(2026, 10, 21), "E1", time(8), time(16)))
            b.save(path)
            got = TimecardBook.load(path).entries
            self.assertEqual((got[("E1", MON)].salary_hours, calc.hours_type(got[("E1", MON)])), (D("7.50"), "Worked"))
            hol = got[("E1", date(2026, 10, 20))]
            self.assertEqual((hol.salary_hours, hol.hours_type), (D("8.00"), "Holiday"))
            punched = got[("E1", date(2026, 10, 21))]
            self.assertEqual((punched.salary_hours, calc.day_hours(punched)), (None, D("8.00")))

            import openpyxl   # a 1.1.1 workbook has no SalaryHours / HoursType columns
            wb = openpyxl.load_workbook(path)
            ws = wb["TimeLog"]
            ws.delete_cols(11, 2)
            wb.save(path)
            old = TimecardBook.load(path).entries
            self.assertNotIn(("E1", MON), old)   # only typed hours: nothing left without the columns
            self.assertEqual(calc.day_hours(old[("E1", date(2026, 10, 21))]), D("8.00"))
            self.assertEqual(old[("E1", date(2026, 10, 21))].hours_type, "Worked")


if __name__ == "__main__":
    unittest.main()
