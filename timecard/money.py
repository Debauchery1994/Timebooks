"""Account balances, the account register and reconciling against bank statements (1.5).

An account's register is built from what the workbook already holds, so nothing has to be entered twice:

  * approved expenses paid from the account (an expense paid by a vendor check shows as that check instead)
  * payroll checks (from the checks account) and vendor checks (from the account they were written on), not voided
  * money in / out entries (AccountEntry): deposits, owner contributions and draws, loans, transfers, interest

Balance = the opening balance (Jan 1) + money in - money out. Reconciling ticks the lines that cleared the bank
through a statement date until they add up to the statement's ending balance; finished reconciles are kept
(book.reconciliations) with the lines they cleared (book.cleared: line key -> statement date).

Owner and loan money (OWNER_KINDS, LOAN_KINDS) is never income or an expense: it stays out of profit and loss.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from . import calc
from .models import AccountEntry, Reconciliation

if TYPE_CHECKING:
    from .storage import TimecardBook

ZERO = Decimal("0")
# kind -> which way the money goes for the account the entry is on (+1 in, -1 out)
KINDS = {
    "Deposit": 1,
    "Owner contribution": 1,
    "Loan received": 1,
    "Interest earned": 1,
    "Owner draw": -1,
    "Loan payment": -1,
    "Transfer": -1,          # out of this account, into to_account_id
    "Other money in": 1,
    "Other money out": -1,
}
OWNER_KINDS = ("Owner contribution", "Owner draw")
LOAN_KINDS = ("Loan received", "Loan payment")
CARRIED_KIND = "Uncleared from last year"


@dataclass
class Line:
    key: str                 # exp:<id> | chk:<number> | ent:<id> (an incoming transfer: ent:<id>:in)
    line_date: date
    description: str
    kind: str
    amount: Decimal          # + money in, - money out
    counts: bool = True      # False for a carried uncleared item (already in the opening balance)


def next_entry_id(entries: list[AccountEntry]) -> str:
    nums = [int(e.entry_id[1:]) for e in entries if e.entry_id[1:].isdigit()]
    return f"M{max(nums, default=0) + 1:04d}"


def lines(book: "TimecardBook", account_id: str) -> list[Line]:
    """The account's register lines, by date."""
    out: list[Line] = []
    checks_acct = book.company.checks_account_id
    live_vendor_checks = {p.check_number for p in book.vendor_payments if not p.voided}
    for e in book.expenses:
        if e.account_id == account_id and e.status != "Draft" and not (
                e.check_number and e.check_number in live_vendor_checks):
            paid = e.payment_method + (f" #{e.check_number}" if e.check_number else "")
            out.append(Line(f"exp:{e.expense_id}", e.expense_date, f"{e.vendor} ({paid})" if paid else e.vendor,
                            "Expense", -e.amount))
    if account_id and account_id == checks_acct:
        for r in book.payroll:
            if not r.voided:
                out.append(Line(f"chk:{r.check_number}", r.check_date, f"Check #{r.check_number} {r.employee_name}",
                                "Payroll check", -r.net_pay))
    for p in book.vendor_payments:
        if not p.voided and (p.account_id or checks_acct) == account_id and account_id:
            out.append(Line(f"chk:{p.check_number}", p.check_date, f"Check #{p.check_number} {p.payee}",
                            "Vendor check", -p.amount))
    names = {a.account_id: a.name for a in book.accounts.values()}
    for m in book.account_entries:
        if m.account_id == account_id:
            what = m.kind + (f" to {names.get(m.to_account_id, 'another account')}" if m.kind == "Transfer" else "")
            amt = m.amount if m.kind == CARRIED_KIND else KINDS.get(m.kind, 1) * m.amount   # carried: signed
            out.append(Line(f"ent:{m.entry_id}", m.entry_date, f"{what}: {m.memo}" if m.memo else what, m.kind,
                            amt, counts=not m.carried))
        if m.kind == "Transfer" and m.to_account_id == account_id and account_id:
            what = f"Transfer from {names.get(m.account_id, 'another account')}"
            out.append(Line(f"ent:{m.entry_id}:in", m.entry_date, f"{what}: {m.memo}" if m.memo else what,
                            "Transfer", m.amount, counts=not m.carried))
    return sorted(out, key=lambda ln: (ln.line_date, ln.key))


def balance(book: "TimecardBook", account_id: str, through: date | None = None) -> Decimal:
    acct = book.accounts.get(account_id)
    start = acct.opening_balance if acct else ZERO
    return calc.money(start + sum((ln.amount for ln in lines(book, account_id)
                                   if ln.counts and (through is None or ln.line_date <= through)), ZERO))


def with_running_balance(book: "TimecardBook", account_id: str) -> list[tuple[Line, Decimal]]:
    acct = book.accounts.get(account_id)
    run = acct.opening_balance if acct else ZERO
    rows = []
    for ln in lines(book, account_id):
        if ln.counts:
            run += ln.amount
        rows.append((ln, calc.money(run)))
    return rows


# ---- reconciling

def last_reconciliation(book: "TimecardBook", account_id: str) -> Reconciliation | None:
    done = [r for r in book.reconciliations if r.account_id == account_id]
    return max(done, key=lambda r: r.statement_date) if done else None


def reconcile_start(book: "TimecardBook", account_id: str) -> Decimal:
    """The bank's balance the next statement starts from: the last reconciled statement's ending balance, or for
    the first one the opening balance less last year's items that hadn't cleared yet."""
    last = last_reconciliation(book, account_id)
    if last is not None:
        return last.statement_balance
    acct = book.accounts.get(account_id)
    start = acct.opening_balance if acct else ZERO
    return calc.money(start - sum((ln.amount for ln in lines(book, account_id) if not ln.counts), ZERO))


def uncleared(book: "TimecardBook", account_id: str, through: date) -> list[Line]:
    return [ln for ln in lines(book, account_id) if ln.key not in book.cleared and ln.line_date <= through]


def difference(start: Decimal, ticked: list[Line], statement_balance: Decimal) -> Decimal:
    """What's left to explain: 0.00 when the ticked lines bring the start to the statement balance."""
    return calc.money(start + sum((ln.amount for ln in ticked), ZERO) - statement_balance)


def finish(book: "TimecardBook", account_id: str, statement_date: date, statement_balance: Decimal,
           ticked: list[Line]) -> Reconciliation:
    start = reconcile_start(book, account_id)
    if difference(start, ticked, statement_balance) != 0:
        raise ValueError("The ticked items don't add up to the statement balance yet.")
    last = last_reconciliation(book, account_id)
    if last is not None and statement_date <= last.statement_date:
        raise ValueError(f"This account is already reconciled through {last.statement_date:%m/%d/%Y}.")
    for ln in ticked:
        book.cleared[ln.key] = statement_date
    r = Reconciliation(account_id, statement_date, calc.money(statement_balance),
                       datetime.now().isoformat(timespec="seconds"))
    book.reconciliations.append(r)
    book.dirty = True
    return r


def undo_last(book: "TimecardBook", account_id: str) -> Reconciliation | None:
    """Take back the account's latest reconcile: its lines are uncleared again."""
    last = last_reconciliation(book, account_id)
    if last is None:
        return None
    keys = {ln.key for ln in lines(book, account_id)}
    for k in [k for k, d in book.cleared.items() if d == last.statement_date and k in keys]:
        del book.cleared[k]
    book.reconciliations.remove(last)
    book.dirty = True
    return last


def cleared_on(book: "TimecardBook", key: str) -> date | None:
    """The statement date a line was reconciled with, if it was."""
    return book.cleared.get(key)


# ---- closing the books

def closed_through(book: "TimecardBook") -> date | None:
    try:
        return date.fromisoformat(book.company.closed_through) if book.company.closed_through else None
    except ValueError:
        return None


def is_closed(book: "TimecardBook", *dates: date | None) -> bool:
    """True if any of the dates is on or before the day the books are closed through."""
    end = closed_through(book)
    return end is not None and any(d is not None and d <= end for d in dates)


def closed_message(book: "TimecardBook") -> str:
    end = closed_through(book)
    return (f"The books are closed through {end:%m/%d/%Y}, so nothing dated then can be changed. To change it, "
            "reopen the books on the Accounts tab (Close Books) first.")


# ---- a new year's workbook

def carry_over(old: "TimecardBook", new: "TimecardBook") -> None:
    """Accounts start the new year at last year's closing balance. For an account that was being reconciled, items
    that hadn't cleared the bank yet come along (not changing the balance again) so January's statement can still
    be reconciled."""
    year = old.company.year
    for aid, a in old.accounts.items():
        new.accounts[aid] = type(a)(a.account_id, a.name, a.kind, a.last4, balance(old, aid), a.active)
        if last_reconciliation(old, aid) is None:
            continue
        for ln in lines(old, aid):
            if ln.key not in old.cleared:
                new.account_entries.append(AccountEntry(next_entry_id(new.account_entries), ln.line_date, aid,
                                                        CARRIED_KIND, ln.amount, f"{ln.description} ({year})",
                                                        carried=True))
