"""Bank statements: CSV columns, statement text (PDF / scan), matching to expenses."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import bank
from timecard.models import Expense

D = Decimal


class AmountDateTests(unittest.TestCase):
    def test_amounts_and_dates(self) -> None:
        self.assertEqual([bank.parse_amount(t) for t in ("$1,234.56", "-12.00", "(12.00)", "12.00-", "abc", "")],
                         [D("1234.56"), D("-12.00"), D("-12.00"), D("-12.00"), None, None])
        self.assertEqual(bank.parse_date("10/03/2026"), date(2026, 10, 3))
        self.assertEqual(bank.parse_date("2026-10-03"), date(2026, 10, 3))
        self.assertEqual(bank.parse_date("10/03", 2025), date(2025, 10, 3))


class CsvTests(unittest.TestCase):
    def test_single_amount_column(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.csv"
            p.write_text("Date,Description,Amount,Balance\n10/03/2026,POS HOME DEPOT #4521,-84.17,915.83\n"
                         "10/05/2026,DEPOSIT,500.00,1415.83\n\n", encoding="utf-8")
            headers, rows = bank.read_csv(p)
            cols = bank.guess_columns(headers)
            self.assertEqual(cols, {"date": 0, "description": 1, "amount": 2})
            lines = bank.lines_from_csv(rows, cols)
            self.assertEqual([(ln.line_date, ln.amount) for ln in lines],
                             [(date(2026, 10, 3), D("-84.17")), (date(2026, 10, 5), D("500.00"))])
            out = Path(d) / "out.csv"
            bank.write_csv(lines, out)
            self.assertIn("2026-10-03,POS HOME DEPOT #4521,-84.17", out.read_text(encoding="utf-8-sig"))

    def test_debit_and_credit_columns(self) -> None:
        cols = bank.guess_columns(["Posting Date", "Payee", "Debit", "Credit"])
        self.assertEqual(cols, {"date": 0, "description": 1, "debit": 2, "credit": 3})
        lines = bank.lines_from_csv([["10/03/2026", "SHELL", "40.00", ""], ["10/04/2026", "DEPOSIT", "", "250.00"]], cols)
        self.assertEqual([ln.amount for ln in lines], [D("-40.00"), D("250.00")])


class StatementTextTests(unittest.TestCase):
    def test_rows_with_a_running_balance(self) -> None:
        text = ["ACME BANK STATEMENT", "Beginning balance 10/01 1,000.00",
                "10/01 BEGINNING BALANCE 1,000.00",
                "10/03 POS PURCHASE HOME DEPOT #4521 84.17 915.83",
                "10/05 MOBILE CHECK DEP 500.00 1,415.83",
                "10/09 SHELL OIL 12345 40.00 1,375.83", "Page 1 of 2"]
        lines = bank.lines_from_text(text, 2026)
        self.assertEqual([(ln.line_date.day, ln.amount) for ln in lines],
                         [(3, D("-84.17")), (5, D("500.00")), (9, D("-40.00"))])

    def test_no_balance_uses_words(self) -> None:
        lines = bank.lines_from_text(["10/03 HOME DEPOT 84.17", "10/05 DEPOSIT 500.00"], 2026)
        self.assertEqual([ln.amount for ln in lines], [D("-84.17"), D("500.00")])


class ReviewFixTests(unittest.TestCase):
    def test_any_balance_row_is_not_a_transaction_and_guesses_are_marked(self) -> None:
        lines = bank.lines_from_text(["10/01 BALANCE FORWARD 1,000.00", "10/02 DAILY BALANCE 1,000.00",
                                      "10/03 HOME DEPOT 84.17 915.83", "10/05 SOMETHING 20.00"], 2026)
        self.assertEqual([(ln.description, ln.amount, ln.sure) for ln in lines],
                         [("HOME DEPOT", D("-84.17"), True), ("SOMETHING", D("-20.00"), False)])

    def test_column_guesses_and_positive_purchases(self) -> None:
        self.assertEqual(bank.guess_columns(["Date", "Transaction Amount", "Description"]),
                         {"date": 0, "amount": 1, "description": 2})
        self.assertNotIn("debit", bank.guess_columns(["Date", "Debit Card No", "Amount", "Payee"]))
        lines = bank.lines_from_csv([["10/03/2026", "AMAZON", "25.00"]], {"date": 0, "description": 1, "amount": 2},
                                    out_is_positive=True)
        self.assertEqual(lines[0].amount, D("-25.00"))


class MatchTests(unittest.TestCase):
    def test_matched_possible_new_and_money_in(self) -> None:
        exp = [Expense("X0001", date(2026, 10, 2), vendor="Home Depot", amount=D("84.17")),
               Expense("X0002", date(2026, 9, 25), vendor="Shell", amount=D("40.00"))]
        lines = [bank.BankLine(date(2026, 10, 3), "HOME DEPOT", D("-84.17")),
                 bank.BankLine(date(2026, 10, 3), "SHELL", D("-40.00")),
                 bank.BankLine(date(2026, 10, 4), "STAPLES", D("-12.00")),
                 bank.BankLine(date(2026, 10, 5), "DEPOSIT", D("500.00")),
                 bank.BankLine(date(2026, 10, 6), "HOME DEPOT", D("-84.17"))]
        got = [(m.status, m.expense.expense_id if m.expense else None) for m in bank.match_lines(lines, exp)]
        self.assertEqual(got, [("matched", "X0001"), ("possible", "X0002"), ("new", None), ("money in", None),
                               ("new", None)])   # an expense is matched once

    def test_vendor_names(self) -> None:
        self.assertEqual(bank.vendor_from("POS PURCHASE HOME DEPOT #4521 ATLANTA GA"), "HOME DEPOT")
        self.assertEqual(bank.vendor_from("SHELL OIL 57444"), "SHELL OIL")


if __name__ == "__main__":
    unittest.main()
