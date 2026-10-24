"""Tests for portable-mode paths and workbook backups."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from timecard import backup, paths, security


class PortablePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = Path(self._tmp.name) / "TimeBooks Portable"
        self.app.mkdir()
        self.appdata = Path(self._tmp.name) / "AppData"   # never the real %APPDATA%
        env = {k: v for k, v in os.environ.items() if k != paths.SETTINGS_ENV}
        env["APPDATA"] = str(self.appdata)
        for p in (mock.patch.dict(os.environ, env, clear=True), mock.patch.object(paths, "app_dir", lambda: self.app),
                  mock.patch.object(paths, "_migrated", False)):
            p.start()
            self.addCleanup(p.stop)

    def test_installed_mode_uses_appdata(self) -> None:
        self.assertFalse(paths.is_portable())
        self.assertEqual(paths.settings_dir(), self.appdata / "TimeBooks")
        self.assertEqual(paths.store_path(self.app / "x.xlsx"), str(self.app / "x.xlsx"))

    def test_timecard_pro_settings_are_copied_once_and_kept(self) -> None:
        old = self.appdata / "TimeCardPro"
        old.mkdir(parents=True)
        (old / "pin.json").write_text("pin")
        (old / "settings.json").write_text("{}")
        new = paths.settings_dir()
        self.assertEqual(new, self.appdata / "TimeBooks")
        self.assertEqual((new / "pin.json").read_text(), "pin")
        self.assertTrue((old / "pin.json").exists())   # the old folder is left as it was
        (new / "pin.json").write_text("changed in TimeBooks")
        self.assertFalse(paths.migrate_settings(old, new))   # never copied over again
        self.assertEqual((new / "pin.json").read_text(), "changed in TimeBooks")

    def test_an_interrupted_copy_is_finished_without_replacing_anything(self) -> None:
        old, new = self.appdata / "TimeCardPro", self.appdata / "TimeBooks"
        old.mkdir(parents=True)
        (old / "pin.json").write_text("pin")
        (old / "settings.json").write_text("old settings")
        new.mkdir()
        (new / "settings.json").write_text("copied before the interruption")   # no pin.json yet
        self.assertTrue(paths.migrate_settings(old, new))
        self.assertEqual((new / "pin.json").read_text(), "pin")
        self.assertEqual((new / "settings.json").read_text(), "copied before the interruption")
        self.assertFalse((self.appdata / "TimeBooks.copying").exists())

    def test_setup_reads_old_settings_without_copying_until_it_installs(self) -> None:
        old = self.appdata / "TimeCardPro"
        old.mkdir(parents=True)
        (old / "pin.json").write_text("pin")
        self.assertEqual(paths.current_settings_dir(), old)   # Setup sees the PIN where it is
        self.assertEqual(paths.installed_settings_dir(migrate=False), self.appdata / "TimeBooks")
        self.assertFalse((self.appdata / "TimeBooks").exists())   # opening Setup / uninstalling copies nothing
        paths.installed_settings_dir()   # installing
        self.assertEqual(paths.current_settings_dir(), self.appdata / "TimeBooks")

    def test_folders_the_owner_already_has_keep_their_old_names(self) -> None:
        parent = Path(self._tmp.name) / "Docs"
        self.assertEqual(paths.kept_folder(parent, "TimeBooks", "TimeCard Pro"), parent / "TimeBooks")
        (parent / "TimeCard Pro").mkdir(parents=True)
        self.assertEqual(paths.kept_folder(parent, "TimeBooks", "TimeCard Pro"), parent / "TimeCard Pro")
        (parent / "TimeBooks").mkdir()
        self.assertEqual(paths.kept_folder(parent, "TimeBooks", "TimeCard Pro"), parent / "TimeBooks")

    def test_portable_mode_keeps_everything_on_the_drive(self) -> None:
        (self.app / paths.PORTABLE_MARKER).write_text("portable")
        self.assertTrue(paths.is_portable())
        self.assertEqual(paths.settings_dir(), self.app / "Data" / "Settings")
        self.assertEqual(paths.documents_dir(), self.app / "Data" / "Workbooks")
        self.assertTrue((self.app / "Data" / "Workbooks").is_dir())
        security.set_pin("2468")  # the PIN lives on the drive too
        self.assertTrue((self.app / "Data" / "Settings" / "pin.json").is_file())

    def test_portable_paths_survive_a_drive_letter_change(self) -> None:
        (self.app / paths.PORTABLE_MARKER).write_text("portable")
        wb = self.app / "Data" / "Workbooks" / "Acme 2026.xlsx"
        stored = paths.store_path(wb)
        self.assertTrue(stored.startswith("portable:"))
        self.assertNotIn(os.path.splitdrive(str(self.app))[0], stored)
        self.assertEqual(Path(paths.resolve_path(stored)), wb.resolve())
        self.assertEqual(paths.resolve_path(r"C:\Other\file.xlsx"), r"C:\Other\file.xlsx")
        self.assertEqual(paths.resolve_path(""), "")

    def test_env_override_wins(self) -> None:
        (self.app / paths.PORTABLE_MARKER).write_text("portable")
        with mock.patch.dict(os.environ, {"TIMEBOOKS_SETTINGS_DIR": str(self.app / "elsewhere")}):
            self.assertEqual(paths.settings_dir(), self.app / "elsewhere")


class BackupTests(unittest.TestCase):
    def test_existing_timecard_pro_backups_folder_keeps_being_used(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "TimeCard Pro Backups").mkdir()
            self.assertEqual(backup.backup_folder(d, "Acme.xlsx"), Path(d) / "TimeCard Pro Backups" / "Acme")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.wb = root / "books" / "Acme Timecards 2026.xlsx"
        self.wb.parent.mkdir()
        self.wb.write_bytes(b"PK fake workbook")
        self.dest = root / "My Drive"
        self.dest.mkdir()

    def test_backup_copies_and_names_by_time(self) -> None:
        out = backup.backup_workbook(self.wb, self.dest, now=datetime(2026, 10, 23, 14, 5, 9))
        self.assertEqual(out, self.dest / "TimeBooks Backups" / "Acme Timecards 2026" /
                         "Acme Timecards 2026 2026-10-23 140509.xlsx")
        self.assertEqual(out.read_bytes(), b"PK fake workbook")
        self.assertEqual(list(out.parent.glob("*.tmp")), [])

    def test_keeps_only_newest_copies(self) -> None:
        start = datetime(2026, 1, 1)
        for i in range(7):
            backup.backup_workbook(self.wb, self.dest, keep=3, now=start + timedelta(days=i))
        names = [p.name for p in backup.list_backups(self.dest, self.wb)]
        self.assertEqual(names, [f"Acme Timecards 2026 2026-01-0{d} 000000.xlsx" for d in (7, 6, 5)])

    def test_pruning_ignores_other_files(self) -> None:
        folder = backup.backup_folder(self.dest, self.wb)
        folder.mkdir(parents=True)
        (folder / "notes.txt").write_text("keep me")
        for i in range(3):
            backup.backup_workbook(self.wb, self.dest, keep=1, now=datetime(2026, 1, 1 + i))
        self.assertTrue((folder / "notes.txt").is_file())
        self.assertEqual(len(backup.list_backups(self.dest, self.wb)), 1)

    def test_errors_when_unavailable(self) -> None:
        with self.assertRaises(OSError):
            backup.backup_workbook(self.wb, self.dest / "missing drive")
        with self.assertRaises(FileNotFoundError):
            backup.backup_workbook(self.wb.with_name("unsaved.xlsx"), self.dest)

    def test_google_drive_detection_finds_home_folder(self) -> None:
        home = Path(self._tmp.name) / "home"
        (home / "Google Drive" / "My Drive").mkdir(parents=True)
        with mock.patch.object(Path, "home", lambda: home):
            found = backup.google_drive_folders()
        self.assertIn(home / "Google Drive" / "My Drive", found)


if __name__ == "__main__":
    unittest.main()
