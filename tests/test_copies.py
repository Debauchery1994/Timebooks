"""1.5: PC and USB copies of a workbook: which one has the newer work."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from timecard import copies, paths
from timecard.models import Company
from timecard.storage import TimecardBook


class CompareTests(unittest.TestCase):
    def test_compare(self) -> None:
        self.assertEqual(copies.compare(["c", "b", "a"], ["c", "b", "a"]), "same")
        self.assertEqual(copies.compare(["b", "a"], ["c", "b", "a"]), "other_newer")
        self.assertEqual(copies.compare(["c", "b", "a"], ["b", "a"]), "this_newer")
        self.assertEqual(copies.compare(["x", "b", "a"], ["y", "b", "a"]), "split")


class CopiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pc = root / "Documents" / "TimeBooks"
        self.usb = root / "E"
        self.usb_wb = self.usb / paths.PORTABLE_FOLDER / "Data" / "Workbooks"
        self.pc.mkdir(parents=True)
        self.usb_wb.mkdir(parents=True)
        self.pc_file = self.pc / "Shop Timecards 2026.xlsx"
        self.usb_file = self.usb_wb / self.pc_file.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _others(self, path: Path):
        book = TimecardBook.load(path)
        return copies.other_copies(path, book.company, drives=[self.usb], pc_folder=self.pc)

    def test_usb_copy_saved_later_is_found_from_the_pc(self) -> None:
        b = TimecardBook.new(Company(company_name="Shop", year=2026))
        b.save(self.pc_file)
        shutil.copy2(self.pc_file, self.usb_file)          # taken to the shop on the USB drive
        self.assertEqual(self._others(self.pc_file), [])   # same so far
        u = TimecardBook.load(self.usb_file)
        u.company.phone = "555-0100"
        u.save()                                           # worked on at the shop
        found = self._others(self.pc_file)
        self.assertEqual([(c.path, c.status) for c in found], [(self.usb_file, "other_newer")])
        self.assertTrue(found[0].saved_at)
        self.assertEqual(self._others(self.usb_file)[0].status, "this_newer")
        p = TimecardBook.load(self.pc_file)
        p.company.address1 = "1 Main St"
        p.save()                                           # and on the PC too: both changed
        self.assertEqual(self._others(self.pc_file)[0].status, "split")

    def test_other_workbooks_and_new_years_are_not_copies(self) -> None:
        a = TimecardBook.new(Company(company_name="Shop", year=2026))
        a.save(self.pc_file)
        other = TimecardBook.new(Company(company_name="Shop", year=2026))
        other.save(self.usb_file)                          # same name, different workbook
        other.save()
        self.assertEqual(self._others(self.pc_file), [])
        self.assertNotEqual(a.company.workbook_id, other.company.workbook_id)
