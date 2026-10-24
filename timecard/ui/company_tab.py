"""Company Settings tab (behind the PIN)."""
from __future__ import annotations

import dataclasses
import re
import tkinter as tk
from decimal import Decimal
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from .. import branding, calc
from .common import (
    CHECK_LAYOUTS, F, MANUAL_FIELDS, P, ScrollFrame, _appearance, is_salaried, logo_photo, scaled, tag_role,
)
from .pin import PinLockable

if TYPE_CHECKING:
    from .app import App


class CompanyTab(PinLockable, ttk.Frame):
    pin_key = ""   # always locked: the Locked by PIN switches and bank numbers live here
    pin_what = "Company settings"
    pin_purpose = "view or change company, bank and check settings"
    pin_locked_by_default = True

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
        ("Features", "use_timecards", "Time cards", "bool", "Track hours (Time Card and Year Log tabs)"),
        ("Features", "use_payroll", "Payroll checks", "bool", "Pay people and print checks (Pay & Checks tab)"),
        ("Locked by PIN", "lock_timecard", "Time Card", "bool", "Lock the Time Card tab"),
        ("Locked by PIN", "lock_employees", "Employees", "bool", "Lock the Employees tab"),
        ("Locked by PIN", "lock_yearlog", "Year Log", "bool", "Lock the Year Log tab"),
        ("Locked by PIN", "lock_payroll", "Pay & Checks", "bool", "Lock the Pay & Checks tab"),
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
                  "Time off": (2, 1), "Locked by PIN": (3, 0), "Features": (3, 1)}
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
        adv_outer.grid(row=4, column=0, columnspan=2, sticky="ew")
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

        # Where the data lives. Shown only here (behind the PIN), not in the header; locking the file is the owner's
        # choice, so this explains how rather than doing it for them.
        r = rows["Company"]
        ttk.Label(frames["Company"], text="Your data is stored in this file:").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(14, 2))
        self.v_data_path = tk.StringVar(value="")
        ttk.Entry(frames["Company"], textvariable=self.v_data_path, state="readonly").grid(
            row=r + 1, column=0, columnspan=2, sticky="ew")
        ttk.Label(frames["Company"], style="Muted.TLabel", wraplength=420, justify="left",
                  text="The PIN locks this app, not the file: anyone who can open the file in GridCraft or Excel "
                       "can read it. If you want the file locked too, use Windows: keep the computer behind its "
                       "Windows sign-in, turn on BitLocker (or Device Encryption), or right-click the file > "
                       "Properties > Advanced > Encrypt contents (Windows Pro). Backups and USB copies are "
                       "separate files, so lock those the same way.").grid(
            row=r + 2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        rows["Company"] += 3

        ttk.Label(frames["Pay"], text="Contractors: leave overtime off so every hour is paid at the same rate.",
                  style="Muted.TLabel", wraplength=360).grid(row=rows["Pay"], column=0, columnspan=2, sticky="w",
                                                             pady=(8, 0))
        ttk.Label(frames["Features"], style="Muted.TLabel", wraplength=420, justify="left",
                  text="Turn off what the business doesn't use and its tabs are hidden. Nothing is deleted: turn it "
                       "back on and everything is still there. With time cards off, Pay & Checks pays salaried "
                       "people only (hourly pay comes from Time Card hours).").grid(
            row=rows["Features"], column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(frames["Locked by PIN"], style="Muted.TLabel", wraplength=420, justify="left",
                  text="Locked tabs ask for the PIN set up when TimeCard Pro was installed (File > Change PIN to "
                       "change it) and lock again when you leave them or after 5 minutes. Company Settings is "
                       "always locked.").grid(row=rows["Locked by PIN"], column=0, columnspan=2, sticky="w",
                                              pady=(8, 0))
        ttk.Label(frames["Checks"], text="Check stock, MICR font and offsets apply to the printed layouts.",
                  style="Muted.TLabel", wraplength=360).grid(row=rows["Checks"], column=0, columnspan=2,
                                                             sticky="w", pady=(8, 0))
        self._update_layout_state()
        btns = self.btns = ttk.Frame(self, padding=(0, 10, 0, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Save Settings", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(btns, text="Revert", command=self.refresh).pack(side="right", padx=6)

    # ----- PIN lock (bank account and routing numbers live here)
    def on_pin_lock(self) -> None:
        self._kept_edits = self.form_changed()   # unsaved edits stay (hidden) until the PIN is entered again

    def on_pin_unlock(self, quiet: bool) -> None:
        if getattr(self, "_kept_edits", False) and self.form_changed():
            self.app.status("Your unsaved changes to company settings were kept. Click Save Settings to keep them.")
        else:
            self.refresh()
            super().on_pin_unlock(quiet)
        self._kept_edits = False

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
            order = ("Company", "Bank", "Branding", "Pay", "Time off", "Checks", "Features", "Locked by PIN")
            for i, name in enumerate(order):
                f[name].grid_configure(row=i, column=0)
            self.adv_outer.grid_configure(row=len(order), column=0, columnspan=1)
        else:
            for name, (r, c) in {"Company": (0, 0), "Bank": (1, 0), "Branding": (2, 0), "Pay": (0, 1),
                                 "Checks": (1, 1), "Time off": (2, 1), "Locked by PIN": (3, 0),
                                 "Features": (3, 1)}.items():
                f[name].grid_configure(row=r, column=c)
            self.adv_outer.grid_configure(row=4, column=0, columnspan=2)

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
        self.v_data_path.set(str(self.book.path) if self.book.path else "Not saved yet")
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
            self.app.apply_features()
            self.app.apply_pin_locks()
            self.app.status(f"Company settings updated.{self.app.save_hint()}")
        else:
            self.app.status("No changes to company settings.")
