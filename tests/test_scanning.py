"""Getting receipts in: draft expenses from what the reader found, the scanner folder, and the scanner dialog."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from timecard import receipts_ocr, scanning
from timecard.models import Expense

RECEIPT = """HOME DEPOT #4521
123 MAIN ST
10/24/2026 11:42 AM
HAMMER 16OZ        12.99
WOOD SCREWS         6.49
SUBTOTAL           19.48
TAX                 1.27
TOTAL              20.75
VISA ****1234      20.75"""


class DraftFromReceiptTests(unittest.TestCase):
    def test_fields_fill_a_draft_and_the_vendor_suggests_its_last_category(self) -> None:
        fields = receipts_ocr.extract_fields(RECEIPT, known_vendors=["Home Depot"])
        past = [Expense("X0001", date(2026, 9, 1), vendor="Home Depot", category="Repairs & maintenance")]
        e = scanning.expense_from_receipt(fields, "X0002", past)
        self.assertEqual((e.expense_id, e.status, e.expense_date), ("X0002", "Draft", date(2026, 10, 24)))
        self.assertEqual((e.amount, e.tax, e.payment_method), (Decimal("20.75"), Decimal("1.27"), "Card"))
        self.assertEqual(e.category, "Repairs & maintenance")
        self.assertIn("HAMMER", e.memo.upper())

    def test_nothing_read_leaves_blanks_for_the_review_screen(self) -> None:
        e = scanning.expense_from_receipt(receipts_ocr.extract_fields(""), "X0001", [], today=date(2026, 1, 2))
        self.assertEqual((e.vendor, e.amount, e.category, e.expense_date), ("", Decimal("0"), "", date(2026, 1, 2)))


class ScannerFolderTests(unittest.TestCase):
    def test_only_new_receipt_files_are_offered_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            for i, name in enumerate(("b.pdf", "a.jpg", "notes.txt")):
                p = folder / name
                p.write_bytes(b"x" * (i + 1))
                os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
            found = scanning.new_scans(folder, set())
            self.assertEqual([p.name for p in found], ["b.pdf", "a.jpg"])
            self.assertEqual(scanning.new_scans(folder, {scanning.scan_key(found[0])}), [found[1]])
            self.assertEqual(scanning.new_scans(folder / "missing", set()), [])


class ScannerDialogTests(unittest.TestCase):
    def _run(self, code: int, make_file: bool = False):
        def fake(cmd, **kw):
            if make_file:
                Path(cmd[-1]).write_bytes(b"png")
            return mock.Mock(returncode=code, stderr="")
        return mock.patch.object(scanning.subprocess, "run", side_effect=fake)

    def test_cancel_no_scanner_and_success(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with self._run(3):
                self.assertIsNone(scanning.acquire_from_scanner(d))
            with self._run(4), self.assertRaises(scanning.ScanError) as ctx:
                scanning.acquire_from_scanner(d)
            self.assertIn("No scanner", str(ctx.exception))
            with self._run(0, make_file=True):
                out = scanning.acquire_from_scanner(d)
            self.assertTrue(out.is_file() and out.suffix == ".png")


if __name__ == "__main__":
    unittest.main()
