"""Shared data models. See DESIGN.md for the workbook schema these map to."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal


@dataclass
class Company:
    company_name: str = "My Company"
    address1: str = ""
    address2: str = ""
    phone: str = ""
    bank_name: str = ""
    bank_address: str = ""
    routing_number: str = ""
    account_number: str = ""
    year: int = date.today().year
    next_check_number: int = 1001
    pay_period: str = "Weekly"  # Weekly | Biweekly | Semimonthly | Monthly
    week_start: str = "Monday"  # Monday | Sunday
    default_lunch_minutes: int = 30
    # Team members are paid as independent contractors: no overtime premium and no withholding
    # by default. The settings stay so a business can turn them on.
    overtime_enabled: bool = False
    overtime_threshold_weekly: float = 40.0
    overtime_multiplier: float = 1.5
    federal_withholding_pct: float = 0.0
    state_withholding_pct: float = 0.0
    social_security_pct: float = 0.0
    medicare_pct: float = 0.0
    # Annual statements flag contractors paid at least this much (1099-NEC reporting threshold;
    # $2,000 for payments made in 2026 — confirm each year with a tax professional).
    form_1099_threshold: float = 2000.0
    # Branding. The logo image itself is stored by storage.py on a hidden "Branding" sheet (TimecardBook.logo_png).
    brand_color: str = "#1F3A5F"
    logo_on_checks: bool = True   # voucher stubs; the check face too on blank stock only
    logo_on_reports: bool = True  # time cards, payroll register, annual statements
    check_stock: str = "preprinted"  # preprinted | blank (print company, bank and MICR too)
    # voucher (QuickBooks-style, the business's stock) | laser_3up | manual_3up (binder checks with a
    # left stub, single copy only) | handwrite
    check_layout: str = "voucher"
    # Sheet measurements for manual_3up, entered by the user (inches).
    manual_sheet_width: float = 11.0
    manual_sheet_height: float = 10.5
    manual_stub_width: float = 2.5
    micr_font_path: str = ""
    print_offset_x: float = 0.0  # inches
    print_offset_y: float = 0.0  # inches
    signature_line_text: str = "Authorized Signature"


@dataclass
class Employee:
    employee_id: str
    first_name: str
    last_name: str
    address1: str = ""
    address2: str = ""
    pay_type: str = "Hourly"  # Hourly | Salary
    pay_rate: Decimal = Decimal("0")  # stored in workbook only; never shown in the GUI
    active: bool = True
    notes: str = ""

    @property
    def display_name(self) -> str:
        """'Last, First' for lists and the TimeLog Employee column."""
        return f"{self.last_name}, {self.first_name}".strip(", ")

    @property
    def full_name(self) -> str:
        """'First Last' for check payee lines."""
        return f"{self.first_name} {self.last_name}".strip()


@dataclass
class TimeEntry:
    work_date: date
    employee_id: str
    in1: time | None = None
    out1: time | None = None
    in2: time | None = None
    out2: time | None = None
    lunch_deduct_min: int = 0
    notes: str = ""

    @property
    def key(self) -> tuple[str, date]:
        return (self.employee_id, self.work_date)

    def is_empty(self) -> bool:
        return not any((self.in1, self.out1, self.in2, self.out2, self.lunch_deduct_min, self.notes))


@dataclass
class PayrollRecord:
    check_number: int
    check_date: date
    period_start: date
    period_end: date
    employee_id: str
    employee_name: str
    regular_hours: Decimal = Decimal("0")
    overtime_hours: Decimal = Decimal("0")
    regular_pay: Decimal = Decimal("0")
    overtime_pay: Decimal = Decimal("0")
    gross_pay: Decimal = Decimal("0")
    federal_tax: Decimal = Decimal("0")
    state_tax: Decimal = Decimal("0")
    social_security: Decimal = Decimal("0")
    medicare: Decimal = Decimal("0")
    other_deductions: Decimal = Decimal("0")
    net_pay: Decimal = Decimal("0")
    memo: str = ""
    voided: bool = False


@dataclass
class YtdTotals:
    """Year-to-date totals for one employee, excluding voided checks."""
    gross_pay: Decimal = Decimal("0")
    federal_tax: Decimal = Decimal("0")
    state_tax: Decimal = Decimal("0")
    social_security: Decimal = Decimal("0")
    medicare: Decimal = Decimal("0")
    other_deductions: Decimal = Decimal("0")
    net_pay: Decimal = Decimal("0")
    hours: Decimal = Decimal("0")


@dataclass
class HoursSummary:
    regular: Decimal = Decimal("0")
    overtime: Decimal = Decimal("0")
    by_week: list[tuple[date, Decimal]] = field(default_factory=list)  # (week start, total hours)

    @property
    def total(self) -> Decimal:
        return self.regular + self.overtime
