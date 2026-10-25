"""Vendor Payments (1.4): pay vendors by check from the same checkbook as payroll.

A separate feature that borrows what it needs from `timecard.checks` (the check face, the 3-up / binder / handwrite
layouts, offsets, logos and the MICR line) without changing how payroll checks print: a vendor payment is handed to
those helpers as a small stand-in record with no hours or pay period. The voucher layout gets its own stub listing
the bills the check pays. Check numbers come from the one sequence shared with payroll (Company.next_check_number).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from reportlab.lib.units import inch

from . import checks as ck
from . import expenses
from .models import Expense, Vendor, VendorPayment

if TYPE_CHECKING:
    from .storage import TimecardBook

ZERO = Decimal("0")
CREATED = "*"   # marks, in VendorPayment.expense_ids, an expense made by the payment itself (removed if voided)


# ---- the one check-number sequence

def used_check_numbers(book: "TimecardBook") -> set[int]:
    return {r.check_number for r in book.payroll} | {p.check_number for p in book.vendor_payments}


def next_check_number(book: "TimecardBook") -> int:
    """Company.next_check_number, skipped past any number already used by a payroll or vendor check."""
    n, used = max(int(book.company.next_check_number or 1), 1), used_check_numbers(book)
    while n in used:
        n += 1
    return n


def unpaid_bills(book: "TimecardBook", vendor: Vendor) -> list[Expense]:
    """The vendor's approved expenses marked 'paid by check' with no check yet: the bills a check can pay."""
    return sorted((e for e in book.expenses
                   if e.status != "Draft" and e.payment_method == "Check" and not e.check_number
                   and (e.vendor_id == vendor.vendor_id or (not e.vendor_id and e.vendor.strip().lower()
                                                            == vendor.name.strip().lower()))),
                  key=lambda e: (e.expense_date, e.expense_id))


def record_payment(book: "TimecardBook", vendor: Vendor, amount: Decimal, check_date: date, memo: str = "",
                   bills: list[Expense] | None = None, account_id: str = "", category: str = "") -> VendorPayment:
    """Write a check to `vendor`: it takes the next check number and pays `bills` (they get the check number);
    with no bills, an expense for the amount is added (so every check is on the Expenses tab once)."""
    if amount <= 0:
        raise ValueError("The check amount must be more than zero.")
    bills = bills or []
    if bills and sum((b.amount for b in bills), ZERO) != amount:
        raise ValueError("The check amount must equal the bills it pays.")
    n = next_check_number(book)
    account_id = account_id or book.company.checks_account_id
    ids = []
    for b in bills:
        b.check_number, b.payment_method = n, "Check"
        b.vendor, b.vendor_id = vendor.name, vendor.vendor_id
        b.account_id = b.account_id or account_id
        ids.append(b.expense_id)
    if not bills:
        e = Expense(expense_id=expenses.next_expense_id(book.expenses), expense_date=check_date, vendor=vendor.name,
                    vendor_id=vendor.vendor_id, category=category or vendor.default_category or "Other",
                    amount=amount, payment_method="Check", check_number=n, memo=memo or f"Check #{n}",
                    account_id=account_id, created=datetime.now().isoformat(timespec="seconds"))
        book.expenses.append(e)
        ids.append(CREATED + e.expense_id)
    p = VendorPayment(check_number=n, check_date=check_date, vendor_id=vendor.vendor_id, payee=vendor.name,
                      amount=amount, memo=memo or _bills_memo(bills, n), expense_ids=",".join(ids),
                      account_id=account_id)
    book.vendor_payments.append(p)
    book.company.next_check_number = n + 1
    book.dirty = True
    return p


def void_payment(book: "TimecardBook", p: VendorPayment) -> None:
    """Void a vendor check: the bills it paid are unpaid again; an expense the check made for itself is removed.
    The check number stays used (never reissued)."""
    if p.voided:
        return
    p.voided = True
    for ref in filter(None, p.expense_ids.split(",")):
        exp_id = ref.lstrip(CREATED)
        e = next((x for x in book.expenses if x.expense_id == exp_id), None)
        if e is None:
            continue
        if ref.startswith(CREATED):
            book.expenses.remove(e)
        elif e.check_number == p.check_number:
            e.check_number = None
    book.dirty = True


def bills_for(book: "TimecardBook", p: VendorPayment) -> list[Expense]:
    ids = [ref.lstrip(CREATED) for ref in p.expense_ids.split(",") if ref]
    return [e for e in book.expenses if e.expense_id in ids]


def _bills_memo(bills: list[Expense], n: int) -> str:
    if not bills:
        return f"Check #{n}"
    if len(bills) == 1:
        b = bills[0]
        return (b.memo or f"{b.category} {b.expense_date:%m/%d/%Y}")[:60]
    return f"{len(bills)} bills, {bills[0].expense_date:%m/%d}–{bills[-1].expense_date:%m/%d/%Y}"


# ---- printing (through the payroll check helpers)

@dataclass
class _CheckStandIn:
    """What `checks` reads from a payroll record, for a vendor check: no hours, no pay period, no adjustments."""
    check_number: int
    check_date: date
    net_pay: Decimal
    memo: str
    employee_name: str
    employee_id: str = ""
    gross_pay: Decimal = ZERO
    regular_hours: Decimal = ZERO
    overtime_hours: Decimal = ZERO
    leave_hours: Decimal = ZERO
    other_deductions: Decimal = ZERO
    period_start: date | None = None
    period_end: date | None = None
    voided: bool = False
    bills: list = field(default_factory=list)


def _stand_in(book: "TimecardBook", p: VendorPayment) -> _CheckStandIn:
    return _CheckStandIn(check_number=p.check_number, check_date=p.check_date, net_pay=p.amount, gross_pay=p.amount,
                         memo=p.memo or f"Check #{p.check_number}", employee_name=p.payee,
                         employee_id=f"vendor:{p.check_number}", bills=bills_for(book, p))


class _VendorBook:
    """The book as the payroll list-maker sees it, with each stand-in's payee looked up as the vendor (so a name like
    "Acme, Inc." prints as written, not flipped like a "Last, First" person)."""

    def __init__(self, book: "TimecardBook", recs: list):
        self._book = book
        self.employees = {rec.employee_id: vendor for rec, vendor in recs}

    def __getattr__(self, name):
        return getattr(self._book, name)


def _payee(book: "TimecardBook", p: VendorPayment) -> Vendor:
    return book.vendors.get(p.vendor_id) or Vendor(p.vendor_id, p.payee)


def _draw_bills_stub(pg: "ck._Page", top: float, company, rec: _CheckStandIn, copy_label: str, logo: bytes) -> None:
    """Voucher stub for a vendor check: payee, check number and date, memo, and the bills it pays."""
    T = lambda d: top + d  # noqa: E731
    ink, _tint = ck._brand(company)
    lw = ck._draw_logo(pg.c, logo, 0.5 * inch, pg.y(T(0.08)), 1.4 * inch, 0.45 * inch)
    nx = 0.5 + (lw / inch + 0.15 if lw else 0.0)
    pg.fit_text(nx, T(0.40), company.company_name or "", max(1.5, 6.6 - nx), "Helvetica-Bold", 11)
    pg.text(8.0, T(0.40), copy_label.upper(), "Helvetica-Bold", 7.5, align="right", color=ck.GREY)
    pg.line(0.5, T(0.58), 8.0, T(0.58), 1.0, ink)
    t = T(0.84)
    for lab, val in (("Payee", rec.employee_name), ("Check No.", str(rec.check_number)),
                     ("Check Date", ck._date(rec.check_date))):
        pg.text(0.5, t, lab, "Helvetica", 7.5, color=ck.GREY)
        pg.fit_text(1.4, t, val, 2.9, "Helvetica-Bold" if lab == "Payee" else "Helvetica", 9)
        t += 0.2
    t += 0.08
    pg.text(0.5, t, "Memo", "Helvetica", 7.5, color=ck.GREY)
    for i, ln in enumerate(ck._wrap(rec.memo, "Helvetica", 8.5, 2.9, 2)):
        pg.text(1.4, t + i * 0.15, ln, "Helvetica", 8.5)
    tx, tw = 4.4, 3.6
    vx = tx + tw - 0.08
    ht = T(0.7)
    pg.rect(tx, ht, tw, 0.2, 0, stroke=ck.SHADE, fill=ck.SHADE)
    pg.text(tx + 0.08, ht + 0.14, "BILLS PAID", "Helvetica-Bold", 7.5)
    t = ht + 0.38
    shown = rec.bills[:7]
    for b in shown:
        label = f"{b.expense_date:%m/%d/%Y}  {b.memo or b.category}"
        pg.fit_text(tx + 0.08, t, label, tw - 1.2, "Helvetica", 8)
        pg.text(vx, t, ck._money(b.amount), "Helvetica", 8, align="right")
        t += 0.17
    if len(rec.bills) > len(shown):
        pg.text(tx + 0.08, t, f"+ {len(rec.bills) - len(shown)} more", "Helvetica-Oblique", 8, color=ck.GREY)
        t += 0.17
    if not rec.bills:
        pg.fit_text(tx + 0.08, t, rec.memo, tw - 0.2, "Helvetica", 8)
        t += 0.17
    pg.line(tx, t - 0.1, tx + tw, t - 0.1, 0.8)
    t += 0.07
    pg.text(tx + 0.08, t, "CHECK AMOUNT", "Helvetica-Bold", 9.5)
    pg.text(vx, t, ck._money(rec.net_pay), "Helvetica-Bold", 9.5, align="right")


def render_vendor_checks(payments: list[VendorPayment], book: "TimecardBook", out_path: str | Path,
                         start_position: int = 1) -> Path:
    """Print vendor checks with the company's check layout (Company Settings > Checks), like payroll checks.
    Voided payments are skipped; raises ValueError if nothing is left."""
    live = [p for p in payments if not p.voided]
    if not live:
        raise ValueError("There are no checks to print (voided checks are skipped).")
    company = book.company
    out = ck._prep_out(out_path)
    lay = ck._layout(company)
    recs = [(_stand_in(book, p), _payee(book, p)) for p in sorted(live, key=lambda p: p.check_number)]
    if lay == "handwrite":
        return ck._render_handwrite([r for r, _v in recs], _VendorBook(book, recs), out)
    face_logo = ck._face_logo(book)
    if lay == "voucher":
        c = ck._new_canvas(out, title="Vendor Checks")
        stub_logo = ck._logo(book, "checks")
        for rec, vendor in recs:
            ck._draw_check_in_slot(c, company, rec, vendor, 0, logo=face_logo)
            c.saveState()
            ck._apply_offset(c, company)
            pg = ck._Page(c)
            ck._draw_perforations(pg)
            _draw_bills_stub(pg, 3.5, company, rec, "Payee Copy", stub_logo)
            _draw_bills_stub(pg, 7.0, company, rec, "Company Copy", stub_logo)
            c.restoreState()
            c.showPage()
        c.save()
        return out
    sp = ck._check_start_position(start_position)
    if lay == "manual_3up":
        w, h, _sw = ck._manual_dims(company)
        c = ck._new_canvas(out, pagesize=(w * inch, h * inch), title="Vendor Checks")
        ck._run_slots(c, recs, sp, lambda rv, slot: ck._draw_manual_slot(c, company, rv[0], rv[1], slot,
                                                                         logo=face_logo))
    else:
        c = ck._new_canvas(out, title="Vendor Checks")
        ck._run_slots(c, recs, sp, lambda rv, slot: ck._draw_check_in_slot(c, company, rv[0], rv[1], slot,
                                                                           logo=face_logo))
    c.showPage()
    c.save()
    return out


def render_register(book: "TimecardBook", out_path: str | Path, start: date, end: date) -> Path:
    """The one check register: every payroll and vendor check between `start` and `end`, by number."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import Table, TableStyle
    from . import vendors
    rows = [c for c in vendors.issued_checks(book) if start <= c.check_date <= end]
    data = [["Check #", "Date", "Type", "Payee", "Amount", "Voided"]]
    total = ZERO
    for c in rows:
        data.append([str(c.check_number), ck._date(c.check_date), c.kind, c.payee, ck._money(c.amount),
                     "VOID" if c.voided else ""])
        total += ZERO if c.voided else c.amount
    data.append(["", "", "", f"Total ({sum(1 for c in rows if not c.voided)} checks)", ck._money(total), ""])
    tbl = Table(data, colWidths=[x * inch for x in (0.8, 0.9, 0.8, 2.9, 1.1, 0.7)], repeatRows=1)
    tbl.setStyle(TableStyle(ck._table_style(book.company) + [("ALIGN", (4, 0), (4, -1), "RIGHT"),
                                                             ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9)]))
    out = ck._prep_out(out_path)
    title = f"Check Register  {ck._date(start)} - {ck._date(end)}"
    doc = ck._doc(out, letter, title)
    hdr = ck._header_fn(book.company, title, f"Printed {ck._date(date.today())}", ck._logo(book, "reports"))
    printed = f"Printed {ck._date(date.today())}"
    doc.build([tbl], onFirstPage=hdr, onLaterPages=hdr, canvasmaker=ck._canvasmaker(printed))
    return out
