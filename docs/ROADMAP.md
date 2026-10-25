# TimeBooks Roadmap

TimeBooks (formerly TimeCard Pro) is a Windows desktop app for small businesses: time cards, paying people, printing
checks, and keeping track of what the business spends and takes in, all in one workbook you own.

> **A clean, simple business app for a small business owner who isn't an accountant.**
> Track the people you pay, the money you spend and the money you take in, and print every check you write,
> all in one workbook on your own computer.

## Principles

1. **Your data stays yours.** One `.xlsx` workbook per business per year, readable in Excel or GridCraft. Nothing is
   sent anywhere; receipt reading happens on your computer.
2. **Only show what the business uses.** Each major area can be turned off and its tab hidden.
3. **Sensitive things sit behind a PIN.** You choose which tabs are locked; Company Settings always is.
4. **One checkbook.** Payroll checks and vendor checks share one check-number sequence and one register.
5. **Automate the boring part, confirm the important part.** Scans fill in the details; a person approves them.
6. **Small, useful releases.** Each release stands on its own and updates in place through Help → Check for Updates.

## Released

| Version | Highlights |
|---|---|
| 1.1 | Time cards, year log, payroll for contractors (hourly or salary), voucher / 3-up / handwritten checks, annual statements, backups, portable USB copy, themes, company branding, Check for Updates |
| 1.2 | Time off (PTO / Holiday / Sick / Closed), lock any tab with the PIN, turn features on or off, salary per year or per pay period |
| 1.3 | Renamed to **TimeBooks**. **Expenses** with receipts: scan from a scanner, photo, PDF, scanner folder or your phone over Wi-Fi; bank statement import and matching; recurring expenses; mileage log; owners as a pay group |
| 1.3.2 | **Clock in / out from a phone** (QR code + personal PIN), a third In/Out pair, W-9 on file, duplicate expense check, exact reading of emailed PDF receipts, installer fits small screens |
| 1.4 | **Vendors**: vendor list, assign unknown vendors (receipts move to the vendor's folder), vendor checks in Pay & Checks on the same checkbook as payroll, vendor 1099 totals; **Accounts**: record which bank account or card paid |

## Coming next

### 1.5 — Money foundations and safety
- Account balances, starting from an opening balance
- Owner draws, contributions and loans, kept out of profit and loss
- Monthly reconcile against the bank statement
- Close a month or year so finished periods can't change by accident
- Change history for every money edit
- Split one expense across categories
- **Restore from backup**, and encrypted backups for cloud folders
- A check between the PC and USB copies of a workbook so work isn't lost between them

### 1.6 — Jobs and time off
- **Jobs**: tag hours, parts and mileage to a job or customer; per-worker commission rates; see what each job cost
  and what it brought in
- **PTO balances**: hours earned, used and left, with a yearly allowance after a waiting period

### 1.7 — Sales
- Record sales and deposits; simple numbered invoices you can print, save or email from your own email program
- Who owes what, with 30/60/90-day aging and printable customer statements
- Sales tax report: collected vs. paid for each filing period

### 1.8 — Reports and taxes
- Profit & loss by month, quarter or year; spending by category and vendor; sales by month and customer
- A tax-professional pack (P&L, expenses by Schedule C category, 1099 totals, annual statements) as PDF and Excel
- Quarterly estimated tax helper and a 1099 reference sheet for your tax preparer
- Equipment and assets list with purchase records, for depreciation and appraisals

### 2.0 — Polish
- Home tab with this month's money in and out, items waiting for review, bills coming due, and reminders
- Search everywhere
- Several businesses in one app, each with its own workbook
- First-run setup wizard, in-app help, and a code-signed installer
- Monthly budgets by category

## Ideas for later

- Reading data from shop estimating software instead of building estimates into TimeBooks
- Direct-deposit (ACH) files for the bank
- Positive pay: send the bank the list of checks written so it only pays those (needs the bank's own file format)
- Importing records from other bookkeeping programs when switching mid-year
- Other languages
- A day that is part worked and part time off
- An optional password-protected workbook (at the cost of opening it in other spreadsheet apps)

## Not planned

- **W-2 payroll** (tax withholding tables and payroll tax forms). Businesses at that stage are better served by a
  full payroll service; TimeBooks pays contractors and salaried people with no withholding by default.

## How releases are made

Each release is planned here first, built in small reviewed steps with automated tests, checked on screen with
sample data, reviewed for money, data-safety and security issues, and then published as a GitHub Release with a
single `TimeBooks-Setup.exe`. Installed copies update through Help → Check for Updates. The technical details of
the workbook and the rules behind each feature are in [DESIGN.md](../DESIGN.md).
