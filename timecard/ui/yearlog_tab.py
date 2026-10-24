"""Year Log tab: hours by pay period and employee, the monthly summary, and CSV export."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, ttk
from typing import TYPE_CHECKING

from ..models import TimeEntry
from .. import calc
from .common import MONTHS, MONTH_NAMES, documents_dir, fmt_hours, fmt_money, safe_filename, style_tree
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class YearLogTab(PinLockable, ttk.Frame):
    pin_key = "yearlog"
    pin_what = "Year Log records"
    pin_purpose = "view the year's hours"

    """One row per pay period and employee: hours worked, lunch deducted and that period's notes. Expand a row
    for its days. Filter by employee and month; click a column heading to sort."""

    # (column, heading, width, anchor)
    COLS = (("period", "Pay period", 240, "w"), ("emp", "Employee", 180, "w"), ("hours", "Hours worked", 100, "e"),
            ("leave", "Time off", 130, "w"), ("lunch", "Lunch (min)", 90, "center"), ("notes", "Notes", 340, "w"))

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.emp_ids: list[str | None] = [None]
        self.sort_col, self.sort_desc = "period", False

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Employee").pack(side="left")
        self.cb_emp = ttk.Combobox(top, state="readonly", width=30)
        self.cb_emp.pack(side="left", padx=(6, 18))
        self.cb_emp.bind("<<ComboboxSelected>>", lambda e: self.reload())
        ttk.Label(top, text="Month").pack(side="left")
        self.cb_month = ttk.Combobox(top, state="readonly", width=14, values=["All year"] + MONTH_NAMES)
        self.cb_month.current(0)
        self.cb_month.pack(side="left", padx=6)
        self.cb_month.bind("<<ComboboxSelected>>", lambda e: self.reload())
        ttk.Button(top, text="Export CSV…", command=self.export_csv).pack(side="right")
        ttk.Button(top, text="Refresh", command=self.reload).pack(side="right", padx=6)

        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True, pady=(10, 0))

        f1 = ttk.Frame(pw)
        self.tree = ttk.Treeview(f1, columns=[c[0] for c in self.COLS], show="tree headings")
        self.tree.column("#0", width=28, minwidth=28, stretch=False)   # expand arrow for the days
        for c, h, w, a in self.COLS:
            self.tree.heading(c, text=h, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("notes", "emp")))
        style_tree(self.tree)
        vsb = ttk.Scrollbar(f1, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._on_double)
        pw.add(f1, weight=3)

        f2 = ttk.Frame(pw, padding=(0, 8, 0, 0))
        ttk.Label(f2, text="Monthly hours and total paid by person (Paid YTD = non-voided checks, for 1099-NEC)",
                  style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        mcols = ["emp"] + MONTHS + ["total", "paid"]
        self.mtree = ttk.Treeview(f2, columns=mcols, show="headings", height=6)
        self.mtree.heading("emp", text="Employee")
        self.mtree.column("emp", width=180, anchor="w")
        for m in MONTHS:
            self.mtree.heading(m, text=m)
            self.mtree.column(m, width=58, anchor="e", stretch=False)
        self.mtree.heading("total", text="Total")
        self.mtree.column("total", width=80, anchor="e", stretch=False)
        self.mtree.heading("paid", text="Paid YTD")
        self.mtree.column("paid", width=110, anchor="e", stretch=False)
        style_tree(self.mtree)
        self.mtree.pack(fill="both", expand=True)
        pw.add(f2, weight=1)
        ttk.Label(self, text="Click a column heading to sort. Click + on a row to see its days; double-click "
                  "a day or row to open it on the Time Card tab.", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

    @property
    def book(self):
        return self.app.book

    def refresh(self) -> None:
        if self.book is None:
            return
        cur = self.selected_eid()
        # the filter offers current team members only; former ones' history still shows under "All"
        emps = self.book.active_employees()
        self.emp_ids = [None] + [e.employee_id for e in emps]
        self.cb_emp["values"] = ["All employees"] + [f"{e.display_name}  ({e.employee_id})" for e in emps]
        self.cb_emp.current(self.emp_ids.index(cur) if cur in self.emp_ids else 0)
        self.reload()

    def selected_eid(self) -> str | None:
        i = self.cb_emp.current()
        return self.emp_ids[i] if 0 <= i < len(self.emp_ids) else None

    def _range(self) -> tuple[date, date]:
        y = self.book.company.year
        m = self.cb_month.current()
        if m <= 0:
            return date(y, 1, 1), date(y, 12, 31)
        start = date(y, m, 1)
        end = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
        return start, end

    def _emp_name(self, eid: str) -> str:
        e = self.book.employees.get(eid)
        if e is None:
            return eid
        return e.display_name + ("" if e.active else " (former)")

    # ----- pay period rows
    def groups(self) -> list[calc.PeriodLog]:
        """Pay period x employee groups for the current filters, in the current sort order."""
        start, end = self._range()
        groups = calc.period_log(self.book.entries_for(self.selected_eid(), start, end), self.book.company)
        keys = {
            "period": lambda g: (g.start, self._emp_name(g.employee_id).lower()),
            "emp": lambda g: (self._emp_name(g.employee_id).lower(), g.start),
            "hours": lambda g: (g.hours, g.start),
            "leave": lambda g: (g.leave_hours, g.start),
            "lunch": lambda g: (g.lunch_minutes, g.start),
            "notes": lambda g: (g.notes_text().lower(), g.start),
        }
        return sorted(groups, key=keys[self.sort_col], reverse=self.sort_desc)

    def _period_label(self, g: calc.PeriodLog) -> str:
        """The period's dates, clipped to the month filter (marked partial) so totals match the month."""
        start, end = self._range()
        s, e = max(g.start, start), min(g.end, end)
        return f"{s:%a %m/%d} – {e:%a %m/%d/%Y}" + ("  (partial)" if (s, e) != (g.start, g.end) else "")

    def sort_by(self, col: str) -> None:
        self.sort_desc = not self.sort_desc if col == self.sort_col else False
        self.sort_col = col
        self.reload()

    def _show_sort_arrow(self) -> None:
        for c, h, _w, _a in self.COLS:
            arrow = (" ▼" if self.sort_desc else " ▲") if c == self.sort_col else ""
            self.tree.heading(c, text=h + arrow)

    @staticmethod
    def _punches(e: TimeEntry) -> str:
        if calc.hours_type(e) != "Worked":
            return ""   # the Time off column says what the day was
        if e.salary_hours is not None:
            return "hours entered"
        pairs = [(a, b) for a, b in ((e.in1, e.out1), (e.in2, e.out2), (e.in3, e.out3)) if a or b]
        return ", ".join(f"{calc.format_time(a) or '?'}–{calc.format_time(b) or '?'}" for a, b in pairs)

    def rows(self) -> list[list[str]]:
        """Export rows: one per pay period and employee."""
        start, end = self._range()
        out = []
        for g in self.groups():
            out.append([max(g.start, start).isoformat(), min(g.end, end).isoformat(), self._emp_name(g.employee_id),
                        fmt_hours(g.hours), fmt_hours(g.leave_hours), g.leave_text(), str(g.lunch_minutes),
                        g.notes_text(), g.employee_id])
        return out

    def reload(self) -> None:
        if self.book is None:
            return
        self.tree.delete(*self.tree.get_children())
        self._show_sort_arrow()
        groups = self.groups()
        total_h, total_off, total_l, days = Decimal("0"), Decimal("0"), 0, 0
        for g in groups:
            total_h += g.hours
            total_off += g.leave_hours
            total_l += g.lunch_minutes
            days += len(g.entries)
            emp = self.book.employees.get(g.employee_id)
            pid = f"{g.employee_id}|{g.start.isoformat()}|period"
            self.tree.insert("", "end", iid=pid, tags=("former",) if emp is not None and not emp.active else (),
                             values=(self._period_label(g), self._emp_name(g.employee_id), fmt_hours(g.hours),
                                     g.leave_text(), g.lunch_minutes or "", g.notes_text()))
            for e in g.entries:
                off = (f"{calc.hours_type(e)} {fmt_hours(calc.leave_hours(e))}" if calc.is_leave(e)
                       else "Closed" if calc.is_closed(e) else "")
                self.tree.insert(pid, "end", iid=f"{e.employee_id}|{e.work_date.isoformat()}|day",
                                 values=(f"{e.work_date:%a %m/%d}   {self._punches(e)}", "",
                                         fmt_hours(calc.day_hours(e)), off, e.lunch_deduct_min or "", e.notes))
        self.tree.insert("", "end", iid="__total__", tags=("total",),
                         values=(f"Total ({len(groups)} pay periods, {days} days)", "", fmt_hours(total_h),
                                 fmt_hours(total_off) if total_off else "", total_l or "", ""))
        # monthly summary (whole year, respecting the employee filter)
        self.mtree.delete(*self.mtree.get_children())
        y = self.book.company.year
        year_entries = self.book.entries_for(self.selected_eid(), date(y, 1, 1), date(y, 12, 31))
        by_emp: dict[str, list[Decimal]] = {}
        for e in year_entries:
            months = by_emp.setdefault(e.employee_id, [Decimal("0")] * 12)
            months[e.work_date.month - 1] += calc.day_hours(e)
        paid = self.paid_ytd()
        want = self.selected_eid()
        for eid in paid:   # anyone paid this year shows, even with no hours logged
            if want is None or eid == want:
                by_emp.setdefault(eid, [Decimal("0")] * 12)
        grand = [Decimal("0")] * 12
        grand_paid = Decimal("0")
        for eid in sorted(by_emp, key=lambda k: self._emp_name(k).lower()):
            months = by_emp[eid]
            grand = [a + b for a, b in zip(grand, months)]
            grand_paid += paid.get(eid, Decimal("0"))
            emp = self.book.employees.get(eid)
            tags = ("former",) if emp is not None and not emp.active else ()
            self.mtree.insert("", "end", tags=tags,
                              values=[self._emp_name(eid)] + [fmt_hours(m) if m else "" for m in months]
                              + [fmt_hours(sum(months)), fmt_money(paid.get(eid, Decimal("0")))])
        if len(by_emp) != 1:
            self.mtree.insert("", "end", tags=("total",), values=["All"] + [
                fmt_hours(m) if m else "" for m in grand] + [fmt_hours(sum(grand)), fmt_money(grand_paid)])

    def paid_ytd(self) -> dict[str, Decimal]:
        """Sum of non-voided check amounts (net_pay) per person for the workbook year."""
        y = self.book.company.year
        out: dict[str, Decimal] = {}
        for r in self.book.payroll:
            if not r.voided and r.check_date.year == y:
                out[r.employee_id] = out.get(r.employee_id, Decimal("0")) + r.net_pay
        return out

    def _on_double(self, event) -> None:
        """A day opens that day on the Time Card; a pay period row opens its first day."""
        iid = self.tree.identify_row(event.y)
        if not iid or iid.count("|") != 2:
            return
        eid, iso, kind = iid.split("|")
        if kind == "period":
            start, end = self._range()
            iso = max(date.fromisoformat(iso), start).isoformat()
        self.app.goto_timecard(eid, date.fromisoformat(iso))

    def export_csv(self) -> None:
        if self.book is None:
            return
        start, end = self._range()
        initial_dir = self.book.path.parent if self.book.path else documents_dir()
        suffix = "" if self.cb_month.current() <= 0 else f" {MONTH_NAMES[start.month - 1]}"
        path = filedialog.asksaveasfilename(parent=self, title="Export time log", defaultextension=".csv",
                                            initialdir=str(initial_dir),
                                            initialfile=safe_filename(f"Time Log {self.book.company.year}{suffix}.csv"),
                                            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        rows = self.rows()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["PeriodStart", "PeriodEnd", "Employee", "HoursWorked", "TimeOffHours", "TimeOff",
                        "LunchDeductMin", "Notes", "EmployeeID"])
            w.writerows(rows)
            w.writerow(["Total", "", "", fmt_hours(sum(Decimal(r[3].replace(",", "")) for r in rows)),
                        fmt_hours(sum(Decimal(r[4].replace(",", "")) for r in rows)), "",
                        str(sum(int(r[6]) for r in rows)), "", ""])
        self.app.status(f"Exported {len(rows)} pay period rows to {Path(path).name}")
