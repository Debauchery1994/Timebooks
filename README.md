# Timebooks

**TimeCard Pro**: a Windows desktop app (Python + tkinter) for tracking contractor hours by year,
running payroll and printing checks to PDF. Data lives in a GridCraft-compatible `.xlsx` workbook.
See [DESIGN.md](DESIGN.md) for the full design contract.
Where it's heading, release by release: [docs/ROADMAP.md](docs/ROADMAP.md).

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

## Releasing an update

The app's **Help → Check for Updates** (and a once-a-day check at startup) reads the latest
[GitHub Release](https://github.com/Debauchery1994/Timebooks/releases) of this repo, which must be public.

1. Bump `__version__` in [timecard/__init__.py](timecard/__init__.py) (the installer uses the same number).
2. Build: `.venv\Scripts\python packaging\build.py`.
3. On GitHub, create a release with the tag `v<version>` (e.g. `v1.2.0`). The description is shown to users as
   "What's new". Attach `dist\TimeCardPro-Setup.exe` with exactly that file name.

The app downloads the attached Setup.exe, checks its size and SHA-256, closes and runs `Setup.exe --update`.
That updates the installed or portable copy in place and restarts the app, keeping workbooks, PIN and settings.
Copies older than 1.1.0 have no update button, so install 1.1.0 by hand once.
