"""TimeBooks desktop app. The window lives in timecard/ui/ (one module per tab); this module keeps the entry
point where the installer and ``python -m timecard`` expect it."""
from __future__ import annotations

from .ui.app import App, main  # noqa: F401

__all__ = ["App", "main"]
