"""Tests for timecard.calc business rules."""
from __future__ import annotations

import unittest
from datetime import date, time, timedelta
from decimal import Decimal

from timecard import calc
from timecard.models import Company, Employee, HoursSummary, PayrollRecord, TimeEntry

D = Decimal


def entry(d: date, in1: str = "", out1: str = "", in2: str = "", out2: str = "",
          lunch: int = 0, emp: str = "E001") -> TimeEntry:
    p = calc.parse_time
    return TimeEntry(d, emp, p(in1), p(out1), p(in2), p(out2), lunch)


class ParseTimeTests(unittest.TestCase):
    def test_accepted_forms(self) -> None:
        cases = {
            "8": time(8, 0), "8:30": time(8, 30), "830a": time(8, 30), "830": time(8, 30),
            "5:15 pm": time(17, 15), "5:15PM": time(17, 15), "17:15": time(17, 15),
            "1715": time(17, 15), "12a": time(0, 0), "12p": time(12, 0), "12:30 am": time(0, 30),
            "12:45p.m.": time(12, 45), "0": time(0, 0), "  9:05 a ": time(9, 5), "7.45": time(7, 45),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(calc.parse_time(text), expected)

    def test_blank_is_none(self) -> None:
        self.assertIsNone(calc.parse_time(""))
        self.assertIsNone(calc.parse_time("   "))

    def test_junk_raises(self) -> None:
        for text in ("abc", "25", "8:61", "13pm", "0a", "8:3", "12345", "8::30", "-1"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                calc.parse_time(text)

    def test_format_time(self) -> None:
        self.assertEqual(calc.format_time(time(8, 30)), "8:30 AM")
        self.assertEqual(calc.format_time(time(0, 5)), "12:05 AM")
        self.assertEqual(calc.format_time(time(12, 0)), "12:00 PM")
        self.assertEqual(calc.format_time(time(17, 15)), "5:15 PM")
        self.assertEqual(calc.format_time(None), "")
        self.assertEqual(calc.parse_time(calc.format_time(time(23, 59))), time(23, 59))

    def test_out_without_ampm_inferred_after_in(self) -> None:
        self.assertEqual(calc.parse_time("5", after=time(8)), time(17))
        self.assertEqual(calc.parse_time("4:30", after=time(12, 30)), time(16, 30))
        self.assertEqual(calc.parse_time("12", after=time(8)), time(12))
        self.assertEqual(calc.parse_time("6", after=time(22)), time(6))  # overnight stays AM
        self.assertEqual(calc.parse_time("5am", after=time(8)), time(5))  # explicit marker wins
        self.assertEqual(calc.parse_time("9", after=time(8)), time(9))


class HoursTests(unittest.TestCase):
    def test_pair_hours(self) -> None:
        self.assertEqual(calc.pair_hours(time(8), time(16, 30)), D("8.50"))
        self.assertEqual(calc.pair_hours(time(8), None), D("0"))
        self.assertEqual(calc.pair_hours(None, time(8)), D("0"))

    def test_midnight_crossing(self) -> None:
        self.assertEqual(calc.pair_hours(time(22), time(6)), D("8.00"))
        self.assertEqual(calc.day_hours(entry(date(2026, 1, 5), "10p", "2:30a")), D("4.50"))

    def test_two_pairs(self) -> None:
        e = entry(date(2026, 1, 5), "8", "12", "12:30p", "5p")
        self.assertEqual(calc.day_hours(e), D("8.50"))

    def test_lunch_deduct_subtracts_minutes(self) -> None:
        e = entry(date(2026, 1, 5), "8a", "5p", lunch=30)
        self.assertEqual(calc.day_hours(e), D("8.50"))
        e = entry(date(2026, 1, 5), "8a", "4:20p", lunch=20)
        self.assertEqual(calc.day_hours(e), D("8.00"))

    def test_lunch_deduct_never_negative(self) -> None:
        self.assertEqual(calc.day_hours(entry(date(2026, 1, 5), "8a", "8:15a", lunch=30)), D("0.00"))
        self.assertEqual(calc.day_hours(entry(date(2026, 1, 5), lunch=45)), D("0.00"))

    def test_incomplete_pair_ignored(self) -> None:
        self.assertEqual(calc.day_hours(entry(date(2026, 1, 5), "8a", "", "1p", "5p")), D("4.00"))

    def test_rounding_2dp(self) -> None:
        # 8:00 -> 8:10 = 0.1666.. h -> 0.17
        self.assertEqual(calc.day_hours(entry(date(2026, 1, 5), "8", "8:10")), D("0.17"))


class PeriodTests(unittest.TestCase):
    def test_week_start_for(self) -> None:
        thu = date(2026, 1, 8)
        self.assertEqual(calc.week_start_for(thu, "Monday"), date(2026, 1, 5))
        self.assertEqual(calc.week_start_for(thu, "Sunday"), date(2026, 1, 4))
        self.assertEqual(calc.week_start_for(date(2026, 1, 4), "Sunday"), date(2026, 1, 4))
        self.assertEqual(calc.week_start_for(date(2026, 1, 4), "Monday"), date(2025, 12, 29))

    def test_weekly(self) -> None:
        c = Company(year=2026, pay_period="Weekly", week_start="Monday")
        self.assertEqual(calc.period_for(date(2026, 1, 8), c), (date(2026, 1, 5), date(2026, 1, 11)))

    def test_biweekly_anchor(self) -> None:
        # Jan 1 2026 is a Thursday; Monday anchor = 2025-12-29.
        c = Company(year=2026, pay_period="Biweekly", week_start="Monday")
        self.assertEqual(calc.period_for(date(2026, 1, 1), c), (date(2025, 12, 29), date(2026, 1, 11)))
        self.assertEqual(calc.period_for(date(2026, 1, 12), c), (date(2026, 1, 12), date(2026, 1, 25)))
        self.assertEqual(calc.period_for(date(2025, 12, 20), c), (date(2025, 12, 15), date(2025, 12, 28)))

    def test_semimonthly_and_monthly(self) -> None:
        c = Company(year=2028, pay_period="Semimonthly")
        self.assertEqual(calc.period_for(date(2028, 2, 15), c), (date(2028, 2, 1), date(2028, 2, 15)))
        self.assertEqual(calc.period_for(date(2028, 2, 16), c), (date(2028, 2, 16), date(2028, 2, 29)))
        c.pay_period = "Monthly"
        self.assertEqual(calc.period_for(date(2028, 4, 9), c), (date(2028, 4, 1), date(2028, 4, 30)))

    def test_periods_in_year_cover_year(self) -> None:
        for kind, count in (("Weekly", 53), ("Biweekly", 27), ("Semimonthly", 24), ("Monthly", 12)):
            with self.subTest(kind=kind):
                c = Company(year=2026, pay_period=kind, week_start="Monday")
                periods = calc.periods_in_year(c)
                self.assertEqual(len(periods), count)
                self.assertLessEqual(periods[0][0], date(2026, 1, 1))
                self.assertGreaterEqual(periods[-1][1], date(2026, 12, 31))
                for (_, e1), (s2, _) in zip(periods, periods[1:]):
                    self.assertEqual(s2, e1 + timedelta(days=1))


class OvertimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company = Company(year=2026, pay_period="Weekly", week_start="Monday", overtime_enabled=True)

    def test_weekly_overtime_at_40(self) -> None:
        mon = date(2026, 1, 5)
        entries = [entry(mon + timedelta(days=i), "8a", "6p", lunch=30) for i in range(5)]  # 9.5 x 5
        s = calc.summarize_hours(entries, self.company, mon, mon + timedelta(days=6))
        self.assertEqual(s.regular, D("40.00"))
        self.assertEqual(s.overtime, D("7.50"))
        self.assertEqual(s.by_week, [(mon, D("47.50"))])

        emp = Employee("E001", "Ann", "Lee", pay_rate=D("20.00"))
        rec = calc.compute_payroll(emp, s, self.company, check_number=1001, check_date=date(2026, 1, 16),
                                   period_start=mon, period_end=mon + timedelta(days=6))
        self.assertEqual(rec.regular_pay, D("800.00"))
        self.assertEqual(rec.overtime_pay, D("225.00"))  # 7.5 * 20 * 1.5
        self.assertEqual(rec.gross_pay, D("1025.00"))

    def test_no_overtime_under_threshold(self) -> None:
        mon = date(2026, 1, 5)
        entries = [entry(mon + timedelta(days=i), "9", "5p") for i in range(5)]
        s = calc.summarize_hours(entries, self.company, mon, mon + timedelta(days=6))
        self.assertEqual((s.regular, s.overtime), (D("40.00"), D("0")))

    def test_overtime_split_across_biweekly_workweeks(self) -> None:
        c = Company(year=2026, pay_period="Biweekly", week_start="Monday", overtime_enabled=True)
        start, end = calc.period_for(date(2026, 1, 12), c)  # Jan 12 .. Jan 25
        wk1 = [entry(start + timedelta(days=i), "7a", "5:30p", lunch=30) for i in range(5)]  # 50h
        wk2 = [entry(start + timedelta(days=7 + i), "8a", "4p") for i in range(4)]          # 32h
        s = calc.summarize_hours(wk1 + wk2, c, start, end)
        # 82h total, but only week 1 exceeds 40 -> 10 OT (not 2).
        self.assertEqual(s.overtime, D("10.00"))
        self.assertEqual(s.regular, D("72.00"))
        self.assertEqual(s.by_week, [(start, D("50.00")), (start + timedelta(days=7), D("32.00"))])

    def test_straddling_week_uses_in_period_days_only(self) -> None:
        c = Company(year=2026, pay_period="Semimonthly", week_start="Monday", overtime_enabled=True)
        # Mon Jan 12..Sun Jan 18 straddles the Jan 15 boundary; 11h/day Mon-Fri (55h, 15 OT if whole).
        days = [entry(date(2026, 1, 12) + timedelta(days=i), "6a", "5p") for i in range(5)]
        first = calc.summarize_hours(days, c, date(2026, 1, 1), date(2026, 1, 15))
        second = calc.summarize_hours(days, c, date(2026, 1, 16), date(2026, 1, 31))
        self.assertEqual((first.regular, first.overtime), (D("40.00"), D("4.00")))
        self.assertEqual((second.regular, second.overtime), (D("11.00"), D("0")))

    def test_entries_outside_range_ignored(self) -> None:
        s = calc.summarize_hours([entry(date(2026, 2, 1), "8", "5p")], self.company,
                                 date(2026, 1, 5), date(2026, 1, 11))
        self.assertEqual(s.total, D("0"))


class PayTests(unittest.TestCase):
    def test_salary_ignores_hours_and_overtime(self) -> None:
        c = Company(pay_period="Biweekly", overtime_enabled=True)
        emp = Employee("E002", "Bo", "Ray", pay_type="Salary", pay_rate=D("50000"))  # annual
        hours = HoursSummary(regular=D("40"), overtime=D("12"))
        rec = calc.compute_payroll(emp, hours, c, check_number=5, check_date=date(2026, 1, 9),
                                   period_start=date(2026, 1, 5), period_end=date(2026, 1, 18))
        self.assertEqual(rec.gross_pay, D("1923.08"))  # 50,000 / 26
        self.assertEqual(rec.overtime_pay, D("0.00"))
        self.assertEqual(rec.overtime_hours, D("12.00"))  # hours still tracked
        self.assertEqual(rec.employee_name, "Bo Ray")

    def test_salary_per_period_by_schedule(self) -> None:
        for period, expected in (("Weekly", "1000.00"), ("Biweekly", "2000.00"),
                                 ("Semimonthly", "2166.67"), ("Monthly", "4333.33")):
            self.assertEqual(calc.salary_per_period(D("52000"), Company(pay_period=period)), D(expected))

    def test_withholding_and_net_rounding(self) -> None:
        c = Company(federal_withholding_pct=10.0, state_withholding_pct=3.07,
                    social_security_pct=6.2, medicare_pct=1.45)
        emp = Employee("E001", "Ann", "Lee", pay_rate=D("15.37"))
        hours = HoursSummary(regular=D("37.25"))
        rec = calc.compute_payroll(emp, hours, c, check_number=7, check_date=date(2026, 1, 9),
                                   period_start=date(2026, 1, 5), period_end=date(2026, 1, 11),
                                   other_deductions=D("12.5"), memo="Week 2")
        self.assertEqual(rec.gross_pay, D("572.53"))     # 572.5325 -> .53
        self.assertEqual(rec.federal_tax, D("57.25"))    # 57.253
        self.assertEqual(rec.state_tax, D("17.58"))      # 17.5767
        self.assertEqual(rec.social_security, D("35.50"))  # 35.49686
        self.assertEqual(rec.medicare, D("8.30"))        # 8.301685
        self.assertEqual(rec.other_deductions, D("12.50"))
        self.assertEqual(rec.net_pay, D("441.40"))
        self.assertEqual(rec.memo, "Week 2")

    def test_half_up(self) -> None:
        self.assertEqual(calc.money(D("2.675")), D("2.68"))
        self.assertEqual(calc.money(2.675), D("2.68"))  # float goes through str()
        self.assertEqual(calc.money(D("0.005")), D("0.01"))
        self.assertEqual(calc.money(3), D("3.00"))


class WordsTests(unittest.TestCase):
    def test_amounts(self) -> None:
        self.assertEqual(calc.amount_to_words(D("0.05")), "Zero and 05/100")
        self.assertEqual(calc.amount_to_words(D("1234.56")),
                         "One Thousand Two Hundred Thirty-Four and 56/100")
        self.assertEqual(calc.amount_to_words(D("1000000.00")), "One Million and 00/100")
        self.assertEqual(calc.amount_to_words(D("20")), "Twenty and 00/100")
        self.assertEqual(calc.amount_to_words(D("115.10")), "One Hundred Fifteen and 10/100")
        self.assertEqual(calc.amount_to_words(D("2005019.99")),
                         "Two Million Five Thousand Nineteen and 99/100")

    def test_negative_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calc.amount_to_words(D("-1"))


class YtdTests(unittest.TestCase):
    def _rec(self, num: int, d: date, gross: str, net: str, voided: bool = False,
             emp: str = "E001") -> PayrollRecord:
        return PayrollRecord(num, d, d, d, emp, "x", regular_hours=D("40"), gross_pay=D(gross),
                             net_pay=D(net), medicare=D("1.00"), voided=voided)

    def test_excludes_voided_other_employees_and_later_checks(self) -> None:
        recs = [
            self._rec(1, date(2026, 1, 9), "100.00", "90.00"),
            self._rec(2, date(2026, 1, 16), "200.00", "180.00", voided=True),
            self._rec(3, date(2026, 1, 23), "300.00", "270.00"),
            self._rec(4, date(2026, 1, 23), "999.00", "999.00", emp="E002"),
            self._rec(5, date(2026, 2, 6), "50.00", "45.00"),
            self._rec(6, date(2025, 12, 26), "70.00", "60.00"),
        ]
        t = calc.ytd_totals(recs, "E001", date(2026, 1, 31))
        self.assertEqual(t.gross_pay, D("400.00"))
        self.assertEqual(t.net_pay, D("360.00"))
        self.assertEqual(t.medicare, D("2.00"))
        self.assertEqual(t.hours, D("80"))

    def test_hours_from_entries(self) -> None:
        entries = [entry(date(2026, 1, 5), "8", "4p"), entry(date(2026, 1, 6), "8", "12"),
                   entry(date(2026, 2, 6), "8", "12"), entry(date(2026, 1, 6), "8", "5p", emp="E002")]
        t = calc.ytd_totals([], "E001", date(2026, 1, 31), entries)
        self.assertEqual(t.hours, D("12.00"))


if __name__ == "__main__":
    unittest.main()
