"""Import a bank statement (CSV, PDF or scan), see each line matched to an expense, and add the unmatched money
out as Draft expenses. Nothing changes until OK."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import bank, expenses
from ..models import Expense
from .common import ModalDialog, fmt_money, log, style_tree

NONE = "(none)"
LABELS = {"matched": "Matched", "possible": "Possible match", "new": "Not in expenses", "money in": "Money in"}


class BankImportDialog(ModalDialog):
    """result = how many Draft expenses were added (None when cancelled)."""

    def __init__(self, master, book, app, path: Path):
        self.book, self.app, self.path = book, app, path
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self.matches: list[bank.Match] = []
        self.add: set[int] = set()
        self.pickers: dict[str, ttk.Combobox] = {}
        super().__init__(master, f"Bank Statement: {path.name}")

    def body(self, f):
        self.is_csv = self.path.suffix.lower() == ".csv"
        if self.is_csv:
            self.headers, self.rows = bank.read_csv(self.path)
            saved = (self.app.settings.data.get("bank_columns") or {}).get("|".join(self.headers)) or {}
            cols = {k: v for k, v in saved.items() if k in bank.FIELDS} or bank.guess_columns(self.headers)
            self.v_flip = tk.BooleanVar(value=bool(saved.get("out_is_positive")))
            pick = ttk.Frame(f)
            pick.pack(fill="x", pady=(0, 8))
            ttk.Label(pick, text="Columns:").pack(side="left")
            for field in bank.FIELDS:
                ttk.Label(pick, text=field.capitalize()).pack(side="left", padx=(10, 2))
                cb = ttk.Combobox(pick, values=[NONE] + self.headers, state="readonly", width=14)
                cb.set(self.headers[cols[field]] if field in cols and cols[field] < len(self.headers) else NONE)
                cb.bind("<<ComboboxSelected>>", lambda e: self.reload())
                cb.pack(side="left")
                self.pickers[field] = cb
            ttk.Checkbutton(pick, text="Money out is positive", variable=self.v_flip,
                            command=self.reload).pack(side="left", padx=(12, 0))
        accts = sorted((a for a in self.book.accounts.values() if a.active), key=lambda a: a.name.lower())
        self.accounts = [("", "(not said)")] + [(a.account_id, a.name) for a in accts]
        arow = ttk.Frame(f)
        arow.pack(fill="x", pady=(0, 6))
        ttk.Label(arow, text="This statement is for").pack(side="left")
        self.cb_acct = ttk.Combobox(arow, values=[n for _i, n in self.accounts], state="readonly", width=28)
        self.cb_acct.current(1 if len(self.accounts) == 2 else 0)
        self.cb_acct.pack(side="left", padx=6)
        cols = (("date", "Date", 90), ("desc", "Description", 320), ("amount", "Amount", 100),
                ("match", "Match", 220), ("add", "Add", 60))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=16)
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c == "amount" else "center" if c == "add" else "w")
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._toggle)
        bar = ttk.Frame(f, padding=(0, 8, 0, 0))
        bar.pack(fill="x")
        self.v_note = ttk.Label(bar, text="", style="Muted.TLabel", wraplength=640, justify="left")
        self.v_note.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Save rows as CSV…", command=self.save_csv).pack(side="right")
        self.reload()
        return self.tree

    def _columns(self) -> dict[str, int]:
        return {f: self.headers.index(cb.get()) for f, cb in self.pickers.items() if cb.get() in self.headers}

    def reload(self) -> None:
        try:
            if self.is_csv:
                lines = bank.lines_from_csv(self.rows, self._columns(), self.v_flip.get())
            else:
                self.app.config(cursor="watch")
                self.app.update_idletasks()
                lines = bank.lines_from_text(bank.statement_text(self.path), self.book.company.year)
        except Exception as exc:  # noqa: BLE001 - any unreadable statement is reported, never fatal
            log.warning("Could not read bank statement %s: %s", self.path, exc)
            lines = []
        finally:
            self.app.config(cursor="")
        self.matches = bank.match_lines(lines, self.book.expenses)
        # offered as drafts: money out not in expenses, read for sure, in this workbook's year
        year = self.book.company.year
        self.add = {i for i, m in enumerate(self.matches)
                    if m.status == "new" and m.line.sure and m.line.line_date.year == year}
        self.fill()

    def fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.matches):
            ln = m.line
            match = LABELS[m.status] + (f": {m.expense.vendor} {m.expense.expense_date:%m/%d}" if m.expense else "")
            if not ln.sure:
                match += " (in/out guessed)"
            if ln.line_date.year != self.book.company.year:
                match += f" ({ln.line_date.year})"
            self.tree.insert("", "end", iid=str(i), tags=("former",) if m.status == "money in" else (),
                             values=(f"{ln.line_date:%m/%d/%Y}", ln.description,
                                     fmt_money(ln.amount) if ln.amount >= 0 else f"-{fmt_money(-ln.amount)}", match,
                                     "+ add" if i in self.add else ""))
        counts = {s: sum(1 for m in self.matches if m.status == s) for s in LABELS}
        self.v_note.configure(text=(
            f"{len(self.matches)} lines: {counts['matched']} matched, {counts['possible']} possible, "
            f"{counts['new']} not in expenses, {counts['money in']} money in (kept for Sales). Double-click a line to "
            f"add it as a draft expense or not. OK adds {len(self.add)} draft(s)."
            if self.matches else "No statement lines could be read. For a CSV, choose the Date, Description and "
                                 "Amount (or Debit / Credit) columns above."))

    def _toggle(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        i = int(iid)
        if self.matches[i].status == "money in":
            return
        self.add.symmetric_difference_update({i})
        self.fill()

    def save_csv(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Save statement rows as CSV", defaultextension=".csv",
                                            initialfile=f"{self.path.stem} rows.csv",
                                            filetypes=[("CSV files", "*.csv")])
        if path:
            bank.write_csv([m.line for m in self.matches], path)
            self.app.status(f"Saved {len(self.matches)} statement rows to {Path(path).name}")

    def apply(self) -> bool:
        now = datetime.now().isoformat(timespec="seconds")
        added = 0
        for i in sorted(self.add):
            ln = self.matches[i].line
            vendor = bank.vendor_from(ln.description)
            self.book.expenses.append(Expense(
                expense_id=expenses.next_expense_id(self.book.expenses), expense_date=ln.line_date, vendor=vendor,
                category=expenses.last_category_for(self.book.expenses, vendor), amount=-ln.amount,
                payment_method="Card", memo=f"From bank statement: {ln.description}"[:200], status="Draft",
                created=now, account_id=self.accounts[max(self.cb_acct.current(), 0)][0]))
            added += 1
        if added and not messagebox.askyesno("Bank Statement", f"Add {added} draft expense(s)? Review and approve "
                                             "them on the Expenses tab.", parent=self):
            del self.book.expenses[-added:]
            return False
        if self.is_csv and self.headers:   # remember this bank's columns once the import is accepted
            saved = dict(self.app.settings.data.get("bank_columns") or {})
            saved["|".join(self.headers)] = {**self._columns(), "out_is_positive": int(self.v_flip.get())}
            self.app.settings.data["bank_columns"] = saved
            self.app.settings.save()
        self.result = added
        return True
