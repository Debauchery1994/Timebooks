"""Expenses: storage, receipt filing next to the workbook, filters, totals and category suggestions."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl

from timecard import backup, expenses
from timecard.models import Company, Expense
from timecard.storage import TimecardBook

D = Decimal


def _exp(i: int, d: date, vendor: str, cat: str, amt: str, **kw) -> Expense:
    return Expense(f"X{i:04d}", d, vendor=vendor, category=cat, amount=D(amt), **kw)


class ExpenseRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [_exp(1, date(2026, 10, 3), "Home Depot", "Supplies", "84.17", tax=D("5.17")),
                      _exp(2, date(2026, 10, 9), "Shell", "Car & truck", "40.00"),
                      _exp(3, date(2026, 11, 2), "Home Depot", "Repairs & maintenance", "12.50"),
                      _exp(4, date(2026, 11, 5), "Unknown", "Supplies", "99.99", status="Draft")]

    def test_ids_and_filters(self) -> None:
        self.assertEqual(expenses.next_expense_id(self.items), "X0005")
        self.assertEqual(expenses.next_expense_id([]), "X0001")
        self.assertEqual([e.expense_id for e in self.items if expenses.matches(e, month=10)], ["X0001", "X0002"])
        self.assertEqual([e.expense_id for e in self.items if expenses.matches(e, vendor="depot")], ["X0001", "X0003"])
        self.assertEqual([e.expense_id for e in self.items if expenses.matches(e, category="Supplies")],
                         ["X0001", "X0004"])

    def test_totals_leave_out_drafts(self) -> None:
        self.assertEqual(expenses.totals_by_category(self.items),
                         [("Supplies", D("84.17")), ("Car & truck", D("40.00")), ("Repairs & maintenance", D("12.50"))])

    def test_next_receipt_from_a_vendor_suggests_its_last_category(self) -> None:
        self.assertEqual(expenses.last_category_for(self.items, "home depot"), "Repairs & maintenance")
        self.assertEqual(expenses.last_category_for(self.items, "Lowes"), "")
        self.assertEqual(expenses.vendors(self.items)[0], "Home Depot")


class ReceiptFilingTests(unittest.TestCase):
    def test_receipt_is_copied_next_to_the_workbook_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wb = Path(d) / "Books" / "Acme 2026.xlsx"
            wb.parent.mkdir()
            photo = Path(d) / "IMG_0001.JPG"
            photo.write_bytes(b"jpeg")
            e = _exp(1, date(2026, 10, 24), 'Home "Depot"', "Supplies", "84.17")
            rel = expenses.file_receipt(photo, e, wb)
            self.assertEqual(rel, str(Path("Receipts") / "2026" / "2026-10-24 Home Depot 84.17.jpg"))
            self.assertEqual((wb.parent / rel).read_bytes(), b"jpeg")
            rel2 = expenses.file_receipt(photo, e, wb)
            self.assertTrue(rel2.endswith("84.17 (2).jpg"))
            e.receipt_file = rel
            self.assertEqual(expenses.receipt_path(e, wb), wb.parent / rel)
            with self.assertRaises(ValueError):
                expenses.file_receipt(Path(d) / "notes.txt", e, wb)

    def test_backups_copy_new_receipts_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wb = Path(d) / "Acme 2026.xlsx"
            wb.write_bytes(b"x")
            r = Path(d) / "Receipts" / "2026" / "a.jpg"
            r.parent.mkdir(parents=True)
            r.write_bytes(b"1")
            dest = Path(d) / "Drive"
            self.assertEqual(backup.backup_receipts(wb, dest), 1)
            self.assertEqual(backup.backup_receipts(wb, dest), 0)   # nothing new
            copied = backup.backup_root(dest) / "Acme 2026 Receipts" / "2026" / "a.jpg"
            self.assertEqual(copied.read_bytes(), b"1")


class ExpenseStorageTests(unittest.TestCase):
    def test_round_trip_and_older_workbooks(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            b = TimecardBook.new(Company(year=2026))
            b.expenses.append(_exp(1, date(2026, 10, 3), "Home Depot", "Supplies", "84.17", tax=D("5.17"),
                                   payment_method="Check", check_number=1042, receipt_file="Receipts\\2026\\a.jpg"))
            b.categories.append("Software")
            b.save(path)
            got = TimecardBook.load(path)
            e = got.expenses[0]
            self.assertEqual((e.vendor, e.amount, e.tax, e.check_number, e.receipt_file, e.status),
                             ("Home Depot", D("84.17"), D("5.17"), 1042, "Receipts\\2026\\a.jpg", "Approved"))
            self.assertIn("Software", got.categories)

            wb = openpyxl.load_workbook(path)   # a 1.2.0 workbook has neither sheet
            del wb["Expenses"]
            del wb["Categories"]
            wb.save(path)
            old = TimecardBook.load(path)
            self.assertEqual(old.expenses, [])
            self.assertEqual(old.categories, list(expenses.DEFAULT_CATEGORIES))


if __name__ == "__main__":
    unittest.main()
