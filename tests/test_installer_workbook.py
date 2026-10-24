"""Tests for the default workbook folder (Documents\\TimeCard Pro) and the workbook Setup creates."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import installer  # noqa: E402

from timecard import paths  # noqa: E402
from timecard.storage import TimecardBook  # noqa: E402

YEAR = date.today().year


class DefaultFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.docs = self.root / "OneDrive" / "Documents"
        self.docs.mkdir(parents=True)
        self.app = self.root / "Programs" / "TimeCardPro"
        self.app.mkdir(parents=True)
        for p in (mock.patch.object(paths, "windows_documents", lambda: self.docs),
                  mock.patch.object(paths, "app_dir", lambda: self.app)):
            p.start()
            self.addCleanup(p.stop)

    def test_installed_default_is_created_inside_real_documents(self) -> None:
        d = paths.documents_dir()
        self.assertEqual(d, self.docs / "TimeCard Pro")
        self.assertTrue(d.is_dir())

    def test_falls_back_to_home_documents(self) -> None:
        with mock.patch.object(paths, "windows_documents", lambda: None), \
                mock.patch.object(Path, "home", lambda: self.root):
            self.assertEqual(paths.default_workbook_dir(), self.root / "Documents" / "TimeCard Pro")

    def test_preview_does_not_create_the_folder(self) -> None:
        self.assertEqual(paths.default_workbook_dir(), self.docs / "TimeCard Pro")
        self.assertFalse((self.docs / "TimeCard Pro").exists())
        self.assertEqual(paths.default_workbook_dir(self.app, portable=True), self.app / "Data" / "Workbooks")

    def test_workbook_filename_is_safe(self) -> None:
        self.assertEqual(paths.workbook_filename("Acme LLC", 2026), "Acme LLC Timecards 2026.xlsx")
        self.assertEqual(paths.workbook_filename(' Acme: "Widgets"/Co ', 2026), "Acme- -Widgets-Co Timecards 2026.xlsx")

    def test_real_documents_lookup_returns_a_folder(self) -> None:
        mock.patch.stopall()
        found = paths.windows_documents()
        if sys.platform == "win32":
            self.assertTrue(found and found.is_dir())


class SetupWorkbookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.docs = self.root / "Documents"
        self.docs.mkdir()
        self.settings = self.root / "AppData" / "TimeCardPro"
        self.app = self.root / "Programs" / "TimeCardPro"
        self.app.mkdir(parents=True)
        p = mock.patch.object(paths, "windows_documents", lambda: self.docs)
        p.start()
        self.addCleanup(p.stop)

    def settings_data(self) -> dict:
        return json.loads((self.settings / "settings.json").read_text(encoding="utf-8"))

    def test_creates_workbook_and_app_opens_it_first(self) -> None:
        wb = installer.create_workbook("Acme LLC", self.settings, self.app)
        self.assertEqual(wb, self.docs / "TimeCard Pro" / f"Acme LLC Timecards {YEAR}.xlsx")
        book = TimecardBook.load(wb)
        self.assertEqual(book.company.company_name, "Acme LLC")
        self.assertEqual(book.company.year, YEAR)
        data = self.settings_data()
        self.assertEqual(data["last_workbook"], str(wb))
        self.assertEqual(data["recent"][0], str(wb))
        self.assertEqual(installer.existing_workbook(self.settings, self.app), wb)

    def test_what_the_business_needs_sets_the_features(self) -> None:
        book = TimecardBook.load(installer.create_workbook("Acme LLC", self.settings, self.app))
        self.assertEqual((book.company.use_timecards, book.company.use_payroll), (True, True))
        wb = installer.create_workbook("Salary Co", self.settings, self.app, use_timecards=False)
        book = TimecardBook.load(wb)
        self.assertEqual((book.company.use_timecards, book.company.use_payroll), (False, True))

    def test_never_overwrites_an_existing_workbook(self) -> None:
        wb = installer.create_workbook("Acme LLC", self.settings, self.app)
        book = TimecardBook.load(wb)
        book.company = book.company.__class__(**{**book.company.__dict__, "phone": "555-0100"})
        book.save(wb)
        installer.create_workbook("Acme LLC", self.settings, self.app)
        self.assertEqual(TimecardBook.load(wb).company.phone, "555-0100")

    def test_keeps_other_settings_and_recent_files(self) -> None:
        self.settings.mkdir(parents=True)
        (self.settings / "settings.json").write_text(json.dumps(
            {"theme": "Dark", "recent": [r"C:\old\Other 2025.xlsx"]}), encoding="utf-8")
        wb = installer.create_workbook("Acme LLC", self.settings, self.app)
        data = self.settings_data()
        self.assertEqual(data["theme"], "Dark")
        self.assertEqual(data["recent"], [str(wb), r"C:\old\Other 2025.xlsx"])

    def test_existing_workbook_ignores_missing_files_and_bad_settings(self) -> None:
        self.assertIsNone(installer.existing_workbook(self.settings, self.app))
        self.settings.mkdir(parents=True)
        f = self.settings / "settings.json"
        f.write_text(json.dumps({"last_workbook": str(self.root / "gone.xlsx")}), encoding="utf-8")
        self.assertIsNone(installer.existing_workbook(self.settings, self.app))
        f.write_text("not json", encoding="utf-8")
        self.assertIsNone(installer.existing_workbook(self.settings, self.app))

    def test_portable_copy_keeps_workbook_on_the_drive(self) -> None:
        target = self.root / "USB" / paths.PORTABLE_FOLDER
        target.mkdir(parents=True)
        (target / paths.PORTABLE_MARKER).write_text("portable")
        settings = paths.portable_data_dir(target) / "Settings"
        wb = installer.create_workbook("Acme LLC", settings, target)
        self.assertEqual(wb, target / "Data" / "Workbooks" / f"Acme LLC Timecards {YEAR}.xlsx")
        stored = json.loads((settings / "settings.json").read_text(encoding="utf-8"))["last_workbook"]
        self.assertTrue(stored.startswith("portable:"))   # survives a drive-letter change
        self.assertEqual(installer.existing_workbook(settings, target), wb.resolve())
        self.assertFalse((self.docs / "TimeCard Pro").exists())   # nothing written to the computer


if __name__ == "__main__":
    unittest.main()
