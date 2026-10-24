"""PDF output with reportlab: checks, payroll register, printable time card, printer alignment test.
See DESIGN.md "Checks", "CHANGE 2026-10-23" and "CHANGE — check layouts".

Check layouts (company.check_layout):
  laser_3up   US letter, three 3.5"-tall checks at 0", 3.5", 7.0" from the top
  manual_3up  custom manual_sheet_width x manual_sheet_height sheet, 3 equal rows, left stub
              manual_stub_width wide, check face scaled into the rest of the row
  voucher     US letter, check in the top 3.5" + payee and company stubs (no tax / YTD) (default; QuickBooks voucher checks)
  handwrite   "Checks to Write" list for filling in a manual checkbook by hand
The check face is drawn in standard coordinates (8.5" x 3.5", measured from the top of the
check) and translated/scaled into place. company.print_offset_x (positive = right) and
company.print_offset_y (positive = down) shift everything on printed checks.

The pay rate is deliberately never printed by anything in this module.
"""
from __future__ import annotations

import io
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import branding, calc
from .models import Company, Employee, PayrollRecord, TimeEntry

if TYPE_CHECKING:  # pragma: no cover
    from .storage import TimecardBook

__all__ = ["render_checks", "render_register", "render_timecard", "render_alignment_test"]

# None = reportlab default (compressed). Tests set this to 0 so the PDF text is greppable.
PAGE_COMPRESSION: int | None = None

PAGE_W, PAGE_H = letter
CHECK_H = 3.5 * inch
SLOT_TOPS = (0.0, 3.5, 7.0)  # inches from page top, one check per slot

INK = colors.black
GREY = colors.Color(0.45, 0.45, 0.45)
LIGHT = colors.Color(0.75, 0.75, 0.75)
SHADE = colors.Color(0.92, 0.92, 0.92)

MICR_WARNING = "MICR font not configured — not bank-processable"
_MICR_FONT_NAME = "TimecardMICR"


# --------------------------------------------------------------------------- helpers
def _dec(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x if x is not None else 0))


def _money(x) -> str:
    return f"{calc.money(_dec(x)):,.2f}"


def _hours(x) -> str:
    return f"{_dec(x):,.2f}"


def _date(d: date | None) -> str:
    return d.strftime("%m/%d/%Y") if d else ""


def _new_canvas(out_path: Path, pagesize=letter, title: str = "") -> rl_canvas.Canvas:
    kw = {}
    if PAGE_COMPRESSION is not None:
        kw["pageCompression"] = PAGE_COMPRESSION
    c = rl_canvas.Canvas(str(out_path), pagesize=pagesize, **kw)
    c.setTitle(title)
    c.setAuthor("TimeCard Pro")
    return c


def _prep_out(out_path) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class _Page:
    """Canvas wrapper using inches measured from the page top-left."""

    def __init__(self, c: rl_canvas.Canvas, page_h: float = PAGE_H):
        self.c = c
        self.page_h = page_h

    def y(self, top_in: float) -> float:
        return self.page_h - top_in * inch

    def text(self, x_in, top_in, s, font="Helvetica", size=9, align="left", color=INK):
        c = self.c
        c.setFont(font, size)
        c.setFillColor(color)
        x, y = x_in * inch, self.y(top_in)
        if align == "right":
            c.drawRightString(x, y, s)
        elif align == "center":
            c.drawCentredString(x, y, s)
        else:
            c.drawString(x, y, s)

    def fit_text(self, x_in, top_in, s, max_w_in, font="Helvetica", size=9.0, min_size=6.0,
                 align="left", color=INK):
        """Draw text, shrinking the font so it fits within max_w_in inches."""
        while size > min_size and pdfmetrics.stringWidth(s, font, size) > max_w_in * inch:
            size -= 0.25
        self.text(x_in, top_in, s, font, size, align, color)

    def line(self, x1, t1, x2, t2, width=0.6, color=INK, dash=None):
        c = self.c
        c.setStrokeColor(color)
        c.setLineWidth(width)
        c.setDash(*dash) if dash else c.setDash()
        c.line(x1 * inch, self.y(t1), x2 * inch, self.y(t2))
        c.setDash()

    def rect(self, x, top, w, h, width=0.6, stroke=INK, fill=None):
        c = self.c
        c.setStrokeColor(stroke)
        c.setLineWidth(width)
        if fill is not None:
            c.setFillColor(fill)
        c.rect(x * inch, self.y(top + h), w * inch, h * inch, stroke=1, fill=1 if fill is not None else 0)


def _apply_offset(c: rl_canvas.Canvas, company: Company) -> None:
    ox = float(company.print_offset_x or 0)
    oy = float(company.print_offset_y or 0)
    if ox or oy:
        c.translate(ox * inch, -oy * inch)


# --------------------------------------------------------------------------- branding
def _brand(company: Company) -> tuple[colors.Color, colors.Color]:
    """(ink, tint): brand color usable as text/lines on white, and a light tint for header shading."""
    raw = branding.brand_color(getattr(company, "brand_color", "") or "")
    ink = colors.HexColor(branding.ink_color(raw))
    b = colors.HexColor(raw)
    k = 0.88  # mix with white
    tint = colors.Color(b.red * (1 - k) + k, b.green * (1 - k) + k, b.blue * (1 - k) + k)
    return ink, tint


def _logo(book, purpose: str) -> bytes:
    """Logo PNG for 'checks' (stubs) or 'reports', or b"" when absent / switched off."""
    png = getattr(book, "logo_png", b"") or b""
    flag = "logo_on_checks" if purpose == "checks" else "logo_on_reports"
    if not png or not getattr(book.company, flag, True):
        return b""
    return png


def _face_logo(book) -> bytes:
    """Logo for the check face: blank stock only (preprinted faces are already printed)."""
    stock = (getattr(book.company, "check_stock", "") or "preprinted").strip().lower()
    return _logo(book, "checks") if stock == "blank" else b""


def _draw_logo(c: rl_canvas.Canvas, png: bytes, x: float, y_top: float, max_w: float, max_h: float) -> float:
    """Draw the logo with its top-left corner at (x, y_top) in points, keeping its shape.
    Returns the drawn width in points (0 if there is no usable logo)."""
    if not png:
        return 0.0
    try:
        w, h = branding.fit_logo(png, max_w, max_h)
        c.drawImage(ImageReader(io.BytesIO(png)), x, y_top - h, w, h, mask="auto")
    except Exception:
        return 0.0
    return w


# --------------------------------------------------------------------------- MICR
def _load_micr_font(path: str) -> tuple[str, str, str] | None:
    """Register the user's E-13B font. Returns (font_name, transit_char, on_us_char) or None."""
    if not path or not os.path.isfile(path):
        return None
    try:
        if _MICR_FONT_NAME not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(_MICR_FONT_NAME, path))
        face = pdfmetrics.getFont(_MICR_FONT_NAME).face
        cmap = getattr(face, "charToGlyph", {}) or {}
    except Exception:
        return None
    if 0x2446 in cmap and 0x2448 in cmap:
        return _MICR_FONT_NAME, "⑆", "⑈"
    # Most E-13B TrueType fonts map the special symbols to letters: A = transit, C = on-us.
    return _MICR_FONT_NAME, "A", "C"


def _draw_micr_symbol(c: rl_canvas.Canvas, kind: str, x: float, y: float, size: float) -> float:
    """Vector approximation of the E-13B transit / on-us symbols for the placeholder MICR line.
    Returns the advance width."""
    h = size * 0.72
    w = size * 0.6
    c.setFillColor(INK)
    bar = w * 0.22
    if kind == "transit":  # tall bar on the left, two small squares on the right
        c.rect(x, y, bar, h, stroke=0, fill=1)
        sq = h * 0.3
        c.rect(x + w - sq, y + h - sq, sq, sq, stroke=0, fill=1)
        c.rect(x + w - sq, y, sq, sq, stroke=0, fill=1)
    else:  # on-us: two squares on the left, bar on the right
        sq = h * 0.3
        c.rect(x, y + h - sq * 1.2, w * 0.55, sq, stroke=0, fill=1)
        c.rect(x, y, w * 0.55, sq * 1.2, stroke=0, fill=1)
        c.rect(x + w - bar, y, bar, h * 0.62, stroke=0, fill=1)
    return w + size * 0.12


def _draw_micr_line(c: rl_canvas.Canvas, company: Company, check_number: int, warn: bool = True) -> None:
    """MICR line in the clear band at the bottom of the check: ⑈check⑈ ⑆routing⑆ account⑈."""
    chk = f"{int(check_number):06d}" if str(check_number).isdigit() else str(check_number)
    routing = "".join(ch for ch in str(company.routing_number or "") if ch.isdigit())
    account = "".join(ch for ch in str(company.account_number or "") if ch.isdigit() or ch == "-")
    baseline = PAGE_H - CHECK_H + 0.25 * inch  # ~1/4" above the bottom edge of the check
    x0 = 1.6 * inch
    loaded = _load_micr_font(company.micr_font_path)
    if loaded:
        font, transit, on_us = loaded
        s = f"{on_us}{chk}{on_us} {transit}{routing}{transit} {account}{on_us}"
        c.setFillColor(INK)
        c.setFont(font, 12)  # E-13B is designed for 8 characters per inch at 12 pt
        c.drawString(x0, baseline, s)
        return

    size = 12
    font = "Courier"
    c.setFont(font, size)
    x = x0
    adv = pdfmetrics.stringWidth

    def run(s):
        nonlocal x
        c.setFillColor(INK)
        c.setFont(font, size)
        c.drawString(x, baseline, s)
        x += adv(s, font, size)

    x += _draw_micr_symbol(c, "onus", x, baseline, size)
    run(chk)
    x += _draw_micr_symbol(c, "onus", x, baseline, size)
    x += size * 0.6
    x += _draw_micr_symbol(c, "transit", x, baseline, size)
    run(routing)
    x += _draw_micr_symbol(c, "transit", x, baseline, size)
    x += size * 0.6
    run(account)
    _draw_micr_symbol(c, "onus", x, baseline, size)

    if warn:
        c.saveState()
        c.setFillColor(colors.red)
        c.setFont("Helvetica-Bold", 6.5)
        c.translate(0.42 * inch, PAGE_H - CHECK_H + 0.15 * inch)
        c.rotate(90)
        c.drawString(0, 0, MICR_WARNING)
        c.restoreState()


# --------------------------------------------------------------------------- check face
def _default_memo(rec: PayrollRecord) -> str:
    """e.g. 'Contract labor 09/21–10/04/2026 · 80.00 hrs' (never the rate)."""
    s, e = rec.period_start, rec.period_end
    if s and e:
        first = s.strftime("%m/%d") if s.year == e.year else s.strftime("%m/%d/%Y")
        period = f"{first}–{e.strftime('%m/%d/%Y')}"
    else:
        period = _date(e or s)
    hrs = _dec(rec.regular_hours) + _dec(rec.overtime_hours)
    memo = f"Contract labor {period}".rstrip()
    if hrs:
        memo += f" · {_hours(hrs)} hrs"
    return memo


def _words_line(amount: Decimal) -> str:
    return calc.amount_to_words(calc.money(amount))


def _draw_check(pg: _Page, company: Company, rec: PayrollRecord, emp: Employee | None,
                payee_name: str, logo: bytes = b"") -> None:
    """Check face. Everything is black: the brand color is never printed on a check face.
    ``logo`` is only drawn on blank stock (callers pass b"" otherwise)."""
    c = pg.c
    blank = (company.check_stock or "preprinted").strip().lower() == "blank"

    if blank:
        # Optional logo, then the company block, top left
        lw = _draw_logo(c, logo, 0.5 * inch, pg.y(0.22), 1.5 * inch, 0.6 * inch)
        cx = 0.5 + (lw / inch + 0.12 if lw else 0.0)
        cw = max(1.2, 3.55 - cx)
        pg.fit_text(cx, 0.45, company.company_name or "", cw, "Helvetica-Bold", 11)
        t = 0.61
        for ln in (company.address1, company.address2, company.phone):
            if ln:
                pg.fit_text(cx, t, ln, cw, "Helvetica", 8)
                t += 0.14
        # Bank block, centre
        if company.bank_name:
            pg.fit_text(4.6, 0.45, company.bank_name, 2.0, "Helvetica-Bold", 8.5, align="center")
        if company.bank_address:
            pg.fit_text(4.6, 0.59, company.bank_address, 2.0, "Helvetica", 7.5, align="center")
        # Check number, top right
        pg.text(8.0, 0.45, str(rec.check_number), "Helvetica-Bold", 12, align="right")

    # Date
    if blank:
        pg.text(6.2, 1.0, "DATE", "Helvetica", 7)
    pg.text(7.15, 0.98, _date(rec.check_date), "Helvetica", 10.5, align="center")
    if blank:
        pg.line(6.55, 1.04, 7.85, 1.04, 0.5)

    # Pay to the order of + numeric amount box
    if blank:
        pg.text(0.5, 1.38, "PAY TO THE", "Helvetica", 6.5)
        pg.text(0.5, 1.48, "ORDER OF", "Helvetica", 6.5)
    pg.fit_text(1.35, 1.46, payee_name, 4.6, "Helvetica-Bold", 11)
    if blank:
        pg.line(1.3, 1.52, 6.2, 1.52, 0.5)
        pg.text(6.43, 1.46, "$", "Helvetica-Bold", 11)
        pg.rect(6.55, 1.25, 1.35, 0.3, 0.7)
    pg.fit_text(7.82, 1.46, f"**{_money(rec.net_pay)}", 1.2, "Helvetica-Bold", 11, align="right")

    # Amount in words, padded with asterisks to fill the line
    words = _words_line(rec.net_pay)
    font, size = "Helvetica", 9.5
    line_w = (7.2 - 0.5) * inch
    while size > 6.5 and pdfmetrics.stringWidth(words + " ", font, size) > line_w:
        size -= 0.25
    star_w = pdfmetrics.stringWidth("*", font, size)
    used = pdfmetrics.stringWidth(words + " ", font, size)
    stars = "*" * max(0, int((line_w - used) / star_w))
    pg.text(0.5, 1.86, f"{words} {stars}", font, size)
    if blank:
        pg.line(0.5, 1.92, 7.2, 1.92, 0.5)
        pg.text(7.9, 1.86, "DOLLARS", "Helvetica", 7, align="right")

    # Payee address block — sits in the lower window of a #10 double-window envelope
    addr_lines = [payee_name]
    if emp is not None:
        addr_lines += [ln for ln in (emp.address1, emp.address2) if ln]
    t = 2.17
    for i, ln in enumerate(addr_lines[:4]):
        pg.fit_text(0.95, t, ln, 3.4, "Helvetica-Bold" if i == 0 else "Helvetica", 9.5)
        t += 0.16

    # Memo
    memo = rec.memo or _default_memo(rec)
    if blank:
        pg.text(0.5, 2.88, "MEMO", "Helvetica", 6.5)
    pg.fit_text(0.95, 2.87, memo, 3.3, "Helvetica", 8.5)
    if blank:
        pg.line(0.9, 2.92, 4.3, 2.92, 0.5)

    # Signature line (preprinted stock already has one)
    if blank:
        pg.line(5.0, 2.72, 7.9, 2.72, 0.7)
        pg.fit_text(6.45, 2.85, company.signature_line_text or "", 2.9, "Helvetica", 7, align="center")

        _draw_micr_line(c, company, rec.check_number)


# --------------------------------------------------------------------------- 3-up layout
def _payee_name(rec: PayrollRecord, emp: Employee | None) -> str:
    if emp is not None:
        return emp.full_name
    payee = rec.employee_name or ""
    if "," in payee:  # "Last, First" -> "First Last"
        last, first = [p.strip() for p in payee.split(",", 1)]
        payee = f"{first} {last}".strip()
    return payee


def _draw_perforations(pg: _Page) -> None:
    for top in SLOT_TOPS[1:]:
        pg.line(0.25, top, 8.25, top, 0.4, LIGHT, dash=(3, 3))


def _draw_check_in_slot(c: rl_canvas.Canvas, company: Company, rec: PayrollRecord,
                        emp: Employee | None, slot: int, void: bool = False, logo: bytes = b"") -> None:
    """Draw one check in slot 0..2 (0 = top third of the letter sheet)."""
    c.saveState()
    _apply_offset(c, company)
    c.translate(0, -SLOT_TOPS[slot] * inch)
    _draw_check(_Page(c), company, rec, emp, _payee_name(rec, emp), logo)
    if void:
        _void_stamp(c)
    c.restoreState()


def _check_start_position(start_position) -> int:
    try:
        sp = int(start_position)
    except (TypeError, ValueError):
        raise ValueError("start_position must be 1, 2 or 3") from None
    if sp not in (1, 2, 3):
        raise ValueError("start_position must be 1, 2 or 3")
    return sp


# --------------------------------------------------------------------------- layout helpers
LAYOUTS = ("laser_3up", "manual_3up", "voucher", "handwrite")
VOID_RED = colors.Color(0.85, 0.1, 0.1, alpha=0.55)


def _layout(company: Company) -> str:
    lay = (getattr(company, "check_layout", "") or Company.check_layout).strip().lower()
    return lay if lay in LAYOUTS else Company.check_layout


def _live_records(records: list[PayrollRecord]) -> list[PayrollRecord]:
    live = [r for r in records if not r.voided]
    if not live:
        raise ValueError("No checks to print (all selected checks are voided).")
    return live


def _void_stamp(c: rl_canvas.Canvas) -> None:
    """VOID stamp in standard check-face coordinates (letter, check in the top 3.5")."""
    c.saveState()
    c.setFillColor(VOID_RED)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(5.75 * inch, PAGE_H - 2.4 * inch, "VOID — ALIGNMENT TEST")
    c.restoreState()


def _sample_records(company: Company, n: int) -> list[PayrollRecord]:
    today = date.today()
    first = int(company.next_check_number or 1001)
    amounts = [Decimal("1234.56"), Decimal("987.65"), Decimal("2045.10")]
    return [PayrollRecord(
        check_number=first + i, check_date=today,
        period_start=today - timedelta(days=13), period_end=today,
        employee_id="SAMPLE", employee_name="Payee, Sample",
        regular_hours=Decimal("80.00"), gross_pay=amounts[i % 3], net_pay=amounts[i % 3],
        memo="ALIGNMENT TEST") for i in range(n)]


_SAMPLE_EMP = Employee("SAMPLE", "Sample", "Payee", "123 Main Street", "Anytown, ST 12345")


def _signed_adjustment(rec: PayrollRecord) -> str:
    """Adjustment as it affects the check: deductions negative, additions positive."""
    v = -_dec(rec.other_deductions)
    if not v:
        return "0.00"
    return ("+" if v > 0 else "-") + _money(abs(v))


def _wrap(s: str, font: str, size: float, width_in: float, max_lines: int) -> list[str]:
    lines = simpleSplit(s or "", font, size, width_in * inch) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and pdfmetrics.stringWidth(last + "…", font, size) > width_in * inch:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _run_slots(c: rl_canvas.Canvas, items: list, start_position: int, draw) -> None:
    """Place items into 3 slots per sheet starting at start_position on the first sheet."""
    slot = start_position - 1
    for item in items:
        if slot == 3:
            c.showPage()
            slot = 0
        draw(item, slot)
        slot += 1


# --------------------------------------------------------------------------- laser_3up
def _render_laser(live, book, out: Path, sp: int, sample: bool = False) -> Path:
    company = book.company
    c = _new_canvas(out, title="Check Alignment Test" if sample else "Checks")
    face_logo = _face_logo(book)

    def draw(rec, slot):
        emp = _SAMPLE_EMP if sample else book.employees.get(rec.employee_id)
        _draw_check_in_slot(c, company, rec, emp, slot, void=sample, logo=face_logo)

    _run_slots(c, live, sp, draw)
    if sample:
        c.saveState()
        _apply_offset(c, company)
        _draw_perforations(_Page(c))
        _draw_rulers(c)
        c.restoreState()
    c.showPage()
    c.save()
    return out


# --------------------------------------------------------------------------- manual_3up
def _manual_dims(company: Company) -> tuple[float, float, float]:
    w = float(getattr(company, "manual_sheet_width", 0) or 11.0)
    h = float(getattr(company, "manual_sheet_height", 0) or 10.5)
    sw = float(getattr(company, "manual_stub_width", 0) or 0.0)
    if w <= 0 or h <= 0 or sw < 0 or sw >= w - 2.0:
        raise ValueError("Manual check sheet measurements are not valid (check width, height and stub width).")
    return w, h, sw


def _draw_manual_slot(c: rl_canvas.Canvas, company: Company, rec: PayrollRecord,
                      emp: Employee | None, slot: int, void: bool = False, logo: bytes = b"") -> None:
    w, h, sw = _manual_dims(company)
    rh = h / 3.0
    top = slot * rh
    payee = _payee_name(rec, emp)
    c.saveState()
    _apply_offset(c, company)
    pg = _Page(c, h * inch)

    # Stub (left). Keep clear of binder ring holes along the left edge.
    if sw >= 1.2:
        x0 = min(0.45, sw * 0.18)
        tw = sw - x0 - 0.15
        step = min(0.37, (rh - 0.45) / 7.0)
        t = top + 0.38
        pg.fit_text(x0, t, f"No. {rec.check_number}", tw, "Helvetica-Bold", 9.5, 6)
        t += step * 0.9
        hrs = _dec(rec.regular_hours) + _dec(rec.overtime_hours)
        items = [("DATE", _date(rec.check_date)), ("TO", payee),
                 ("AMOUNT", f"${_money(rec.net_pay)}")]
        if hrs:
            items.append(("HOURS", _hours(hrs)))
        for lab, val in items:
            pg.text(x0, t, lab, "Helvetica", 5.5, color=GREY)
            pg.fit_text(x0, t + 0.13, val, tw, "Helvetica-Bold" if lab == "AMOUNT" else "Helvetica", 8, 5.5)
            t += step
        memo = rec.memo or _default_memo(rec)
        pg.text(x0, t, "MEMO", "Helvetica", 5.5, color=GREY)
        for i, ln in enumerate(_wrap(memo, "Helvetica", 7, tw, 2)):
            pg.text(x0, t + 0.12 + i * 0.11, ln, "Helvetica", 7)
        # Tear line between stub and check
        pg.line(sw, top + 0.08, sw, top + rh - 0.08, 0.4, LIGHT, dash=(2, 2))

    # Check face scaled into the area to the right of the stub, anchored at its top-left.
    s = min((w - sw) / 8.5, rh / 3.5)
    c.translate(sw * inch, h * inch - top * inch)
    c.scale(s, s)
    c.translate(0, -PAGE_H)
    _draw_check(_Page(c), company, rec, emp, payee, logo)
    if void:
        _void_stamp(c)
    c.restoreState()


def _render_manual(live, book, out: Path, sp: int, sample: bool = False) -> Path:
    company = book.company
    w, h, _sw = _manual_dims(company)
    c = _new_canvas(out, pagesize=(w * inch, h * inch),
                    title="Check Alignment Test" if sample else "Checks")
    face_logo = _face_logo(book)

    def draw(rec, slot):
        emp = _SAMPLE_EMP if sample else book.employees.get(rec.employee_id)
        _draw_manual_slot(c, company, rec, emp, slot, void=sample, logo=face_logo)

    _run_slots(c, live, sp, draw)
    if sample:
        c.saveState()
        _apply_offset(c, company)
        pg = _Page(c, h * inch)
        for k in (1, 2):
            pg.line(0.25, k * h / 3, w - 0.25, k * h / 3, 0.4, LIGHT, dash=(3, 3))
        _draw_rulers(c, w * inch, h * inch, note=False)
        c.restoreState()
    c.showPage()
    c.save()
    return out


# --------------------------------------------------------------------------- voucher
def _draw_simple_stub(pg: _Page, top: float, company: Company, rec: PayrollRecord, payee: str,
                      copy_label: str, logo: bytes = b"") -> None:
    T = lambda d: top + d  # noqa: E731
    ink, _tint = _brand(company)
    lw = _draw_logo(pg.c, logo, 0.5 * inch, pg.y(T(0.08)), 1.4 * inch, 0.45 * inch)
    nx = 0.5 + (lw / inch + 0.15 if lw else 0.0)
    pg.fit_text(nx, T(0.40), company.company_name or "", max(1.5, 6.6 - nx), "Helvetica-Bold", 11)
    pg.text(8.0, T(0.40), copy_label.upper(), "Helvetica-Bold", 7.5, align="right", color=GREY)
    pg.line(0.5, T(0.58), 8.0, T(0.58), 1.0, ink)

    # Left: identification
    rows = [("Payee", payee), ("Check No.", str(rec.check_number)), ("Check Date", _date(rec.check_date)),
            ("Pay Period", f"{_date(rec.period_start)} - {_date(rec.period_end)}")]
    t = T(0.84)
    for lab, val in rows:
        pg.text(0.5, t, lab, "Helvetica", 7.5, color=GREY)
        pg.fit_text(1.4, t, val, 2.9, "Helvetica-Bold" if lab == "Payee" else "Helvetica", 9)
        t += 0.2
    t += 0.08
    pg.text(0.5, t, "Memo", "Helvetica", 7.5, color=GREY)
    for i, ln in enumerate(_wrap(rec.memo or _default_memo(rec), "Helvetica", 8.5, 2.9, 2)):
        pg.text(1.4, t + i * 0.15, ln, "Helvetica", 8.5)

    # Right: amounts
    tx, tw = 4.75, 3.25
    vx = tx + tw - 0.08
    ht = T(0.7)
    pg.rect(tx, ht, tw, 0.2, 0, stroke=SHADE, fill=SHADE)
    pg.text(tx + 0.08, ht + 0.14, "PAYMENT DETAIL", "Helvetica-Bold", 7.5)
    reg, ot = _dec(rec.regular_hours), _dec(rec.overtime_hours)
    lines = []
    if ot:
        lines += [("Regular Hours", _hours(reg)), ("Overtime Hours", _hours(ot))]
    elif reg:
        lines.append(("Hours", _hours(reg)))
    lines += [("Amount", _money(rec.gross_pay)), ("Adjustment", _signed_adjustment(rec))]
    t = ht + 0.38
    for lab, val in lines:
        pg.text(tx + 0.08, t, lab, "Helvetica", 8.5)
        pg.text(vx, t, val, "Helvetica", 8.5, align="right")
        t += 0.19
    pg.line(tx, t - 0.12, tx + tw, t - 0.12, 0.8)
    t += 0.05
    pg.text(tx + 0.08, t, "CHECK AMOUNT", "Helvetica-Bold", 9.5)
    pg.text(vx, t, _money(rec.net_pay), "Helvetica-Bold", 9.5, align="right")


def _render_voucher(live, book, out: Path, sample: bool = False) -> Path:
    company = book.company
    c = _new_canvas(out, title="Check Alignment Test" if sample else "Checks")
    face_logo, stub_logo = _face_logo(book), _logo(book, "checks")
    for rec in live:
        emp = _SAMPLE_EMP if sample else book.employees.get(rec.employee_id)
        payee = _payee_name(rec, emp)
        _draw_check_in_slot(c, company, rec, emp, 0, void=sample, logo=face_logo)
        c.saveState()
        _apply_offset(c, company)
        pg = _Page(c)
        _draw_perforations(pg)
        _draw_simple_stub(pg, 3.5, company, rec, payee, "Payee Copy", stub_logo)
        _draw_simple_stub(pg, 7.0, company, rec, payee, "Company Copy", stub_logo)
        if sample:
            _draw_rulers(c)
        c.restoreState()
        c.showPage()
    c.save()
    return out


# --------------------------------------------------------------------------- handwrite
class _CheckBox(Flowable):
    """Empty tick box for the handwrite list."""

    def wrap(self, aw, ah):
        return 10, 10

    def draw(self):
        self.canv.setStrokeColor(INK)
        self.canv.setLineWidth(0.7)
        self.canv.rect(0, 0, 10, 10, stroke=1, fill=0)


def _render_handwrite(live, book, out: Path, sample: bool = False) -> Path:
    company = book.company
    recs = sorted(live, key=lambda r: (r.check_number, r.check_date))
    small = ParagraphStyle("hw", fontName="Helvetica", fontSize=7.5, leading=9)
    bold = ParagraphStyle("hwb", parent=small, fontName="Helvetica-Bold", fontSize=8.5, leading=10)
    header = ["Check #", "Date", "Payee", "Amount", "Amount in Words", "Memo",
              "Written / Initials"]
    data = [header]
    total = Decimal("0")
    for r in recs:
        emp = _SAMPLE_EMP if sample else book.employees.get(r.employee_id)
        total += _dec(r.net_pay)
        data.append([
            str(r.check_number), _date(r.check_date),
            Paragraph(_esc(_payee_name(r, emp)), bold),
            f"${_money(r.net_pay)}",
            Paragraph(_esc(_words_line(r.net_pay) + " Dollars"), small),
            Paragraph(_esc(r.memo or _default_memo(r)), small),
            _CheckBox(),
        ])
    n = len(recs)
    data.append(["", "", f"TOTAL ({n} check{'s' if n != 1 else ''})", f"${_money(total)}", "", "", ""])
    widths = [0.65, 0.8, 1.75, 0.95, 2.4, 2.2, 1.25]
    tbl = Table(data, colWidths=[x * inch for x in widths], repeatRows=1,
                rowHeights=[None] + [0.5 * inch] * n + [None])
    style = _table_style(company) + [
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("FONT", (3, 1), (3, -1), "Helvetica-Bold", 9),
        ("GRID", (0, 0), (-1, -2), 0.3, LIGHT),
        ("BOX", (0, 0), (-1, -2), 0.8, INK),
        ("TOPPADDING", (0, 1), (-1, -2), 7),
        ("BOTTOMPADDING", (0, 1), (-1, -2), 7),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, INK),
    ]
    tbl.setStyle(TableStyle(style))
    title = "Checks to Write" + (" — SAMPLE" if sample else "")
    story = [tbl, Spacer(1, 8), Paragraph(
        "Write each check from this list, then tick the box and initial it. Void any spoiled check "
        "and record the replacement number.",
        ParagraphStyle("n", fontName="Helvetica-Oblique", fontSize=7.5, textColor=GREY))]
    doc = _doc(out, landscape(letter), title)
    hdr = _header_fn(company, title, f"Printed {_date(date.today())}", _logo(book, "reports"))
    doc.build(story, onFirstPage=hdr, onLaterPages=hdr,
              canvasmaker=_canvasmaker(f"Printed {_date(date.today())}"))
    return out


# --------------------------------------------------------------------------- public API
def render_checks(records: list[PayrollRecord], book: "TimecardBook", out_path: str | Path,
                  start_position: int = 1) -> Path:
    """Print checks using ``book.company.check_layout``:

    * ``laser_3up``: 3 checks per letter sheet at 0", 3.5", 7.0" from the top.
    * ``manual_3up``: custom sheet (manual_sheet_width x manual_sheet_height), 3 rows, left stub.
    * ``voucher`` (default): letter, check on top + payee and company stubs; one check per page.
    * ``handwrite``: a "Checks to Write" list for writing checks by hand.

    ``start_position`` (1-3) applies to the 3-up layouts: the slot on the FIRST sheet to start in.
    Voided records are skipped; raises ValueError if nothing is left to print.
    """
    lay = _layout(book.company)
    sp = _check_start_position(start_position) if lay in ("laser_3up", "manual_3up") else 1
    live = _live_records(records)
    out = _prep_out(out_path)
    if lay == "manual_3up":
        return _render_manual(live, book, out, sp)
    if lay == "voucher":
        return _render_voucher(live, book, out)
    if lay == "handwrite":
        return _render_handwrite(live, book, out)
    return _render_laser(live, book, out, sp)


def render_alignment_test(book: "TimecardBook", out_path: str | Path, start_position: int = 1) -> Path:
    """Sample VOID checks in the configured layout with inch rulers along the paper edges
    (3-up layouts: from ``start_position`` to slot 3). For ``handwrite`` a sample list."""
    lay = _layout(book.company)
    sp = _check_start_position(start_position) if lay in ("laser_3up", "manual_3up") else 1
    out = _prep_out(out_path)
    company = book.company
    if lay == "manual_3up":
        return _render_manual(_sample_records(company, 4 - sp), book, out, sp, sample=True)
    if lay == "voucher":
        return _render_voucher(_sample_records(company, 1), book, out, sample=True)
    if lay == "handwrite":
        return _render_handwrite(_sample_records(company, 3), book, out, sample=True)
    return _render_laser(_sample_records(company, 4 - sp), book, out, sp, sample=True)


def _draw_rulers(c: rl_canvas.Canvas, w: float = PAGE_W, h: float = PAGE_H, note: bool = True) -> None:
    c.setStrokeColor(GREY)
    c.setFillColor(GREY)
    c.setLineWidth(0.3)
    nx, ny = int(round(w / inch * 8)), int(round(h / inch * 8))
    # Top edge: ticks every 1/8", labels each inch
    for i in range(nx + 1):
        x = i * inch / 8
        ln = 0.18 if i % 8 == 0 else 0.11 if i % 4 == 0 else 0.06
        c.line(x, h, x, h - ln * inch)
        if i % 8 == 0 and 0 < i < nx - 2:
            c.setFont("Helvetica", 6)
            c.drawCentredString(x, h - 0.27 * inch, str(i // 8))
    # Left edge
    for i in range(ny + 1):
        y = h - i * inch / 8
        ln = 0.18 if i % 8 == 0 else 0.11 if i % 4 == 0 else 0.06
        c.line(0, y, ln * inch, y)
        if i % 8 == 0 and 0 < i < ny - 2:
            c.setFont("Helvetica", 6)
            c.drawString(0.21 * inch, y - 2, str(i // 8))
    # Bottom and right edges (shorter, unlabeled) to reveal clipping
    for i in range(nx + 1):
        x = i * inch / 8
        c.line(x, 0, x, (0.12 if i % 8 == 0 else 0.06) * inch)
    for i in range(ny + 1):
        y = h - i * inch / 8
        c.line(w, y, w - (0.12 if i % 8 == 0 else 0.06) * inch, y)
    if note:
        c.setFont("Helvetica", 6.5)
        c.drawCentredString(w / 2, 0.2 * inch,
                            "Rulers in inches from the paper edge. Adjust Print Offset X/Y in Company settings "
                            "until the check fields line up with your stock.")


# --------------------------------------------------------------------------- register / time card
class _NumberedCanvas(rl_canvas.Canvas):
    """Canvas that writes 'Page N of M' on every page (two-pass via saved page states)."""

    footer_text = ""

    def __init__(self, *args, **kwargs):
        if PAGE_COMPRESSION is not None:
            kwargs.setdefault("pageCompression", PAGE_COMPRESSION)
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            w, _h = self._pagesize
            self.setFont("Helvetica", 7.5)
            self.setFillColor(GREY)
            self.drawRightString(w - 0.5 * inch, 0.35 * inch, f"Page {self._pageNumber} of {total}")
            if self.footer_text:
                self.drawString(0.5 * inch, 0.35 * inch, self.footer_text)
            super().showPage()
        super().save()


def _canvasmaker(footer: str):
    return type("_Footer", (_NumberedCanvas,), {"footer_text": footer})


def _doc(out: Path, pagesize, title: str) -> SimpleDocTemplate:
    kw = {}
    if PAGE_COMPRESSION is not None:
        kw["pageCompression"] = PAGE_COMPRESSION
    return SimpleDocTemplate(str(out), pagesize=pagesize, leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                             topMargin=1.1 * inch, bottomMargin=0.65 * inch, title=title,
                             author="TimeCard Pro", **kw)


def _header_fn(company: Company, title: str, subtitle: str, logo: bytes = b""):
    """Report page header: optional logo top-left, company name, title/subtitle right, brand rule."""
    ink, _tint = _brand(company)
    name = company.company_name or ""

    def on_page(c: rl_canvas.Canvas, doc):
        w, h = doc.pagesize
        c.saveState()
        lw = _draw_logo(c, logo, 0.5 * inch, h - 0.25 * inch, 2.0 * inch, 0.6 * inch)
        x = 0.5 * inch + (lw + 0.15 * inch if lw else 0)
        title_w = max(pdfmetrics.stringWidth(title, "Helvetica-Bold", 11),
                      pdfmetrics.stringWidth(subtitle or "", "Helvetica", 8.5))
        room = w - 0.5 * inch - title_w - 0.3 * inch - x
        size = 13.0
        while size > 8 and pdfmetrics.stringWidth(name, "Helvetica-Bold", size) > room:
            size -= 0.5
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", size)
        c.drawString(x, h - 0.6 * inch, name)
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(w - 0.5 * inch, h - 0.6 * inch, title)
        c.setFont("Helvetica", 8.5)
        c.setFillColor(GREY)
        if subtitle:
            c.drawRightString(w - 0.5 * inch, h - 0.77 * inch, subtitle)
        c.setStrokeColor(ink)
        c.setLineWidth(1.0)
        c.line(0.5 * inch, h - 0.92 * inch, w - 0.5 * inch, h - 0.92 * inch)
        c.restoreState()
    return on_page


_BASE_TABLE_STYLE = [
    ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
    ("BACKGROUND", (0, 0), (-1, 0), SHADE),
    ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
]


def _table_style(company: Company) -> list:
    """Base table style with the header row in the brand tint / ink."""
    ink, tint = _brand(company)
    return list(_BASE_TABLE_STYLE) + [
        ("BACKGROUND", (0, 0), (-1, 0), tint),
        ("TEXTCOLOR", (0, 0), (-1, 0), ink),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, ink),
    ]


def render_register(records: list[PayrollRecord], book: "TimecardBook", out_path: str | Path,
                    title: str = "Payroll Register") -> Path:
    """Landscape table of payroll records with column totals (voided checks excluded from totals).
    Tax columns that are zero for every record are omitted (contractors have no withholding)."""
    out = _prep_out(out_path)
    company = book.company
    recs = sorted(records, key=lambda r: (r.check_date, r.check_number))

    def any_nonzero(attr):
        return any(_dec(getattr(r, attr)) for r in recs)

    # (header, value function, kind, relative width)
    cols = [("Check #", lambda r: r.check_number, "text", 0.7),
            ("Date", lambda r: r.check_date, "text", 0.85),
            ("Payee", None, "name", 2.2)]
    if any_nonzero("overtime_hours"):
        cols += [("Reg Hrs", lambda r: _dec(r.regular_hours), "hours", 0.65),
                 ("OT Hrs", lambda r: _dec(r.overtime_hours), "hours", 0.65)]
    else:
        cols += [("Hours", lambda r: _dec(r.regular_hours) + _dec(r.overtime_hours), "hours", 0.7)]
    cols += [("Amount", lambda r: _dec(r.gross_pay), "money", 0.9)]
    for hdr, attr in (("Federal", "federal_tax"), ("State", "state_tax"),
                      ("Soc Sec", "social_security"), ("Medicare", "medicare")):
        if any_nonzero(attr):
            cols.append((hdr, (lambda a: lambda r: _dec(getattr(r, a)))(attr), "money", 0.75))
    cols += [("Adjustment", lambda r: _dec(r.other_deductions), "money", 0.85),
             ("Check Amount", lambda r: _dec(r.net_pay), "money", 0.95),
             ("Void", lambda r: "VOID" if r.voided else "", "text", 0.4)]

    data = [[c[0] for c in cols]]
    totals = [Decimal("0")] * len(cols)
    voided_rows = []
    for r in recs:
        row = []
        for i, (hdr, fn, kind, _w) in enumerate(cols):
            if kind == "name":
                emp = book.employees.get(r.employee_id)
                row.append((emp.display_name if emp is not None else r.employee_name) or r.employee_id)
                continue
            v = fn(r)
            if kind == "money":
                row.append(_money(v))
            elif kind == "hours":
                row.append(_hours(v))
            else:
                row.append(_date(v) if isinstance(v, date) else str(v))
            if kind in ("money", "hours") and not r.voided:
                totals[i] += v
        data.append(row)
        if r.voided:
            voided_rows.append(len(data) - 1)
    n_live = len(recs) - len(voided_rows)
    tot_row = []
    for i, (hdr, fn, kind, _w) in enumerate(cols):
        if kind == "name":
            tot_row.append(f"TOTALS ({n_live} check{'s' if n_live != 1 else ''})")
        elif kind == "money":
            tot_row.append(_money(totals[i]))
        elif kind == "hours":
            tot_row.append(_hours(totals[i]))
        else:
            tot_row.append("")
    data.append(tot_row)

    widths = [c[3] for c in cols]
    scale = 10.0 / sum(widths)
    tbl = Table(data, colWidths=[w * scale * inch for w in widths], repeatRows=1)
    style = _table_style(company) + [
        ("ALIGN", (3, 0), (-2, -1), "RIGHT"),
        ("ALIGN", (-1, 0), (-1, -1), "CENTER"),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, INK),
        ("FONT", (-1, 1), (-1, -1), "Helvetica-Bold", 6.5),
    ]
    for i in range(1, len(data) - 1):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.Color(0.97, 0.97, 0.97)))
    for i in voided_rows:
        style.append(("TEXTCOLOR", (0, i), (-2, i), GREY))
        style.append(("TEXTCOLOR", (-1, i), (-1, i), colors.red))
    tbl.setStyle(TableStyle(style))

    if recs:
        subtitle = f"Check dates {_date(recs[0].check_date)} - {_date(recs[-1].check_date)}"
    else:
        subtitle = "No records"
    story = [tbl]
    notes = []
    if voided_rows:
        notes.append("Voided checks are listed but excluded from totals.")
    notes.append("Adjustment: positive amounts are deducted from the check, negative amounts are added.")
    nstyle = ParagraphStyle("n", fontName="Helvetica-Oblique", fontSize=7.5, textColor=GREY)
    story += [Spacer(1, 6)] + [Paragraph(n, nstyle) for n in notes]
    doc = _doc(out, landscape(letter), title)
    hdr = _header_fn(company, title, subtitle, _logo(book, "reports"))
    doc.build(story, onFirstPage=hdr, onLaterPages=hdr,
              canvasmaker=_canvasmaker(f"Printed {_date(date.today())}"))
    return out


def _daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def render_timecard(employee: Employee, entries: list[TimeEntry], start: date, end: date,
                    book: "TimecardBook", out_path: str | Path) -> Path:
    """Printable time card for one employee, one row per day, with weekly subtotals and
    signature lines. Contains hours only — no pay rate and no money."""
    out = _prep_out(out_path)
    company = book.company
    by_date = {e.work_date: e for e in entries
               if e.employee_id == employee.employee_id and start <= e.work_date <= end}
    header = ["Date", "In", "Out", "In", "Out", "Lunch\nDeduct (min)", "Hours", "Notes"]
    data = [header]
    style = _table_style(company) + [
        ("ALIGN", (1, 0), (6, -1), "CENTER"),
        ("ALIGN", (6, 1), (6, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.3, LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.8, INK),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]
    notes_style = ParagraphStyle("notes", fontName="Helvetica", fontSize=7.5, leading=9)
    week_total = Decimal("0")
    cur_week = None

    def add_subtotal(ws: date):
        data.append([f"Week of {_date(ws)}", "", "", "", "", "Week total", _hours(week_total), ""])
        i = len(data) - 1
        style.extend([("BACKGROUND", (0, i), (-1, i), _brand(company)[1]),
                      ("FONT", (0, i), (-1, i), "Helvetica-Bold", 8),
                      ("SPAN", (0, i), (4, i)),
                      ("LINEABOVE", (0, i), (-1, i), 0.6, INK)])

    for d in _daterange(start, end):
        ws = calc.week_start_for(d, company.week_start)
        if cur_week is not None and ws != cur_week:
            add_subtotal(cur_week)
            week_total = Decimal("0")
        cur_week = ws
        e = by_date.get(d)
        if e is not None:
            hrs = _dec(calc.day_hours(e))   # hours worked; time off is noted, not counted
            week_total += hrs
            note = e.notes
            if calc.is_leave(e):
                note = f"{calc.hours_type(e)} {calc.leave_hours(e):.2f} h" + (f"; {e.notes}" if e.notes else "")
            elif e.salary_hours is not None:
                note = "Hours entered" + (f"; {e.notes}" if e.notes else "")
            row = [d.strftime("%a %m/%d/%Y"), calc.format_time(e.in1), calc.format_time(e.out1),
                   calc.format_time(e.in2), calc.format_time(e.out2),
                   str(e.lunch_deduct_min) if e.lunch_deduct_min else "",
                   _hours(hrs) if (hrs or e.in1 or e.out1 or e.salary_hours is not None) else "",
                   Paragraph(_esc(note), notes_style) if note else ""]
        else:
            row = [d.strftime("%a %m/%d/%Y"), "", "", "", "", "", "", ""]
        data.append(row)
        if d.weekday() >= 5:
            style.append(("TEXTCOLOR", (0, len(data) - 1), (0, len(data) - 1), GREY))
    if cur_week is not None:
        add_subtotal(cur_week)

    summary = calc.summarize_hours(list(by_date.values()), company, start, end)
    widths = [1.15, 0.72, 0.72, 0.72, 0.72, 0.8, 0.6, 2.07]
    tbl = Table(data, colWidths=[w * inch for w in widths], repeatRows=1)
    tbl.setStyle(TableStyle(style))

    lab = ParagraphStyle("lab", fontName="Helvetica", fontSize=9, leading=12)
    info = Table([
        [Paragraph(f"<b>Employee:</b> {_esc(employee.full_name)}", lab),
         Paragraph(f"<b>Employee ID:</b> {_esc(employee.employee_id)}", lab)],
        [Paragraph(f"<b>Period:</b> {_date(start)} - {_date(end)}", lab),
         Paragraph(f"<b>Workweek starts:</b> {_esc(company.week_start)}", lab)],
    ], colWidths=[4.25 * inch, 3.25 * inch])
    info.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))

    totals = Table([
        ["Regular Hours", _hours(summary.regular)],
        ["Overtime Hours", _hours(summary.overtime)],
        ["Total Hours", _hours(summary.regular + summary.overtime)],
    ], colWidths=[1.5 * inch, 0.9 * inch], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 9.5),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ("BOX", (0, 0), (-1, -1), 0.8, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    sig = Table([
        ["", "", "", "", "", "", ""],
        ["Employee Signature", "", "Date", "", "Supervisor Signature", "", "Date"],
    ], colWidths=[w * inch for w in (2.4, 0.2, 0.85, 0.35, 2.4, 0.2, 0.85)],
        rowHeights=[0.45 * inch, None], hAlign="LEFT")
    sig.setStyle(TableStyle([
        ("FONT", (0, 1), (-1, 1), "Helvetica", 7.5),
        ("TEXTCOLOR", (0, 1), (-1, 1), GREY),
        *[("LINEBELOW", (i, 0), (i, 0), 0.7, INK) for i in (0, 2, 4, 6)],
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    cert = Paragraph("I certify that the hours shown above are a true and accurate record of the time I worked.",
                     ParagraphStyle("cert", fontName="Helvetica-Oblique", fontSize=7.5, textColor=GREY))

    story = [info, Spacer(1, 8), tbl, Spacer(1, 10), totals, Spacer(1, 14), cert, Spacer(1, 4), sig]
    title = "Employee Time Card"
    doc = _doc(out, letter, title)
    hdr = _header_fn(company, title, f"{_date(start)} - {_date(end)}", _logo(book, "reports"))
    doc.build(story, onFirstPage=hdr, onLaterPages=hdr,
              canvasmaker=_canvasmaker(f"{employee.display_name} ({employee.employee_id})"))
    return out


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
