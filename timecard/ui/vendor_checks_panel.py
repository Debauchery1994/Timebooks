"""Vendor Checks: the Pay & Checks tab's second page (1.4). All checks live in Pay & Checks ("keep money with money"):
pay a vendor's bills by check on the shared checkbook, print, void, and the combined check register. Vendors and
unknown vendors are managed on the Vendors tab."""
from __future__ import annotations

from datetime import date
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import vendor_payments as vp
from .common import fmt_money, open_file, style_tree
from .vendors_tab import AccountsDialog, PayBillsDialog

if TYPE_CHECKING:
    from .app import App


class VendorChecksPanel(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, padding=4)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Vendor").pack(side="left")
        self.cb_vendor = ttk.Combobox(top, state="readonly", width=30)
        self.cb_vendor.pack(side="left", padx=(6, 6))
        ttk.Button(top, text="Pay Bills…", style="Accent.TButton", command=self.pay).pack(side="left")
        for text, cmd in (("Print Checks…", self.print_checks), ("Void Check…", self.void_check),
                          ("Check Register…", self.print_register), ("Accounts…", self.open_accounts)):
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=(6, 0))
        cols = (("num", "Check #", 80), ("date", "Date", 90), ("payee", "Payee", 220), ("amount", "Amount", 100),
                ("acct", "Paid from", 150), ("memo", "Memo", 240), ("void", "", 60))
        self.tree = ttk.Treeview(self, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c == "amount" else "w")
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True, pady=(8, 0))
        ttk.Label(self, text="Vendor checks share the check numbers with payroll checks. Add vendors and assign "
                  "unknown vendors on the Vendors tab. Printing uses the check layout in Company Settings > Checks.",
                  style="Muted.TLabel", wraplength=860).pack(anchor="w", pady=(6, 0))
        self.vendor_ids: list[str] = []

    @property
    def book(self):
        return self.app.book

    @property
    def locked(self) -> bool:
        return self.app.payroll_tab.locked

    def refresh(self) -> None:
        if self.book is None:
            return
        active = sorted((v for v in self.book.vendors.values() if v.active), key=lambda v: v.name.lower())
        cur = self.vendor_ids[self.cb_vendor.current()] if 0 <= self.cb_vendor.current() < len(self.vendor_ids) else ""
        self.vendor_ids = [v.vendor_id for v in active]
        self.cb_vendor["values"] = [v.name for v in active]
        if cur in self.vendor_ids:
            self.cb_vendor.current(self.vendor_ids.index(cur))
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for p in sorted(self.book.vendor_payments, key=lambda p: p.check_number, reverse=True):
            acct = self.book.accounts.get(p.account_id)
            self.tree.insert("", "end", iid=str(p.check_number), tags=("former",) if p.voided else (),
                             values=(p.check_number, f"{p.check_date:%m/%d/%Y}", p.payee, fmt_money(p.amount),
                                     acct.name if acct else "", p.memo, "VOID" if p.voided else ""))
        for iid in keep:
            if self.tree.exists(iid):
                self.tree.selection_add(iid)

    def pay(self) -> None:
        if self.book is None or self.locked:
            return
        i = self.cb_vendor.current()
        v = self.book.vendors.get(self.vendor_ids[i]) if 0 <= i < len(self.vendor_ids) else None
        if v is None:
            messagebox.showinfo("Pay Bills", "Choose the vendor first (add vendors on the Vendors tab).", parent=self)
            return
        r = PayBillsDialog(self, self.book, v).show()
        if r is None:
            return
        try:
            p = vp.record_payment(self.book, v, **r)
        except ValueError as exc:
            messagebox.showwarning("Pay Bills", str(exc), parent=self)
            return
        self.app.mark_dirty()
        if self.book.path is not None and not self.app.save():   # the check is on record before it can be printed
            self.app.status("The check is recorded but the workbook couldn't be saved yet; save before printing.",
                            error=True)
        self.app.expenses_tab.refresh()
        self.refresh()
        self.tree.selection_set(str(p.check_number))
        if messagebox.askyesno("Pay Bills", f"Check #{p.check_number} to {v.name} for {fmt_money(p.amount)} is "
                               "recorded. Print it now?", parent=self):
            self.print_checks()

    def _selected(self):
        nums = {int(i) for i in self.tree.selection()}
        return [p for p in self.book.vendor_payments if p.check_number in nums]

    def print_checks(self) -> None:
        if self.book is None or self.locked:
            return
        chosen = [p for p in self._selected() if not p.voided]
        if not chosen:
            messagebox.showinfo("Print Checks", "Select the vendor checks to print.", parent=self)
            return
        out_dir = self.app.output_dir()
        if out_dir is None:
            return
        sp = 1
        if (self.book.company.check_layout or "") in ("laser_3up", "manual_3up"):
            sp = self.app.ask_start_position(len(chosen))
            if sp is None:
                return
        name = f"Vendor checks {min(p.check_number for p in chosen)}-{max(p.check_number for p in chosen)}.pdf"
        try:
            out = vp.render_vendor_checks(chosen, self.book, out_dir / name, start_position=sp)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Print Checks", f"The checks couldn't be made:\n{exc}", parent=self)
            return
        open_file(out)
        self.app.status(f"Vendor checks PDF: {out.name}")

    def void_check(self) -> None:
        if self.book is None or self.locked:
            return
        chosen = [p for p in self._selected() if not p.voided]
        if len(chosen) != 1:
            messagebox.showinfo("Void Check", "Select one vendor check to void.", parent=self)
            return
        p = chosen[0]
        if not messagebox.askyesno("Void Check", f"Void check #{p.check_number} to {p.payee} for "
                                   f"{fmt_money(p.amount)}? The bills it paid become unpaid again; the number "
                                   "isn't reused.", parent=self):
            return
        vp.void_payment(self.book, p)
        self.app.mark_dirty()
        self.app.expenses_tab.refresh()
        self.refresh()

    def print_register(self) -> None:
        if self.book is None or self.locked:
            return
        out_dir = self.app.output_dir()
        if out_dir is None:
            return
        y = self.book.company.year
        open_file(vp.render_register(self.book, out_dir / f"Check register {y}.pdf", date(y, 1, 1), date(y, 12, 31)))

    def open_accounts(self) -> None:
        if self.book is not None and not self.locked:
            AccountsDialog(self, self.book, self.app).show()
            self.refresh()
