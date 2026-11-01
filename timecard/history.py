"""Change history for money records (1.5).

Rather than every screen remembering to write a log line, each save compares the money records with how they were
when the workbook was opened or last saved, and adds a line for every record added, changed or removed: when, who
(Windows user and computer), what, and the fields before -> after. The lines are kept on the workbook's History
sheet, so the history travels with the file and its backups. Nothing here can stop an edit; it only records it.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .storage import TimecardBook


@dataclass
class Change:
    when: str          # ISO date-time
    who: str           # "user on COMPUTER"
    action: str        # Added | Changed | Removed
    record: str        # e.g. "Expense X0001", "Check #1001"
    details: str       # "amount 40.00 -> 45.00; date ..." or the record's fields


def _m(x) -> str:
    return f"{x:.2f}"


def snapshot(book: "TimecardBook") -> dict[str, dict[str, str]]:
    """The money records as text fields, keyed by a readable record name."""
    snap: dict[str, dict[str, str]] = {}
    for e in book.expenses:
        snap[f"Expense {e.expense_id}"] = {
            "date": f"{e.expense_date}", "vendor": e.vendor, "category": e.category, "amount": _m(e.amount),
            "split": "; ".join(f"{c} {_m(a)}" for c, a in e.splits), "paid by": e.payment_method,
            "check": str(e.check_number or ""), "account": e.account_id, "status": e.status}
    for r in book.payroll:
        snap[f"Check #{r.check_number}"] = {
            "date": f"{r.check_date}", "payee": r.employee_name, "pay": _m(r.gross_pay), "amount": _m(r.net_pay),
            "advance repaid": _m(r.advance_repaid), "voided": "yes" if r.voided else "no"}
    for p in book.vendor_payments:
        snap[f"Vendor check #{p.check_number}"] = {
            "date": f"{p.check_date}", "payee": p.payee, "amount": _m(p.amount), "account": p.account_id,
            "voided": "yes" if p.voided else "no"}
    for m in book.account_entries:
        snap[f"{m.kind} {m.entry_id}"] = {
            "date": f"{m.entry_date}", "account": m.account_id, "amount": _m(m.amount), "into": m.to_account_id,
            "note": m.memo}
    for a in book.advances:
        snap[f"Advance {a.advance_id}"] = {"date": f"{a.advance_date}", "person": a.employee_id,
                                           "amount": _m(a.amount), "note": a.memo}
    for a in book.accounts.values():
        snap[f"Account {a.account_id}"] = {"name": a.name, "balance on Jan 1": _m(a.opening_balance),
                                           "in use": "yes" if a.active else "no"}
    for r in book.reconciliations:
        snap[f"Reconcile {r.account_id} {r.statement_date}"] = {"statement balance": _m(r.statement_balance)}
    snap["Books closed"] = {"through": book.company.closed_through or "(open)"}
    for e in book.employees.values():
        if e.advance_per_check:
            snap[f"Advance per check {e.employee_id}"] = {"amount": _m(e.advance_per_check)}
    return snap


def who() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "?"
    return f"{user} on {os.environ.get('COMPUTERNAME') or socket.gethostname() or '?'}"


def changes(before: dict[str, dict[str, str]], after: dict[str, dict[str, str]],
            now: datetime | None = None) -> list[Change]:
    when, by = (now or datetime.now()).isoformat(timespec="seconds"), who()
    out: list[Change] = []
    for key in sorted(after.keys() - before.keys()):
        out.append(Change(when, by, "Added", key, "; ".join(f"{k} {v}" for k, v in after[key].items() if v)))
    for key in sorted(before.keys() & after.keys()):
        diff = [f"{k} {before[key].get(k, '')} -> {v}" for k, v in after[key].items() if before[key].get(k, "") != v]
        if diff:
            out.append(Change(when, by, "Changed", key, "; ".join(diff)))
    for key in sorted(before.keys() - after.keys()):
        out.append(Change(when, by, "Removed", key, "; ".join(f"{k} {v}" for k, v in before[key].items() if v)))
    return out
