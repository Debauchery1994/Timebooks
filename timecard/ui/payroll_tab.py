"""Pay & Checks tab: calculate, record, print and void checks."""
from __future__ import annotations

import tkinter as tk
from datetime import date
from decimal import Decimal
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import TYPE_CHECKING

from ..models import HoursSummary, PayrollRecord
from .. import calc, checks
from .common import (
    FlowFrame, THREE_UP_LAYOUTS, _has_pay_hours, fmt_hours, fmt_money, fmt_period, is_salaried, open_file, parse_date,
    parse_money, period_index_for, style_tree,
)
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class PayrollTab(PinLockable, ttk.Frame):
    pin_key = "payroll"
    pin_what = "Pay and check records"
    pin_purpose = "calculate, print or void checks"

    """Pay & Checks: contractors are paid hours x rate (or a flat amount) minus an Adjustment. No taxes."""

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.periods: list[tuple[date, date]] = []
        self.period_chosen = False   # True once the user picks a period; until then follow the default
        self.preview: dict[str, dict] = {}   # eid -> {emp, hours, other, memo, record, dup}
        self.preview_period: tuple[date, date] | None = None
        self._ot_shown: bool | None = None

        top = FlowFrame(self)
        top.pack(fill="x")
        g1 = top.group()
        ttk.Label(g1, text="Pay period").pack(side="left")
        ttk.Button(g1, text="\u25C0", width=3, command=lambda: self.step_period(-1)).pack(side="left", padx=(6, 2))
        self.cb_period = ttk.Combobox(g1, state="readonly", width=31)
        self.cb_period.pack(side="left")
        self.cb_period.bind("<<ComboboxSelected>>", lambda e: self._chose_period())
        ttk.Button(g1, text="\u25B6", width=3, command=lambda: self.step_period(1)).pack(side="left", padx=(2, 0))
        g2 = top.group()
        ttk.Label(g2, text="Check date").pack(side="left")
        self.v_check_date = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(g2, textvariable=self.v_check_date, width=12).pack(side="left", padx=6)
        ttk.Button(g2, text="Calculate", style="Accent.TButton", command=self.calculate).pack(side="left", padx=6)

        cols = ("emp", "hours", "ot", "amount", "adj", "net", "memo")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=8)
        for c, h, w, a in (("emp", "Name", 200, "w"), ("hours", "Hours", 80, "e"), ("ot", "OT Hrs", 70, "e"),
                           ("amount", "Amount", 100, "e"), ("adj", "Adjustment ✎", 110, "e"),
                           ("net", "Check Amount", 110, "e"), ("memo", "Memo ✎", 320, "w")):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("emp", "memo")))
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True, pady=(10, 0))
        self.tree.bind("<Double-1>", self._on_double)
        self.v_note = tk.StringVar()
        ttk.Label(self, textvariable=self.v_note, style="Muted.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(4, 0))

        btns = ttk.Frame(self, padding=(0, 8))
        btns.pack(fill="x")
        self.btn_record = ttk.Button(btns, text="Record Pay & Print Checks", style="Accent.TButton",
                                     command=self.record_and_print)
        self.btn_record.pack(side="left")
        self.btn_reprint = ttk.Button(btns, text="Reprint Checks", command=self.reprint)
        self.btn_reprint.pack(side="left", padx=6)
        ttk.Button(btns, text="Print Register", command=self.print_register).pack(side="left")
        ttk.Button(btns, text="Void Check…", command=self.void_check).pack(side="left", padx=6)
        self.btn_align = ttk.Button(btns, text="Print Alignment Test", command=self.alignment_test)
        self.btn_align.pack(side="right")

        ttk.Label(self, text="Checks recorded for this period", style="Section.TLabel").pack(anchor="w", pady=(6, 4))
        rcols = ("no", "date", "emp", "hours", "amount", "net", "void")
        self.rtree = ttk.Treeview(self, columns=rcols, show="headings", height=6)
        for c, h, w, a in (("no", "Check #", 80, "center"), ("date", "Check Date", 100, "center"),
                           ("emp", "Name", 200, "w"), ("hours", "Hours", 80, "e"), ("amount", "Amount", 100, "e"),
                           ("net", "Check Amount", 110, "e"), ("void", "Voided", 70, "center")):
            self.rtree.heading(c, text=h)
            self.rtree.column(c, width=w, anchor=a, stretch=(c == "emp"))
        style_tree(self.rtree)
        self.rtree.pack(fill="both", expand=True)

    HINT = "Double-click Adjustment (positive deducts, negative adds, e.g. a reimbursement) or Memo to edit."

    @property
    def book(self):
        return self.app.book

    @property
    def ot_enabled(self) -> bool:
        return bool(self.book is not None and getattr(self.book.company, "overtime_enabled", False))

    @property
    def layout(self) -> str:
        return getattr(self.book.company, "check_layout", "voucher") if self.book is not None else "voucher"

    def _apply_layout(self) -> None:
        """Button labels follow the check layout; handwrite prints a list instead of checks."""
        hand = self.layout == "handwrite"
        self.btn_record.configure(text="Record Pay & Print Checks-to-Write List" if hand
                                  else "Record Pay & Print Checks")
        self.btn_reprint.configure(text="Print Checks-to-Write List" if hand else "Reprint Checks")
        self.btn_align.configure(state="disabled" if hand else "normal")

    def _apply_columns(self) -> None:
        self._apply_layout()
        ot = self.ot_enabled
        if ot != self._ot_shown:
            self.tree["displaycolumns"] = ("emp", "hours", "ot", "amount", "adj", "net", "memo") if ot else \
                ("emp", "hours", "amount", "adj", "net", "memo")
            self.tree.heading("hours", text="Reg Hrs" if ot else "Hours")
            self._ot_shown = ot

    def refresh(self) -> None:
        if self.book is None:
            return
        self._apply_columns()
        old = self.selected_period()
        self.periods = calc.periods_in_year(self.book.company)
        self.cb_period["values"] = [fmt_period(p) for p in self.periods]
        if old in self.periods and self.period_chosen:
            self.cb_period.current(self.periods.index(old))
        elif self.periods:
            self.cb_period.current(self._default_period_index())
        self.on_period_change()

    def _default_period_index(self) -> int:
        """The pay period containing today, or the one before it when the company holds a period back
        (Company Settings > Pay timing)."""
        today = date.today()
        idx = period_index_for(self.periods, today)
        held_back = (self.book.company.pay_hold_back or "").strip().lower() == "one"
        if held_back and idx > 0 and self.periods[idx][0] <= today <= self.periods[idx][1]:
            idx -= 1
        return idx

    def _unpaid_hourly(self, period: tuple[date, date]) -> set[str]:
        """Active hourly people with hours (or paid time off) in `period` and no (non-voided) check for it yet."""
        start, end = period
        paid = {r.employee_id for r in self.book.records_for_period(start, end) if not r.voided}
        return {e.employee_id for e in self.book.active_employees()
                if not is_salaried(e) and e.employee_id not in paid
                and _has_pay_hours(calc.summarize_hours(self.book.entries_for(e.employee_id, start, end),
                                                        self.book.company, start, end))}

    def _hours_elsewhere(self, eid: str, current: tuple[date, date]) -> tuple[date, date] | None:
        """The latest other period (up to today) where `eid` has unpaid hours, if any."""
        today = date.today()
        for p in reversed(self.periods):
            if p != current and p[0] <= today and eid in self._unpaid_hourly(p):
                return p
        return None

    def selected_period(self) -> tuple[date, date] | None:
        i = self.cb_period.current()
        return self.periods[i] if 0 <= i < len(self.periods) else None

    def step_period(self, delta: int) -> None:
        i = self.cb_period.current() + delta
        if 0 <= i < len(self.periods):
            self.cb_period.current(i)
            self._chose_period()

    def _chose_period(self) -> None:
        self.period_chosen = True   # keep the user's choice instead of the automatic default
        self.on_period_change()

    def on_shown(self) -> None:
        """Tab opened: until the user picks a period, follow the default (hours may have been entered since)."""
        if self.book is not None and not self.period_chosen and not self.preview and self.periods:
            idx = self._default_period_index()
            if idx != self.cb_period.current():
                self.cb_period.current(idx)
                self.on_period_change()
                return
        self.load_recorded()

    def on_period_change(self) -> None:
        self.preview.clear()
        self.preview_period = None
        self.tree.delete(*self.tree.get_children())
        self.v_note.set("Click Calculate to preview this period's checks. " + self.HINT)
        self.load_recorded()

    def load_recorded(self) -> None:
        self.rtree.delete(*self.rtree.get_children())
        p = self.selected_period()
        if p is None or self.book is None:
            return
        for r in sorted(self.book.records_for_period(*p), key=lambda r: r.check_number):
            self.rtree.insert("", "end", iid=str(r.check_number), tags=("void",) if r.voided else (),
                              values=(r.check_number, f"{r.check_date:%m/%d/%Y}", r.employee_name,
                                      fmt_hours(r.regular_hours + r.overtime_hours), fmt_money(r.gross_pay),
                                      fmt_money(r.net_pay), "Yes" if r.voided else "No"))

    def _check_date(self) -> date | None:
        try:
            return parse_date(self.v_check_date.get())
        except ValueError as exc:
            messagebox.showwarning("Pay & Checks", str(exc), parent=self)
            return None

    @staticmethod
    def default_memo(start: date, end: date, hours: HoursSummary, salaried: bool = False) -> str:
        span = f"{start:%m/%d}\u2013{end:%m/%d/%Y}" if start.year == end.year else \
            f"{start:%m/%d/%Y}\u2013{end:%m/%d/%Y}"
        off = f" + {hours.paid_leave:.2f} time off" if hours.paid_leave else ""
        if salaried:
            return f"Salary {span}" + (f" \u00b7 {hours.total:.2f} hrs" if hours.total else "")
        return f"Contract labor {span} \u00b7 {hours.total:.2f} hrs{off}"

    def _compute(self, item: dict, check_number: int, check_date: date) -> PayrollRecord:
        start, end = self.preview_period
        return calc.compute_payroll(item["emp"], item["hours"], self.book.company, check_number=check_number,
                                    check_date=check_date, period_start=start, period_end=end,
                                    other_deductions=item["other"], memo=item["memo"])

    def calculate(self) -> None:
        if self.book is None:
            return
        self.app.commit_pending()
        self._apply_columns()
        p = self.selected_period()
        cd = self._check_date()
        if p is None or cd is None:
            return
        start, end = p
        keep = {k: (v["other"], v["memo"]) for k, v in self.preview.items()} if self.preview_period == p else {}
        self.preview.clear()
        self.preview_period = p
        already = {r.employee_id for r in self.book.records_for_period(start, end) if not r.voided}
        no_rate, dups, no_hours = [], [], []
        for emp in self.book.active_employees():
            entries = self.book.entries_for(emp.employee_id, start, end)
            hours = calc.summarize_hours(entries, self.book.company, start, end)
            if not _has_pay_hours(hours) and not is_salaried(emp):   # salaried people are paid every period
                no_hours.append(emp)
                continue
            if not self.book.has_pay_rate(emp.employee_id):
                no_rate.append(emp.full_name)
                continue
            other, memo = keep.get(emp.employee_id, (Decimal("0"), self.default_memo(start, end, hours, is_salaried(emp))))
            item = dict(emp=emp, hours=hours, other=other, memo=memo, dup=emp.employee_id in already)
            item["record"] = self._compute(item, 0, cd)
            self.preview[emp.employee_id] = item
            if item["dup"]:
                dups.append(emp.full_name)
        self._render_preview()
        notes = []
        elsewhere = [(e, self._hours_elsewhere(e.employee_id, p)) for e in no_hours]
        elsewhere = [(e, q) for e, q in elsewhere if q is not None]
        if elsewhere:
            notes.append("No hours in this period for " + "; ".join(
                f"{e.full_name} (has unpaid hours in {q[0]:%m/%d}-{q[1]:%m/%d})" for e, q in elsewhere)
                + ". Choose that pay period above to pay them.")
        elif no_hours and not self.preview:
            notes.append("No hours were entered on the Time Card for this pay period.")
        if dups:
            notes.append("Already paid for this period (shown in orange): " + ", ".join(dups) + ".")
        if no_rate:
            notes.append("Excluded - no pay rate on file: " + ", ".join(no_rate) + ".")
        c = self.book.company
        if any((c.federal_withholding_pct, c.state_withholding_pct, c.social_security_pct, c.medicare_pct)):
            notes.append("Note: withholding % values under Company Settings > Advanced are not zero and are "
                         "being subtracted from the check amounts.")
        self.v_note.set("  ".join(notes) or f"{len(self.preview)} check(s) ready. " + self.HINT)
        if no_rate:
            messagebox.showwarning("Pay & Checks", "These contractors have hours but no pay rate on file and "
                                   "were excluded:\n\n" + "\n".join(no_rate) +
                                   "\n\nSet a rate on the Employees tab, then Calculate again.", parent=self)
        if dups:
            messagebox.showwarning("Pay & Checks", "Checks for this period are already recorded for:\n\n" +
                                   "\n".join(dups), parent=self)
        self.app.status(f"Calculated {len(self.preview)} check(s) for {fmt_period(p)}.")

    def _render_preview(self) -> None:
        self.tree.delete(*self.tree.get_children())
        tot_h = tot_ot = tot_amt = tot_adj = tot_net = Decimal("0")
        for eid, item in sorted(self.preview.items(), key=lambda kv: kv[1]["emp"].display_name.lower()):
            r: PayrollRecord = item["record"]
            tot_h += r.regular_hours
            tot_ot += r.overtime_hours
            tot_amt += r.gross_pay
            tot_adj += r.other_deductions
            tot_net += r.net_pay
            hrs = r.regular_hours if self.ot_enabled else r.regular_hours + r.overtime_hours
            hrs_txt = "Salaried" if is_salaried(item["emp"]) and not (r.regular_hours + r.overtime_hours) \
                else fmt_hours(hrs)
            name = item["emp"].display_name + ("  (already paid)" if item["dup"] else "")
            self.tree.insert("", "end", iid=eid, tags=("dup",) if item["dup"] else (),
                             values=(name, hrs_txt, fmt_hours(r.overtime_hours), fmt_money(r.gross_pay),
                                     self._fmt_adj(r.other_deductions), fmt_money(r.net_pay), r.memo))
        if self.preview:
            self.tree.insert("", "end", iid="__total__", tags=("total",),
                             values=(f"Total ({len(self.preview)} checks)",
                                     fmt_hours(tot_h if self.ot_enabled else tot_h + tot_ot), fmt_hours(tot_ot),
                                     fmt_money(tot_amt), self._fmt_adj(tot_adj), fmt_money(tot_net), ""))

    @staticmethod
    def _fmt_adj(x: Decimal) -> str:
        if not x:
            return ""
        return f"-{fmt_money(x)}" if x > 0 else f"+{fmt_money(-x)}"

    def _on_double(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or iid not in self.preview:
            return
        try:
            name = self.tree["displaycolumns"][int(col[1:]) - 1]
        except (ValueError, IndexError):
            return
        if name == "adj":
            self.edit_adjustment(iid)
        elif name == "memo":
            self.edit_memo(iid)
        else:
            self.v_note.set(self.HINT)

    def edit_adjustment(self, eid: str) -> None:
        item = self.preview[eid]
        s = simpledialog.askstring(
            "Adjustment", f"Adjustment for {item['emp'].full_name}:\n"
            "positive = deduct from the check, negative = add (e.g. -25.00 for a reimbursement)",
            initialvalue=f"{item['other']:.2f}", parent=self)
        if s is None:
            return
        try:
            amt = parse_money(s)
        except ValueError as exc:
            messagebox.showwarning("Adjustment", str(exc), parent=self)
            return
        cd = self._check_date()
        if cd is None:
            return
        trial = dict(item, other=amt)
        rec = self._compute(trial, 0, cd)
        if rec.net_pay < 0:
            messagebox.showwarning("Adjustment", "That would make the check amount negative.", parent=self)
            return
        item["other"], item["record"] = amt, rec
        self._render_preview()

    def edit_memo(self, eid: str) -> None:
        item = self.preview[eid]
        s = simpledialog.askstring("Memo", f"Check memo for {item['emp'].full_name}:", initialvalue=item["memo"],
                                   parent=self)
        if s is None:
            return
        item["memo"] = s.strip()[:80]
        cd = self._check_date()
        if cd is not None:
            item["record"] = self._compute(item, 0, cd)
        self._render_preview()

    def record_and_print(self) -> None:
        if self.book is None:
            return
        if not self.preview or self.preview_period != self.selected_period():
            messagebox.showinfo("Pay & Checks", "Click Calculate first to preview this period's checks.",
                                parent=self)
            return
        cd = self._check_date()
        if cd is None:
            return
        items = sorted(self.preview.values(), key=lambda it: it["emp"].display_name.lower())
        dups = [it for it in items if it["dup"]]
        if dups:
            ans = messagebox.askyesnocancel(
                "Already paid", f"{len(dups)} contractor(s) already have a check for this period:\n\n" +
                "\n".join(it["emp"].full_name for it in dups) +
                "\n\nYes = include them anyway (extra check)\nNo = skip them", parent=self)
            if ans is None:
                return
            if not ans:
                items = [it for it in items if not it["dup"]]
        if not items:
            messagebox.showinfo("Pay & Checks", "There are no checks to record.", parent=self)
            return
        start_no = simpledialog.askinteger("Starting check number", "First check number:", parent=self,
                                           initialvalue=int(self.book.company.next_check_number), minvalue=1)
        if start_no is None:
            return
        numbers = list(range(start_no, start_no + len(items)))
        clash = sorted(set(numbers) & {r.check_number for r in self.book.payroll})
        if clash:
            messagebox.showerror("Pay & Checks", f"Check number(s) already used: {', '.join(map(str, clash))}.",
                                 parent=self)
            return
        records = [self._compute(it, n, cd) for it, n in zip(items, numbers)]
        total = sum((r.net_pay for r in records), Decimal("0"))
        if not messagebox.askyesno(
                "Record pay", f"Record {len(records)} check(s) totaling {fmt_money(total)}\n"
                f"numbered {numbers[0]}-{numbers[-1]}, dated {cd:%m/%d/%Y}?\n\n"
                "The workbook will be saved and the checks PDF opened.", parent=self):
            return
        self.book.record_payroll(records)
        self.app.mark_dirty()
        if not self.app.save():
            messagebox.showwarning("Pay & Checks", "The checks were recorded but the workbook is NOT saved yet. "
                                   "Save it before closing.", parent=self)
        p = self.preview_period
        self.on_period_change()
        self.app.refresh_all()
        self._print_checks(records, f"Checks {p[0]:%Y-%m-%d} to {p[1]:%Y-%m-%d} #{numbers[0]}-{numbers[-1]}.pdf",
                           f"Recorded {len(records)} checks.")

    def _start_position(self, count: int | None) -> int | None:
        """Ask which slot to start at, but only for the 3-per-page layouts."""
        if self.layout in THREE_UP_LAYOUTS:
            return self.app.ask_start_position(count)
        return 1

    def _print_checks(self, records: list[PayrollRecord], filename: str, done_msg: str) -> None:
        pos = self._start_position(len(records))
        if pos is None:
            self.app.status(done_msg + " Printing cancelled - use Reprint Checks to print them.")
            return
        if self.layout == "handwrite":
            filename = filename.replace("Checks", "Checks to Write", 1)
        out = self.app.output_path(filename)
        if out is None:
            return
        path = checks.render_checks(records, self.book, out, start_position=pos)
        self.app.status(f"{done_msg} PDF: {Path(path).name}")
        open_file(Path(path))

    def _selected_records(self) -> list[PayrollRecord]:
        p = self.selected_period()
        if p is None:
            return []
        recs = [r for r in self.book.records_for_period(*p) if not r.voided]
        sel = {int(i) for i in self.rtree.selection()}
        chosen = [r for r in recs if r.check_number in sel]
        return chosen or recs

    def reprint(self) -> None:
        if self.book is None:
            return
        recs = sorted(self._selected_records(), key=lambda r: r.check_number)
        if not recs:
            messagebox.showinfo("Reprint Checks", "No recorded (non-voided) checks for this period.", parent=self)
            return
        self._print_checks(recs, f"Checks reprint #{recs[0].check_number}-{recs[-1].check_number}.pdf",
                           f"Reprinted {len(recs)} check(s).")

    def print_register(self) -> None:
        p = self.selected_period()
        if self.book is None or p is None:
            return
        recs = sorted(self.book.records_for_period(*p), key=lambda r: r.check_number)
        if not recs:
            messagebox.showinfo("Print Register", "No checks recorded for this period yet.", parent=self)
            return
        out = self.app.output_path(f"Check Register {p[0]:%Y-%m-%d} to {p[1]:%Y-%m-%d}.pdf")
        if out is None:
            return
        path = checks.render_register(recs, self.book, out,
                                      title=f"Check Register  {p[0]:%m/%d/%Y} - {p[1]:%m/%d/%Y}")
        self.app.status(f"Register saved: {Path(path).name}")
        open_file(Path(path))

    def void_check(self) -> None:
        if self.book is None:
            return
        sel = self.rtree.selection()
        n = simpledialog.askinteger("Void Check", "Check number to void:", parent=self,
                                    initialvalue=int(sel[0]) if sel else None, minvalue=1)
        if n is None:
            return
        rec = next((r for r in self.book.payroll if r.check_number == n), None)
        if rec is None:
            messagebox.showerror("Void Check", f"Check #{n} was not found in this workbook.", parent=self)
            return
        if rec.voided:
            messagebox.showinfo("Void Check", f"Check #{n} is already voided.", parent=self)
            return
        if not messagebox.askyesno("Void Check", f"Void check #{n} to {rec.employee_name} for "
                                   f"{fmt_money(rec.net_pay)}?\n\nThe check stays in the log marked Voided.",
                                   parent=self):
            return
        self.book.void_check(n)
        self.app.mark_dirty()
        self.load_recorded()
        self.app.status(f"Check #{n} voided.{self.app.save_hint()}")

    def alignment_test(self) -> None:
        if self.book is None:
            return
        if self.layout == "handwrite":
            messagebox.showinfo("Alignment test", "No alignment test is needed when checks are written by hand.",
                                parent=self)
            return
        pos = self._start_position(None)
        if pos is None:
            return
        out = self.app.output_path("Check Alignment Test.pdf")
        if out is None:
            return
        path = checks.render_alignment_test(self.book, out, start_position=pos)
        self.app.status(f"Alignment test saved: {Path(path).name}")
        open_file(Path(path))
