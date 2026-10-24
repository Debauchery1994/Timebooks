"""Company Settings > Locked by PIN: which tabs ask for the PIN, saved in the workbook."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import openpyxl

from timecard.models import Company
from timecard.storage import TimecardBook

LOCK_KEYS = ("lock_timecard", "lock_employees", "lock_yearlog", "lock_payroll")


class PinLockSettingsTests(unittest.TestCase):
    def test_defaults_lock_only_employees(self) -> None:
        c = Company()
        self.assertEqual([getattr(c, k) for k in LOCK_KEYS], [False, True, False, False])

    def test_round_trip_and_older_workbooks_keep_employees_locked(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            TimecardBook.new(Company(year=2026, lock_timecard=True, lock_employees=False)).save(path)
            c = TimecardBook.load(path).company
            self.assertEqual([getattr(c, k) for k in LOCK_KEYS], [True, False, False, False])

            wb = openpyxl.load_workbook(path)   # a workbook from before Locked by PIN existed
            ws = wb["Company"]
            for row in reversed(list(ws.iter_rows(min_row=2))):
                if row[0].value in LOCK_KEYS:
                    ws.delete_rows(row[0].row)
            wb.save(path)
            c = TimecardBook.load(path).company
            self.assertEqual([getattr(c, k) for k in LOCK_KEYS], [False, True, False, False])


if __name__ == "__main__":
    unittest.main()
