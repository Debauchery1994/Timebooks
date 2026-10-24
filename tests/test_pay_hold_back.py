"""Tests for Company.pay_hold_back (Company Settings > Pay timing)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from timecard.models import Company
from timecard.storage import TimecardBook


class PayHoldBackTests(unittest.TestCase):
    def test_default_is_no_hold_back(self) -> None:
        self.assertEqual(Company().pay_hold_back, "none")

    def test_round_trip_and_older_workbooks(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "book.xlsx"
            book = TimecardBook.new(Company(year=2026, pay_hold_back="one"))
            book.save(path)
            self.assertEqual(TimecardBook.load(path).company.pay_hold_back, "one")
            # a workbook saved before the setting existed has no row for it: no hold-back
            import openpyxl
            wb = openpyxl.load_workbook(path)
            ws = wb["Company"]
            for row in ws.iter_rows(min_row=2):
                if row[0].value == "pay_hold_back":
                    ws.delete_rows(row[0].row)
                    break
            wb.save(path)
            self.assertEqual(TimecardBook.load(path).company.pay_hold_back, "none")


if __name__ == "__main__":
    unittest.main()
