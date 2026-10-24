"""Load/save the GridCraft-compatible .xlsx client file. Schema: see DESIGN.md."""
from __future__ import annotations

import base64
import binascii
import dataclasses
import os
import re
import tempfile
import zipfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel
from openpyxl.worksheet.worksheet import Worksheet

from . import branding, calc
from .models import Company, Employee, PayrollRecord, TimeEntry

LOGO_CHUNK = 30_000  # base64 characters per cell on the hidden Branding sheet
LOCKED_MSG = "The workbook is open in another program (GridCraft/Excel). Close it and try again."

FMT_DATE = "yyyy-mm-dd"
FMT_DAY_DATE = "ddd yyyy-mm-dd"
FMT_TIME = "h:mm AM/PM"
FMT_MONEY = "$#,##0.00"
FMT_HOURS = "0.00"

EMPLOYEE_HEADERS = ["EmployeeID", "FirstName", "LastName", "Address1", "Address2",
                    "PayType", "PayRate", "Active", "Notes"]
TIMELOG_HEADERS = ["Date", "EmployeeID", "Employee", "In1", "Out1", "In2", "Out2",
                   "LunchDeductMin", "Hours", "Notes"]
PAYROLL_HEADERS = ["CheckNumber", "CheckDate", "PeriodStart", "PeriodEnd", "EmployeeID", "Employee",
                   "RegularHours", "OvertimeHours", "RegularPay", "OvertimePay", "GrossPay",
                   "FederalTax", "StateTax", "SocialSecurity", "Medicare", "OtherDeductions",
                   "NetPay", "Memo", "Voided"]
# PayrollRecord attribute for each Payroll column (same order as PAYROLL_HEADERS).
PAYROLL_FIELDS = ["check_number", "check_date", "period_start", "period_end", "employee_id",
                  "employee_name", "regular_hours", "overtime_hours", "regular_pay", "overtime_pay",
                  "gross_pay", "federal_tax", "state_tax", "social_security", "medicare",
                  "other_deductions", "net_pay", "memo", "voided"]
PAYROLL_MONEY = {"regular_pay", "overtime_pay", "gross_pay", "federal_tax", "state_tax",
                 "social_security", "medicare", "other_deductions", "net_pay"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

HOURS_FORMULA = ('=MAX(0,ROUND((IF(AND(D{r}<>"",E{r}<>""),MOD(E{r}-D{r},1),0)'
                 '+IF(AND(F{r}<>"",G{r}<>""),MOD(G{r}-F{r},1),0))*24-H{r}/60,2))')


class WorkbookError(Exception):
    """A user-presentable problem reading or writing the workbook."""


# ---------------------------------------------------------------- cell value coercion

def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _to_decimal(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    if isinstance(v, Decimal):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return Decimal(str(v))
    try:
        return Decimal(str(v).replace("$", "").replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal("0")


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(_to_decimal(v))
    except (InvalidOperation, ValueError):
        return default


def _to_bool(v: Any, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("yes", "y", "true", "1", "x")


def _to_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        result = from_excel(v)
        return result.date() if isinstance(result, datetime) else None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%a %Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _to_time(v: Any) -> time | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.time().replace(second=0, microsecond=0)
    if isinstance(v, time):
        return v.replace(second=0, microsecond=0, tzinfo=None)
    if isinstance(v, timedelta):
        v = v.total_seconds() / 86400
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        minutes = round((float(v) % 1) * 1440) % 1440
        return time(minutes // 60, minutes % 60)
    try:
        return calc.parse_time(str(v))
    except ValueError:
        return None


def _header_map(ws: Worksheet) -> dict[str, int]:
    """Header text (lowercase, no spaces) -> 0-based column index."""
    first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), ())
    return {re.sub(r"\s+", "", _text(h)).lower(): i for i, h in enumerate(first) if _text(h)}


def _rows(ws: Worksheet) -> list[dict[str, Any]]:
    """Data rows as dicts keyed by normalized header; fully blank rows skipped."""
    cols = _header_map(ws)
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(c not in (None, "") for c in row):
            continue
        out.append({k: (row[i] if i < len(row) else None) for k, i in cols.items()})
    return out


# ---------------------------------------------------------------- sheet formatting

def _write_header(ws: Worksheet, headers: list[str], widths: list[float]) -> None:
    ws.append(headers)
    bold = Font(bold=True)
    for cell in ws[1]:
        cell.font = bold
    ws.freeze_panes = "A2"
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _fmt_row(ws: Worksheet, row: int, formats: dict[int, str]) -> None:
    for col, fmt in formats.items():
        ws.cell(row=row, column=col).number_format = fmt


# ---------------------------------------------------------------- the book

class TimecardBook:
    """In-memory client file: company settings, employees, time entries, payroll checks."""

    path: Path | None
    company: Company
    employees: dict[str, Employee]
    entries: dict[tuple[str, date], TimeEntry]
    payroll: list[PayrollRecord]
    logo_png: bytes  # company logo (PNG from branding.normalize_logo), b"" when none
    dirty: bool

    def __init__(self, company: Company | None = None, path: Path | None = None) -> None:
        self.path = path
        self.company = company or Company()
        self.employees = {}
        self.entries = {}
        self.payroll = []
        self.logo_png = b""
        self.dirty = False

    @classmethod
    def new(cls, company: Company) -> "TimecardBook":
        book = cls(company)
        book.dirty = True
        return book

    # ------------------------------------------------------------ load

    @classmethod
    def load(cls, path: str | Path) -> "TimecardBook":
        p = Path(path)
        if not p.exists():
            raise WorkbookError(f"File not found: {p}")
        try:
            wb = openpyxl.load_workbook(p, data_only=False)
        except PermissionError as exc:
            raise WorkbookError(LOCKED_MSG) from exc
        except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
            raise WorkbookError(f"{p.name} is not a readable .xlsx workbook ({exc}).") from exc

        names = {n.lower(): n for n in wb.sheetnames}
        if "employees" not in names and "timelog" not in names:
            raise WorkbookError(f"{p.name} is not a TimeCard Pro workbook "
                                "(it has no Employees or TimeLog sheet).")
        book = cls(path=p)

        def sheet(name: str) -> Worksheet | None:
            return wb[names[name]] if name in names else None

        try:
            if (ws := sheet("company")) is not None:
                book.company = cls._read_company(ws)
            if (ws := sheet("employees")) is not None:
                for r in _rows(ws):
                    emp = cls._read_employee(r)
                    if emp:
                        book.employees[emp.employee_id] = emp
            if (ws := sheet("timelog")) is not None:
                for r in _rows(ws):
                    entry = cls._read_entry(r)
                    if entry and not entry.is_empty():
                        book.entries[entry.key] = entry
            if (ws := sheet("payroll")) is not None:
                for r in _rows(ws):
                    rec = cls._read_payroll(r)
                    if rec:
                        book.payroll.append(rec)
            if (ws := sheet("branding")) is not None:
                book.logo_png = cls._read_logo(ws)
        finally:
            wb.close()
        book.dirty = False
        return book

    @staticmethod
    def _read_company(ws: Worksheet) -> Company:
        values: dict[str, Any] = {}
        for row in ws.iter_rows(min_row=2, max_col=2, values_only=True):
            key, value = (tuple(row) + (None, None))[:2]
            if _text(key):
                values[_text(key)] = value
        defaults = Company()
        kwargs: dict[str, Any] = {}
        for f in dataclasses.fields(Company):
            if f.name not in values or values[f.name] is None:
                continue
            raw, default = values[f.name], getattr(defaults, f.name)
            try:
                if isinstance(default, bool):
                    kwargs[f.name] = _to_bool(raw, default)
                elif isinstance(default, int):
                    kwargs[f.name] = _to_int(raw, default)
                elif isinstance(default, float):
                    kwargs[f.name] = float(_to_decimal(raw))
                else:
                    kwargs[f.name] = _text(raw)
            except (InvalidOperation, ValueError):
                pass  # keep the default
        return Company(**kwargs)

    @staticmethod
    def _read_employee(r: dict[str, Any]) -> Employee | None:
        emp_id = _text(r.get("employeeid"))
        if not emp_id:
            return None
        pay_type = _text(r.get("paytype")) or "Hourly"
        return Employee(
            employee_id=emp_id,
            first_name=_text(r.get("firstname")),
            last_name=_text(r.get("lastname")),
            address1=_text(r.get("address1")),
            address2=_text(r.get("address2")),
            pay_type="Salary" if pay_type.lower().startswith("sal") else "Hourly",
            pay_rate=calc.money(_to_decimal(r.get("payrate"))),
            active=_to_bool(r.get("active"), default=True),
            notes=_text(r.get("notes")),
        )

    @staticmethod
    def _read_entry(r: dict[str, Any]) -> TimeEntry | None:
        d = _to_date(r.get("date"))
        emp_id = _text(r.get("employeeid"))
        if d is None or not emp_id:
            return None
        return TimeEntry(
            work_date=d,
            employee_id=emp_id,
            in1=_to_time(r.get("in1")),
            out1=_to_time(r.get("out1")),
            in2=_to_time(r.get("in2")),
            out2=_to_time(r.get("out2")),
            lunch_deduct_min=max(_to_int(r.get("lunchdeductmin")), 0),
            notes=_text(r.get("notes")),
        )

    @staticmethod
    def _read_payroll(r: dict[str, Any]) -> PayrollRecord | None:
        num = _to_int(r.get("checknumber"), 0)
        emp_id = _text(r.get("employeeid"))
        check_date = _to_date(r.get("checkdate"))
        if not num or not emp_id or check_date is None:
            return None
        kwargs: dict[str, Any] = {}
        for header, name in zip(PAYROLL_HEADERS, PAYROLL_FIELDS):
            raw = r.get(header.lower())
            if name in ("check_number", "employee_id"):
                continue
            if name in ("check_date", "period_start", "period_end"):
                kwargs[name] = _to_date(raw) or check_date
            elif name in ("regular_hours", "overtime_hours") or name in PAYROLL_MONEY:
                kwargs[name] = calc.money(_to_decimal(raw))
            elif name == "voided":
                kwargs[name] = _to_bool(raw)
            else:
                kwargs[name] = _text(raw)
        return PayrollRecord(check_number=num, employee_id=emp_id, **kwargs)

    # ------------------------------------------------------------ save

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise WorkbookError("No file name chosen for this workbook.")
        target = target.resolve()
        wb = self._build_workbook()
        tmp_name: str | None = None
        try:
            fd, tmp_name = tempfile.mkstemp(prefix=f".~{target.stem}-", suffix=".xlsx",
                                            dir=target.parent)
            os.close(fd)
            wb.save(tmp_name)
            os.replace(tmp_name, target)
            tmp_name = None
        except PermissionError as exc:
            raise WorkbookError(LOCKED_MSG) from exc
        except OSError as exc:
            raise WorkbookError(f"Could not save {target.name}: {exc}") from exc
        finally:
            if tmp_name and os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass
        self.path = target
        self.dirty = False
        return target

    # ------------------------------------------------------------ branding

    def set_logo(self, image: bytes | str | Path) -> None:
        """Store a company logo from image bytes or a file path (normalized to a small PNG)."""
        data = image if isinstance(image, bytes) else Path(image).read_bytes()
        self.logo_png = branding.normalize_logo(data)
        self.dirty = True

    def clear_logo(self) -> None:
        self.logo_png = b""
        self.dirty = True

    @staticmethod
    def _read_logo(ws: Worksheet) -> bytes:
        chunks = [str(r[1].value) for r in ws.iter_rows(min_row=2, max_col=2)
                  if str(r[0].value or "").startswith("logo_png") and r[1].value]
        try:
            return base64.b64decode("".join(chunks), validate=True) if chunks else b""
        except (ValueError, binascii.Error):
            return b""  # a damaged logo shouldn't stop the workbook from opening

    def _write_branding(self, ws: Worksheet) -> None:
        """Logo as base64 text in chunks (a cell holds at most 32,767 characters). The sheet is hidden."""
        _write_header(ws, ["Key", "Value"], [14, 80])
        text = base64.b64encode(self.logo_png).decode("ascii")
        for i in range(0, len(text), LOGO_CHUNK):
            ws.append([f"logo_png_{i // LOGO_CHUNK:03d}", text[i:i + LOGO_CHUNK]])
        ws.sheet_state = "hidden"

    def _build_workbook(self) -> openpyxl.Workbook:
        wb = openpyxl.Workbook()
        ws_company = wb.active
        ws_company.title = "Company"
        self._write_company(ws_company)
        self._write_employees(wb.create_sheet("Employees"))
        self._write_timelog(wb.create_sheet("TimeLog"))
        self._write_payroll(wb.create_sheet("Payroll"))
        self._write_summary(wb.create_sheet("Summary"))
        if self.logo_png:
            self._write_branding(wb.create_sheet("Branding"))
        return wb

    def _write_company(self, ws: Worksheet) -> None:
        _write_header(ws, ["Setting", "Value"], [28, 44])
        for f in dataclasses.fields(Company):
            ws.append([f.name, getattr(self.company, f.name)])
        # Keep bank numbers as text so leading zeros survive.
        for row in ws.iter_rows(min_row=2):
            if row[0].value in ("routing_number", "account_number"):
                row[1].number_format = "@"

    def _write_employees(self, ws: Worksheet) -> None:
        _write_header(ws, EMPLOYEE_HEADERS, [12, 14, 16, 28, 28, 10, 12, 8, 30])
        for emp in sorted(self.employees.values(), key=lambda e: e.employee_id):
            ws.append([emp.employee_id, emp.first_name, emp.last_name, emp.address1, emp.address2,
                       emp.pay_type, float(calc.money(emp.pay_rate)),
                       "Yes" if emp.active else "No", emp.notes])
            _fmt_row(ws, ws.max_row, {7: FMT_MONEY})

    def _write_timelog(self, ws: Worksheet) -> None:
        _write_header(ws, TIMELOG_HEADERS, [16, 12, 22, 11, 11, 11, 11, 15, 8, 30])
        for entry in sorted(self.entries.values(), key=lambda e: (e.work_date, e.employee_id)):
            r = ws.max_row + 1
            emp = self.employees.get(entry.employee_id)
            ws.append([entry.work_date, entry.employee_id, emp.display_name if emp else "",
                       entry.in1, entry.out1, entry.in2, entry.out2,
                       int(entry.lunch_deduct_min or 0), HOURS_FORMULA.format(r=r), entry.notes])
            _fmt_row(ws, r, {1: FMT_DAY_DATE, 4: FMT_TIME, 5: FMT_TIME, 6: FMT_TIME, 7: FMT_TIME,
                             9: FMT_HOURS})

    def _write_payroll(self, ws: Worksheet) -> None:
        _write_header(ws, PAYROLL_HEADERS, [12, 12, 12, 12, 12, 22] + [13] * 11 + [24, 8])
        for rec in sorted(self.payroll, key=lambda p: p.check_number):
            row: list[Any] = []
            for name in PAYROLL_FIELDS:
                v = getattr(rec, name)
                if isinstance(v, Decimal):
                    v = float(v)
                elif isinstance(v, bool):
                    v = "Yes" if v else "No"
                row.append(v)
            ws.append(row)
            formats = {2: FMT_DATE, 3: FMT_DATE, 4: FMT_DATE, 7: FMT_HOURS, 8: FMT_HOURS}
            formats.update({PAYROLL_FIELDS.index(f) + 1: FMT_MONEY for f in PAYROLL_MONEY})
            _fmt_row(ws, ws.max_row, formats)

    def _write_summary(self, ws: Worksheet) -> None:
        _write_header(ws, ["EmployeeID", "Employee", "Hours YTD", "Gross YTD", "Net YTD"],
                      [12, 24] + [12] * 13)
        # Everyone active plus former team members who worked or were paid this year, so
        # year-end (1099) totals stay complete after someone leaves.
        worked = {e.employee_id for e in self.entries.values()} | {p.employee_id for p in self.payroll}
        people = sorted((e for e in self.employees.values() if e.active or e.employee_id in worked),
                        key=lambda e: (not e.active, e.last_name.lower(), e.first_name.lower()))
        for emp in people:
            r = ws.max_row + 1
            ws.append([
                emp.employee_id, emp.display_name + ("" if emp.active else " (former)"),
                f"=SUMIFS(TimeLog!I:I,TimeLog!B:B,A{r})",
                f'=SUMIFS(Payroll!K:K,Payroll!E:E,A{r},Payroll!S:S,"<>Yes")',
                f'=SUMIFS(Payroll!Q:Q,Payroll!E:E,A{r},Payroll!S:S,"<>Yes")',
            ])
            _fmt_row(ws, r, {3: FMT_HOURS, 4: FMT_MONEY, 5: FMT_MONEY})

        # Monthly hours block, two rows below the YTD block.
        y = self.company.year
        head = ws.max_row + 2
        bold = Font(bold=True)
        for col, label in enumerate(["EmployeeID", "Employee", *MONTHS, "Total"], start=1):
            ws.cell(row=head, column=col, value=label).font = bold
        for i, emp in enumerate(people, start=1):
            r = head + i
            ws.cell(row=r, column=1, value=emp.employee_id)
            ws.cell(row=r, column=2, value=emp.display_name + ("" if emp.active else " (former)"))
            for m in range(1, 13):
                ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
                cell = ws.cell(row=r, column=2 + m, value=(
                    f'=SUMIFS(TimeLog!I:I,TimeLog!B:B,$A{r},'
                    f'TimeLog!A:A,">="&DATE({y},{m},1),TimeLog!A:A,"<"&DATE({ny},{nm},1))'))
                cell.number_format = FMT_HOURS
            total = ws.cell(row=r, column=15, value=f"=SUM(C{r}:N{r})")
            total.number_format = FMT_HOURS

    # ------------------------------------------------------------ employees

    def next_employee_id(self) -> str:
        nums = [int(m.group(1)) for k in self.employees if (m := re.fullmatch(r"E(\d+)", k))]
        return f"E{max(nums, default=0) + 1:03d}"

    def upsert_employee(self, emp: Employee) -> None:
        """Add or update an employee. An existing employee's stored pay_rate is always kept."""
        new = dataclasses.replace(emp)
        if emp.employee_id in self.employees:
            new.pay_rate = self.employees[emp.employee_id].pay_rate
        else:
            new.pay_rate = calc.money(emp.pay_rate)
        self.employees[emp.employee_id] = new
        self.dirty = True

    def set_pay_rate(self, employee_id: str, rate: Decimal) -> None:
        rate = calc.money(rate)
        if rate < 0:
            raise ValueError("Pay rate cannot be negative")
        self.employees[employee_id].pay_rate = rate  # KeyError for an unknown id
        self.dirty = True

    def has_pay_rate(self, employee_id: str) -> bool:
        emp = self.employees.get(employee_id)
        return emp is not None and emp.pay_rate > 0

    def active_employees(self) -> list[Employee]:
        return sorted((e for e in self.employees.values() if e.active),
                      key=lambda e: (e.last_name.lower(), e.first_name.lower(), e.employee_id))

    # ------------------------------------------------------------ time

    def get_entry(self, employee_id: str, d: date) -> TimeEntry:
        """A copy of the stored entry, or a blank one."""
        entry = self.entries.get((employee_id, d))
        return dataclasses.replace(entry) if entry else TimeEntry(work_date=d, employee_id=employee_id)

    def set_entry(self, entry: TimeEntry) -> None:
        if entry.is_empty():
            if self.entries.pop(entry.key, None) is not None:
                self.dirty = True
            return
        self.entries[entry.key] = dataclasses.replace(entry)
        self.dirty = True

    def entries_for(self, employee_id: str | None, start: date, end: date) -> list[TimeEntry]:
        """Entries in [start, end] for one employee (or all when None), sorted by date then id."""
        found = [e for e in self.entries.values()
                 if start <= e.work_date <= end and (employee_id is None or e.employee_id == employee_id)]
        return sorted(found, key=lambda e: (e.work_date, e.employee_id))

    # ------------------------------------------------------------ payroll

    def record_payroll(self, records: list[PayrollRecord]) -> None:
        if not records:
            return
        self.payroll.extend(records)
        highest = max(r.check_number for r in records)
        self.company.next_check_number = max(self.company.next_check_number, highest + 1)
        self.dirty = True

    def void_check(self, check_number: int) -> None:
        for rec in self.payroll:
            if rec.check_number == check_number:
                rec.voided = True
                self.dirty = True
                return
        raise KeyError(f"No check number {check_number}")

    def records_for_period(self, start: date, end: date) -> list[PayrollRecord]:
        """Checks recorded for exactly this pay period (voided included), by check number."""
        return sorted((r for r in self.payroll if r.period_start == start and r.period_end == end),
                      key=lambda r: r.check_number)
