"""Time Card tab: one person's pay period, typed punches, salary hours and time off."""
from __future__ import annotations

import dataclasses
import tkinter as tk
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from ..models import TimeEntry
from .. import calc, checks
from ..storage import TimecardBook
from .common import (
    F, FlowFrame, ScrollFrame, fmt_hours, fmt_period, is_salaried, open_file, paint_cell, period_index_for, set_invalid,
)
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class _DayRow:
    __slots__ = ("d", "eid", "cells", "lunch", "notes", "hours_lbl", "day_lbl", "salary", "kind")

    def __init__(self, d: date, eid: str):
        self.d, self.eid = d, eid
        self.cells: dict[str, tk.Entry] = {}
        self.lunch: ttk.Spinbox | None = None
        self.notes: tk.Entry | None = None
        self.salary: tk.Entry | None = None       # typed hours: salaried hours, or a time off day's hours
        self.kind: ttk.Combobox | None = None     # Worked / PTO / Holiday / Sick / Closed
        self.hours_lbl: ttk.Label | None = None
        self.day_lbl: ttk.Label | None = None


class TimeCardTab(PinLockable, ttk.Frame):
    pin_key = "timecard"
    pin_what = "Time cards"
    pin_purpose = "enter or view hours"

    TIME_FIELDS = ("in1", "out1", "in2", "out2")

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.emp_ids: list[str] = []
        self.periods: list[tuple[date, date]] = []
        self.rows: list[_DayRow] = []
        self.week_lbls: dict[date, ttk.Label] = {}
        self.current_row = 0
        self.commit_fns: dict[str, callable] = {}

        top = FlowFrame(self)
        top.pack(fill="x")
        g1 = top.group()
        ttk.Label(g1, text="Employee").pack(side="left")
        self.v_emp = tk.StringVar()
        self.cb_emp = ttk.Combobox(g1, textvariable=self.v_emp, state="readonly", width=26)
        self.cb_emp.pack(side="left", padx=(6, 0))
        self.cb_emp.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
        g2 = top.group()
        ttk.Label(g2, text="Pay period").pack(side="left")
        ttk.Button(g2, text="\u25C0", width=3, command=lambda: self.step_period(-1)).pack(side="left", padx=(6, 2))
        self.v_period = tk.StringVar()
        self.cb_period = ttk.Combobox(g2, textvariable=self.v_period, state="readonly", width=31)
        self.cb_period.pack(side="left")
        self.cb_period.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
        ttk.Button(g2, text="\u25B6", width=3, command=lambda: self.step_period(1)).pack(side="left", padx=(2, 6))
        ttk.Button(g2, text="Today", command=self.go_today).pack(side="left")

        actions = FlowFrame(self, gap=8, padding=(0, 8, 0, 0))
        actions.pack(fill="x")
        a1 = actions.group()
        ttk.Button(a1, text="Copy previous day", command=self.copy_previous_day).pack(side="left")
        ttk.Button(a1, text="Clear day", command=self.clear_day).pack(side="left", padx=6)
        a2 = actions.group()
        ttk.Button(a2, text="Print time card", style="Accent.TButton",
                   command=self.print_timecard).pack(side="left")
        ttk.Label(self, text="Type times like 8, 830a, 5:15 pm or 17:15. Tab / Enter moves to the next cell.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.v_salary_hint = tk.StringVar(value="")   # set in rebuild() for salaried people
        hint = ttk.Label(self, textvariable=self.v_salary_hint, style="Muted.TLabel", justify="left")
        hint.pack(anchor="w", fill="x", pady=(0, 6))
        hint.bind("<Configure>", lambda e: hint.configure(wraplength=max(e.width - 8, 200)))

        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self.grid_frame = self.scroll.inner

        foot = ttk.Frame(self, style="Card.TFrame", padding=(14, 10))
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        self.scroll.pack_forget()   # re-pack after the footer so the footer always keeps its height
        self.scroll.pack(fill="both", expand=True)
        self.v_reg = tk.StringVar(value="0.00")
        self.v_ot = tk.StringVar(value="0.00")
        self.v_tot = tk.StringVar(value="0.00")
        self.v_leave = tk.StringVar(value="0.00")
        self.foot_boxes: dict[str, ttk.Frame] = {}
        for key, label, var in (("reg", "Regular hours", self.v_reg), ("ot", "Overtime hours", self.v_ot),
                                ("tot", "Total hours worked", self.v_tot), ("leave", "Time off (PTO etc.)",
                                                                           self.v_leave)):
            box = ttk.Frame(foot, style="CardInner.TFrame")
            ttk.Label(box, text=label, style="CardMuted.TLabel").pack(anchor="w")
            ttk.Label(box, textvariable=var, style="CardBig.TLabel").pack(anchor="w")
            self.foot_boxes[key] = box
        self._foot_layout: tuple | None = None
        self._layout_footer()

    def _layout_footer(self, show_leave: bool = False) -> None:
        """Regular / Overtime / Total when overtime is enabled, otherwise just Total; plus Time off for salaried
        people or when there is any."""
        ot = bool(self.book is not None and getattr(self.book.company, "overtime_enabled", False))
        if (ot, show_leave) == self._foot_layout:
            return
        self._foot_layout = (ot, show_leave)
        for box in self.foot_boxes.values():
            box.pack_forget()
        keys = ("reg", "ot", "tot") if ot else ("tot",)
        for key in keys + (("leave",) if show_leave else ()):
            self.foot_boxes[key].pack(side="left", padx=(0, 36))

    # ----- data refresh
    @property
    def book(self) -> TimecardBook | None:
        return self.app.book

    def refresh(self, keep_selection: bool = True) -> None:
        if self.book is None:
            return
        old_eid = self.selected_eid() if keep_selection else None
        old_period = self.selected_period() if keep_selection else None
        self._load_employee_list(old_eid)
        self.periods = calc.periods_in_year(self.book.company)
        self.cb_period["values"] = [fmt_period(p) for p in self.periods]
        if old_period in self.periods:
            idx = self.periods.index(old_period)
        else:
            idx = period_index_for(self.periods, date.today())
        if self.periods:
            self.cb_period.current(idx)
        self.rebuild()

    def _load_employee_list(self, want: str | None = None) -> None:
        # former team members (Active = No) are kept in the workbook but never offered here
        emps = self.book.active_employees()
        self.emp_ids = [e.employee_id for e in emps]
        labels = [f"{e.display_name}  ({e.employee_id})" for e in emps]
        self.cb_emp["values"] = labels
        if want in self.emp_ids:
            self.cb_emp.current(self.emp_ids.index(want))
        elif self.emp_ids:
            self.cb_emp.current(0)
        else:
            self.v_emp.set("")

    def selected_eid(self) -> str | None:
        i = self.cb_emp.current()
        return self.emp_ids[i] if 0 <= i < len(self.emp_ids) else None

    def selected_period(self) -> tuple[date, date] | None:
        i = self.cb_period.current()
        return self.periods[i] if 0 <= i < len(self.periods) else None

    def step_period(self, delta: int) -> None:
        self.commit_pending()
        i = self.cb_period.current() + delta
        if 0 <= i < len(self.periods):
            self.cb_period.current(i)
            self.rebuild()

    def go_today(self) -> None:
        self.commit_pending()
        if self.periods:
            self.cb_period.current(period_index_for(self.periods, date.today()))
            self.rebuild()
            self.focus_date(date.today())

    def show(self, eid: str, d: date) -> None:
        """Jump to an employee/day (used by Year Log double-click). Former team members are not shown."""
        self.commit_pending()
        emp = self.book.employees.get(eid)
        if emp is None or not emp.active:
            name = emp.full_name if emp else eid
            self.app.status(f"{name} is no longer with the company; their time card is not shown "
                            "(history stays in the Year Log).", error=True)
            return
        self._load_employee_list(eid)
        idx = period_index_for(self.periods, d)
        if self.periods:
            self.cb_period.current(idx)
        self.rebuild()
        self.focus_date(d)

    def focus_date(self, d: date) -> None:
        for i, r in enumerate(self.rows):
            if r.d == d:
                self.current_row = i
                self.update_idletasks()
                r.cells["in1"].focus_set()
                self.scroll.scroll_into_view(r.cells["in1"])
                return

    # ----- grid
    def rebuild(self) -> None:
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.rows.clear()
        self.week_lbls.clear()
        self.commit_fns.clear()
        self.current_row = 0
        eid, period = self.selected_eid(), self.selected_period()
        g = self.grid_frame
        if self.book is None or eid is None or period is None:
            ttk.Label(g, text="Add an employee on the Employees tab to start a time card.",
                      style="Muted.TLabel", padding=20).grid(row=0, column=0)
            self.update_totals()
            return
        start, end = period
        emp = self.book.employees.get(eid)
        # Everyone gets a Type box (Worked / PTO / Holiday / Sick / Closed). Salaried people type their hours; for
        # hourly people worked hours are the punches and the hours box is only for a time off day's hours.
        salaried = self._salaried = emp is not None and is_salaried(emp)
        heads = ["Day / Date", "In", "Out", "In", "Out", "Lunch Deduct (min)", "",
                 "Salary hours" if salaried else "Time off hrs", "Type", "Hours", "Notes"]
        c_hours, c_notes, ncols = len(heads) - 2, len(heads) - 1, len(heads)
        for c, h in enumerate(heads):
            ttk.Label(g, text=h, style="GridHead.TLabel").grid(row=0, column=c, sticky="w", padx=4, pady=(0, 4))
        ttk.Separator(g).grid(row=1, column=0, columnspan=ncols, sticky="ew", pady=(0, 4))
        for c in range(ncols):
            g.columnconfigure(c, weight=0)
        g.columnconfigure(c_notes, weight=1)
        self.v_salary_hint.set(
            "Salaried: type the day's hours in Salary hours (it replaces In/Out). " if salaried else
            "Worked hours come from the In/Out times. ")
        self.v_salary_hint.set(self.v_salary_hint.get() +
                               "Choose PTO, Holiday or Sick and the day's hours fill in from Company Settings (change "
                               "them for a part day); Closed means the business was closed (0 hours). Time off isn't "
                               "counted as hours worked.")

        r = 2
        d = start
        wk = self.book.company.week_start
        today = date.today()
        while d <= end:
            row = _DayRow(d, eid)
            idx = len(self.rows)
            entry = self.book.get_entry(eid, d)
            style = "Today.TLabel" if d == today else ("Weekend.TLabel" if d.weekday() >= 5 else "TLabel")
            row.day_lbl = ttk.Label(g, text=f"{d:%a  %m/%d}", style=style, width=12)
            row.day_lbl.grid(row=r, column=0, sticky="w", padx=4, pady=2)
            for c, fname in enumerate(self.TIME_FIELDS, start=1):
                e = tk.Entry(g, width=10, font=F("base"), relief="flat", bd=1, justify="center")
                paint_cell(e)
                e.insert(0, calc.format_time(getattr(entry, fname)))
                e.grid(row=r, column=c, padx=3, pady=2, ipady=2)
                commit = (lambda rw=row, fn=fname, w=e: self._commit_time(rw, fn, w))
                self._bind_cell(e, idx, commit)
                e.bind("<KeyRelease>", lambda ev, rw=row, fn=fname, w=e: self._live_time(rw, fn, w), add="+")
                row.cells[fname] = e
            sp = ttk.Spinbox(g, from_=0, to=240, increment=5, width=5, justify="center",
                             command=lambda rw=row: self._commit_lunch(rw))
            sp.set(str(entry.lunch_deduct_min or 0))
            sp.grid(row=r, column=5, padx=3, pady=2, sticky="w")
            self._bind_cell(sp, idx, lambda rw=row: self._commit_lunch(rw))
            row.lunch = sp
            b = ttk.Button(g, text="+Lunch", width=7, takefocus=0, style="Small.TButton",
                           command=lambda rw=row: self._add_lunch(rw))
            b.grid(row=r, column=6, padx=(0, 6), pady=2)
            se = tk.Entry(g, width=7, font=F("base"), relief="flat", bd=1, justify="center")
            paint_cell(se)
            se.grid(row=r, column=7, padx=3, pady=2, ipady=2)
            self._bind_cell(se, idx, lambda rw=row, w=se: self._commit_salary_hours(rw, w))
            row.salary = se
            kb = ttk.Combobox(g, values=list(calc.HOURS_TYPES), state="readonly", width=8)
            kb.grid(row=r, column=8, padx=3, pady=2, sticky="w")
            kb.bind("<<ComboboxSelected>>", lambda ev, rw=row, w=kb: self._choose_type(rw, w.get()))
            row.kind = kb
            self._fill_hours_type(row, entry)
            row.hours_lbl = ttk.Label(g, text=self._hours_text(entry), width=13, anchor="e",
                                      style="Hours.TLabel")
            row.hours_lbl.grid(row=r, column=c_hours, padx=6, pady=2, sticky="e")
            ne = tk.Entry(g, font=F("base"), relief="flat", bd=1)
            paint_cell(ne)
            ne.insert(0, entry.notes)
            ne.grid(row=r, column=c_notes, padx=3, pady=2, sticky="ew", ipady=2)
            self._bind_cell(ne, idx, lambda rw=row, w=ne: self._commit_notes(rw, w))
            row.notes = ne
            self.rows.append(row)
            r += 1
            nxt = d + timedelta(days=1)
            if nxt > end or calc.week_start_for(nxt, wk) != calc.week_start_for(d, wk):
                ws = calc.week_start_for(d, wk)
                lbl_txt = f"Week of {max(ws, start):%m/%d} subtotal"
                ttk.Label(g, text=lbl_txt, style="Subtotal.TLabel").grid(row=r, column=0, columnspan=c_hours,
                                                                       sticky="e", padx=6, pady=(2, 2))
                wl = ttk.Label(g, text="0.00", style="SubtotalVal.TLabel", width=13, anchor="e")
                wl.grid(row=r, column=c_hours, padx=6, sticky="e")
                self.week_lbls[ws] = wl
                r += 1
                if nxt <= end:
                    ttk.Separator(g).grid(row=r, column=0, columnspan=ncols, sticky="ew", pady=4)
                    r += 1
            d = nxt
        self.scroll.scroll_top()
        self.update_totals()

    def _bind_cell(self, w, idx: int, commit) -> None:
        self.commit_fns[str(w)] = commit

        def on_focus_in(_e, i=idx, w=w):
            self.current_row = i
            self.scroll.scroll_into_view(w)

        def on_return(_e, w=w):
            commit()
            nxt = w.tk_focusNext()
            if nxt is not None:
                nxt.focus_set()
            return "break"

        w.bind("<FocusIn>", on_focus_in, add="+")
        w.bind("<FocusOut>", lambda e: commit(), add="+")
        w.bind("<Return>", on_return)
        w.bind("<KP_Enter>", on_return)

    def commit_pending(self) -> None:
        """Commit the cell that currently has focus (e.g. before saving)."""
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            w = None
        if w is not None:
            fn = self.commit_fns.get(str(w))
            if fn:
                fn()

    def _update_entry(self, row: _DayRow, **changes) -> None:
        if self.book is None:
            return
        cur = self.book.get_entry(row.eid, row.d)
        new = dataclasses.replace(cur, **changes)
        if new != cur:
            self.book.set_entry(new)
            self.app.mark_dirty()
        self.update_totals()

    def _preceding_punch(self, row: _DayRow, fname: str) -> time | None:
        """Nearest earlier punch on the same day, so '5' after an 8:00 AM In parses as 5:00 PM."""
        e = self.book.get_entry(row.eid, row.d)
        idx = self.TIME_FIELDS.index(fname)
        for prev in reversed(self.TIME_FIELDS[:idx]):
            t = getattr(e, prev)
            if t is not None:
                return t
        return None

    def _commit_time(self, row: _DayRow, fname: str, w: tk.Entry) -> None:
        if not w.winfo_exists() or self.book is None:
            return
        text = w.get().strip()
        try:
            t = calc.parse_time(text, after=self._preceding_punch(row, fname))
        except ValueError:
            set_invalid(w, True)
            self.app.status(f"{row.d:%a %m/%d}: '{text}' is not a valid time. Try 8:30 AM, 830a or 17:15.",
                            error=True)
            return
        set_invalid(w, False)
        formatted = calc.format_time(t)
        if w.get() != formatted:
            w.delete(0, "end")
            w.insert(0, formatted)
        self._update_entry(row, **{fname: t})

    def _live_time(self, row: _DayRow, fname: str, w: tk.Entry) -> None:
        """Update the hours label while typing, without committing."""
        if self.book is None:
            return
        try:
            t = calc.parse_time(w.get(), after=self._preceding_punch(row, fname))
        except ValueError:
            return
        e = dataclasses.replace(self.book.get_entry(row.eid, row.d), **{fname: t})
        row.hours_lbl.configure(text=self._hours_text(e))

    @staticmethod
    def _hours_text(e: TimeEntry) -> str:
        """The day's hours; time off and closed days are labelled ('8.00 PTO', '0.00 Closed')."""
        return fmt_hours(calc.entry_hours(e)) + ("" if calc.hours_type(e) == "Worked" else f" {calc.hours_type(e)}")

    def _commit_salary_hours(self, row: _DayRow, w: tk.Entry) -> None:
        if not w.winfo_exists() or self.book is None:
            return
        try:
            hours = calc.parse_hours(w.get())
        except ValueError:
            set_invalid(w, True)
            self.app.status(f"{row.d:%a %m/%d}: enter hours like 8, 7.5 or 7:30 (0 to 24).", error=True)
            return
        set_invalid(w, False)
        shown = fmt_hours(hours) if hours is not None else ""
        if w.get() != shown:
            w.delete(0, "end")
            w.insert(0, shown)
        self._update_entry(row, salary_hours=hours)

    def _choose_type(self, row: _DayRow, kind: str) -> None:
        """Type chosen: a PTO / Holiday / Sick day takes its hours from Company Settings; Closed is 0 hours; back to
        Worked drops the time off hours so the punches (or the salaried hours typed next) count again."""
        if self.book is None:
            return
        cur = self.book.get_entry(row.eid, row.d)
        changes: dict = {"hours_type": kind}
        if kind in calc.LEAVE_TYPES:
            changes["salary_hours"] = calc.leave_default_hours(self.book.company, kind)
        elif calc.hours_type(cur) != "Worked" or kind == "Closed":
            changes["salary_hours"] = None
        self._update_entry(row, **changes)
        self._fill_hours_type(row, self.book.get_entry(row.eid, row.d))

    def _fill_hours_type(self, row: _DayRow, e: TimeEntry) -> None:
        """Show the day's typed hours and type. For hourly people the hours box is only open on a time off day
        (or when hours were already typed there, so they can be cleared)."""
        se = row.salary
        se.configure(state="normal")
        set_invalid(se, False)
        se.delete(0, "end")
        se.insert(0, fmt_hours(e.salary_hours) if e.salary_hours is not None else "")
        if not (getattr(self, "_salaried", False) or calc.is_leave(e) or e.salary_hours is not None):
            se.configure(state="disabled")
        row.kind.set(calc.hours_type(e))

    def _commit_lunch(self, row: _DayRow) -> None:
        sp = row.lunch
        if sp is None or not sp.winfo_exists():
            return
        text = sp.get().strip() or "0"
        try:
            v = int(float(text))
            if not 0 <= v <= 240:
                raise ValueError
        except ValueError:
            cur = self.book.get_entry(row.eid, row.d).lunch_deduct_min
            sp.set(str(cur))
            self.app.status(f"Lunch deduct must be a number of minutes from 0 to 240.", error=True)
            return
        if sp.get() != str(v):
            sp.set(str(v))
        self._update_entry(row, lunch_deduct_min=v)

    def _add_lunch(self, row: _DayRow) -> None:
        mins = int(self.book.company.default_lunch_minutes or 0)
        row.lunch.set(str(mins))
        self._commit_lunch(row)
        self.app.status(f"{row.d:%a %m/%d}: lunch deduct set to {mins} min.")

    def _commit_notes(self, row: _DayRow, w: tk.Entry) -> None:
        if w.winfo_exists():
            self._update_entry(row, notes=w.get().strip())

    def _fill_row(self, row: _DayRow) -> None:
        e = self.book.get_entry(row.eid, row.d)
        for fname, w in row.cells.items():
            set_invalid(w, False)
            w.delete(0, "end")
            w.insert(0, calc.format_time(getattr(e, fname)))
        row.lunch.set(str(e.lunch_deduct_min or 0))
        row.notes.delete(0, "end")
        row.notes.insert(0, e.notes)
        if row.salary is not None:
            self._fill_hours_type(row, e)

    def update_totals(self) -> None:
        if self.book is None or not self.rows:
            self._layout_footer()
            self.v_reg.set("0.00")
            self.v_ot.set("0.00")
            self.v_tot.set("0.00")
            self.v_leave.set("0.00")
            return
        for row in self.rows:
            row.hours_lbl.configure(text=self._hours_text(self.book.get_entry(row.eid, row.d)))
        start, end = self.selected_period()
        entries = self.book.entries_for(self.rows[0].eid, start, end)
        summary = calc.summarize_hours(entries, self.book.company, start, end)
        leave = sum((calc.leave_hours(e) for e in entries), Decimal("0"))
        self.v_leave.set(fmt_hours(leave))
        self._layout_footer(show_leave=leave > 0 or getattr(self, "_salaried", False))
        weeks = dict(summary.by_week)
        for ws, lbl in self.week_lbls.items():
            lbl.configure(text=fmt_hours(weeks.get(ws, Decimal("0"))))
        self.v_reg.set(fmt_hours(summary.regular))
        self.v_ot.set(fmt_hours(summary.overtime))
        self.v_tot.set(fmt_hours(summary.total))

    # ----- actions
    def _target_row(self) -> _DayRow | None:
        if not self.rows:
            return None
        return self.rows[min(max(self.current_row, 0), len(self.rows) - 1)]

    def copy_previous_day(self) -> None:
        self.commit_pending()
        row = self._target_row()
        if row is None:
            return
        prev = self.book.get_entry(row.eid, row.d - timedelta(days=1))
        if not any((prev.in1, prev.out1, prev.in2, prev.out2)) and prev.salary_hours is None:
            self.app.status(f"Nothing to copy: {row.d - timedelta(days=1):%a %m/%d} has no hours.", error=True)
            return
        self._update_entry(row, in1=prev.in1, out1=prev.out1, in2=prev.in2, out2=prev.out2,
                           lunch_deduct_min=prev.lunch_deduct_min, salary_hours=prev.salary_hours,
                           hours_type=prev.hours_type)
        self._fill_row(row)
        self.app.status(f"Copied {row.d - timedelta(days=1):%a %m/%d} to {row.d:%a %m/%d}.")

    def clear_day(self) -> None:
        row = self._target_row()
        if row is None:
            return
        self.book.set_entry(TimeEntry(work_date=row.d, employee_id=row.eid))
        self.app.mark_dirty()
        self._fill_row(row)
        self.update_totals()
        self.app.status(f"Cleared {row.d:%a %m/%d}.")

    def print_timecard(self) -> None:
        self.commit_pending()
        eid, period = self.selected_eid(), self.selected_period()
        if eid is None or period is None:
            return
        emp = self.book.employees[eid]
        start, end = period
        out = self.app.output_path(f"Time Card {emp.last_name} {emp.first_name} {start:%Y-%m-%d}.pdf")
        if out is None:
            return
        path = checks.render_timecard(emp, self.book.entries_for(eid, start, end), start, end, self.book, out)
        self.app.status(f"Time card saved: {Path(path).name}")
        open_file(Path(path))
