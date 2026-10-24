"""Entry point: ``python -m timecard`` (also used as the PyInstaller script)."""
import sys

try:
    from .app import main
except ImportError:  # run as a top-level script (e.g. PyInstaller entry)
    from timecard.app import main

if __name__ == "__main__":
    sys.exit(main())
