"""Tests for timecard.checks (PDF rendering). Run from timecard/:
    ..\\.venv\\Scripts\\python -m unittest tests.test_checks -v
"""
from __future__ import annotations

import os
import re
import sys
import types
import unittest
import zlib
from datetime import date, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from timecard.models import Company, Employee, HoursSummary, PayrollRecord, TimeEntry, YtdTotals  # noqa: E402

_NEEDED = ("amount_to_words", "money", "format_time", "day_hours", "week_start_for",
           "ytd_totals", "summarize_hours")


def _install_calc_stub() -> None:
    """Minimal calc used only when the real timecard.calc is missing or incomplete."""
    m = types.ModuleType("timecard.calc")

    def money(x):
        return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)

    def format_time(t):
        return t.strftime("%I:%M %p").lstrip("0") if t else ""

    def _pair(a, b):
        if not a or not b:
            return 0
        s = (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)
        return s + 1440 if s < 0 else s

    def day_hours(e):
        mins = _pair(e.in1, e.out1) + _pair(e.in2, e.out2) - e.lunch_deduct_min
        return money(max(0, mins) / 60)

    def week_start_for(d, ws):
        return d - timedelta(days=(d.weekday() if ws == "Monday" else (d.weekday() + 1) % 7))

    def summarize_hours(entries, company, start, end):
        weeks = {}
        for e in entries:
            if start <= e.work_date <= end:
                k = week_start_for(e.work_date, company.week_start)
                weeks[k] = weeks.get(k, Decimal(0)) + day_hours(e)
        th = Decimal(str(company.overtime_threshold_weekly))
        reg = sum((min(v, th) for v in weeks.values()), Decimal(0))
        ot = sum((max(Decimal(0), v - th) for v in weeks.values()), Decimal(0))
        return HoursSummary(reg, ot, sorted(weeks.items()))

    def ytd_totals(records, employee_id, through, entries=None):
        y = YtdTotals()
        for r in records:
            if r.employee_id == employee_id and not r.voided and r.check_date <= through \
                    and r.check_date.year == through.year:
                for f in ("gross_pay", "federal_tax", "state_tax", "social_security", "medicare",
                          "other_deductions", "net_pay"):
                    setattr(y, f, getattr(y, f) + getattr(r, f))
        return y

    ones = "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen " \
           "Fifteen Sixteen Seventeen Eighteen Nineteen".split()
    tens = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()

    def _w(n):
        if n < 20:
            return ones[n]
        if n < 100:
            return tens[n // 10] + ("-" + ones[n % 10] if n % 10 else "")
        if n < 1000:
            return ones[n // 100] + " Hundred" + (" " + _w(n % 100) if n % 100 else "")
        return _w(n // 1000) + " Thousand" + (" " + _w(n % 1000) if n % 1000 else "")

    def amount_to_words(a):
        a = money(a)
        return f"{_w(int(a))} and {int((a - int(a)) * 100):02d}/100"

    for name, fn in list(locals().items()):
        if callable(fn) and not name.startswith("_"):
            setattr(m, name, fn)
    sys.modules["timecard.calc"] = m
    import timecard
    timecard.calc = m


try:
    from timecard import calc as _real_calc
    if not all(hasattr(_real_calc, n) for n in _NEEDED):
        raise ImportError("calc incomplete")
except Exception:  # real module missing or broken while agent A is still writing it
    _install_calc_stub()

from timecard import checks  # noqa: E402

OUT_DIR = Path(os.environ.get("TEMP", "/tmp")) / "claude" / "checks_test"
RATE = Decimal("23.47")


def _pdf_text(path: Path) -> str:
    """Raw text of an (uncompressed) PDF, inflating any Flate streams just in case."""
    raw = path.read_bytes()
    parts = [raw.decode("latin-1")]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            parts.append(zlib.decompress(m.group(1)).decode("latin-1"))
        except Exception:
            pass
    return "\n".join(parts)


def _make_book(check_stock="blank", micr=""):
    company = Company(
        company_name="Acme Widget Works, LLC", address1="1200 Industrial Parkway",
        address2="Springfield, IL 62704", phone="(217) 555-0142", bank_name="First Prairie Bank",
        bank_address="300 Main St, Springfield, IL 62701", routing_number="071000013",
        account_number="123456789", year=2026, week_start="Monday", check_stock=check_stock,
        micr_font_path=micr, signature_line_text="Authorized Signature",
        check_layout="laser_3up",  # ChecksPdfTests cover the 3-per-page layout; CheckLayoutTests the rest
    )
    emp = Employee("E001", "Maria", "Gonzalez-Whitfield", "4512 Old Orchard Road, Apt 3B",
                   "Springfield, IL 62711", "Hourly", RATE)
    emp2 = Employee("E002", "Tom", "Lee", "88 Elm St", "Chatham, IL 62629", "Hourly", RATE)
    entries = {}
    start = date(2026, 10, 5)
    for i in range(14):
        d = start + timedelta(days=i)
        if d.weekday() < 5:
            e = TimeEntry(d, "E001", time(8, 0), time(12, 0), time(12, 30), time(17, 45 if i % 3 else 30),
                          0, "Forgot lunch punch" if i == 3 else "")
            if i == 8:
                e = TimeEntry(d, "E001", time(7, 30), time(16, 30), lunch_deduct_min=30,
                              notes="Single punch pair, lunch deducted")
            entries[e.key] = e
    # Independent contractors: no withholding; check amount = gross - adjustment.
    payroll = [
        PayrollRecord(1040, date(2026, 10, 2), date(2026, 9, 21), date(2026, 10, 4), "E001",
                      "Gonzalez-Whitfield, Maria", Decimal("80.00"), gross_pay=Decimal("1877.60"),
                      net_pay=Decimal("1877.60")),
        PayrollRecord(1041, date(2026, 10, 16), date(2026, 10, 5), date(2026, 10, 18), "E001",
                      "Gonzalez-Whitfield, Maria", Decimal("82.50"), gross_pay=Decimal("1936.28"),
                      other_deductions=Decimal("-25.00"), net_pay=Decimal("1961.28"),
                      memo="Includes 25.00 mileage reimbursement"),
        PayrollRecord(1042, date(2026, 10, 16), date(2026, 10, 5), date(2026, 10, 18), "E002",
                      "Lee, Tom", Decimal("40"), gross_pay=Decimal("938.80"),
                      net_pay=Decimal("938.80"), voided=True),
        PayrollRecord(1043, date(2026, 10, 16), date(2026, 10, 5), date(2026, 10, 18), "E002",
                      "Lee, Tom", Decimal("37.25"), gross_pay=Decimal("874.26"),
                      other_deductions=Decimal("50.00"), net_pay=Decimal("824.26")),
    ]

    def entries_for(employee_id, s, e):
        return sorted((x for x in entries.values()
                       if (employee_id is None or x.employee_id == employee_id) and s <= x.work_date <= e),
                      key=lambda x: (x.work_date, x.employee_id))

    return SimpleNamespace(company=company, employees={"E001": emp, "E002": emp2}, entries=entries,
                           payroll=payroll, entries_for=entries_for)


def _page_count(p: Path) -> int:
    return len(re.findall(rb"/Type /Page[^s]", p.read_bytes()))


class ChecksPdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cls._old = checks.PAGE_COMPRESSION
        checks.PAGE_COMPRESSION = 0

    @classmethod
    def tearDownClass(cls):
        checks.PAGE_COMPRESSION = cls._old

    def assertPdf(self, p: Path, min_size=2000):
        self.assertTrue(p.exists(), p)
        self.assertGreater(p.stat().st_size, min_size)
        self.assertTrue(p.read_bytes().startswith(b"%PDF"))
        text = _pdf_text(p)
        self.assertNotIn(str(RATE), text, "pay rate leaked into PDF")
        self.assertNotIn("23,47", text)
        return text

    def test_checks_blank_stock_three_up(self):
        book = _make_book("blank")
        p = checks.render_checks(book.payroll, book, OUT_DIR / "checks_blank.pdf")
        text = self.assertPdf(p)
        self.assertEqual(_page_count(p), 1)  # 3 live checks fill one sheet
        self.assertIn("Maria Gonzalez-Whitfield", text)
        self.assertIn("**1,961.28", text)
        self.assertIn("**824.26", text)
        self.assertIn("First Prairie Bank", text)
        self.assertIn("071000013", text)
        self.assertIn("not bank-processable", text)
        self.assertIn("Eight Hundred Twenty-Four and 26/100", text)  # short enough not to wrap
        self.assertNotIn("938.80", text)  # voided check skipped
        self.assertIn("mileage reimbursement", text)  # explicit memo wins
        # default memo: period and hours, never the rate
        self.assertIn("Contract labor 09/21", text)
        self.assertIn("10/04/2026", text)
        self.assertIn("80.00 hrs", text)
        # no stubs / YTD / tax summaries any more
        for gone in ("EMPLOYEE COPY", "Year to Date", "NET PAY", "Social Security"):
            self.assertNotIn(gone, text)

    def test_default_memo(self):
        rec = PayrollRecord(1, date(2026, 1, 9), date(2025, 12, 29), date(2026, 1, 11), "E001", "x",
                            Decimal("38.5"), Decimal("1.25"))
        self.assertEqual(checks._default_memo(rec),
                         "Contract labor 12/29/2025–01/11/2026 · 39.75 hrs")

    def test_start_position_and_paging(self):
        book = _make_book("preprinted")
        live = [r for r in book.payroll if not r.voided]
        p = checks.render_checks(live, book, OUT_DIR / "checks_start3.pdf", start_position=3)
        self.assertPdf(p)
        self.assertEqual(_page_count(p), 2)  # slot 3 on sheet 1, then slots 1-2 on sheet 2
        p = checks.render_checks(live * 2, book, OUT_DIR / "checks_six.pdf", start_position=1)
        self.assertEqual(_page_count(p), 2)
        p = checks.render_checks(live * 2 + live[:1], book, OUT_DIR / "checks_seven.pdf")
        self.assertEqual(_page_count(p), 3)
        for bad in (0, 4, "x"):
            with self.assertRaises(ValueError):
                checks.render_checks(live, book, OUT_DIR / "never.pdf", start_position=bad)

    def test_checks_preprinted_stock_with_offset(self):
        book = _make_book("preprinted")
        book.company.print_offset_x = 0.1
        book.company.print_offset_y = -0.05
        p = checks.render_checks(book.payroll[1:2], book, OUT_DIR / "checks_preprinted.pdf",
                                 start_position=2)
        text = self.assertPdf(p)
        self.assertNotIn("First Prairie Bank", text)
        self.assertNotIn("not bank-processable", text)
        self.assertNotIn("PAY TO THE", text)
        self.assertIn("**1,961.28", text)

    def test_all_voided_raises(self):
        book = _make_book()
        with self.assertRaises(ValueError):
            checks.render_checks([book.payroll[2]], book, OUT_DIR / "never.pdf")

    def test_bad_micr_font_falls_back(self):
        book = _make_book("blank", micr=str(OUT_DIR / "does_not_exist.ttf"))
        p = checks.render_checks(book.payroll[1:2], book, OUT_DIR / "checks_badfont.pdf")
        self.assertIn("not bank-processable", self.assertPdf(p))

    def test_register_contractors_drops_zero_tax_columns(self):
        book = _make_book()
        p = checks.render_register(book.payroll, book, OUT_DIR / "register.pdf")
        text = self.assertPdf(p)
        self.assertIn("Payroll Register", text)
        self.assertIn("VOID", text)
        self.assertIn("Adjustment", text)
        self.assertIn("Check Amount", text)
        for gone in ("Federal", "Soc Sec", "Medicare", "OT Hrs"):
            self.assertNotIn(gone, text)
        self.assertIn("4,688.14", text)  # amount total excludes the voided 938.80
        self.assertIn("4,663.14", text)  # check amount total
        self.assertIn("199.75", text)    # hours total
        self.assertIn("Page 1 of 1", text)

    def test_register_keeps_nonzero_tax_columns(self):
        book = _make_book()
        book.payroll[0].overtime_hours = Decimal("1.50")
        book.payroll[0].social_security = Decimal("116.41")
        p = checks.render_register(book.payroll, book, OUT_DIR / "register_taxes.pdf")
        text = self.assertPdf(p)
        self.assertIn("Soc Sec", text)
        self.assertIn("OT Hrs", text)
        self.assertNotIn("Federal", text)

    def test_register_multipage(self):
        book = _make_book()
        base = book.payroll[1]
        recs = [PayrollRecord(2000 + i, base.check_date, base.period_start, base.period_end, "E001",
                              base.employee_name, Decimal("40"), gross_pay=Decimal("100.00"),
                              net_pay=Decimal("90.00"), other_deductions=Decimal("10.00"))
                for i in range(80)]
        p = checks.render_register(recs, book, OUT_DIR / "register_long.pdf")
        text = self.assertPdf(p)
        self.assertIn("Page 3 of 3", text)
        self.assertIn("8,000.00", text)
        self.assertGreaterEqual(text.count("Check Amount"), 3)  # header repeated

    def test_timecard(self):
        book = _make_book()
        emp = book.employees["E001"]
        s, e = date(2026, 10, 5), date(2026, 10, 18)
        p = checks.render_timecard(emp, book.entries_for("E001", s, e), s, e, book, OUT_DIR / "timecard.pdf")
        text = self.assertPdf(p)
        self.assertIn("Supervisor Signature", text)
        self.assertIn("Week of 10/05/2026", text)
        self.assertIn("Week of 10/12/2026", text)
        self.assertNotIn("$", text.split("stream", 1)[-1].replace("\\$", ""))

    def test_alignment(self):
        book = _make_book("blank")
        p = checks.render_alignment_test(book, OUT_DIR / "alignment.pdf")
        text = self.assertPdf(p)
        self.assertEqual(_page_count(p), 1)
        self.assertEqual(text.count("ALIGNMENT TEST)"), 6)  # stamp + memo in each of 3 slots
        p = checks.render_alignment_test(book, OUT_DIR / "alignment_slot3.pdf", start_position=3)
        self.assertEqual(_pdf_text(p).count("ALIGNMENT TEST)"), 2)


class CheckLayoutTests(unittest.TestCase):
    """manual_3up / voucher / handwrite layouts (laser_3up is covered by ChecksPdfTests)."""

    @classmethod
    def setUpClass(cls):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        cls._old = checks.PAGE_COMPRESSION
        checks.PAGE_COMPRESSION = 0

    @classmethod
    def tearDownClass(cls):
        checks.PAGE_COMPRESSION = cls._old

    assertPdf = ChecksPdfTests.assertPdf

    def _book(self, layout, stock="preprinted"):
        book = _make_book(stock)
        book.company.check_layout = layout
        return book

    def test_unknown_layout_falls_back_to_voucher_default(self):
        self.assertEqual(Company().check_layout, "voucher")
        book = self._book("bogus")
        p = checks.render_checks(book.payroll, book, OUT_DIR / "layout_bogus.pdf")
        self.assertIn(b"/MediaBox [ 0 0 612 792 ]", p.read_bytes())
        self.assertEqual(_page_count(p), len(checks._live_records(book.payroll)))  # one check per page

    def test_manual_3up_page_size_and_slots(self):
        book = self._book("manual_3up", "blank")
        p = checks.render_checks(book.payroll, book, OUT_DIR / "manual_3up.pdf", start_position=2)
        text = self.assertPdf(p)
        self.assertIn(b"/MediaBox [ 0 0 792 756 ]", p.read_bytes())  # 11 x 10.5 in
        self.assertEqual(_page_count(p), 2)  # slots 2-3, then slot 1 of sheet 2
        self.assertIn("No. 1040", text)
        self.assertIn("$1,961.28", text)  # stub amount
        self.assertIn("**1,961.28", text)  # check amount box
        self.assertIn("HOURS", text)
        self.assertNotIn("938.80", text)

    def test_manual_3up_custom_size(self):
        book = self._book("manual_3up")
        book.company.manual_sheet_width = 10.0
        book.company.manual_sheet_height = 9.0
        book.company.manual_stub_width = 2.0
        p = checks.render_checks(book.payroll, book, OUT_DIR / "manual_custom.pdf")
        self.assertPdf(p)
        self.assertIn(b"/MediaBox [ 0 0 720 648 ]", p.read_bytes())
        self.assertEqual(_page_count(p), 1)
        book.company.manual_stub_width = 9.5
        with self.assertRaises(ValueError):
            checks.render_checks(book.payroll, book, OUT_DIR / "never.pdf")

    def test_voucher_one_per_page(self):
        book = self._book("voucher", "blank")
        p = checks.render_checks(book.payroll, book, OUT_DIR / "voucher.pdf", start_position=3)
        text = self.assertPdf(p)
        self.assertEqual(_page_count(p), 3)  # 3 live checks, start_position ignored
        self.assertIn(b"/MediaBox [ 0 0 612 792 ]", p.read_bytes())
        for s in ("PAYEE COPY", "COMPANY COPY", "CHECK AMOUNT", "Adjustment", "+25.00", "-50.00",
                  "mileage reimbursement"):
            self.assertIn(s, text)
        for gone in ("Year to Date", "Social Security", "Federal", "Overtime Hours"):
            self.assertNotIn(gone, text)

    def test_voucher_shows_overtime_only_when_present(self):
        book = self._book("voucher")
        book.payroll[0].overtime_hours = Decimal("2.00")
        p = checks.render_checks(book.payroll[:1], book, OUT_DIR / "voucher_ot.pdf")
        self.assertIn("Overtime Hours", self.assertPdf(p))

    def test_handwrite_lists_all_live(self):
        book = self._book("handwrite")
        p = checks.render_checks(book.payroll, book, OUT_DIR / "handwrite.pdf")
        text = self.assertPdf(p)
        self.assertIn("Checks to Write", text)
        for no in ("1040", "1041", "1043"):
            self.assertIn(f"({no})", text)
        self.assertNotIn("1042", text)
        self.assertIn("Eight Hundred Twenty-Four and 26/100", text)  # short enough not to wrap
        self.assertIn("$4,663.14", text)

    def test_handwrite_many_rows(self):
        book = self._book("handwrite")
        base = book.payroll[0]
        recs = [PayrollRecord(3000 + i, base.check_date, base.period_start, base.period_end, "E001",
                              base.employee_name, Decimal("10"), gross_pay=Decimal("100.00"),
                              net_pay=Decimal("100.00")) for i in range(45)]
        p = checks.render_checks(recs, book, OUT_DIR / "handwrite_long.pdf")
        text = self.assertPdf(p)
        self.assertGreater(_page_count(p), 1)
        self.assertIn("(3044)", text)
        self.assertIn("$4,500.00", text)

    def test_alignment_every_layout(self):
        for layout in checks.LAYOUTS:
            with self.subTest(layout=layout):
                book = self._book(layout, "blank")
                p = checks.render_alignment_test(book, OUT_DIR / f"alignment_{layout}.pdf")
                text = self.assertPdf(p)
                self.assertEqual(_page_count(p), 1)
                if layout == "handwrite":
                    self.assertIn("SAMPLE", text)
                else:
                    self.assertIn("ALIGNMENT TEST", text)
                if layout == "manual_3up":
                    self.assertIn(b"/MediaBox [ 0 0 792 756 ]", p.read_bytes())


if __name__ == "__main__":
    unittest.main()
