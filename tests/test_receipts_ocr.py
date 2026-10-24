"""Tests for timecard.receipts_ocr: field rules on receipt text, and Windows OCR on generated receipt images."""
from __future__ import annotations

import os
import random
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from timecard import receipts_ocr as ro
from timecard.receipts_ocr import HIGH, LOW, extract_fields, parse_money

D = Decimal
TODAY = date(2026, 10, 24)


def fields(text: str, **kw) -> ro.ReceiptFields:
    return extract_fields(text.strip("\n").splitlines(), today=TODAY, **kw)


GROCERY = """
KROGER
1050 E Main St
Springfield, IL 62701
(217) 555-0143
BANANAS
2 @ 0.59            1.18 F
MILK 2% GAL         3.49 F
BREAD WHEAT         2.79 F
PAPER TOWELS 6PK    8.99 T
YOU SAVED           1.00
SUBTOTAL           15.45
TAX                 0.83
**** BALANCE       16.28
VISA TEND          16.28
CHANGE              0.00
10/22/26 4:51 PM 0412 03 1234 567
THANK YOU FOR SHOPPING KROGER
"""

HARDWARE = """
THE HOME DEPOT
Store 0423
4410 W Lake Ave, Peoria, IL 61615
(309) 555-0199
SALE  SELF CHECKOUT
012345678901 2X4X8 STUD <A>        3.98
012345678902 2X4X8 STUD <A>        3.98
000000123456 DECK SCREWS 5LB      34.97
000000654321 HAMMER 16OZ          24.97
000000777777 WORK GLOVES           9.97
SUBTOTAL                          77.87
SALES TAX 8.10%                    6.30
TOTAL                            $84.17
XXXXXXXXXXXX1234 VISA       USD$ 84.17
AUTH CODE 012345/1234567
10/24/2026 09:12 AM
RETURN POLICY DEFINITIONS POLICY ID DAYS POLICY EXPIRES ON
A 1 90 01/22/2027
"""

RESTAURANT = """
Joe's Diner
22 Elm Street
Springfield IL 62704
Server: Amy        Table 4
Guest Check #1234
Oct 23, 2026 7:42 PM
2 Cheeseburger            25.00
1 Fries                    4.50
2 Iced Tea                 6.00
1 Apple Pie                3.00
Subtotal                  38.50
Tax                        3.18
Total                     41.68
Suggested tip: 18% = 6.93  20% = 7.70
Tip                        8.00
Total                     49.68
MASTERCARD ************5521
Thank you!
"""

GAS = """
SHELL
FOOD MART #4471
1200 HWY 51 S
2026-10-20 06:58
PUMP# 05
REGULAR
10.523 G @ $3.459/G
FUEL SALE                $36.40
TOTAL                    $36.40
DEBIT ************8890
APPROVED
"""

CARD_SLIP = """
MERCHANT COPY
ACME LANDSCAPE SUPPLY
88 Industrial Pkwy
Decatur, IL 62521
SALE
DATE: 10/19/26   TIME: 14:03
CARD: VISA ************4417
AMOUNT:              $1,245.00
APPROVED  AUTH: 0A12B3
CUSTOMER SIGNATURE
"""

MESSY = """
H0ME DEP0T
St0re 0423
ST 4410 W Lake Ave
1O/24/26 O9:12
HAMMER 16OZ        24.97
DECK SCREWS        34 .97
STUD 2X4            3,98
SUBT0TAL           63.92
TAX                 5.18
T0TAL              69.1O
CASH              100.00
CHANGE             30.90
"""

REFUND = """
BEST HARDWARE
10/24/26
RETURN
HAMMER 16OZ                84.17-
SUBTOTAL                   84.17-
TOTAL                      84.17-
CASH REFUND                84.17
"""


class MoneyTests(unittest.TestCase):
    def test_parse_money_formats(self) -> None:
        cases = {
            "TOTAL $14.03": D("14.03"), "TOTAL 14.03": D("14.03"), "$1,234.56": D("1234.56"),
            "BALANCE 84.17-": D("-84.17"), "DISCOUNT (3.00)": D("-3.00"), "AMOUNT -5.25": D("-5.25"),
            "TOTAL 12 .99": D("12.99"), "TOTAL $ 14.03": D("14.03"), "TOTAL 14,03": D("14.03"),
            "TOTAL $14 03": D("14.03"), "TAX I.e4": D("1.04"), "T0TAL 69.1O": D("69.10"),
            "USD$ 84.17": D("84.17"), "TOTAL 12,345.67": D("12345.67"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_money(text), expected)

    def test_decimal_point_read_as_a_bullet(self) -> None:
        f = extract_fields(["HOME DEPOT", "SUBTOTAL 19.48", "TAX 1 • 27", "TOTAL 20.75"])
        self.assertEqual(f.tax.value, Decimal("1.27"))
        self.assertEqual(f.total.value, Decimal("20.75"))
        self.assertEqual(f.warnings, [])   # subtotal + tax now matches the total

    def test_not_money(self) -> None:
        for text in ("10.523 G @ $3.459/G", "TAX 8.25%", "10/24/26", "12:30", "VISA ****0042", "NO PRICE",
                     "PHONE 555 0143", "QTY 3"):
            with self.subTest(text=text):
                self.assertIsNone(parse_money(text))

    def test_money_is_decimal_two_places(self) -> None:
        value = parse_money("TOTAL 5.00")
        self.assertIsInstance(value, Decimal)
        self.assertEqual(value.as_tuple().exponent, -2)


class ReceiptStyleTests(unittest.TestCase):
    def test_grocery(self) -> None:
        r = fields(GROCERY)
        self.assertEqual(r.vendor.value, "KROGER")
        self.assertEqual(r.date.value, date(2026, 10, 22))
        self.assertEqual(r.total.value, D("16.28"))
        self.assertEqual(r.total.confidence, HIGH)
        self.assertEqual(r.total.source, "**** BALANCE       16.28")
        self.assertEqual(r.subtotal.value, D("15.45"))
        self.assertEqual(r.tax.value, D("0.83"))
        self.assertEqual(r.payment_method.value, "Card")
        self.assertEqual([tuple(i) for i in r.items], [
            ("BANANAS 2 @ 0.59", D("1.18")), ("MILK 2% GAL", D("3.49")), ("BREAD WHEAT", D("2.79")),
            ("PAPER TOWELS 6PK", D("8.99")), ("YOU SAVED", D("-1.00"))])
        self.assertEqual(r.items_confidence, HIGH)   # items add up to the subtotal

    def test_hardware(self) -> None:
        r = fields(HARDWARE)
        self.assertEqual(r.vendor.value, "THE HOME DEPOT")
        self.assertEqual(r.vendor.confidence, LOW)   # a new vendor is always checked by a person
        self.assertEqual(r.needs_review(), ["vendor"])
        self.assertEqual(fields(HARDWARE, known_vendors=["The Home Depot"]).vendor.confidence, HIGH)
        self.assertEqual(r.date.value, date(2026, 10, 24))   # not the 01/22/2027 return-by date
        self.assertEqual(r.total.value, D("84.17"))
        self.assertEqual(r.total.confidence, HIGH)
        self.assertEqual(r.tax.value, D("6.30"))             # not the 8.10% rate
        self.assertEqual(r.subtotal.value, D("77.87"))
        self.assertEqual(r.payment_method.value, "Card")
        self.assertEqual(len(r.items), 5)
        self.assertEqual(r.items[2].amount, D("34.97"))
        self.assertIn("DECK SCREWS", r.items[2].description)
        self.assertEqual(r.items_confidence, HIGH)
        self.assertEqual(fields(HARDWARE, known_vendors=["The Home Depot"]).needs_review(), [])

    def test_restaurant_with_tip(self) -> None:
        r = fields(RESTAURANT)
        self.assertEqual(r.vendor.value, "Joe's Diner")
        self.assertEqual(r.date.value, date(2026, 10, 23))
        self.assertEqual(r.total.value, D("49.68"))   # the total with the tip, not 41.68 or a suggested tip
        self.assertEqual(r.total.confidence, HIGH)
        self.assertEqual(r.tip.value, D("8.00"))
        self.assertEqual(r.tax.value, D("3.18"))
        self.assertEqual(r.payment_method.value, "Card")
        self.assertEqual(r.payment_method.note, "Mastercard")
        self.assertEqual(len(r.items), 4)
        self.assertEqual(r.items_confidence, HIGH)

    def test_gas_station(self) -> None:
        r = fields(GAS)
        self.assertEqual(r.vendor.value, "SHELL")
        self.assertEqual(r.date.value, date(2026, 10, 20))
        self.assertEqual(r.total.value, D("36.40"))
        self.assertEqual(r.total.confidence, HIGH)
        self.assertIsNone(r.tax.value)
        self.assertEqual(r.payment_method.value, "Card")

    def test_gas_station_without_total_line(self) -> None:
        r = fields(GAS.replace("TOTAL                    $36.40\n", ""))
        self.assertEqual(r.total.value, D("36.40"))   # from FUEL SALE

    def test_card_slip(self) -> None:
        r = fields(CARD_SLIP)
        self.assertEqual(r.vendor.value, "ACME LANDSCAPE SUPPLY")   # not MERCHANT COPY
        self.assertEqual(r.date.value, date(2026, 10, 19))
        self.assertEqual(r.total.value, D("1245.00"))
        self.assertEqual(r.payment_method.value, "Card")
        self.assertEqual(r.items, [])

    def test_messy_ocr(self) -> None:
        r = fields(MESSY)
        self.assertEqual(r.vendor.value, "H0ME DEP0T")
        self.assertEqual(r.date.value, date(2026, 10, 24))
        self.assertEqual(r.date.confidence, LOW)    # needed O->0 fixes
        self.assertEqual(r.total.value, D("69.10"))
        self.assertEqual(r.total.confidence, HIGH)  # T0TAL / 69.1O needed fixing, but 63.92 + 5.18 = 69.10
        self.assertEqual(r.subtotal.value, D("63.92"))
        self.assertEqual(r.tax.value, D("5.18"))
        self.assertEqual(r.payment_method.value, "Cash")
        self.assertEqual([i.amount for i in r.items], [D("24.97"), D("34.97"), D("3.98")])
        self.assertIn("date", r.needs_review())

    def test_refund_is_negative(self) -> None:
        r = fields(REFUND)
        self.assertEqual(r.total.value, D("-84.17"))
        self.assertEqual(r.items[0].amount, D("-84.17"))

    def test_total_is_not_subtotal_tax_change_or_cash(self) -> None:
        r = fields("""
CORNER STORE
10/01/26
SODA 1.99
SUBTOTAL 99.99
TAX 7.00
CASH TENDERED 200.00
CHANGE DUE 93.01
TOTAL 106.99
""")
        self.assertEqual(r.total.value, D("106.99"))
        self.assertEqual(r.payment_method.value, "Cash")

    def test_amount_due_and_balance_due(self) -> None:
        r = fields("PLUMBING PLUS\n10/02/2026\nLABOR 150.00\nPARTS 52.10\nAMOUNT DUE 202.10\n")
        self.assertEqual(r.total.value, D("202.10"))
        r = fields("PLUMBING PLUS\n10/02/2026\nINVOICE TOTAL 300.00\nDEPOSIT -100.00\nBALANCE DUE 200.00\n")
        self.assertEqual(r.total.value, D("200.00"))   # what is owed beats the larger invoice total

    def test_amount_on_the_line_below_the_label(self) -> None:
        r = fields("OFFICE DEPOT\n10/03/26\nPAPER 9.99\nTOTAL\n$10.79\n")
        self.assertEqual(r.total.value, D("10.79"))
        self.assertEqual(r.total.source, "$10.79")

    def test_several_tax_lines_are_added(self) -> None:
        r = fields("FARM SUPPLY\n10/05/26\nFEED 40.00\nSUBTOTAL 40.00\nSTATE TAX 2.50\nCOUNTY TAX 0.40\nTOTAL 42.90\n")
        self.assertEqual(r.tax.value, D("2.90"))
        self.assertEqual(r.total.confidence, HIGH)

    def test_total_tax_line_is_not_double_counted(self) -> None:
        r = fields("SHOP\n10/05/26\nX 10.00\nSUBTOTAL 10.00\nGST 0.50\nPST 0.70\nTOTAL TAX 1.20\nTOTAL 11.20\n")
        self.assertEqual(r.tax.value, D("1.20"))
        self.assertEqual(r.total.value, D("11.20"))

    def test_taxable_amount_is_not_tax(self) -> None:
        r = fields("SHOP\n10/05/26\nTAXABLE AMOUNT 20.00\nTAX 1.25\nTOTAL 21.25\n")
        self.assertEqual(r.tax.value, D("1.25"))
        self.assertEqual(r.total.value, D("21.25"))

    def test_thousands_and_missing_dollar_sign(self) -> None:
        r = fields("EQUIPMENT RENTAL CO\n09/30/26\nSKID STEER 1 WEEK 1,150.00\nSUBTOTAL 1,150.00\nTAX 94.88\n"
                   "TOTAL 1,244.88\n")
        self.assertEqual(r.total.value, D("1244.88"))
        self.assertEqual(r.total.confidence, HIGH)

    def test_mismatched_totals_are_flagged(self) -> None:
        r = fields("SHOP\n10/05/26\nSUBTOTAL 10.00\nTAX 0.80\nTOTAL 18.80\n")
        self.assertEqual(r.total.value, D("18.80"))
        self.assertEqual(r.total.confidence, LOW)
        self.assertTrue(r.warnings)

    def test_no_total_label_uses_subtotal_plus_tax_or_largest_amount(self) -> None:
        r = fields("SHOP\n10/05/26\nSUBTOTAL 10.00\nTAX 0.80\n")
        self.assertEqual(r.total.value, D("10.80"))
        self.assertEqual(r.total.confidence, LOW)
        r = fields("SHOP\n10/05/26\nWIDGET 3.00\nGADGET 7.50\n")
        self.assertEqual(r.total.value, D("7.50"))
        self.assertEqual(r.total.confidence, LOW)

    def test_nothing_found(self) -> None:
        r = fields("")
        self.assertIsNone(r.vendor.value)
        self.assertIsNone(r.date.value)
        self.assertIsNone(r.total.value)
        self.assertEqual(r.needs_review(), ["vendor", "date", "total"])

    def test_accepts_one_string(self) -> None:
        r = extract_fields("CAFE\n10/01/26\nTOTAL 4.50", today=TODAY)
        self.assertEqual(r.total.value, D("4.50"))


class DateTests(unittest.TestCase):
    def check(self, line: str, expected: date | None) -> None:
        r = fields("SHOP\n" + line + "\nTOTAL 1.00")
        self.assertEqual(r.date.value, expected, line)

    def test_us_formats(self) -> None:
        cases = {
            "10/24/26": date(2026, 10, 24), "10/24/2026": date(2026, 10, 24), "1/5/26 3:04 PM": date(2026, 1, 5),
            "10-24-2026": date(2026, 10, 24), "2026-10-24": date(2026, 10, 24), "2026/10/24": date(2026, 10, 24),
            "Oct 24, 2026": date(2026, 10, 24), "OCTOBER 24 2026": date(2026, 10, 24),
            "24 Oct 2026": date(2026, 10, 24), "24-OCT-26": date(2026, 10, 24), "Sept 3, 2026": date(2026, 9, 3),
            "DATE: 09/03/26 TIME 12:01": date(2026, 9, 3),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.check(line, expected)

    def test_implausible_dates_are_skipped(self) -> None:
        for line in ("13/45/26", "02/30/26", "10/24/31", "12/28"):   # bad day, future, card expiry
            with self.subTest(line=line):
                self.check(line, None)

    def test_first_date_wins_and_return_by_dates_are_ignored(self) -> None:
        r = fields("SHOP\n10/20/26\nTOTAL 1.00\nRETURN BY 11/19/26\n")
        self.assertEqual(r.date.value, date(2026, 10, 20))
        self.assertEqual(r.date.confidence, HIGH)
        r = fields("SHOP\n10/20/26\nTOTAL 1.00\nORDER PLACED 10/18/26\n")
        self.assertEqual(r.date.value, date(2026, 10, 20))
        self.assertEqual(r.date.confidence, LOW)   # two different dates: ask

    def test_day_first_is_low_confidence(self) -> None:
        r = fields("SHOP\n24/10/2026\nTOTAL 1.00")
        self.assertEqual(r.date.value, date(2026, 10, 24))
        self.assertEqual(r.date.confidence, LOW)


class VendorTests(unittest.TestCase):
    def test_skips_noise_at_the_top(self) -> None:
        r = fields("""
*** CUSTOMER COPY ***
WELCOME TO
(555) 123-4567
www.example.com
Store #0042
1234 Main Street
CASEY'S GENERAL STORE
10/01/26
TOTAL 3.00
""")
        self.assertEqual(r.vendor.value, "CASEY'S GENERAL STORE")
        self.assertEqual(r.vendor.confidence, LOW)   # not one of the first lines

    def test_welcome_prefix(self) -> None:
        self.assertEqual(fields("Welcome to Walmart\n10/01/26\nTOTAL 3.00").vendor.value, "Walmart")

    def test_known_vendor_list(self) -> None:
        r = fields("THE H0ME DEPOT #0423\n10/01/26\nTOTAL 3.00", known_vendors=["Lowe's", "The Home Depot"])
        self.assertEqual(r.vendor.value, "The Home Depot")
        self.assertEqual(r.vendor.confidence, HIGH)


class StructureTests(unittest.TestCase):
    def test_items_unpack_and_carry_their_source(self) -> None:
        r = fields(HARDWARE)
        description, amount = r.items[0]
        self.assertIn("STUD", description)
        self.assertEqual(amount, D("3.98"))
        self.assertTrue(r.items[0].source.endswith("3.98"))
        self.assertEqual(r.lines[r.items[0].line_index], r.items[0].source)

    def test_confidence_map(self) -> None:
        conf = fields(HARDWARE).confidence
        self.assertEqual(set(conf), {"vendor", "date", "total", "subtotal", "tax", "tip", "payment_method", "items"})
        self.assertEqual(conf["total"], HIGH)

    def test_bad_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ro.ReceiptReadError):
                ro._load_image(Path(tmp) / "missing.png")
            junk = Path(tmp) / "junk.jpg"
            junk.write_bytes(b"not an image")
            with self.assertRaises(ro.ReceiptReadError):
                ro._load_image(junk)

    def test_rows_regroup_a_price_column(self) -> None:
        W = ro.OcrWord
        raw = [[W("HAMMER", 10, 100, 60, 14)], [W("SUBTOTAL", 10, 140, 80, 14)],
               [W("12.99", 400, 101, 50, 14)], [W("12.99", 400, 141, 50, 14)]]
        self.assertEqual([ln.text for ln in ro._rows(raw)], ["HAMMER 12.99", "SUBTOTAL 12.99"])


# ----------------------------------------------------------------------------------------------------------------
# Synthetic receipt images (also used by the accuracy measurement in docs/ocr-findings.md)
# ----------------------------------------------------------------------------------------------------------------

SYNTHETIC = [
    {"name": "hardware", "vendor": "THE HOME DEPOT", "date": date(2026, 10, 24), "total": D("84.17"),
     "tax": D("6.30"), "subtotal": D("77.87"),
     "lines": [("c", "THE HOME DEPOT"), ("c", "4410 W LAKE AVE"), ("c", "PEORIA, IL 61615"),
               ("l", "10/24/26  09:12 AM"), ("r", "2X4X8 STUD", "3.98"), ("r", "DECK SCREWS 5LB", "34.97"),
               ("r", "HAMMER 16OZ", "24.97"), ("r", "WORK GLOVES", "13.95"), ("r", "SUBTOTAL", "77.87"),
               ("r", "SALES TAX", "6.30"), ("r", "TOTAL", "$84.17"), ("l", "VISA ************1234")]},
    {"name": "grocery", "vendor": "FRESH MARKET", "date": date(2026, 10, 22), "total": D("17.28"),
     "tax": D("0.83"), "subtotal": D("16.45"),
     "lines": [("c", "FRESH MARKET"), ("c", "(217) 555-0143"), ("r", "BANANAS", "1.18"), ("r", "MILK GAL", "3.49"),
               ("r", "BREAD WHEAT", "2.79"), ("r", "PAPER TOWELS", "8.99"), ("r", "SUBTOTAL", "16.45"),
               ("r", "TAX", "0.83"), ("r", "TOTAL", "17.28"), ("r", "CASH", "20.00"), ("r", "CHANGE", "2.72"),
               ("l", "10/22/2026 4:51 PM")]},
    {"name": "restaurant", "vendor": "BLUE PLATE CAFE", "date": date(2026, 10, 23), "total": D("49.68"),
     "tax": D("3.18"), "subtotal": D("38.50"),
     "lines": [("c", "BLUE PLATE CAFE"), ("c", "22 ELM STREET"), ("l", "OCT 23, 2026  7:42 PM"),
               ("r", "2 CHEESEBURGER", "25.00"), ("r", "1 FRIES", "4.50"), ("r", "2 ICED TEA", "6.00"),
               ("r", "1 APPLE PIE", "3.00"), ("r", "SUBTOTAL", "38.50"), ("r", "TAX", "3.18"),
               ("r", "TOTAL", "41.68"), ("r", "TIP", "8.00"), ("r", "TOTAL", "49.68")]},
    {"name": "gas", "vendor": "SHELL", "date": date(2026, 10, 20), "total": D("36.40"), "tax": None,
     "subtotal": None,
     "lines": [("c", "SHELL"), ("c", "1200 HWY 51 S"), ("l", "2026-10-20 06:58"), ("l", "PUMP 05 REGULAR"),
               ("l", "10.523 G @ 3.459/G"), ("r", "FUEL SALE", "$36.40"), ("r", "TOTAL", "$36.40"),
               ("l", "DEBIT ************8890")]},
    {"name": "supply", "vendor": "ACME LANDSCAPE SUPPLY", "date": date(2026, 10, 19), "total": D("1245.00"),
     "tax": D("92.25"), "subtotal": D("1152.75"),
     "lines": [("c", "ACME LANDSCAPE SUPPLY"), ("c", "88 INDUSTRIAL PKWY"), ("l", "DATE: 10/19/26  14:03"),
               ("r", "MULCH 20 YD", "720.00"), ("r", "EDGING 100 FT", "432.75"), ("r", "SUBTOTAL", "1,152.75"),
               ("r", "TAX", "92.25"), ("r", "AMOUNT DUE", "$1,245.00")]},
]

_FONTS = {"mono": ["consola.ttf", "lucon.ttf", "cour.ttf", "DejaVuSansMono.ttf"],
          "sans": ["arial.ttf", "DejaVuSans.ttf"], "bold": ["consolab.ttf", "courbd.ttf", "arialbd.ttf"]}


def _font(kind: str, size: int):
    from PIL import ImageFont

    for name in _FONTS[kind]:
        for folder in (os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"), ""):
            try:
                return ImageFont.truetype(os.path.join(folder, name) if folder else name, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def render_receipt(spec: dict, variant: str = "clean", font: str = "mono", seed: int = 0):
    """A receipt image like a scanned thermal print: 80 mm paper (576 px at 203 dpi), centred header,
    prices right-aligned in their own column. ``variant``: clean, small, rotated, faint, photo."""
    from PIL import Image, ImageDraw, ImageFilter

    size = {"small": 13}.get(variant, 22)
    f = _font(font, size)
    width = 576 if variant != "small" else 360
    margin = 24
    step = int(size * 1.55)
    height = margin * 2 + step * len(spec["lines"]) + step
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(spec["lines"]):
        y = margin + i * step
        if line[0] == "c":
            w = draw.textlength(line[1], font=f)
            draw.text(((width - w) / 2, y), line[1], font=f, fill=0)
        elif line[0] == "l":
            draw.text((margin, y), line[1], font=f, fill=0)
        else:
            draw.text((margin, y), line[1], font=f, fill=0)
            w = draw.textlength(line[2], font=f)
            draw.text((width - margin - w, y), line[2], font=f, fill=0)
    rnd = random.Random(seed)
    if variant == "rotated":
        img = img.rotate(rnd.choice((-1, 1)) * 2.5, resample=Image.BICUBIC, expand=True, fillcolor=255)
    elif variant == "faint":   # faded thermal paper: grey print on a grey, speckled background
        img = img.point(lambda p: 205 - int((255 - p) * 0.40))
        noise = Image.effect_noise(img.size, 22)
        img = Image.blend(img, noise, 0.18).filter(ImageFilter.GaussianBlur(0.6))
    elif variant == "photo":   # phone photo: bigger, slightly blurred, uneven light, a small tilt
        img = img.resize((img.width * 2, img.height * 2), Image.BICUBIC).rotate(
            1.2, resample=Image.BICUBIC, expand=True, fillcolor=250)
        gradient = Image.linear_gradient("L").resize(img.size).point(lambda p: int(p * 0.25))
        img = Image.composite(img, Image.new("L", img.size, 180), Image.eval(gradient, lambda p: 255 - p))
        img = img.filter(ImageFilter.GaussianBlur(1.2))
    return img


def _ocr_ready() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return ro.ocr_available("en-US")


@unittest.skipUnless(_ocr_ready(), "Windows OCR (Windows.Media.Ocr, en-US) is not available")
class WindowsOcrTests(unittest.TestCase):
    """End to end: generated receipt image -> Windows OCR -> fields."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def read(self, spec: dict, variant: str, fmt: str = "png", font: str = "mono") -> ro.ReceiptFields:
        img = render_receipt(spec, variant, font)
        path = self.tmp / f"{spec['name']}-{variant}-{font}.{fmt}"
        img.save(path, {"jpg": "JPEG", "tif": "TIFF", "pdf": "PDF"}.get(fmt, fmt.upper()),
                 **({"resolution": 203} if fmt == "pdf" else {}))
        return ro.read_receipt(path)

    def check(self, spec: dict, variant: str, **kw) -> None:
        r = self.read(spec, variant, **kw)
        msg = f"{spec['name']}/{variant}: {r.lines}"
        self.assertEqual(r.total.value, spec["total"], msg)
        self.assertEqual(r.date.value, spec["date"], msg)

    def test_clean(self) -> None:
        for spec in SYNTHETIC:
            with self.subTest(spec["name"]):
                self.check(spec, "clean")
                r = self.read(spec, "clean")
                self.assertEqual(r.vendor.value, spec["vendor"])
                self.assertEqual(r.tax.value, spec["tax"])
                self.assertEqual(r.subtotal.value, spec["subtotal"])

    def test_small_font(self) -> None:
        for spec in SYNTHETIC:
            with self.subTest(spec["name"]):
                self.check(spec, "small")

    def test_rotated(self) -> None:
        for spec in SYNTHETIC:
            with self.subTest(spec["name"]):
                self.check(spec, "rotated")

    def test_faint_and_noisy(self) -> None:
        for spec in SYNTHETIC:
            with self.subTest(spec["name"]):
                self.check(spec, "faint")

    def test_proportional_font_photo(self) -> None:
        for spec in SYNTHETIC:
            with self.subTest(spec["name"]):
                self.check(spec, "photo", font="sans")

    def test_file_formats(self) -> None:
        spec = SYNTHETIC[0]
        for fmt in ("jpg", "bmp", "tif"):
            with self.subTest(fmt):
                self.check(spec, "clean", fmt=fmt)

    @unittest.skipUnless(__import__("importlib").util.find_spec("pypdfium2"), "pypdfium2 not installed")
    def test_pdf_first_page(self) -> None:
        self.check(SYNTHETIC[1], "clean", fmt="pdf")

    def test_result_has_boxes(self) -> None:
        path = self.tmp / "boxes.png"
        render_receipt(SYNTHETIC[0], "clean").save(path)
        result = ro.ocr_image(path)
        self.assertIn(result.engine, ("winrt", "powershell"))
        self.assertTrue(result.lines)
        line = next(ln for ln in result.lines if "TOTAL" in ln.text and "SUB" not in ln.text)
        x, y, w, h = line.box
        self.assertGreater(w, 100)   # the label and the right-hand price were joined into one row
        self.assertTrue(all(word.w > 0 and word.h > 0 for word in line.words))


if __name__ == "__main__":
    unittest.main()
