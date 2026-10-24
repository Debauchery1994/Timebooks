"""Time off from Company Settings (PTO / Holiday / Sick hours and whether hourly people are paid), and Closed days."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

from timecard import calc
from timecard.models import Company, Employee, TimeEntry
from timecard.storage import TimecardBook

D = Decimal
MON = date(2026, 10, 19)
SUN = MON + timedelta(days=6)


def _worked(day: int, eid: str = "E1") -> TimeEntry:
    return TimeEntry(MON + timedelta(days=day), eid, time(8), time(16))   # 8 hours


def _pay(emp: Employee, entries: list[TimeEntry], company: Company):
    hours = calc.summarize_hours(entries, company, MON, SUN)
    return calc.compute_payroll(emp, hours, company, check_number=1, check_date=SUN,
                                period_start=MON, period_end=SUN)


class TimeOffRuleTests(unittest.TestCase):
    def test_default_hours_come_from_company_settings(self) -> None:
        c = Company(pto_hours=8, holiday_hours=10, sick_hours=4)
        self.assertEqual([calc.leave_default_hours(c, k) for k in ("PTO", "Holiday", "Sick")],
                         [D("8.00"), D("10.00"), D("4.00")])
        self.assertEqual(calc.leave_default_hours(c, "Closed"), D("0.00"))
        self.assertEqual(calc.leave_default_hours(c, "Worked"), D("0.00"))
        self.assertEqual(calc.leave_default_hours(Company(pto_hours=30), "PTO"), D("24.00"))

    def test_closed_day_is_zero_hours_and_kept(self) -> None:
        e = TimeEntry(MON, "E1", salary_hours=D("8"), hours_type="Closed")
        self.assertEqual((calc.entry_hours(e), calc.day_hours(e), calc.leave_hours(e)), (0, 0, 0))
        self.assertFalse(calc.is_leave(e))
        self.assertTrue(calc.is_closed(e))
        self.assertFalse(TimeEntry(MON, "E1", hours_type="Closed").is_empty())
        log = calc.PeriodLog(MON, SUN, "E1", [TimeEntry(MON, "E1", hours_type="Closed"),
                                               TimeEntry(MON + timedelta(1), "E1", salary_hours=D("8"),
                                                         hours_type="PTO")])
        self.assertEqual(log.leave_text(), "PTO 8.00, Closed 1 day")

    def test_hourly_worked_hours_are_the_punches_and_paid_pto_is_added(self) -> None:
        emp = Employee("E1", "Ann", "Lee", pay_rate=D("20"))
        entries = [_worked(i) for i in range(4)] + [
            TimeEntry(MON + timedelta(4), "E1", salary_hours=D("8"), hours_type="PTO")]
        rec = _pay(emp, entries, Company())
        self.assertEqual((rec.regular_hours, rec.leave_hours), (D("32.00"), D("8.00")))
        self.assertEqual((rec.regular_pay, rec.leave_pay, rec.gross_pay), (D("640.00"), D("160.00"), D("800.00")))

    def test_unpaid_time_off_is_only_noted(self) -> None:
        emp = Employee("E1", "Ann", "Lee", pay_rate=D("20"))
        entries = [_worked(0), TimeEntry(MON + timedelta(1), "E1", salary_hours=D("8"), hours_type="Holiday")]
        rec = _pay(emp, entries, Company(holiday_paid=False))
        self.assertEqual((rec.leave_hours, rec.leave_pay, rec.gross_pay), (D("0.00"), D("0.00"), D("160.00")))

    def test_closed_day_is_unpaid_for_hourly(self) -> None:
        emp = Employee("E1", "Ann", "Lee", pay_rate=D("20"))
        rec = _pay(emp, [_worked(0), TimeEntry(MON + timedelta(1), "E1", hours_type="Closed")], Company())
        self.assertEqual(rec.gross_pay, D("160.00"))

    def test_paid_time_off_never_counts_toward_overtime(self) -> None:
        emp = Employee("E1", "Ann", "Lee", pay_rate=D("20"))
        entries = [_worked(i) for i in range(5)] + [
            TimeEntry(MON + timedelta(5), "E1", salary_hours=D("8"), hours_type="Holiday")]
        rec = _pay(emp, entries, Company(overtime_enabled=True, overtime_threshold_weekly=40))
        self.assertEqual((rec.regular_hours, rec.overtime_hours, rec.leave_hours), (D("40.00"), 0, D("8.00")))
        self.assertEqual(rec.gross_pay, D("960.00"))

    def test_salaried_pay_is_unchanged_by_time_off(self) -> None:
        emp = Employee("E1", "Ann", "Lee", pay_type="Salary", pay_rate=D("52000"))
        entries = [TimeEntry(MON, "E1", salary_hours=D("8"), hours_type="PTO"),
                   TimeEntry(MON + timedelta(1), "E1", hours_type="Closed")]
        rec = _pay(emp, entries, Company(pay_period="Weekly"))
        self.assertEqual((rec.gross_pay, rec.leave_pay, rec.leave_hours), (D("1000.00"), D("0.00"), D("8.00")))


class TimeOffStorageTests(unittest.TestCase):
    def test_round_trip_settings_closed_days_and_check_time_off(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            b = TimecardBook.new(Company(year=2026, pto_hours=7.5, sick_paid=False))
            emp = Employee("E1", "Ann", "Lee", pay_rate=D("20"))
            b.upsert_employee(emp)
            entries = [_worked(0), TimeEntry(MON + timedelta(1), "E1", hours_type="Closed"),
                       TimeEntry(MON + timedelta(2), "E1", salary_hours=D("7.5"), hours_type="PTO")]
            for e in entries:
                b.set_entry(e)
            b.record_payroll([_pay(emp, entries, b.company)])
            b.save(path)

            got = TimecardBook.load(path)
            self.assertEqual((got.company.pto_hours, got.company.sick_paid, got.company.holiday_paid),
                             (7.5, False, True))
            self.assertEqual(got.entries[("E1", MON + timedelta(1))].hours_type, "Closed")
            rec = got.payroll[0]
            self.assertEqual((rec.leave_hours, rec.leave_pay, rec.gross_pay), (D("7.50"), D("150.00"), D("310.00")))

            import openpyxl   # a 1.1.2 workbook has no LeaveHours / LeavePay columns
            wb = openpyxl.load_workbook(path)
            ws = wb["Payroll"]
            ws.delete_cols(20, 2)
            wb.save(path)
            old = TimecardBook.load(path).payroll[0]
            self.assertEqual((old.leave_hours, old.leave_pay, old.gross_pay), (D("0.00"), D("0.00"), D("310.00")))


if __name__ == "__main__":
    unittest.main()
