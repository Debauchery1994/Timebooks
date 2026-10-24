"""TimeCard Pro - tkinter GUI.

Tabs: Time Card, Employees, Year Log, Payroll & Checks, Company Settings.
The pay rate is stored in the workbook but is NEVER displayed anywhere in this UI.
"""
from __future__ import annotations

import csv
import io
import dataclasses
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import time as _time
import traceback
import webbrowser
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, simpledialog, ttk

from .models import *  # noqa: F401,F403  (Company, Employee, TimeEntry, PayrollRecord, HoursSummary, ...)
from .models import Company, Employee, HoursSummary, PayrollRecord, TimeEntry
from . import backup, branding, calc, checks, paths, security, statements, updates
from .storage import TimecardBook, WorkbookError

try:
    from . import APP_NAME, __version__
except ImportError:  # pragma: no cover
    APP_NAME, __version__ = "TimeCard Pro", "1.1.2"

# --------------------------------------------------------------------------- constants

GRIDCRAFT_EXE = Path(r"C:\Program Files\GridCraft\gridcraft.exe")
OUTPUT_DIR_NAME = "TimeCard Pro Output"
MAX_RECENT = 8
AUTOSAVE_DELAY_MS = 2000      # save this long after the last change
AUTOSAVE_RETRY_MS = 30_000    # retry while the file is open in another program
FONT_FAMILY = "Segoe UI"


CHECK_LAYOUTS = [
    ("voucher", "Voucher check, 1 per page with 2 stubs (QuickBooks checks)"),
    ("laser_3up", "Laser checks, 3 per page"),
    ("manual_3up", "Manual binder checks, 3 per page with side stub (single-copy only)"),
    ("handwrite", "Write checks by hand (print a list)"),
]
THREE_UP_LAYOUTS = ("laser_3up", "manual_3up")
MANUAL_FIELDS = ("manual_sheet_width", "manual_sheet_height", "manual_stub_width")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


# --------------------------------------------------------------------------- appearance (themes + text size)
#
# All colours come from the live palette P and all fonts are named fonts from FONT_ROLES, so switching the
# theme or text size at runtime only re-configures styles/fonts and repaints classic tk widgets
# (apply_tk_colors) - no window rebuild is needed.

THEME_NAMES = ("Light", "Dark", "High Contrast")
TEXT_SCALES = (100, 125, 150, 175, 200)

THEMES: dict[str, dict] = {
    "Light": dict(
        base="vista", bg="#F0F0F0", fg="#111827", muted="#4B5563", warn="#9A3412", accent="#1F5FAD",
        accent_fg="#FFFFFF", field_bg="#FFFFFF", field_fg="#111827", field_border="#8A9099",
        invalid_bg="#F8D7DA", invalid_fg="#7F1D1D", invalid_border="#B91C1C",
        select_bg="#1F5FAD", select_fg="#FFFFFF", card_bg="#FFFFFF", card_fg="#111827", card_border="#C8CDD3",
        status_bg="#E5E7EB", status_fg="#1F2937", error_fg="#B91C1C", header_bg="#1F3A5F", header_fg="#FFFFFF",
        header_sub="#C9D6E8", header_btn_bg="#2E5283", header_btn_fg="#FFFFFF", year_bg="#2E5283", portable_bg="#B45309",
        portable_fg="#FFFFFF", total_bg="#E3E8EF", inactive_fg="#6B7280", heading_bg="#E9ECF0",
        heading_fg="#111827", grid_head_fg="#374151", disabled_fg="#6B7280", focus="#1F5FAD",
        focus_width=1, border_width=1, bold_all=False),
    "Dark": dict(
        base="clam", bg="#1E1F22", fg="#E8EAED", muted="#AEB4BC", warn="#FDBA74", accent="#7CB8FF",
        accent_fg="#0B1220", field_bg="#2B2D31", field_fg="#F1F3F4", field_border="#6B7078",
        invalid_bg="#6B1A1A", invalid_fg="#FFE4E4", invalid_border="#FF8A8A",
        select_bg="#2F5C8F", select_fg="#FFFFFF", card_bg="#26282C", card_fg="#F1F3F4", card_border="#4A4E55",
        status_bg="#2B2D31", status_fg="#D7DADF", error_fg="#FF9B9B", header_bg="#10233D", header_fg="#FFFFFF",
        header_sub="#B9C9DE", header_btn_bg="#24456F", header_btn_fg="#FFFFFF", year_bg="#24456F", portable_bg="#9A4A06",
        portable_fg="#FFFFFF", total_bg="#33373D", inactive_fg="#9AA0A8", heading_bg="#2F3136",
        heading_fg="#F1F3F4", grid_head_fg="#D0D4DA", disabled_fg="#8A9099", focus="#7CB8FF",
        focus_width=2, border_width=1, bold_all=False),
    "High Contrast": dict(
        base="clam", bg="#000000", fg="#FFFFFF", muted="#FFFF00", warn="#FFD000", accent="#FFFF00",
        accent_fg="#000000", field_bg="#000000", field_fg="#FFFFFF", field_border="#FFFFFF",
        invalid_bg="#B00000", invalid_fg="#FFFFFF", invalid_border="#FF6060",
        select_bg="#FFFF00", select_fg="#000000", card_bg="#000000", card_fg="#FFFFFF", card_border="#FFFFFF",
        status_bg="#000000", status_fg="#FFFFFF", error_fg="#FF8080", header_bg="#000000", header_fg="#FFFFFF",
        header_sub="#FFFF00", header_btn_bg="#FFFF00", header_btn_fg="#000000", year_bg="#000000", portable_bg="#000000",
        portable_fg="#FFD000", total_bg="#1A1A1A", inactive_fg="#00FFFF", heading_bg="#000000",
        heading_fg="#FFFF00", grid_head_fg="#FFFF00", disabled_fg="#C0C0C0", focus="#00FFFF",
        focus_width=3, border_width=2, bold_all=True),
}
P: dict = dict(THEMES["Light"])        # live palette (mutated by apply_appearance)
_appearance = {"theme": "Light", "scale": 100}

# role -> (base size in points, weight, slant, underline)
FONT_ROLES: dict[str, tuple] = {
    "base": (10, "normal", "roman", 0), "bold": (10, "bold", "roman", 0), "italic": (10, "normal", "italic", 0),
    "small": (9, "normal", "roman", 0), "small_bold": (9, "bold", "roman", 0),
    "small_italic": (9, "normal", "italic", 0), "tiny": (8, "normal", "roman", 0),
    "section": (11, "bold", "roman", 0), "big": (16, "bold", "roman", 0), "title": (15, "bold", "roman", 0),
    "year": (13, "bold", "roman", 0), "pin": (12, "normal", "roman", 0), "icon": (36, "normal", "roman", 0),
    "welcome": (18, "bold", "roman", 0), "link": (9, "normal", "roman", 1), "heading": (10, "bold", "roman", 0),
}
_FONTS: dict[str, tkfont.Font] = {}


def F(role: str) -> tkfont.Font:
    """Named font for a role; sizes follow the current text scale."""
    return _FONTS[role]


def scaled(px: int) -> int:
    """Scale a pixel measurement (padding, wraplength, row height) by the text size."""
    return int(round(px * _appearance["scale"] / 100))


def _configure_fonts(root: tk.Misc) -> None:
    k = _appearance["scale"] / 100
    bold_all = P["bold_all"]
    if _FONTS and next(iter(_FONTS.values()))._tk is not root.tk:   # a new Tk root (tests)
        _FONTS.clear()
    for role, (size, weight, slant, underline) in FONT_ROLES.items():
        spec = dict(family=FONT_FAMILY, size=max(6, int(round(size * k))),
                    weight="bold" if bold_all else weight, slant=slant, underline=underline)
        name = "BoldFont" if role == "bold" else f"TC{role}"
        if role not in _FONTS:
            try:
                _FONTS[role] = tkfont.Font(root, name=name, exists=True)
            except tk.TclError:
                _FONTS[role] = tkfont.Font(root, name=name)
        _FONTS[role].configure(**spec)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                 "TkSmallCaptionFont", "TkTooltipFont", "TkFixedFont", "TkIconFont"):
        try:
            f = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue
        size = 9 if name in ("TkSmallCaptionFont", "TkTooltipFont") else 10
        f.configure(family=FONT_FAMILY if name != "TkFixedFont" else "Consolas",
                    size=int(round(size * k)), weight="bold" if bold_all else "normal")


def _configure_styles(root: tk.Misc) -> None:
    style = ttk.Style(root)
    themes = style.theme_names()
    base = P["base"] if P["base"] in themes and _appearance["scale"] <= 125 else "clam"
    if style.theme_use() != base:
        style.theme_use(base)
    bg, fg, muted, accent = P["bg"], P["fg"], P["muted"], P["accent"]
    bw, fw = P["border_width"], P["focus_width"]
    recolor = base != "vista"     # vista draws native widgets and ignores most colours
    style.configure(".", font=F("base"), background=bg, foreground=fg)
    if recolor:
        style.configure(".", fieldbackground=P["field_bg"], selectbackground=P["select_bg"],
                        selectforeground=P["select_fg"], insertcolor=P["field_fg"], troughcolor=P["card_bg"],
                        bordercolor=P["field_border"], lightcolor=bg, darkcolor=bg, focuscolor=P["focus"],
                        arrowcolor=fg, indicatorcolor=P["field_bg"], arrowsize=scaled(13))
        style.map(".", foreground=[("disabled", P["disabled_fg"])],
                  background=[("disabled", bg)])
        style.configure("TButton", background=P["card_bg"], foreground=fg, bordercolor=P["field_border"],
                        borderwidth=bw, focusthickness=fw, focuscolor=P["focus"], padding=(scaled(8), scaled(3)))
        style.map("TButton", background=[("pressed", P["select_bg"]), ("active", P["total_bg"])],
                  foreground=[("pressed", P["select_fg"]), ("disabled", P["disabled_fg"])],
                  bordercolor=[("focus", P["focus"])])
        style.configure("Accent.TButton", background=accent, foreground=P["accent_fg"])
        style.map("Accent.TButton", background=[("pressed", P["select_bg"]), ("active", P["focus"]),
                                                ("disabled", P["card_bg"])],
                  foreground=[("disabled", P["disabled_fg"]), ("active", P["accent_fg"])])
        for w in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(w, fieldbackground=P["field_bg"], foreground=P["field_fg"],
                            bordercolor=P["field_border"], lightcolor=P["field_bg"], darkcolor=P["field_bg"],
                            insertcolor=P["field_fg"], arrowcolor=fg, borderwidth=bw,
                            selectbackground=P["select_bg"], selectforeground=P["select_fg"],
                            arrowsize=scaled(13))
            style.map(w, bordercolor=[("focus", P["focus"])], lightcolor=[("focus", P["focus"])],
                      fieldbackground=[("readonly", P["field_bg"]), ("disabled", bg)],
                      foreground=[("disabled", P["disabled_fg"]), ("readonly", P["field_fg"])],
                      selectbackground=[("readonly", P["select_bg"])],
                      selectforeground=[("readonly", P["select_fg"])])
        for w in ("TCheckbutton", "TRadiobutton"):
            style.configure(w, background=bg, foreground=fg, indicatorbackground=P["field_bg"],
                            indicatorforeground=fg, focuscolor=P["focus"], focusthickness=fw)
            style.map(w, background=[("active", bg)], indicatorbackground=[("selected", P["field_bg"])],
                      foreground=[("disabled", P["disabled_fg"])])
        style.configure("TNotebook", background=bg, bordercolor=P["card_border"], tabmargins=(2, 4, 2, 0))
        style.configure("TNotebook.Tab", background=P["card_bg"], foreground=fg, bordercolor=P["card_border"],
                        focuscolor=P["focus"])
        style.map("TNotebook.Tab", background=[("selected", P["select_bg"])],
                  foreground=[("selected", P["select_fg"])], expand=[("selected", (1, 1, 1, 0))])
        style.configure("TLabelframe", background=bg, bordercolor=P["card_border"], borderwidth=bw,
                        lightcolor=bg, darkcolor=bg)
        style.configure("TScrollbar", background=P["card_bg"], troughcolor=bg, arrowcolor=fg,
                        bordercolor=P["card_border"], arrowsize=scaled(14))
        style.configure("TSeparator", background=P["card_border"])
        style.configure("TPanedwindow", background=bg)
        style.configure("Sash", background=P["card_border"])
        style.configure("Treeview.Heading", background=P["heading_bg"], foreground=P["heading_fg"],
                        bordercolor=P["card_border"], borderwidth=bw)
        style.map("Treeview.Heading", background=[("active", P["total_bg"])])
        style.configure("Toolbutton", background=bg, foreground=accent)
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground=fg)
    style.configure("TLabelframe.Label", font=F("bold"), background=bg, foreground=fg)
    style.configure("TNotebook.Tab", padding=(scaled(10), scaled(5)), font=F("base"))
    style.configure("Treeview", rowheight=F("base").metrics("linespace") + scaled(8), font=F("base"),
                    background=P["field_bg"], fieldbackground=P["field_bg"], foreground=P["field_fg"])
    style.map("Treeview", background=[("selected", P["select_bg"])], foreground=[("selected", P["select_fg"])])
    style.configure("Treeview.Heading", font=F("heading"))
    style.configure("Muted.TLabel", foreground=muted)
    style.configure("Warn.TLabel", foreground=P["warn"], font=F("italic"))
    style.configure("Error.TLabel", foreground=P["error_fg"], font=F("bold"))
    style.configure("Bold.TLabel", font=F("bold"))
    style.configure("Section.TLabel", font=F("section"))
    style.configure("Welcome.TLabel", font=F("welcome"))
    style.configure("Icon.TLabel", font=F("icon"))
    style.configure("LockTitle.TLabel", font=F("title"))
    style.configure("GridHead.TLabel", font=F("bold"), foreground=P["grid_head_fg"])
    style.configure("Weekend.TLabel", foreground=muted)
    style.configure("Today.TLabel", foreground=accent, font=F("bold"))
    style.configure("Hours.TLabel", font=F("bold"))
    style.configure("Subtotal.TLabel", foreground=muted, font=F("small_italic"))
    style.configure("SubtotalVal.TLabel", foreground=accent, font=F("bold"))
    style.configure("Accent.TButton", font=F("bold"))
    style.configure("Small.TButton", font=F("tiny"), padding=(2, 0))
    style.configure("Link.TButton", foreground=accent, relief="flat", borderwidth=0, padding=(2, 0),
                    font=F("link"), background=bg)
    style.map("Link.TButton", foreground=[("active", accent), ("focus", accent)], background=[("active", bg)])
    style.configure("Card.TFrame", background=P["card_bg"], relief="solid", borderwidth=P["border_width"],
                    bordercolor=P["card_border"])
    style.configure("CardInner.TFrame", background=P["card_bg"], borderwidth=0, relief="flat")
    style.configure("CardMuted.TLabel", background=P["card_bg"], foreground=muted, font=F("small"))
    style.configure("CardBig.TLabel", background=P["card_bg"], foreground=P["card_fg"], font=F("big"))
    style.configure("Status.TFrame", background=P["status_bg"])
    style.configure("Status.TLabel", background=P["status_bg"], foreground=P["status_fg"], font=F("small"))
    style.configure("StatusErr.TLabel", background=P["status_bg"], foreground=P["error_fg"], font=F("small_bold"))
    # dropdown lists of comboboxes created from now on
    for opt, val in (("background", P["field_bg"]), ("foreground", P["field_fg"]),
                     ("selectBackground", P["select_bg"]), ("selectForeground", P["select_fg"])):
        root.option_add(f"*TCombobox*Listbox.{opt}", val)
    root.option_add("*TCombobox*Listbox.font", F("base"))
    # classic tk widgets created later (simpledialog prompts, etc.)
    for opt, val in (("background", bg), ("foreground", fg), ("activeBackground", P["select_bg"]),
                     ("activeForeground", P["select_fg"]), ("selectBackground", P["select_bg"]),
                     ("selectForeground", P["select_fg"]), ("insertBackground", P["field_fg"]),
                     ("highlightColor", P["focus"]), ("highlightBackground", bg),
                     ("disabledForeground", P["disabled_fg"])):
        root.option_add(f"*{opt}", val)
    root.option_add("*Entry.background", P["field_bg"])
    root.option_add("*Entry.foreground", P["field_fg"])
    # classic tk widgets only - a generic "*font" would also override every ttk style font
    for cls in ("Label", "Entry", "Button", "Listbox", "Text", "Menu", "Checkbutton", "Radiobutton", "Spinbox"):
        root.option_add(f"*{cls}.font", F("base"))


TREE_TAGS = {
    "total": lambda: dict(font=F("bold"), background=P["total_bg"], foreground=P["fg"]),
    "inactive": lambda: dict(foreground=P["inactive_fg"], font=F("italic")),
    "former": lambda: dict(foreground=P["inactive_fg"], font=F("italic")),
    "norate": lambda: dict(foreground=P["warn"]),
    "dup": lambda: dict(foreground=P["warn"], font=F("bold")),
    "void": lambda: dict(foreground=P["inactive_fg"], font=F("italic")),
}


def style_tree(tree: ttk.Treeview) -> None:
    for tag, spec in TREE_TAGS.items():
        tree.tag_configure(tag, **spec())


def paint_cell(w: tk.Widget) -> None:
    """Colours for a classic tk.Entry/Text/Spinbox; keeps the 'invalid' state distinguishable."""
    invalid = getattr(w, "_tc_invalid", False)
    try:
        w.configure(bg=P["invalid_bg"] if invalid else P["field_bg"],
                    fg=P["invalid_fg"] if invalid else P["field_fg"],
                    insertbackground=P["invalid_fg"] if invalid else P["field_fg"],
                    highlightcolor=P["focus"], highlightthickness=max(1, P["focus_width"]),
                    highlightbackground=P["invalid_border"] if invalid else P["field_border"],
                    selectbackground=P["select_bg"], selectforeground=P["select_fg"])
    except tk.TclError:
        pass


def set_invalid(w: tk.Widget, invalid: bool) -> None:
    w._tc_invalid = invalid
    paint_cell(w)


def apply_tk_colors(widget: tk.Misc) -> None:
    """Repaint classic tk widgets (and ttk tree tags / combobox dropdowns) under `widget`."""
    stack = [widget]
    while stack:
        w = stack.pop()
        try:
            stack.extend(w.winfo_children())
            role = getattr(w, "_tc_role", None)
            cls = w.winfo_class()
            if role in HEADER_ROLES:
                fg_key, bg_key = HEADER_ROLES[role]
                kw = dict(bg=P[bg_key])
                if fg_key:
                    kw["fg"] = P[fg_key]
                if role == "header_btn":
                    kw.update(disabledforeground=_mix(P["header_btn_bg"], P["header_btn_fg"], 0.55),
                          activebackground=P["select_bg"], activeforeground=P["select_fg"],
                              highlightcolor=P["focus"], highlightbackground=P["header_bg"],
                              highlightthickness=max(1, P["focus_width"]), relief="flat", bd=0)
                    if P["bold_all"]:   # high contrast: always-visible white outline, cyan when focused
                        kw.update(highlightbackground=P["header_fg"], highlightthickness=2)
                if role in ("year", "portable") and P["bold_all"]:
                    kw.update(highlightthickness=1, highlightbackground=P["header_fg"])
                w.configure(**kw)
            elif role == "keep":
                pass
            elif cls in ("Entry", "Text", "Spinbox", "Listbox"):
                paint_cell(w)
            elif cls in ("Frame", "Toplevel", "Canvas", "Tk"):
                w.configure(background=P["bg"])
            elif cls in ("Label", "Button", "Checkbutton", "Radiobutton"):
                w.configure(background=P["bg"], foreground=P["fg"])
            elif cls == "Menu":
                w.configure(background=P["field_bg"], foreground=P["field_fg"],
                            activebackground=P["select_bg"], activeforeground=P["select_fg"],
                            selectcolor=P["field_fg"], font=F("base"))
            elif cls == "Treeview":
                style_tree(w)
                base_w = getattr(w, "_tc_basew", None)
                if base_w is None:
                    base_w = w._tc_basew = {c: int(w.column(c, "width")) for c in w["columns"]}
                for c, bw_ in base_w.items():
                    w.column(c, width=scaled(bw_), minwidth=scaled(min(bw_, 40)))
            elif cls == "TCombobox":
                try:
                    pd = w.tk.call("ttk::combobox::PopdownWindow", w)
                    w.tk.call(f"{pd}.f.l", "configure", "-background", P["field_bg"], "-foreground",
                              P["field_fg"], "-selectbackground", P["select_bg"], "-selectforeground",
                              P["select_fg"], "-font", F("base"))
                except tk.TclError:
                    pass
        except tk.TclError:
            continue


# role -> (fg palette key or None, bg palette key)
HEADER_ROLES = {
    "header": (None, "header_bg"), "header_title": ("header_fg", "header_bg"),
    "header_sub": ("header_sub", "header_bg"), "header_btn": ("header_btn_fg", "header_btn_bg"),
    "stripe": (None, "stripe_bg"),
    "year": ("header_fg", "year_bg"), "portable": ("portable_fg", "portable_bg"),
}


_brand = {"color": ""}


def _mix(a: str, b: str, t: float) -> str:
    a, b = branding.brand_color(a), branding.brand_color(b)
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))


def _apply_brand_palette() -> None:
    """Light theme: header in the company's brand colour with readable (black/white) text.
    Dark / High Contrast keep their own header colours and only show a thin brand stripe."""
    color = _brand["color"]
    valid = branding.is_valid_color(color or "")
    P["stripe_bg"] = branding.brand_color(color) if valid else P["header_bg"]
    if _appearance["theme"] == "Light" and valid:
        c = branding.brand_color(color)
        fg = branding.text_on(c)
        btn = _mix(c, fg, 0.18)
        P.update(header_bg=c, header_fg=fg, header_sub=fg, year_bg=_mix(c, fg, 0.10),
                 header_btn_bg=btn, header_btn_fg=branding.text_on(btn))


def tag_role(w: tk.Widget, role: str) -> tk.Widget:
    w._tc_role = role
    return w


def logo_photo(master: tk.Misc, png: bytes, max_w: int, max_h: int):
    """PhotoImage of the logo fitted into max_w x max_h (caller must keep a reference), or None."""
    if not png:
        return None
    try:
        from PIL import Image, ImageTk
        w, h = branding.fit_logo(png, max_w, max_h)
        img = Image.open(io.BytesIO(png)).convert("RGBA")
        img = img.resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)
        return ImageTk.PhotoImage(img, master=master)
    except Exception:  # noqa: BLE001 - a bad logo must never break the window
        log.exception("Could not display logo")
        return None


def apply_appearance(root: tk.Misc, theme: str, scale: int) -> None:
    theme = theme if theme in THEMES else "Light"
    scale = min(TEXT_SCALES, key=lambda s: abs(s - int(scale or 100)))
    _appearance.update(theme=theme, scale=scale)
    P.clear()
    P.update(THEMES[theme])
    _apply_brand_palette()
    _configure_fonts(root)
    _configure_styles(root)
    apply_tk_colors(root)


SETTINGS_DIR = paths.settings_dir()
SETTINGS_PATH = SETTINGS_DIR / "settings.json"
LOG_PATH = SETTINGS_DIR / "app.log"

log = logging.getLogger("timecard")


def setup_logging() -> None:
    if log.handlers:
        return
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2,
                                                       encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    except OSError:
        log.addHandler(logging.NullHandler())
    log.setLevel(logging.INFO)


# --------------------------------------------------------------------------- helpers

def pay_type_label(pay_type: str, salary_basis: str = "Annual") -> str:
    """Display label for the stored PayType value ("Hourly" / "Salary") and, for salaries, its basis."""
    if (pay_type or "").strip().lower() != "salary":
        return "Hourly"
    per_period = (salary_basis or "").strip().lower().replace(" ", "") == "perperiod"
    return "Salaried (per period)" if per_period else "Salaried (per year)"


def salary_amount_text(emp: Employee, company: Company) -> str:
    """'an annual salary' / 'a salary per pay period (Weekly)' for messages."""
    if calc.salary_is_per_period(emp):
        return f"a salary per pay period ({company.pay_period.lower()})"
    return "an annual salary"


def is_salaried(emp: Employee) -> bool:
    return (emp.pay_type or "").strip().lower() == "salary"


def recent_labels(recent: list[str]) -> list[str]:
    """Recent Files labels: the file name only (the full path would show visitors where the unlocked workbook
    lives); the folder name is added only when two files share a name."""
    names = [Path(p).name for p in recent]
    return [f"{n}  ({Path(p).parent.name})" if names.count(n) > 1 else n for p, n in zip(recent, names)]


def _has_pay_hours(hours: HoursSummary) -> bool:
    """Hours worked or paid time off: something an hourly person is paid for."""
    return hours.total > 0 or hours.paid_leave > 0


def fmt_money(x) -> str:
    return f"${Decimal(x):,.2f}"


def fmt_hours(x) -> str:
    return f"{Decimal(x):,.2f}"


def fmt_period(p: tuple[date, date]) -> str:
    s, e = p
    return f"{s:%a %m/%d/%Y}  -  {e:%a %m/%d/%Y}"


def parse_date(text: str) -> date:
    text = text.strip()
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, f).date()
        except ValueError:
            pass
    raise ValueError(f"Not a date: {text!r} (use YYYY-MM-DD or MM/DD/YYYY)")


def parse_money(text: str) -> Decimal:
    cleaned = text.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return Decimal("0")
    try:
        return calc.money(Decimal(cleaned))
    except InvalidOperation:
        raise ValueError(f"Not an amount: {text!r}") from None


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "-", name).strip() or "output"


def documents_dir() -> Path:
    return paths.documents_dir()


def period_index_for(periods: list[tuple[date, date]], d: date) -> int:
    for i, (s, e) in enumerate(periods):
        if s <= d <= e:
            return i
    if periods and d > periods[-1][1]:
        return len(periods) - 1
    return 0


def open_file(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except (OSError, AttributeError) as exc:
        messagebox.showwarning(APP_NAME, f"The file was created but could not be opened:\n{path}\n\n{exc}")


# --------------------------------------------------------------------------- settings

class Settings:
    def __init__(self) -> None:
        self.data: dict = {"last_workbook": "", "geometry": "", "zoomed": False, "recent": [],
                           "backup_dir": "", "backup_on_save": False, "backup_keep": backup.DEFAULT_KEEP,
                           "last_backup": "", "theme": "Light", "text_scale": 100,
                           "update_check_on_start": True, "last_update_check": "", "skip_version": "",
                           "autosave": True}
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            tmp = SETTINGS_PATH.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, SETTINGS_PATH)
        except OSError:
            log.exception("Could not save settings")

    # Paths are stored with paths.store_path so a portable copy survives drive-letter changes.
    @property
    def recent(self) -> list[str]:
        r = self.data.get("recent") or []
        return [paths.resolve_path(p) for p in r if isinstance(p, str)]

    @property
    def last_workbook(self) -> str:
        return paths.resolve_path(self.data.get("last_workbook") or "")

    @last_workbook.setter
    def last_workbook(self, path) -> None:
        self.data["last_workbook"] = paths.store_path(path) if path else ""

    @property
    def backup_dir(self) -> str:
        return paths.resolve_path(self.data.get("backup_dir") or "")

    @backup_dir.setter
    def backup_dir(self, path) -> None:
        self.data["backup_dir"] = paths.store_path(path) if path else ""

    def add_recent(self, path: Path) -> None:
        p = str(Path(path))
        r = [x for x in self.recent if os.path.normcase(x) != os.path.normcase(p)]
        self.data["recent"] = [paths.store_path(x) for x in [p] + r[: MAX_RECENT - 1]]
        self.last_workbook = p
        self.save()


# --------------------------------------------------------------------------- reusable widgets

_FLOWS: list = []


class FlowFrame(ttk.Frame):
    """Lays out groups (sub-frames made with .group()) left to right, wrapping to new lines when the
    window is too narrow - keeps toolbars usable at large text sizes."""

    def __init__(self, master, gap: int = 18, **kw):
        super().__init__(master, **kw)
        self.gap = gap
        self.groups: list[ttk.Frame] = []
        self._last = None
        self.bind("<Configure>", lambda e: self.relayout())
        _FLOWS.append(self)

    def group(self) -> ttk.Frame:
        g = ttk.Frame(self)
        self.groups.append(g)
        return g

    def relayout(self) -> None:
        try:
            width = self.winfo_width()
        except tk.TclError:
            return
        if width <= 1:
            width = self.winfo_toplevel().winfo_width() or 1000
        widths = tuple(g.winfo_reqwidth() for g in self.groups)
        if self._last == (width, widths):
            return
        self._last = (width, widths)
        row = col = x = 0
        gap = scaled(self.gap)
        for g, w in zip(self.groups, widths):
            if col and x + w > width:
                row, col, x = row + 1, 0, 0
            g.grid(row=row, column=col, sticky="w", padx=(0, gap), pady=(0, scaled(4)))
            x += w + gap
            col += 1
        for c in range(len(self.groups) + 1):
            self.columnconfigure(c, weight=0)


def relayout_flows() -> None:
    for f in list(_FLOWS):
        try:
            if f.winfo_exists():
                f._last = None
                f.relayout()
            else:
                _FLOWS.remove(f)
        except tk.TclError:
            _FLOWS.remove(f)


class ScrollFrame(ttk.Frame):
    """Scrollable frame (vertical, plus horizontal when the content is wider); put children in .inner."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=P["bg"])
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self._xset)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", lambda e: self._fit())
        self.canvas.bind("<Configure>", lambda e: self._fit())

    def _fit(self) -> None:
        """Inner frame fills the canvas width, but may be wider (then a horizontal scrollbar appears)."""
        try:
            want = max(self.canvas.winfo_width(), self.inner.winfo_reqwidth())
            self.canvas.itemconfigure(self._win, width=want)
            self.canvas.configure(scrollregion=(0, 0, want, max(self.inner.winfo_reqheight(), 1)))
        except tk.TclError:
            pass

    def _xset(self, lo, hi) -> None:
        self.hsb.set(lo, hi)
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.hsb.grid_remove()
        else:
            self.hsb.grid(row=1, column=0, sticky="ew")
        self.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

    def scroll_into_view(self, widget) -> None:
        try:
            h = self.inner.winfo_height()
            if h <= 1:
                return
            y = widget.winfo_rooty() - self.inner.winfo_rooty()
            wh = widget.winfo_height()
            top = self.canvas.canvasy(0)
            view_h = self.canvas.winfo_height()
            if y < top:
                self.canvas.yview_moveto(max(0, y - 30) / h)
            elif y + wh > top + view_h:
                self.canvas.yview_moveto((y + wh - view_h + 30) / h)
        except tk.TclError:
            pass

    def scroll_top(self) -> None:
        self.canvas.yview_moveto(0)


class ModalDialog(tk.Toplevel):
    """Simple OK/Cancel modal. Subclasses implement body(frame) and apply() -> bool."""

    def __init__(self, master, title: str):
        super().__init__(master)
        self.withdraw()
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.result = None
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        self.initial_focus = self.body(body)
        btns = ttk.Frame(outer, padding=(0, 12, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", command=self.cancel).pack(side="right")
        ttk.Button(btns, text="OK", style="Accent.TButton", command=self.ok).pack(side="right", padx=(0, 8))
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        apply_tk_colors(self)
        self.update_idletasks()
        top = master.winfo_toplevel()
        x = top.winfo_rootx() + max(0, (top.winfo_width() - self.winfo_reqwidth()) // 2)
        y = top.winfo_rooty() + max(0, (top.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.grab_set()
        (self.initial_focus or self).focus_set()

    def body(self, frame):  # pragma: no cover - overridden
        return None

    def apply(self) -> bool:  # pragma: no cover - overridden
        return True

    def ok(self):
        if self.apply():
            self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result


class NewYearDialog(ModalDialog):
    def __init__(self, master, current: TimecardBook | None):
        self.current = current
        super().__init__(master, "New Year Workbook")

    def body(self, f):
        c = self.current.company if self.current else Company()
        default_year = date.today().year
        if self.current and self.current.company.year >= default_year:
            default_year = self.current.company.year + 1
        self.v_name = tk.StringVar(value=c.company_name if self.current else "")
        self.v_year = tk.StringVar(value=str(default_year))
        self.v_period = tk.StringVar(value=c.pay_period)
        self.v_week = tk.StringVar(value=c.week_start)
        self.v_carry = tk.BooleanVar(value=self.current is not None)
        ttk.Label(f, text="Create a workbook for one business and one calendar year.",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(f, text="Company name").grid(row=1, column=0, sticky="w", pady=4)
        e = ttk.Entry(f, textvariable=self.v_name, width=36)
        e.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(f, text="Year").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=2000, to=2100, textvariable=self.v_year, width=8).grid(row=2, column=1, sticky="w")
        ttk.Label(f, text="Pay period").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_period, state="readonly", width=14,
                     values=list(calc.PAY_PERIODS)).grid(row=3, column=1, sticky="w")
        ttk.Label(f, text="Week starts on").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_week, state="readonly", width=14,
                     values=list(calc.WEEK_STARTS)).grid(row=4, column=1, sticky="w")
        if self.current is not None:
            ttk.Checkbutton(f, text="Carry over company settings and employees from the open workbook",
                            variable=self.v_carry).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        f.columnconfigure(1, weight=1)
        return e

    def apply(self) -> bool:
        name = self.v_name.get().strip()
        if not name:
            messagebox.showwarning("New Year Workbook", "Enter the company name.", parent=self)
            return False
        try:
            year = int(self.v_year.get())
            if not 2000 <= year <= 2100:
                raise ValueError
        except ValueError:
            messagebox.showwarning("New Year Workbook", "Enter a valid year.", parent=self)
            return False
        self.result = dict(name=name, year=year, pay_period=self.v_period.get(),
                           week_start=self.v_week.get(), carry=bool(self.v_carry.get()))
        return True


class PayRateDialog(ModalDialog):
    """Masked entry for the pay rate. The value is never displayed."""

    def __init__(self, master, book: TimecardBook, emp: Employee):
        self.book, self.emp = book, emp
        self.salaried = is_salaried(emp)
        self.per_period = self.salaried and calc.salary_is_per_period(emp)
        super().__init__(master, ("Set Salary per Pay Period" if self.per_period else "Set Annual Salary")
                         if self.salaried else "Set Hourly Rate")

    def body(self, f):
        period = self.book.company.pay_period.lower()
        what = (f"salary per pay period ({period})" if self.per_period else "annual salary") if self.salaried \
            else "hourly rate"
        ttk.Label(f, text=f"Set the {what} for {self.emp.full_name}.").grid(row=0, column=0, columnspan=2,
                                                                            sticky="w")
        ttk.Label(f, text="The rate is saved in the workbook and is never shown on screen.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.e1 = ttk.Entry(f, show="\u2022", width=20)
        self.e2 = ttk.Entry(f, show="\u2022", width=20)
        label = ("Salary per pay period" if self.per_period else "Annual salary") if self.salaried \
            else "Hourly rate"
        ttk.Label(f, text=label).grid(row=2, column=0, sticky="w", pady=4)
        self.e1.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(f, text=f"Confirm {label.lower()}").grid(row=3, column=0, sticky="w", pady=4)
        self.e2.grid(row=3, column=1, sticky="w", pady=4)
        if self.salaried:
            note = (f"Each check is this amount, before adjustments (pay period: {period})." if self.per_period
                    else "Each check is the annual salary divided by the number of pay periods in the year.")
            ttk.Label(f, text=note, style="Muted.TLabel", wraplength=340).grid(row=4, column=0, columnspan=2,
                                                                               sticky="w", pady=(6, 0))
        return self.e1

    def apply(self) -> bool:
        a, b = self.e1.get(), self.e2.get()
        try:
            rate = parse_money(a)
        except ValueError:
            rate = None
        if rate is None or rate <= 0:
            messagebox.showwarning("Set Pay Rate", "Enter a valid amount greater than zero.", parent=self)
            self.e1.delete(0, "end")
            self.e2.delete(0, "end")
            self.e1.focus_set()
            return False
        if a.strip() != b.strip():
            messagebox.showwarning("Set Pay Rate", "The two entries do not match. Please re-enter.", parent=self)
            self.e1.delete(0, "end")
            self.e2.delete(0, "end")
            self.e1.focus_set()
            return False
        self.book.set_pay_rate(self.emp.employee_id, rate)
        self.e1.delete(0, "end")
        self.e2.delete(0, "end")
        self.result = True
        return True


# --------------------------------------------------------------------------- Time Card tab

class _DayRow:
    __slots__ = ("d", "eid", "cells", "lunch", "notes", "hours_lbl", "day_lbl", "salary", "kind")

    def __init__(self, d: date, eid: str):
        self.d, self.eid = d, eid
        self.cells: dict[str, tk.Entry] = {}
        self.lunch: ttk.Spinbox | None = None
        self.notes: tk.Entry | None = None
        self.salary: tk.Entry | None = None       # typed hours: salaried hours, or a time off day's hours
        self.kind: ttk.Combobox | None = None     # Worked / PTO / Holiday / Sick / Closed
        self.hours_lbl: ttk.Label | None = None
        self.day_lbl: ttk.Label | None = None


class TimeCardTab(ttk.Frame):
    TIME_FIELDS = ("in1", "out1", "in2", "out2")

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.emp_ids: list[str] = []
        self.periods: list[tuple[date, date]] = []
        self.rows: list[_DayRow] = []
        self.week_lbls: dict[date, ttk.Label] = {}
        self.current_row = 0
        self.commit_fns: dict[str, callable] = {}

        top = FlowFrame(self)
        top.pack(fill="x")
        g1 = top.group()
        ttk.Label(g1, text="Employee").pack(side="left")
        self.v_emp = tk.StringVar()
        self.cb_emp = ttk.Combobox(g1, textvariable=self.v_emp, state="readonly", width=26)
        self.cb_emp.pack(side="left", padx=(6, 0))
        self.cb_emp.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
        g2 = top.group()
        ttk.Label(g2, text="Pay period").pack(side="left")
        ttk.Button(g2, text="\u25C0", width=3, command=lambda: self.step_period(-1)).pack(side="left", padx=(6, 2))
        self.v_period = tk.StringVar()
        self.cb_period = ttk.Combobox(g2, textvariable=self.v_period, state="readonly", width=31)
        self.cb_period.pack(side="left")
        self.cb_period.bind("<<ComboboxSelected>>", lambda e: self.rebuild())
        ttk.Button(g2, text="\u25B6", width=3, command=lambda: self.step_period(1)).pack(side="left", padx=(2, 6))
        ttk.Button(g2, text="Today", command=self.go_today).pack(side="left")

        actions = FlowFrame(self, gap=8, padding=(0, 8, 0, 0))
        actions.pack(fill="x")
        a1 = actions.group()
        ttk.Button(a1, text="Copy previous day", command=self.copy_previous_day).pack(side="left")
        ttk.Button(a1, text="Clear day", command=self.clear_day).pack(side="left", padx=6)
        a2 = actions.group()
        ttk.Button(a2, text="Print time card", style="Accent.TButton",
                   command=self.print_timecard).pack(side="left")
        ttk.Label(self, text="Type times like 8, 830a, 5:15 pm or 17:15. Tab / Enter moves to the next cell.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.v_salary_hint = tk.StringVar(value="")   # set in rebuild() for salaried people
        hint = ttk.Label(self, textvariable=self.v_salary_hint, style="Muted.TLabel", justify="left")
        hint.pack(anchor="w", fill="x", pady=(0, 6))
        hint.bind("<Configure>", lambda e: hint.configure(wraplength=max(e.width - 8, 200)))

        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        self.grid_frame = self.scroll.inner

        foot = ttk.Frame(self, style="Card.TFrame", padding=(14, 10))
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        self.scroll.pack_forget()   # re-pack after the footer so the footer always keeps its height
        self.scroll.pack(fill="both", expand=True)
        self.v_reg = tk.StringVar(value="0.00")
        self.v_ot = tk.StringVar(value="0.00")
        self.v_tot = tk.StringVar(value="0.00")
        self.v_leave = tk.StringVar(value="0.00")
        self.foot_boxes: dict[str, ttk.Frame] = {}
        for key, label, var in (("reg", "Regular hours", self.v_reg), ("ot", "Overtime hours", self.v_ot),
                                ("tot", "Total hours worked", self.v_tot), ("leave", "Time off (PTO etc.)",
                                                                           self.v_leave)):
            box = ttk.Frame(foot, style="CardInner.TFrame")
            ttk.Label(box, text=label, style="CardMuted.TLabel").pack(anchor="w")
            ttk.Label(box, textvariable=var, style="CardBig.TLabel").pack(anchor="w")
            self.foot_boxes[key] = box
        self._foot_layout: tuple | None = None
        self._layout_footer()

    def _layout_footer(self, show_leave: bool = False) -> None:
        """Regular / Overtime / Total when overtime is enabled, otherwise just Total; plus Time off for salaried
        people or when there is any."""
        ot = bool(self.book is not None and getattr(self.book.company, "overtime_enabled", False))
        if (ot, show_leave) == self._foot_layout:
            return
        self._foot_layout = (ot, show_leave)
        for box in self.foot_boxes.values():
            box.pack_forget()
        keys = ("reg", "ot", "tot") if ot else ("tot",)
        for key in keys + (("leave",) if show_leave else ()):
            self.foot_boxes[key].pack(side="left", padx=(0, 36))

    # ----- data refresh
    @property
    def book(self) -> TimecardBook | None:
        return self.app.book

    def refresh(self, keep_selection: bool = True) -> None:
        if self.book is None:
            return
        old_eid = self.selected_eid() if keep_selection else None
        old_period = self.selected_period() if keep_selection else None
        self._load_employee_list(old_eid)
        self.periods = calc.periods_in_year(self.book.company)
        self.cb_period["values"] = [fmt_period(p) for p in self.periods]
        if old_period in self.periods:
            idx = self.periods.index(old_period)
        else:
            idx = period_index_for(self.periods, date.today())
        if self.periods:
            self.cb_period.current(idx)
        self.rebuild()

    def _load_employee_list(self, want: str | None = None) -> None:
        # former team members (Active = No) are kept in the workbook but never offered here
        emps = self.book.active_employees()
        self.emp_ids = [e.employee_id for e in emps]
        labels = [f"{e.display_name}  ({e.employee_id})" for e in emps]
        self.cb_emp["values"] = labels
        if want in self.emp_ids:
            self.cb_emp.current(self.emp_ids.index(want))
        elif self.emp_ids:
            self.cb_emp.current(0)
        else:
            self.v_emp.set("")

    def selected_eid(self) -> str | None:
        i = self.cb_emp.current()
        return self.emp_ids[i] if 0 <= i < len(self.emp_ids) else None

    def selected_period(self) -> tuple[date, date] | None:
        i = self.cb_period.current()
        return self.periods[i] if 0 <= i < len(self.periods) else None

    def step_period(self, delta: int) -> None:
        self.commit_pending()
        i = self.cb_period.current() + delta
        if 0 <= i < len(self.periods):
            self.cb_period.current(i)
            self.rebuild()

    def go_today(self) -> None:
        self.commit_pending()
        if self.periods:
            self.cb_period.current(period_index_for(self.periods, date.today()))
            self.rebuild()
            self.focus_date(date.today())

    def show(self, eid: str, d: date) -> None:
        """Jump to an employee/day (used by Year Log double-click). Former team members are not shown."""
        self.commit_pending()
        emp = self.book.employees.get(eid)
        if emp is None or not emp.active:
            name = emp.full_name if emp else eid
            self.app.status(f"{name} is no longer with the company; their time card is not shown "
                            "(history stays in the Year Log).", error=True)
            return
        self._load_employee_list(eid)
        idx = period_index_for(self.periods, d)
        if self.periods:
            self.cb_period.current(idx)
        self.rebuild()
        self.focus_date(d)

    def focus_date(self, d: date) -> None:
        for i, r in enumerate(self.rows):
            if r.d == d:
                self.current_row = i
                self.update_idletasks()
                r.cells["in1"].focus_set()
                self.scroll.scroll_into_view(r.cells["in1"])
                return

    # ----- grid
    def rebuild(self) -> None:
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.rows.clear()
        self.week_lbls.clear()
        self.commit_fns.clear()
        self.current_row = 0
        eid, period = self.selected_eid(), self.selected_period()
        g = self.grid_frame
        if self.book is None or eid is None or period is None:
            ttk.Label(g, text="Add an employee on the Employees tab to start a time card.",
                      style="Muted.TLabel", padding=20).grid(row=0, column=0)
            self.update_totals()
            return
        start, end = period
        emp = self.book.employees.get(eid)
        # Everyone gets a Type box (Worked / PTO / Holiday / Sick / Closed). Salaried people type their hours; for
        # hourly people worked hours are the punches and the hours box is only for a time off day's hours.
        salaried = self._salaried = emp is not None and is_salaried(emp)
        heads = ["Day / Date", "In", "Out", "In", "Out", "Lunch Deduct (min)", "",
                 "Salary hours" if salaried else "Time off hrs", "Type", "Hours", "Notes"]
        c_hours, c_notes, ncols = len(heads) - 2, len(heads) - 1, len(heads)
        for c, h in enumerate(heads):
            ttk.Label(g, text=h, style="GridHead.TLabel").grid(row=0, column=c, sticky="w", padx=4, pady=(0, 4))
        ttk.Separator(g).grid(row=1, column=0, columnspan=ncols, sticky="ew", pady=(0, 4))
        for c in range(ncols):
            g.columnconfigure(c, weight=0)
        g.columnconfigure(c_notes, weight=1)
        self.v_salary_hint.set(
            "Salaried: type the day's hours in Salary hours (it replaces In/Out). " if salaried else
            "Worked hours come from the In/Out times. ")
        self.v_salary_hint.set(self.v_salary_hint.get() +
                               "Choose PTO, Holiday or Sick and the day's hours fill in from Company Settings (change "
                               "them for a part day); Closed means the business was closed (0 hours). Time off isn't "
                               "counted as hours worked.")

        r = 2
        d = start
        wk = self.book.company.week_start
        today = date.today()
        while d <= end:
            row = _DayRow(d, eid)
            idx = len(self.rows)
            entry = self.book.get_entry(eid, d)
            style = "Today.TLabel" if d == today else ("Weekend.TLabel" if d.weekday() >= 5 else "TLabel")
            row.day_lbl = ttk.Label(g, text=f"{d:%a  %m/%d}", style=style, width=12)
            row.day_lbl.grid(row=r, column=0, sticky="w", padx=4, pady=2)
            for c, fname in enumerate(self.TIME_FIELDS, start=1):
                e = tk.Entry(g, width=10, font=F("base"), relief="flat", bd=1, justify="center")
                paint_cell(e)
                e.insert(0, calc.format_time(getattr(entry, fname)))
                e.grid(row=r, column=c, padx=3, pady=2, ipady=2)
                commit = (lambda rw=row, fn=fname, w=e: self._commit_time(rw, fn, w))
                self._bind_cell(e, idx, commit)
                e.bind("<KeyRelease>", lambda ev, rw=row, fn=fname, w=e: self._live_time(rw, fn, w), add="+")
                row.cells[fname] = e
            sp = ttk.Spinbox(g, from_=0, to=240, increment=5, width=5, justify="center",
                             command=lambda rw=row: self._commit_lunch(rw))
            sp.set(str(entry.lunch_deduct_min or 0))
            sp.grid(row=r, column=5, padx=3, pady=2, sticky="w")
            self._bind_cell(sp, idx, lambda rw=row: self._commit_lunch(rw))
            row.lunch = sp
            b = ttk.Button(g, text="+Lunch", width=7, takefocus=0, style="Small.TButton",
                           command=lambda rw=row: self._add_lunch(rw))
            b.grid(row=r, column=6, padx=(0, 6), pady=2)
            se = tk.Entry(g, width=7, font=F("base"), relief="flat", bd=1, justify="center")
            paint_cell(se)
            se.grid(row=r, column=7, padx=3, pady=2, ipady=2)
            self._bind_cell(se, idx, lambda rw=row, w=se: self._commit_salary_hours(rw, w))
            row.salary = se
            kb = ttk.Combobox(g, values=list(calc.HOURS_TYPES), state="readonly", width=8)
            kb.grid(row=r, column=8, padx=3, pady=2, sticky="w")
            kb.bind("<<ComboboxSelected>>", lambda ev, rw=row, w=kb: self._choose_type(rw, w.get()))
            row.kind = kb
            self._fill_hours_type(row, entry)
            row.hours_lbl = ttk.Label(g, text=self._hours_text(entry), width=13, anchor="e",
                                      style="Hours.TLabel")
            row.hours_lbl.grid(row=r, column=c_hours, padx=6, pady=2, sticky="e")
            ne = tk.Entry(g, font=F("base"), relief="flat", bd=1)
            paint_cell(ne)
            ne.insert(0, entry.notes)
            ne.grid(row=r, column=c_notes, padx=3, pady=2, sticky="ew", ipady=2)
            self._bind_cell(ne, idx, lambda rw=row, w=ne: self._commit_notes(rw, w))
            row.notes = ne
            self.rows.append(row)
            r += 1
            nxt = d + timedelta(days=1)
            if nxt > end or calc.week_start_for(nxt, wk) != calc.week_start_for(d, wk):
                ws = calc.week_start_for(d, wk)
                lbl_txt = f"Week of {max(ws, start):%m/%d} subtotal"
                ttk.Label(g, text=lbl_txt, style="Subtotal.TLabel").grid(row=r, column=0, columnspan=c_hours,
                                                                       sticky="e", padx=6, pady=(2, 2))
                wl = ttk.Label(g, text="0.00", style="SubtotalVal.TLabel", width=13, anchor="e")
                wl.grid(row=r, column=c_hours, padx=6, sticky="e")
                self.week_lbls[ws] = wl
                r += 1
                if nxt <= end:
                    ttk.Separator(g).grid(row=r, column=0, columnspan=ncols, sticky="ew", pady=4)
                    r += 1
            d = nxt
        self.scroll.scroll_top()
        self.update_totals()

    def _bind_cell(self, w, idx: int, commit) -> None:
        self.commit_fns[str(w)] = commit

        def on_focus_in(_e, i=idx, w=w):
            self.current_row = i
            self.scroll.scroll_into_view(w)

        def on_return(_e, w=w):
            commit()
            nxt = w.tk_focusNext()
            if nxt is not None:
                nxt.focus_set()
            return "break"

        w.bind("<FocusIn>", on_focus_in, add="+")
        w.bind("<FocusOut>", lambda e: commit(), add="+")
        w.bind("<Return>", on_return)
        w.bind("<KP_Enter>", on_return)

    def commit_pending(self) -> None:
        """Commit the cell that currently has focus (e.g. before saving)."""
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            w = None
        if w is not None:
            fn = self.commit_fns.get(str(w))
            if fn:
                fn()

    def _update_entry(self, row: _DayRow, **changes) -> None:
        if self.book is None:
            return
        cur = self.book.get_entry(row.eid, row.d)
        new = dataclasses.replace(cur, **changes)
        if new != cur:
            self.book.set_entry(new)
            self.app.mark_dirty()
        self.update_totals()

    def _preceding_punch(self, row: _DayRow, fname: str) -> time | None:
        """Nearest earlier punch on the same day, so '5' after an 8:00 AM In parses as 5:00 PM."""
        e = self.book.get_entry(row.eid, row.d)
        idx = self.TIME_FIELDS.index(fname)
        for prev in reversed(self.TIME_FIELDS[:idx]):
            t = getattr(e, prev)
            if t is not None:
                return t
        return None

    def _commit_time(self, row: _DayRow, fname: str, w: tk.Entry) -> None:
        if not w.winfo_exists() or self.book is None:
            return
        text = w.get().strip()
        try:
            t = calc.parse_time(text, after=self._preceding_punch(row, fname))
        except ValueError:
            set_invalid(w, True)
            self.app.status(f"{row.d:%a %m/%d}: '{text}' is not a valid time. Try 8:30 AM, 830a or 17:15.",
                            error=True)
            return
        set_invalid(w, False)
        formatted = calc.format_time(t)
        if w.get() != formatted:
            w.delete(0, "end")
            w.insert(0, formatted)
        self._update_entry(row, **{fname: t})

    def _live_time(self, row: _DayRow, fname: str, w: tk.Entry) -> None:
        """Update the hours label while typing, without committing."""
        if self.book is None:
            return
        try:
            t = calc.parse_time(w.get(), after=self._preceding_punch(row, fname))
        except ValueError:
            return
        e = dataclasses.replace(self.book.get_entry(row.eid, row.d), **{fname: t})
        row.hours_lbl.configure(text=self._hours_text(e))

    @staticmethod
    def _hours_text(e: TimeEntry) -> str:
        """The day's hours; time off and closed days are labelled ('8.00 PTO', '0.00 Closed')."""
        return fmt_hours(calc.entry_hours(e)) + ("" if calc.hours_type(e) == "Worked" else f" {calc.hours_type(e)}")

    def _commit_salary_hours(self, row: _DayRow, w: tk.Entry) -> None:
        if not w.winfo_exists() or self.book is None:
            return
        try:
            hours = calc.parse_hours(w.get())
        except ValueError:
            set_invalid(w, True)
            self.app.status(f"{row.d:%a %m/%d}: enter hours like 8, 7.5 or 7:30 (0 to 24).", error=True)
            return
        set_invalid(w, False)
        shown = fmt_hours(hours) if hours is not None else ""
        if w.get() != shown:
            w.delete(0, "end")
            w.insert(0, shown)
        self._update_entry(row, salary_hours=hours)

    def _choose_type(self, row: _DayRow, kind: str) -> None:
        """Type chosen: a PTO / Holiday / Sick day takes its hours from Company Settings; Closed is 0 hours; back to
        Worked drops the time off hours so the punches (or the salaried hours typed next) count again."""
        if self.book is None:
            return
        cur = self.book.get_entry(row.eid, row.d)
        changes: dict = {"hours_type": kind}
        if kind in calc.LEAVE_TYPES:
            changes["salary_hours"] = calc.leave_default_hours(self.book.company, kind)
        elif calc.hours_type(cur) != "Worked" or kind == "Closed":
            changes["salary_hours"] = None
        self._update_entry(row, **changes)
        self._fill_hours_type(row, self.book.get_entry(row.eid, row.d))

    def _fill_hours_type(self, row: _DayRow, e: TimeEntry) -> None:
        """Show the day's typed hours and type. For hourly people the hours box is only open on a time off day
        (or when hours were already typed there, so they can be cleared)."""
        se = row.salary
        se.configure(state="normal")
        set_invalid(se, False)
        se.delete(0, "end")
        se.insert(0, fmt_hours(e.salary_hours) if e.salary_hours is not None else "")
        if not (getattr(self, "_salaried", False) or calc.is_leave(e) or e.salary_hours is not None):
            se.configure(state="disabled")
        row.kind.set(calc.hours_type(e))

    def _commit_lunch(self, row: _DayRow) -> None:
        sp = row.lunch
        if sp is None or not sp.winfo_exists():
            return
        text = sp.get().strip() or "0"
        try:
            v = int(float(text))
            if not 0 <= v <= 240:
                raise ValueError
        except ValueError:
            cur = self.book.get_entry(row.eid, row.d).lunch_deduct_min
            sp.set(str(cur))
            self.app.status(f"Lunch deduct must be a number of minutes from 0 to 240.", error=True)
            return
        if sp.get() != str(v):
            sp.set(str(v))
        self._update_entry(row, lunch_deduct_min=v)

    def _add_lunch(self, row: _DayRow) -> None:
        mins = int(self.book.company.default_lunch_minutes or 0)
        row.lunch.set(str(mins))
        self._commit_lunch(row)
        self.app.status(f"{row.d:%a %m/%d}: lunch deduct set to {mins} min.")

    def _commit_notes(self, row: _DayRow, w: tk.Entry) -> None:
        if w.winfo_exists():
            self._update_entry(row, notes=w.get().strip())

    def _fill_row(self, row: _DayRow) -> None:
        e = self.book.get_entry(row.eid, row.d)
        for fname, w in row.cells.items():
            set_invalid(w, False)
            w.delete(0, "end")
            w.insert(0, calc.format_time(getattr(e, fname)))
        row.lunch.set(str(e.lunch_deduct_min or 0))
        row.notes.delete(0, "end")
        row.notes.insert(0, e.notes)
        if row.salary is not None:
            self._fill_hours_type(row, e)

    def update_totals(self) -> None:
        if self.book is None or not self.rows:
            self._layout_footer()
            self.v_reg.set("0.00")
            self.v_ot.set("0.00")
            self.v_tot.set("0.00")
            self.v_leave.set("0.00")
            return
        for row in self.rows:
            row.hours_lbl.configure(text=self._hours_text(self.book.get_entry(row.eid, row.d)))
        start, end = self.selected_period()
        entries = self.book.entries_for(self.rows[0].eid, start, end)
        summary = calc.summarize_hours(entries, self.book.company, start, end)
        leave = sum((calc.leave_hours(e) for e in entries), Decimal("0"))
        self.v_leave.set(fmt_hours(leave))
        self._layout_footer(show_leave=leave > 0 or getattr(self, "_salaried", False))
        weeks = dict(summary.by_week)
        for ws, lbl in self.week_lbls.items():
            lbl.configure(text=fmt_hours(weeks.get(ws, Decimal("0"))))
        self.v_reg.set(fmt_hours(summary.regular))
        self.v_ot.set(fmt_hours(summary.overtime))
        self.v_tot.set(fmt_hours(summary.total))

    # ----- actions
    def _target_row(self) -> _DayRow | None:
        if not self.rows:
            return None
        return self.rows[min(max(self.current_row, 0), len(self.rows) - 1)]

    def copy_previous_day(self) -> None:
        self.commit_pending()
        row = self._target_row()
        if row is None:
            return
        prev = self.book.get_entry(row.eid, row.d - timedelta(days=1))
        if not any((prev.in1, prev.out1, prev.in2, prev.out2)) and prev.salary_hours is None:
            self.app.status(f"Nothing to copy: {row.d - timedelta(days=1):%a %m/%d} has no hours.", error=True)
            return
        self._update_entry(row, in1=prev.in1, out1=prev.out1, in2=prev.in2, out2=prev.out2,
                           lunch_deduct_min=prev.lunch_deduct_min, salary_hours=prev.salary_hours,
                           hours_type=prev.hours_type)
        self._fill_row(row)
        self.app.status(f"Copied {row.d - timedelta(days=1):%a %m/%d} to {row.d:%a %m/%d}.")

    def clear_day(self) -> None:
        row = self._target_row()
        if row is None:
            return
        self.book.set_entry(TimeEntry(work_date=row.d, employee_id=row.eid))
        self.app.mark_dirty()
        self._fill_row(row)
        self.update_totals()
        self.app.status(f"Cleared {row.d:%a %m/%d}.")

    def print_timecard(self) -> None:
        self.commit_pending()
        eid, period = self.selected_eid(), self.selected_period()
        if eid is None or period is None:
            return
        emp = self.book.employees[eid]
        start, end = period
        out = self.app.output_path(f"Time Card {emp.last_name} {emp.first_name} {start:%Y-%m-%d}.pdf")
        if out is None:
            return
        path = checks.render_timecard(emp, self.book.entries_for(eid, start, end), start, end, self.book, out)
        self.app.status(f"Time card saved: {Path(path).name}")
        open_file(Path(path))


# --------------------------------------------------------------------------- PIN lock

MAX_PIN_TRIES = 5
LOCKOUT_SECONDS = 30
IDLE_LOCK_SECONDS = 5 * 60


class ChangePinDialog(ModalDialog):
    def __init__(self, master):
        super().__init__(master, "Change PIN")

    def body(self, f):
        ttk.Label(f, text=f"The PIN protects the Employees tab ({security.MIN_LEN}-{security.MAX_LEN} digits).",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.e_cur = ttk.Entry(f, show="•", width=14)
        self.e_new = ttk.Entry(f, show="•", width=14)
        self.e_conf = ttk.Entry(f, show="•", width=14)
        for r, (label, e) in enumerate((("Current PIN", self.e_cur), ("New PIN", self.e_new),
                                        ("Confirm new PIN", self.e_conf)), start=1):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            e.grid(row=r, column=1, sticky="w", pady=4)
        return self.e_cur

    def apply(self) -> bool:
        new, conf = self.e_new.get(), self.e_conf.get()
        err = security.validate_pin(new)
        if err is None and new != conf:
            err = "The new PIN entries do not match."
        if err:
            messagebox.showwarning("Change PIN", err, parent=self)
            self.e_new.delete(0, "end")
            self.e_conf.delete(0, "end")
            self.e_new.focus_set()
            return False
        try:
            security.change_pin(self.e_cur.get(), new)
        except PermissionError:
            messagebox.showwarning("Change PIN", "The current PIN is not correct.", parent=self)
            self.e_cur.delete(0, "end")
            self.e_cur.focus_set()
            return False
        except ValueError as exc:
            messagebox.showwarning("Change PIN", str(exc), parent=self)
            return False
        self.result = True
        return True


def _lockout_text(seconds: int) -> str:
    mins = max(1, (int(seconds) + 59) // 60)
    return f"Too many wrong answers. Try again in {mins} minute{'s' if mins != 1 else ''}."


class ForgotPinDialog(ModalDialog):
    """Reset the PIN by answering the security question set at install / first use."""

    def __init__(self, master):
        super().__init__(master, "Forgot PIN")

    def body(self, f):
        q = security.recovery_question()
        ttk.Label(f, text="Answer your security question to choose a new PIN.",
                  style="Muted.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(f, text="Question").grid(row=1, column=0, sticky="nw", pady=4, padx=(0, 10))
        ttk.Label(f, text=q or "", style="Bold.TLabel", wraplength=320).grid(row=1, column=1, sticky="w", pady=4)
        self.e_ans = ttk.Entry(f, show="\u2022", width=30)
        self.e_new = ttk.Entry(f, show="\u2022", width=14)
        self.e_conf = ttk.Entry(f, show="\u2022", width=14)
        for r, (label, e) in enumerate((("Answer", self.e_ans), ("New PIN", self.e_new),
                                        ("Confirm new PIN", self.e_conf)), start=2):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            e.grid(row=r, column=1, sticky="w", pady=4)
        self.v_msg = tk.StringVar()
        ttk.Label(f, textvariable=self.v_msg, style="Error.TLabel", wraplength=380).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        remaining = security.answer_lockout_remaining()
        if remaining > 0:
            self.v_msg.set(_lockout_text(remaining))
        return self.e_ans

    def apply(self) -> bool:
        remaining = security.answer_lockout_remaining()
        if remaining > 0:
            self.v_msg.set(_lockout_text(remaining))
            return False
        new, conf = self.e_new.get(), self.e_conf.get()
        err = security.validate_pin(new)
        if err is None and new != conf:
            err = "The new PIN entries do not match."
        if err:
            self.v_msg.set(err)
            self.e_new.delete(0, "end")
            self.e_conf.delete(0, "end")
            self.e_new.focus_set()
            return False
        try:
            security.reset_pin_with_answer(self.e_ans.get(), new)
        except PermissionError:
            self.e_ans.delete(0, "end")
            remaining = security.answer_lockout_remaining()
            self.v_msg.set(_lockout_text(remaining) if remaining > 0 else "That answer is not correct.")
            self.e_ans.focus_set()
            return False
        except LookupError:
            self.v_msg.set("No security question is set, so the PIN cannot be recovered.")
            return False
        except ValueError as exc:
            self.v_msg.set(str(exc))
            return False
        self.result = True
        return True


class SecurityQuestionDialog(ModalDialog):
    """Change the PIN-recovery question (requires the current PIN)."""

    def __init__(self, master):
        super().__init__(master, "Change Security Question")

    def body(self, f):
        ttk.Label(f, text="Used to reset the Employees PIN if it is forgotten. The answer ignores case and "
                  "spacing.", style="Muted.TLabel", wraplength=400).grid(row=0, column=0, columnspan=2,
                                                                         sticky="w", pady=(0, 8))
        self.e_pin = ttk.Entry(f, show="\u2022", width=14)
        self.cb_q = ttk.Combobox(f, values=list(security.SECURITY_QUESTIONS), width=44)
        self.cb_q.set(security.recovery_question() or (security.SECURITY_QUESTIONS[0]
                                                        if security.SECURITY_QUESTIONS else ""))
        self.e_ans = ttk.Entry(f, show="\u2022", width=30)
        self.e_conf = ttk.Entry(f, show="\u2022", width=30)
        for r, (label, e) in enumerate((("Current PIN", self.e_pin), ("Question", self.cb_q),
                                        ("Answer", self.e_ans), ("Confirm answer", self.e_conf)), start=1):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            e.grid(row=r, column=1, sticky="w", pady=4)
        return self.e_pin

    def apply(self) -> bool:
        if not security.verify_pin(self.e_pin.get()):
            messagebox.showwarning(self.title(), "The current PIN is not correct.", parent=self)
            self.e_pin.delete(0, "end")
            self.e_pin.focus_set()
            return False
        ans, conf = self.e_ans.get(), self.e_conf.get()
        err = security.validate_answer(ans)
        if err is None and ans != conf:
            err = "The two answers do not match."
        if err is None:
            try:
                security.set_recovery(self.cb_q.get(), ans)
            except ValueError as exc:
                err = str(exc)
        if err:
            messagebox.showwarning(self.title(), err, parent=self)
            return False
        self.result = True
        return True


class LockOverlay(ttk.Frame):
    """Covers a protected tab (Employees, Company Settings) until the PIN is entered (or created, if none is
    set yet). `what` names the protected records; `purpose` finishes "Enter the PIN to ..."."""

    def __init__(self, master, on_unlock, what: str = "Employee records",
                 purpose: str = "view or edit team members"):
        super().__init__(master, padding=40)
        self.on_unlock = on_unlock
        self.what, self.purpose = what, purpose
        self.inner = ttk.Frame(self)
        self.inner.place(relx=0.5, rely=0.35, anchor="center")
        self.v_msg = tk.StringVar()
        self.e_pin: ttk.Entry | None = None
        self.e_conf: ttk.Entry | None = None
        self.btn: ttk.Button | None = None
        self.mode = ""
        self.build()

    def build(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self.v_msg.set("")
        create = not security.has_pin()
        self.mode = "create" if create else "unlock"
        ttk.Label(self.inner, text="\U0001F512", style="Icon.TLabel").pack()
        ttk.Label(self.inner, text=f"Create a PIN to protect {self.what.lower()}" if create
                  else f"{self.what} are locked", style="LockTitle.TLabel").pack(pady=(4, 2))
        hint = (f"Choose a {security.MIN_LEN}-{security.MAX_LEN} digit PIN. You will need it to open this tab."
                if create else f"Enter the PIN to {self.purpose}.")
        ttk.Label(self.inner, text=hint, style="Muted.TLabel").pack(pady=(0, 12))
        form = ttk.Frame(self.inner)
        form.pack()
        ttk.Label(form, text="PIN").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=3)
        self.e_pin = ttk.Entry(form, show="•", width=14, justify="center", font=F("pin"))
        self.e_pin.grid(row=0, column=1, pady=3, sticky="w")
        self.e_conf = None
        self.cb_q = self.e_ans = self.e_ans2 = None
        if create:
            ttk.Label(form, text="Confirm PIN").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=3)
            self.e_conf = ttk.Entry(form, show="\u2022", width=14, justify="center", font=F("pin"))
            self.e_conf.grid(row=1, column=1, pady=3, sticky="w")
            ttk.Label(form, text="Security question (to reset a forgotten PIN)", style="Muted.TLabel").grid(
                row=2, column=0, columnspan=2, sticky="w", pady=(12, 2))
            ttk.Label(form, text="Question").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=3)
            self.cb_q = ttk.Combobox(form, values=list(security.SECURITY_QUESTIONS), width=40)
            if security.SECURITY_QUESTIONS:
                self.cb_q.set(security.SECURITY_QUESTIONS[0])
            self.cb_q.grid(row=3, column=1, pady=3, sticky="w")
            ttk.Label(form, text="Answer").grid(row=4, column=0, sticky="e", padx=(0, 8), pady=3)
            self.e_ans = ttk.Entry(form, show="\u2022", width=30)
            self.e_ans.grid(row=4, column=1, pady=3, sticky="w")
            ttk.Label(form, text="Confirm answer").grid(row=5, column=0, sticky="e", padx=(0, 8), pady=3)
            self.e_ans2 = ttk.Entry(form, show="\u2022", width=30)
            self.e_ans2.grid(row=5, column=1, pady=3, sticky="w")
            for a, b in ((self.e_pin, self.e_conf), (self.e_conf, self.cb_q), (self.cb_q, self.e_ans),
                         (self.e_ans, self.e_ans2)):
                a.bind("<Return>", lambda e, b=b: b.focus_set())
            self.e_ans2.bind("<Return>", lambda e: self.submit())
        else:
            self.e_pin.bind("<Return>", lambda e: self.submit())
        self.btn = ttk.Button(self.inner, text="Create PIN" if create else "Unlock", style="Accent.TButton",
                              command=self.submit)
        self.btn.pack(pady=(10, 4))
        if not create:
            ttk.Button(self.inner, text="Forgot PIN?", style="Link.TButton", takefocus=0,
                       command=self.forgot).pack()
        ttk.Label(self.inner, textvariable=self.v_msg, style="Error.TLabel",
                  wraplength=460, justify="center").pack()

    # One wrong-PIN count and lockout shared by every lock screen, so switching tabs doesn't buy more guesses.
    _shared = {"fails": 0, "locked_until": 0.0}

    @property
    def fails(self) -> int:
        return LockOverlay._shared["fails"]

    @fails.setter
    def fails(self, value: int) -> None:
        LockOverlay._shared["fails"] = value

    @property
    def locked_until(self) -> float:
        return LockOverlay._shared["locked_until"]

    @locked_until.setter
    def locked_until(self, value: float) -> None:
        LockOverlay._shared["locked_until"] = value

    def show(self) -> None:
        """Cover the tab, rebuilding first if a PIN was created (or removed) since this screen was built."""
        if self.mode != ("unlock" if security.has_pin() else "create"):
            self.build()
        self.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.lift()
        for e in (self.e_pin, self.e_conf, self.e_ans, self.e_ans2):
            if e is not None:
                e.delete(0, "end")
        if _time.monotonic() >= self.locked_until:
            self.v_msg.set("")
        else:   # locked out on the other tab: same countdown here
            self._set_input_state("disabled")
            self._countdown()

    def focus_pin(self) -> None:
        if self.e_pin is not None and str(self.e_pin.cget("state")) != "disabled":
            self.e_pin.focus_set()

    def submit(self) -> None:
        if _time.monotonic() < self.locked_until:
            return
        pin = self.e_pin.get()
        if self.mode == "create" and security.has_pin():
            # a PIN was created on the other tab since this screen was built: never let it be replaced here
            self.build()
            self.v_msg.set("A PIN has already been set. Enter it to unlock.")
            self.focus_pin()
            return
        if self.mode == "create":
            conf = self.e_conf.get() if self.e_conf else ""
            question, ans, ans2 = self.cb_q.get().strip(), self.e_ans.get(), self.e_ans2.get()
            err = security.validate_pin(pin)
            if err is None and pin != conf:
                err = "The two PIN entries do not match."
            if err:
                self.e_pin.delete(0, "end")
                self.e_conf.delete(0, "end")
                self.v_msg.set(err)
                self.e_pin.focus_set()
                return
            err = None if question else "Choose or type a security question."
            err = err or security.validate_answer(ans)
            if err is None and ans != ans2:
                err = "The two answers do not match."
            if err:
                self.e_ans.delete(0, "end")
                self.e_ans2.delete(0, "end")
                self.v_msg.set(err)
                self.e_ans.focus_set()
                return
            security.set_pin(pin)
            security.set_recovery(question, ans)
            self.build()
            self.on_unlock()
            return
        ok = security.verify_pin(pin)
        self.e_pin.delete(0, "end")
        if ok:
            self.fails = 0
            self.v_msg.set("")
            self.on_unlock()
            return
        self.fails += 1
        if self.fails >= MAX_PIN_TRIES:
            self.fails = 0
            self.locked_until = _time.monotonic() + LOCKOUT_SECONDS
            self._set_input_state("disabled")
            self._countdown()
        else:
            left = MAX_PIN_TRIES - self.fails
            plural = "s" if left != 1 else ""
            self.v_msg.set(f"Wrong PIN. {left} attempt{plural} left before a {LOCKOUT_SECONDS}-second lockout.")
            self.e_pin.focus_set()

    def forgot(self) -> None:
        if not security.has_recovery():
            messagebox.showinfo(
                "Forgot PIN", "No security question was set up, so the PIN cannot be recovered.\n\n"
                "Employee records can only be opened with the PIN. Once you are in, use "
                "File > Change Security Question\u2026 so this can't happen again.", parent=self)
            return
        if ForgotPinDialog(self).show():
            self.fails = 0
            self.locked_until = 0.0
            self._set_input_state("normal")
            self.v_msg.set("")
            self.on_unlock()

    def _set_input_state(self, state: str) -> None:
        for w in (self.e_pin, self.btn):
            if w is not None and w.winfo_exists():
                w.configure(state=state)

    def _countdown(self) -> None:
        if not self.winfo_exists():
            return
        remaining = int(self.locked_until - _time.monotonic() + 0.999)
        if remaining > 0:
            self.v_msg.set(f"Too many wrong attempts. Try again in {remaining} s.")
            self.after(1000, self._countdown)
        else:
            self.v_msg.set("")
            self._set_input_state("normal")
            self.focus_pin()


# --------------------------------------------------------------------------- Employees tab

class EmployeesTab(ttk.Frame):
    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.selected: str | None = None

        left = ttk.Frame(self)
        left.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(left)
        bar.pack(side="top", fill="x", pady=(0, 6))
        self.v_show_former = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Show former team members", variable=self.v_show_former,
                        command=self.refresh).pack(side="left")
        self.v_count = tk.StringVar()
        ttk.Button(bar, text="\U0001F512 Lock", command=self.lock).pack(side="right")
        ttk.Button(bar, text="Export Annual Statements…", command=self.export_statements).pack(side="right",
                                                                                                padx=(0, 8))
        ttk.Button(bar, text="Backup…", command=self.open_backup).pack(side="right", padx=(0, 8))
        ttk.Label(bar, textvariable=self.v_count, style="Muted.TLabel").pack(side="right", padx=10)
        cols = ("id", "name", "type", "active", "rate")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c, h, w, anchor in (("id", "ID", 70, "w"), ("name", "Name", 220, "w"), ("type", "Pay Type", 150, "w"),
                                ("active", "Status", 90, "center"), ("rate", "Rate on file", 90, "center")):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "name"))
        style_tree(self.tree)
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        form = ttk.LabelFrame(self, text="Employee", padding=12)
        form.pack(side="left", fill="y", padx=(12, 0))
        self.v = {k: tk.StringVar() for k in ("first", "last", "address1", "address2", "pay_type",
                                              "salary_basis")}
        self.v_active = tk.BooleanVar(value=True)
        self.v_id = tk.StringVar(value="(new)")
        r = 0
        ttk.Label(form, text="Employee ID").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Label(form, textvariable=self.v_id, style="Bold.TLabel").grid(row=r, column=1, sticky="w")
        r += 1
        for key, label in (("first", "First name"), ("last", "Last name"), ("address1", "Address line 1"),
                           ("address2", "City, State ZIP")):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=self.v[key], width=32).grid(row=r, column=1, sticky="ew", pady=3)
            r += 1
        ttk.Label(form, text="Pay type").grid(row=r, column=0, sticky="w", pady=3)
        pt = ttk.Frame(form)
        pt.grid(row=r, column=1, sticky="w", pady=3)
        ttk.Radiobutton(pt, text="Hourly", value="Hourly", variable=self.v["pay_type"]).pack(side="left")
        ttk.Radiobutton(pt, text="Salaried", value="Salary", variable=self.v["pay_type"]).pack(side="left",
                                                                                              padx=(12, 0))
        r += 1
        self.lbl_basis = ttk.Label(form, text="Salary is")
        self.lbl_basis.grid(row=r, column=0, sticky="w", pady=3)
        self.basis_row = ttk.Frame(form)
        self.basis_row.grid(row=r, column=1, sticky="w", pady=3)
        ttk.Radiobutton(self.basis_row, text="Per year", value="Annual",
                        variable=self.v["salary_basis"]).pack(side="left")
        ttk.Radiobutton(self.basis_row, text="Per pay period", value="Per Period",
                        variable=self.v["salary_basis"]).pack(side="left", padx=(12, 0))
        self.v["pay_type"].trace_add("write", lambda *_: self._show_basis())
        r += 1
        self.v_status = tk.StringVar(value="Active")
        ttk.Label(form, text="Status").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Label(form, textvariable=self.v_status).grid(row=r, column=1, sticky="w", pady=3)
        r += 1
        ttk.Label(form, text="Notes").grid(row=r, column=0, sticky="nw", pady=3)
        self.notes = tk.Text(form, width=32, height=4, font=F("base"), relief="flat", bd=1,
                             wrap="word")
        self.notes.grid(row=r, column=1, sticky="ew", pady=3)
        r += 1
        self.v_rate = tk.StringVar(value="Rate on file: -")
        ttk.Label(form, textvariable=self.v_rate, style="Muted.TLabel").grid(row=r, column=1, sticky="w", pady=(6, 0))
        r += 1
        btns = ttk.Frame(form, padding=(0, 12, 0, 0))
        btns.grid(row=r, column=0, columnspan=2, sticky="ew")
        ttk.Button(btns, text="New", command=self.clear_form).pack(side="left")
        ttk.Button(btns, text="Add", command=self.add).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="left")
        r += 1
        self.btn_deact = ttk.Button(form, text="No Longer With Company", command=self.toggle_active)
        self.btn_deact.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        r += 1
        ttk.Button(form, text="Set Pay Rate\u2026", command=self.set_pay_rate).grid(row=r, column=0, columnspan=2,
                                                                                sticky="ew", pady=(8, 0))
        self.clear_form()
        self.locked = True
        self.overlay = LockOverlay(self, self.unlock)
        self.overlay.show()

    def open_backup(self) -> None:
        if self.app.book is None or self.locked:
            return
        BackupDialog(self, self.app).show()

    # ----- annual statements for a tax professional
    def export_statements(self) -> None:
        book = self.app.book
        if book is None or self.locked:
            return
        emp = book.employees.get(self.selected) if self.selected else None
        choice = ExportStatementsDialog(self, book.company.year, emp, self.app.output_dir).show()
        if not choice:
            return
        only, folder = choice
        try:
            pdf, xlsx = statements.export_annual_statements(book, folder, [only] if only else None)
        except ValueError as exc:
            messagebox.showinfo("Export Annual Statements", str(exc), parent=self)
            return
        except PermissionError:
            messagebox.showerror("Export Annual Statements", "A statement file with the same name is open in "
                                 "another program. Close it and try again.", parent=self)
            return
        self.app.status(f"Exported {pdf.name} and {xlsx.name}")
        if messagebox.askyesno("Export Annual Statements",
                               f"Saved to {folder}:\n\n  {pdf.name}  (printable statements)\n  {xlsx.name}  "
                               f"(spreadsheet for the tax professional)\n\nOpen the PDF now?", parent=self):
            open_file(pdf)

    # ----- PIN lock
    def lock(self) -> None:
        """Hide all employee details behind the PIN overlay."""
        self.locked = True
        self.clear_form()
        self.tree.delete(*self.tree.get_children())
        self.v_count.set("")
        self.overlay.show()

    def unlock(self) -> None:
        self.locked = False
        self.overlay.place_forget()
        self.refresh()
        self.app.note_activity()
        self.app.status("Employee records unlocked. They lock again when you leave this tab.")

    @property
    def book(self):
        return self.app.book

    def refresh(self) -> None:
        if self.locked:
            return
        if self.book is None:
            return
        self.tree.delete(*self.tree.get_children())
        # former team members stay in the workbook (bookkeeping) but are hidden unless asked for
        show_former = bool(self.v_show_former.get())
        all_emps = list(self.book.employees.values())
        emps = sorted((e for e in all_emps if e.active or show_former),
                      key=lambda e: (not e.active, e.last_name.lower(), e.first_name.lower()))
        n_former = sum(1 for e in all_emps if not e.active)
        self.v_count.set(f"{len(all_emps) - n_former} active" + (f", {n_former} former" if n_former else ""))
        for e in emps:
            has = self.book.has_pay_rate(e.employee_id)
            tags = () if e.active else ("inactive",)
            if e.active and not has:
                tags = ("norate",)
            self.tree.insert("", "end", iid=e.employee_id, tags=tags,
                             values=(e.employee_id, e.display_name + ("" if e.active else "  (former)"), pay_type_label(e.pay_type, e.salary_basis),
                                     "Active" if e.active else "Former",
                                     "Yes" if has else "No"))
        if self.selected and self.tree.exists(self.selected):
            self.tree.selection_set(self.selected)
            self.tree.see(self.selected)
        else:
            self.clear_form()

    def _on_select(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        e = self.book.employees.get(sel[0])
        if e is None:
            return
        self.selected = e.employee_id
        self.v_id.set(e.employee_id)
        self.v["first"].set(e.first_name)
        self.v["last"].set(e.last_name)
        self.v["address1"].set(e.address1)
        self.v["address2"].set(e.address2)
        self.v["salary_basis"].set("Per Period" if calc.salary_is_per_period(e) else "Annual")
        self.v["pay_type"].set(e.pay_type)
        self.v_active.set(e.active)
        self.v_status.set("Active" if e.active else "No longer with the company (records kept)")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", e.notes)
        self.v_rate.set("Rate on file: " + ("Yes" if self.book.has_pay_rate(e.employee_id) else "No"))
        self.btn_deact.configure(text="No Longer With Company" if e.active else "Reactivate")

    def clear_form(self) -> None:
        self.selected = None
        if self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())
        self.v_id.set("(new)")
        for k in ("first", "last", "address1", "address2"):
            self.v[k].set("")
        self.v["salary_basis"].set("Annual")
        self.v["pay_type"].set("Hourly")
        self.v_active.set(True)
        self.v_status.set("Active")
        self.notes.delete("1.0", "end")
        self.v_rate.set("Rate on file: -")
        self.btn_deact.configure(text="No Longer With Company")

    def _show_basis(self) -> None:
        """'Salary is per year / per pay period' only applies to salaried people."""
        for w in (self.lbl_basis, self.basis_row):
            if self.v["pay_type"].get() == "Salary":
                w.grid()
            else:
                w.grid_remove()

    def _form_values(self) -> dict | None:
        first, last = self.v["first"].get().strip(), self.v["last"].get().strip()
        if not first and not last:
            messagebox.showwarning("Employees", "Enter at least a first or last name.", parent=self)
            return None
        return dict(first_name=first, last_name=last, address1=self.v["address1"].get().strip(),
                    address2=self.v["address2"].get().strip(), pay_type=self.v["pay_type"].get() or "Hourly",
                    salary_basis=self.v["salary_basis"].get() or "Annual",
                    active=bool(self.v_active.get()), notes=self.notes.get("1.0", "end").strip())

    def add(self) -> None:
        vals = self._form_values()
        if vals is None or self.book is None:
            return
        eid = self.book.next_employee_id()
        emp = Employee(employee_id=eid, **vals)
        self.book.upsert_employee(emp)
        self.selected = eid
        self.app.mark_dirty()
        self.app.refresh_employees()
        self.app.status(f"Added {emp.full_name} ({eid}).")
        if messagebox.askyesno("Employees", f"Set the pay rate for {emp.full_name} now?", parent=self):
            self.set_pay_rate()

    def save(self) -> None:
        if self.selected is None:
            self.add()
            return
        vals = self._form_values()
        if vals is None:
            return
        cur = self.book.employees[self.selected]
        new = dataclasses.replace(cur, **vals)
        basis_changed = (is_salaried(cur) and is_salaried(new)
                         and calc.salary_is_per_period(cur) != calc.salary_is_per_period(new))
        type_changed = ((is_salaried(cur) != is_salaried(new) or basis_changed)
                        and self.book.has_pay_rate(cur.employee_id))
        if type_changed:
            want = salary_amount_text(new, self.book.company) if is_salaried(new) else "an hourly rate"
            old_label = pay_type_label(cur.pay_type, cur.salary_basis)
            new_label = pay_type_label(new.pay_type, new.salary_basis)
            if not messagebox.askokcancel(
                    "Pay type changed", f"{new.full_name} is changing from {old_label} to "
                    f"{new_label}.\n\nThe rate on file must be re-entered as {want}. "
                    "The old rate will be cleared and they will be left out of Pay & Checks until a new one "
                    "is set.", parent=self):
                return
        if new != cur:
            self.book.upsert_employee(new)
            if type_changed:
                self.book.set_pay_rate(new.employee_id, Decimal("0"))
            self.app.mark_dirty()
            self.app.refresh_employees()
        self.app.status(f"Saved {new.full_name}.")
        if type_changed:
            self.set_pay_rate()

    def toggle_active(self) -> None:
        if self.selected is None:
            return
        cur = self.book.employees[self.selected]
        if cur.active:
            if not messagebox.askyesno(
                    "No Longer With Company",
                    f"Mark {cur.full_name} as no longer with the company?\n\n"
                    "Their name, time records and checks are kept in the workbook for bookkeeping, "
                    "but they will no longer appear on the Time Card, in Pay & Checks, or in these lists.\n\n"
                    "To bring them back later, tick 'Show former team members' and click Reactivate.",
                    parent=self):
                return
        self.book.upsert_employee(dataclasses.replace(cur, active=not cur.active))
        self.app.mark_dirty()
        if cur.active and not self.v_show_former.get():
            self.selected = None
        self.app.refresh_employees()
        self.app.status(f"{cur.full_name} " + ("is marked as no longer with the company (records kept)."
                                               if cur.active else "has been reactivated."))

    def set_pay_rate(self) -> None:
        if self.selected is None:
            messagebox.showinfo("Set Pay Rate", "Select an employee first.", parent=self)
            return
        emp = self.book.employees[self.selected]
        if PayRateDialog(self, self.book, emp).show():
            self.app.mark_dirty()
            self.app.refresh_employees()
            self.app.status(f"Pay rate saved for {emp.full_name}.")


# --------------------------------------------------------------------------- Year Log tab

class YearLogTab(ttk.Frame):
    """One row per pay period and employee: hours worked, lunch deducted and that period's notes. Expand a row
    for its days. Filter by employee and month; click a column heading to sort."""

    # (column, heading, width, anchor)
    COLS = (("period", "Pay period", 240, "w"), ("emp", "Employee", 180, "w"), ("hours", "Hours worked", 100, "e"),
            ("leave", "Time off", 130, "w"), ("lunch", "Lunch (min)", 90, "center"), ("notes", "Notes", 340, "w"))

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.emp_ids: list[str | None] = [None]
        self.sort_col, self.sort_desc = "period", False

        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Employee").pack(side="left")
        self.cb_emp = ttk.Combobox(top, state="readonly", width=30)
        self.cb_emp.pack(side="left", padx=(6, 18))
        self.cb_emp.bind("<<ComboboxSelected>>", lambda e: self.reload())
        ttk.Label(top, text="Month").pack(side="left")
        self.cb_month = ttk.Combobox(top, state="readonly", width=14, values=["All year"] + MONTH_NAMES)
        self.cb_month.current(0)
        self.cb_month.pack(side="left", padx=6)
        self.cb_month.bind("<<ComboboxSelected>>", lambda e: self.reload())
        ttk.Button(top, text="Export CSV…", command=self.export_csv).pack(side="right")
        ttk.Button(top, text="Refresh", command=self.reload).pack(side="right", padx=6)

        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True, pady=(10, 0))

        f1 = ttk.Frame(pw)
        self.tree = ttk.Treeview(f1, columns=[c[0] for c in self.COLS], show="tree headings")
        self.tree.column("#0", width=28, minwidth=28, stretch=False)   # expand arrow for the days
        for c, h, w, a in self.COLS:
            self.tree.heading(c, text=h, command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("notes", "emp")))
        style_tree(self.tree)
        vsb = ttk.Scrollbar(f1, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._on_double)
        pw.add(f1, weight=3)

        f2 = ttk.Frame(pw, padding=(0, 8, 0, 0))
        ttk.Label(f2, text="Monthly hours and total paid by person (Paid YTD = non-voided checks, for 1099-NEC)",
                  style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        mcols = ["emp"] + MONTHS + ["total", "paid"]
        self.mtree = ttk.Treeview(f2, columns=mcols, show="headings", height=6)
        self.mtree.heading("emp", text="Employee")
        self.mtree.column("emp", width=180, anchor="w")
        for m in MONTHS:
            self.mtree.heading(m, text=m)
            self.mtree.column(m, width=58, anchor="e", stretch=False)
        self.mtree.heading("total", text="Total")
        self.mtree.column("total", width=80, anchor="e", stretch=False)
        self.mtree.heading("paid", text="Paid YTD")
        self.mtree.column("paid", width=110, anchor="e", stretch=False)
        style_tree(self.mtree)
        self.mtree.pack(fill="both", expand=True)
        pw.add(f2, weight=1)
        ttk.Label(self, text="Click a column heading to sort. Click + on a row to see its days; double-click "
                  "a day or row to open it on the Time Card tab.", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

    @property
    def book(self):
        return self.app.book

    def refresh(self) -> None:
        if self.book is None:
            return
        cur = self.selected_eid()
        # the filter offers current team members only; former ones' history still shows under "All"
        emps = self.book.active_employees()
        self.emp_ids = [None] + [e.employee_id for e in emps]
        self.cb_emp["values"] = ["All employees"] + [f"{e.display_name}  ({e.employee_id})" for e in emps]
        self.cb_emp.current(self.emp_ids.index(cur) if cur in self.emp_ids else 0)
        self.reload()

    def selected_eid(self) -> str | None:
        i = self.cb_emp.current()
        return self.emp_ids[i] if 0 <= i < len(self.emp_ids) else None

    def _range(self) -> tuple[date, date]:
        y = self.book.company.year
        m = self.cb_month.current()
        if m <= 0:
            return date(y, 1, 1), date(y, 12, 31)
        start = date(y, m, 1)
        end = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
        return start, end

    def _emp_name(self, eid: str) -> str:
        e = self.book.employees.get(eid)
        if e is None:
            return eid
        return e.display_name + ("" if e.active else " (former)")

    # ----- pay period rows
    def groups(self) -> list[calc.PeriodLog]:
        """Pay period x employee groups for the current filters, in the current sort order."""
        start, end = self._range()
        groups = calc.period_log(self.book.entries_for(self.selected_eid(), start, end), self.book.company)
        keys = {
            "period": lambda g: (g.start, self._emp_name(g.employee_id).lower()),
            "emp": lambda g: (self._emp_name(g.employee_id).lower(), g.start),
            "hours": lambda g: (g.hours, g.start),
            "leave": lambda g: (g.leave_hours, g.start),
            "lunch": lambda g: (g.lunch_minutes, g.start),
            "notes": lambda g: (g.notes_text().lower(), g.start),
        }
        return sorted(groups, key=keys[self.sort_col], reverse=self.sort_desc)

    def _period_label(self, g: calc.PeriodLog) -> str:
        """The period's dates, clipped to the month filter (marked partial) so totals match the month."""
        start, end = self._range()
        s, e = max(g.start, start), min(g.end, end)
        return f"{s:%a %m/%d} – {e:%a %m/%d/%Y}" + ("  (partial)" if (s, e) != (g.start, g.end) else "")

    def sort_by(self, col: str) -> None:
        self.sort_desc = not self.sort_desc if col == self.sort_col else False
        self.sort_col = col
        self.reload()

    def _show_sort_arrow(self) -> None:
        for c, h, _w, _a in self.COLS:
            arrow = (" ▼" if self.sort_desc else " ▲") if c == self.sort_col else ""
            self.tree.heading(c, text=h + arrow)

    @staticmethod
    def _punches(e: TimeEntry) -> str:
        if calc.hours_type(e) != "Worked":
            return ""   # the Time off column says what the day was
        if e.salary_hours is not None:
            return "hours entered"
        pairs = [(a, b) for a, b in ((e.in1, e.out1), (e.in2, e.out2)) if a or b]
        return ", ".join(f"{calc.format_time(a) or '?'}–{calc.format_time(b) or '?'}" for a, b in pairs)

    def rows(self) -> list[list[str]]:
        """Export rows: one per pay period and employee."""
        start, end = self._range()
        out = []
        for g in self.groups():
            out.append([max(g.start, start).isoformat(), min(g.end, end).isoformat(), self._emp_name(g.employee_id),
                        fmt_hours(g.hours), fmt_hours(g.leave_hours), g.leave_text(), str(g.lunch_minutes),
                        g.notes_text(), g.employee_id])
        return out

    def reload(self) -> None:
        if self.book is None:
            return
        self.tree.delete(*self.tree.get_children())
        self._show_sort_arrow()
        groups = self.groups()
        total_h, total_off, total_l, days = Decimal("0"), Decimal("0"), 0, 0
        for g in groups:
            total_h += g.hours
            total_off += g.leave_hours
            total_l += g.lunch_minutes
            days += len(g.entries)
            emp = self.book.employees.get(g.employee_id)
            pid = f"{g.employee_id}|{g.start.isoformat()}|period"
            self.tree.insert("", "end", iid=pid, tags=("former",) if emp is not None and not emp.active else (),
                             values=(self._period_label(g), self._emp_name(g.employee_id), fmt_hours(g.hours),
                                     g.leave_text(), g.lunch_minutes or "", g.notes_text()))
            for e in g.entries:
                off = (f"{calc.hours_type(e)} {fmt_hours(calc.leave_hours(e))}" if calc.is_leave(e)
                       else "Closed" if calc.is_closed(e) else "")
                self.tree.insert(pid, "end", iid=f"{e.employee_id}|{e.work_date.isoformat()}|day",
                                 values=(f"{e.work_date:%a %m/%d}   {self._punches(e)}", "",
                                         fmt_hours(calc.day_hours(e)), off, e.lunch_deduct_min or "", e.notes))
        self.tree.insert("", "end", iid="__total__", tags=("total",),
                         values=(f"Total ({len(groups)} pay periods, {days} days)", "", fmt_hours(total_h),
                                 fmt_hours(total_off) if total_off else "", total_l or "", ""))
        # monthly summary (whole year, respecting the employee filter)
        self.mtree.delete(*self.mtree.get_children())
        y = self.book.company.year
        year_entries = self.book.entries_for(self.selected_eid(), date(y, 1, 1), date(y, 12, 31))
        by_emp: dict[str, list[Decimal]] = {}
        for e in year_entries:
            months = by_emp.setdefault(e.employee_id, [Decimal("0")] * 12)
            months[e.work_date.month - 1] += calc.day_hours(e)
        paid = self.paid_ytd()
        want = self.selected_eid()
        for eid in paid:   # anyone paid this year shows, even with no hours logged
            if want is None or eid == want:
                by_emp.setdefault(eid, [Decimal("0")] * 12)
        grand = [Decimal("0")] * 12
        grand_paid = Decimal("0")
        for eid in sorted(by_emp, key=lambda k: self._emp_name(k).lower()):
            months = by_emp[eid]
            grand = [a + b for a, b in zip(grand, months)]
            grand_paid += paid.get(eid, Decimal("0"))
            emp = self.book.employees.get(eid)
            tags = ("former",) if emp is not None and not emp.active else ()
            self.mtree.insert("", "end", tags=tags,
                              values=[self._emp_name(eid)] + [fmt_hours(m) if m else "" for m in months]
                              + [fmt_hours(sum(months)), fmt_money(paid.get(eid, Decimal("0")))])
        if len(by_emp) != 1:
            self.mtree.insert("", "end", tags=("total",), values=["All"] + [
                fmt_hours(m) if m else "" for m in grand] + [fmt_hours(sum(grand)), fmt_money(grand_paid)])

    def paid_ytd(self) -> dict[str, Decimal]:
        """Sum of non-voided check amounts (net_pay) per person for the workbook year."""
        y = self.book.company.year
        out: dict[str, Decimal] = {}
        for r in self.book.payroll:
            if not r.voided and r.check_date.year == y:
                out[r.employee_id] = out.get(r.employee_id, Decimal("0")) + r.net_pay
        return out

    def _on_double(self, event) -> None:
        """A day opens that day on the Time Card; a pay period row opens its first day."""
        iid = self.tree.identify_row(event.y)
        if not iid or iid.count("|") != 2:
            return
        eid, iso, kind = iid.split("|")
        if kind == "period":
            start, end = self._range()
            iso = max(date.fromisoformat(iso), start).isoformat()
        self.app.goto_timecard(eid, date.fromisoformat(iso))

    def export_csv(self) -> None:
        if self.book is None:
            return
        start, end = self._range()
        initial_dir = self.book.path.parent if self.book.path else documents_dir()
        suffix = "" if self.cb_month.current() <= 0 else f" {MONTH_NAMES[start.month - 1]}"
        path = filedialog.asksaveasfilename(parent=self, title="Export time log", defaultextension=".csv",
                                            initialdir=str(initial_dir),
                                            initialfile=safe_filename(f"Time Log {self.book.company.year}{suffix}.csv"),
                                            filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        rows = self.rows()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["PeriodStart", "PeriodEnd", "Employee", "HoursWorked", "TimeOffHours", "TimeOff",
                        "LunchDeductMin", "Notes", "EmployeeID"])
            w.writerows(rows)
            w.writerow(["Total", "", "", fmt_hours(sum(Decimal(r[3].replace(",", "")) for r in rows)),
                        fmt_hours(sum(Decimal(r[4].replace(",", "")) for r in rows)), "",
                        str(sum(int(r[6]) for r in rows)), "", ""])
        self.app.status(f"Exported {len(rows)} pay period rows to {Path(path).name}")


# --------------------------------------------------------------------------- Payroll tab

class BackupDialog(ModalDialog):
    """Backup folder (e.g. Google Drive), back up on every save, how many copies to keep, Back Up Now."""

    def __init__(self, master, app: "App"):
        self.app = app
        super().__init__(master, "Backup")

    def body(self, f):
        s = self.app.settings
        ttk.Label(f, text="Back up the workbook to Google Drive or another folder", style="Bold.TLabel").pack(
            anchor="w")
        ttk.Label(f, text="Each backup is a dated copy of the workbook. With Google Drive for desktop installed,\n"
                          "Google Drive appears as a folder (usually G:\\My Drive) and syncs the copies online.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(2, 10))
        ttk.Label(f, text="Backup folder:").pack(anchor="w")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(2, 0))
        self.v_dir = tk.StringVar(value=s.backup_dir)
        entry = ttk.Entry(row, textvariable=self.v_dir, width=52)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse\u2026", command=self._browse).pack(side="left", padx=(6, 0))
        ttk.Button(f, text="Use Google Drive", command=self._use_google_drive).pack(anchor="w", pady=(6, 0))

        self.v_on_save = tk.BooleanVar(value=bool(s.data.get("backup_on_save")))
        ttk.Checkbutton(f, text="Back up automatically every time the workbook is saved",
                        variable=self.v_on_save).pack(anchor="w", pady=(10, 0))
        keep = ttk.Frame(f)
        keep.pack(anchor="w", pady=(6, 0))
        ttk.Label(keep, text="Keep the newest").pack(side="left")
        self.v_keep = tk.StringVar(value=str(s.data.get("backup_keep") or backup.DEFAULT_KEEP))
        ttk.Spinbox(keep, from_=1, to=365, textvariable=self.v_keep, width=5).pack(side="left", padx=6)
        ttk.Label(keep, text="backup copies of each workbook").pack(side="left")

        self.v_last = tk.StringVar()
        self._refresh_last()
        ttk.Label(f, textvariable=self.v_last, style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        btns = ttk.Frame(f)
        btns.pack(anchor="w", pady=(6, 0))
        ttk.Button(btns, text="Back Up Now", command=self._backup_now).pack(side="left")
        ttk.Button(btns, text="Open Backup Folder", command=self._open_folder).pack(side="left", padx=(6, 0))
        ttk.Label(f, text="Backups contain pay rates and addresses. Keep the backup folder private.",
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        return entry

    def _refresh_last(self) -> None:
        last = self.app.settings.data.get("last_backup")
        self.v_last.set(f"Last backup: {last}" if last else "No backup made yet.")

    def _browse(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Choose a backup folder",
                                    initialdir=self.v_dir.get() or str(Path.home()))
        if d:
            self.v_dir.set(str(Path(d)))

    def _use_google_drive(self) -> None:
        found = backup.google_drive_folders()
        if found:
            self.v_dir.set(str(found[0]))
            return
        messagebox.showinfo("Use Google Drive",
                            "Google Drive for desktop was not found on this computer.\n\n"
                            "Install it from google.com/drive/download, sign in, and Google Drive will appear as a "
                            "drive (usually G:\\My Drive). Then click Use Google Drive again, or choose any synced "
                            "folder with Browse.", parent=self)

    def _store(self) -> bool:
        d = self.v_dir.get().strip()
        if d and not Path(d).is_dir():
            messagebox.showwarning("Backup", f"The folder doesn't exist or isn't connected:\n{d}", parent=self)
            return False
        try:
            keep = max(1, min(365, int(self.v_keep.get())))
        except ValueError:
            keep = backup.DEFAULT_KEEP
        s = self.app.settings
        s.backup_dir = d
        s.data["backup_on_save"] = bool(self.v_on_save.get()) and bool(d)
        s.data["backup_keep"] = keep
        s.save()
        return True

    def _backup_now(self) -> None:
        if not self._store():
            return
        copy = self.app.backup_now()
        if copy is not None:
            self._refresh_last()
            messagebox.showinfo("Backup", f"Backed up to:\n{copy}", parent=self)

    def _open_folder(self) -> None:
        d = self.v_dir.get().strip()
        if not d or not Path(d).is_dir():
            messagebox.showinfo("Backup", "Choose a backup folder first.", parent=self)
            return
        folder = Path(d) / backup.BACKUP_ROOT_NAME
        open_file(folder if folder.is_dir() else Path(d))

    def apply(self) -> bool:
        return self._store()


class ExportStatementsDialog(ModalDialog):
    """Choose who to include and where to save annual statements. Result: (employee_id | None, folder)."""

    def __init__(self, master, year: int, selected: Employee | None, default_dir):
        self.year, self.selected, self.default_dir = year, selected, default_dir
        super().__init__(master, "Export Annual Statements")

    def body(self, f):
        ttk.Label(f, text=f"Annual payment statements for {self.year}", style="Bold.TLabel").pack(anchor="w")
        ttk.Label(f, text="Creates a printable PDF and an Excel workbook for your tax professional.\n"
                          "Pay rates are not included.", style="Muted.TLabel", justify="left").pack(anchor="w",
                                                                                                   pady=(2, 10))
        self.v_who = tk.StringVar(value="all")
        first = ttk.Radiobutton(f, text="Everyone paid this year (including former team members)",
                                value="all", variable=self.v_who)
        first.pack(anchor="w")
        one = ttk.Radiobutton(f, value="one", variable=self.v_who,
                              text=f"Only {self.selected.full_name}" if self.selected else "Only the selected person")
        one.pack(anchor="w", pady=(2, 0))
        if not self.selected:
            one.state(["disabled"])
        ttk.Label(f, text="Save to folder:").pack(anchor="w", pady=(12, 2))
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.v_dir = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.v_dir, width=48).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._browse).pack(side="left", padx=(6, 0))
        ttk.Label(f, text="Leave blank to use the TimeCard Pro Output folder next to the workbook.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        return first

    def _browse(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Choose a folder for the statements",
                                    initialdir=self.v_dir.get() or str(documents_dir()))
        if d:
            self.v_dir.set(d)

    def apply(self) -> bool:
        folder = Path(self.v_dir.get().strip()) if self.v_dir.get().strip() else self.default_dir()
        if folder is None:
            return False
        self.result = (self.selected.employee_id if self.v_who.get() == "one" and self.selected else None, folder)
        return True


class StartPositionDialog(ModalDialog):
    """Ask which check slot (1-3) on the first 3-per-page sheet to start printing into."""

    def __init__(self, master, count: int | None = None):
        self.count = count
        super().__init__(master, "Check position")

    def body(self, f):
        self.v = tk.IntVar(value=1)
        what = f"{self.count} check(s)" if self.count else "the test check"
        ttk.Label(f, text=f"Printing {what} on 3-per-sheet business checks.").pack(anchor="w")
        ttk.Label(f, text="Start at check position on the first sheet:").pack(anchor="w", pady=(8, 4))
        first = None
        for n, txt in ((1, "1 (top)"), (2, "2 (middle)"), (3, "3 (bottom)")):
            rb = ttk.Radiobutton(f, text=txt, value=n, variable=self.v)
            rb.pack(anchor="w", padx=12, pady=1)
            first = first or rb
        ttk.Label(f, text="Use 2 or 3 to finish a partly used sheet.", style="Muted.TLabel").pack(anchor="w",
                                                                                                pady=(8, 0))
        return first

    def apply(self) -> bool:
        self.result = int(self.v.get())
        return True


class PayrollTab(ttk.Frame):
    """Pay & Checks: contractors are paid hours x rate (or a flat amount) minus an Adjustment. No taxes."""

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.periods: list[tuple[date, date]] = []
        self.period_chosen = False   # True once the user picks a period; until then follow the default
        self.preview: dict[str, dict] = {}   # eid -> {emp, hours, other, memo, record, dup}
        self.preview_period: tuple[date, date] | None = None
        self._ot_shown: bool | None = None

        top = FlowFrame(self)
        top.pack(fill="x")
        g1 = top.group()
        ttk.Label(g1, text="Pay period").pack(side="left")
        ttk.Button(g1, text="\u25C0", width=3, command=lambda: self.step_period(-1)).pack(side="left", padx=(6, 2))
        self.cb_period = ttk.Combobox(g1, state="readonly", width=31)
        self.cb_period.pack(side="left")
        self.cb_period.bind("<<ComboboxSelected>>", lambda e: self._chose_period())
        ttk.Button(g1, text="\u25B6", width=3, command=lambda: self.step_period(1)).pack(side="left", padx=(2, 0))
        g2 = top.group()
        ttk.Label(g2, text="Check date").pack(side="left")
        self.v_check_date = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(g2, textvariable=self.v_check_date, width=12).pack(side="left", padx=6)
        ttk.Button(g2, text="Calculate", style="Accent.TButton", command=self.calculate).pack(side="left", padx=6)

        cols = ("emp", "hours", "ot", "amount", "adj", "net", "memo")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=8)
        for c, h, w, a in (("emp", "Name", 200, "w"), ("hours", "Hours", 80, "e"), ("ot", "OT Hrs", 70, "e"),
                           ("amount", "Amount", 100, "e"), ("adj", "Adjustment ✎", 110, "e"),
                           ("net", "Check Amount", 110, "e"), ("memo", "Memo ✎", 320, "w")):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("emp", "memo")))
        style_tree(self.tree)
        self.tree.pack(fill="both", expand=True, pady=(10, 0))
        self.tree.bind("<Double-1>", self._on_double)
        self.v_note = tk.StringVar()
        ttk.Label(self, textvariable=self.v_note, style="Muted.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(4, 0))

        btns = ttk.Frame(self, padding=(0, 8))
        btns.pack(fill="x")
        self.btn_record = ttk.Button(btns, text="Record Pay & Print Checks", style="Accent.TButton",
                                     command=self.record_and_print)
        self.btn_record.pack(side="left")
        self.btn_reprint = ttk.Button(btns, text="Reprint Checks", command=self.reprint)
        self.btn_reprint.pack(side="left", padx=6)
        ttk.Button(btns, text="Print Register", command=self.print_register).pack(side="left")
        ttk.Button(btns, text="Void Check…", command=self.void_check).pack(side="left", padx=6)
        self.btn_align = ttk.Button(btns, text="Print Alignment Test", command=self.alignment_test)
        self.btn_align.pack(side="right")

        ttk.Label(self, text="Checks recorded for this period", style="Section.TLabel").pack(anchor="w", pady=(6, 4))
        rcols = ("no", "date", "emp", "hours", "amount", "net", "void")
        self.rtree = ttk.Treeview(self, columns=rcols, show="headings", height=6)
        for c, h, w, a in (("no", "Check #", 80, "center"), ("date", "Check Date", 100, "center"),
                           ("emp", "Name", 200, "w"), ("hours", "Hours", 80, "e"), ("amount", "Amount", 100, "e"),
                           ("net", "Check Amount", 110, "e"), ("void", "Voided", 70, "center")):
            self.rtree.heading(c, text=h)
            self.rtree.column(c, width=w, anchor=a, stretch=(c == "emp"))
        style_tree(self.rtree)
        self.rtree.pack(fill="both", expand=True)

    HINT = "Double-click Adjustment (positive deducts, negative adds, e.g. a reimbursement) or Memo to edit."

    @property
    def book(self):
        return self.app.book

    @property
    def ot_enabled(self) -> bool:
        return bool(self.book is not None and getattr(self.book.company, "overtime_enabled", False))

    @property
    def layout(self) -> str:
        return getattr(self.book.company, "check_layout", "voucher") if self.book is not None else "voucher"

    def _apply_layout(self) -> None:
        """Button labels follow the check layout; handwrite prints a list instead of checks."""
        hand = self.layout == "handwrite"
        self.btn_record.configure(text="Record Pay & Print Checks-to-Write List" if hand
                                  else "Record Pay & Print Checks")
        self.btn_reprint.configure(text="Print Checks-to-Write List" if hand else "Reprint Checks")
        self.btn_align.configure(state="disabled" if hand else "normal")

    def _apply_columns(self) -> None:
        self._apply_layout()
        ot = self.ot_enabled
        if ot != self._ot_shown:
            self.tree["displaycolumns"] = ("emp", "hours", "ot", "amount", "adj", "net", "memo") if ot else \
                ("emp", "hours", "amount", "adj", "net", "memo")
            self.tree.heading("hours", text="Reg Hrs" if ot else "Hours")
            self._ot_shown = ot

    def refresh(self) -> None:
        if self.book is None:
            return
        self._apply_columns()
        old = self.selected_period()
        self.periods = calc.periods_in_year(self.book.company)
        self.cb_period["values"] = [fmt_period(p) for p in self.periods]
        if old in self.periods and self.period_chosen:
            self.cb_period.current(self.periods.index(old))
        elif self.periods:
            self.cb_period.current(self._default_period_index())
        self.on_period_change()

    def _default_period_index(self) -> int:
        """The pay period containing today, or the one before it when the company holds a period back
        (Company Settings > Pay timing)."""
        today = date.today()
        idx = period_index_for(self.periods, today)
        held_back = (self.book.company.pay_hold_back or "").strip().lower() == "one"
        if held_back and idx > 0 and self.periods[idx][0] <= today <= self.periods[idx][1]:
            idx -= 1
        return idx

    def _unpaid_hourly(self, period: tuple[date, date]) -> set[str]:
        """Active hourly people with hours (or paid time off) in `period` and no (non-voided) check for it yet."""
        start, end = period
        paid = {r.employee_id for r in self.book.records_for_period(start, end) if not r.voided}
        return {e.employee_id for e in self.book.active_employees()
                if not is_salaried(e) and e.employee_id not in paid
                and _has_pay_hours(calc.summarize_hours(self.book.entries_for(e.employee_id, start, end),
                                                        self.book.company, start, end))}

    def _hours_elsewhere(self, eid: str, current: tuple[date, date]) -> tuple[date, date] | None:
        """The latest other period (up to today) where `eid` has unpaid hours, if any."""
        today = date.today()
        for p in reversed(self.periods):
            if p != current and p[0] <= today and eid in self._unpaid_hourly(p):
                return p
        return None

    def selected_period(self) -> tuple[date, date] | None:
        i = self.cb_period.current()
        return self.periods[i] if 0 <= i < len(self.periods) else None

    def step_period(self, delta: int) -> None:
        i = self.cb_period.current() + delta
        if 0 <= i < len(self.periods):
            self.cb_period.current(i)
            self._chose_period()

    def _chose_period(self) -> None:
        self.period_chosen = True   # keep the user's choice instead of the automatic default
        self.on_period_change()

    def on_shown(self) -> None:
        """Tab opened: until the user picks a period, follow the default (hours may have been entered since)."""
        if self.book is not None and not self.period_chosen and not self.preview and self.periods:
            idx = self._default_period_index()
            if idx != self.cb_period.current():
                self.cb_period.current(idx)
                self.on_period_change()
                return
        self.load_recorded()

    def on_period_change(self) -> None:
        self.preview.clear()
        self.preview_period = None
        self.tree.delete(*self.tree.get_children())
        self.v_note.set("Click Calculate to preview this period's checks. " + self.HINT)
        self.load_recorded()

    def load_recorded(self) -> None:
        self.rtree.delete(*self.rtree.get_children())
        p = self.selected_period()
        if p is None or self.book is None:
            return
        for r in sorted(self.book.records_for_period(*p), key=lambda r: r.check_number):
            self.rtree.insert("", "end", iid=str(r.check_number), tags=("void",) if r.voided else (),
                              values=(r.check_number, f"{r.check_date:%m/%d/%Y}", r.employee_name,
                                      fmt_hours(r.regular_hours + r.overtime_hours), fmt_money(r.gross_pay),
                                      fmt_money(r.net_pay), "Yes" if r.voided else "No"))

    def _check_date(self) -> date | None:
        try:
            return parse_date(self.v_check_date.get())
        except ValueError as exc:
            messagebox.showwarning("Pay & Checks", str(exc), parent=self)
            return None

    @staticmethod
    def default_memo(start: date, end: date, hours: HoursSummary, salaried: bool = False) -> str:
        span = f"{start:%m/%d}\u2013{end:%m/%d/%Y}" if start.year == end.year else \
            f"{start:%m/%d/%Y}\u2013{end:%m/%d/%Y}"
        off = f" + {hours.paid_leave:.2f} time off" if hours.paid_leave else ""
        if salaried:
            return f"Salary {span}" + (f" \u00b7 {hours.total:.2f} hrs" if hours.total else "")
        return f"Contract labor {span} \u00b7 {hours.total:.2f} hrs{off}"

    def _compute(self, item: dict, check_number: int, check_date: date) -> PayrollRecord:
        start, end = self.preview_period
        return calc.compute_payroll(item["emp"], item["hours"], self.book.company, check_number=check_number,
                                    check_date=check_date, period_start=start, period_end=end,
                                    other_deductions=item["other"], memo=item["memo"])

    def calculate(self) -> None:
        if self.book is None:
            return
        self.app.commit_pending()
        self._apply_columns()
        p = self.selected_period()
        cd = self._check_date()
        if p is None or cd is None:
            return
        start, end = p
        keep = {k: (v["other"], v["memo"]) for k, v in self.preview.items()} if self.preview_period == p else {}
        self.preview.clear()
        self.preview_period = p
        already = {r.employee_id for r in self.book.records_for_period(start, end) if not r.voided}
        no_rate, dups, no_hours = [], [], []
        for emp in self.book.active_employees():
            entries = self.book.entries_for(emp.employee_id, start, end)
            hours = calc.summarize_hours(entries, self.book.company, start, end)
            if not _has_pay_hours(hours) and not is_salaried(emp):   # salaried people are paid every period
                no_hours.append(emp)
                continue
            if not self.book.has_pay_rate(emp.employee_id):
                no_rate.append(emp.full_name)
                continue
            other, memo = keep.get(emp.employee_id, (Decimal("0"), self.default_memo(start, end, hours, is_salaried(emp))))
            item = dict(emp=emp, hours=hours, other=other, memo=memo, dup=emp.employee_id in already)
            item["record"] = self._compute(item, 0, cd)
            self.preview[emp.employee_id] = item
            if item["dup"]:
                dups.append(emp.full_name)
        self._render_preview()
        notes = []
        elsewhere = [(e, self._hours_elsewhere(e.employee_id, p)) for e in no_hours]
        elsewhere = [(e, q) for e, q in elsewhere if q is not None]
        if elsewhere:
            notes.append("No hours in this period for " + "; ".join(
                f"{e.full_name} (has unpaid hours in {q[0]:%m/%d}-{q[1]:%m/%d})" for e, q in elsewhere)
                + ". Choose that pay period above to pay them.")
        elif no_hours and not self.preview:
            notes.append("No hours were entered on the Time Card for this pay period.")
        if dups:
            notes.append("Already paid for this period (shown in orange): " + ", ".join(dups) + ".")
        if no_rate:
            notes.append("Excluded - no pay rate on file: " + ", ".join(no_rate) + ".")
        c = self.book.company
        if any((c.federal_withholding_pct, c.state_withholding_pct, c.social_security_pct, c.medicare_pct)):
            notes.append("Note: withholding % values under Company Settings > Advanced are not zero and are "
                         "being subtracted from the check amounts.")
        self.v_note.set("  ".join(notes) or f"{len(self.preview)} check(s) ready. " + self.HINT)
        if no_rate:
            messagebox.showwarning("Pay & Checks", "These contractors have hours but no pay rate on file and "
                                   "were excluded:\n\n" + "\n".join(no_rate) +
                                   "\n\nSet a rate on the Employees tab, then Calculate again.", parent=self)
        if dups:
            messagebox.showwarning("Pay & Checks", "Checks for this period are already recorded for:\n\n" +
                                   "\n".join(dups), parent=self)
        self.app.status(f"Calculated {len(self.preview)} check(s) for {fmt_period(p)}.")

    def _render_preview(self) -> None:
        self.tree.delete(*self.tree.get_children())
        tot_h = tot_ot = tot_amt = tot_adj = tot_net = Decimal("0")
        for eid, item in sorted(self.preview.items(), key=lambda kv: kv[1]["emp"].display_name.lower()):
            r: PayrollRecord = item["record"]
            tot_h += r.regular_hours
            tot_ot += r.overtime_hours
            tot_amt += r.gross_pay
            tot_adj += r.other_deductions
            tot_net += r.net_pay
            hrs = r.regular_hours if self.ot_enabled else r.regular_hours + r.overtime_hours
            hrs_txt = "Salaried" if is_salaried(item["emp"]) and not (r.regular_hours + r.overtime_hours) \
                else fmt_hours(hrs)
            name = item["emp"].display_name + ("  (already paid)" if item["dup"] else "")
            self.tree.insert("", "end", iid=eid, tags=("dup",) if item["dup"] else (),
                             values=(name, hrs_txt, fmt_hours(r.overtime_hours), fmt_money(r.gross_pay),
                                     self._fmt_adj(r.other_deductions), fmt_money(r.net_pay), r.memo))
        if self.preview:
            self.tree.insert("", "end", iid="__total__", tags=("total",),
                             values=(f"Total ({len(self.preview)} checks)",
                                     fmt_hours(tot_h if self.ot_enabled else tot_h + tot_ot), fmt_hours(tot_ot),
                                     fmt_money(tot_amt), self._fmt_adj(tot_adj), fmt_money(tot_net), ""))

    @staticmethod
    def _fmt_adj(x: Decimal) -> str:
        if not x:
            return ""
        return f"-{fmt_money(x)}" if x > 0 else f"+{fmt_money(-x)}"

    def _on_double(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or iid not in self.preview:
            return
        try:
            name = self.tree["displaycolumns"][int(col[1:]) - 1]
        except (ValueError, IndexError):
            return
        if name == "adj":
            self.edit_adjustment(iid)
        elif name == "memo":
            self.edit_memo(iid)
        else:
            self.v_note.set(self.HINT)

    def edit_adjustment(self, eid: str) -> None:
        item = self.preview[eid]
        s = simpledialog.askstring(
            "Adjustment", f"Adjustment for {item['emp'].full_name}:\n"
            "positive = deduct from the check, negative = add (e.g. -25.00 for a reimbursement)",
            initialvalue=f"{item['other']:.2f}", parent=self)
        if s is None:
            return
        try:
            amt = parse_money(s)
        except ValueError as exc:
            messagebox.showwarning("Adjustment", str(exc), parent=self)
            return
        cd = self._check_date()
        if cd is None:
            return
        trial = dict(item, other=amt)
        rec = self._compute(trial, 0, cd)
        if rec.net_pay < 0:
            messagebox.showwarning("Adjustment", "That would make the check amount negative.", parent=self)
            return
        item["other"], item["record"] = amt, rec
        self._render_preview()

    def edit_memo(self, eid: str) -> None:
        item = self.preview[eid]
        s = simpledialog.askstring("Memo", f"Check memo for {item['emp'].full_name}:", initialvalue=item["memo"],
                                   parent=self)
        if s is None:
            return
        item["memo"] = s.strip()[:80]
        cd = self._check_date()
        if cd is not None:
            item["record"] = self._compute(item, 0, cd)
        self._render_preview()

    def record_and_print(self) -> None:
        if self.book is None:
            return
        if not self.preview or self.preview_period != self.selected_period():
            messagebox.showinfo("Pay & Checks", "Click Calculate first to preview this period's checks.",
                                parent=self)
            return
        cd = self._check_date()
        if cd is None:
            return
        items = sorted(self.preview.values(), key=lambda it: it["emp"].display_name.lower())
        dups = [it for it in items if it["dup"]]
        if dups:
            ans = messagebox.askyesnocancel(
                "Already paid", f"{len(dups)} contractor(s) already have a check for this period:\n\n" +
                "\n".join(it["emp"].full_name for it in dups) +
                "\n\nYes = include them anyway (extra check)\nNo = skip them", parent=self)
            if ans is None:
                return
            if not ans:
                items = [it for it in items if not it["dup"]]
        if not items:
            messagebox.showinfo("Pay & Checks", "There are no checks to record.", parent=self)
            return
        start_no = simpledialog.askinteger("Starting check number", "First check number:", parent=self,
                                           initialvalue=int(self.book.company.next_check_number), minvalue=1)
        if start_no is None:
            return
        numbers = list(range(start_no, start_no + len(items)))
        clash = sorted(set(numbers) & {r.check_number for r in self.book.payroll})
        if clash:
            messagebox.showerror("Pay & Checks", f"Check number(s) already used: {', '.join(map(str, clash))}.",
                                 parent=self)
            return
        records = [self._compute(it, n, cd) for it, n in zip(items, numbers)]
        total = sum((r.net_pay for r in records), Decimal("0"))
        if not messagebox.askyesno(
                "Record pay", f"Record {len(records)} check(s) totaling {fmt_money(total)}\n"
                f"numbered {numbers[0]}-{numbers[-1]}, dated {cd:%m/%d/%Y}?\n\n"
                "The workbook will be saved and the checks PDF opened.", parent=self):
            return
        self.book.record_payroll(records)
        self.app.mark_dirty()
        if not self.app.save():
            messagebox.showwarning("Pay & Checks", "The checks were recorded but the workbook is NOT saved yet. "
                                   "Save it before closing.", parent=self)
        p = self.preview_period
        self.on_period_change()
        self.app.refresh_all()
        self._print_checks(records, f"Checks {p[0]:%Y-%m-%d} to {p[1]:%Y-%m-%d} #{numbers[0]}-{numbers[-1]}.pdf",
                           f"Recorded {len(records)} checks.")

    def _start_position(self, count: int | None) -> int | None:
        """Ask which slot to start at, but only for the 3-per-page layouts."""
        if self.layout in THREE_UP_LAYOUTS:
            return self.app.ask_start_position(count)
        return 1

    def _print_checks(self, records: list[PayrollRecord], filename: str, done_msg: str) -> None:
        pos = self._start_position(len(records))
        if pos is None:
            self.app.status(done_msg + " Printing cancelled - use Reprint Checks to print them.")
            return
        if self.layout == "handwrite":
            filename = filename.replace("Checks", "Checks to Write", 1)
        out = self.app.output_path(filename)
        if out is None:
            return
        path = checks.render_checks(records, self.book, out, start_position=pos)
        self.app.status(f"{done_msg} PDF: {Path(path).name}")
        open_file(Path(path))

    def _selected_records(self) -> list[PayrollRecord]:
        p = self.selected_period()
        if p is None:
            return []
        recs = [r for r in self.book.records_for_period(*p) if not r.voided]
        sel = {int(i) for i in self.rtree.selection()}
        chosen = [r for r in recs if r.check_number in sel]
        return chosen or recs

    def reprint(self) -> None:
        if self.book is None:
            return
        recs = sorted(self._selected_records(), key=lambda r: r.check_number)
        if not recs:
            messagebox.showinfo("Reprint Checks", "No recorded (non-voided) checks for this period.", parent=self)
            return
        self._print_checks(recs, f"Checks reprint #{recs[0].check_number}-{recs[-1].check_number}.pdf",
                           f"Reprinted {len(recs)} check(s).")

    def print_register(self) -> None:
        p = self.selected_period()
        if self.book is None or p is None:
            return
        recs = sorted(self.book.records_for_period(*p), key=lambda r: r.check_number)
        if not recs:
            messagebox.showinfo("Print Register", "No checks recorded for this period yet.", parent=self)
            return
        out = self.app.output_path(f"Check Register {p[0]:%Y-%m-%d} to {p[1]:%Y-%m-%d}.pdf")
        if out is None:
            return
        path = checks.render_register(recs, self.book, out,
                                      title=f"Check Register  {p[0]:%m/%d/%Y} - {p[1]:%m/%d/%Y}")
        self.app.status(f"Register saved: {Path(path).name}")
        open_file(Path(path))

    def void_check(self) -> None:
        if self.book is None:
            return
        sel = self.rtree.selection()
        n = simpledialog.askinteger("Void Check", "Check number to void:", parent=self,
                                    initialvalue=int(sel[0]) if sel else None, minvalue=1)
        if n is None:
            return
        rec = next((r for r in self.book.payroll if r.check_number == n), None)
        if rec is None:
            messagebox.showerror("Void Check", f"Check #{n} was not found in this workbook.", parent=self)
            return
        if rec.voided:
            messagebox.showinfo("Void Check", f"Check #{n} is already voided.", parent=self)
            return
        if not messagebox.askyesno("Void Check", f"Void check #{n} to {rec.employee_name} for "
                                   f"{fmt_money(rec.net_pay)}?\n\nThe check stays in the log marked Voided.",
                                   parent=self):
            return
        self.book.void_check(n)
        self.app.mark_dirty()
        self.load_recorded()
        self.app.status(f"Check #{n} voided.{self.app.save_hint()}")

    def alignment_test(self) -> None:
        if self.book is None:
            return
        if self.layout == "handwrite":
            messagebox.showinfo("Alignment test", "No alignment test is needed when checks are written by hand.",
                                parent=self)
            return
        pos = self._start_position(None)
        if pos is None:
            return
        out = self.app.output_path("Check Alignment Test.pdf")
        if out is None:
            return
        path = checks.render_alignment_test(self.book, out, start_position=pos)
        self.app.status(f"Alignment test saved: {Path(path).name}")
        open_file(Path(path))


# --------------------------------------------------------------------------- Company tab

class CompanyTab(ttk.Frame):
    # (section, field, label, kind, options)
    FIELDS = [
        ("Company", "company_name", "Company name", "str", None),
        ("Company", "address1", "Address line 1", "str", None),
        ("Company", "address2", "City, State ZIP", "str", None),
        ("Company", "phone", "Phone", "str", None),
        ("Bank", "bank_name", "Bank name", "str", None),
        ("Bank", "bank_address", "Bank address", "str", None),
        ("Bank", "routing_number", "Routing number", "secret", None),
        ("Bank", "account_number", "Account number", "secret", None),
        ("Pay", "year", "Year", "readonly", None),
        ("Pay", "pay_period", "Pay period", "choice", list(calc.PAY_PERIODS)),
        ("Pay", "week_start", "Week starts on", "choice", list(calc.WEEK_STARTS)),
        ("Pay", "pay_hold_back", "Pay timing", "radio",
         [("none", "Pay the current pay period (no hold-back)"),
          ("one", "Hold one pay period back (e.g. pay last week's hours this week)")]),
        ("Pay", "default_lunch_minutes", "Default lunch deduct (min)", "int", None),
        ("Pay", "next_check_number", "Next check number", "int", None),
        ("Pay", "overtime_enabled", "Overtime", "bool", "Overtime enabled"),
        ("Pay", "overtime_threshold_weekly", "Overtime after (hrs/week)", "float", None),
        ("Pay", "overtime_multiplier", "Overtime multiplier", "float", None),
        ("Pay", "form_1099_threshold", "1099 review threshold ($)", "float", None),
        ("Time off", "pto_hours", "PTO day (hours)", "float", None),
        ("Time off", "holiday_hours", "Holiday (hours)", "float", None),
        ("Time off", "sick_hours", "Sick day (hours)", "float", None),
        ("Time off", "pto_paid", "PTO", "bool", "Pay hourly people for PTO hours"),
        ("Time off", "holiday_paid", "Holiday", "bool", "Pay hourly people for holiday hours"),
        ("Time off", "sick_paid", "Sick", "bool", "Pay hourly people for sick hours"),
        ("Checks", "check_layout", "Check layout", "radio", CHECK_LAYOUTS),
        ("Checks", "manual_sheet_width", "Sheet width (inches)", "float", None),
        ("Checks", "manual_sheet_height", "Sheet height (inches)", "float", None),
        ("Checks", "manual_stub_width", "Stub width (inches)", "float", None),
        ("Checks", "check_stock", "Check stock", "radio", [("preprinted", "Preprinted (bank info on stock)"),
                                                           ("blank", "Blank (print MICR line)")]),
        ("Checks", "micr_font_path", "MICR font file (.ttf)", "file", None),
        ("Checks", "print_offset_x", "Print offset X (inches)", "float", None),
        ("Checks", "print_offset_y", "Print offset Y (inches)", "float", None),
        ("Checks", "signature_line_text", "Signature line text", "str", None),
        ("Branding", "brand_color", "Brand color", "color", None),
        ("Branding", "logo_on_checks", "Checks", "bool", "Show logo on checks (stubs; check face only on blank stock)"),
        ("Branding", "logo_on_reports", "Reports", "bool",
         "Show logo on reports (time cards, register, annual statements)"),
        ("Advanced", "federal_withholding_pct", "Federal withholding %", "float", None),
        ("Advanced", "state_withholding_pct", "State withholding %", "float", None),
        ("Advanced", "social_security_pct", "Social Security %", "float", None),
        ("Advanced", "medicare_pct", "Medicare %", "float", None),
    ]
    OT_FIELDS = ("overtime_threshold_weekly", "overtime_multiplier")

    def __init__(self, master, app: "App"):
        super().__init__(master, padding=12)
        self.app = app
        self.vars: dict[str, tk.Variable] = {}
        self.widgets: dict[str, tk.Widget] = {}
        self.secret_entries: list[ttk.Entry] = []
        self.manual_widgets: list[tk.Widget] = []
        self.v_show = tk.BooleanVar(value=False)

        sf = self.sf = ScrollFrame(self)
        sf.pack(fill="both", expand=True)
        body = sf.inner
        frames: dict[str, ttk.Frame] = {}
        layout = {"Company": (0, 0), "Bank": (1, 0), "Branding": (2, 0), "Pay": (0, 1), "Checks": (1, 1),
                  "Time off": (2, 1)}
        rows: dict[str, int] = {}
        for section, (r, c) in layout.items():
            lf = ttk.LabelFrame(body, text=section, padding=12)
            lf.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
            lf.columnconfigure(1, weight=1)
            frames[section] = lf
            rows[section] = 0
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        # collapsed "Advanced" group for the (unused for contractors) withholding percentages
        adv_outer = ttk.Frame(body, padding=(6, 4))
        adv_outer.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.section_frames = frames
        self.adv_outer = adv_outer
        self._sections_single = None
        self._build_logo_controls(frames["Branding"])
        rows["Branding"] = 3
        self.v_adv_open = tk.BooleanVar(value=False)
        self.btn_adv = ttk.Button(adv_outer, text="", style="Toolbutton", command=self._toggle_advanced)
        self.btn_adv.pack(anchor="w")
        self.adv_frame = ttk.LabelFrame(adv_outer, text="Withholding (not used for independent contractors)",
                                        padding=12)
        frames["Advanced"] = self.adv_frame
        rows["Advanced"] = 0
        ttk.Label(self.adv_frame, text="Leave these at 0 for contractors. Any non-zero percentage is subtracted "
                  "from each check as a flat estimate.", style="Warn.TLabel",
                  wraplength=520).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        rows["Advanced"] = 1
        self._toggle_advanced(initial=True)

        for section, fname, label, kind, opts in self.FIELDS:
            lf = frames[section]
            r = rows[section]
            rows[section] += 1
            v = tk.BooleanVar() if kind == "bool" else tk.StringVar()
            self.vars[fname] = v
            if fname == MANUAL_FIELDS[0]:   # hint + warning shown only for manual binder checks
                hint = ttk.Label(lf, text="Measure one sheet of your checks with a ruler.", style="Muted.TLabel")
                hint.grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 0))
                warn = ttk.Label(lf, text="Duplicate/carbon copy checks can't go through a printer.",
                                 style="Warn.TLabel", wraplength=360)
                warn.grid(row=r + 1, column=0, columnspan=2, sticky="w", pady=(0, 2))
                self.manual_widgets += [hint, warn]
                r += 2
                rows[section] += 2
            lbl = ttk.Label(lf, text=label)
            lbl.grid(row=r, column=0, sticky="nw" if kind == "radio" else "w", pady=3, padx=(0, 10))
            if fname in MANUAL_FIELDS:
                self.manual_widgets.append(lbl)
            w = None
            if kind in ("str", "int", "float"):
                w = ttk.Entry(lf, textvariable=v, width=30 if kind == "str" else 12)
                w.grid(row=r, column=1, sticky="ew" if kind == "str" else "w", pady=3)
            elif kind == "bool":
                w = ttk.Checkbutton(lf, text=opts, variable=v, command=self._update_ot_state)
                w.grid(row=r, column=1, sticky="w", pady=3)
            elif kind == "color":
                fr = ttk.Frame(lf)
                fr.grid(row=r, column=1, sticky="w", pady=3)
                self.swatch = tag_role(tk.Label(fr, width=4, relief="solid", bd=1), "keep")
                self.swatch.pack(side="left", padx=(0, 6), ipady=2)
                w = ttk.Entry(fr, textvariable=v, width=10)
                w.pack(side="left")
                ttk.Button(fr, text="Pick\u2026", command=self._pick_color).pack(side="left", padx=(6, 0))
                v.trace_add("write", lambda *_: self._update_swatch())
            elif kind == "secret":
                w = ttk.Entry(lf, textvariable=v, width=24, show="•")
                w.grid(row=r, column=1, sticky="w", pady=3)
                self.secret_entries.append(w)
            elif kind == "readonly":
                ttk.Label(lf, textvariable=v, style="Bold.TLabel").grid(row=r, column=1, sticky="w", pady=3)
            elif kind == "choice":
                w = ttk.Combobox(lf, textvariable=v, values=opts, state="readonly", width=14)
                w.grid(row=r, column=1, sticky="w", pady=3)
            elif kind == "radio":
                fr = ttk.Frame(lf)
                fr.grid(row=r, column=1, sticky="w", pady=3)
                for val, txt in opts:
                    ttk.Radiobutton(fr, text=txt, value=val, variable=v).pack(anchor="w")
            elif kind == "file":
                fr = ttk.Frame(lf)
                fr.grid(row=r, column=1, sticky="ew", pady=3)
                ttk.Entry(fr, textvariable=v, width=26).pack(side="left", fill="x", expand=True)
                ttk.Button(fr, text="Browse…", command=lambda var=v: self._pick_font(var)).pack(side="left",
                                                                                                padx=(4, 0))
            if w is not None:
                self.widgets[fname] = w
                if fname in MANUAL_FIELDS:
                    self.manual_widgets.append(w)
            if fname == "check_layout":
                v.trace_add("write", lambda *_: self._update_layout_state())
            if fname == "account_number":
                ttk.Checkbutton(lf, text="Show bank numbers", variable=self.v_show,
                                command=self._toggle_show).grid(row=r + 1, column=1, sticky="w", pady=3)
                rows[section] += 1

        ttk.Label(frames["Pay"], text="Contractors: leave overtime off so every hour is paid at the same rate.",
                  style="Muted.TLabel", wraplength=360).grid(row=rows["Pay"], column=0, columnspan=2, sticky="w",
                                                             pady=(8, 0))
        ttk.Label(frames["Checks"], text="Check stock, MICR font and offsets apply to the printed layouts.",
                  style="Muted.TLabel", wraplength=360).grid(row=rows["Checks"], column=0, columnspan=2,
                                                             sticky="w", pady=(8, 0))
        self._update_layout_state()
        btns = self.btns = ttk.Frame(self, padding=(0, 10, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Save Settings", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(btns, text="Revert", command=self.refresh).pack(side="right", padx=6)
        self.locked = False
        self.overlay = LockOverlay(self, self.unlock, what="Company settings",
                                   purpose="view or change company, bank and check settings")
        self.lock()

    # ----- PIN lock (bank account and routing numbers live here)
    def lock(self) -> None:
        """Take the settings off the screen (not just covered, so Tab can't reach them) behind the PIN."""
        self.locked = True
        self._kept_edits = self.form_changed()   # unsaved edits stay (hidden) until the PIN is entered again
        self.sf.pack_forget()
        self.btns.pack_forget()
        self.overlay.show()

    def unlock(self) -> None:
        self.locked = False
        self.overlay.place_forget()
        self.sf.pack(fill="both", expand=True)
        self.btns.pack(fill="x")
        if getattr(self, "_kept_edits", False) and self.form_changed():
            self.app.status("Your unsaved changes to company settings were kept. Click Save Settings to keep them.")
        else:
            self.refresh()
            self.app.status("Company settings unlocked. They lock again when you leave this tab.")
        self._kept_edits = False
        self.app.note_activity()

    def form_changed(self) -> bool:
        """True if the form holds edits that haven't been saved to the workbook with Save Settings."""
        if self.book is None or not self.vars:
            return False
        c = self.book.company
        for _s, fname, _l, kind, _o in self.FIELDS:
            val = getattr(c, fname, None)
            shown = self.vars[fname].get()
            if kind == "bool":
                if bool(shown) != bool(val):
                    return True
            elif str(shown).strip() != ("" if val is None else str(val)).strip():
                return True
        return False

    @property
    def book(self):
        return self.app.book

    def _toggle_advanced(self, initial: bool = False) -> None:
        if not initial:
            self.v_adv_open.set(not self.v_adv_open.get())
        if self.v_adv_open.get():
            self.adv_frame.pack(fill="x", pady=(4, 0))
            self.btn_adv.configure(text="▾ Advanced (not used for contractors)")
        else:
            self.adv_frame.pack_forget()
            self.btn_adv.configure(text="▸ Advanced (not used for contractors)")

    # ----- branding
    def _build_logo_controls(self, lf) -> None:
        ttk.Label(lf, text="Logo").grid(row=0, column=0, sticky="nw", pady=3, padx=(0, 10))
        self.logo_box = tag_role(tk.Label(lf, text="No logo", relief="solid", bd=1, anchor="center"), "keep")
        self.logo_box.grid(row=0, column=1, sticky="w", pady=3)
        self._logo_preview_img = None
        bf = ttk.Frame(lf)
        bf.grid(row=1, column=1, sticky="w", pady=(2, 6))
        ttk.Button(bf, text="Choose Logo\u2026", command=self.choose_logo).pack(side="left")
        ttk.Button(bf, text="Remove Logo", command=self.remove_logo).pack(side="left", padx=6)
        ttk.Label(lf, text="PNG, JPG, BMP or GIF. It is stored inside the workbook.", style="Muted.TLabel",
                  wraplength=360).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 4))

    def refresh_logo_preview(self) -> None:
        png = self.book.logo_png if self.book is not None else b""
        self._logo_preview_img = logo_photo(self, png, scaled(240), scaled(90))
        if self._logo_preview_img is not None:
            self.logo_box.configure(image=self._logo_preview_img, text="", width=0, height=0,
                                    bg=P["card_bg"], fg=P["card_fg"])
        else:
            # width/height are in characters when there is no image
            self.logo_box.configure(image="", text="No logo", width=24, height=4, bg=P["card_bg"],
                                    fg=P["muted"], font=F("italic"))

    def choose_logo(self) -> None:
        if self.book is None:
            return
        path = filedialog.askopenfilename(parent=self, title="Choose company logo",
                                          filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif"),
                                                     ("All files", "*.*")])
        if not path:
            return
        self.set_logo(path)

    def set_logo(self, path_or_bytes) -> bool:
        try:
            self.book.set_logo(path_or_bytes)
        except (ValueError, OSError) as exc:
            messagebox.showwarning("Company logo", str(exc), parent=self)
            return False
        self.app.mark_dirty()
        self.refresh_logo_preview()
        self.app.refresh_branding()
        self.app.status(f"Logo added.{self.app.save_hint()}")
        return True

    def remove_logo(self) -> None:
        if self.book is None or not self.book.logo_png:
            return
        self.book.clear_logo()
        self.app.mark_dirty()
        self.refresh_logo_preview()
        self.app.refresh_branding()
        self.app.status(f"Logo removed.{self.app.save_hint()}")

    def _update_swatch(self) -> None:
        val = self.vars["brand_color"].get().strip()
        ok = branding.is_valid_color(val)
        self.swatch.configure(bg=val if ok else P["bg"], text="" if ok else "?",
                              fg=P["error_fg"])

    def _pick_color(self) -> None:
        cur = self.vars["brand_color"].get().strip()
        rgb, hx = colorchooser.askcolor(color=branding.brand_color(cur), parent=self, title="Brand color")
        if hx:
            self.vars["brand_color"].set(hx.upper())

    def relayout_sections(self) -> None:
        """Two columns normally; one column at large text sizes so nothing needs sideways scrolling."""
        single = _appearance["scale"] >= 150
        if single == self._sections_single:
            return
        self._sections_single = single
        f = self.section_frames
        if single:
            order = ("Company", "Bank", "Branding", "Pay", "Time off", "Checks")
            for i, name in enumerate(order):
                f[name].grid_configure(row=i, column=0)
            self.adv_outer.grid_configure(row=len(order), column=0, columnspan=1)
        else:
            for name, (r, c) in {"Company": (0, 0), "Bank": (1, 0), "Branding": (2, 0), "Pay": (0, 1),
                                 "Checks": (1, 1), "Time off": (2, 1)}.items():
                f[name].grid_configure(row=r, column=c)
            self.adv_outer.grid_configure(row=3, column=0, columnspan=2)

    def _update_layout_state(self) -> None:
        manual = self.vars.get("check_layout") is not None and self.vars["check_layout"].get() == "manual_3up"
        for w in self.manual_widgets:
            if manual:
                w.grid()
            else:
                w.grid_remove()

    def _update_ot_state(self) -> None:
        state = "normal" if self.vars["overtime_enabled"].get() else "disabled"
        for f in self.OT_FIELDS:
            self.widgets[f].configure(state=state)

    def _toggle_show(self) -> None:
        for e in self.secret_entries:
            e.configure(show="" if self.v_show.get() else "•")

    def _pick_font(self, var: tk.StringVar) -> None:
        p = filedialog.askopenfilename(parent=self, title="Select MICR E-13B font",
                                       filetypes=[("TrueType fonts", "*.ttf"), ("All files", "*.*")])
        if p:
            var.set(p)

    def refresh(self) -> None:
        if self.book is None:
            return
        c = self.book.company
        for _s, fname, _l, kind, _o in self.FIELDS:
            val = getattr(c, fname, None)
            if kind == "bool":
                self.vars[fname].set(bool(val))
            else:
                self.vars[fname].set("" if val is None else str(val))
        self.v_show.set(False)
        self._toggle_show()
        self._update_ot_state()
        self._update_layout_state()
        self._update_swatch()
        self.refresh_logo_preview()
        self.relayout_sections()
        if any(getattr(c, f[1], 0) for f in self.FIELDS if f[0] == "Advanced") and not self.v_adv_open.get():
            self._toggle_advanced()   # show non-zero withholding so it isn't hidden

    def save(self) -> None:
        if self.book is None or self.locked:
            return
        c = self.book.company
        changes = {}
        errors = []
        for _s, fname, label, kind, _o in self.FIELDS:
            if kind == "readonly":
                continue
            if kind == "bool":
                changes[fname] = bool(self.vars[fname].get())
                continue
            raw = self.vars[fname].get().strip()
            try:
                if kind == "int":
                    val = int(raw)
                    if val < 0:
                        raise ValueError
                elif kind == "float":
                    val = float(raw or 0)
                else:
                    val = raw
            except ValueError:
                errors.append(f"{label}: '{raw}' is not a valid number.")
                continue
            changes[fname] = val
        rn = changes.get("routing_number", "")
        if rn and not (rn.isdigit() and len(rn) == 9):
            errors.append("Routing number must be exactly 9 digits.")
        an = changes.get("account_number", "")
        if an and not re.fullmatch(r"[0-9 -]+", an):
            errors.append("Account number may contain only digits, spaces and dashes.")
        if not 0 <= changes.get("default_lunch_minutes", 0) <= 240:
            errors.append("Default lunch deduct must be 0 to 240 minutes.")
        if not all(0 <= changes.get(k, 8) <= 24 for k in ("pto_hours", "holiday_hours", "sick_hours")):
            errors.append("PTO, Holiday and Sick day hours must be between 0 and 24.")
        if changes.get("overtime_enabled"):
            if changes.get("overtime_multiplier", 1) < 1:
                errors.append("Overtime multiplier must be at least 1.")
            if changes.get("overtime_threshold_weekly", 40) <= 0:
                errors.append("Overtime threshold must be more than 0 hours.")
        for k in ("federal_withholding_pct", "state_withholding_pct", "social_security_pct", "medicare_pct"):
            if not 0 <= changes.get(k, 0) <= 100:
                errors.append("Withholding percentages must be between 0 and 100.")
                break
        if not changes.get("company_name"):
            errors.append("Company name is required.")
        if not branding.is_valid_color(changes.get("brand_color", "")):
            errors.append("Brand color must look like #1F3A5F (a # and six hex digits).")
        else:
            changes["brand_color"] = changes["brand_color"].upper()
        if changes.get("check_layout") not in {k for k, _ in CHECK_LAYOUTS}:
            errors.append("Choose a check layout.")
        if changes.get("check_layout") == "manual_3up":
            w, h, stub = (changes.get(k, 0) for k in MANUAL_FIELDS)
            if not (3 <= w <= 14 and 3 <= h <= 14):
                errors.append("Sheet width and height must be between 3 and 14 inches.")
            if not 0 <= stub < w:
                errors.append("Stub width must be less than the sheet width.")
        if errors:
            messagebox.showwarning("Company Settings", "\n".join(errors), parent=self)
            return
        new = dataclasses.replace(c, **changes)
        per_period = [e for e in self.book.active_employees()
                      if is_salaried(e) and calc.salary_is_per_period(e) and self.book.has_pay_rate(e.employee_id)]
        if new.pay_period != c.pay_period and per_period:
            if not messagebox.askokcancel(
                    "Pay period changed",
                    f"The pay period is changing from {c.pay_period} to {new.pay_period}.\n\n"
                    "These people have a salary per pay period that was set for the old schedule:\n\n"
                    + "\n".join(e.full_name for e in per_period)
                    + "\n\nTheir amounts will be cleared so they can be re-entered for the new schedule. "
                      "They are left out of Pay & Checks until then.", parent=self):
                return
            for e in per_period:
                self.book.set_pay_rate(e.employee_id, Decimal("0"))
        if new != c:
            self.book.company = new
            self.app.mark_dirty()
            self.app.refresh_all()
            self.app.refresh_branding()
            self.app.status(f"Company settings updated.{self.app.save_hint()}")
        else:
            self.app.status("No changes to company settings.")


# --------------------------------------------------------------------------- updates

def _place_over(win: tk.Toplevel, master) -> None:
    win.update_idletasks()
    top = master.winfo_toplevel()
    x = top.winfo_rootx() + max(0, (top.winfo_width() - win.winfo_reqwidth()) // 2)
    y = top.winfo_rooty() + max(0, (top.winfo_height() - win.winfo_reqheight()) // 3)
    win.geometry(f"+{x}+{y}")


class UpdateDialog(tk.Toplevel):
    """'Version X is available' with the release notes. Result: "install", "skip" or None (later)."""

    def __init__(self, master, info: updates.ReleaseInfo):
        super().__init__(master)
        self.withdraw()
        self.title("Update Available")
        self.transient(master.winfo_toplevel())
        self.info, self.result = info, None
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"{APP_NAME} {info.version} is available", style="Bold.TLabel").pack(anchor="w")
        ttk.Label(f, text=f"You have version {__version__}. Updating keeps your workbooks, PIN and settings.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        ttk.Label(f, text="What's new:").pack(anchor="w")
        box = ttk.Frame(f)
        box.pack(fill="both", expand=True, pady=(2, 0))
        notes = tk.Text(box, width=64, height=12, font=F("base"), relief="flat", bd=1, wrap="word")
        sb = ttk.Scrollbar(box, orient="vertical", command=notes.yview)
        notes.configure(yscrollcommand=sb.set)
        notes.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        notes.insert("1.0", info.notes or "No release notes were published for this version.")
        notes.configure(state="disabled")
        ttk.Button(f, text="View on GitHub", command=lambda: webbrowser.open(info.page_url)).pack(anchor="w",
                                                                                             pady=(8, 0))
        btns = ttk.Frame(f, padding=(0, 12, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Later", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Skip This Version", command=lambda: self._done("skip")).pack(side="right",
                                                                                          padx=(0, 8))
        install = ttk.Button(btns, text="Download and Install", style="Accent.TButton",
                             command=lambda: self._done("install"))
        install.pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        apply_tk_colors(self)
        _place_over(self, master)
        self.deiconify()
        self.grab_set()
        install.focus_set()

    def _done(self, result: str) -> None:
        self.result = result
        self.destroy()

    def show(self):
        self.wait_window(self)
        return self.result


class DownloadDialog(tk.Toplevel):
    """Downloads the release's Setup.exe with a progress bar. Result: the downloaded Path, or None."""

    def __init__(self, master, info: updates.ReleaseInfo, dest_dir: Path):
        super().__init__(master)
        self.withdraw()
        self.title("Downloading Update")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.result: Path | None = None
        self._state: dict = {"done": 0, "total": info.setup_size, "cancel": False}
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=f"Downloading {APP_NAME} {info.version}…", style="Bold.TLabel").pack(anchor="w")
        self.v_msg = tk.StringVar(value="Starting…")
        ttk.Label(f, textvariable=self.v_msg, style="Muted.TLabel", width=44).pack(anchor="w", pady=(2, 8))
        self.bar = ttk.Progressbar(f, mode="determinate", length=360, maximum=1000)
        self.bar.pack(fill="x")
        btns = ttk.Frame(f, padding=(0, 12, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel", command=self.cancel).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())
        apply_tk_colors(self)
        _place_over(self, master)
        self.deiconify()
        self.grab_set()
        self._worker = threading.Thread(target=self._work, args=(info, dest_dir), daemon=True)
        self._worker.start()
        self.after(150, self._poll)

    def _progress(self, done: int, total: int) -> None:
        if self._state["cancel"]:
            raise updates.UpdateError("cancelled")
        self._state.update(done=done, total=total)

    def _work(self, info, dest_dir) -> None:
        try:
            self._state["path"] = updates.download(info, dest_dir, self._progress)
        except updates.UpdateError as exc:
            self._state["error"] = exc
        except Exception as exc:  # noqa: BLE001 - reported below
            log.exception("Update download failed")
            self._state["error"] = updates.UpdateError(f"The download failed: {exc}")

    def _poll(self) -> None:
        st = self._state
        mb = lambda n: f"{n / 1_048_576:.1f}"
        if st["total"]:
            self.bar["value"] = min(1000, st["done"] * 1000 // st["total"])
            self.v_msg.set(f"{mb(st['done'])} of {mb(st['total'])} MB")
        else:
            self.v_msg.set(f"{mb(st['done'])} MB")
        if self._worker.is_alive():
            self.after(150, self._poll)
            return
        if "error" in st and not st["cancel"]:
            messagebox.showwarning("Update", str(st["error"]), parent=self)
        self.result = st.get("path") if not st["cancel"] else None
        self.destroy()

    def cancel(self) -> None:
        self._state["cancel"] = True
        self.v_msg.set("Cancelling…")

    def show(self):
        self.wait_window(self)
        return self.result


# --------------------------------------------------------------------------- main window

class App(tk.Tk):
    def __init__(self, load_last: bool = True):
        super().__init__(className="TimeCardPro")
        setup_logging()
        self.settings = Settings()
        self.book: TimecardBook | None = None
        self.title(APP_NAME)
        self.minsize(980, 640)
        self._set_icon()
        self.v_theme = tk.StringVar(value=self.settings.data.get("theme") or "Light")
        self.v_scale = tk.IntVar(value=int(self.settings.data.get("text_scale") or 100))
        self._setup_style()
        self._restore_geometry()
        self.report_callback_exception = self._report_exception
        sys.excepthook = lambda et, ev, tb: self._report_exception(et, ev, tb)

        self._build_menu()
        self._build_header()
        self.v_status = tk.StringVar(value="Ready")
        self.v_dirty = tk.StringVar(value="")
        status = ttk.Frame(self, style="Status.TFrame", padding=(10, 3))
        status.pack(side="bottom", fill="x")
        self.lbl_status = ttk.Label(status, textvariable=self.v_status, style="Status.TLabel")
        self.lbl_status.pack(side="left")
        ttk.Label(status, textvariable=self.v_dirty, style="Status.TLabel").pack(side="right")

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(self.body, padding=(8, 8, 8, 4))
        self.timecard_tab = TimeCardTab(self.nb, self)
        self.employees_tab = EmployeesTab(self.nb, self)
        self.yearlog_tab = YearLogTab(self.nb, self)
        self.payroll_tab = PayrollTab(self.nb, self)
        self.company_tab = CompanyTab(self.nb, self)
        for tab, text in ((self.timecard_tab, "Time Card"), (self.employees_tab, "Employees"),
                          (self.yearlog_tab, "Year Log"), (self.payroll_tab, "Pay & Checks"),
                          (self.company_tab, "Company Settings")):
            self.nb.add(tab, text=f"  {text}  ")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.welcome = self._build_welcome(self.body)
        self._show_book_ui(False)

        self.protocol("WM_DELETE_WINDOW", self.exit)
        self._last_activity = _time.monotonic()
        for seq in ("<KeyPress>", "<ButtonPress>", "<Motion>"):
            self.bind_all(seq, self.note_activity, add="+")
        self._idle_job = self.after(10_000, self._idle_check)
        self.bind_all("<Control-s>", lambda e: (self.save(), "break")[1])
        self.bind_all("<Control-o>", lambda e: (self.open_dialog(), "break")[1])
        self.bind_all("<Control-n>", lambda e: (self.new_year_workbook(), "break")[1])
        for seq in ("<Control-equal>", "<Control-plus>", "<Control-KP_Add>"):
            self.bind_all(seq, lambda e: (self.change_text_size(1), "break")[1])
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind_all(seq, lambda e: (self.change_text_size(-1), "break")[1])
        for seq in ("<Control-0>", "<Control-KP_0>"):
            self.bind_all(seq, lambda e: (self.set_appearance(scale=100), "break")[1])
        apply_tk_colors(self)
        self._update_view_buttons()
        self.refresh_branding()

        self._update_thread: threading.Thread | None = None
        self._autosave_job: str | None = None
        self._unbacked_autosave = False   # auto-saves since the last backup (backed up when closing)
        self._autosave_failed = False     # last attempt failed (status bar shows an error)
        self._autosave_paused = False     # file changed outside the app: wait for the user
        self._snapshot_taken = False      # copy of the file as opened, made before the first autosave
        if getattr(sys, "frozen", False):   # only the installed/portable app checks; not tests or source runs
            self.after(4000, self._startup_update_check)

        if load_last:
            self.after(50, self._open_last)

    # ----- setup
    def _set_icon(self) -> None:
        candidates = []
        if getattr(sys, "frozen", False):
            candidates.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "timecard.ico")
            candidates.append(Path(sys.executable).parent / "timecard.ico")
        candidates.append(Path(__file__).resolve().parent.parent / "packaging" / "timecard.ico")
        for p in candidates:
            if p.is_file():
                try:
                    self.iconbitmap(default=str(p))
                    return
                except tk.TclError:
                    pass

    def _setup_style(self) -> None:
        apply_appearance(self, self.v_theme.get(), self.v_scale.get())
        self.v_theme.set(_appearance["theme"])
        self.v_scale.set(_appearance["scale"])

    # ----- View: theme + text size (live, no restart)
    def set_appearance(self, theme: str | None = None, scale: int | None = None) -> None:
        theme = theme or self.v_theme.get()
        scale = scale or self.v_scale.get()
        apply_appearance(self, theme, scale)
        self.v_theme.set(_appearance["theme"])
        self.v_scale.set(_appearance["scale"])
        self.settings.data["theme"] = _appearance["theme"]
        self.settings.data["text_scale"] = _appearance["scale"]
        self.settings.save()
        self._update_view_buttons()
        self.refresh_branding()
        if hasattr(self, "company_tab"):
            self.company_tab.relayout_sections()
            self.company_tab.refresh_logo_preview()
        self.update_idletasks()
        relayout_flows()
        if hasattr(self, "timecard_tab") and self.book is not None:
            self.timecard_tab.update_totals()
        self.status(f"{_appearance['theme']} theme, text size {_appearance['scale']}%.")

    def refresh_branding(self) -> None:
        """Header logo, brand-coloured header (Light) or brand stripe (Dark/High Contrast); live."""
        _brand["color"] = self.book.company.brand_color if self.book is not None else ""
        P.update(THEMES[_appearance["theme"]])
        _apply_brand_palette()
        apply_tk_colors(self._hdr)
        apply_tk_colors(self.hdr_stripe)
        png = self.book.logo_png if self.book is not None else b""
        self._hdr_logo_img = logo_photo(self, png, scaled(160), scaled(40))
        if self._hdr_logo_img is not None:
            self.hdr_logo.configure(image=self._hdr_logo_img)
            self.hdr_logo.pack(side="left", padx=(0, 12), before=self.lbl_company.master)
        else:
            self.hdr_logo.configure(image="")
            self.hdr_logo.pack_forget()
        color = self.book.company.brand_color if self.book is not None else ""
        show_stripe = (_appearance["theme"] != "Light" and branding.is_valid_color(color or "")
                       and branding.brand_color(color) != branding.DEFAULT_BRAND_COLOR)
        if show_stripe:
            self.hdr_stripe.pack(side="top", fill="x", after=self._hdr)
        else:
            self.hdr_stripe.pack_forget()

    def change_text_size(self, step: int) -> None:
        cur = TEXT_SCALES.index(_appearance["scale"]) if _appearance["scale"] in TEXT_SCALES else 0
        nxt = min(max(cur + step, 0), len(TEXT_SCALES) - 1)
        self.set_appearance(scale=TEXT_SCALES[nxt])

    def next_theme_name(self) -> str:
        i = THEME_NAMES.index(_appearance["theme"]) if _appearance["theme"] in THEME_NAMES else 0
        return THEME_NAMES[(i + 1) % len(THEME_NAMES)]

    def cycle_theme(self) -> None:
        self.set_appearance(theme=self.next_theme_name())

    def _update_view_buttons(self) -> None:
        if hasattr(self, "btn_theme"):
            self.btn_theme.configure(text=f"\u25D0 {self.next_theme_name()}")
            self.btn_smaller.configure(state="disabled" if _appearance["scale"] <= TEXT_SCALES[0] else "normal")
            self.btn_larger.configure(state="disabled" if _appearance["scale"] >= TEXT_SCALES[-1] else "normal")

    def _restore_geometry(self) -> None:
        geo = self.settings.data.get("geometry") or ""
        m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geo)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if m:
            w, h, x, y = map(int, m.groups())
            if w <= sw + 50 and h <= sh + 50 and -50 <= x < sw - 100 and -10 <= y < sh - 100:
                self.geometry(geo)
            else:
                self.geometry(f"{min(w, sw)}x{min(h, sh - 60)}+40+40")
        else:
            w, h = min(1200, sw - 80), min(800, sh - 100)
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        if self.settings.data.get("zoomed"):
            try:
                self.state("zoomed")
            except tk.TclError:
                pass

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        fm = tk.Menu(menubar, tearoff=0)
        fm.add_command(label="New Year Workbook\u2026", accelerator="Ctrl+N", command=self.new_year_workbook)
        fm.add_command(label="Open\u2026", accelerator="Ctrl+O", command=self.open_dialog)
        self.recent_menu = tk.Menu(fm, tearoff=0)
        fm.add_cascade(label="Recent Files", menu=self.recent_menu)
        fm.add_separator()
        fm.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        fm.add_command(label="Save As\u2026", command=self.save_as)
        self.v_autosave = tk.BooleanVar(value=bool(self.settings.data.get("autosave", True)))
        fm.add_checkbutton(label="Save automatically", variable=self.v_autosave, command=self._toggle_autosave)
        fm.add_separator()
        fm.add_command(label="Open in GridCraft", command=self.open_in_gridcraft)
        fm.add_command(label="Open Output Folder", command=self.open_output_folder)
        fm.add_separator()
        fm.add_command(label="Change PIN\u2026", command=self.change_pin)
        fm.add_command(label="Change Security Question\u2026", command=self.change_security_question)
        fm.add_command(label="Lock Employees and Company Settings", command=self.lock_employees)
        fm.add_separator()
        fm.add_command(label="Exit", command=self.exit)
        menubar.add_cascade(label="File", menu=fm)
        vm = tk.Menu(menubar, tearoff=0)
        for name in THEME_NAMES:
            vm.add_radiobutton(label=f"{name} theme", value=name, variable=self.v_theme,
                               command=lambda n=name: self.set_appearance(theme=n))
        vm.add_separator()
        for sc in TEXT_SCALES:
            vm.add_radiobutton(label=f"Text size {sc}%", value=sc, variable=self.v_scale,
                               command=lambda v=sc: self.set_appearance(scale=v))
        vm.add_separator()
        vm.add_command(label="Larger text", accelerator="Ctrl+=", command=lambda: self.change_text_size(1))
        vm.add_command(label="Smaller text", accelerator="Ctrl+-", command=lambda: self.change_text_size(-1))
        vm.add_command(label="Reset text size", accelerator="Ctrl+0", command=lambda: self.set_appearance(scale=100))
        menubar.add_cascade(label="View", menu=vm)
        hm = tk.Menu(menubar, tearoff=0)
        hm.add_command(label="Check for Updates\u2026", command=self.check_for_updates)
        self.v_update_on_start = tk.BooleanVar(value=bool(self.settings.data.get("update_check_on_start", True)))
        hm.add_checkbutton(label="Check for updates at startup", variable=self.v_update_on_start,
                           command=self._toggle_update_on_start)
        hm.add_separator()
        hm.add_command(label=f"About {APP_NAME}", command=self.about)
        menubar.add_cascade(label="Help", menu=hm)
        self.configure(menu=menubar)
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        self.recent_menu.delete(0, "end")
        recent = self.settings.recent
        if not recent:
            self.recent_menu.add_command(label="(none)", state="disabled")
            return
        for i, (p, label) in enumerate(zip(recent, recent_labels(recent)), 1):
            self.recent_menu.add_command(label=f"{i}  {label}", command=lambda p=p: self.open_path(p))
        self.recent_menu.add_separator()
        self.recent_menu.add_command(label="Clear list", command=self._clear_recent)

    def _clear_recent(self) -> None:
        self.settings.data["recent"] = []
        self.settings.save()
        self._rebuild_recent()

    def _build_header(self) -> None:
        hdr = tag_role(tk.Frame(self, padx=16, pady=10), "header")
        hdr.pack(side="top", fill="x")
        self.hdr_logo = tag_role(tk.Label(hdr, bd=0), "header")
        self._hdr_logo_img = None
        self.hdr_stripe = tag_role(tk.Frame(self, height=4), "stripe")
        left = tag_role(tk.Frame(hdr), "header")
        self.lbl_company = tag_role(tk.Label(left, text=APP_NAME, font=F("title"), anchor="w"), "header_title")
        self.lbl_company.pack(anchor="w")
        self.lbl_path = tag_role(tk.Label(left, text="No workbook open", font=F("small"), anchor="w"),
                                 "header_sub")
        self.lbl_path.pack(anchor="w")
        # compact, keyboard-accessible view controls (Tab to them, Space/Enter to press)
        view = tag_role(tk.Frame(hdr), "header")
        view.pack(side="right", padx=(12, 0))

        def hbtn(text, cmd, tip):
            b = tag_role(tk.Button(view, text=text, command=cmd, font=F("bold"), padx=8, pady=2,
                                   cursor="hand2", takefocus=1), "header_btn")
            b.bind("<Return>", lambda e: (cmd(), "break")[1])
            b.bind("<FocusIn>", lambda e: self.status(tip))
            return b

        self.btn_smaller = hbtn("A\u2212", lambda: self.change_text_size(-1), "Smaller text (Ctrl+-)")
        self.btn_larger = hbtn("A+", lambda: self.change_text_size(1), "Larger text (Ctrl+=)")
        self.btn_theme = hbtn("\u25D0 Dark", self.cycle_theme, "Switch colour theme (View menu)")
        self.btn_smaller.pack(side="left", padx=(0, 4))
        self.btn_larger.pack(side="left", padx=(0, 4))
        self.btn_theme.pack(side="left")
        self.lbl_year = tag_role(tk.Label(hdr, text="", font=F("year"), padx=14, pady=4), "year")
        self.lbl_year.pack(side="right")
        if paths.is_portable():
            drive = os.path.splitdrive(str(paths.app_dir()))[0] or str(paths.app_dir())
            tag_role(tk.Label(hdr, text=f"PORTABLE \u2014 data on {drive}", font=F("small_bold"), padx=10, pady=4),
                     "portable").pack(side="right", padx=(0, 10))
        self.hdr_logo.pack(side="left", padx=(0, 12))
        self.hdr_logo.pack_forget()
        left.pack(side="left", fill="x", expand=True)   # packed last so the right side never clips
        self._hdr = hdr

    def _build_welcome(self, master) -> ttk.Frame:
        f = ttk.Frame(master, padding=40)
        inner = ttk.Frame(f)
        inner.place(relx=0.5, rely=0.4, anchor="center")
        ttk.Label(inner, text=f"Welcome to {APP_NAME}", style="Welcome.TLabel").pack(pady=(0, 6))
        ttk.Label(inner, text="Each business keeps one workbook per year. Create a new one or open an existing "
                  "workbook.", style="Muted.TLabel").pack(pady=(0, 18))
        b = ttk.Frame(inner)
        b.pack()
        ttk.Button(b, text="New Year Workbook\u2026", style="Accent.TButton",
                   command=self.new_year_workbook).pack(side="left", padx=6, ipadx=10, ipady=4)
        ttk.Button(b, text="Open Workbook\u2026", command=self.open_dialog).pack(side="left", padx=6, ipadx=10,
                                                                               ipady=4)
        return f

    def _show_book_ui(self, has_book: bool) -> None:
        if has_book:
            self.welcome.pack_forget()
            self.nb.pack(fill="both", expand=True)
        else:
            self.nb.pack_forget()
            self.welcome.pack(fill="both", expand=True)

    # ----- error handling
    def _report_exception(self, exc_type, exc, tb) -> None:
        log.error("Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        if isinstance(exc, WorkbookError):
            msg = str(exc)
        else:
            msg = f"{exc_type.__name__}: {exc}\n\nDetails were written to:\n{LOG_PATH}"
        try:
            messagebox.showerror(f"{APP_NAME} - Error", msg, parent=self)
        except tk.TclError:
            try:
                messagebox.showerror(f"{APP_NAME} - Error", msg)
            except tk.TclError:
                pass

    # ----- status / title
    def status(self, msg: str, error: bool = False) -> None:
        self.v_status.set(msg)
        self.lbl_status.configure(style="StatusErr.TLabel" if error else "Status.TLabel")

    def mark_dirty(self) -> None:
        if self.book is not None:
            self.book.dirty = True
        self.update_title()
        self._schedule_autosave()

    # ----- autosave
    def save_hint(self) -> str:
        """Tail for status messages after a change: nothing when it will be saved automatically."""
        return "" if self.settings.data.get("autosave", True) else " Remember to save the workbook."

    def _toggle_autosave(self) -> None:
        self.settings.data["autosave"] = bool(self.v_autosave.get())
        self.settings.save()
        if self.v_autosave.get():
            self._schedule_autosave()
        elif self._autosave_job is not None:   # turned off: drop a save that was already on its way
            self.after_cancel(self._autosave_job)
            self._autosave_job = None

    def _schedule_autosave(self, delay_ms: int = AUTOSAVE_DELAY_MS) -> None:
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        if self.settings.data.get("autosave", True) and self.book is not None and self.book.path is not None:
            self._autosave_job = self.after(delay_ms, self._autosave)

    def _autosave(self) -> None:
        """Quietly save the open workbook; a locked file only warns in the status bar and retries."""
        self._autosave_job = None
        if (self.book is None or self.book.path is None or not self.book.dirty
                or not self.settings.data.get("autosave", True) or self._autosave_paused
                or self.book.from_newer_app):
            return
        if self.book.changed_on_disk():
            # GridCraft / Excel / another PC changed the file: never overwrite that without asking
            self._autosave_paused = True
            log.warning("Autosave paused: %s changed outside the app", self.book.path)
            self.status(f"{self.book.path.name} was changed outside TimeCard Pro, so automatic saving is paused. "
                        "Open it again to see those changes, or use File > Save to replace them.", error=True)
            return
        self._snapshot_before_first_autosave()
        try:
            self.book.save()
        except WorkbookError as exc:
            log.warning("Autosave failed: %s", exc)
            self._autosave_failed = True
            self.status(f"Could not save automatically ({exc}). Close the workbook in other programs; "
                        "TimeCard Pro will try again.", error=True)
            self._schedule_autosave(AUTOSAVE_RETRY_MS)
            return
        self._unbacked_autosave = True
        if self._autosave_failed:
            self._autosave_failed = False
            self.status("Saved automatically.")
        self.update_title()

    def _snapshot_before_first_autosave(self) -> None:
        """Keep a copy of the workbook as it was opened, so automatic saving can always be undone. Goes to the
        backup folder when one is set, else a "TimeCard Pro Backups" folder next to the workbook."""
        if self._snapshot_taken or self.book is None or self.book.path is None:
            return
        self._snapshot_taken = True
        dest = self.settings.backup_dir if self.settings.backup_dir and Path(self.settings.backup_dir).is_dir() \
            else str(Path(self.book.path).parent)
        try:
            backup.backup_workbook(self.book.path, dest, keep=int(self.settings.data.get("backup_keep")
                                                                  or backup.DEFAULT_KEEP))
        except OSError as exc:   # includes FileNotFoundError for a workbook not on disk yet
            log.warning("Could not copy the workbook before autosaving: %s", exc)

    def _backup_after_autosaves(self) -> None:
        """Auto-saves skip the backup copy (it would fill the backup folder); make one when leaving the book."""
        if self._unbacked_autosave and self.book is not None and self.book.path is not None:
            self._unbacked_autosave = False
            self.auto_backup()

    def update_title(self) -> None:
        if self.book is None:
            self.title(APP_NAME)
            self.lbl_company.configure(text=APP_NAME)
            self.lbl_path.configure(text="No workbook open")
            self.lbl_year.configure(text="")
            self.v_dirty.set("")
            return
        name = self.book.path.name if self.book.path else "Untitled workbook"
        star = "*" if self.book.dirty else ""
        self.title(f"{star}{name} - {APP_NAME}")
        self.lbl_company.configure(text=self.book.company.company_name or APP_NAME)
        # File name only: the full path would show visitors at the desk where the (unlocked) workbook lives.
        self.lbl_path.configure(text=self.book.path.name if self.book.path else "Not saved yet")
        self.lbl_year.configure(text=str(self.book.company.year))
        self.v_dirty.set("Unsaved changes" if self.book.dirty else "All changes saved")

    # ----- book lifecycle
    def set_book(self, book: TimecardBook) -> None:
        self._backup_after_autosaves()
        if self._autosave_job is not None:
            self.after_cancel(self._autosave_job)
            self._autosave_job = None
        self.book = book
        self._autosave_paused = self._autosave_failed = self._snapshot_taken = False
        self.payroll_tab.period_chosen = False
        self._show_book_ui(True)
        self.employees_tab.selected = None
        self.refresh_all(keep_selection=False)
        self.refresh_branding()
        if book.path:
            self.settings.add_recent(book.path)
            self._rebuild_recent()

    def refresh_all(self, keep_selection: bool = True) -> None:
        if self.book is None:
            return
        self.update_title()
        self.timecard_tab.refresh(keep_selection=keep_selection)
        self.employees_tab.refresh()
        self.yearlog_tab.refresh()
        self.payroll_tab.refresh()
        self.company_tab.refresh()

    def refresh_employees(self) -> None:
        self.employees_tab.refresh()
        self.timecard_tab.refresh()
        self.yearlog_tab.refresh()
        self.payroll_tab.on_period_change()   # drop any preview that may include a changed person

    def _on_tab_changed(self, _e=None) -> None:
        if self.book is None:
            return
        self.commit_pending()
        current = self.nb.nametowidget(self.nb.select())
        for tab in self.pin_tabs:
            if current is not tab and not tab.locked:
                tab.lock()
        if current in self.pin_tabs and current.locked:
            current.overlay.show()   # refreshes create/unlock mode if a PIN was set on the other tab
            current.overlay.focus_pin()
        if current is self.yearlog_tab:
            self.yearlog_tab.reload()
        elif current is self.payroll_tab:
            self.payroll_tab.on_shown()
        elif current is self.timecard_tab:
            self.timecard_tab.update_totals()

    def commit_pending(self) -> None:
        if self.book is not None:
            self.timecard_tab.commit_pending()

    # ----- PIN / idle lock
    def note_activity(self, _e=None) -> None:
        self._last_activity = _time.monotonic()

    @property
    def pin_tabs(self) -> tuple:
        """Tabs behind the PIN."""
        return self.employees_tab, self.company_tab

    def _idle_check(self) -> None:
        open_tabs = [t for t in self.pin_tabs if not t.locked]
        if open_tabs and _time.monotonic() - self._last_activity >= IDLE_LOCK_SECONDS:
            for tab in open_tabs:
                tab.lock()
            self.status("Employee records and company settings locked after 5 minutes of inactivity.")
        self._idle_job = self.after(10_000, self._idle_check)

    def lock_employees(self) -> None:
        for tab in self.pin_tabs:
            tab.lock()
        self.status("Employee records and company settings locked.")

    def change_pin(self) -> None:
        if not security.has_pin():
            messagebox.showinfo("Change PIN", "No PIN is set yet. Open the Employees tab to create one.",
                                parent=self)
            return
        if ChangePinDialog(self).show():
            self.status("PIN changed.")

    def change_security_question(self) -> None:
        if not security.has_pin():
            messagebox.showinfo("Change Security Question", "No PIN is set yet. Open the Employees tab to "
                                "create one.", parent=self)
            return
        if SecurityQuestionDialog(self).show():
            self.status("Security question saved.")

    def confirm_discard(self) -> bool:
        """True if it's OK to drop the current book (saved, discarded, or nothing to lose)."""
        self.commit_pending()
        if self.book is None or not self.book.dirty:
            return True
        if self.settings.data.get("autosave", True) and self.book.path is not None:
            self._autosave()   # saving automatically: save now instead of asking
            if not self.book.dirty:
                return True
        ans = messagebox.askyesnocancel(APP_NAME, "Save changes to the current workbook?", parent=self)
        if ans is None:
            return False
        if ans:
            return self.save()
        return True

    def _open_last(self) -> None:
        last = self.settings.last_workbook
        if last and Path(last).is_file():
            self.open_path(last, quiet_missing=True)

    def open_dialog(self) -> None:
        if not self.confirm_discard():
            return
        initial = Path(self.settings.last_workbook or documents_dir())
        path = filedialog.askopenfilename(parent=self, title="Open time card workbook",
                                          initialdir=str(initial.parent if initial.suffix else initial),
                                          filetypes=[("Excel workbooks", "*.xlsx"), ("All files", "*.*")])
        if path:
            self.open_path(path, confirmed=True)

    def open_path(self, path: str | Path, quiet_missing: bool = False, confirmed: bool = False) -> bool:
        if not confirmed and not self.confirm_discard():
            return False
        path = Path(path)
        if not path.is_file():
            if not quiet_missing:
                messagebox.showerror(APP_NAME, f"File not found:\n{path}", parent=self)
            return False
        try:
            book = TimecardBook.load(path)
        except WorkbookError as exc:
            messagebox.showerror(APP_NAME, f"Could not open the workbook:\n{path}\n\n{exc}", parent=self)
            return False
        self.set_book(book)
        self.status(f"Opened {Path(path).name}")
        log.info("Opened %s", path)
        if book.from_newer_app:
            log.warning("Opened a workbook from a newer version (schema %s, saved by %s)",
                        book.schema_version, book.saved_by or "?")
            self.status("Saved by a newer version of TimeCard Pro: changes here can't be saved over it.", error=True)
            messagebox.showwarning(APP_NAME, book.newer_message() + "\n\nUntil then, anything you change here can "
                                   "only be kept with File > Save As (a separate copy).", parent=self)
        return True

    def new_year_workbook(self) -> None:
        self.commit_pending()
        res = NewYearDialog(self, self.book).show()
        if not res:
            return
        if not self.confirm_discard():
            return
        initial_dir = self.book.path.parent if (self.book and self.book.path) else documents_dir()
        path = filedialog.asksaveasfilename(
            parent=self, title="Save new workbook as", defaultextension=".xlsx", initialdir=str(initial_dir),
            initialfile=paths.workbook_filename(res['name'], res['year']),
            filetypes=[("Excel workbooks", "*.xlsx")])
        if not path:
            return
        book = self.build_new_book(res, carry_from=self.book if res["carry"] else None)
        cleared = []
        if res["carry"] and self.book is not None and self.book.company.pay_period != book.company.pay_period:
            # a salary per pay period was set for the old schedule; it has to be re-entered for the new one
            for e in book.employees.values():
                if is_salaried(e) and calc.salary_is_per_period(e) and book.has_pay_rate(e.employee_id):
                    book.set_pay_rate(e.employee_id, Decimal("0"))
                    cleared.append(e.full_name)
        try:
            book.save(path)
        except WorkbookError as exc:
            messagebox.showerror(APP_NAME, f"Could not save the new workbook:\n{exc}", parent=self)
            return
        self.set_book(book)
        self.status(f"Created {Path(path).name}")
        if cleared:
            messagebox.showinfo("New Year Workbook", f"The pay period changed to {book.company.pay_period}, so "
                                "these salaries per pay period were cleared and need to be set again on the "
                                "Employees tab:\n\n" + "\n".join(cleared), parent=self)

    @staticmethod
    def build_new_book(res: dict, carry_from: TimecardBook | None = None) -> TimecardBook:
        if carry_from is not None:
            company = dataclasses.replace(carry_from.company, company_name=res["name"], year=res["year"],
                                          pay_period=res["pay_period"], week_start=res["week_start"])
        else:
            company = Company(company_name=res["name"], year=res["year"], pay_period=res["pay_period"],
                              week_start=res["week_start"])
        book = TimecardBook.new(company)
        if carry_from is not None:
            for emp in carry_from.employees.values():
                book.upsert_employee(dataclasses.replace(emp))
                if carry_from.has_pay_rate(emp.employee_id):
                    book.set_pay_rate(emp.employee_id, emp.pay_rate)
        return book

    def save(self) -> bool:
        if self.book is None:
            return False
        self.commit_pending()
        if self.book.path is None:
            return self.save_as()
        if self.book.from_newer_app:
            messagebox.showwarning(APP_NAME, self.book.newer_message() + "\n\nTo keep your changes, use File > "
                                   "Save As to save a separate copy.", parent=self)
            return False
        if self.book.changed_on_disk() and not messagebox.askyesno(
                APP_NAME, f"{self.book.path.name} was changed outside TimeCard Pro (for example in GridCraft) "
                "since it was opened here.\n\nSaving replaces those changes with what is shown in TimeCard Pro. "
                "To keep them instead, choose No and open the workbook again.\n\nReplace the file?", parent=self):
            return False
        try:
            p = self.book.save()
        except WorkbookError as exc:
            messagebox.showerror(APP_NAME, f"Could not save the workbook.\n\n{exc}\n\nIf it is open in GridCraft, "
                                 "close it there and try again.", parent=self)
            return False
        self._autosave_paused = self._autosave_failed = False
        self.update_title()
        self.status(f"Saved {p.name}")
        self.auto_backup()
        return True

    def auto_backup(self) -> None:
        """Back up after a save when the user turned that on; never blocks saving."""
        self._unbacked_autosave = False
        if self.settings.data.get("backup_on_save") and self.settings.backup_dir:
            self.backup_now(quiet=True)

    def backup_now(self, quiet: bool = False) -> Path | None:
        if self.book is None or self.book.path is None:
            if not quiet:
                messagebox.showinfo("Backup", "Save the workbook first.", parent=self)
            return None
        dest = self.settings.backup_dir
        if not dest:
            if not quiet:
                messagebox.showinfo("Backup", "Choose a backup folder first.", parent=self)
            return None
        try:
            copy = backup.backup_workbook(self.book.path, dest, int(self.settings.data.get("backup_keep")
                                                                    or backup.DEFAULT_KEEP))
        except OSError as exc:
            log.warning("Backup failed: %s", exc)
            msg = (f"Backup skipped: {dest} is not available. Is Google Drive (or the backup drive) connected?"
                   if not Path(dest).is_dir() else f"Backup failed: {exc}")
            self.status(msg, error=True)
            if not quiet:
                messagebox.showwarning("Backup", msg, parent=self)
            return None
        self.settings.data["last_backup"] = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        self.settings.save()
        self.status("Saved and backed up.")
        return copy

    def save_as(self) -> bool:
        if self.book is None:
            return False
        self.commit_pending()
        c = self.book.company
        initial_dir = self.book.path.parent if self.book.path else documents_dir()
        path = filedialog.asksaveasfilename(
            parent=self, title="Save workbook as", defaultextension=".xlsx", initialdir=str(initial_dir),
            initialfile=self.book.path.name if self.book.path else
            paths.workbook_filename(c.company_name, c.year),
            filetypes=[("Excel workbooks", "*.xlsx")])
        if not path:
            return False
        try:
            p = self.book.save(path)
        except WorkbookError as exc:
            messagebox.showerror(APP_NAME, f"Could not save the workbook.\n\n{exc}", parent=self)
            return False
        self.settings.add_recent(Path(p))
        self._rebuild_recent()
        self.update_title()
        self.status(f"Saved {p.name}")
        self.auto_backup()
        return True

    def open_in_gridcraft(self) -> None:
        if self.book is None:
            return
        if not self.save():
            return
        path = str(self.book.path)
        try:
            if GRIDCRAFT_EXE.is_file():
                subprocess.Popen([str(GRIDCRAFT_EXE), path], close_fds=True)
            else:
                os.startfile(path)  # type: ignore[attr-defined]
            self.status("Opened in GridCraft. Close it there before saving again from TimeCard Pro.")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not launch GridCraft:\n{exc}", parent=self)

    def output_dir(self) -> Path | None:
        if self.book is None:
            return None
        if self.book.path is None:
            if not messagebox.askyesno(APP_NAME, "Save the workbook first? PDFs are saved next to it.", parent=self):
                return None
            if not self.save_as():
                return None
        d = Path(self.book.path).parent / OUTPUT_DIR_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d

    def output_path(self, filename: str) -> Path | None:
        d = self.output_dir()
        if d is None:
            return None
        p = d / safe_filename(filename)
        if p.exists():
            try:
                with open(p, "ab"):
                    pass
            except OSError:  # open in a PDF viewer -> pick a new name
                stem, n = p.stem, 2
                while (d / f"{stem} ({n}).pdf").exists():
                    n += 1
                p = d / f"{stem} ({n}){p.suffix}"
        return p

    def open_output_folder(self) -> None:
        d = self.output_dir()
        if d is not None:
            open_file(d)

    def ask_start_position(self, count: int | None) -> int | None:
        """Which slot (1 top, 2 middle, 3 bottom) on the first 3-per-page check sheet to start at."""
        return StartPositionDialog(self, count).show()

    def goto_timecard(self, eid: str, d: date) -> None:
        emp = self.book.employees.get(eid) if self.book else None
        if emp is None or not emp.active:
            self.timecard_tab.show(eid, d)   # only reports that former team members aren't shown
            return
        self.nb.select(self.timecard_tab)
        self.timecard_tab.show(eid, d)

    def about(self) -> None:
        messagebox.showinfo(f"About {APP_NAME}",
                            f"{APP_NAME} {__version__}\n\nEmployee time cards, yearly time log, payroll and check "
                            f"printing.\nData is stored in an .xlsx workbook you can open in GridCraft.\n\n"
                            f"The PIN locks pay rates, bank numbers and settings inside this app. It doesn't lock "
                            f"the workbook file itself: keep the computer behind its Windows sign-in (and "
                            f"BitLocker on a laptop) and keep backups somewhere private.\n\n"
                            f"Settings and log: {SETTINGS_DIR}", parent=self)

    # ----- updates
    def _toggle_update_on_start(self) -> None:
        self.settings.data["update_check_on_start"] = bool(self.v_update_on_start.get())
        self.settings.save()

    def _startup_update_check(self) -> None:
        d = self.settings.data
        if not d.get("update_check_on_start", True) or d.get("last_update_check") == date.today().isoformat():
            return
        if self.grab_current() is not None:   # a dialog (e.g. PIN) is open: try again shortly
            self.after(30_000, self._startup_update_check)
            return
        self.check_for_updates(quiet=True)

    def check_for_updates(self, quiet: bool = False) -> None:
        """Ask GitHub for a newer release in the background. quiet=True (startup) only speaks up when there
        is a new version the user hasn't skipped; failures are just logged."""
        if self._update_thread is not None and self._update_thread.is_alive():
            return
        result: dict = {}

        def work() -> None:
            try:
                result["info"] = updates.check(__version__)
            except updates.UpdateError as exc:
                result["error"] = exc
            except Exception as exc:  # noqa: BLE001 - never let the check break the app
                log.exception("Update check failed")
                result["error"] = updates.UpdateError(f"The update check failed: {exc}")

        def poll() -> None:
            if self._update_thread.is_alive():
                self.after(200, poll)
                return
            self.settings.data["last_update_check"] = date.today().isoformat()
            self.settings.save()
            if "error" in result:
                log.info("Update check: %s", result["error"])
                if not quiet:
                    self.status("Update check failed.")
                    messagebox.showwarning("Check for Updates", str(result["error"]), parent=self)
                return
            info = result["info"]
            if info is None:
                if not quiet:
                    self.status("No updates available.")
                    messagebox.showinfo("Check for Updates", f"You have the latest version of {APP_NAME} "
                                        f"({__version__}).", parent=self)
                return
            if quiet and self.settings.data.get("skip_version") == info.version:
                return
            self.status(f"Version {info.version} is available.")
            choice = UpdateDialog(self, info).show()
            if choice == "skip":
                self.settings.data["skip_version"] = info.version
                self.settings.save()
            elif choice == "install":
                self.install_update(info)

        if not quiet:
            self.status("Checking for updates…")
        self._update_thread = threading.Thread(target=work, daemon=True)
        self._update_thread.start()
        self.after(200, poll)

    def install_update(self, info: updates.ReleaseInfo) -> None:
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Update", f"This copy is running from the source code, so it can't update "
                                f"itself. Use git pull to get version {info.version}, or download the installer "
                                f"from the releases page.", parent=self)
            return
        if not info.setup_url:
            if messagebox.askyesno("Update", f"Version {info.version} has no installer attached yet. Open the "
                                   f"releases page?", parent=self):
                webbrowser.open(info.page_url)
            return
        if not self.confirm_discard():
            return
        # Portable copies keep the download on their own drive; installed copies use the temp folder.
        dest = (paths.portable_data_dir() / "Updates" if paths.is_portable()
                else Path(tempfile.gettempdir()) / "TimeCardPro-Update")
        for old in dest.glob("TimeCardPro-Setup-*.exe") if dest.is_dir() else ():
            old.unlink(missing_ok=True)
        setup = DownloadDialog(self, info, dest).show()
        if setup is None:
            self.status("Update cancelled.")
            return
        try:
            subprocess.Popen([str(setup), "--update", sys.executable, "--wait-pid", str(os.getpid())],
                             cwd=str(setup.parent))
        except OSError as exc:
            messagebox.showerror("Update", f"Could not start the installer:\n{exc}", parent=self)
            return
        log.info("Starting update to %s; closing.", info.version)
        self._close()

    def exit(self) -> None:
        if not self.confirm_discard():
            return
        self._close()

    def _close(self) -> None:
        """Remember the window state and close (the caller has already dealt with unsaved changes)."""
        self._backup_after_autosaves()
        try:
            zoomed = self.state() == "zoomed"
            self.settings.data["zoomed"] = zoomed
            if not zoomed:
                self.settings.data["geometry"] = self.geometry()
            if self.book is not None and self.book.path is not None:
                self.settings.last_workbook = self.book.path
            self.settings.save()
        except tk.TclError:
            pass
        self.destroy()


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001 - best effort only
            pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    _enable_dpi_awareness()
    app = App(load_last=not argv)
    if argv and argv[0].lower().endswith(".xlsx"):
        app.after(50, lambda: app.open_path(argv[0]))
    app.mainloop()
    return 0
