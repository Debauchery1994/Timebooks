"""Tests for the installer's portable (USB drive) mode, using a small fake payload."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import installer  # noqa: E402

from timecard import paths, security  # noqa: E402


class PortableInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.drive = self.root / "USB"
        self.drive.mkdir()
        payload_dir = self.root / "res"
        payload_dir.mkdir()
        with zipfile.ZipFile(payload_dir / "payload.zip", "w") as z:
            z.writestr("TimeCardPro.exe", "new exe")
            z.writestr("_internal/lib.dll", "new lib")
        for p in (mock.patch.object(installer, "resource", lambda name: payload_dir / name),
                  mock.patch.dict(os.environ, {"TIMECARDPRO_SETTINGS_DIR": str(self.root / "unused")})):
            p.start()
            self.addCleanup(p.stop)

    def test_target_is_its_own_subfolder(self) -> None:
        self.assertEqual(installer.portable_target("E:\\"), Path("E:\\") / "TimeCard Pro Portable")
        self.assertEqual(installer.portable_target("E:\\TimeCard Pro Portable"), Path("E:\\TimeCard Pro Portable"))

    def test_creates_portable_copy_with_data_folders(self) -> None:
        target = installer.portable_target(self.drive)
        exe = installer.install_portable(target)
        self.assertEqual(exe, target / "TimeCardPro.exe")
        self.assertTrue(paths.is_portable(target))
        self.assertTrue((target / "Data" / "Workbooks").is_dir())
        self.assertTrue((target / "Data" / "Settings").is_dir())
        self.assertEqual((target / "_internal" / "lib.dll").read_text(), "new lib")

    def test_update_keeps_workbooks_and_pin(self) -> None:
        target = installer.portable_target(self.drive)
        installer.install_portable(target)
        wb = target / "Data" / "Workbooks" / "Acme 2026.xlsx"
        wb.write_text("my data")
        installer.use_settings_dir(target / "Data" / "Settings")
        security.set_pin("1357")
        (target / "_internal" / "stale.dll").write_text("old")
        installer.install_portable(target)  # e.g. updating to a newer version
        self.assertEqual(wb.read_text(), "my data")
        self.assertTrue(security.verify_pin("1357"))
        self.assertFalse((target / "_internal" / "stale.dll").exists())

    def test_refuses_folder_with_other_files(self) -> None:
        target = installer.portable_target(self.drive)
        target.mkdir()
        (target / "someone else's file.txt").write_text("x")
        self.assertIn("already contains other files", installer.portable_problem(target))
        with self.assertRaises(ValueError):
            installer.install_portable(target)

    def test_missing_drive_is_reported(self) -> None:
        problem = installer.portable_problem(self.root / "Z-not-plugged-in" / "TimeCard Pro Portable")
        self.assertIn("not available", problem)


if __name__ == "__main__":
    unittest.main()
