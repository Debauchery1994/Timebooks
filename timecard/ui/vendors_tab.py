"""Vendors tab (1.4): the vendor list and Unknown vendors (assign a vendor; receipts move to its folder). Vendor checks
are written on the Pay & Checks tab (VendorChecksPanel), which uses PayBillsDialog and AccountsDialog from here."""
from __future__ import annotations

import tkinter as tk
from datetime import date
from decimal import Decimal
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import vendor_payments as vp
from .. import vendors
from ..models import Account, Vendor
from .common import ModalDialog, fmt_money, log, parse_date, parse_money, style_tree
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class AccountsDialog(ModalDialog):
    """Name the bank accounts, cards and cash money is paid from; choose which account checks are written from; see
    money out per account this year. Changes are kept on the book directly (it's a list editor)."""

    def __init__(self, master, book, app: "App"):
        self.book, self.app = book, app
        super().__init__(master, "Accounts")

    def body(self, f):
        cols = (("name", "Account", 200), ("kind", "Type", 100), ("last4", "Last 4", 60), ("out", "Money out", 110))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=8)
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c == "out" else "w")
        style_tree(self.tree)
        self.tree.grid(row=0, column=0, columnspan=4, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._load())
        self.v_name, self.v_kind, self.v_last4 = tk.StringVar(), tk.StringVar(value="Checking"), tk.StringVar()
        ttk.Label(f, text="Name").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(f, textvariable=self.v_name, width=26).grid(row=1, column=1, sticky="w", pady=(8, 2))
        ttk.Label(f, text="Type").grid(row=1, column=2, sticky="w", padx=(10, 4), pady=(8, 2))
        ttk.Combobox(f, textvariable=self.v_kind, values=list(vendors.ACCOUNT_KINDS), state="readonly",
                     width=12).grid(row=1, column=3, sticky="w", pady=(8, 2))
        ttk.Label(f, text="Last 4 digits").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.v_last4, width=8).grid(row=2, column=1, sticky="w", pady=2)
        b = ttk.Frame(f)
        b.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(b, text="Add", command=self.add).pack(side="left")
        ttk.Button(b, text="Save", command=self.save).pack(side="left", padx=6)
        ttk.Button(b, text="Not used any more", command=self.retire).pack(side="left")
        ttk.Label(f, text="Checks are written from").grid(row=4, column=0, sticky="w", pady=(12, 2))
        self.cb_checks = ttk.Combobox(f, state="readonly", width=26)
        self.cb_checks.grid(row=4, column=1, columnspan=2, sticky="w", pady=(12, 2))
        self.cb_checks.bind("<<ComboboxSelected>>", lambda e: self._set_checks_account())
        ttk.Label(f, text="Money out is this year's approved expenses and checks. Balances come with reconciling "
                  "(1.5). Only the last 4 digits are kept, never the full number.", style="Muted.TLabel",
                  wraplength=460).grid(row=5, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.fill()
        return self.tree

    def _active(self) -> list[Account]:
        return sorted((a for a in self.book.accounts.values() if a.active), key=lambda a: a.name.lower())

    def fill(self) -> None:
        totals = vendors.account_totals(self.book, self.book.company.year)
        self.tree.delete(*self.tree.get_children())
        for a in self._active():
            out = totals.get(a.account_id)
            self.tree.insert("", "end", iid=a.account_id, values=(a.name, a.kind, a.last4,
                                                                 fmt_money(out.money_out) if out else ""))
        accts = self._active()
        self.cb_checks["values"] = ["(not set)"] + [a.name for a in accts]
        cur = self.book.accounts.get(self.book.company.checks_account_id)
        self.cb_checks.set(cur.name if cur else "(not set)")

    def _load(self) -> None:
        a = self.book.accounts.get((self.tree.selection() or [""])[0])
        if a:
            self.v_name.set(a.name)
            self.v_kind.set(a.kind)
            self.v_last4.set(a.last4)

    def _values(self) -> tuple[str, str, str] | None:
        name = self.v_name.get().strip()
        last4 = "".join(ch for ch in self.v_last4.get() if ch.isdigit())[-4:]
        if not name:
            messagebox.showwarning("Accounts", "Enter a name for the account, e.g. Business checking.", parent=self)
            return None
        return name, self.v_kind.get() or "Checking", last4

    def add(self) -> None:
        v = self._values()
        if v:
            aid = vendors.next_account_id(self.book.accounts)
            self.book.accounts[aid] = Account(aid, *v)
            if not self.book.company.checks_account_id and v[1] == "Checking":
                self.book.company.checks_account_id = aid
            self.app.mark_dirty()
            self.fill()

    def save(self) -> None:
        a = self.book.accounts.get((self.tree.selection() or [""])[0])
        v = self._values()
        if a and v:
            a.name, a.kind, a.last4 = v
            self.app.mark_dirty()
            self.fill()

    def retire(self) -> None:
        a = self.book.accounts.get((self.tree.selection() or [""])[0])
        if a and messagebox.askyesno("Accounts", f"Stop offering {a.name}? Expenses already paid from it keep it.",
                                     parent=self):
            a.active = False
            self.app.mark_dirty()
            self.fill()

    def _set_checks_account(self) -> None:
        accts = self._active()
        i = self.cb_checks.current() - 1
        self.book.company.checks_account_id = accts[i].account_id if 0 <= i < len(accts) else ""
        self.app.mark_dirty()

    def apply(self) -> bool:
        return True


class PayBillsDialog(ModalDialog):
    """Write a check to a vendor: tick the bills it pays (or type an amount and memo). result = dict for
    vendor_payments.record_payment (built in apply, before the window closes)."""

    def __init__(self, master, book, vendor: Vendor):
        self.book, self.vendor = book, vendor
        self.bills = vp.unpaid_bills(book, vendor)
        self.ticked: set[str] = set()
        super().__init__(master, f"Pay {vendor.name}")

    def body(self, f):
        ttk.Label(f, text=f"Check #{vp.next_check_number(self.book)} to {self.vendor.name}",
                  style="Bold.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        self.tree = ttk.Treeview(f, columns=("pay", "date", "memo", "amount"), show="headings", height=7)
        for c, h, w in (("pay", "Pay", 50), ("date", "Date", 90), ("memo", "Bill", 240), ("amount", "Amount", 90)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c == "amount" else "center" if c == "pay" else "w")
        style_tree(self.tree)
        self.tree.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=(6, 4))
        self.tree.bind("<Double-1>", self._toggle)
        for e in self.bills:
            self.tree.insert("", "end", iid=e.expense_id, values=("", f"{e.expense_date:%m/%d/%Y}",
                                                                 e.memo or e.category, fmt_money(e.amount)))
        ttk.Label(f, text=("Double-click the bills this check pays (expenses marked 'paid by check' with no check yet)."
                           if self.bills else "No unpaid bills for this vendor: type the amount and a memo."),
                  style="Muted.TLabel", wraplength=460).grid(row=2, column=0, columnspan=4, sticky="w")
        self.e_date, self.e_amount, self.e_memo = ttk.Entry(f, width=14), ttk.Entry(f, width=14), ttk.Entry(f, width=40)
        self.e_date.insert(0, date.today().isoformat())
        self.cb_cat = ttk.Combobox(f, values=self.book.categories, width=24)
        self.cb_cat.set(self.vendor.default_category or "")
        accts = sorted((a for a in self.book.accounts.values() if a.active), key=lambda a: a.name.lower())
        self.accounts = [("", "(checks account)")] + [(a.account_id, a.name) for a in accts]
        self.cb_acct = ttk.Combobox(f, values=[n for _i, n in self.accounts], state="readonly", width=24)
        self.cb_acct.current(0)
        for r, (label, w) in enumerate((("Check date", self.e_date), ("Amount", self.e_amount), ("Memo", self.e_memo),
                                        ("Category (no bills)", self.cb_cat), ("Paid from", self.cb_acct)), start=3):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 8))
            w.grid(row=r, column=1, columnspan=3, sticky="w", pady=2)
        return self.e_amount if not self.bills else self.tree

    def _toggle(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.ticked.symmetric_difference_update({iid})
        self.tree.set(iid, "pay", "✔" if iid in self.ticked else "")
        total = sum((e.amount for e in self.bills if e.expense_id in self.ticked), Decimal("0"))
        self.e_amount.delete(0, "end")
        if total:
            self.e_amount.insert(0, f"{total:.2f}")

    def apply(self) -> bool:
        try:
            d = parse_date(self.e_date.get())
            amount = parse_money(self.e_amount.get() or "0")
        except ValueError as exc:
            messagebox.showwarning("Pay Bills", str(exc), parent=self)
            return False
        bills = [e for e in self.bills if e.expense_id in self.ticked]
        if amount <= 0:
            messagebox.showwarning("Pay Bills", "Enter the check amount (or double-click the bills it pays).",
                                   parent=self)
            return False
        if bills and sum((b.amount for b in bills), Decimal("0")) != amount:
            messagebox.showwarning("Pay Bills", "The amount must equal the bills ticked.", parent=self)
            return False
        self.result = dict(amount=amount, check_date=d, memo=self.e_memo.get().strip(), bills=bills,
                           account_id=self.accounts[max(self.cb_acct.current(), 0)][0],
                           category=self.cb_cat.get().strip())
        return True


class VendorsTab(PinLockable, ttk.Frame):
    pin_key = "vendors"
    pin_what = "Vendor records"
    pin_purpose = "view vendors or write vendor checks"

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.selected: str | None = None
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self._build_list(nb)
        self._build_unknown(nb)
        nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh())
        self.nb = nb

    @property
    def book(self):
        return self.app.book

    # ----- the vendor list
    def _build_list(self, nb) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="  Vendors  ")
        left = ttk.Frame(f)
        left.pack(side="left", fill="both", expand=True)
        cols = (("name", "Vendor", 220), ("cat", "Default category", 160), ("t1099", "1099", 50),
                ("phone", "Phone", 110))
        self.tree = ttk.Treeview(left, columns=[c[0] for c in cols], show="headings")
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="center" if c == "t1099" else "w")
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.v_show_old = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Show vendors no longer used", variable=self.v_show_old,
                        command=self.refresh).pack(anchor="w", pady=(4, 0))
        form = ttk.LabelFrame(f, text="Vendor", padding=10)
        form.pack(side="left", fill="y", padx=(12, 0))
        self.v = {k: tk.StringVar() for k in ("name", "address1", "address2", "phone", "email", "cat", "notes")}
        self.v_1099, self.v_w9 = tk.BooleanVar(), tk.BooleanVar()
        r = 0
        for key, label in (("name", "Name"), ("address1", "Address line 1"), ("address2", "City, State ZIP"),
                           ("phone", "Phone"), ("email", "Email"), ("notes", "Notes")):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(form, textvariable=self.v[key], width=30).grid(row=r, column=1, sticky="ew", pady=2)
            r += 1
        ttk.Label(form, text="Default category").grid(row=r, column=0, sticky="w", pady=2)
        self.cb_cat = ttk.Combobox(form, textvariable=self.v["cat"], width=28)
        self.cb_cat.grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        ttk.Checkbutton(form, text="Send a 1099 (services)", variable=self.v_1099).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(form, text="W-9 on file (kept on paper)", variable=self.v_w9).grid(row=r, column=1, sticky="w")
        r += 1
        b = ttk.Frame(form, padding=(0, 8, 0, 0))
        b.grid(row=r, column=0, columnspan=2, sticky="w")
        ttk.Button(b, text="New", command=self.clear_form).pack(side="left")
        ttk.Button(b, text="Add", command=self.add).pack(side="left", padx=4)
        ttk.Button(b, text="Save", style="Accent.TButton", command=self.save).pack(side="left")
        r += 1
        ttk.Button(form, text="No longer used / Use again", command=self.toggle_active).grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _on_select(self, _e=None) -> None:
        v = self.book.vendors.get((self.tree.selection() or [""])[0]) if self.book else None
        if v is None:
            return
        self.selected = v.vendor_id
        for key, val in (("name", v.name), ("address1", v.address1), ("address2", v.address2), ("phone", v.phone),
                         ("email", v.email), ("cat", v.default_category), ("notes", v.notes)):
            self.v[key].set(val)
        self.v_1099.set(v.is_1099)
        self.v_w9.set(v.w9_on_file)

    def clear_form(self) -> None:
        self.selected = None
        for var in self.v.values():
            var.set("")
        self.v_1099.set(False)
        self.v_w9.set(False)
        if self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())

    def _form(self) -> dict | None:
        name = self.v["name"].get().strip()
        if not name:
            messagebox.showwarning("Vendors", "Enter the vendor's name.", parent=self)
            return None
        return dict(name=name, address1=self.v["address1"].get().strip(), address2=self.v["address2"].get().strip(),
                    phone=self.v["phone"].get().strip(), email=self.v["email"].get().strip(),
                    default_category=self.v["cat"].get().strip(), notes=self.v["notes"].get().strip(),
                    is_1099=bool(self.v_1099.get()), w9_on_file=bool(self.v_w9.get()))

    def add(self) -> None:
        if self.book is None or self.locked:
            return
        vals = self._form()
        if vals is None:
            return
        same = vendors.find_vendor(self.book.vendors, vals["name"])
        if same is not None:
            messagebox.showwarning("Vendors", f"{same.name} is already on the list.", parent=self)
            return
        vid = vendors.next_vendor_id(self.book.vendors)
        self.book.vendors[vid] = v = Vendor(vid, **vals)
        linked = vendors.link_same_name(self.book, v)
        self._category_known(v.default_category)
        self.app.mark_dirty()
        self.selected = vid
        self.refresh()
        self.app.status(f"Added {v.name}" + (f"; {linked} expense(s) linked to it" if linked else "") + ".")

    def save(self) -> None:
        v = self.book.vendors.get(self.selected or "") if self.book and not self.locked else None
        if v is None:
            self.add()
            return
        vals = self._form()
        if vals is None:
            return
        other = vendors.find_vendor(self.book.vendors, vals["name"])
        if other is not None and other.vendor_id != v.vendor_id:
            messagebox.showwarning("Vendors", f"{other.name} is already on the list. Use another name, or assign "
                                   "expenses to that vendor instead.", parent=self)
            return
        old_name = v.name
        for k, val in vals.items():
            setattr(v, k, val)
        if v.name != old_name:   # keep its expenses and their receipt folders in step with the new name
            mine = [e for e in self.book.expenses if e.vendor_id == v.vendor_id]
            problems = vendors.assign_vendor(self.book, mine, v)
            if problems:
                log.warning("Receipts not moved on rename: %s", problems)
        vendors.link_same_name(self.book, v)
        self._category_known(v.default_category)
        self.app.mark_dirty()
        self.refresh()
        self.app.status(f"Saved {v.name}.{self.app.save_hint()}")

    def _category_known(self, cat: str) -> None:
        if cat and cat not in self.book.categories:
            self.book.categories.append(cat)

    def toggle_active(self) -> None:
        v = self.book.vendors.get(self.selected or "") if self.book and not self.locked else None
        if v is not None:
            v.active = not v.active
            self.app.mark_dirty()
            self.refresh()

    # ----- unknown vendors
    def _build_unknown(self, nb) -> None:
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text="  Unknown vendors  ")
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.v_blank_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Only expenses with no vendor at all", variable=self.v_blank_only,
                        command=self.refresh).pack(side="left")
        ttk.Button(top, text="Assign vendor to selected…", style="Accent.TButton",
                   command=self.assign_selected).pack(side="right")
        cols = (("date", "Date", 90), ("vendor", "Vendor as entered", 220), ("amount", "Amount", 90),
                ("receipt", "Receipt", 70), ("memo", "Memo", 300))
        self.utree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for c, h, w in cols:
            self.utree.heading(c, text=h)
            self.utree.column(c, width=w, anchor="e" if c == "amount" else "w")
        style_tree(self.utree)
        self.utree.pack(fill="both", expand=True, pady=(6, 0))
        self.utree.bind("<Double-1>", lambda e: self.assign_selected())
        ttk.Label(f, text="Expenses with no vendor, or a vendor name that isn't on the list yet. Select one or more "
                  "(Ctrl / Shift-click) and assign a vendor: they get its name (and default category if they have "
                  "none), and their receipts move into that vendor's folder.", style="Muted.TLabel",
                  wraplength=760).pack(anchor="w", pady=(6, 0))

    def assign_selected(self) -> None:
        if self.book is None or self.locked:
            return
        items = [e for e in self.book.expenses if e.expense_id in set(self.utree.selection())]
        if not items:
            messagebox.showinfo("Unknown vendors", "Select the expenses first.", parent=self)
            return
        result = ChooseVendorDialog(self, self.book, items[0].vendor).show()
        if result is None:
            return
        v = self.book.vendors.get(result) if isinstance(result, str) else None
        if v is None:   # a new vendor was typed
            vid = vendors.next_vendor_id(self.book.vendors)
            v = self.book.vendors[vid] = Vendor(vid, result["name"], default_category=result["category"])
        problems = vendors.assign_vendor(self.book, items, v)
        self.app.mark_dirty()
        self.app.expenses_tab.refresh()
        self.refresh()
        msg = f"{len(items)} expense(s) now under {v.name}."
        if problems:
            msg += f" {len(problems)} receipt(s) couldn't be moved (see the log)."
            log.warning("Receipts not moved: %s", problems)
        self.app.status(msg + self.app.save_hint())

    # ----- refresh
    def refresh(self) -> None:
        if self.book is None:
            return
        self.cb_cat["values"] = self.book.categories
        self.tree.delete(*self.tree.get_children())
        for v in sorted(self.book.vendors.values(), key=lambda v: v.name.lower()):
            if v.active or self.v_show_old.get():
                self.tree.insert("", "end", iid=v.vendor_id, tags=() if v.active else ("former",),
                                 values=(v.name, v.default_category, "Yes" if v.is_1099 else "", v.phone))
        if self.selected and self.tree.exists(self.selected):
            self.tree.selection_set(self.selected)
        self.utree.delete(*self.utree.get_children())
        for e in vendors.unknown_vendor_expenses(self.book, self.v_blank_only.get()):
            self.utree.insert("", "end", iid=e.expense_id, values=(
                f"{e.expense_date:%m/%d/%Y}", e.vendor or "(none)", fmt_money(e.amount),
                "Yes" if e.receipt_file else "", e.memo))


class ChooseVendorDialog(ModalDialog):
    """Pick a vendor for unknown-vendor expenses, or type a new one. result = a VendorID, or
    {"name", "category"} for a new vendor (built in apply)."""

    def __init__(self, master, book, hint: str):
        self.book, self.hint = book, hint
        super().__init__(master, "Assign vendor")

    def body(self, f):
        self.names = sorted((v for v in self.book.vendors.values() if v.active), key=lambda v: v.name.lower())
        ttk.Label(f, text="Vendor").grid(row=0, column=0, sticky="w", pady=3, padx=(0, 8))
        self.cb = ttk.Combobox(f, values=[v.name for v in self.names], width=32)
        self.cb.set(self.hint or "")
        self.cb.grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Category for a new vendor").grid(row=1, column=0, sticky="w", pady=3, padx=(0, 8))
        self.cb_cat = ttk.Combobox(f, values=self.book.categories, width=32)
        self.cb_cat.grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Pick one from the list, or type a new name to add it as a vendor.", style="Muted.TLabel",
                  wraplength=360).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        return self.cb

    def apply(self) -> bool:
        name = self.cb.get().strip()
        if not name:
            messagebox.showwarning("Assign vendor", "Choose or type a vendor.", parent=self)
            return False
        known = vendors.find_vendor(self.book.vendors, name)
        self.result = known.vendor_id if known else {"name": name, "category": self.cb_cat.get().strip()}
        return True
