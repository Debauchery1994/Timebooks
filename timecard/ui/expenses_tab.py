"""Expenses tab: money the business spent, with receipts attached, filters, and totals by category; receipts can be
scanned, picked up from the scanner's folder or imported, read automatically, and approved on a review screen."""
from __future__ import annotations

import queue
import re
import tkinter as tk
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from .. import expenses, phone_upload, receipts_ocr, scanning
from ..models import Expense, Trip
from .common import MONTH_NAMES, FlowFrame, ModalDialog, fmt_money, log, open_file, parse_date, parse_money, style_tree
from .bank_dialog import BankImportDialog
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class ExpenseDialog(ModalDialog):
    """Add or edit one expense. result = the Expense, plus the receipt file chosen to attach (not filed yet)."""

    status = "Approved"   # what OK saves (the review screen can keep a scanned receipt as a Draft)
    repeats = "No"        # the Repeats choice, kept by apply() for after the form has closed

    def __init__(self, master, book, expense: Expense, title: str = "", receipt_source: str = ""):
        self.book, self.e = book, expense
        self.receipt_source = receipt_source
        super().__init__(master, title or ("Edit Expense" if expense.vendor or expense.amount else "Add Expense"))

    def body(self, f):
        e = self.e
        self.w: dict = {}

        def row(r, label, widget):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=r, column=1, sticky="ew", pady=3)
            return widget

        self.w["date"] = row(0, "Date", ttk.Entry(f, width=14))
        self.w["date"].insert(0, e.expense_date.isoformat())
        self.w["vendor"] = row(1, "Vendor (who)", ttk.Combobox(f, values=expenses.vendors(self.book.expenses),
                                                                width=34))
        self.w["vendor"].set(e.vendor)
        self.w["vendor"].bind("<FocusOut>", self._suggest_category)
        self.w["vendor"].bind("<<ComboboxSelected>>", self._suggest_category)
        self.w["category"] = row(2, "Category", ttk.Combobox(f, values=self.book.categories, width=34))
        self.w["category"].set(e.category)
        self.w["amount"] = row(3, "Amount paid", ttk.Entry(f, width=14))
        self.w["amount"].insert(0, f"{e.amount:.2f}" if e.amount else "")
        self.w["tax"] = row(4, "Tax included", ttk.Entry(f, width=14))
        self.w["tax"].insert(0, f"{e.tax:.2f}" if e.tax else "")
        self.w["method"] = row(5, "Paid by", ttk.Combobox(f, values=list(expenses.PAYMENT_METHODS), state="readonly",
                                                         width=16))
        self.w["method"].set(e.payment_method if e.payment_method in expenses.PAYMENT_METHODS else "Other")
        self.w["check"] = row(6, "Check #", ttk.Entry(f, width=14))
        self.w["check"].insert(0, str(e.check_number or ""))
        self.w["memo"] = row(7, "Memo", ttk.Entry(f, width=36))
        self.w["memo"].insert(0, e.memo)
        self.people = [(None, "The business")] + [(p.employee_id, p.display_name) for p in
                                                   sorted(self.book.employees.values(), key=lambda p: p.display_name)
                                                   if p.active or p.employee_id == e.person_id]
        self.w["account"] = row(10, "On whose account", ttk.Combobox(f, values=[n for _i, n in self.people],
                                                                    state="readonly", width=24))
        self.w["account"].current(next((i for i, (pid, _n) in enumerate(self.people) if pid == (e.person_id or None)),
                                       0))
        accts = sorted((a for a in self.book.accounts.values() if a.active or a.account_id == e.account_id),
                       key=lambda a: a.name.lower())
        self.accounts = [("", "(not said)")] + [(a.account_id, a.name + (f" ··{a.last4}" if a.last4 else ""))
                                               for a in accts]
        self.w["account_paid"] = row(11, "Paid from", ttk.Combobox(f, values=[n for _i, n in self.accounts],
                                                                    state="readonly", width=24))
        self.w["account_paid"].current(next((i for i, (aid, _n) in enumerate(self.accounts)
                                             if aid == e.account_id), 0))
        self.w["repeats"] = ttk.Combobox(f, values=["No"] + list(expenses.REPEATS), state="readonly", width=12)
        linked = next((r for r in self.book.recurring if r.recurring_id == e.recurring_id), None)
        self.w["repeats"].set(linked.every if linked else "No")
        if linked is not None:
            self.w["repeats"].configure(state="disabled")   # change it under Recurring…
        row(9, "Repeats", self.w["repeats"])
        rf = ttk.Frame(f)
        shown = Path(self.receipt_source or e.receipt_file).name if (self.receipt_source or e.receipt_file) else ""
        self.v_receipt = ttk.Label(rf, text=shown or "No receipt attached", style="Muted.TLabel")
        self.v_receipt.pack(side="left")
        ttk.Button(rf, text="Attach…", command=self._choose_receipt).pack(side="left", padx=(8, 0))
        row(8, "Receipt", rf)
        ttk.Label(f, text="Amount is the total paid, tax included. Receipts are copied into a Receipts folder next to "
                  "the workbook.", style="Muted.TLabel", wraplength=380).grid(row=13, column=0, columnspan=2,
                                                                             sticky="w", pady=(6, 0))
        return self.w["vendor"] if not e.vendor else self.w["amount"]

    def _suggest_category(self, _e=None) -> None:
        if not self.w["category"].get().strip():
            self.w["category"].set(expenses.last_category_for(self.book.expenses, self.w["vendor"].get()))

    def _choose_receipt(self) -> None:
        types = " ".join(f"*{s}" for s in expenses.RECEIPT_TYPES)
        path = filedialog.askopenfilename(parent=self, title="Attach a receipt",
                                          filetypes=[("Pictures and PDFs", types), ("All files", "*.*")])
        if path:
            self.receipt_source = path
            self.v_receipt.configure(text=Path(path).name)

    def apply(self) -> bool:
        w = self.w
        for key in ("amount", "tax"):
            if re.search(r",\d{1,2}\s*$", w[key].get()):
                messagebox.showwarning("Expense", f"Use a dot for cents, e.g. 12.50 (not {w[key].get().strip()}).",
                                       parent=self)
                return False
        try:
            d = parse_date(w["date"].get())
            amount = parse_money(w["amount"].get() or "0")
            tax = parse_money(w["tax"].get() or "0")
            check = int(w["check"].get()) if w["check"].get().strip() else None
        except ValueError as exc:
            messagebox.showwarning("Expense", str(exc) or "Check the date, amounts and check number.", parent=self)
            return False
        vendor, category = w["vendor"].get().strip(), w["category"].get().strip()
        problems = [m for bad, m in ((not vendor, "Enter who was paid (the vendor)."),
                                     (amount <= 0, "Enter the amount paid."),
                                     (tax < 0 or tax > amount, "Tax can't be more than the amount paid."),
                                     (not category, "Choose a category.")) if bad]
        if problems:
            messagebox.showwarning("Expense", "\n".join(problems), parent=self)
            return False
        self.repeats = w["repeats"].get()
        probe = Expense(self.e.expense_id, d, vendor=vendor, amount=amount)
        same = expenses.possible_duplicates(probe, self.book.expenses)
        if same and not messagebox.askyesno(
                "Possible duplicate", "This looks like an expense that's already entered:\n\n" + "\n".join(
                    f"  {o.expense_date:%m/%d/%Y}  {o.vendor or '(no vendor)'}  {fmt_money(o.amount)}"
                    + ("  (draft)" if o.status == "Draft" else "") for o in same[:5])
                + "\n\nSave it anyway?", parent=self):
            return False
        self.result = Expense(
            expense_id=self.e.expense_id, expense_date=d, vendor=vendor, category=category, amount=amount, tax=tax,
            payment_method=w["method"].get(), check_number=check if w["method"].get() == "Check" else None,
            memo=w["memo"].get().strip(), receipt_file=self.e.receipt_file, status=self.status,
            vendor_id=self.e.vendor_id, created=self.e.created or datetime.now().isoformat(timespec="seconds"),
            recurring_id=self.e.recurring_id,
            person_id=self.people[max(w["account"].current(), 0)][0] or "",
            account_id=self.accounts[max(w["account_paid"].current(), 0)][0])
        return True


class ReceiptReviewDialog(ExpenseDialog):
    """The receipt on the left, what was read from it on the right; fields the reader wasn't sure of say "check this".
    OK approves the expense; "Keep as draft" saves it to review later."""

    # review field name (receipts_ocr) -> ExpenseDialog field
    FIELD_MAP = {"vendor": "vendor", "date": "date", "total": "amount", "tax": "tax", "payment_method": "method"}

    def __init__(self, master, book, expense: Expense, receipt: Path, fields: receipts_ocr.ReceiptFields | None,
                 receipt_source: str = ""):
        self.receipt, self.fields = receipt, fields
        self._img = None
        super().__init__(master, book, expense, title="Review Scanned Receipt", receipt_source=receipt_source)

    def body(self, f):
        left, right = ttk.Frame(f), ttk.Frame(f)
        left.grid(row=0, column=0, sticky="n", padx=(0, 16))
        right.grid(row=0, column=1, sticky="n")
        self._show_picture(left)
        first = super().body(right)
        review = self.fields.needs_review() if self.fields is not None else list(self.FIELD_MAP)
        rows = {"date": 0, "vendor": 1, "amount": 3, "tax": 4, "method": 5}
        for name in review:
            key = self.FIELD_MAP.get(name)
            if key in rows:
                ttk.Label(right, text="check this", style="Warn.TLabel").grid(row=rows[key], column=2, sticky="w",
                                                                           padx=(6, 0))
        if not self.w["category"].get():
            ttk.Label(right, text="choose", style="Warn.TLabel").grid(row=2, column=2, sticky="w", padx=(6, 0))
        warn = ("Couldn't read this receipt automatically; fill it in from the picture." if self.fields is None else
                "Fields marked \"check this\" weren't read for sure. " + " ".join(self.fields.warnings))
        ttk.Label(right, text=warn, style="Muted.TLabel", wraplength=380).grid(row=16, column=0, columnspan=3,
                                                                           sticky="w", pady=(6, 0))
        probe = Expense(self.e.expense_id, self.e.expense_date, vendor=self.e.vendor, amount=self.e.amount)
        same = expenses.possible_duplicates(probe, self.book.expenses)
        if same:
            o = same[0]
            ttk.Label(right, text=f"⚠ Possible duplicate of {o.vendor or '(no vendor)'} {fmt_money(o.amount)} on "
                      f"{o.expense_date:%m/%d/%Y}" + (" (draft)" if o.status == "Draft" else "")
                      + ". If it's the same receipt, Cancel and delete this draft.", style="Warn.TLabel",
                      wraplength=380).grid(row=18, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.v_draft = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="Keep as draft (review later; drafts aren't in the totals)",
                        variable=self.v_draft).grid(row=17, column=0, columnspan=3, sticky="w", pady=(6, 0))
        return first

    def _show_picture(self, frame) -> None:
        try:
            from PIL import ImageTk
            img = receipts_ocr._load_image(self.receipt, pdf_dpi=110)   # the reader's own loader (PDF page 1 too)
            img.thumbnail((380, max(240, min(560, self.winfo_screenheight() - 330))))   # fits a small laptop
            self._img = ImageTk.PhotoImage(img)
            tk.Label(frame, image=self._img, bd=1, relief="solid").pack()
        except Exception as exc:  # noqa: BLE001 - the form still works without the picture
            log.warning("Could not show receipt %s: %s", self.receipt, exc)
            ttk.Label(frame, text="(The receipt picture can't be shown.)", style="Muted.TLabel").pack()
        ttk.Button(frame, text="Open full size", command=lambda: open_file(self.receipt)).pack(pady=(6, 0))

    def apply(self) -> bool:
        self.status = "Draft" if self.v_draft.get() else "Approved"
        return super().apply()


class TripDialog(ModalDialog):
    """Add or edit one trip in the Mileage log."""

    def __init__(self, master, book, trip: Trip):
        self.book, self.t = book, trip
        super().__init__(master, "Trip")

    def body(self, f):
        t = self.t
        self.people = [(p.employee_id, p.display_name) for p in sorted(self.book.employees.values(),
                                                                       key=lambda p: p.display_name)
                       if p.active or p.employee_id == t.person_id]
        self.w: dict = {}
        for r, (key, label, width) in enumerate((("date", "Date", 14), ("person", "Who drove", 26),
                                                  ("purpose", "Business purpose", 34), ("from", "From", 26),
                                                  ("to", "To", 26), ("miles", "Miles", 10), ("notes", "Notes", 34))):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            w = (ttk.Combobox(f, values=[n for _i, n in self.people], state="readonly", width=width)
                 if key == "person" else ttk.Entry(f, width=width))
            w.grid(row=r, column=1, sticky="w", pady=3)
            self.w[key] = w
        self.w["date"].insert(0, t.trip_date.isoformat())
        idx = next((i for i, (pid, _n) in enumerate(self.people) if pid == t.person_id), 0)
        if self.people:
            self.w["person"].current(idx)
        for key, val in (("purpose", t.purpose), ("from", t.from_place), ("to", t.to_place), ("notes", t.notes)):
            self.w[key].insert(0, val)
        self.w["miles"].insert(0, f"{t.miles:.2f}" if t.miles else "")
        ttk.Label(f, text=f"At ${t.rate:.3f} a mile (Company Settings > Pay; confirm each year with your tax "
                  "professional).", style="Muted.TLabel", wraplength=360).grid(row=7, column=0, columnspan=2,
                                                                              sticky="w", pady=(6, 0))
        return self.w["purpose"]

    def apply(self) -> bool:
        w = self.w
        try:
            d = parse_date(w["date"].get())
            miles = parse_money(w["miles"].get() or "0")
        except ValueError as exc:
            messagebox.showwarning("Trip", str(exc), parent=self)
            return False
        if miles <= 0 or not w["purpose"].get().strip() or not self.people:
            messagebox.showwarning("Trip", "Enter the business purpose and the miles (add the driver on the Employees "
                                   "tab first if they aren't listed).", parent=self)
            return False
        self.result = Trip(trip_id=self.t.trip_id, trip_date=d, person_id=self.people[w["person"].current()][0],
                           purpose=w["purpose"].get().strip(), from_place=w["from"].get().strip(),
                           to_place=w["to"].get().strip(), miles=miles, rate=self.t.rate,
                           notes=w["notes"].get().strip())
        return True


class MileageDialog(ModalDialog):
    """The Mileage log for the workbook's year, and each person's account (miles, mileage, expenses on account)."""

    def __init__(self, master, book, app):
        self.book, self.app = book, app
        super().__init__(master, "Mileage Log")

    def body(self, f):
        cols = (("date", "Date", 90), ("person", "Who", 160), ("purpose", "Purpose", 220), ("route", "From → To", 200),
                ("miles", "Miles", 70), ("amount", "Amount", 90))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=12)
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c in ("miles", "amount") else "w")
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.edit())
        b = ttk.Frame(f, padding=(0, 8, 0, 4))
        b.pack(fill="x")
        ttk.Button(b, text="Add Trip…", style="Accent.TButton", command=self.add).pack(side="left")
        ttk.Button(b, text="Edit…", command=self.edit).pack(side="left", padx=6)
        ttk.Button(b, text="Delete", command=self.delete).pack(side="left")
        ttk.Label(f, text=f"Accounts for {self.book.company.year} (expenses on account leave out drafts)",
                  style="Muted.TLabel").pack(anchor="w", pady=(8, 2))
        self.atree = ttk.Treeview(f, columns=("who", "miles", "mileage", "expenses"), show="headings", height=5)
        for c, h, w in (("who", "Person", 200), ("miles", "Miles", 80), ("mileage", "Mileage", 100),
                        ("expenses", "Expenses on account", 150)):
            self.atree.heading(c, text=h)
            self.atree.column(c, width=w, anchor="w" if c == "who" else "e")
        style_tree(self.atree)
        self.atree.pack(anchor="w")
        self.fill()
        return self.tree

    def _person_name(self, pid: str) -> str:
        e = self.book.employees.get(pid)
        return (e.display_name + ("  (owner)" if (e.pay_type or "").lower() == "owner" else "")) if e else \
            (pid or "The business")

    def fill(self) -> None:
        year = self.book.company.year
        self.tree.delete(*self.tree.get_children())
        for t in sorted((t for t in self.book.trips if t.trip_date.year == year), key=lambda t: t.trip_date):
            route = " → ".join(x for x in (t.from_place, t.to_place) if x)
            who = self._person_name(t.person_id)
            self.tree.insert("", "end", iid=t.trip_id, values=(f"{t.trip_date:%m/%d/%Y}", who, t.purpose, route,
                                                               f"{t.miles:.2f}", fmt_money(t.amount)))
        self.atree.delete(*self.atree.get_children())
        for pid, (miles, mileage, spent) in sorted(expenses.account_totals(self.book.trips, self.book.expenses,
                                                                           year).items(),
                                                  key=lambda kv: self._person_name(kv[0]).lower()):
            self.atree.insert("", "end", values=(self._person_name(pid), f"{miles:.2f}", fmt_money(mileage),
                                                 fmt_money(spent)))

    def _sel(self) -> Trip | None:
        sel = self.tree.selection()
        return next((t for t in self.book.trips if sel and t.trip_id == sel[0]), None)

    def _edit(self, t: Trip, new: bool) -> None:
        result = TripDialog(self, self.book, t).show()
        if result is None:
            return
        if new:
            self.book.trips.append(result)
        else:
            self.book.trips[self.book.trips.index(t)] = result
        self.app.mark_dirty()
        self.fill()

    def add(self) -> None:
        rate = Decimal(str(self.book.company.mileage_rate or 0))
        self._edit(Trip(expenses.next_trip_id(self.book.trips), date.today(), rate=rate), new=True)

    def edit(self) -> None:
        t = self._sel()
        if t is not None:
            self._edit(t, new=False)

    def delete(self) -> None:
        t = self._sel()
        if t is not None and messagebox.askyesno("Mileage", f"Delete the trip on {t.trip_date:%m/%d/%Y} "
                                                 f"({t.miles:.1f} miles)?", parent=self):
            self.book.trips.remove(t)
            self.app.mark_dirty()
            self.fill()

    def apply(self) -> bool:
        return True


class RecurringDialog(ModalDialog):
    """The recurring expenses: stop or restart one, or skip its next time."""

    def __init__(self, master, book):
        self.book = book
        super().__init__(master, "Recurring Expenses")

    def body(self, f):
        cols = (("vendor", "Vendor", 200), ("amount", "Amount", 90), ("every", "Repeats", 90),
                ("next", "Next due", 100), ("state", "", 80))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=10)
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="e" if c == "amount" else "w")
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True)
        b = ttk.Frame(f, padding=(0, 8, 0, 0))
        b.pack(fill="x")
        ttk.Button(b, text="Stop / Restart", command=self.toggle).pack(side="left")
        ttk.Button(b, text="Skip next time", command=self.skip).pack(side="left", padx=6)
        ttk.Label(f, text="To make an expense repeat, add or edit it and choose Repeats. Each time one comes due it "
                  "is added as a draft for you to approve.", style="Muted.TLabel", wraplength=520).pack(anchor="w",
                                                                                                pady=(8, 0))
        self.fill()
        return self.tree

    def fill(self) -> None:
        keep = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for r in self.book.recurring:
            nxt = f"{r.next_date:%m/%d/%Y}" if r.next_date and r.active else ""
            self.tree.insert("", "end", iid=r.recurring_id, values=(r.vendor, fmt_money(r.amount), r.every, nxt,
                                                                    "" if r.active else "Stopped"))
        if keep and self.tree.exists(keep[0]):
            self.tree.selection_set(keep[0])

    def _sel(self):
        sel = self.tree.selection()
        return next((r for r in self.book.recurring if sel and r.recurring_id == sel[0]), None)

    def toggle(self) -> None:
        r = self._sel()
        if r is not None:
            r.active = not r.active
            if r.active and (r.next_date is None or r.next_date < date.today()):
                # restarting: the next one is due from today on, not every missed period
                while r.next_date is None or r.next_date < date.today():
                    r.next_date = expenses.add_period(r.next_date or date.today(), r.every, r.day or None)
            self.fill()

    def skip(self) -> None:
        r = self._sel()
        if r is not None and r.active and r.next_date:
            r.next_date = expenses.add_period(r.next_date, r.every, r.day or None)
            self.fill()

    def apply(self) -> bool:
        return True


class PhoneUploadDialog(tk.Toplevel):
    """Shows the QR code / address for the phone and adds each photo as a Draft as it arrives; the upload page stops
    when this window closes."""

    def __init__(self, master, on_receipt):
        super().__init__(master)
        self.title("Receipts from your phone")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.on_receipt = on_receipt
        self.arrived: queue.Queue = queue.Queue()
        self.count = 0
        f = ttk.Frame(self, padding=18)
        f.pack(fill="both", expand=True)
        try:
            self.server = phone_upload.PhoneUploadServer(scanning.scans_folder() / "Phone", self.arrived.put).start()
        except OSError as exc:
            ttk.Label(f, text=f"The phone page couldn't be started ({exc}). Use the scanner folder instead (for "
                      "example the folder your phone's photos sync to).", wraplength=420).pack()
            ttk.Button(f, text="Close", command=self.destroy).pack(pady=(10, 0))
            self.server = None
            return
        img = phone_upload.qr_image(self.server.url)
        if img is not None:
            from PIL import ImageTk
            self._qr = ImageTk.PhotoImage(img)
            tk.Label(f, image=self._qr, bd=0).pack()
        ttk.Label(f, text="On your phone (same Wi-Fi), scan this code with the camera, or open:",
                  wraplength=420).pack(pady=(10, 2))
        e = ttk.Entry(f, width=46, justify="center")
        e.insert(0, self.server.url)
        e.configure(state="readonly")
        e.pack()
        ttk.Label(f, text="Take a picture of each receipt and tap Send; each one arrives here as a draft. The page "
                  "only works while this window is open, and only on this network. If Windows asks about the "
                  "firewall, allow private networks.", style="Muted.TLabel", wraplength=420).pack(pady=(10, 4))
        ttk.Label(f, text="Use this only on your business or home Wi-Fi, not public Wi-Fi: the photos travel over the "
                  "network unencrypted.", style="Warn.TLabel", wraplength=420).pack(pady=(0, 4))
        if self.server.host.startswith("127."):
            ttk.Label(f, text="This computer doesn't seem to be on a network, so a phone can't reach it. Connect to "
                      "your Wi-Fi and open this window again.", style="Warn.TLabel", wraplength=420).pack(pady=(0, 4))
        self.v_count = ttk.Label(f, text="Waiting for photos…", style="Hours.TLabel")
        self.v_count.pack(pady=(4, 8))
        ttk.Button(f, text="Done", style="Accent.TButton", command=self.close).pack()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", lambda e: self.server.stop() if e.widget is self and self.server else None)
        self.after(500, self._poll)

    def _poll(self) -> None:
        while not self.arrived.empty():
            path = self.arrived.get()
            try:
                self.on_receipt(path)
            except Exception as exc:  # noqa: BLE001 - one unreadable photo must not stop the others
                log.warning("Phone photo %s could not be added: %s", path, exc)
                continue
            self.count += 1
            self.v_count.configure(text=f"{self.count} receipt{'s' if self.count != 1 else ''} received as drafts")
        if self.server is not None and self.server.stopped_reason:
            self.v_count.configure(text=self.server.stopped_reason)
        if self.winfo_exists() and self.server is not None and not self.server.stopped:
            self.after(500, self._poll)

    def close(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None
            while not self.arrived.empty():   # anything that arrived in the last moment
                try:
                    self.on_receipt(self.arrived.get())
                except Exception as exc:  # noqa: BLE001
                    log.warning("Phone photo could not be added: %s", exc)
        self.destroy()

    def show(self) -> None:
        self.grab_set()
        self.wait_window(self)


class ExpensesTab(PinLockable, ttk.Frame):
    pin_key = "expenses"
    pin_what = "Expense records"
    pin_purpose = "view or enter expenses"

    # (column, heading, width, anchor)
    COLS = (("date", "Date", 100, "w"), ("vendor", "Vendor", 200, "w"), ("category", "Category", 170, "w"),
            ("amount", "Amount", 100, "e"), ("tax", "Tax", 80, "e"), ("method", "Paid by", 110, "w"),
            ("receipt", "Receipt", 70, "center"), ("memo", "Memo", 260, "w"))

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.sort_col, self.sort_desc = "date", False

        # Buttons on their own row, wrapping on a narrow window (nothing hidden); the filters on the row below.
        bar = FlowFrame(self, gap=14)
        bar.pack(fill="x")
        g = bar.group()
        scan = ttk.Menubutton(g, text="Scan Receipt ▾", style="Accent.TButton")
        menu = tk.Menu(scan, tearoff=0)
        menu.add_command(label="From the scanner…", command=self.scan_from_scanner)
        menu.add_command(label="From a photo or PDF…", command=self.scan_from_file)
        menu.add_command(label="From my phone (Wi-Fi)…", command=self.scan_from_phone)
        menu.add_separator()
        menu.add_command(label="Check Scanner Folder", command=self.check_scanner_folder)
        menu.add_command(label="Choose Scanner Folder…", command=self.choose_scanner_folder)
        scan["menu"] = menu
        scan.pack(side="left")
        ttk.Button(g, text="Phone Photos…", command=self.scan_from_phone).pack(side="left", padx=(6, 0))
        g = bar.group()
        for text, cmd in (("Add Expense…", self.add), ("Edit…", self.edit), ("View Receipt", self.view_receipt),
                          ("Delete", self.delete)):
            ttk.Button(g, text=text, command=cmd).pack(side="left", padx=(0, 6))
        g = bar.group()
        for text, cmd in (("Bank Statement…", self.import_bank), ("Recurring…", self.manage_recurring),
                          ("Mileage…", self.open_mileage), ("Accounts…", self.open_accounts)):
            ttk.Button(g, text=text, command=cmd).pack(side="left", padx=(0, 6))

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(6, 0))
        ttk.Label(top, text="Month").pack(side="left")
        self.cb_month = ttk.Combobox(top, state="readonly", width=12, values=["All year"] + MONTH_NAMES)
        self.cb_month.current(0)
        self.cb_month.pack(side="left", padx=(6, 14))
        ttk.Label(top, text="Category").pack(side="left")
        self.cb_cat = ttk.Combobox(top, state="readonly", width=22)
        self.cb_cat.pack(side="left", padx=(6, 14))
        ttk.Label(top, text="Vendor").pack(side="left")
        self.e_vendor = ttk.Entry(top, width=18)
        self.e_vendor.pack(side="left", padx=6)
        for w in (self.cb_month, self.cb_cat):
            w.bind("<<ComboboxSelected>>", lambda e: self.reload())
        self.e_vendor.bind("<KeyRelease>", lambda e: self.reload())

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.tree = ttk.Treeview(body, columns=[c[0] for c in self.COLS], show="headings")
        for c, h, w, a in self.COLS:
            self.tree.heading(c, text=h, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("vendor", "memo")))
        style_tree(self.tree)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit())

        foot = ttk.Frame(self, padding=(0, 8, 0, 0))
        foot.pack(fill="x")
        ttk.Label(foot, text="Totals by category (shown expenses; drafts not included)",
                  style="Muted.TLabel").pack(anchor="w")
        self.ctree = ttk.Treeview(foot, columns=("category", "total"), show="headings", height=6)
        self.ctree.heading("category", text="Category")
        self.ctree.heading("total", text="Total")
        self.ctree.column("category", width=240)
        self.ctree.column("total", width=120, anchor="e")
        style_tree(self.ctree)
        self.ctree.pack(anchor="w", pady=(4, 0))

    @property
    def book(self):
        return self.app.book

    def refresh(self) -> None:
        if self.book is None:
            return
        cur = self.cb_cat.get()
        self.cb_cat["values"] = ["All categories"] + self.book.categories
        self.cb_cat.set(cur if cur in self.book.categories else "All categories")
        self.reload()

    def shown(self) -> list[Expense]:
        m = self.cb_month.current()
        cat = self.cb_cat.get() if self.cb_cat.current() > 0 else ""
        return [e for e in self.book.expenses if expenses.matches(e, m or None, cat, self.e_vendor.get())]

    def sort_by(self, col: str) -> None:
        self.sort_desc = not self.sort_desc if col == self.sort_col else False
        self.sort_col = col
        self.reload()

    def reload(self) -> None:
        if self.book is None:
            return
        keys = {"date": lambda e: (e.expense_date, e.expense_id), "vendor": lambda e: e.vendor.lower(),
                "category": lambda e: e.category.lower(), "amount": lambda e: e.amount, "tax": lambda e: e.tax,
                "method": lambda e: e.payment_method, "receipt": lambda e: bool(e.receipt_file),
                "memo": lambda e: e.memo.lower()}
        items = sorted(self.shown(), key=keys[self.sort_col], reverse=self.sort_desc)
        for c, h, _w, _a in self.COLS:
            self.tree.heading(c, text=h + ((" ▼" if self.sort_desc else " ▲") if c == self.sort_col else ""))
        self.tree.delete(*self.tree.get_children())
        total = Decimal("0")
        for e in items:
            paid = e.payment_method + (f" #{e.check_number}" if e.check_number else "")
            draft = e.status == "Draft"
            dup = draft and bool(expenses.possible_duplicates(e, self.book.expenses))
            self.tree.insert("", "end", iid=e.expense_id, tags=("former",) if draft else (),
                             values=(f"{e.expense_date:%m/%d/%Y}",
                                     e.vendor + ("  (draft)" if draft else "") + ("  ⚠ duplicate?" if dup else ""),
                                     e.category, fmt_money(e.amount), fmt_money(e.tax) if e.tax else "", paid,
                                     "Yes" if e.receipt_file else "", e.memo))
            total += 0 if draft else e.amount
        self.tree.insert("", "end", iid="__total__", tags=("total",),
                         values=(f"{len(items)} shown", "", "", fmt_money(total), "", "", "", ""))
        self.ctree.delete(*self.ctree.get_children())
        for cat, amt in expenses.totals_by_category(items):
            self.ctree.insert("", "end", values=(cat, fmt_money(amt)))

    def _selected(self) -> Expense | None:
        sel = [i for i in self.tree.selection() if i != "__total__"]
        return next((e for e in self.book.expenses if sel and e.expense_id == sel[0]), None) if self.book else None

    def _save_dialog(self, original: Expense, new: bool, dlg: ExpenseDialog | None = None) -> None:
        dlg = dlg or ExpenseDialog(self, self.book, original)
        result = dlg.show()
        if result is None:
            return
        if dlg.receipt_source:
            if self.book.path is None and not self.app.save_as():
                return
            try:
                result.receipt_file = expenses.file_receipt(dlg.receipt_source, result, self.book.path)
            except (OSError, ValueError) as exc:
                if not messagebox.askyesno("Receipt", f"The receipt could not be attached:\n{exc}\n\nSave the "
                                           "expense without it? (Choose No to keep what you had.)", parent=self):
                    return
        elif result.receipt_file and self.book.path is not None and (
                (result.vendor, result.expense_date, result.amount) != (original.vendor, original.expense_date,
                                                                        original.amount)):
            try:   # vendor / date / amount corrected: keep the receipt in the matching vendor folder and name
                result.receipt_file = expenses.refile_receipt(result, self.book.path)
            except OSError as exc:
                log.warning("Could not move receipt %s: %s", result.receipt_file, exc)
        if result.category not in self.book.categories:
            self.book.categories.append(result.category)
        repeats = dlg.repeats   # read in apply(): the form's widgets are gone once it closes
        if repeats in expenses.REPEATS and not result.recurring_id:
            rec = expenses.recurring_from(result, repeats, expenses.next_recurring_id(self.book.recurring))
            self.book.recurring.append(rec)
            result.recurring_id = rec.recurring_id
        if new:
            self.book.expenses.append(result)
        else:
            self.book.expenses[self.book.expenses.index(original)] = result
        self.app.mark_dirty()
        self.refresh()
        if self.tree.exists(result.expense_id):
            self.tree.selection_set(result.expense_id)
            self.tree.see(result.expense_id)
        done = "Added" if new else "Saved"
        self.app.status(f"{done} {result.vendor} {fmt_money(result.amount)}.{self.app.save_hint()}")

    def add(self) -> None:
        if self.book is None or self.locked:
            return
        self._save_dialog(Expense(expenses.next_expense_id(self.book.expenses), date.today()), new=True)

    def edit(self) -> None:
        e = self._selected() if not self.locked else None
        if e is None:
            return
        receipt = expenses.receipt_path(e, self.book.path)
        if e.status == "Draft" and receipt is not None and receipt.is_file():
            self._review(e, receipt, new=False)
        else:
            self._save_dialog(e, new=False)

    def delete(self) -> None:
        e = self._selected() if not self.locked else None
        if e is None:
            return
        if not messagebox.askyesno("Delete expense", f"Delete {e.vendor} {fmt_money(e.amount)} on "
                                   f"{e.expense_date:%m/%d/%Y}?\n\nIts receipt file is kept in the Receipts folder.",
                                   parent=self):
            return
        self.book.expenses.remove(e)
        self.app.mark_dirty()
        self.reload()
        self.app.status(f"Deleted {e.vendor} {fmt_money(e.amount)}.{self.app.save_hint()}")

    # ----- scanning
    def _read(self, path: Path) -> receipts_ocr.ReceiptFields | None:
        """Read a receipt (None when it can't be read: the review screen then asks for everything)."""
        self.app.config(cursor="watch")
        self.app.update_idletasks()
        try:
            return receipts_ocr.read_receipt(path, known_vendors=expenses.vendors(self.book.expenses))
        except receipts_ocr.ReceiptReadError as exc:
            log.warning("Could not read receipt %s: %s", path, exc)
            return None
        finally:
            self.app.config(cursor="")

    def _review(self, e: Expense, receipt: Path, new: bool, source: str = "",
                fields: receipts_ocr.ReceiptFields | None = None) -> None:
        fields = fields if fields is not None else self._read(receipt)
        dlg = ReceiptReviewDialog(self, self.book, e, receipt, fields, receipt_source=source)
        self._save_dialog(e, new=new, dlg=dlg)

    def _new_from_receipt(self, path: Path) -> tuple[Expense, receipts_ocr.ReceiptFields | None]:
        """A Draft expense filled in from the receipt, and what was read (None if it couldn't be read)."""
        fields = self._read(path)
        new_id = expenses.next_expense_id(self.book.expenses)
        if fields is None:
            return Expense(new_id, date.today(), status="Draft"), None
        return scanning.expense_from_receipt(fields, new_id, self.book.expenses), fields

    def _scan_ready(self) -> bool:
        return self.book is not None and not self.locked

    def scan_from_file(self) -> None:
        if not self._scan_ready():
            return
        types = " ".join(f"*{s}" for s in expenses.RECEIPT_TYPES)
        path = filedialog.askopenfilename(parent=self, title="Choose a receipt photo, scan or PDF",
                                          filetypes=[("Pictures and PDFs", types), ("All files", "*.*")])
        if path:
            e, fields = self._new_from_receipt(Path(path))
            self._review(e, Path(path), new=True, source=path, fields=fields)

    def scan_from_scanner(self) -> None:
        if not self._scan_ready():
            return
        self.app.status("Scanning… use the scanner window that opens.")
        try:
            path = scanning.acquire_from_scanner(scanning.scans_folder())
        except scanning.ScanError as exc:
            messagebox.showwarning("Scan Receipt", str(exc), parent=self)
            return
        if path is None:
            self.app.status("Scan cancelled.")
            return
        e, fields = self._new_from_receipt(path)
        try:
            self._review(e, path, new=True, source=str(path), fields=fields)
        finally:
            path.unlink(missing_ok=True)   # the scan was copied into Receipts if it was kept

    def scan_from_phone(self) -> None:
        if not self._scan_ready():
            return
        if self.book.path is None and not self.app.save_as():
            return
        PhoneUploadDialog(self, self._phone_receipt).show()

    def _phone_receipt(self, path: Path) -> None:
        """A photo arrived from the phone: file it with a Draft expense, read the same way as the scanner folder."""
        e, _fields = self._new_from_receipt(path)
        try:
            e.receipt_file = expenses.file_receipt(path, e, self.book.path)
        except (OSError, ValueError) as exc:
            log.warning("Phone photo %s skipped: %s", path, exc)
            return
        path.unlink(missing_ok=True)   # the filed copy is in Receipts; don't let %TEMP% fill up
        self.book.expenses.append(e)
        self.app.mark_dirty()
        self.refresh()

    def choose_scanner_folder(self) -> bool:
        d = filedialog.askdirectory(parent=self, title="Folder your scanner saves receipts into",
                                    initialdir=self.app.settings.data.get("scanner_folder") or None)
        if not d:
            return False
        self.app.settings.data["scanner_folder"] = d
        self.app.settings.save()
        self.app.status("Scanner folder set. New receipts saved there are added as drafts when you check it "
                        "or open this tab.")
        return True

    def check_scanner_folder(self, quiet: bool = False) -> None:
        """Bring in new files from the scanner folder as Draft expenses (each filed with its receipt)."""
        if not self._scan_ready():
            return
        folder = self.app.settings.data.get("scanner_folder") or ""
        if not folder:
            if not quiet and self.choose_scanner_folder():
                self.check_scanner_folder()
            return
        if self.book.path is None:
            if quiet or not self.app.save_as():
                return
        seen = set(self.app.settings.data.get("scanner_seen") or [])
        found = scanning.new_scans(folder, seen)
        added = 0
        for path in found:
            e, _fields = self._new_from_receipt(path)
            try:
                e.receipt_file = expenses.file_receipt(path, e, self.book.path)
            except (OSError, ValueError) as exc:
                log.warning("Scanner folder file %s skipped: %s", path, exc)
                continue
            self.book.expenses.append(e)
            seen.add(scanning.scan_key(path))
            added += 1
        self.app.settings.data["scanner_seen"] = sorted(seen)[-2000:]
        self.app.settings.save()
        if added:
            self.app.mark_dirty()
            self.refresh()
            self.app.status(f"{added} receipt{'s' if added != 1 else ''} from the scanner folder added as drafts. "
                            f"Double-click a draft to review and approve it.{self.app.save_hint()}")
        elif not quiet:
            self.app.status("No new receipts in the scanner folder.")

    def on_shown(self) -> None:
        """Tab opened: add recurring expenses that came due, and pick up anything new in the scanner folder."""
        self.add_due_recurring()
        if self._scan_ready() and self.app.settings.data.get("scanner_folder"):
            self.check_scanner_folder(quiet=True)

    # ----- recurring expenses
    def add_due_recurring(self) -> int:
        """Add a Draft for each recurring expense that has come due (also when the workbook is opened)."""
        if self.book is None or not self.book.company.use_expenses or not self.book.recurring:
            return 0
        # never past the workbook's year: opening last year's workbook must not fill it with this year's bills
        until = min(date.today(), date(self.book.company.year, 12, 31))
        drafts = expenses.due_drafts(self.book.recurring, self.book.expenses, until)
        if drafts:
            self.book.expenses.extend(drafts)
            self.app.mark_dirty()
            self.refresh()
            self.app.status(f"{len(drafts)} recurring expense{'s' if len(drafts) != 1 else ''} came due and "
                            f"{'were' if len(drafts) != 1 else 'was'} added as drafts on the Expenses tab.")
        return len(drafts)

    def import_bank(self) -> None:
        if self.book is None or self.locked:
            return
        path = filedialog.askopenfilename(parent=self, title="Choose a bank statement (CSV, PDF or a scan)",
                                          filetypes=[("Bank statements", "*.csv *.pdf *.png *.jpg *.jpeg *.tif *.tiff"),
                                                     ("All files", "*.*")])
        if not path:
            return
        added = BankImportDialog(self, self.book, self.app, Path(path)).show()
        if added:
            self.app.mark_dirty()
            self.refresh()
            self.app.status(f"{added} draft expense(s) added from the bank statement. Double-click a draft to review "
                            f"and approve it.{self.app.save_hint()}")

    def open_accounts(self) -> None:
        if self.book is None or self.locked:
            return
        from .vendors_tab import AccountsDialog
        AccountsDialog(self, self.book, self.app).show()
        self.refresh()

    def open_mileage(self) -> None:
        if self.book is None or self.locked:
            return
        MileageDialog(self, self.book, self.app).show()

    def manage_recurring(self) -> None:
        if self.book is None or self.locked:
            return
        RecurringDialog(self, self.book).show()
        self.app.mark_dirty()
        self.reload()

    def view_receipt(self) -> None:
        e = self._selected() if not self.locked else None
        if e is None:
            return
        p = expenses.receipt_path(e, self.book.path)
        if p is None:
            self.app.status("This expense has no receipt attached. Edit it to attach one.", error=True)
        elif not p.is_file():
            self.app.status(f"The receipt file is missing: {p.name} (was the Receipts folder moved?)", error=True)
        else:
            open_file(p)
