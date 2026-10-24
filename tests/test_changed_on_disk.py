"""TimecardBook.changed_on_disk: autosave and Save must notice edits made outside the app."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import openpyxl

from timecard.models import Company, Employee
from timecard.storage import TimecardBook


class ChangedOnDiskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "book.xlsx"
        book = TimecardBook.new(Company(year=2026))
        book.upsert_employee(Employee("E001", "Ann", "Lee"))
        book.save(self.path)

    def test_unchanged_after_load_and_after_own_save(self) -> None:
        book = TimecardBook.load(self.path)
        self.assertFalse(book.changed_on_disk())
        book.upsert_employee(Employee("E002", "Bo", "Ray"))
        book.save()
        self.assertFalse(book.changed_on_disk())

    def test_detects_an_outside_edit(self) -> None:
        book = TimecardBook.load(self.path)
        time.sleep(0.02)
        wb = openpyxl.load_workbook(self.path)
        wb["Employees"]["B2"] = "Annie"   # edited in GridCraft / Excel
        wb.save(self.path)
        self.assertTrue(book.changed_on_disk())

    def test_detects_a_touch_with_same_size(self) -> None:
        book = TimecardBook.load(self.path)
        st = self.path.stat()
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.assertTrue(book.changed_on_disk())

    def test_new_or_missing_file_is_not_a_conflict(self) -> None:
        self.assertFalse(TimecardBook.new(Company()).changed_on_disk())
        book = TimecardBook.load(self.path)
        self.path.unlink()
        self.assertFalse(book.changed_on_disk())


if __name__ == "__main__":
    unittest.main()
