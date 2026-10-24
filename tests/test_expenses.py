"""Expenses: storage, receipt filing next to the workbook, filters, totals and category suggestions."""
from __future__ import annotations

import tempfile
import unittest
import unittest.mock
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


class DuplicateTests(unittest.TestCase):
    def test_same_amount_close_date_similar_vendor(self) -> None:
        items = [_exp(1, date(2026, 10, 3), "The Home Depot #4521", "Supplies", "84.17"),
                 _exp(2, date(2026, 10, 3), "Shell", "Car & truck", "40.00")]
        new = _exp(9, date(2026, 10, 5), "HOME DEPOT", "", "84.17")
        self.assertEqual([o.expense_id for o in expenses.possible_duplicates(new, items)], ["X0001"])
        self.assertEqual(expenses.possible_duplicates(_exp(9, date(2026, 10, 9), "Home Depot", "", "84.17"), items), [])
        self.assertEqual(expenses.possible_duplicates(_exp(9, date(2026, 10, 3), "Lowes", "", "84.17"), items), [])
        self.assertEqual(len(expenses.possible_duplicates(_exp(9, date(2026, 10, 3), "", "", "40.00"), items)), 1)
        self.assertEqual(expenses.possible_duplicates(items[0], items), [])   # never itself


class ReceiptFilingTests(unittest.TestCase):
    def test_receipt_is_copied_next_to_the_workbook_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wb = Path(d) / "Books" / "Acme 2026.xlsx"
            wb.parent.mkdir()
            photo = Path(d) / "IMG_0001.JPG"
            photo.write_bytes(b"jpeg")
            e = _exp(1, date(2026, 10, 24), 'Home "Depot"', "Supplies", "84.17")
            rel = expenses.file_receipt(photo, e, wb)
            self.assertEqual(rel, str(Path("Receipts") / "2026" / "Home Depot" / "2026-10-24 Home Depot 84.17.jpg"))
            self.assertEqual((wb.parent / rel).read_bytes(), b"jpeg")
            rel2 = expenses.file_receipt(photo, e, wb)
            self.assertTrue(rel2.endswith("84.17 (2).jpg"))
            e.receipt_file = rel
            self.assertEqual(expenses.receipt_path(e, wb), wb.parent / rel)
            with self.assertRaises(ValueError):
                expenses.file_receipt(Path(d) / "notes.txt", e, wb)

    def test_a_corrected_vendor_moves_the_receipt_to_its_folder(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wb = Path(d) / "Acme 2026.xlsx"
            photo = Path(d) / "scan.png"
            photo.write_bytes(b"png")
            e = _exp(1, date(2026, 10, 24), "", "", "0.00")   # the reader couldn't read it
            e.receipt_file = expenses.file_receipt(photo, e, wb)
            self.assertIn("Unknown vendor", e.receipt_file)
            e.vendor, e.amount = "Ace Hardware", D("42.10")      # corrected on the review screen
            e.receipt_file = expenses.refile_receipt(e, wb)
            self.assertEqual(e.receipt_file, str(Path("Receipts") / "2026" / "Ace Hardware" /
                                                 "2026-10-24 Ace Hardware 42.10.png"))
            self.assertEqual((Path(d) / e.receipt_file).read_bytes(), b"png")
            self.assertFalse((Path(d) / "Receipts" / "2026" / "Unknown vendor").exists())   # emptied folder removed
            self.assertEqual(expenses.refile_receipt(e, wb), e.receipt_file)                 # already in place
            outside = _exp(2, date(2026, 10, 24), "X", "", "1.00", receipt_file=str(photo))
            self.assertEqual(expenses.refile_receipt(outside, wb), str(photo))               # never moved

    def test_a_receipt_moved_by_a_failed_save_is_linked_again(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wb = Path(d) / "Acme 2026.xlsx"
            photo = Path(d) / "scan.png"
            photo.write_bytes(b"png")
            e = _exp(1, date(2026, 10, 21), "", "", "0.00")
            e.receipt_file = expenses.file_receipt(photo, e, wb)
            fixed = _exp(1, date(2026, 10, 21), "Shell", "", "40.00", receipt_file=e.receipt_file)
            target = expenses.receipt_folder(fixed, wb) / expenses.receipt_name(fixed, ".png")
            target.parent.mkdir(parents=True)
            (Path(d) / e.receipt_file).rename(target)   # moved, but the expense wasn't updated (1.3.0 bug)
            self.assertEqual(expenses.refile_receipt(fixed, wb), str(target.relative_to(Path(d))))

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
            bad = r.parent / "locked.jpg"
            bad.write_bytes(b"2")
            real_copy = backup.shutil.copy2
            with unittest.mock.patch.object(backup.shutil, "copy2",
                                            side_effect=lambda a, b: (_ for _ in ()).throw(PermissionError("locked"))
                                            if Path(a).name == "locked.jpg" else real_copy(a, b)):
                self.assertEqual(backup.backup_receipts(wb, dest), 0)   # skipped, not an error
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

    def test_pasted_rows_with_repeated_or_blank_ids_get_fresh_ids(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            b = TimecardBook.new(Company(year=2026))
            b.expenses.append(_exp(1, date(2026, 10, 3), "A", "Supplies", "1.00"))
            b.save(path)
            wb = openpyxl.load_workbook(path)
            ws = wb["Expenses"]
            ws.append(["X0001", date(2026, 10, 4), "B", "", "Supplies", 2.0])   # pasted copy, same id
            ws.append([None, date(2026, 10, 5), "C", "", "Supplies", 3.0])      # no id
            wb.save(path)
            ids = [e.expense_id for e in TimecardBook.load(path).expenses]
            self.assertEqual(len(ids), 3)
            self.assertEqual(len(set(ids)), 3)


if __name__ == "__main__":
    unittest.main()
