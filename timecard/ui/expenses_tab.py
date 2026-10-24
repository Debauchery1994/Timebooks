"""Expenses tab: money the business spent, with receipts attached, filters, and totals by category; receipts can be
scanned, picked up from the scanner's folder or imported, read automatically, and approved on a review screen."""
from __future__ import annotations

import tkinter as tk
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from .. import expenses, receipts_ocr, scanning
from ..models import Expense
from .common import MONTH_NAMES, ModalDialog, fmt_money, log, open_file, parse_date, parse_money, style_tree
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class ExpenseDialog(ModalDialog):
    """Add or edit one expense. result = the Expense, plus the receipt file chosen to attach (not filed yet)."""

    status = "Approved"   # what OK saves (the review screen can keep a scanned receipt as a Draft)

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
        rf = ttk.Frame(f)
        shown = Path(self.receipt_source or e.receipt_file).name if (self.receipt_source or e.receipt_file) else ""
        self.v_receipt = ttk.Label(rf, text=shown or "No receipt attached", style="Muted.TLabel")
        self.v_receipt.pack(side="left")
        ttk.Button(rf, text="Attach…", command=self._choose_receipt).pack(side="left", padx=(8, 0))
        row(8, "Receipt", rf)
        ttk.Label(f, text="Amount is the total paid, tax included. Receipts are copied into a Receipts folder next to "
                  "the workbook.", style="Muted.TLabel", wraplength=380).grid(row=9, column=0, columnspan=2,
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
        self.result = Expense(
            expense_id=self.e.expense_id, expense_date=d, vendor=vendor, category=category, amount=amount, tax=tax,
            payment_method=w["method"].get(), check_number=check if w["method"].get() == "Check" else None,
            memo=w["memo"].get().strip(), receipt_file=self.e.receipt_file, status=self.status,
            vendor_id=self.e.vendor_id, created=self.e.created or datetime.now().isoformat(timespec="seconds"))
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
        ttk.Label(right, text=warn, style="Muted.TLabel", wraplength=380).grid(row=10, column=0, columnspan=3,
                                                                           sticky="w", pady=(6, 0))
        self.v_draft = tk.BooleanVar(value=False)
        ttk.Checkbutton(right, text="Keep as draft (review later; drafts aren't in the totals)",
                        variable=self.v_draft).grid(row=11, column=0, columnspan=3, sticky="w", pady=(6, 0))
        return first

    def _show_picture(self, frame) -> None:
        try:
            from PIL import ImageTk
            img = receipts_ocr._load_image(self.receipt, pdf_dpi=110)   # the reader's own loader (PDF page 1 too)
            img.thumbnail((380, 560))
            self._img = ImageTk.PhotoImage(img)
            tk.Label(frame, image=self._img, bd=1, relief="solid").pack()
        except Exception as exc:  # noqa: BLE001 - the form still works without the picture
            log.warning("Could not show receipt %s: %s", self.receipt, exc)
            ttk.Label(frame, text="(The receipt picture can't be shown.)", style="Muted.TLabel").pack()
        ttk.Button(frame, text="Open full size", command=lambda: open_file(self.receipt)).pack(pady=(6, 0))

    def apply(self) -> bool:
        self.status = "Draft" if self.v_draft.get() else "Approved"
        return super().apply()


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

        top = ttk.Frame(self)
        top.pack(fill="x")
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
        ttk.Button(top, text="Delete", command=self.delete).pack(side="right")
        ttk.Button(top, text="View Receipt", command=self.view_receipt).pack(side="right", padx=6)
        ttk.Button(top, text="Edit…", command=self.edit).pack(side="right")
        ttk.Button(top, text="Add Expense…", command=self.add).pack(side="right", padx=6)
        scan = ttk.Menubutton(top, text="Scan Receipt ▾", style="Accent.TButton")
        menu = tk.Menu(scan, tearoff=0)
        menu.add_command(label="From the scanner…", command=self.scan_from_scanner)
        menu.add_command(label="From a photo or PDF…", command=self.scan_from_file)
        menu.add_separator()
        menu.add_command(label="Check Scanner Folder", command=self.check_scanner_folder)
        menu.add_command(label="Choose Scanner Folder…", command=self.choose_scanner_folder)
        scan["menu"] = menu
        scan.pack(side="right")

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
            self.tree.insert("", "end", iid=e.expense_id, tags=("former",) if draft else (),
                             values=(f"{e.expense_date:%m/%d/%Y}", e.vendor + ("  (draft)" if draft else ""),
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
                messagebox.showwarning("Receipt", f"The receipt could not be attached:\n{exc}", parent=self)
        if result.category not in self.book.categories:
            self.book.categories.append(result.category)
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
        self._review(e, path, new=True, source=str(path), fields=fields)

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
        """Tab opened: pick up anything new in the scanner folder."""
        if self._scan_ready() and self.app.settings.data.get("scanner_folder"):
            self.check_scanner_folder(quiet=True)

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
