# Timebooks — Next Steps Playbook

The working plan for turning TimeCard Pro from a timecard and payroll-check app into a small-business bookkeeping
desktop app. Each phase ships as its own release. Check items off as they land, and add new ideas to the
**Parking lot** at the bottom rather than letting them grow the current phase.

_Last updated: 2026-10-24 · current version: 1.1.2 (not yet released)_

---

## 1. Where we are

Shipped (1.1.0–1.1.1): time cards with lunch deduct, year log, payroll for contractors (hourly or salary), voucher /
3-up / handwrite checks, annual statements for the tax professional, PIN on Employees, portable USB copy, Google Drive
style backups, themes and text size, company branding, Check for Updates from GitHub Releases, and a workbook created
by Setup in `Documents\TimeCard Pro`.

Committed for 1.1.2, not pushed yet:
- Salary per year **or** per pay period
- Time card hours save automatically; Pay & Checks opens on the right period and says who it skipped
- Company Settings → Pay timing (hold one pay period back)
- Company Settings behind the PIN
- Year Log by pay period × employee, with that period's notes

Carried over and still open:
- [ ] Push 1.1.2 and publish the release (then test Check for Updates from 1.1.1 → 1.1.2: the first real update)
- [ ] Printer test on the real QuickBooks voucher checks (Pay & Checks → Alignment Test on plain paper first)

## 2. Where we're going

> **A clean, simple business app for a small business owner who isn't an accountant.**
> Track the people you pay, the money you spend, and the money you take in, and print every check you write,
> all in one workbook you own, on your own computer.

Guiding rules (these come from the decisions already made in the app):
1. **Your data stays yours.** One `.xlsx` workbook per business per year, readable in GridCraft/Excel. Nothing is sent
   anywhere unless the owner turns it on (and says yes each time it matters).
2. **Only show what the business uses.** Every major area can be turned off and its tab hidden.
3. **Sensitive things sit behind the PIN.** Pay rates, bank numbers, and anything a visitor at the desk shouldn't see.
4. **One checkbook.** Payroll checks and vendor checks share one check-number sequence and one register.
5. **Automate the boring part, confirm the important part.** Scanning fills in a receipt; a person approves it.
6. **Small, shippable steps.** Each phase below is a release that is useful on its own.

## 3. The plan at a glance

| Phase | Release | What the owner gets | Size |
|---|---|---|---|
| 0 | 1.1.2 | Ship what's committed; printer test | S |
| 1 | 1.2.0 | Features on/off (hide Time Cards for all-salary businesses); code split to make room | M |
| 2 | 1.3.0 | **Expenses & Receipts** tab, entered by hand with a receipt image attached | M |
| 3 | 1.4.0 | **Scan a receipt** (scanner, scanner folder, or photo) → auto-filled vendor, date, total, items | L |
| 4 | 1.5.0 | **Vendors & Pay Bills**: print checks to vendors on the same checkbook | M |
| 5 | 1.6.0 | **Sales** tab: record money coming in | M |
| 6 | 1.7.0 | **Reports**: Profit & Loss, spending by category, tax-pro export pack | M |
| 7 | 2.0.0 | Rename to Timebooks, first-run setup wizard, polish | M |

Phases 4 and 5 are independent and can swap order. Phase 3 needs Phase 2.

---

## Phase 0 — Ship 1.1.2 (now)

- [ ] Push the 4 local commits
- [ ] Build `TimeCardPro-Setup.exe` and publish release `1.1.2`
- [ ] From an installed 1.1.1: Help → Check for Updates → install → confirm the workbook, PIN and settings survived
- [ ] When a printer is available: alignment test, then one real voucher check

**Done when** the update path has been used for real once and a check printed lined up.

## Phase 1 — Features on/off, and room to grow (1.2.0)

**Owner sees:** Company Settings → **Features** with switches. Turning one off hides its tabs, menus, and printouts.
Nothing is deleted; turning it back on brings everything back.

| Feature switch | Tabs it controls | Default |
|---|---|---|
| Time cards | Time Card, Year Log | On |
| Payroll checks | Pay & Checks (Employees stays for salaried staff) | On |
| Expenses & receipts | Expenses (Phase 2) | Off until built |
| Vendor checks | Vendors / Pay Bills (Phase 4) | Off until built |
| Sales | Sales (Phase 5) | Off until built |

All-salary business: **Time cards off** → Pay & Checks pays salaried people every period with no hours needed (that
part already works), and the memo/stub leaves out hours.

**Engineering (do this first, before adding tabs):**
- [ ] Split `timecard/app.py` (≈4,000 lines) into `timecard/ui/` modules: `common.py` (theme, widgets, dialogs),
      `timecard_tab.py`, `employees_tab.py`, `yearlog_tab.py`, `payroll_tab.py`, `company_tab.py`, `app.py`. No behaviour
      change; the existing tests and screen smoke tests must pass unchanged.
- [ ] Workbook **schema version** (a `schema_version` row on the Company sheet) and a small migration step on load,
      so new sheets/columns are added cleanly and an older app warns before saving a newer workbook
      (this prevents the "1.1.1 drops the SalaryBasis column" problem from happening again).
- [ ] `Company.features` setting and a tab registry the notebook is built from.
- [ ] Setup: an optional "What does your business need?" step that sets the switches.

**Done when** an all-salary business can hide Time Card and Year Log and still run payroll, and the app reopens the
same way.

## Phase 2 — Expenses & Receipts, by hand (1.3.0)

**Owner sees:** an **Expenses** tab. Add an expense (date, vendor, amount, category, payment method, memo), attach a
receipt (image or PDF), view it, filter by month/category/vendor, and sort, the same way as the Year Log. Totals by
category at the bottom.

**Data (new sheets):**

`Expenses`: `ExpenseID | Date | Vendor | VendorID | Category | Amount | Tax | PaymentMethod | CheckNumber | Memo |
ReceiptFile | Status | Created`
- `Status`: `Draft` (scanned, not reviewed) / `Approved`
- `PaymentMethod`: Cash, Card, Check, Bank transfer, Other. `CheckNumber` links to a vendor check (Phase 4).
- `ReceiptFile` is a path **relative to the workbook's folder**, e.g. `Receipts\2026\2026-10-24 Home Depot 84.17.jpg`

`Categories`: the owner's list, seeded with IRS Schedule C lines so the tax professional can use it directly:
Advertising, Car & truck, Commissions & fees, Contract labor, Insurance, Legal & professional, Office expense, Rent
(equipment), Rent (property), Repairs & maintenance, Supplies, Taxes & licenses, Travel, Meals, Utilities, Other.

**Receipt files** live in a `Receipts\<year>\` folder next to the workbook (`Data\Receipts` on a portable copy), not
inside the `.xlsx`. Images would make the workbook huge and slow to save.
- [ ] Backups copy the receipts folder too (only new files each time)
- [ ] Moving or renaming a receipt is done through the app so links don't break

**Done when** a month of expenses can be entered with receipts attached, and the category totals match a hand count.

## Phase 3 — Scan a receipt and fill it in automatically (1.4.0)

**Owner sees:** **Expenses → Scan Receipt**. The receipt is scanned (or picked up from the scanner's folder, or
dropped in as a phone photo or PDF), and a review screen shows the image on the left and the filled-in fields on the
right: **vendor (who), date, total (how much), tax, and line items (what)**, plus a suggested category. Fix anything
that's wrong, then Approve.

**Three ways in, from most to least automatic:**
1. **Scanner (WIA):** Windows' built-in scanner interface works with most USB/network scanners and needs no extra
   drivers beyond the scanner's own. _Checked on this PC 2026-10-24: WIA is available; no scanner was connected._
2. **Scanner folder:** most scanners (Epson, Brother, Canon, ScanSnap) can save straight to a folder. The app watches a
   chosen folder and offers each new file as a Draft expense. This is the most reliable path across brands.
3. **Import a file:** a phone photo, an emailed PDF receipt, or drag and drop.

**Reading the receipt (offline first):**
- [ ] **Windows OCR** (`Windows.Media.Ocr`): built into Windows 10/11, free, offline, no install.
      _Checked on this PC: available, English (en-US)._
- [ ] Field extraction rules: total = the largest amount next to TOTAL / AMOUNT DUE / BALANCE; date = first date
      pattern; tax = TAX line; vendor = top lines matched against the Vendors list (learns from corrections); items =
      lines with a price.
- [ ] Remember corrections: the next receipt from the same vendor suggests the same category.
- [ ] Confidence: fields the app isn't sure of are highlighted for review; nothing is approved without a person.
- [ ] Optional, off by default: **AI receipt reading** for faded or messy receipts. It sends the image to an online
      service, so it needs the owner's own API key, a clear one-time warning, and a per-receipt indicator. Decide
      in Phase 3 whether it's worth it after seeing how well offline OCR does on real receipts.

**Done when** 20 real receipts from the business are scanned, at least 80% get the correct total and date without
edits, and every one can be fixed on the review screen.

## Phase 4 — Vendors and paying bills by check (1.5.0)

**Owner sees:** a **Vendors** list (name, address, phone, default category, "send a 1099"), and **Pay Bills**: pick a
vendor, amount, memo, and which expenses or bills it pays, then print. The check prints with the same layouts
and alignment as payroll checks.

- [ ] **One check-number sequence** shared with payroll (`next_check_number`) and one **Check Register** showing every
      check (payroll and vendor), with void and reprint
- [ ] `checks.py`: generalise the payee from Employee to "payee" (employee or vendor), with no payroll stub text
      on vendor checks; voucher stub shows the bills paid
- [ ] `Vendors` sheet: `VendorID | Name | Address1 | Address2 | Phone | Email | DefaultCategory | Is1099 | Notes | Active`
- [ ] `VendorChecks` (or a `Type` column on a shared `Checks` sheet; decide in Phase 1's schema work)
- [ ] Vendor 1099 totals in the annual export (threshold from Company Settings; confirm each year with the tax pro)
- [ ] No vendor tax IDs stored (same rule as contractors: collect W-9s on paper)

**Done when** a vendor check and a payroll check print on the same stock with consecutive numbers and both appear
in the register.

## Phase 5 — Sales / money in (1.6.0)

**Owner sees:** a **Sales** tab. Record a sale or deposit: date, customer, amount, sales tax collected, payment
method, category (service, product, other), invoice/reference #, memo; attach a document. Month and customer filters
and totals, same as the other tabs.

- [ ] `Sales` sheet: `SaleID | Date | Customer | Category | Amount | SalesTax | PaymentMethod | Reference | Memo |
      AttachmentFile`
- [ ] `Customers` list (optional; free-typed names work too)
- [ ] Sales tax collected shown separately (it's owed to the state, not income)
- [ ] Later (parking lot): simple invoices and "mark paid"

**Done when** a month of sales can be entered and the monthly total matches the bank deposits.

## Phase 6 — Reports (1.7.0)

- [ ] **Profit & Loss** for a month, quarter or year: Sales − Expenses by category − Payroll (contract labor/wages)
- [ ] Spending by category and by vendor; sales by month and by customer
- [ ] **Tax-pro pack**: one export with the P&L, expenses by Schedule C category, contractor and vendor 1099 totals,
      and the existing annual statements, as PDF and Excel
- [ ] Every report prints with the company branding and opens in GridCraft

**Done when** the tax professional can work from the pack without asking for anything else.

## Phase 7 — Timebooks 2.0 (polish)

- [ ] Product name: rename the app from "TimeCard Pro" to **Timebooks** (window title, installer, shortcuts, the
      Documents folder) with a migration that moves nothing the owner has to find again
- [ ] First-run wizard: business name, features, PIN, pay schedule, check stock
- [ ] Short in-app help for each tab; a printable "getting started" page
- [ ] Code signing for Setup.exe (so Windows SmartScreen doesn't warn)

---

## 4. How we work each phase

1. **Plan:** turn the phase into a short checklist here; settle its open decisions (below) first.
2. **Schema first:** add sheets/columns with the schema version bump and migration, plus tests that an older workbook
   loads and a round trip keeps everything.
3. **Build** in small commits; keep `python -m unittest discover -s tests` green.
4. **Check it on screen:** a smoke script that drives the real window with temporary data and takes screenshots
   (never the owner's real workbook).
5. **Release:** bump `__version__`, build Setup.exe, publish a GitHub Release with "What's new", update from the
   previous version through Check for Updates.
6. **Write it down:** DESIGN.md gets the rules and schema; this playbook gets the checkmarks.

## 5. Decisions to make (owner)

| # | Decision | When | Recommendation |
|---|---|---|---|
| 1 | Rename to Timebooks now or at 2.0? | Phase 1 or 7 | At 2.0, once expenses/sales exist |
| 2 | Hide a turned-off feature completely, or show it greyed out? | Phase 1 | Hide completely |
| 3 | Which scanner does the business have (brand/model, USB or network)? | Before Phase 3 | Needed to test WIA vs. folder |
| 4 | Allow optional online AI receipt reading? | Mid Phase 3 | Decide after testing offline OCR on real receipts |
| 5 | Is the Expenses tab behind the PIN? | Phase 2 | No by default; a switch to lock it |
| 6 | Cash-basis only (record when paid)? | Phase 2 | Yes, the same as the annual statements today |
| 7 | One shared Checks sheet for payroll and vendors, or separate sheets? | Phase 1 schema | One shared sheet with a Type column |

## 6. Risks and how we handle them

- **Older app saves a newer workbook and drops data.** Schema version + "this workbook was made by a newer version"
  warning (Phase 1). Keep every copy updated via Check for Updates.
- **OCR misreads totals.** Review screen and highlighted low-confidence fields; never auto-approve.
- **Receipt folder separated from the workbook** (copied to another PC without it). Paths stored relative to the
  workbook; backups and portable copies include the folder; a "find missing receipts" tool.
- **Printer alignment** for vendor checks: reuse the payroll alignment test and offsets.
- **Workbook gets large over a year.** Images stay outside the `.xlsx`; one workbook per year.
- **Tax rules change** (e.g. 1099 thresholds). Thresholds are settings, labelled "confirm with your tax professional".

## 7. Parking lot (ideas, not scheduled)

- Bank statement CSV import and matching against expenses/sales
- Mileage log
- Recurring expenses (rent, subscriptions)
- Simple invoices with a printable PDF
- Multiple businesses in one app (one workbook each, quick switcher)
- Positive-pay file export for the bank
- Mobile photo upload over the local network
