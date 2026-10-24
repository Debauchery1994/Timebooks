"""Business rules: time parsing, hours, overtime, pay periods, pay, withholding, words.

All money and hours are Decimal. Money is quantized to cents with ROUND_HALF_UP.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .models import Company, Employee, HoursSummary, PayrollRecord, TimeEntry, YtdTotals

CENT = Decimal("0.01")
ZERO = Decimal("0")

PAY_PERIODS = ("Weekly", "Biweekly", "Semimonthly", "Monthly")
WEEK_STARTS = ("Monday", "Sunday")


def money(x) -> Decimal:
    """Quantize to cents, ROUND_HALF_UP. Floats go through str() to avoid binary noise."""
    if not isinstance(x, Decimal):
        x = Decimal(str(x)) if isinstance(x, float) else Decimal(x)
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _dec(x: float | int | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _hours2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- time text

# "8:30" / "8.30" (separated) or a bare 1-4 digit run ("8", "830", "1715").
_TIME_RE = re.compile(
    r"^(?:(?P<h>\d{1,2})[:.](?P<m>\d{2})|(?P<digits>\d{1,4}))\s*(?P<ap>a|p|am|pm|a\.m\.|p\.m\.)?$"
)


def parse_time(text: str, after: time | None = None) -> time | None:
    """Parse tolerant clock text: "8", "8:30", "830a", "5:15 pm", "17:15", "12a", "12p".

    Empty/blank -> None. Without am/pm the hour is read as 24-hour (0-23), except that when
    `after` (the preceding punch) is given, a 1-11 o'clock time that would land at or before it
    is read as PM if that lands after it, so In "8" / Out "5" means 8 AM to 5 PM.
    Raises ValueError on anything unparseable.
    """
    s = (text or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    m = _TIME_RE.match(s)
    if not m:
        raise ValueError(f"Not a time: {text!r}")
    if m.group("digits"):
        digits = m.group("digits")
        hour, minute = (int(digits), 0) if len(digits) <= 2 else (int(digits[:-2]), int(digits[-2:]))
    else:
        hour, minute = int(m.group("h")), int(m.group("m"))
    ap = m.group("ap")
    if minute > 59:
        raise ValueError(f"Not a time: {text!r}")
    if ap:
        if not 1 <= hour <= 12:
            raise ValueError(f"Not a time: {text!r}")
        if ap.startswith("a"):
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif hour > 23:
        raise ValueError(f"Not a time: {text!r}")
    elif after is not None and 1 <= hour <= 11 and time(hour, minute) <= after < time(hour + 12, minute):
        hour += 12
    return time(hour, minute)


def format_time(t: time | None) -> str:
    """"8:30 AM"; "" for None."""
    if t is None:
        return ""
    h12 = t.hour % 12 or 12
    return f"{h12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


# ---------------------------------------------------------------- hours

def _pair_seconds(t_in: time | None, t_out: time | None) -> int:
    if t_in is None or t_out is None:
        return 0
    secs_in = t_in.hour * 3600 + t_in.minute * 60 + t_in.second
    secs_out = t_out.hour * 3600 + t_out.minute * 60 + t_out.second
    return (secs_out - secs_in) % 86400  # Out earlier than In crosses midnight


def pair_hours(t_in: time | None, t_out: time | None) -> Decimal:
    """Hours between a punch pair, 2 dp; 0 if either is missing."""
    return _hours2(Decimal(_pair_seconds(t_in, t_out)) / 3600)


def day_hours(e: TimeEntry) -> Decimal:
    """Sum of complete pairs minus the lunch deduct, never negative, 2 dp."""
    secs = _pair_seconds(e.in1, e.out1) + _pair_seconds(e.in2, e.out2)
    secs -= int(e.lunch_deduct_min or 0) * 60
    return _hours2(Decimal(max(secs, 0)) / 3600)


# ---------------------------------------------------------------- periods

def week_start_for(d: date, week_start: str) -> date:
    """The first day ("Monday" or "Sunday") of the workweek containing d."""
    first = 6 if week_start.strip().lower().startswith("sun") else 0
    return d - timedelta(days=(d.weekday() - first) % 7)


def period_for(d: date, company: Company) -> tuple[date, date]:
    """Pay period (start, end) inclusive that contains d."""
    kind = company.pay_period.strip().lower()
    if kind == "weekly":
        start = week_start_for(d, company.week_start)
        return start, start + timedelta(days=6)
    if kind == "biweekly":
        anchor = week_start_for(date(company.year, 1, 1), company.week_start)
        start = anchor + timedelta(days=14 * ((d - anchor).days // 14))
        return start, start + timedelta(days=13)
    last_day = calendar.monthrange(d.year, d.month)[1]
    if kind == "semimonthly":
        if d.day <= 15:
            return date(d.year, d.month, 1), date(d.year, d.month, 15)
        return date(d.year, d.month, 16), date(d.year, d.month, last_day)
    if kind == "monthly":
        return date(d.year, d.month, 1), date(d.year, d.month, last_day)
    raise ValueError(f"Unknown pay period: {company.pay_period!r}")


def periods_in_year(company: Company) -> list[tuple[date, date]]:
    """Every pay period that touches company.year, in order."""
    periods: list[tuple[date, date]] = []
    year_end = date(company.year, 12, 31)
    start, end = period_for(date(company.year, 1, 1), company)
    while start <= year_end:
        periods.append((start, end))
        start, end = period_for(end + timedelta(days=1), company)
    return periods


def summarize_hours(entries: list[TimeEntry], company: Company,
                    start: date, end: date) -> HoursSummary:
    """Regular/OT for entries within [start, end], OT computed per workweek on in-period days.

    With company.overtime_enabled off (the contractor default) every hour is regular.
    """
    threshold = _dec(company.overtime_threshold_weekly) if company.overtime_enabled else Decimal("Infinity")
    weeks: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for e in entries:
        if start <= e.work_date <= end:
            weeks[week_start_for(e.work_date, company.week_start)] += day_hours(e)
    summary = HoursSummary()
    for ws in sorted(weeks):
        total = weeks[ws]
        ot = max(total - threshold, ZERO)
        summary.regular += total - ot
        summary.overtime += ot
        summary.by_week.append((ws, total))
    return summary


# ---------------------------------------------------------------- pay

PERIODS_PER_YEAR = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


def salary_per_period(annual: Decimal, company: Company) -> Decimal:
    """Equal share of an annual salary for one pay period (Salary pay_rate is the annual amount)."""
    return money(_dec(annual) / PERIODS_PER_YEAR.get(company.pay_period.strip().lower(), 52))


def salary_is_per_period(employee: Employee) -> bool:
    """True when a salaried employee's pay_rate is the amount per pay period rather than per year."""
    return (employee.salary_basis or "").strip().lower().replace(" ", "") == "perperiod"


def salary_check_amount(employee: Employee, company: Company) -> Decimal:
    """One pay period's salary: the rate itself when entered per pay period, else annual / periods per year."""
    if salary_is_per_period(employee):
        return money(employee.pay_rate)
    return salary_per_period(employee.pay_rate, company)


def compute_payroll(employee: Employee, hours: HoursSummary, company: Company, *,
                    check_number: int, check_date: date, period_start: date, period_end: date,
                    other_deductions: Decimal = Decimal("0"), memo: str = "") -> PayrollRecord:
    """Gross, flat-percent withholding estimate and net for one employee/period."""
    rate = _dec(employee.pay_rate)
    if employee.pay_type.strip().lower() == "salary":
        regular_pay, overtime_pay = salary_check_amount(employee, company), money(0)
    else:
        regular_pay = money(hours.regular * rate)
        overtime_pay = money(hours.overtime * rate * _dec(company.overtime_multiplier))
    gross = regular_pay + overtime_pay

    def pct(p: float) -> Decimal:
        return money(gross * _dec(p) / 100)

    federal = pct(company.federal_withholding_pct)
    state = pct(company.state_withholding_pct)
    ss = pct(company.social_security_pct)
    medicare = pct(company.medicare_pct)
    other = money(other_deductions)
    net = money(gross - federal - state - ss - medicare - other)
    return PayrollRecord(
        check_number=check_number,
        check_date=check_date,
        period_start=period_start,
        period_end=period_end,
        employee_id=employee.employee_id,
        employee_name=employee.full_name,
        regular_hours=_hours2(hours.regular),
        overtime_hours=_hours2(hours.overtime),
        regular_pay=regular_pay,
        overtime_pay=overtime_pay,
        gross_pay=gross,
        federal_tax=federal,
        state_tax=state,
        social_security=ss,
        medicare=medicare,
        other_deductions=other,
        net_pay=net,
        memo=memo,
    )


def ytd_totals(records: list[PayrollRecord], employee_id: str, through: date,
               entries: list[TimeEntry] | None = None) -> YtdTotals:
    """Totals for checks dated Jan 1..through of through.year, excluding voided.

    Hours come from day_hours over entries when given, else from the checks' reg+OT hours.
    """
    jan1 = date(through.year, 1, 1)
    t = YtdTotals()
    for r in records:
        if r.voided or r.employee_id != employee_id or not jan1 <= r.check_date <= through:
            continue
        t.gross_pay += r.gross_pay
        t.federal_tax += r.federal_tax
        t.state_tax += r.state_tax
        t.social_security += r.social_security
        t.medicare += r.medicare
        t.other_deductions += r.other_deductions
        t.net_pay += r.net_pay
        if entries is None:
            t.hours += r.regular_hours + r.overtime_hours
    if entries is not None:
        t.hours = sum((day_hours(e) for e in entries
                       if e.employee_id == employee_id and jan1 <= e.work_date <= through), ZERO)
    return t


# ---------------------------------------------------------------- words

_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
         "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()
_SCALES = ((10**12, "Trillion"), (10**9, "Billion"), (10**6, "Million"), (1000, "Thousand"))


def _under_thousand(n: int) -> str:
    parts: list[str] = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def _int_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts: list[str] = []
    for value, name in _SCALES:
        if n >= value:
            parts.append(f"{_int_words(n // value)} {name}")
            n %= value
    if n:
        parts.append(_under_thousand(n))
    return " ".join(parts)


def amount_to_words(amount: Decimal) -> str:
    """Check-style words: "One Thousand Two Hundred Thirty-Four and 56/100"."""
    amt = money(amount)
    if amt < 0:
        raise ValueError("Check amount cannot be negative")
    dollars = int(amt)
    cents = int((amt - dollars) * 100)
    return f"{_int_words(dollars)} and {cents:02d}/100"
