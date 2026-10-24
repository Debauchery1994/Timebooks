"""Shared UI pieces: constants, themes and text size, settings, small helpers and reusable widgets."""
from __future__ import annotations

import io
import json
import logging
import logging.handlers
import os
import re
import tkinter as tk
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import font as tkfont, messagebox, ttk

from ..models import Company, Employee, HoursSummary
from .. import APP_NAME, backup, branding, calc, paths


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
