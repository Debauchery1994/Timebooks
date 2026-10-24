"""Employees tab (behind the PIN) and the pay rate dialog."""
from __future__ import annotations

import dataclasses
import tkinter as tk
from decimal import Decimal
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from ..models import Employee
from .. import calc, statements
from ..storage import TimecardBook
from .common import F, ModalDialog, is_salaried, open_file, parse_money, pay_type_label, salary_amount_text, style_tree
from .dialogs import BackupDialog, ExportStatementsDialog
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class PayRateDialog(ModalDialog):
    """Masked entry for the pay rate. The value is never displayed."""

    def __init__(self, master, book: TimecardBook, emp: Employee):
        self.book, self.emp = book, emp
        self.salaried = is_salaried(emp)
        self.per_period = self.salaried and calc.salary_is_per_period(emp)
        super().__init__(master, ("Set Salary per Pay Period" if self.per_period else "Set Annual Salary")
                         if self.salaried else "Set Hourly Rate")

    def body(self, f):
        period = self.book.company.pay_period.lower()
        what = (f"salary per pay period ({period})" if self.per_period else "annual salary") if self.salaried \
            else "hourly rate"
        ttk.Label(f, text=f"Set the {what} for {self.emp.full_name}.").grid(row=0, column=0, columnspan=2,
                                                                            sticky="w")
        ttk.Label(f, text="The rate is saved in the workbook and is never shown on screen.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.e1 = ttk.Entry(f, show="\u2022", width=20)
        self.e2 = ttk.Entry(f, show="\u2022", width=20)
        label = ("Salary per pay period" if self.per_period else "Annual salary") if self.salaried \
            else "Hourly rate"
        ttk.Label(f, text=label).grid(row=2, column=0, sticky="w", pady=4)
        self.e1.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(f, text=f"Confirm {label.lower()}").grid(row=3, column=0, sticky="w", pady=4)
        self.e2.grid(row=3, column=1, sticky="w", pady=4)
        if self.salaried:
            note = (f"Each check is this amount, before adjustments (pay period: {period})." if self.per_period
                    else "Each check is the annual salary divided by the number of pay periods in the year.")
            ttk.Label(f, text=note, style="Muted.TLabel", wraplength=340).grid(row=4, column=0, columnspan=2,
                                                                               sticky="w", pady=(6, 0))
        return self.e1

    def apply(self) -> bool:
        a, b = self.e1.get(), self.e2.get()
        try:
            rate = parse_money(a)
        except ValueError:
            rate = None
        if rate is None or rate <= 0:
            messagebox.showwarning("Set Pay Rate", "Enter a valid amount greater than zero.", parent=self)
            self.e1.delete(0, "end")
            self.e2.delete(0, "end")
            self.e1.focus_set()
            return False
        if a.strip() != b.strip():
            messagebox.showwarning("Set Pay Rate", "The two entries do not match. Please re-enter.", parent=self)
            self.e1.delete(0, "end")
            self.e2.delete(0, "end")
            self.e1.focus_set()
            return False
        self.book.set_pay_rate(self.emp.employee_id, rate)
        self.e1.delete(0, "end")
        self.e2.delete(0, "end")
        self.result = True
        return True


class EmployeesTab(PinLockable, ttk.Frame):
    pin_key = "employees"
    pin_what = "Employee records"
    pin_purpose = "view or edit team members"
    pin_locked_by_default = True

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.selected: str | None = None

        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(left)
        bar.pack(side="top", fill="x", pady=(0, 6))
        self.v_show_former = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Show former team members", variable=self.v_show_former,
                        command=self.refresh).pack(side="left")
        self.v_count = tk.StringVar()
        ttk.Button(bar, text="\U0001F512 Lock", command=self.lock).pack(side="right")
        ttk.Button(bar, text="Export Annual Statements…", command=self.export_statements).pack(side="right",
                                                                                                padx=(0, 8))
        ttk.Button(bar, text="Backup…", command=self.open_backup).pack(side="right", padx=(0, 8))
        ttk.Label(bar, textvariable=self.v_count, style="Muted.TLabel").pack(side="right", padx=10)
        cols = ("id", "name", "type", "active", "rate")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c, h, w, anchor in (("id", "ID", 70, "w"), ("name", "Name", 220, "w"), ("type", "Pay Type", 150, "w"),
                                ("active", "Status", 90, "center"), ("rate", "Rate on file", 90, "center")):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "name"))
        style_tree(self.tree)
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        form = ttk.LabelFrame(self, text="Employee", padding=12)
        form.pack(side="left", fill="y", padx=(12, 0))
        self.v = {k: tk.StringVar() for k in ("first", "last", "address1", "address2", "pay_type",
                                              "salary_basis")}
        self.v_active = tk.BooleanVar(value=True)
        self.v_id = tk.StringVar(value="(new)")
        r = 0
        ttk.Label(form, text="Employee ID").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Label(form, textvariable=self.v_id, style="Bold.TLabel").grid(row=r, column=1, sticky="w")
        r += 1
        for key, label in (("first", "First name"), ("last", "Last name"), ("address1", "Address line 1"),
                           ("address2", "City, State ZIP")):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=self.v[key], width=32).grid(row=r, column=1, sticky="ew", pady=3)
            r += 1
        ttk.Label(form, text="Pay type").grid(row=r, column=0, sticky="w", pady=3)
        pt = ttk.Frame(form)
        pt.grid(row=r, column=1, sticky="w", pady=3)
        ttk.Radiobutton(pt, text="Hourly", value="Hourly", variable=self.v["pay_type"]).pack(side="left")
        ttk.Radiobutton(pt, text="Salaried", value="Salary", variable=self.v["pay_type"]).pack(side="left",
                                                                                              padx=(12, 0))
        ttk.Radiobutton(pt, text="Owner", value="Owner", variable=self.v["pay_type"]).pack(side="left", padx=(12, 0))
        r += 1
        self.lbl_basis = ttk.Label(form, text="Salary is")
        self.lbl_basis.grid(row=r, column=0, sticky="w", pady=3)
        self.basis_row = ttk.Frame(form)
        self.basis_row.grid(row=r, column=1, sticky="w", pady=3)
        ttk.Radiobutton(self.basis_row, text="Per year", value="Annual",
                        variable=self.v["salary_basis"]).pack(side="left")
        ttk.Radiobutton(self.basis_row, text="Per pay period", value="Per Period",
                        variable=self.v["salary_basis"]).pack(side="left", padx=(12, 0))
        self.v["pay_type"].trace_add("write", lambda *_: self._show_basis())
        r += 1
        self.v_status = tk.StringVar(value="Active")
        ttk.Label(form, text="Status").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Label(form, textvariable=self.v_status).grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        ttk.Label(form, text="Notes").grid(row=r, column=0, sticky="nw", pady=3)
        self.notes = tk.Text(form, width=32, height=4, font=F("base"), relief="flat", bd=1,
                             wrap="word")
        self.notes.grid(row=r, column=1, sticky="ew", pady=3)
        r += 1
        self.v_rate = tk.StringVar(value="Rate on file: -")
        ttk.Label(form, textvariable=self.v_rate, style="Muted.TLabel").grid(row=r, column=1, sticky="w", pady=(6, 0))
        r += 1
        btns = ttk.Frame(form, padding=(0, 12, 0, 0))
        btns.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(btns, text="New", command=self.clear_form).pack(side="left")
        ttk.Button(btns, text="Add", command=self.add).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="left")
        r += 1
        self.btn_deact = ttk.Button(form, text="No Longer With Company", command=self.toggle_active)
        self.btn_deact.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        r += 1
        ttk.Button(form, text="Set Pay Rate\u2026", command=self.set_pay_rate).grid(row=r, column=0, columnspan=2,
                                                                                sticky="ew", pady=(8, 0))
        self.clear_form()

    def open_backup(self) -> None:
        if self.app.book is None or self.locked:
            return
        BackupDialog(self, self.app).show()

    # ----- annual statements for a tax professional
    def export_statements(self) -> None:
        book = self.app.book
        if book is None or self.locked:
            return
        emp = book.employees.get(self.selected) if self.selected else None
        choice = ExportStatementsDialog(self, book.company.year, emp, self.app.output_dir).show()
        if not choice:
            return
        only, folder = choice
        try:
            pdf, xlsx = statements.export_annual_statements(book, folder, [only] if only else None)
        except ValueError as exc:
            messagebox.showinfo("Export Annual Statements", str(exc), parent=self)
            return
        except PermissionError:
            messagebox.showerror("Export Annual Statements", "A statement file with the same name is open in "
                                 "another program. Close it and try again.", parent=self)
            return
        self.app.status(f"Exported {pdf.name} and {xlsx.name}")
        if messagebox.askyesno("Export Annual Statements",
                               f"Saved to {folder}:\n\n  {pdf.name}  (printable statements)\n  {xlsx.name}  "
                               f"(spreadsheet for the tax professional)\n\nOpen the PDF now?", parent=self):
            open_file(pdf)

    # ----- PIN lock
    def on_pin_lock(self) -> None:
        """Clear all employee details as well as hiding them."""
        self.clear_form()
        self.tree.delete(*self.tree.get_children())
        self.v_count.set("")

    def on_pin_unlock(self, quiet: bool) -> None:
        self.refresh()
        super().on_pin_unlock(quiet)

    @property
    def book(self):
        return self.app.book

    def refresh(self) -> None:
        if self.locked:
            return
        if self.book is None:
            return
        self.tree.delete(*self.tree.get_children())
        # former team members stay in the workbook (bookkeeping) but are hidden unless asked for
        show_former = bool(self.v_show_former.get())
        all_emps = list(self.book.employees.values())
        emps = sorted((e for e in all_emps if e.active or show_former),
                      key=lambda e: (not e.active, e.last_name.lower(), e.first_name.lower()))
        n_former = sum(1 for e in all_emps if not e.active)
        self.v_count.set(f"{len(all_emps) - n_former} active" + (f", {n_former} former" if n_former else ""))
        for e in emps:
            has = self.book.has_pay_rate(e.employee_id)
            tags = () if e.active else ("inactive",)
            if e.active and not has:
                tags = ("norate",)
            self.tree.insert("", "end", iid=e.employee_id, tags=tags,
                             values=(e.employee_id, e.display_name + ("" if e.active else "  (former)"), pay_type_label(e.pay_type, e.salary_basis),
                                     "Active" if e.active else "Former",
                                     "Yes" if has else "No"))
        if self.selected and self.tree.exists(self.selected):
            self.tree.selection_set(self.selected)
            self.tree.see(self.selected)
        else:
            self.clear_form()

    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        e = self.book.employees.get(sel[0])
        if e is None:
            return
        self.selected = e.employee_id
        self.v_id.set(e.employee_id)
        self.v["first"].set(e.first_name)
        self.v["last"].set(e.last_name)
        self.v["address1"].set(e.address1)
        self.v["address2"].set(e.address2)
        self.v["salary_basis"].set("Per Period" if calc.salary_is_per_period(e) else "Annual")
        self.v["pay_type"].set(e.pay_type)
        self.v_active.set(e.active)
        self.v_status.set("Active" if e.active else "No longer with the company (records kept)")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", e.notes)
        self.v_rate.set("Rate on file: " + ("Yes" if self.book.has_pay_rate(e.employee_id) else "No"))
        self.btn_deact.configure(text="No Longer With Company" if e.active else "Reactivate")

    def clear_form(self) -> None:
        self.selected = None
        if self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())
        self.v_id.set("(new)")
        for k in ("first", "last", "address1", "address2"):
            self.v[k].set("")
        self.v["salary_basis"].set("Annual")
        self.v["pay_type"].set("Hourly")
        self.v_active.set(True)
        self.v_status.set("Active")
        self.notes.delete("1.0", "end")
        self.v_rate.set("Rate on file: -")
        self.btn_deact.configure(text="No Longer With Company")

    def _show_basis(self) -> None:
        """'Salary is per year / per pay period' only applies to salaried people."""
        for w in (self.lbl_basis, self.basis_row):
            if self.v["pay_type"].get() == "Salary":
                w.grid()
            else:
                w.grid_remove()

    def _form_values(self) -> dict | None:
        first, last = self.v["first"].get().strip(), self.v["last"].get().strip()
        if not first and not last:
            messagebox.showwarning("Employees", "Enter at least a first or last name.", parent=self)
            return None
        return dict(first_name=first, last_name=last, address1=self.v["address1"].get().strip(),
                    address2=self.v["address2"].get().strip(), pay_type=self.v["pay_type"].get() or "Hourly",
                    salary_basis=self.v["salary_basis"].get() or "Annual",
                    active=bool(self.v_active.get()), notes=self.notes.get("1.0", "end").strip())

    def add(self) -> None:
        vals = self._form_values()
        if vals is None or self.book is None:
            return
        eid = self.book.next_employee_id()
        emp = Employee(employee_id=eid, **vals)
        self.book.upsert_employee(emp)
        self.selected = eid
        self.app.mark_dirty()
        self.app.refresh_employees()
        self.app.status(f"Added {emp.full_name} ({eid}).")
        if messagebox.askyesno("Employees", f"Set the pay rate for {emp.full_name} now?", parent=self):
            self.set_pay_rate()

    def save(self) -> None:
        if self.selected is None:
            self.add()
            return
        vals = self._form_values()
        if vals is None:
            return
        cur = self.book.employees[self.selected]
        new = dataclasses.replace(cur, **vals)
        basis_changed = (is_salaried(cur) and is_salaried(new)
                         and calc.salary_is_per_period(cur) != calc.salary_is_per_period(new))
        type_changed = ((is_salaried(cur) != is_salaried(new) or basis_changed)
                        and self.book.has_pay_rate(cur.employee_id))
        if type_changed:
            want = salary_amount_text(new, self.book.company) if is_salaried(new) else "an hourly rate"
            old_label = pay_type_label(cur.pay_type, cur.salary_basis)
            new_label = pay_type_label(new.pay_type, new.salary_basis)
            if not messagebox.askokcancel(
                    "Pay type changed", f"{new.full_name} is changing from {old_label} to "
                    f"{new_label}.\n\nThe rate on file must be re-entered as {want}. "
                    "The old rate will be cleared and they will be left out of Pay & Checks until a new one "
                    "is set.", parent=self):
                return
        if new != cur:
            self.book.upsert_employee(new)
            if type_changed:
                self.book.set_pay_rate(new.employee_id, Decimal("0"))
            self.app.mark_dirty()
            self.app.refresh_employees()
        self.app.status(f"Saved {new.full_name}.")
        if type_changed:
            self.set_pay_rate()

    def toggle_active(self) -> None:
        if self.selected is None:
            return
        cur = self.book.employees[self.selected]
        if cur.active:
            if not messagebox.askyesno(
                    "No Longer With Company",
                    f"Mark {cur.full_name} as no longer with the company?\n\n"
                    "Their name, time records and checks are kept in the workbook for bookkeeping, "
                    "but they will no longer appear on the Time Card, in Pay & Checks, or in these lists.\n\n"
                    "To bring them back later, tick 'Show former team members' and click Reactivate.",
                    parent=self):
                return
        self.book.upsert_employee(dataclasses.replace(cur, active=not cur.active))
        self.app.mark_dirty()
        if cur.active and not self.v_show_former.get():
            self.selected = None
        self.app.refresh_employees()
        self.app.status(f"{cur.full_name} " + ("is marked as no longer with the company (records kept)."
                                               if cur.active else "has been reactivated."))

    def set_pay_rate(self) -> None:
        if self.selected is None:
            messagebox.showinfo("Set Pay Rate", "Select an employee first.", parent=self)
            return
        emp = self.book.employees[self.selected]
        if PayRateDialog(self, self.book, emp).show():
            self.app.mark_dirty()
            self.app.refresh_employees()
            self.app.status(f"Pay rate saved for {emp.full_name}.")
