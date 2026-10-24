"""Shared data models. See DESIGN.md for the workbook schema these map to."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import ROUND_HALF_UP, Decimal


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
    # Which period Pay & Checks pays by default: "none" = the current pay period; "one" = hold one pay period
    # back (pay the period that has ended, as businesses that keep a week in hand do).
    pay_hold_back: str = "none"
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
    # IRS standard mileage rate ($ per business mile) for the Mileage log; confirm each year with a tax professional.
    mileage_rate: float = 0.70
    # Time off: the hours a PTO / Holiday / Sick day fills in on the Time Card when that type is chosen (the day can
    # still be changed, e.g. 4 for a half day), and whether hourly people are paid for those hours at their regular
    # rate. Salaried pay never changes. A "Closed" day (business closed) is always 0 hours and unpaid for hourly.
    pto_hours: float = 8.0
    holiday_hours: float = 8.0
    sick_hours: float = 8.0
    pto_paid: bool = True
    holiday_paid: bool = True
    sick_paid: bool = True
    # Features the business uses (Company Settings > Features). Turning one off hides its tabs; nothing is deleted.
    use_timecards: bool = True   # Time Card and Year Log (hourly pay comes from these hours)
    use_payroll: bool = True     # Pay & Checks
    use_expenses: bool = False   # Expenses (money spent, with receipts)
    # Tabs the owner has put behind the PIN (Company Settings > Locked by PIN). Company Settings itself is always
    # locked, since these switches live there. There is one PIN for the whole app, set up when it was installed.
    lock_timecard: bool = False
    lock_employees: bool = True
    lock_yearlog: bool = False
    lock_payroll: bool = False
    lock_expenses: bool = False
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
    pay_type: str = "Hourly"  # Hourly | Salary | Owner (no payroll checks; expenses and mileage on their account)
    pay_rate: Decimal = Decimal("0")  # stored in workbook only; never shown in the GUI
    active: bool = True
    notes: str = ""
    # Salary only: what pay_rate means. "Annual" = yearly salary (each check is a share of it);
    # "Per Period" = the amount of every check.
    salary_basis: str = "Annual"

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
    # The day's hours typed directly (replaces the punches when set): salaried people's hours, or a time off day's
    # hours (filled from Company Settings when the type is chosen). PTO / Holiday / Sick hours are time off, not
    # hours worked; a Closed day (business closed) is 0 hours. An hourly person's worked hours are always punches.
    salary_hours: Decimal | None = None
    hours_type: str = "Worked"   # Worked | PTO | Holiday | Sick | Closed

    @property
    def key(self) -> tuple[str, date]:
        return (self.employee_id, self.work_date)

    def is_empty(self) -> bool:
        return not any((self.in1, self.out1, self.in2, self.out2, self.lunch_deduct_min, self.notes)) \
            and self.salary_hours is None and (self.hours_type or "").strip().lower() in ("", "worked")


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
    # Paid time off (PTO / Holiday / Sick) on this check: the hours and, for hourly people, the pay for them (part
    # of gross_pay). Salaried checks record the hours with no extra pay.
    leave_hours: Decimal = Decimal("0")
    leave_pay: Decimal = Decimal("0")


@dataclass
class Expense:
    """Money the business spent (cash basis: recorded when paid). See DESIGN.md "Expenses"."""
    expense_id: str
    expense_date: date
    vendor: str = ""
    category: str = ""
    amount: Decimal = Decimal("0")     # total paid, tax included
    tax: Decimal = Decimal("0")        # the tax part of `amount` (shown separately, not added)
    payment_method: str = "Card"       # Cash | Card | Check | Bank transfer | Other
    check_number: int | None = None    # when paid by check
    memo: str = ""
    receipt_file: str = ""             # relative to the workbook's folder, e.g. Receipts\2026\... .jpg
    status: str = "Approved"           # Approved | Draft (scanned, not reviewed yet)
    vendor_id: str = ""                # the Vendors list (1.4.0)
    created: str = ""                  # when it was entered, ISO date-time
    recurring_id: str = ""             # made from this recurring expense (1.3.0)
    person_id: str = ""                # on whose account (e.g. an owner's gas), "" = the business


@dataclass
class Trip:
    """One business trip in the Mileage log."""
    trip_id: str
    trip_date: date
    person_id: str = ""
    purpose: str = ""
    from_place: str = ""
    to_place: str = ""
    miles: Decimal = Decimal("0")
    rate: Decimal = Decimal("0")       # $ per mile when the trip was logged (the rate can change each year)
    notes: str = ""

    @property
    def amount(self) -> Decimal:
        return (self.miles * self.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Recurring:
    """An expense that repeats (rent, phone, software). Each time it comes due a Draft expense is added for it."""
    recurring_id: str
    vendor: str
    category: str = ""
    amount: Decimal = Decimal("0")
    payment_method: str = "Card"
    memo: str = ""
    every: str = "Monthly"             # Monthly | Quarterly | Yearly
    next_date: date | None = None      # when the next Draft is due
    active: bool = True
    day: int = 0                       # day of the month it's due (31 = last day of short months); 0 = next_date's


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
    # Time off hours the company pays hourly people for (Company Settings); never part of regular/overtime.
    paid_leave: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.regular + self.overtime
