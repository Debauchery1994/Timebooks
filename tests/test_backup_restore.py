"""1.5: encrypted backups (workbook and receipts) and reading backups back for a restore."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from timecard import backup
from timecard.models import Company
from timecard.storage import TimecardBook


class EncryptedBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.wb = root / "PC" / "Shop Timecards 2026.xlsx"
        self.wb.parent.mkdir()
        TimecardBook.new(Company(company_name="Shop", year=2026)).save(self.wb)
        (self.wb.parent / "Receipts" / "2026").mkdir(parents=True)
        (self.wb.parent / "Receipts" / "2026" / "napa.jpg").write_bytes(b"\xff\xd8 receipt picture")
        self.cloud = root / "Cloud"
        self.cloud.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_encrypted_copies_open_only_with_the_password(self) -> None:
        key = backup.make_key("correct horse")
        copy = backup.backup_workbook(self.wb, self.cloud, key=key, now=datetime(2026, 11, 1, 9, 30))
        self.assertEqual(copy.suffix, ".tbk")
        self.assertTrue(backup.is_encrypted(copy))
        self.assertNotIn(b"Shop", copy.read_bytes())                  # nothing readable in the cloud copy
        self.assertEqual(backup.restore_bytes(copy, "correct horse"), self.wb.read_bytes())
        self.assertEqual(backup.restore_bytes(copy, key=key), self.wb.read_bytes())   # the saved key
        with self.assertRaises(backup.BackupPasswordError):
            backup.restore_bytes(copy, "wrong password")
        with self.assertRaises(backup.BackupPasswordError):
            backup.restore_bytes(copy)                                  # no password at all
        self.assertEqual(backup.backup_receipts(self.wb, self.cloud, key), 1)
        self.assertEqual(backup.backup_receipts(self.wb, self.cloud, key), 0)   # already there
        enc = next(backup.backup_root(self.cloud).rglob("napa.jpg.tbk"))
        self.assertNotIn(b"receipt picture", enc.read_bytes())

    def test_listing_and_restoring_receipts(self) -> None:
        key = backup.make_key("correct horse")
        plain = backup.backup_workbook(self.wb, self.cloud, now=datetime(2026, 10, 1, 8, 0))
        enc = backup.backup_workbook(self.wb, self.cloud, key=key, now=datetime(2026, 11, 1, 9, 30))
        self.assertEqual(backup.list_backups(self.cloud, self.wb), [enc, plain])   # both kinds, newest first
        self.assertEqual(backup.all_backups(self.cloud), [enc, plain])
        self.assertEqual(backup.backup_stem(enc), "Shop Timecards 2026")
        self.assertEqual(backup.restore_bytes(plain), self.wb.read_bytes())
        again = backup.backup_workbook(self.wb, self.cloud, key=key, now=datetime(2026, 11, 1, 9, 30))
        self.assertNotEqual(again, enc)                      # same second: a new name, the first copy is kept
        self.assertTrue(enc.is_file())
        again.unlink()
        backup.backup_receipts(self.wb, self.cloud, key)
        new_pc = Path(self.tmp.name) / "NewPC" / "Shop Timecards 2026.xlsx"   # the PC was replaced
        new_pc.parent.mkdir()
        root = backup.backup_root(self.cloud)
        self.assertEqual(backup.restore_receipts(root, "Shop Timecards 2026", new_pc, "wrong"), (0, 1))
        self.assertEqual(backup.restore_receipts(root, "Shop Timecards 2026", new_pc, "correct horse"), (1, 0))
        self.assertEqual((new_pc.parent / "Receipts" / "2026" / "napa.jpg").read_bytes(), b"\xff\xd8 receipt picture")
        self.assertEqual(backup.restore_receipts(root, "Shop Timecards 2026", new_pc, "correct horse"), (0, 0))
