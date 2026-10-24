# TimeCard Pro — design contract

Windows desktop app (Python 3.15 + tkinter/ttk) that tracks hours worked per employee for a
calendar year, stores everything in a GridCraft-compatible `.xlsx` workbook (the "client file"),
runs payroll and prints checks to PDF. Modeled on timecardcalculator.net (weekly card, in/out
punches, break deduction, daily/weekly totals, overtime).

Packaging: PyInstaller one-folder build, installed per-user to
`%LOCALAPPDATA%\Programs\TimeCardPro` with Start Menu + Desktop shortcuts.

Dependencies (venv at `../.venv`): `openpyxl`, `reportlab`. Standard library otherwise.
Run from source: `..\.venv\Scripts\python -m timecard` (package dir `timecard/timecard/`).

## Hard requirements from the user
1. Track time worked by employee and log it for the given **year** (one workbook per business per year).
2. Each day has a **lunch deduct in minutes** next to it, for days a team member forgets to
   clock out for lunch. Hours for the day = punched time − deduct minutes.
3. **Pay rate is stored in the client file (workbook), never displayed on screen.** The GUI may
   *set* a rate through a masked dialog and may show "rate on file: yes/no", but never shows the value.
   Check amounts / net pay necessarily appear on the payroll screen and on checks.
4. Print **checks** for businesses (PDF, standard letter-size voucher check stock: check on top,
   two stubs below).
5. Data storage is an `.xlsx` that opens cleanly in **GridCraft** (`C:\Program Files\GridCraft\gridcraft.exe`,
   CLI `gridcraft-cli.exe info|cat|check|convert`).

## Package layout (`timecard/timecard/`)
| module | owner | purpose |
|---|---|---|
| `models.py` | lead (done) | dataclasses shared by everyone — DO NOT change signatures without telling the lead |
| `calc.py` | agent A | time parsing/formatting, daily/weekly hours, overtime, pay & withholding, amount-to-words, YTD |
| `storage.py` | agent A | load/save/create the workbook with openpyxl |
| `checks.py` | agent B | reportlab PDFs: checks, payroll register, printable time card |
| `app.py` + `__main__.py` | agent C | entry point (`app.py` re-exports `ui.app.main`) |
| `ui/` | | tkinter GUI, one module per tab: `common` (themes, settings, helpers, widgets), `pin` (PIN dialogs and lock overlay), `dialogs`, `update_dialogs`, `timecard_tab`, `employees_tab`, `yearlog_tab`, `payroll_tab`, `company_tab`, `app` (main window) |
| `tests/` | each agent | `unittest` tests, run with `..\.venv\Scripts\python -m unittest discover -s tests` from `timecard/` |

## Workbook schema (sheet names and column headers are exact; row 1 = headers)

### `Company` — two columns `Setting`, `Value`, one row per key
Keys (match `models.Company` field names exactly, snake_case):
`company_name, address1, address2, phone, bank_name, bank_address, routing_number, account_number,
year, next_check_number, pay_period, week_start, default_lunch_minutes, overtime_threshold_weekly,
overtime_multiplier, federal_withholding_pct, state_withholding_pct, social_security_pct,
medicare_pct, check_stock, micr_font_path, print_offset_x, print_offset_y, signature_line_text` (plus every later
`models.Company` field, e.g. `pay_hold_back`, the branding keys and the time off keys `pto_hours` … `sick_paid`).

Then two rows that aren't settings: `schema_version` (`storage.SCHEMA_VERSION`: 1 = 1.1.0–1.1.1, no marker;
2 = 1.1.2; 3 = 1.2.0) and `app_version` (the version that saved the file). Bump `SCHEMA_VERSION` whenever a sheet or
column is added. A workbook whose `schema_version` is higher than the app's opens with a warning and is **never
saved over** (no autosave; File > Save refuses; Save As to a separate copy works), so an older copy can't drop columns
it doesn't know. Copies older than 1.2.0 don't read the marker, so they still have to be updated by hand.

### `Employees`
`EmployeeID | FirstName | LastName | Address1 | Address2 | PayType | PayRate | Active | Notes | SalaryBasis`
- PayType: `Hourly` or `Salary`; hours are tracked either way.
- SalaryBasis (Salary only, blank for Hourly): `Annual` (default, and what a workbook without the column means):
  PayRate = yearly salary, each check = annual ÷ periods per year (52/26/24/12). `Per Period`: PayRate = the amount
  of every check. Changing PayType or SalaryBasis in the app clears the rate so it is re-entered in the new terms.
  Added last so existing column letters don't move.
- Active: `Yes`/`No`. PayRate cell number format `$#,##0.00`.

### `TimeLog` — one row per employee per day worked
`Date | EmployeeID | Employee | In1 | Out1 | In2 | Out2 | LunchDeductMin | Hours | Notes`
- Date: Excel date, format `yyyy-mm-dd` (weekday shown via format `ddd yyyy-mm-dd` is fine).
- In/Out: Excel time values (fraction of day), format `h:mm AM/PM`; blank if not punched.
- Out earlier than In = crosses midnight (add 24h).
- `Employee` is a display name ("Last, First") for people reading it in GridCraft; app ignores it on load.
- `Hours` is written as a **formula** so it stays live in GridCraft, e.g. row 2:
  `=MAX(0,ROUND((IF(AND(D2<>"",E2<>""),MOD(E2-D2,1),0)+IF(AND(F2<>"",G2<>""),MOD(G2-F2,1),0))*24-H2/60,2))`
  (`MAX(0, …)` keeps it in line with the never-negative rule; verified in GridCraft.)
  The app never relies on cached formula values; it recomputes in Python with `calc.day_hours`.
- Rows sorted by Date then EmployeeID on save.

### `Payroll` — one row per check
`CheckNumber | CheckDate | PeriodStart | PeriodEnd | EmployeeID | Employee | RegularHours |
OvertimeHours | RegularPay | OvertimePay | GrossPay | FederalTax | StateTax | SocialSecurity |
Medicare | OtherDeductions | NetPay | Memo | Voided | LeaveHours | LeavePay`
- Money columns format `$#,##0.00`; Voided `Yes`/`No`.
- `LeaveHours` / `LeavePay`: paid time off on the check (see Time off below); `GrossPay` includes `LeavePay`.
  Workbooks without the columns load them as 0.

### `Summary` — regenerated on every save, formulas only (for viewing in GridCraft)
Per active employee plus former (Active=No) employees with hours or checks this year, name suffixed " (former)": `EmployeeID | Employee | Hours YTD | Gross YTD | Net YTD` using
`SUMIFS` over TimeLog/Payroll (Payroll formulas exclude Voided="Yes"). Then a monthly hours
block: rows = employees, columns Jan..Dec, `SUMIFS(TimeLog!I:I, TimeLog!B:B, id, TimeLog!A:A, ">="&DATE(y,m,1), TimeLog!A:A, "<"&DATE(y,m+1,1))`.

Freeze header rows, bold headers, reasonable column widths on all sheets.

## Business rules
- Day hours = Σ(Out−In for complete pairs) − LunchDeductMin/60, never negative, rounded to 2 dp.
- Overtime: per **workweek** (starting `company.week_start`, "Monday" or "Sunday"); hours over
  `overtime_threshold_weekly` (default 40) are OT at `overtime_multiplier` (default 1.5).
  A pay period's reg/OT = sum over the workweeks that fall in the period (only days inside the period count;
  a workweek straddling the period boundary is computed using only the in-period days).
- Pay periods: `Weekly`, `Biweekly` (anchored to the first `week_start` day on or before Jan 1 of `year`),
  `Semimonthly` (1–15, 16–end), `Monthly`.
- Hourly gross = reg×rate + ot×rate×multiplier. Salary gross = calc.salary_per_period(annual rate), OT not paid.
- Withholding = flat percentages of gross from Company (fed/state default 0, SS 6.2, Medicare 1.45).
  This is a simple estimate — the UI must label it as such. OtherDeductions entered manually per check.
- Net = gross − all deductions, rounded to cents (use `Decimal`, ROUND_HALF_UP).
- Check numbers come from `company.next_check_number`, incremented when payroll is recorded.

## Checks
- `check_stock = "preprinted"` (default): stock already has bank/MICR/company printed; app prints only
  date, payee, numeric amount, amount in words, memo, address block, stubs.
- `check_stock = "blank"`: also prints company name/address, bank name, check number, signature line
  and a MICR line `⑈check⑈ ⑆routing⑆ account⑈` using the TTF at `micr_font_path` (E-13B font the user
  supplies). If no font is set, print the MICR line in a placeholder font and add a warning on the PDF
  margin: "MICR font not configured — not bank-processable".
- `print_offset_x/y` (inches) shift everything for printer alignment.
- Stubs show: employee, period, check #, date, hours (reg/OT), current and YTD gross, each deduction, net.
  **Stubs do not show the pay rate.**
- Amount in words: `"One Thousand Two Hundred Thirty-Four and 56/100"` followed by asterisks to fill the line.

## Module APIs (exact signatures — the GUI codes against these in parallel)

### `calc.py`
```python
def parse_time(text: str) -> time | None        # "8", "8:30", "830a", "5:15 pm", "17:15", "" -> None; ValueError on junk
def format_time(t: time | None) -> str          # "8:30 AM", "" for None
def pair_hours(t_in: time | None, t_out: time | None) -> Decimal   # 0 if either missing; crosses midnight
def day_hours(e: TimeEntry) -> Decimal          # rule above, 2 dp, >= 0
def week_start_for(d: date, week_start: str) -> date
def period_for(d: date, company: Company) -> tuple[date, date]     # pay period containing d
def periods_in_year(company: Company) -> list[tuple[date, date]]   # every pay period touching company.year
def summarize_hours(entries: list[TimeEntry], company: Company,
                    start: date, end: date) -> HoursSummary        # only entries within [start, end]
def compute_payroll(employee: Employee, hours: HoursSummary, company: Company, *,
                    check_number: int, check_date: date, period_start: date, period_end: date,
                    other_deductions: Decimal = Decimal("0"), memo: str = "") -> PayrollRecord
def amount_to_words(amount: Decimal) -> str     # "One Thousand Two Hundred Thirty-Four and 56/100"
def ytd_totals(records: list[PayrollRecord], employee_id: str, through: date,
               entries: list[TimeEntry] | None = None) -> YtdTotals   # excludes voided; hours from entries if given
def money(x) -> Decimal                          # quantize to cents, ROUND_HALF_UP
```

### `storage.py`
```python
class WorkbookError(Exception): ...

class TimecardBook:
    path: Path | None
    company: Company
    employees: dict[str, Employee]          # by employee_id
    entries: dict[tuple[str, date], TimeEntry]
    payroll: list[PayrollRecord]
    dirty: bool

    @classmethod
    def new(cls, company: Company) -> "TimecardBook"
    @classmethod
    def load(cls, path: str | Path) -> "TimecardBook"     # raises WorkbookError with a friendly message
    def save(self, path: str | Path | None = None) -> Path  # writes all sheets atomically (temp file + replace); clears dirty
                                                          # raises WorkbookError if file is locked (open elsewhere)
    # employees
    def next_employee_id(self) -> str                   # "E001", "E002", ...
    def upsert_employee(self, emp: Employee) -> None    # for an existing id, ALWAYS keeps the stored pay_rate
                                                        # (rates change only via set_pay_rate)
    def set_pay_rate(self, employee_id: str, rate: Decimal) -> None
    def has_pay_rate(self, employee_id: str) -> bool
    def active_employees(self) -> list[Employee]        # sorted by last, first
    # time
    def get_entry(self, employee_id: str, d: date) -> TimeEntry   # returns a blank entry if none
    def set_entry(self, entry: TimeEntry) -> None       # removes it if entry.is_empty()
    def entries_for(self, employee_id: str | None, start: date, end: date) -> list[TimeEntry]  # sorted
    # payroll
    def record_payroll(self, records: list[PayrollRecord]) -> None   # appends; sets next_check_number = max+1
    def void_check(self, check_number: int) -> None
    def records_for_period(self, start: date, end: date) -> list[PayrollRecord]
```

### `checks.py`
```python
def render_checks(records: list[PayrollRecord], book: TimecardBook, out_path: str | Path) -> Path
    # one letter page per check: check top third, employee stub, company stub. Uses book.company,
    # book.employees (address), calc.ytd_totals(book.payroll, ...). Skips voided records.
def render_register(records: list[PayrollRecord], book: TimecardBook, out_path: str | Path,
                    title: str = "Payroll Register") -> Path   # landscape table with totals
def render_timecard(employee: Employee, entries: list[TimeEntry], start: date, end: date,
                    book: TimecardBook, out_path: str | Path) -> Path  # printable card w/ signature lines, NO pay rate
def render_alignment_test(book: TimecardBook, out_path: str | Path) -> Path  # sample "VOID" check for printer alignment
```

## CHANGE 2026-10-23 — independent contractors, 3-per-sheet business checks (supersedes above)
- Team members are **independent contractors**. `Company.overtime_enabled` (default **False**) — when off,
  every hour is regular. All withholding percentages default to **0**; the payroll UI shows no tax columns.
  Check amount = hours × rate (or flat Salary/per-period amount) − OtherDeductions, where OtherDeductions
  is shown in the UI as **"Adjustment"** (positive = deduct, negative = add, e.g. reimbursement).
- Checks print on a **regular business checkbook: 3 checks per letter sheet, no stubs** (each check 3.5"
  tall at y = 0, 3.5", 7.0" from the top). No pay-stub/YTD summary. `render_checks(..., start_position=1)`
  where start_position 1–3 = which slot on the first sheet to print into, so partially used sheets aren't wasted.
- Memo line defaults to e.g. `"Contract labor 09/21–10/04/2026 · 80.00 hrs"` (never the rate).
- Summary sheet's Gross YTD column = total paid per contractor for the year (useful for 1099-NEC).
- **Former team members** (Active = No) are never deleted: their Employees row, TimeLog and Payroll history stay
  in the workbook and in the Summary sheet, but the app does not list them in pickers (Time Card, Pay & Checks,
  Year Log filter) or the Employees list unless "Show former team members" is ticked.

## CHANGE — check layouts (`Company.check_layout`)
The business currently uses Vistaprint-style **manual 3-to-a-page binder checks** (left tear-off stub, 7-ring
holes, single or carbonless duplicate). Those are made for handwriting; duplicates cannot go through a printer.
The app offers four layouts; `check_stock` (preprinted/blank) applies to the printed ones.
| key | page | content |
|---|---|---|
| `laser_3up` | US letter, 3 checks at y = 0, 3.5", 7.0" | check only; `start_position` 1–3 |
| `manual_3up` | custom `manual_sheet_width` × `manual_sheet_height`; 3 equal-height rows; left stub `manual_stub_width` wide | stub (check #, date, payee, amount, memo, hours) + check face to the right of the stub; `start_position` 1–3; single-copy sheets only |
| `voucher` (default — the business has QuickBooks voucher checks) | US letter, check top 3.5", two stubs below | stubs: payee, check #, date, period, hours, amount, adjustment, check amount, memo — no tax/YTD summary |
| `handwrite` | US letter | "Checks to Write" list: check #, date, payee, amount, amount in words, memo, plus a signature/initial column — for writing a manual checkbook by hand |

## Annual statements export (`statements.py`) — Employees tab, behind the PIN
"Export Annual Statements…" writes `<Company> Annual Statements <year>.pdf` + `.xlsx` (everyone, or one person).
Cash basis: checks count in the year of their check date; voided checks excluded; former team members included
when they had hours or checks. PDF: summary page (hours, compensation, adjustments, total paid, checks,
"At/over threshold" vs `Company.form_1099_threshold`, default $2,000) + one statement page per payee (address,
totals, paid by month, every check). XLSX sheets: Summary, Payments, Monthly. Never includes pay rates; no TINs are
stored (collect W-9s).

## Portable mode (`paths.py`) and backups (`backup.py`)
- **Portable:** Setup.exe → "Portable copy on a USB or external drive" extracts the app to
  `<drive>\TimeCard Pro Portable\` with `portable.txt`. When that marker sits next to TimeCardPro.exe, settings, PIN,
  log and the default workbook folder are `Data\Settings` and `Data\Workbooks` on the drive; nothing is written to
  the computer (no shortcuts/registry). Paths on the program's drive are stored as `portable:<relative>` so a new
  drive letter still works. Updating a portable copy replaces `_internal` + the exe only; `Data` is never touched.
- **Backups:** Employees tab → Backup… (behind the PIN). Folder = any synced folder; "Use Google Drive" detects
  Google Drive for desktop (`G:\My Drive`, `~\Google Drive\My Drive`, …). Copies go to
  `<folder>\TimeCard Pro Backups\<workbook>\<workbook> YYYY-MM-DD HHMMSS.xlsx`, newest N kept (default 30).
  Optional automatic backup after every save; a missing drive only shows a status-bar warning.
  Google sign-in/API was deliberately not used (needs an OAuth app + Google verification).

## Branding (`branding.py`)
- Company Settings → Branding: logo (any PNG/JPG/BMP/GIF, normalized to PNG ≤ 800×400, transparent border trimmed)
  stored **inside the workbook** on a hidden `Branding` sheet as base64 chunks of ≤ 30,000 chars
  (`TimecardBook.logo_png`), so it travels with backups/portable copies; GridCraft shows it as "Branding (Hidden)".
- `Company.brand_color` (#RRGGBB): app header in the Light theme (text via `text_on`), report headings/rules
  (`ink_color` falls back to near-black if the color is too light to read on paper). Never on the check face.
- `logo_on_checks`: voucher stubs; the check face only on blank stock (preprinted QuickBooks checks are untouched).
  `logo_on_reports`: time cards, register, Checks-to-Write list, annual statements.

## Updates (`updates.py`, installer `--update`)
- Help → **Check for Updates…**, plus "Check for updates at startup" (default on; frozen builds only, at most once a
  day, silent unless a newer version that wasn't skipped exists; errors only logged).
- Source of truth: `GET https://api.github.com/repos/Debauchery1994/Timebooks/releases/latest` (anonymous; the repo is
  public). Tag `vX.Y.Z` compared numerically with `__version__`; asset `TimeCardPro-Setup.exe`.
- Dialog: version, release notes, View on GitHub, **Download and Install / Skip This Version / Later**.
- Download only from `https://github.com/<repo>/releases/download/…` (redirects to GitHub asset hosts only); size
  and GitHub's `sha256` asset digest are verified before running. Installed copies download to `%TEMP%\TimeCardPro-Update`,
  portable copies to `Data\Updates` on the drive.
- The app asks to save, starts `Setup.exe --update <TimeCardPro.exe> --wait-pid <pid>` and closes. The installer waits
  for that process, updates the same copy (portable in place, `Data` untouched; installed copy keeps whichever
  shortcuts exist), retries while files are locked, then restarts the app. Nothing about the user is sent.

## Default workbook folder and the workbook Setup creates
- Installed copies keep workbooks in **Documents\TimeCard Pro** (`paths.default_workbook_dir`), created automatically;
  Documents is the real known folder from Windows (`SHGetKnownFolderPath`), so a OneDrive-redirected Documents is used.
  Portable copies keep `Data\Workbooks` on the drive. Every New/Save As/Open/export dialog starts there.
  Never inside the program folder: updates replace it and uninstall deletes it.
- Setup → "Timecard workbook": **Company name** (required) and a live preview of the path. After installing it creates
  `<Company> Timecards <year>.xlsx` (`paths.workbook_filename`) for the current year, never overwriting an existing file,
  and writes it to settings.json as `last_workbook` + top of Recent Files, so the app opens it on first launch.
  If settings already point at a workbook that exists, Setup shows it as kept and asks nothing.
  Silent installs: `--company "Name"`.

## Autosave, pay timing and the Company Settings lock
- **Autosave** (File → Save automatically, default on, `settings.json` `autosave`): every `mark_dirty` schedules a save
  2 s later. A locked file (open in GridCraft/Excel) only shows a status-bar error and retries every 30 s. Autosaves don't
  make backup copies; one is made when the workbook is closed or another is opened. With autosave on, closing saves
  instead of asking.
- Autosave never overwrites outside edits: the book remembers the file's (mtime, size) at load/save
  (`TimecardBook.changed_on_disk`). If it changed (GridCraft, Excel, another PC) autosave pauses with a status-bar
  message, and File → Save asks before replacing the file. Before the first autosave of each opened book a copy of the
  file as opened goes to the backup folder (or `TimeCard Pro Backups` next to the workbook), so autosave can be undone.
- Changing the pay period (Company Settings, or a New Year workbook carrying people over) clears salaries entered
  per pay period, after a warning, so they are re-entered for the new schedule.
- **Pay timing** (`Company.pay_hold_back`, Company Settings → Pay): `none` (default for new workbooks; workbooks from
  before the setting existed load as `one`, matching 1.1.1) = Pay & Checks opens on the pay period containing today; `one` = hold one pay period back and open on the period before it. Until the user picks a
  period, opening the tab re-applies this. Calculate names hourly people skipped for having no hours in the period when
  they have unpaid hours in another one.
- **Company Settings is behind the PIN** like Employees (bank routing/account numbers live there): same LockOverlay,
  the settings form is removed from the layout while locked, it relocks when the tab is left, on the 5-minute idle
  lock and from File → Lock Employees and Company Settings. Both lock screens share one wrong-PIN count/lockout,
  a screen built before a PIN existed can't be used to replace it, and unsaved Company edits survive a relock.

## Year Log
- One row per **pay period × employee** (`calc.period_log` / `PeriodLog`): Pay period | Employee | Hours worked |
  Lunch (min, total deducted) | Notes, where Notes joins every day's note in the period as `Tue 10/20: …; Thu 10/22: …`.
  Expanding a row shows its days with the In/Out punches. Double-click opens the day (or the period's first day) on the
  Time Card.
- Filters: employee (current team members; former ones show under All) and month. With a month selected, a pay period
  that crosses the month edge only counts that month's days and is labelled "(partial)", so totals match the monthly
  summary. Column headings sort (click again to reverse).
- Export CSV: PeriodStart, PeriodEnd, Employee, HoursWorked, TimeOffHours, TimeOff, LunchDeductMin, Notes, EmployeeID,
  plus a total row.

## Salary hours and time off (Time Card)
- `TimeEntry.salary_hours` (Decimal, optional) and `TimeEntry.hours_type` (`Worked` default | `PTO` | `Holiday` | `Sick`
  | `Closed`), stored in two TimeLog columns at the end: `SalaryHours`, `HoursType` (blank for Worked). Workbooks without
  them load as punches only / Worked. A day with a type other than Worked is kept even with no hours.
- The Time Card shows the hours box and **Type** for everyone. Salaried people type their hours there (`8`, `7.5`,
  `7:30`; replaces the In/Out punches). For hourly people worked hours are always the punches; the box (headed "Time off
  hrs") is only open on a PTO / Holiday / Sick day.
- **Choosing a type:** PTO / Holiday / Sick fills the day's hours from Company Settings → Time off
  (`Company.pto_hours`, `holiday_hours`, `sick_hours`, default 8; editable for a part day); Closed (business closed) is
  always 0 hours (so salaried hours don't look like overwork); going back to Worked clears the time off hours. The hours
  are stored on the day, so changing the setting later doesn't change past days.
- **Pay:** hourly people are paid for PTO / Holiday / Sick hours at their regular rate when that type's switch is on
  (`Company.pto_paid`, `holiday_paid`, `sick_paid`, default on); otherwise the day is only noted. Paid time off never
  counts toward overtime (`HoursSummary.paid_leave`, `PayrollRecord.leave_hours` / `leave_pay`). Closed days are unpaid
  for hourly people. Salaried pay is the salary whatever the days say.
- `calc.entry_hours` = all hours on the day (0 when Closed); `calc.day_hours` = hours **worked** (0 for PTO/Holiday/Sick/Closed), which is what
  pay, overtime, the Year Log, monthly summary, statements and memos count; `calc.leave_hours` = time off. The TimeLog
  `Hours` formula follows the same rule (verified in GridCraft). Time Card footer shows Time off; Year Log has a Time
  off column ("PTO 8.00, Sick 4.00"); the printed time card notes the time off. Salaried pay never depends on hours.
