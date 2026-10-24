# TimeBooks — Roadmap

The working plan for turning TimeCard Pro into **TimeBooks**, a small-business bookkeeping desktop app. Each release
below is useful on its own. Check items off as they land, and add new ideas to the **Parking lot** at the bottom
rather than letting them grow the current release.

_Last updated: 2026-10-24 · published: 1.2.0 · in progress: 1.3.0 (part 3 of 3) · next: 1.4.0_

---

## 1. Where we are

**1.1.0–1.1.1:** time cards with lunch deduct, year log, payroll for contractors (hourly or salary), voucher / 3-up /
handwrite checks, annual statements for the tax professional, PIN on Employees, portable USB copy, Google Drive style
backups, themes and text size, company branding, Check for Updates from GitHub Releases, and a workbook created by
Setup in `Documents\TimeCard Pro`.

**1.2.0 (released 2026-10-24):**
- Salary per year or per pay period; Pay timing (hold one pay period back)
- Time card hours save automatically; Pay & Checks opens on the right period and says who it skipped
- Year Log by pay period × employee, with that period's notes and time off
- **Time off:** a Type on every day (Worked / PTO / Holiday / Sick / Closed); hours fill in from Company Settings;
  hourly people paid for time off at their regular rate (a switch per type), never toward overtime; Closed = 0 hours
- **Lock any tab with the PIN** (Company Settings → Locked by PIN; one PIN from Setup; Company Settings always locked)
- **Features on/off** (Company Settings → Features: Time cards, Payroll checks); Setup asks what the business needs
- Privacy: the window shows the workbook's file name only; Company Settings shows where it is and how to lock it
- Workbook schema version: an older copy never saves over a newer workbook
- Code split into `timecard/ui/`, one module per tab

Still open from earlier releases:
- [ ] First real update: an installed 1.1.1 → Help → Check for Updates → 1.2.0; confirm the workbook, PIN and settings
- [ ] Printer test on the real QuickBooks voucher checks (Pay & Checks → Alignment Test on plain paper first)

## 2. Where we're going

> **A clean, simple business app for a small business owner who isn't an accountant.**
> Track the people you pay, the money you spend, and the money you take in, and print every check you write,
> all in one workbook you own, on your own computer.

Guiding rules (these come from the decisions already made in the app):
1. **Your data stays yours.** One `.xlsx` workbook per business per year, readable in GridCraft/Excel. Nothing is sent
   anywhere unless the owner turns it on (and says yes each time it matters).
2. **Only show what the business uses.** Every major area can be turned off and its tab hidden.
3. **Sensitive things sit behind the PIN.** The owner chooses which tabs are locked; Company Settings always is.
4. **One checkbook.** Payroll checks and vendor checks share one check-number sequence and one register.
5. **Automate the boring part, confirm the important part.** Scanning fills in a receipt; a person approves it.
6. **Small, shippable steps.** Each release is useful on its own.

## 3. The plan at a glance

| Release | What the owner gets | Size |
|---|---|---|
| 1.2.0 | ✅ Released: time off, tab locks, features on/off, code split | — |
| **1.3.0** | **TimeBooks**: the new name; **Expenses & receipts** (by hand, scanned, scanner folder); recurring expenses; a mileage log; owners as a pay group; bank statements in and matched; phone photos over Wi-Fi | XL |
| 1.4.0 | **Vendors & Pay Bills**: print checks to vendors on the same checkbook; positive-pay file for the bank | M |
| 1.5.0 | **Sales** tab: record money coming in; simple invoices with a printable PDF | M |
| 1.6.0 | **Reports**: Profit & Loss, spending by category, mileage, tax-pro export pack | M |
| 2.0.0 | Several businesses in one app, first-run wizard, in-app help, code-signed installer | M |

1.4.0 and 1.5.0 are independent and can swap order.

---

## 1.3.0 — TimeBooks: everything since 1.2.0, in one release

_Decided 2026-10-24:_ nothing has been published since 1.2.0, so the rename, Expenses and scanning, recurring
expenses / mileage / owners, and bank statements / phone photos all ship together as 1.3.0 (built in three
parts below, each reviewed before the next).

- [ ] Publish `v1.3.0` as the only download (delete the 1.2.0 release); the owner uninstalls TimeCard Pro and
      installs TimeBooks

### Part 1 — TimeBooks: the new name, Expenses and receipt scanning

One release with three parts, built in this order so each part lands on top of the last: **A** the rename, **B**
expenses by hand, **C** scanning. Each part is committed and tested on its own; only the release waits for all three.

#### A. Rename to TimeBooks (first, before any new tabs)

**Owner sees:** the app, Setup, shortcuts, window title, printouts and Help → About say **TimeBooks**. Their
workbooks, PIN, settings, backups and recent files are all still there after the update.

- [ ] Names: `APP_NAME` (app and installer), the `.exe` (`Timebooks.exe`), Start menu / desktop shortcuts, the
      uninstall entry (registry key and publisher), window title, About, PDF titles and footers, the output folder
      ("Timebooks Output"), the installer's own text
- [ ] **Moving the owner's things** (once, on first start of 1.3.0, with a log line for each step, never deleting
      the old copy until the new one is in place):
      - settings and PIN: `%APPDATA%\TimeCardPro` → `%APPDATA%\Timebooks`
      - program: `%LOCALAPPDATA%\Programs\TimeCardPro` → `...\Programs\Timebooks` (shortcuts and uninstall entry
        repointed; the old folder removed after the new one runs)
      - portable copies: `TimeCard Pro Portable` keeps its folder name (it may be on a labelled USB drive); only the
        program file inside is renamed
      - workbooks are **not moved or renamed** (`Documents\TimeCard Pro` stays where it is; new workbooks go to
        `Documents\Timebooks`). Recent files and the last workbook keep pointing at the existing files
- [ ] **Updates from 1.2.0 must still find 1.3.0:** 1.2.0 copies look for a release file named exactly
      `TimeCardPro-Setup.exe`. The 1.3.0 release attaches the installer under **both** names
      (`Timebooks-Setup.exe` and `TimeCardPro-Setup.exe`); 1.3.0 looks for `Timebooks-Setup.exe` first and falls back
      to the old name. Keep attaching the old name until no 1.2.0 copies are left.
- [ ] `--update` from a 1.2.0 copy installs into the new folder and starts `Timebooks.exe`
- [ ] Python package stays `timecard` (internal only; renaming it would touch every file for no visible gain)
- [ ] Test: update a 1.2.0 install and a 1.2.0 portable copy to 1.3.0; workbook, PIN, settings, recent files, backups
      and shortcuts all survive; uninstall removes everything

#### B. Expenses & Receipts, entered by hand

**Owner sees:** an **Expenses** tab. Add an expense (date, vendor, amount, category, payment method, memo), attach a
receipt (image or PDF), view it, filter by month/category/vendor, and sort, the same way as the Year Log. Totals by
category at the bottom.

- [ ] Company Settings → Features gets **Expenses & receipts** (off until turned on); Locked by PIN gets an
      **Expenses** box (off by default)
- [ ] Schema version 4 for the new sheets

**Data (new sheets):**

`Expenses`: `ExpenseID | Date | Vendor | VendorID | Category | Amount | Tax | PaymentMethod | CheckNumber | Memo |
ReceiptFile | Status | Created`
- `Status`: `Draft` (scanned, not reviewed) / `Approved`
- `PaymentMethod`: Cash, Card, Check, Bank transfer, Other. `CheckNumber` links to a vendor check (1.4.0).
- `ReceiptFile` is a path **relative to the workbook's folder**, e.g. `Receipts\2026\2026-10-24 Home Depot 84.17.jpg`

`Categories`: the owner's list, seeded with IRS Schedule C lines so the tax professional can use it directly:
Advertising, Car & truck, Commissions & fees, Contract labor, Insurance, Legal & professional, Office expense, Rent
(equipment), Rent (property), Repairs & maintenance, Supplies, Taxes & licenses, Travel, Meals, Utilities, Other.

**Receipt files** live in a `Receipts\<year>\` folder next to the workbook (`Data\Receipts` on a portable copy), not
inside the `.xlsx`. Images would make the workbook huge and slow to save.
- [ ] Backups copy the receipts folder too (only new files each time)
- [ ] Moving or renaming a receipt is done through the app so links don't break

#### C. Scan a receipt and fill it in automatically

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

- [x] Scan Receipt menu: scanner (WIA dialog), photo/PDF, scanner folder (checked when the tab opens); review
      screen with the picture and "check this" marks; drafts for later. _Not yet tried with a real scanner._
- [ ] Owner: try it with the business's scanner (decision 3) and the 20-real-receipt test below

**Reading the receipt (offline first):**
- [x] **Prototype built** (2026-10-24, branch `worktree-agent-afa97093d5a3d2d78`, commit `c4ff4d2`, not merged yet):
      `timecard/receipts_ocr.py` + 41 tests + `docs/ocr-findings.md`. On 135 generated receipts: total 99%, date 96%,
      total and date both 95%, vendor spelling 91%; never "high confidence" when wrong; about 0.05 s per receipt.
      Merged into main 2026-10-24.
- [x] **Windows OCR** (`Windows.Media.Ocr`): built into Windows 10/11, free, offline. _Decided:_ read it in-process
      with the PyWinRT packages (add the pins below to `requirements.txt`), with Windows PowerShell 5.1 as the automatic
      fallback when they're missing. _Checked on this PC: available, English (en-US)._
      ```
      winrt-runtime==4.0.0
      winrt-Windows.Foundation==4!10.0.28000.2705
      winrt-Windows.Foundation.Collections==4!10.0.28000.2705
      winrt-Windows.Globalization==4!10.0.28000.2705
      winrt-Windows.Graphics.Imaging==4!10.0.28000.2705
      winrt-Windows.Media.Ocr==4!10.0.28000.2705
      winrt-Windows.Storage.Streams==4!10.0.28000.2705
      pypdfium2==5.14.0
      ```
- [x] **PDF receipts:** _Decided: yes._ First page rendered at 300 dpi with `pypdfium2` (about 7 MB in the installer).
      Later: PDFs that already contain text (emailed receipts) can skip OCR and read the text directly.
- [x] Field extraction rules (prototype; plus a decimal point read as a bullet, "1 • 27"): total = the largest amount next to TOTAL / AMOUNT DUE / BALANCE; date = first date
      pattern; tax = TAX line; vendor = top lines matched against the vendor names already used (the Vendors list
      once 1.4.0 adds it; learns from corrections); items = lines with a price.
- [x] Remember corrections: the next receipt from the same vendor suggests the same category.
- [x] Confidence: fields the app isn't sure of are highlighted for review; nothing is approved without a person.
      _Decided:_ a vendor is only "sure" when it matches a known vendor, so the first receipt from every new vendor
      is highlighted until that vendor is saved.
- [x] **No online AI receipt reading for now.** _Decided:_ ship offline only. Revisit only if the 20-real-receipt test
      fails mainly on faded thermal paper or phone photos; if it's ever added it needs the owner's own API key, a clear
      one-time warning and a per-receipt indicator.

- [x] Pre-release reviews (rename; Expenses and scanning): all findings fixed

**Part 1 is done when** a 1.2.0 copy updates to Timebooks with everything intact; a month of expenses can be entered
with receipts attached and the category totals match a hand count; and 20 real receipts from the business are
scanned, at least 80% get the correct total and date without edits, and every one can be fixed on the review screen.

### Part 2 — Recurring expenses, mileage, owners

From the parking lot (owner's notes, 2026-10-24). Built in this order; each part is committed and tested on its own.

#### A. Recurring expenses (Expenses tab)
**Owner sees:** mark an expense as **repeats monthly / quarterly / yearly** (rent, phone, software). On the due date
the app adds it as a Draft for that period (nothing is counted until approved), and the Expenses tab shows what's due.
- [x] `Recurring` sheet: `RecurringID | Vendor | Category | Amount | PaymentMethod | Memo | Every | NextDate | Active | Day`
- [x] Due ones become Drafts when the workbook opens (once per period, never twice); stop / restart / skip one

#### B. Mileage log, and owners as a pay group
**Owner sees:** a **Mileage** list (in Expenses): date, who drove, purpose, from/to, miles; the IRS standard mileage
rate from Company Settings (labelled "confirm each year with your tax professional"), totals by person and month.
On the Employees tab a person can be an **Owner**: no payroll checks (a sole proprietor takes draws, not pay), not on
1099s, but gas, mileage and other expenses can be put **on their account**.
- [x] `Mileage` sheet: `TripID | Date | PersonID | Purpose | From | To | Miles | Rate | Amount | Notes`
- [x] `Employee.pay_type` "Owner" (Pay & Checks and 1099 totals leave owners out; their history stays)
- [x] Expense `PersonID` column ("on whose account"), so gas paid by the owner shows on their account
- [ ] Mileage and owner totals feed the 1.6.0 reports

**Part 2 is done when** a month's rent repeats on its own, a week of trips adds up correctly, and an owner's gas
and miles show on their account without a paycheck.

- [x] Pre-release review (part 2)

### Part 3 — Bank statements and phone photos

#### A. Bank statements in
**Owner sees:** **Expenses → Import Bank Statement**: a CSV from the bank, or, when the bank only gives a PDF (or a
paper statement is scanned), the app reads the statement and builds the list itself. Each line is matched to an
existing expense (same amount, close date); unmatched money out can become Draft expenses with one click.
- [x] CSV import with a column picker (date, description, amount / debit / credit), remembered per bank
- [x] Statement PDF / scan → rows: text PDFs read directly (pypdfium2 text), scans through the receipt reader; the
      owner checks the rows before anything is matched, and can save them as a CSV
- [x] Matching rules and a review list (matched / possible / unmatched); nothing changes without the owner's OK
- [x] Money in is kept for the Sales tab (1.5.0) to match deposits

#### B. Phone photos over Wi-Fi
**Owner sees:** **Scan Receipt → From my phone**: a QR code and a short web address; the phone (same Wi-Fi) opens it,
takes photos of receipts, and they arrive as Drafts. No app to install and nothing goes over the internet.
- [x] Small upload page served by TimeBooks only while that window is open, on the local network only, with a
      one-time code in the address; pictures go to the scanner-folder flow
- [x] Windows Firewall will ask the first time; explain it in the window. If a PC blocks it, the scanner folder
      (e.g. a phone's OneDrive/Google Photos sync folder) is the fallback

**Part 3 is done when** a real bank statement (CSV and PDF) matches against the expenses and a phone photo arrives
as a draft.

## 1.4.0 — Vendors and paying bills by check

**Owner sees:** a **Vendors** list (name, address, phone, default category, "send a 1099"), and **Pay Bills**: pick a
vendor, amount, memo, and which expenses or bills it pays, then print. The check prints with the same layouts
and alignment as payroll checks.

- [ ] **One check-number sequence** shared with payroll (`next_check_number`) and one **Check Register** showing every
      check (payroll and vendor), with void and reprint
- [ ] `checks.py`: generalise the payee from Employee to "payee" (employee or vendor), with no payroll stub text
      on vendor checks; voucher stub shows the bills paid
- [ ] `Vendors` sheet: `VendorID | Name | Address1 | Address2 | Phone | Email | DefaultCategory | Is1099 | Notes | Active`
- [ ] Vendor checks: a `Type` column on a shared `Checks` sheet or a separate sheet (decision 7)
- [ ] Vendor 1099 totals in the annual export (threshold from Company Settings; confirm each year with the tax pro)
- [ ] No vendor tax IDs stored (same rule as contractors: collect W-9s on paper)
- [ ] **Positive-pay file** for the bank (check number, date, amount, payee) from the Check Register, in the format
      the business's bank asks for (from the parking lot)
- [ ] Features switch **Vendor checks**; Locked by PIN box for the new tab

**Done when** a vendor check and a payroll check print on the same stock with consecutive numbers and both appear
in the register.

## 1.5.0 — Sales / money in

**Owner sees:** a **Sales** tab. Record a sale or deposit: date, customer, amount, sales tax collected, payment
method, category (service, product, other), invoice/reference #, memo; attach a document. Month and customer filters
and totals, same as the other tabs.

- [ ] `Sales` sheet: `SaleID | Date | Customer | Category | Amount | SalesTax | PaymentMethod | Reference | Memo |
      AttachmentFile`
- [ ] `Customers` list (optional; free-typed names work too)
- [ ] Sales tax collected shown separately (it's owed to the state, not income)
- [ ] Features switch **Sales**; Locked by PIN box for the new tab
- [ ] **Simple invoices** with a printable PDF (company branding), numbered; "mark paid" records the sale
      (from the parking lot)

**Done when** a month of sales can be entered and the monthly total matches the bank deposits.

## 1.6.0 — Reports

- [ ] **Profit & Loss** for a month, quarter or year: Sales − Expenses by category − Payroll (contract labor/wages)
- [ ] Spending by category and by vendor; sales by month and by customer
- [ ] **Tax-pro pack**: one export with the P&L, expenses by Schedule C category, contractor and vendor 1099 totals,
      and the existing annual statements, as PDF and Excel
- [ ] Every report prints with the company branding and opens in GridCraft

**Done when** the tax professional can work from the pack without asking for anything else.

## 2.0.0 — Polish

- [ ] **Several businesses in one app**: one workbook each, a quick switcher, each with its own settings and
      check numbers (from the parking lot)

- [ ] First-run wizard: business name, features, PIN, pay schedule, check stock
- [ ] Short in-app help for each tab; a printable "getting started" page
- [ ] Code signing for Setup.exe (so Windows SmartScreen doesn't warn)

---

## 4. How we work each release

1. **Plan:** turn the release into a short checklist here; settle its open decisions (below) first.
2. **Schema first:** add sheets/columns with a `SCHEMA_VERSION` bump, plus tests that an older workbook loads and a
   round trip keeps everything.
3. **Build** in small commits; keep `python -m unittest discover -s tests` green.
4. **Check it on screen:** a smoke script that drives the real window with temporary data and takes screenshots
   (never the owner's real workbook or PIN).
5. **Review** before release (the timebooks-reviewer checklist: check amounts, workbook data, exposed pay or bank
   details, getting past the PIN).
6. **Release:** bump `__version__`, build Setup.exe, publish a GitHub Release with "What's new", update from the
   previous version through Check for Updates.
7. **Write it down:** DESIGN.md gets the rules and schema; this roadmap gets the checkmarks.

## 5. Decisions

| # | Decision | When | Answer / recommendation |
|---|---|---|---|
| 1 | Rename to TimeBooks now or at 2.0? | — | **Decided 2026-10-24: in 1.3.0**, first part of that release |
| 2 | Hide a turned-off feature completely, or show it greyed out? | — | **Done in 1.2.0: hidden completely** |
| 3 | Which scanner does the business have (brand/model, USB or network)? | Before 1.3.0 part C | Needed to test WIA vs. folder |
| 4 | Allow optional online AI receipt reading? | Mid 1.3.0 part C | **Decided 2026-10-24: no, offline only for now**; revisit after the 20-real-receipt test |
| 5 | Is the Expenses tab behind the PIN? | — | **Settled by 1.2.0's Locked by PIN:** a box for Expenses, off by default |
| 6 | Cash-basis only (record when paid)? | 1.3.0 part B | Yes, the same as the annual statements today |
| 7 | One shared Checks sheet for payroll and vendors, or separate sheets? | 1.4.0 schema | One shared sheet with a Type column |
| 8 | Add the Windows OCR packages (PyWinRT) to the app? | — | **Decided 2026-10-24: yes**, with the PowerShell fallback |
| 9 | Read PDF receipts (pypdfium2, ~7 MB)? | — | **Decided 2026-10-24: yes** |
| 10 | Highlight every new vendor for review? | — | **Decided 2026-10-24: yes**, until the vendor is known |
| 11 | Nothing extra behind the PIN; owner locks the file if they want | — | **Decided 2026-10-24** (Company Settings explains how) |
| 12 | Skip publishing 1.1.2 | — | **Decided 2026-10-24**: its changes shipped in 1.2.0 |
| 14 | Owners: a pay type on Employees, or a separate list? | 1.3.0 | A pay type "Owner" on Employees (one list of people, history kept) |
| 15 | Mileage: standard rate or actual car costs? | 1.3.0 | Standard rate (a setting, confirm yearly); actual costs stay ordinary expenses |
| 16 | Phone photos: local upload page or a sync folder? | 1.3.0 | Local page while the window is open; a phone sync folder as the fallback |
| 17 | Ship everything since 1.2.0 as one 1.3.0? | — | **Decided 2026-10-24**: no releases were published in between |

## 6. Risks and how we handle them

- **Older app saves a newer workbook and drops data.** The schema version (1.2.0) stops copies from 1.2.0 on; keep
  every copy updated via Check for Updates.
- **The rename strands someone.** 1.2.0 copies must still find the update (installer attached under the old name too),
  and settings, PIN and program folder move once with the old copy kept until the new one runs. Workbooks don't move.
- **OCR misreads totals.** Review screen and highlighted low-confidence fields; never auto-approve.
- **Receipt folder separated from the workbook** (copied to another PC without it). Paths stored relative to the
  workbook; backups and portable copies include the folder; a "find missing receipts" tool.
- **Printer alignment** for vendor checks: reuse the payroll alignment test and offsets.
- **Workbook gets large over a year.** Images stay outside the `.xlsx`; one workbook per year.
- **Tax rules change** (e.g. 1099 thresholds). Thresholds are settings, labelled "confirm with your tax professional".

## 7. Parking lot (ideas, not scheduled)

- Year Log / monthly summary: include days just outside the year that fall in the first/last pay period (review #11)
- A day that is part worked, part time off (one Type per day today)
- Optional password-protected workbook (encrypted `.xlsx`), for owners who would trade "opens in GridCraft" for a
  locked file

_Moved out of the parking lot 2026-10-24:_ bank statement import (1.3.0), mileage log and owners as a pay group
(1.3.0), recurring expenses (1.3.0), phone photo upload (1.3.0), positive pay (1.4.0), invoices (1.5.0),
several businesses (2.0.0).
