"""Bank statements in: read a CSV from the bank, or a statement PDF / scan, into lines; match them to expenses.

Nothing here changes the workbook: the Expenses tab shows the lines and matches for the owner to check, and only
what the owner picks becomes Draft expenses. Money in (deposits) is kept for the Sales tab (1.5.0).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import Expense

CENT = Decimal("0.01")
FIELDS = ("date", "description", "amount", "debit", "credit")
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y")
_MONTH_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})$")   # statements often print 10/03 with the year in the heading
_AMOUNT = re.compile(r"^\(?-?\$?\s*-?[\d,]*\d\.\d{2}\)?-?$")
_MONEY_IN_WORDS = re.compile(
    r"\b(DEPOSIT|CREDIT|REFUND|INTEREST|TRANSFER FROM|PAYMENT RECEIVED|ZELLE FROM|DIRECT DEP)\b", re.I)
_BALANCE_ROW = re.compile(r"\b(BEGINNING|ENDING|OPENING|CLOSING|PREVIOUS|NEW|STARTING)\s+BALANCE\b", re.I)


@dataclass
class BankLine:
    line_date: date
    description: str
    amount: Decimal          # negative = money out (a purchase, a check), positive = money in (a deposit)
    source: str = ""         # the statement text it came from, for checking


@dataclass
class Match:
    line: BankLine
    status: str              # "matched" | "possible" | "new" (money out with no expense) | "money in"
    expense: Expense | None = None


def parse_amount(text: str) -> Decimal | None:
    """'$1,234.56', '-12.00', '(12.00)', '12.00-' -> Decimal (negative for the last three); None if not money."""
    t = (text or "").strip().replace(" ", "")
    if not t:
        return None
    neg = t.startswith("-") or t.endswith("-") or (t.startswith("(") and t.endswith(")"))
    digits = t.strip("()-").replace("$", "").replace(",", "").lstrip("-")
    try:
        value = Decimal(digits).quantize(CENT)
    except InvalidOperation:
        return None
    return -value if neg else value


def parse_date(text: str, year: int | None = None) -> date | None:
    t = (text or "").strip()
    if m := _MONTH_DAY.match(t):
        try:
            return date(year or date.today().year, int(m[1]), int(m[2]))
        except ValueError:
            return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


# ---- CSV

def read_csv(path: str | Path) -> tuple[list[str], list[list[str]]]:
    """(headers, rows) from a bank CSV (any common encoding; blank rows dropped)."""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = [r for r in csv.reader(text.splitlines()) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return [h.strip() for h in rows[0]], rows[1:]


def guess_columns(headers: list[str]) -> dict[str, int]:
    """Which column is the date, description and amount (or debit and credit), from the header names."""
    out: dict[str, int] = {}
    for i, h in enumerate(h.lower() for h in headers):
        if "date" in h and "date" not in out:
            out["date"] = i
        elif any(w in h for w in ("description", "payee", "memo", "details", "name", "transaction")) \
                and "description" not in out and "type" not in h:
            out["description"] = i
        elif any(w in h for w in ("debit", "withdrawal", "money out", "paid out")) and "debit" not in out:
            out["debit"] = i
        elif any(w in h for w in ("credit", "deposit", "money in", "paid in")) and "credit" not in out:
            out["credit"] = i
        elif "amount" in h and "amount" not in out:
            out["amount"] = i
    return out


def lines_from_csv(rows: list[list[str]], cols: dict[str, int]) -> list[BankLine]:
    """Bank lines from CSV rows with the chosen columns; rows without a readable date and amount are skipped.
    Separate debit/credit columns: debits are money out. A single amount column keeps its sign."""
    out = []
    for r in rows:
        def cell(name: str) -> str:
            i = cols.get(name)
            return r[i] if i is not None and i < len(r) else ""
        d = parse_date(cell("date"))
        if d is None:
            continue
        if "amount" in cols:
            amt = parse_amount(cell("amount"))
        else:
            debit, credit = parse_amount(cell("debit")), parse_amount(cell("credit"))
            amt = -abs(debit) if debit else (abs(credit) if credit else None)
        if amt is None or amt == 0:
            continue
        out.append(BankLine(d, cell("description").strip(), amt, ",".join(r)))
    return out


def write_csv(lines: list[BankLine], path: str | Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Description", "Amount"])
        for ln in lines:
            w.writerow([ln.line_date.isoformat(), ln.description, f"{ln.amount:.2f}"])


# ---- statement text (PDF or scan)

def lines_from_text(text_lines: list[str], year: int | None = None) -> list[BankLine]:
    """Statement rows: a line that starts with a date and ends with one or two amounts (amount, then balance).
    Money in or out comes from the running balance when the statement prints one, else from words like DEPOSIT."""
    out: list[BankLine] = []
    prev_balance: Decimal | None = None
    for raw in text_lines:
        parts = raw.split()
        if len(parts) < 3:
            continue
        d = parse_date(parts[0], year)
        if d is None:
            continue
        amounts: list[Decimal] = []
        while parts and _AMOUNT.match(parts[-1]) and len(amounts) < 2:
            amounts.insert(0, parse_amount(parts.pop()))
        if not amounts or len(parts) < 2:
            continue
        desc = " ".join(parts[1:])
        if _BALANCE_ROW.search(desc):   # "BEGINNING BALANCE 1,000.00": not a transaction, but starts the balance
            prev_balance = amounts[-1]
            continue
        amount = abs(amounts[0])
        balance = amounts[1] if len(amounts) == 2 else None
        if balance is not None and prev_balance is not None and abs(abs(balance - prev_balance) - amount) < CENT:
            signed = amount if balance > prev_balance else -amount
        elif amounts[0] < 0:
            signed = amounts[0]
        else:
            signed = amount if _MONEY_IN_WORDS.search(desc) else -amount
        if balance is not None:
            prev_balance = balance
        out.append(BankLine(d, desc, signed, raw.strip()))
    return out


def statement_text(path: str | Path) -> list[str]:
    """Text lines of a statement: a PDF's own text when it has some (emailed / downloaded statements), else every page
    read with the receipt reader (scanned statements and photos)."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(p))
        try:
            text = "\n".join(page.get_textpage().get_text_range() for page in pdf)
            if len(text.strip()) > 40:
                return [ln for ln in text.splitlines() if ln.strip()]
            from tempfile import TemporaryDirectory
            from . import receipts_ocr
            out: list[str] = []
            with TemporaryDirectory() as d:
                for i, page in enumerate(pdf):
                    img = Path(d) / f"page{i + 1}.png"
                    page.render(scale=300 / 72).to_pil().save(img)
                    out += receipts_ocr.ocr_image(img).lines
            return out
        finally:
            pdf.close()
    from . import receipts_ocr
    return receipts_ocr.ocr_image(p).lines


# ---- matching

def match_lines(lines: list[BankLine], expenses: list[Expense], days: int = 3, maybe_days: int = 10) -> list[Match]:
    """Money out matched to an expense of the same amount within `days` (each expense used once); within `maybe_days`
    it's "possible"; otherwise "new". Money in is kept apart."""
    used: set[str] = set()
    out = []
    for ln in lines:
        if ln.amount > 0:
            out.append(Match(ln, "money in"))
            continue
        same = [e for e in expenses if e.expense_id not in used and e.amount == -ln.amount]
        best = min(same, key=lambda e: abs((e.expense_date - ln.line_date).days), default=None)
        gap = abs((best.expense_date - ln.line_date).days) if best else None
        if best is not None and gap <= days:
            used.add(best.expense_id)
            out.append(Match(ln, "matched", best))
        elif best is not None and gap <= maybe_days:
            used.add(best.expense_id)
            out.append(Match(ln, "possible", best))
        else:
            out.append(Match(ln, "new"))
    return out


def vendor_from(description: str) -> str:
    """A tidy vendor name from a bank description: 'POS PURCHASE HOME DEPOT #4521 ATLANTA GA' -> 'HOME DEPOT'."""
    d = re.sub(r"^(POS|DEBIT CARD|CHECKCARD|CARD|ACH|PURCHASE|RECURRING|ONLINE)(\s+(PURCHASE|DEBIT|PAYMENT))*\s+", "",
               description.strip(), flags=re.I)
    d = re.split(r"\s+#|\s+\d{3,}|\s{2,}", d)[0]
    return d.strip(" -*")[:40] or description.strip()[:40]
