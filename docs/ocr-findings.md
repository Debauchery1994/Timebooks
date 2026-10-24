# Offline receipt reading: findings (Phase 3 prototype)

_Measured 2026-10-24 on Windows 11 Pro (build 26200), Python 3.15.0 64-bit, Windows OCR language en-US._
Code: `timecard/receipts_ocr.py`. Tests: `tests/test_receipts_ocr.py`.

## Route chosen

**Windows' built-in OCR (`Windows.Media.Ocr`) through the PyWinRT packages, in process**, with **Windows
PowerShell 5.1 as an automatic fallback** when the packages aren't installed (e.g. the current dev venv).
Both are fully offline. `TIMECARD_OCR_BACKEND=winrt|powershell` forces one.

| | PyWinRT (`winrt-*`) | PowerShell 5.1 + embedded script |
|---|---|---|
| Python 3.15 win_amd64 wheels | yes: `winrt-runtime 4.0.0` is `cp315`; the namespace packages are pure `py3-none-any` | n/a |
| Time per OCR pass | 10-60 ms | ~0.3 s, mostly PowerShell start-up |
| Time per receipt (all passes) | median 50 ms, max ~100 ms | median 0.73 s, max 1.2 s |
| Extra dependencies | 7 small packages, 0.9 MB in the frozen app | none |
| PyInstaller | worked frozen with **no hooks or options** (tested one-dir build, PyInstaller 6.22.3) | worked frozen too |
| Risks | a WinRT projection version bump | blocked by AppLocker / Constrained Language Mode on locked-down PCs; AV heuristics dislike hidden PowerShell started by an unsigned exe |

The old `winsdk` package has no 3.15 wheel (abandoned), so it's out.

Pins to add to `requirements.txt` (Windows only; all verified to install and run under 3.15):

```
winrt-runtime==4.0.0
winrt-Windows.Foundation==4!10.0.28000.2705
winrt-Windows.Foundation.Collections==4!10.0.28000.2705
winrt-Windows.Globalization==4!10.0.28000.2705
winrt-Windows.Graphics.Imaging==4!10.0.28000.2705
winrt-Windows.Media.Ocr==4!10.0.28000.2705
winrt-Windows.Storage.Streams==4!10.0.28000.2705
pypdfium2==5.14.0          # PDF receipts only; adds ~7 MB to the frozen app
```

(Verified in a fresh venv with exactly these; `Foundation.Collections` arrives as a dependency of the OCR package
anyway, pinned here so the set is reproducible. Pillow 13.0.0 is already required. Use a
`; sys_platform == "win32"` marker if the requirements must install elsewhere.)

**Formats:** PNG, JPG, BMP, TIFF (first page), GIF, WebP via Pillow (with EXIF rotation for phone photos).
**PDF:** first page rendered at 300 dpi with `pypdfium2` (lazy import; a clear error if it's missing). Tested,
including frozen. Text-based PDFs (emailed receipts) could skip OCR entirely by reading the PDF's own text with
pypdfium2's `get_textpage()`; not done yet, worth adding in Phase 3.

## How it reads a receipt

1. Pillow: greyscale, auto-contrast, cap at Windows' 10,000 px limit.
2. OCR pass 1. If Windows reports the text tilted ≥0.4°, rotate and re-read. If the letters are under ~22 px
   tall, enlarge to ~32 px (very small print is tried at two sizes in one batch) and keep the best-scoring pass.
3. **Regroup words into printed rows** using their boxes, levelled for tilt. Essential: Windows OCR returns a
   right-hand price column as separate "lines", in a different order (`HAMMER`, `SUBTOTAL`, …, `12.99`, `12.99`).
4. Field rules on the text lines (`extract_fields`, also usable on text from any source):
   total = largest amount on TOTAL / BALANCE / AMOUNT DUE-type lines, with "… DUE"/GRAND TOTAL beating plain TOTAL
   beating AMOUNT/SALE; never SUBTOTAL, TAX, CHANGE, CASH/TENDERED, suggested-tip (%) or savings lines; amount may
   be on the next line. Then cross-checked: subtotal + tax (+ tip) must equal it, and a card payment must match.
   OCR look-alikes are fixed in numbers and keywords (`T0TAL`, `69.1O`, `I.e4`, `12 .99`, `$1, 245 .øø`).

Every field carries `confidence` (high/low), the `source` line and its index, so the review screen can highlight it.

## Accuracy on synthetic receipts

135 generated receipts: 5 layouts (hardware, grocery w/ cash+change, restaurant w/ tip and two totals, gas
station, landscape supply with $1,245.00 AMOUNT DUE) × 3 fonts (Consolas-like mono, Arial, bold) × 9 conditions:
clean 203-dpi thermal print, small font, 2.5° tilt, faint+noisy, phone-photo style (blur, uneven light, 1.2° tilt),
6° tilt, JPEG quality 30, very faint (25 % contrast + heavy noise), low-res (≈100 dpi).

| Field | Correct (PyWinRT) | Marked **high** and wrong |
|---|---|---|
| Total | **134/135 (99 %)** | 0 |
| Date | 129/135 (96 %) | 0 |
| Subtotal | 134/135 (99 %) | 0 |
| Tax | 132/135 (98 %) | 0 |
| Vendor (exact spelling) | 123/135 (91 %) | 0 (vendor is only "high" when it matches the Vendors list) |
| Total **and** date | **128/135 (95 %)** | |

PowerShell route: identical numbers (same engine; only the transport differs), but median 0.73 s and max 1.2 s per receipt.

All misses are in the "very faint" and "low-res" conditions; clean, small, tilted (2.5° and 6°), faint, JPEG-30 and
photo-style receipts were 100 % on total and date. The confidence flags were well calibrated: **no field was ever
marked high while wrong**, so "only review what's highlighted" is safe on this set. (The "very faint" noise is not
seeded, so those numbers move by ±1 between runs.)

## Failure modes seen

- **Low resolution (≈100 dpi) and very faint print:** digits read as letters (`$36. u`, `ae,t24/26`), whole
  words dropped (`THE DEPOT`), the date lost. Enlarging helps only partly. Fix on the input side: scan at 300 dpi.
- **Store names misspelled** (`FRESH hARKET`, `THE I-ME DEPOT`): the rules can't know; matching against the Vendors
  list (`known_vendors=`, fuzzy) fixes repeat vendors, so "learns from corrections" matters most for this field.
- **Thin fonts** (Courier) occasionally lose a whole price column at some sizes; the multi-size pass recovers most.
- **Image-only PDF re-rendered at the wrong scale** misread a tax digit once (`1.64` for `1.04`); the
  subtotal + tax ≠ total check flagged it low.
- Items are "lines ending in a price above the totals" — fine for listing, but SKUs, quantities and discounts stay
  in the description text; descriptions on wrapped lines are joined to the price line only for `2 @ 0.59` style.

## What real receipts will do differently

Synthetic receipts are kinder than real ones: perfectly flat paper, a real TrueType font, black-on-white. Expect
on real ones: faded thermal paper (the main risk; our "very faint" case is where misses started), crumples and
curl in phone photos, shadows, logos instead of a printed store name (the vendor then comes from the address line
or is missing), two-column layouts, handwritten tips/totals on restaurant slips (OCR won't read handwriting), and
long receipts photographed in two halves. Card slips and invoices with "AMOUNT DUE" should do well.

## Recommendation for decision #4 (optional online AI reading)

**Ship offline-only first; don't build the AI option yet.** Windows OCR is fast (≈50 ms), free, private, adds
<1 MB, and on everything short of badly faded or low-resolution images it got total and date right, with honest
confidence flags. Run the playbook's real test (20 real receipts, ≥80 % total+date without edits). Add the optional
AI reader later **only if** that test fails mainly on faded thermal receipts or phone photos — those are the cases
where a vision model clearly beats OCR. `extract_fields` takes plain text, so an AI route could slot in behind the
same review screen without changing anything else. Before that, the cheaper wins are: tell the owner to scan at
300 dpi, read text PDFs directly, and fill the Vendors list from corrections.
