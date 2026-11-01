"""Accounts tab (1.5): each account's balance and register, money in / out that isn't an expense (deposits, owner
draws and contributions, loans, transfers), and reconciling against the bank statement. See money.py."""
from __future__ import annotations

import tkinter as tk
from datetime import date, timedelta
from decimal import Decimal
from tkinter import messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from .. import calc, money, security
from ..models import AccountEntry
from .common import ModalDialog, fmt_money, parse_date, parse_money, style_tree
from .pin import PinLockable
from .vendors_tab import AccountsDialog

if TYPE_CHECKING:
    from .app import App

ZERO = Decimal("0")


def _signed(x: Decimal) -> str:
    return fmt_money(x) if x >= 0 else f"-{fmt_money(-x)}"


class EntryDialog(ModalDialog):
    """Add or change one money in / out entry. result = the AccountEntry (built in apply)."""

    def __init__(self, master, book, entry: AccountEntry):
        self.book, self.m = book, entry
        super().__init__(master, "Money In / Out")

    def body(self, f):
        m = self.m
        accts = sorted((a for a in self.book.accounts.values() if a.active or a.account_id in
                        (m.account_id, m.to_account_id)), key=lambda a: a.name.lower())
        self.accts = [(a.account_id, a.name) for a in accts]
        owners = [e for e in self.book.employees.values() if calc.is_owner(e) or e.employee_id == m.person_id]
        self.owners = [("", "(not said)")] + [(e.employee_id, e.full_name) for e in owners]
        self.e_date, self.e_amount, self.e_memo = ttk.Entry(f, width=14), ttk.Entry(f, width=14), ttk.Entry(f, width=40)
        self.e_date.insert(0, m.entry_date.isoformat())
        self.e_amount.insert(0, f"{m.amount:.2f}" if m.amount else "")
        self.e_memo.insert(0, m.memo)
        self.cb_kind = ttk.Combobox(f, values=list(money.KINDS), state="readonly", width=22)
        self.cb_kind.set(m.kind if m.kind in money.KINDS else "Deposit")
        self.cb_kind.bind("<<ComboboxSelected>>", lambda e: self._show())
        names = [n for _i, n in self.accts]
        self.cb_acct = ttk.Combobox(f, values=names, state="readonly", width=26)
        self.cb_to = ttk.Combobox(f, values=names, state="readonly", width=26)
        self.cb_owner = ttk.Combobox(f, values=[n for _i, n in self.owners], state="readonly", width=26)
        ids = [i for i, _n in self.accts]
        if m.account_id in ids:
            self.cb_acct.current(ids.index(m.account_id))
        elif ids:
            self.cb_acct.current(0)
        if m.to_account_id in ids:
            self.cb_to.current(ids.index(m.to_account_id))
        oids = [i for i, _n in self.owners]
        self.cb_owner.current(oids.index(m.person_id) if m.person_id in oids else 0)
        self.rows = {}
        for r, (key, label, w) in enumerate((("date", "Date", self.e_date), ("kind", "What", self.cb_kind),
                                             ("acct", "Account", self.cb_acct), ("to", "Into account", self.cb_to),
                                             ("owner", "Owner", self.cb_owner), ("amount", "Amount", self.e_amount),
                                             ("memo", "Note", self.e_memo))):
            lbl = ttk.Label(f, text=label)
            lbl.grid(row=r, column=0, sticky="w", pady=2, padx=(0, 8))
            w.grid(row=r, column=1, sticky="w", pady=2)
            self.rows[key] = (lbl, w)
        self.v_hint = tk.StringVar()
        ttk.Label(f, textvariable=self.v_hint, style="Muted.TLabel", wraplength=380).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._show()
        return self.e_amount

    def _show(self) -> None:
        kind = self.cb_kind.get()
        for key, on in (("to", kind == "Transfer"), ("owner", kind in money.OWNER_KINDS)):
            for w in self.rows[key]:
                w.grid() if on else w.grid_remove()
        self.rows["acct"][0].configure(text="From account" if kind == "Transfer" else "Account")
        self.v_hint.set({
            "Owner contribution": "Money the owner put into the business. Not income: it stays out of profit and loss.",
            "Owner draw": "Money the owner took out for themselves. Not an expense: it stays out of profit and loss.",
            "Loan received": "Borrowed money coming in. Not income.",
            "Loan payment": "Paying a loan back (the part that isn't interest; record interest as an expense).",
            "Transfer": "Moving money between your own accounts: out of one, into the other.",
            "Deposit": "Money in, e.g. customer payments taken to the bank.",
        }.get(kind, ""))

    def apply(self) -> bool:
        try:
            d = parse_date(self.e_date.get())
            amt = parse_money(self.e_amount.get() or "0")
        except ValueError as exc:
            messagebox.showwarning("Money In / Out", str(exc), parent=self)
            return False
        kind = self.cb_kind.get()
        acct = self.accts[self.cb_acct.current()][0] if self.cb_acct.current() >= 0 else ""
        to = self.accts[self.cb_to.current()][0] if kind == "Transfer" and self.cb_to.current() >= 0 else ""
        problems = [msg for bad, msg in ((amt <= 0, "Enter the amount."), (not acct, "Choose the account."),
                                         (kind == "Transfer" and (not to or to == acct),
                                          "Choose the other account the money went into.")) if bad]
        if problems:
            messagebox.showwarning("Money In / Out", "\n".join(problems), parent=self)
            return False
        if money.is_closed(self.book, d):
            messagebox.showwarning("Money In / Out", money.closed_message(self.book), parent=self)
            return False
        person = self.owners[max(self.cb_owner.current(), 0)][0] if kind in money.OWNER_KINDS else ""
        self.result = AccountEntry(self.m.entry_id, d, acct, kind, calc.money(amt), self.e_memo.get().strip(),
                                   to, person)
        return True


class ReconcileDialog(ModalDialog):
    """Tick what cleared the bank through the statement date until it matches the statement's ending balance.
    result = (statement date, statement balance, ticked lines) (built in apply; finished by the caller)."""

    def __init__(self, master, book, account_id: str):
        self.book, self.account_id = book, account_id
        self.ticked: set[str] = set()
        self.lines: list[money.Line] = []
        acct = book.accounts.get(account_id)
        super().__init__(master, f"Reconcile {acct.name if acct else ''}")

    def body(self, f):
        last = money.last_reconciliation(self.book, self.account_id)
        self.start = money.reconcile_start(self.book, self.account_id)
        ttk.Label(f, text=(f"Last reconciled through {last.statement_date:%m/%d/%Y}." if last else
                           "First reconcile for this account.") + f" Starting balance {fmt_money(self.start)}.",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        self.e_date, self.e_bal = ttk.Entry(f, width=14), ttk.Entry(f, width=14)
        today = date.today()
        self.e_date.insert(0, (date(today.year, today.month, 1) - timedelta(days=1)).isoformat())   # last month end
        for c, (label, w) in enumerate((("Statement date", self.e_date), ("Ending balance", self.e_bal))):
            ttk.Label(f, text=label).grid(row=1, column=c * 2, sticky="w", pady=(8, 4), padx=(0 if c == 0 else 16, 6))
            w.grid(row=1, column=c * 2 + 1, sticky="w", pady=(8, 4))
        self.e_date.bind("<FocusOut>", lambda e: self.fill())
        self.e_bal.bind("<KeyRelease>", lambda e: self._totals())
        cols = (("tick", "Cleared", 60), ("date", "Date", 90), ("what", "Description", 300), ("amount", "Amount", 100))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=12)
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c == "amount" else "center" if c == "tick" else "w")
        style_tree(self.tree)
        self.tree.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(4, 4))
        self.tree.bind("<Double-1>", lambda e: self._toggle([self.tree.identify_row(e.y)]))
        self.tree.bind("<space>", lambda e: self._toggle(list(self.tree.selection())))
        b = ttk.Frame(f)
        b.grid(row=3, column=0, columnspan=4, sticky="w")
        ttk.Button(b, text="Tick all", command=lambda: self._tick_all(True)).pack(side="left")
        ttk.Button(b, text="Untick all", command=lambda: self._tick_all(False)).pack(side="left", padx=6)
        self.v_totals = tk.StringVar()
        ttk.Label(f, textvariable=self.v_totals, style="Bold.TLabel").grid(row=4, column=0, columnspan=4, sticky="w",
                                                                           pady=(8, 0))
        ttk.Label(f, text="Double-click (or Space) the items that are on the statement. When the difference is 0.00, "
                  "click OK to finish. Items not on this statement stay for the next one.", style="Muted.TLabel",
                  wraplength=560).grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.fill()
        return self.e_bal

    def _through(self) -> date | None:
        try:
            return parse_date(self.e_date.get())
        except ValueError:
            return None

    def fill(self) -> None:
        through = self._through()
        if through is None:
            return
        self.lines = money.uncleared(self.book, self.account_id, through)
        self.ticked &= {ln.key for ln in self.lines}
        self.tree.delete(*self.tree.get_children())
        for ln in self.lines:
            self.tree.insert("", "end", iid=ln.key, values=("✔" if ln.key in self.ticked else "",
                                                            f"{ln.line_date:%m/%d/%Y}", ln.description,
                                                            _signed(ln.amount)))
        self._totals()

    def _toggle(self, iids: list[str]) -> str:
        for iid in filter(None, iids):
            self.ticked.symmetric_difference_update({iid})
            self.tree.set(iid, "tick", "✔" if iid in self.ticked else "")
        self._totals()
        return "break"

    def _tick_all(self, on: bool) -> None:
        self.ticked = {ln.key for ln in self.lines} if on else set()
        for ln in self.lines:
            self.tree.set(ln.key, "tick", "✔" if on else "")
        self._totals()

    def _statement(self) -> Decimal | None:
        try:
            return parse_money(self.e_bal.get()) if self.e_bal.get().strip() else None
        except ValueError:
            return None

    def _totals(self) -> None:
        chosen = [ln for ln in self.lines if ln.key in self.ticked]
        cleared = self.start + sum((ln.amount for ln in chosen), ZERO)
        st = self._statement()
        diff = "" if st is None else f"   Difference {_signed(money.difference(self.start, chosen, st))}"
        self.v_totals.set(f"Cleared balance {_signed(calc.money(cleared))} ({len(chosen)} items){diff}")

    def apply(self) -> bool:
        through, st = self._through(), self._statement()
        if through is None or st is None:
            messagebox.showwarning("Reconcile", "Enter the statement date and its ending balance.", parent=self)
            return False
        chosen = [ln for ln in self.lines if ln.key in self.ticked]
        diff = money.difference(self.start, chosen, st)
        if diff != 0:
            messagebox.showwarning("Reconcile", f"The difference is {_signed(diff)}. Tick the items on the statement "
                                   "(or check the ending balance) until it's 0.00. Missing a bank fee or deposit? "
                                   "Add it first, then reconcile.", parent=self)
            return False
        last = money.last_reconciliation(self.book, self.account_id)
        if last is not None and through <= last.statement_date:
            messagebox.showwarning("Reconcile", f"This account is already reconciled through "
                                   f"{last.statement_date:%m/%d/%Y}.", parent=self)
            return False
        self.result = (through, st, chosen)
        return True


class CloseBooksDialog(ModalDialog):
    """Close the books through a date (or reopen them). result = the new ISO date, or "" to open everything."""

    def __init__(self, master, book):
        self.book = book
        super().__init__(master, "Close Books")

    def body(self, f):
        end = money.closed_through(self.book)
        ttk.Label(f, text=(f"The books are closed through {end:%m/%d/%Y}." if end else "The books are open."),
                  style="Bold.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Closing a month or year stops anything dated on or before that day from being changed by "
                  "accident: expenses, checks and voids, money in / out, advances and reconciles. You can reopen "
                  "the books (with the PIN) to fix something.", style="Muted.TLabel", wraplength=440,
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))
        self.v = tk.StringVar(value="close")
        ttk.Radiobutton(f, text="Close the books through", value="close", variable=self.v).grid(row=2, column=0,
                                                                                               sticky="w")
        self.e_date = ttk.Entry(f, width=14)
        today = date.today()
        self.e_date.insert(0, (date(today.year, today.month, 1) - timedelta(days=1)).isoformat())
        self.e_date.grid(row=2, column=1, sticky="w")
        if end is not None:
            ttk.Radiobutton(f, text="Reopen all the books", value="open", variable=self.v).grid(
                row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        recon = []
        for a in sorted((a for a in self.book.accounts.values() if a.active), key=lambda a: a.name.lower()):
            last = money.last_reconciliation(self.book, a.account_id)
            recon.append(f"{a.name}: " + (f"reconciled through {last.statement_date:%m/%d/%Y}" if last
                                          else "not reconciled yet"))
        if recon:
            ttk.Label(f, text="\n".join(recon), style="Muted.TLabel", justify="left").grid(
                row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        return self.e_date

    def apply(self) -> bool:
        if self.v.get() == "open":
            self.result = ""
            return True
        try:
            d = parse_date(self.e_date.get())
        except ValueError as exc:
            messagebox.showwarning("Close Books", str(exc), parent=self)
            return False
        self.result = d.isoformat()
        return True


class HistoryDialog(ModalDialog):
    """The workbook's change history (money records), newest first, with a search box."""

    def __init__(self, master, book):
        self.book = book
        super().__init__(master, "Change History")

    def body(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        self.v_find = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.v_find, width=30)
        e.pack(side="left", padx=6)
        self.v_find.trace_add("write", lambda *_: self.fill())
        cols = (("when", "When", 140), ("who", "Who", 170), ("action", "", 70), ("record", "Record", 170),
                ("details", "Details", 520))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=18)
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "details"))
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True, pady=(6, 0))
        ttk.Label(f, text="Every change to money records is noted here when the workbook is saved. It travels with "
                  "the workbook (its History sheet) and its backups.", style="Muted.TLabel").pack(anchor="w",
                                                                                                   pady=(6, 0))
        self.fill()
        return e

    def fill(self) -> None:
        q = self.v_find.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for c in reversed(self.book.history):
            if q and q not in f"{c.when} {c.who} {c.action} {c.record} {c.details}".lower():
                continue
            self.tree.insert("", "end", values=(c.when.replace("T", " "), c.who, c.action, c.record, c.details))

    def apply(self) -> bool:
        return True


class AccountsTab(PinLockable, ttk.Frame):
    pin_key = "accounts"
    pin_what = "Account balances"
    pin_purpose = "see balances, record money in and out, or reconcile"

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Money In / Out…", style="Accent.TButton", command=self.add_entry).pack(side="left")
        for text, cmd in (("Reconcile…", self.reconcile), ("Undo Last Reconcile", self.undo_reconcile),
                          ("Edit Accounts…", self.edit_accounts), ("Close Books…", self.close_books),
                          ("History…", self.show_history)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(6, 0))
        cols = (("name", "Account", 220), ("kind", "Type", 100), ("last4", "Last 4", 60), ("bal", "Balance", 120),
                ("rec", "Reconciled through", 140))
        self.atree = ttk.Treeview(self, columns=[c[0] for c in cols], show="headings", height=5, selectmode="browse")
        for c, h, w in cols:
            self.atree.heading(c, text=h)
            self.atree.column(c, width=w, anchor="e" if c == "bal" else "w")
        style_tree(self.atree)
        self.atree.pack(fill="x", pady=(8, 0))
        self.atree.bind("<<TreeviewSelect>>", lambda e: self.fill_register())
        self.v_owner = tk.StringVar()
        ttk.Label(self, textvariable=self.v_owner, style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(4, 0))
        self.v_closed = tk.StringVar()
        ttk.Label(self, textvariable=self.v_closed, style="Muted.TLabel").pack(anchor="w")
        cols = (("date", "Date", 90), ("what", "Description", 340), ("kind", "Kind", 130), ("out", "Money out", 100),
                ("in", "Money in", 100), ("bal", "Balance", 110), ("clr", "Cleared", 90))
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c in ("out", "in", "bal") else "w", stretch=(c == "what"))
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True, pady=(8, 0))
        self.tree.bind("<Double-1>", lambda e: self.edit_entry())
        b = ttk.Frame(self)
        b.pack(fill="x", pady=(6, 0))
        ttk.Button(b, text="Edit…", command=self.edit_entry).pack(side="left")
        ttk.Button(b, text="Delete", command=self.delete_entry).pack(side="left", padx=6)
        ttk.Label(b, text="Expenses and checks show here by themselves; edit them on their own tabs. Money In / Out is "
                  "for deposits, owner draws and contributions, loans and transfers.", style="Muted.TLabel",
                  wraplength=700).pack(side="left", padx=(10, 0))

    @property
    def book(self):
        return self.app.book

    def pin_required(self, company) -> bool:
        """Locked when Accounts is, and also when Pay & Checks or Expenses is: the register shows their amounts."""
        return any(getattr(company, f"lock_{k}", False) for k in ("accounts", "payroll", "expenses"))

    def on_pin_unlock(self, quiet: bool) -> None:
        self.refresh()
        super().on_pin_unlock(quiet)

    def selected_account(self) -> str:
        sel = self.atree.selection()
        return sel[0] if sel else ""

    def refresh(self) -> None:
        if self.book is None or self.locked:
            return
        keep = self.selected_account()
        self.atree.delete(*self.atree.get_children())
        for a in sorted((a for a in self.book.accounts.values() if a.active), key=lambda a: a.name.lower()):
            last = money.last_reconciliation(self.book, a.account_id)
            self.atree.insert("", "end", iid=a.account_id, values=(
                a.name, a.kind, a.last4, _signed(money.balance(self.book, a.account_id)),
                f"{last.statement_date:%m/%d/%Y}" if last else ""))
        kids = self.atree.get_children()
        if keep in kids:
            self.atree.selection_set(keep)
        elif kids:
            self.atree.selection_set(kids[0])
        self._owner_totals()
        end = money.closed_through(self.book)
        self.v_closed.set(f"Books closed through {end:%m/%d/%Y}." if end else "")
        self.fill_register()

    def _owner_totals(self) -> None:
        year = self.book.company.year
        tot = {k: ZERO for k in money.OWNER_KINDS + money.LOAN_KINDS}
        for m in self.book.account_entries:
            if m.kind in tot and m.entry_date.year == year and not m.carried:
                tot[m.kind] += m.amount
        self.v_owner.set(f"{year} (kept out of profit and loss): owner draws {fmt_money(tot['Owner draw'])}, "
                         f"owner contributions {fmt_money(tot['Owner contribution'])}, loans received "
                         f"{fmt_money(tot['Loan received'])}, loan payments {fmt_money(tot['Loan payment'])}")

    def fill_register(self) -> None:
        self.tree.delete(*self.tree.get_children())
        aid = self.selected_account()
        if not aid or self.book is None:
            return
        acct = self.book.accounts[aid]
        self.tree.insert("", "end", iid="__open__", tags=("total",), values=(
            f"01/01/{self.book.company.year}", "Opening balance", "", "", "", _signed(acct.opening_balance), ""))
        for ln, run in money.with_running_balance(self.book, aid):
            cleared = money.cleared_on(self.book, ln.key)
            self.tree.insert("", "end", iid=ln.key, tags=() if ln.counts else ("former",), values=(
                f"{ln.line_date:%m/%d/%Y}", ln.description, ln.kind,
                fmt_money(-ln.amount) if ln.amount < 0 else "", fmt_money(ln.amount) if ln.amount > 0 else "",
                _signed(run) if ln.counts else "(last year)", f"{cleared:%m/%d/%Y}" if cleared else ""))
        kids = self.tree.get_children()
        if kids:
            self.tree.see(kids[-1])

    def _selected_entry(self) -> AccountEntry | None:
        key = (self.tree.selection() or [""])[0]
        if not key.startswith("ent:"):
            if key:
                self.app.status("Expenses and checks are changed on the Expenses and Pay & Checks tabs.")
            return None
        eid = key.split(":")[1]
        return next((m for m in self.book.account_entries if m.entry_id == eid), None)

    def _changeable(self, m: AccountEntry) -> bool:
        keys = [f"ent:{m.entry_id}", f"ent:{m.entry_id}:in"]
        done = [money.cleared_on(self.book, k) for k in keys if money.cleared_on(self.book, k)]
        if m.carried or done:
            messagebox.showinfo("Accounts", "This item was reconciled (or came from last year), so it can't be "
                                "changed. Undo the reconcile first if it's wrong.", parent=self)
            return False
        if money.is_closed(self.book, m.entry_date):
            messagebox.showinfo("Accounts", money.closed_message(self.book), parent=self)
            return False
        return True

    def add_entry(self) -> None:
        if self.book is None or self.locked:
            return
        if not self.book.accounts:
            messagebox.showinfo("Money In / Out", "Add your accounts first (Edit Accounts).", parent=self)
            return
        m = AccountEntry(money.next_entry_id(self.book.account_entries), date.today(), self.selected_account(),
                         "Deposit")
        r = EntryDialog(self, self.book, m).show()
        if r is not None:
            self.book.account_entries.append(r)
            self._changed(f"Added {r.kind.lower()} of {fmt_money(r.amount)}.")

    def edit_entry(self) -> None:
        m = self._selected_entry() if self.book is not None and not self.locked else None
        if m is None or not self._changeable(m):
            return
        r = EntryDialog(self, self.book, m).show()
        if r is not None:
            self.book.account_entries[self.book.account_entries.index(m)] = r
            self._changed(f"Saved the {r.kind.lower()}.")

    def delete_entry(self) -> None:
        m = self._selected_entry() if self.book is not None and not self.locked else None
        if m is None or not self._changeable(m):
            return
        if messagebox.askyesno("Accounts", f"Delete the {m.kind.lower()} of {fmt_money(m.amount)} on "
                               f"{m.entry_date:%m/%d/%Y}?", parent=self):
            self.book.account_entries.remove(m)
            self._changed("Deleted.")

    def _changed(self, msg: str) -> None:
        self.app.mark_dirty()
        self.refresh()
        self.app.status(msg + self.app.save_hint())

    def reconcile(self) -> None:
        aid = self.selected_account() if self.book is not None and not self.locked else ""
        if not aid:
            messagebox.showinfo("Reconcile", "Choose the account to reconcile.", parent=self)
            return
        r = ReconcileDialog(self, self.book, aid).show()
        if r is None:
            return
        through, st, chosen = r
        money.finish(self.book, aid, through, st, chosen)
        self._changed(f"Reconciled {self.book.accounts[aid].name} through {through:%m/%d/%Y}.")

    def undo_reconcile(self) -> None:
        aid = self.selected_account() if self.book is not None and not self.locked else ""
        last = money.last_reconciliation(self.book, aid) if aid else None
        if last is None:
            messagebox.showinfo("Reconcile", "This account hasn't been reconciled yet.", parent=self)
            return
        if money.is_closed(self.book, last.statement_date):
            messagebox.showinfo("Undo Last Reconcile", money.closed_message(self.book), parent=self)
            return
        if messagebox.askyesno("Undo Last Reconcile", f"Undo the reconcile through {last.statement_date:%m/%d/%Y}? "
                               "Its items become uncleared again.", parent=self):
            money.undo_last(self.book, aid)
            self._changed(f"Undid the reconcile through {last.statement_date:%m/%d/%Y}.")

    def close_books(self) -> None:
        if self.book is None or self.locked:
            return
        r = CloseBooksDialog(self, self.book).show()
        if r is None or r == self.book.company.closed_through:
            return
        if security.has_pin():
            pin = simpledialog.askstring("Close Books", "Enter the PIN to " + ("reopen" if not r else "close")
                                         + " the books:", show="\u2022", parent=self)
            if pin is None:
                return
            if not security.verify_pin(pin):
                messagebox.showwarning("Close Books", "That PIN isn't right.", parent=self)
                return
        self.book.company.closed_through = r
        self._changed(f"Books closed through {date.fromisoformat(r):%m/%d/%Y}." if r else "The books are open.")

    def show_history(self) -> None:
        if self.book is not None and not self.locked:
            HistoryDialog(self, self.book).show()

    def edit_accounts(self) -> None:
        if self.book is not None and not self.locked:
            AccountsDialog(self, self.book, self.app).show()
            self.refresh()
