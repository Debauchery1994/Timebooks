"""Tests for calc.period_log: the Year Log's pay period x employee rows."""
from __future__ import annotations

import unittest
from datetime import date, time
from decimal import Decimal

from timecard import calc
from timecard.models import Company, TimeEntry

D = Decimal


def day(d: date, eid: str = "E001", start: int = 8, end: int = 16, lunch: int = 0, notes: str = "") -> TimeEntry:
    return TimeEntry(d, eid, time(start), time(end), lunch_deduct_min=lunch, notes=notes)


class PeriodLogTests(unittest.TestCase):
    def test_groups_by_pay_period_and_employee(self) -> None:
        c = Company(pay_period="Weekly", week_start="Monday", year=2026)
        entries = [day(date(2026, 10, 19)), day(date(2026, 10, 20), lunch=30),
                   day(date(2026, 10, 19), "E002"), day(date(2026, 10, 27))]
        groups = calc.period_log(entries, c)
        self.assertEqual([(g.start, g.employee_id, len(g.entries)) for g in groups],
                         [(date(2026, 10, 19), "E001", 2), (date(2026, 10, 19), "E002", 1),
                          (date(2026, 10, 26), "E001", 1)])
        first = groups[0]
        self.assertEqual(first.end, date(2026, 10, 25))
        self.assertEqual(first.hours, D("15.50"))      # 8 + (8 - 0.5)
        self.assertEqual(first.lunch_minutes, 30)

    def test_notes_from_the_whole_period_are_combined_with_their_days(self) -> None:
        c = Company(pay_period="Weekly", week_start="Monday", year=2026)
        g = calc.period_log([day(date(2026, 10, 22), notes="left early "), day(date(2026, 10, 20), notes="tile run"),
                             day(date(2026, 10, 21))], c)[0]
        self.assertEqual(g.notes_text(), "Tue 10/20: tile run; Thu 10/22: left early")

    def test_biweekly_period_spans_two_weeks(self) -> None:
        c = Company(pay_period="Biweekly", week_start="Monday", year=2026)
        start, end = calc.period_for(date(2026, 10, 19), c)
        groups = calc.period_log([day(start), day(end)], c)
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0].start, groups[0].end), (start, end))

    def test_empty(self) -> None:
        self.assertEqual(calc.period_log([], Company()), [])
        g = calc.PeriodLog(date(2026, 1, 5), date(2026, 1, 11), "E001")
        self.assertEqual((g.hours, g.lunch_minutes, g.notes_text()), (D("0"), 0, ""))


if __name__ == "__main__":
    unittest.main()
