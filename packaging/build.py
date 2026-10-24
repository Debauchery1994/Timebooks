"""Build TimeBooks: the app (one-folder) and a single-file Setup.exe installer.

Run from the timecard/ directory:  ..\\.venv\\Scripts\\python packaging\\build.py
Output: dist\\TimeBooks-Setup.exe
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "packaging"
BUILD = ROOT / "build"
DIST = ROOT / "dist"


def pyinstaller(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--workpath", str(BUILD), "--specpath", str(BUILD), *args], check=True, cwd=ROOT)


def main() -> None:
    shutil.rmtree(DIST, ignore_errors=True)
    icon = str(PKG / "timecard.ico")

    pyinstaller("--name", "TimeBooks", "--windowed", "--onedir", "--icon", icon,
                "--add-data", f"{icon};.", "--paths", str(ROOT),
                "--distpath", str(DIST / "app"), str(PKG / "run_timecard.py"))

    payload = BUILD / "payload.zip"
    app_dir = DIST / "app" / "TimeBooks"
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as z:
        for f in app_dir.rglob("*"):
            z.write(f, f.relative_to(app_dir))

    pyinstaller("--name", "TimeBooks-Setup", "--windowed", "--onefile", "--icon", icon,
                "--add-data", f"{payload};.", "--add-data", f"{icon};.", "--paths", str(ROOT),
                "--distpath", str(DIST), str(PKG / "installer.py"))
    print("Built", DIST / "TimeBooks-Setup.exe")


if __name__ == "__main__":
    main()
