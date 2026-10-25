"""1.4: vendors, unknown vendors, accounts, vendor payments (shared check numbers, printing), 1099 totals, positive pay."""
from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import expenses, vendor_payments as vp, vendors
from timecard.models import Account, Company, Expense, PayrollRecord, Vendor
from timecard.storage import TimecardBook

D = Decimal


def _book(**company) -> TimecardBook:
    b = TimecardBook.new(Company(year=2026, next_check_number=1001, **company))
    b.vendors["V0001"] = Vendor("V0001", "Ace Hardware", "1 Main St", "Town, ST", default_category="Supplies",
                                is_1099=True)
    b.accounts["A01"] = Account("A01", "Business checking", last4="1234")
    return b


def _payroll(num: int, d: date, name: str = "Ann") -> PayrollRecord:
    return PayrollRecord(num, d, date(2026, 9, 21), date(2026, 9, 27), "E1", name, net_pay=D("500"),
                         gross_pay=D("500"))


class VendorTests(unittest.TestCase):
    def test_unknown_vendors_are_assigned_and_receipts_move(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            b = _book()
            b.path = Path(d) / "b.xlsx"
            photo = Path(d) / "scan.png"
            photo.write_bytes(b"png")
            e = Expense("X0001", date(2026, 10, 2), amount=D("12.00"), status="Draft")
            e.receipt_file = expenses.file_receipt(photo, e, b.path)
            b.expenses += [e, Expense("X0002", date(2026, 10, 3), vendor="ACE HARDWARE", amount=D("5"))]
            self.assertEqual([x.expense_id for x in vendors.unknown_vendor_expenses(b)], ["X0001"])
            self.assertEqual(vendors.link_same_name(b, b.vendors["V0001"]), 1)
            self.assertEqual(vendors.assign_vendor(b, [e], b.vendors["V0001"]), [])
            self.assertEqual((e.vendor, e.vendor_id, e.category), ("Ace Hardware", "V0001", "Supplies"))
            self.assertIn("Ace Hardware", e.receipt_file)
            self.assertTrue((Path(d) / e.receipt_file).is_file())
            self.assertEqual(vendors.next_vendor_id(b.vendors), "V0002")


class VendorPaymentTests(unittest.TestCase):
    def test_shared_check_numbers_bills_and_void(self) -> None:
        b = _book()
        b.record_payroll([_payroll(1001, date(2026, 10, 1))])
        bill = Expense("X0001", date(2026, 10, 2), vendor="Ace Hardware", amount=D("84.17"), payment_method="Check")
        b.expenses.append(bill)
        self.assertEqual(vp.unpaid_bills(b, b.vendors["V0001"]), [bill])
        p = vp.record_payment(b, b.vendors["V0001"], D("84.17"), date(2026, 10, 5), bills=[bill], account_id="A01")
        self.assertEqual(p.check_number, 1002)                # 1001 was taken by payroll
        self.assertEqual((bill.check_number, bill.vendor_id), (1002, "V0001"))
        self.assertEqual(b.company.next_check_number, 1003)
        with self.assertRaises(ValueError):
            vp.record_payment(b, b.vendors["V0001"], D("10"), date(2026, 10, 5), bills=[bill])   # wrong total
        q = vp.record_payment(b, b.vendors["V0001"], D("40.00"), date(2026, 10, 6), memo="Paint")
        made = [e for e in b.expenses if e.check_number == q.check_number]
        self.assertEqual((len(made), made[0].amount, made[0].category), (1, D("40.00"), "Supplies"))
        vp.void_payment(b, q)
        self.assertTrue(q.voided)
        self.assertFalse(any(e.check_number == q.check_number for e in b.expenses))   # its own expense removed
        vp.void_payment(b, p)
        self.assertIsNone(bill.check_number)                                          # the bill is unpaid again
        self.assertEqual(vp.next_check_number(b), 1004)                               # numbers are never reused

    def test_prints_on_every_layout(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for layout in ("voucher", "laser_3up", "manual_3up", "handwrite"):
                b = _book(check_layout=layout)
                bill = Expense("X0001", date(2026, 10, 2), vendor="Ace Hardware", amount=D("84.17"),
                               payment_method="Check", memo="Lumber")
                b.expenses.append(bill)
                p = vp.record_payment(b, b.vendors["V0001"], D("84.17"), date(2026, 10, 5), bills=[bill])
                out = vp.render_vendor_checks([p], b, Path(d) / f"{layout}.pdf")
                self.assertGreater(out.stat().st_size, 1000, layout)
            reg = vp.render_register(b, Path(d) / "register.pdf", date(2026, 1, 1), date(2026, 12, 31))
            self.assertGreater(reg.stat().st_size, 1000)


class AccountAnd1099Tests(unittest.TestCase):
    def test_account_totals_dont_count_a_check_twice(self) -> None:
        b = _book(checks_account_id="A01")
        b.expenses.append(Expense("X0001", date(2026, 3, 1), vendor="Shell", amount=D("40"), account_id="A02"))
        bill = Expense("X0002", date(2026, 3, 2), vendor="Ace Hardware", amount=D("100"), payment_method="Check")
        b.expenses.append(bill)
        vp.record_payment(b, b.vendors["V0001"], D("100"), date(2026, 3, 3), bills=[bill])
        t = vendors.account_totals(b, 2026)
        self.assertEqual((t["A02"].money_out, t["A01"].checks, t["A01"].expenses), (D("40"), D("100"), D("0")))

    def test_vendor_1099_totals_leave_out_cards(self) -> None:
        b = _book()
        b.expenses += [Expense("X0001", date(2026, 2, 1), vendor="Ace Hardware", amount=D("700"),
                               payment_method="Cash"),
                       Expense("X0002", date(2026, 2, 2), vendor="Ace Hardware", amount=D("300"),
                               payment_method="Card")]
        vp.record_payment(b, b.vendors["V0001"], D("1500"), date(2026, 2, 3))
        self.assertEqual(vendors.vendor_1099_totals(b, 2026), [(b.vendors["V0001"], D("2200"))])


class PositivePayTests(unittest.TestCase):
    def test_file_has_both_kinds_of_check(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            b = _book(account_number="000123456789")
            b.record_payroll([_payroll(1001, date(2026, 10, 1), "Ann Lee")])
            p = vp.record_payment(b, b.vendors["V0001"], D("84.17"), date(2026, 10, 5))
            vp.void_payment(b, p)
            n = vendors.write_positive_pay(b, date(2026, 10, 1), date(2026, 10, 31), Path(d) / "pp.csv")
            with open(Path(d) / "pp.csv", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            self.assertEqual(n, 2)
            self.assertEqual(rows[1], ["000123456789", "1001", "10/01/2026", "500.00", "Ann Lee", ""])
            self.assertEqual(rows[2][-1], "V")


class StorageTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "b.xlsx"
            b = _book()
            p = vp.record_payment(b, b.vendors["V0001"], D("40.00"), date(2026, 10, 6), account_id="A01")
            b.save(path)
            got = TimecardBook.load(path)
            self.assertEqual(got.vendors["V0001"].address1, "1 Main St")
            self.assertTrue(got.vendors["V0001"].is_1099)
            self.assertEqual((got.accounts["A01"].name, got.accounts["A01"].last4), ("Business checking", "1234"))
            q = got.vendor_payments[0]
            self.assertEqual((q.check_number, q.amount, q.account_id, q.expense_ids),
                             (p.check_number, D("40.00"), "A01", p.expense_ids))
            self.assertEqual(got.expenses[0].account_id, "A01")


if __name__ == "__main__":
    unittest.main()
