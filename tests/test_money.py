"""1.5: account balances, money in / out, reconciling, and carrying accounts into a new year."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import money, vendor_payments as vp
from timecard.models import Account, AccountEntry, Company, Expense, PayrollRecord, Vendor
from timecard.storage import TimecardBook

D = Decimal


def _book() -> TimecardBook:
    b = TimecardBook.new(Company(company_name="Shop", year=2026, checks_account_id="A01", next_check_number=1001))
    b.accounts["A01"] = Account("A01", "Business checking", opening_balance=D("5000"))
    b.accounts["A02"] = Account("A02", "Savings", "Savings", opening_balance=D("1000"))
    b.vendors["V0001"] = Vendor("V0001", "Napa", default_category="Parts")
    b.expenses.append(Expense("X0001", date(2026, 1, 3), "Shell", "Fuel", D("40"), payment_method="Card",
                              account_id="A01"))
    b.expenses.append(Expense("X0002", date(2026, 1, 4), "Napa", "Parts", D("100"), payment_method="Check",
                              vendor_id="V0001", account_id="A01"))
    b.expenses.append(Expense("X0003", date(2026, 1, 5), "Shell", "Fuel", D("9"), status="Draft", account_id="A01"))
    b.record_payroll([PayrollRecord(1001, date(2026, 1, 9), date(2026, 1, 1), date(2026, 1, 7), "E1", "Sam",
                                    net_pay=D("800"), gross_pay=D("800"))])
    vp.record_payment(b, b.vendors["V0001"], D("100"), date(2026, 1, 6), bills=[b.expenses[1]])   # check 1002
    b.account_entries += [
        AccountEntry("M0001", date(2026, 1, 2), "A01", "Deposit", D("1200"), "Customer payments"),
        AccountEntry("M0002", date(2026, 1, 10), "A01", "Owner draw", D("300")),
        AccountEntry("M0003", date(2026, 1, 11), "A01", "Transfer", D("500"), to_account_id="A02"),
        AccountEntry("M0004", date(2026, 1, 12), "A01", "Owner contribution", D("250")),
    ]
    return b


class BalanceTests(unittest.TestCase):
    def test_register_and_balance(self) -> None:
        b = _book()
        rows = money.lines(b, "A01")
        keys = [ln.key for ln in rows]
        self.assertIn("chk:1002", keys)          # the vendor check, not also the bill it paid
        self.assertNotIn("exp:X0002", keys)
        self.assertNotIn("exp:X0003", keys)      # drafts aren't money out yet
        # 5000 + 1200 - 40 - 100 - 800 - 300 - 500 + 250
        self.assertEqual(money.balance(b, "A01"), D("4710.00"))
        self.assertEqual(money.balance(b, "A02"), D("1500.00"))             # the transfer came in
        self.assertEqual(money.balance(b, "A01", date(2026, 1, 5)), D("6160.00"))
        self.assertEqual(money.with_running_balance(b, "A01")[-1][1], D("4710.00"))
        b.payroll[0].voided = True
        self.assertEqual(money.balance(b, "A01"), D("5510.00"))             # a voided check is gone

    def test_reconcile_finish_and_undo(self) -> None:
        b = _book()
        start = money.reconcile_start(b, "A01")
        self.assertEqual(start, D("5000"))
        through = date(2026, 1, 31)
        open_lines = money.uncleared(b, "A01", through)
        cleared = [ln for ln in open_lines if ln.key != "chk:1001"]        # the payroll check hasn't cleared
        statement = D("5510.00")
        self.assertEqual(money.difference(start, cleared, statement), D("0.00"))
        with self.assertRaises(ValueError):
            money.finish(b, "A01", through, D("5000"), cleared)            # doesn't add up
        money.finish(b, "A01", through, statement, cleared)
        self.assertEqual([ln.key for ln in money.uncleared(b, "A01", date(2026, 2, 28))], ["chk:1001"])
        self.assertEqual(money.reconcile_start(b, "A01"), statement)
        self.assertEqual(money.cleared_on(b, "chk:1002"), through)
        with tempfile.TemporaryDirectory() as d:
            b.save(Path(d) / "b.xlsx")
            again = TimecardBook.load(Path(d) / "b.xlsx")
        self.assertEqual(again.cleared, b.cleared)
        self.assertEqual([(r.statement_date, r.statement_balance) for r in again.reconciliations], [(through, statement)])
        self.assertEqual(money.balance(again, "A01"), D("4710.00"))
        money.undo_last(b, "A01")
        self.assertEqual(b.cleared, {})
        self.assertEqual(money.reconcile_start(b, "A01"), D("5000"))

    def test_new_year_carries_balances_and_uncleared_checks(self) -> None:
        old = _book()
        through = date(2026, 12, 31)
        cleared = [ln for ln in money.uncleared(old, "A01", through) if ln.key != "chk:1001"]
        money.finish(old, "A01", through, D("5510.00"), cleared)
        new = TimecardBook.new(Company(company_name="Shop", year=2027, checks_account_id="A01"))
        money.carry_over(old, new)
        self.assertEqual(new.accounts["A01"].opening_balance, D("4710.00"))
        self.assertEqual(money.balance(new, "A01"), D("4710.00"))          # the carried check isn't counted again
        carried = money.uncleared(new, "A01", date(2027, 1, 31))
        self.assertEqual([(ln.amount, ln.counts) for ln in carried], [(D("-800"), False)])
        self.assertEqual(money.reconcile_start(new, "A01"), D("5510.00"))   # the bank's Dec 31 balance
        money.finish(new, "A01", date(2027, 1, 31), D("4710.00"), carried)  # the check cleared in January
        self.assertEqual(new.accounts["A02"].opening_balance, D("1500.00"))


class SplitExpenseTests(unittest.TestCase):
    def test_split_counts_in_each_category_and_is_saved(self) -> None:
        from timecard import expenses
        b = _book()
        e = Expense("X0009", date(2026, 2, 1), "Napa", expenses.SPLIT, D("150"), account_id="A01",
                    splits=[("Parts", D("120")), ("Supplies", D("30"))])
        b.expenses.append(e)
        totals = dict(expenses.totals_by_category([e, Expense("X0010", date(2026, 2, 2), "Ace", "Supplies", D("5"))]))
        self.assertEqual((totals["Parts"], totals["Supplies"]), (D("120"), D("35")))
        self.assertNotIn(expenses.SPLIT, totals)
        self.assertTrue(expenses.matches(e, category="Supplies"))
        self.assertFalse(expenses.matches(e, category="Fuel"))
        self.assertEqual(expenses.recurring_from(e, "Monthly", "R0001").category, "Parts")
        with tempfile.TemporaryDirectory() as d:
            b.save(Path(d) / "b.xlsx")
            again = TimecardBook.load(Path(d) / "b.xlsx")
        got = next(x for x in again.expenses if x.expense_id == "X0009")
        self.assertEqual((got.category, got.splits), (expenses.SPLIT, [("Parts", D("120.00")), ("Supplies", D("30.00"))]))
        self.assertEqual(money.balance(again, "A01"), D("4560.00"))   # the whole 150 left the account once


class PayBillGuardTests(unittest.TestCase):
    def test_reconciled_or_closed_bills_are_not_offered(self) -> None:
        b = _book()
        napa = b.vendors["V0001"]
        b.expenses += [Expense("X0101", date(2026, 3, 2), "Napa", "Parts", D("10"), payment_method="Check",
                               vendor_id="V0001", account_id="A01"),
                       Expense("X0102", date(2026, 3, 20), "Napa", "Parts", D("20"), payment_method="Check",
                               vendor_id="V0001", account_id="A01")]
        self.assertEqual([e.expense_id for e in vp.unpaid_bills(b, napa)], ["X0101", "X0102"])
        b.cleared["exp:X0102"] = date(2026, 3, 31)
        self.assertEqual([e.expense_id for e in vp.unpaid_bills(b, napa)], ["X0101"])
        b.company.closed_through = "2026-03-15"
        self.assertEqual(vp.unpaid_bills(b, napa), [])
