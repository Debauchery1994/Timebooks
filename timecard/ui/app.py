"""The main window: menus, header, tabs, opening and saving workbooks, autosave, backups and updates."""
from __future__ import annotations

import dataclasses
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
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..models import Company
from .. import APP_NAME, __version__, backup, branding, calc, paths, security, updates
from ..storage import TimecardBook, WorkbookError
from .common import (
    AUTOSAVE_DELAY_MS, AUTOSAVE_RETRY_MS, F, GRIDCRAFT_EXE, LOG_PATH, OLD_OUTPUT_DIR_NAME, OUTPUT_DIR_NAME, P,
    SETTINGS_DIR, Settings, TEXT_SCALES, THEMES, THEME_NAMES, _appearance, _apply_brand_palette, _brand,
    apply_appearance, apply_tk_colors,
    documents_dir, is_salaried, log, logo_photo, open_file, recent_labels, relayout_flows, safe_filename, scaled,
    setup_logging, tag_role,
)
from .company_tab import CompanyTab
from .expenses_tab import ExpensesTab
from .dialogs import NewYearDialog, StartPositionDialog
from .employees_tab import EmployeesTab
from .payroll_tab import PayrollTab
from .pin import ChangePinDialog, IDLE_LOCK_SECONDS, SecurityQuestionDialog
from .timecard_tab import TimeCardTab
from .update_dialogs import DownloadDialog, UpdateDialog
from .yearlog_tab import YearLogTab


class App(tk.Tk):
    def __init__(self, load_last: bool = True):
        super().__init__(className="TimeBooks")
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
        self.expenses_tab = ExpensesTab(self.nb, self)
        self.company_tab = CompanyTab(self.nb, self)
        # The tab registry: (tab, label, the Company feature switch that shows it, or None for always shown).
        self.tab_specs = ((self.timecard_tab, "Time Card", "use_timecards"), (self.employees_tab, "Employees", None),
                          (self.yearlog_tab, "Year Log", "use_timecards"), (self.payroll_tab, "Pay & Checks",
                                                                            "use_payroll"),
                          (self.expenses_tab, "Expenses", "use_expenses"), (self.company_tab, "Company Settings", None))
        for tab, text, _feature in self.tab_specs:
            self.nb.add(tab, text=f"  {text}  ")
        for tab in self.lockable_tabs:
            tab.init_pin_lock()
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
        fm.add_command(label="Lock PIN-Protected Tabs Now", command=self.lock_pin_tabs)
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
            self.status(f"{self.book.path.name} was changed outside TimeBooks, so automatic saving is paused. "
                        "Open it again to see those changes, or use File > Save to replace them.", error=True)
            return
        self._snapshot_before_first_autosave()
        try:
            self.book.save()
        except WorkbookError as exc:
            log.warning("Autosave failed: %s", exc)
            self._autosave_failed = True
            self.status(f"Could not save automatically ({exc}). Close the workbook in other programs; "
                        "TimeBooks will try again.", error=True)
            self._schedule_autosave(AUTOSAVE_RETRY_MS)
            return
        self._unbacked_autosave = True
        if self._autosave_failed:
            self._autosave_failed = False
            self.status("Saved automatically.")
        self.update_title()

    def _snapshot_before_first_autosave(self) -> None:
        """Keep a copy of the workbook as it was opened, so automatic saving can always be undone. Goes to the
        backup folder when one is set, else a "TimeBooks Backups" folder next to the workbook."""
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
        self.apply_features()
        self.expenses_tab.add_due_recurring()
        self.apply_pin_locks(lock_current=True)
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
        self.expenses_tab.refresh()
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
        elif current is self.expenses_tab:
            self.expenses_tab.on_shown()
        elif current is self.timecard_tab:
            self.timecard_tab.update_totals()

    def commit_pending(self) -> None:
        if self.book is not None:
            self.timecard_tab.commit_pending()

    # ----- PIN / idle lock
    def note_activity(self, _e=None) -> None:
        self._last_activity = _time.monotonic()

    def apply_features(self) -> None:
        """Show the tabs of the features the business uses (Company Settings > Features) and hide the rest; a hidden
        tab keeps its data and comes back in the same place."""
        if self.book is None:
            return
        for tab, _text, feature in self.tab_specs:
            if feature is None or getattr(self.book.company, feature, True):
                self.nb.add(tab)   # re-adding a hidden tab shows it again where it was
            else:
                self.nb.hide(tab)
        sel = self.nb.select()
        if not sel or str(self.nb.tab(sel, "state")) == "hidden":
            self.nb.select(self.employees_tab)

    @property
    def lockable_tabs(self) -> tuple:
        return (self.timecard_tab, self.employees_tab, self.yearlog_tab, self.payroll_tab, self.expenses_tab,
                self.company_tab)

    @property
    def pin_tabs(self) -> list:
        """Tabs behind the PIN: the ones locked in Company Settings > Locked by PIN, and Company Settings."""
        company = self.book.company if self.book is not None else Company()
        return [t for t in self.lockable_tabs if t.pin_required(company)]

    def apply_pin_locks(self, lock_current: bool = False) -> None:
        """Match the tabs to the Locked by PIN settings: lock newly locked tabs (the open one too when a workbook
        is opened, not while the owner is changing the settings) and open tabs that are no longer locked."""
        current = self.nb.nametowidget(self.nb.select()) if self.nb.select() else None
        required = self.pin_tabs
        for tab in self.lockable_tabs:
            if tab in required and not tab.locked and (lock_current or tab is not current):
                tab.lock()
            elif tab not in required and tab.locked:
                tab.unlock(quiet=True)
        if current in required and current.locked:
            current.overlay.focus_pin()

    def _idle_check(self) -> None:
        open_tabs = [t for t in self.pin_tabs if not t.locked]
        if open_tabs and _time.monotonic() - self._last_activity >= IDLE_LOCK_SECONDS:
            for tab in open_tabs:
                tab.lock()
            self.status("Locked tabs locked again after 5 minutes of inactivity.")
        self._idle_job = self.after(10_000, self._idle_check)

    def lock_pin_tabs(self) -> None:
        for tab in self.pin_tabs:
            tab.lock()
        self.status("Locked tabs locked.")

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
            self.status("Saved by a newer version of TimeBooks: changes here can't be saved over it.", error=True)
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
            # rent and subscriptions keep repeating in the new year; the owner's categories carry over too
            book.recurring = [dataclasses.replace(r) for r in carry_from.recurring]
            book.categories = list(carry_from.categories)
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
                APP_NAME, f"{self.book.path.name} was changed outside TimeBooks (for example in GridCraft) "
                "since it was opened here.\n\nSaving replaces those changes with what is shown in TimeBooks. "
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
        try:
            backup.backup_receipts(self.book.path, dest)   # receipt files live next to the workbook, not in it
        except OSError as exc:
            log.warning("Receipt backup incomplete: %s", exc)
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
            self.status("Opened in GridCraft. Close it there before saving again from TimeBooks.")
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
        d = paths.kept_folder(Path(self.book.path).parent, OUTPUT_DIR_NAME, OLD_OUTPUT_DIR_NAME)
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
                else Path(tempfile.gettempdir()) / "TimeBooks-Update")
        for old in dest.glob("TimeBooks-Setup-*.exe") if dest.is_dir() else ():
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
