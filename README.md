# Timebooks

**TimeCard Pro**: a Windows desktop app (Python + tkinter) for tracking contractor hours by year,
running payroll and printing checks to PDF. Data lives in a GridCraft-compatible `.xlsx` workbook.
See [DESIGN.md](DESIGN.md) for the full design contract.

## Setup

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

## Run, test, build

```powershell
.venv\Scripts\python -m timecard                                # run from source
.venv\Scripts\python -m unittest discover -s tests              # tests
.venv\Scripts\python packaging\build.py                         # -> dist\TimeCardPro-Setup.exe
```

> DESIGN.md was written when the venv lived one level up (`..\.venv`); in this repo it is `.venv` at the root.
