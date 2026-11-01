"""General dialogs: new year workbook, backups, annual statements export, check start position."""
from __future__ import annotations

import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from ..models import Company, Employee
from .. import backup, calc, copies
from ..storage import TimecardBook
from .common import ModalDialog, documents_dir, open_file, style_tree

if TYPE_CHECKING:
    from .app import App


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

        enc = ttk.Frame(f)
        enc.pack(anchor="w", pady=(10, 0))
        self.v_encrypt = tk.BooleanVar(value=bool(s.data.get("backup_encrypt")))
        ttk.Checkbutton(enc, text="Encrypt backups with a password (recommended for Google Drive and other cloud "
                        "folders)", variable=self.v_encrypt, command=self._encrypt_toggled).pack(anchor="w")
        pw = ttk.Frame(enc)
        pw.pack(anchor="w", pady=(4, 0))
        ttk.Button(pw, text="Set Backup Password\u2026", command=self._set_password).pack(side="left")
        self.v_pw = tk.StringVar()
        ttk.Label(pw, textvariable=self.v_pw, style="Muted.TLabel").pack(side="left", padx=(8, 0))
        self._refresh_password()

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

    def _refresh_password(self) -> None:
        if self.app.saved_backup_key() is not None:
            self.v_pw.set("Password saved on this computer.")
        elif self.app.settings.data.get("backup_key"):
            self.v_pw.set("Enter the backup password again on this computer.")
        else:
            self.v_pw.set("No backup password set yet.")

    def _set_password(self) -> bool:
        pw = BackupPasswordDialog(self).show()
        if not pw:
            return False
        self.app.set_backup_password(pw)
        self.v_encrypt.set(True)
        self._refresh_password()
        return True

    def _encrypt_toggled(self) -> None:
        if self.v_encrypt.get() and self.app.saved_backup_key() is None and not self._set_password():
            self.v_encrypt.set(False)

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
        s.data["backup_encrypt"] = bool(self.v_encrypt.get())
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
        folder = backup.backup_root(d)
        open_file(folder if folder.is_dir() else Path(d))

    def apply(self) -> bool:
        return self._store()


class BackupPasswordDialog(ModalDialog):
    """Choose the backup password (typed twice). result = the password."""

    MIN_LEN = 8

    def __init__(self, master):
        super().__init__(master, "Backup Password")

    def body(self, f):
        ttk.Label(f, text="Backups are encrypted with this password before they go to the backup folder.\n"
                  "Write it down somewhere safe: if it's lost, nobody can open the encrypted backups,\n"
                  "not even TimeBooks. It's saved on this computer (protected by Windows) so backups\n"
                  "happen without asking; restoring on another computer asks for it.",
                  style="Muted.TLabel", justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.e1, self.e2 = ttk.Entry(f, show="\u2022", width=28), ttk.Entry(f, show="\u2022", width=28)
        for r, (label, e) in enumerate((("Backup password", self.e1), ("Type it again", self.e2)), start=1):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
            e.grid(row=r, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Changing it later: older backups still open with the password they were made with.",
                  style="Muted.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        return self.e1

    def apply(self) -> bool:
        a, b = self.e1.get(), self.e2.get()
        if len(a) < self.MIN_LEN:
            messagebox.showwarning("Backup Password", f"Use at least {self.MIN_LEN} characters.", parent=self)
            return False
        if a != b:
            messagebox.showwarning("Backup Password", "The two entries don't match.", parent=self)
            return False
        self.result = a
        return True


class RestoreDialog(ModalDialog):
    """Pick a backup to restore: the backups in the backup folder (newest first) or any backup file.
    result = the chosen file's path."""

    def __init__(self, master, app: "App"):
        self.app = app
        self.chosen: Path | None = None
        super().__init__(master, "Restore from Backup")

    def body(self, f):
        ttk.Label(f, text="Choose the backup to bring back. You'll see what's in it before anything changes, and "
                  "the workbook it replaces is kept as a backup first.", style="Muted.TLabel",
                  wraplength=560).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Backup folder").grid(row=1, column=0, sticky="w", pady=(8, 2))
        self.v_dir = tk.StringVar(value=self.app.settings.backup_dir)
        ttk.Entry(f, textvariable=self.v_dir, width=48).grid(row=1, column=1, sticky="ew", pady=(8, 2))
        ttk.Button(f, text="Browse\u2026", command=self._browse).grid(row=1, column=2, padx=(6, 0), pady=(8, 2))
        cols = (("when", "Backed up", 150), ("book", "Workbook", 250), ("kind", "", 90))
        self.tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", height=10, selectmode="browse")
        for c, h, w in cols:
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        style_tree(self.tree)
        self.tree.grid(row=2, column=0, columnspan=3, sticky="nsew", pady=(6, 4))
        self.tree.bind("<Double-1>", lambda e: self.ok())
        ttk.Button(f, text="Choose another backup file\u2026", command=self._pick_file).grid(
            row=3, column=0, columnspan=3, sticky="w")
        self._fill()
        return self.tree

    def _browse(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Folder the backups were made to",
                                    initialdir=self.v_dir.get() or str(Path.home()))
        if d:
            self.v_dir.set(str(Path(d)))
            self._fill()

    def _fill(self) -> None:
        self.tree.delete(*self.tree.get_children())
        d = self.v_dir.get().strip()
        found = backup.all_backups(d) if d and Path(d).is_dir() else []
        mine = self.app.book.path.stem if self.app.book is not None and self.app.book.path else ""
        found.sort(key=lambda p: backup.backup_stem(p) != mine)   # this workbook's first, each newest first
        for p in found:
            stamp = p.stem[-17:]
            when = f"{stamp[5:7]}/{stamp[8:10]}/{stamp[:4]} {stamp[11:13]}:{stamp[13:15]}"
            self.tree.insert("", "end", iid=str(p), values=(when, backup.backup_stem(p),
                                                           "Encrypted" if p.suffix == backup.ENCRYPTED_SUFFIX else ""))
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])

    def _pick_file(self) -> None:
        p = filedialog.askopenfilename(parent=self, title="Choose a backup",
                                       initialdir=self.v_dir.get() or str(Path.home()),
                                       filetypes=[("TimeBooks backups", "*.tbk *.xlsx"), ("All files", "*.*")])
        if p:
            self.chosen = Path(p)
            self.ok()

    def apply(self) -> bool:
        if self.chosen is None:
            sel = self.tree.selection()
            if not sel:
                messagebox.showinfo("Restore from Backup", "Choose a backup first.", parent=self)
                return False
            self.chosen = Path(sel[0])
        self.result = self.chosen
        return True


class OtherCopyDialog(ModalDialog):
    """Another copy of the open workbook (PC or USB) has work this one doesn't. result = "open" | "copy" | "keep"."""

    def __init__(self, master, this: Path, other: "copies.OtherCopy"):
        self.this, self.other = this, other
        super().__init__(master, "Another copy of this workbook")

    def body(self, f):
        o = self.other
        when = f"saved {o.saved_at}" + (f" on {o.saved_on}" if o.saved_on else "")
        if o.status == "other_newer":
            msg = (f"The copy at\n    {o.path}\nhas newer work than the one you opened ({when}).\n\n"
                   "Work saved there isn't in this copy.")
        else:
            msg = (f"This workbook and the copy at\n    {o.path}\nwere both changed separately since they were last "
                   f"the same (that one {when}).\n\nTimeBooks can't combine them: each has work the other doesn't. "
                   "Open the other one to compare, and keep working in one of them.")
        ttk.Label(f, text=msg, justify="left", wraplength=520).pack(anchor="w")
        self.v = tk.StringVar(value="open")
        choices = [("open", "Open that copy instead")]
        if o.status == "other_newer":
            choices.append(("copy", "Copy it over this one, so both match (this file is backed up first)"))
        choices.append(("keep", "Keep using this one"))
        for val, text in choices:
            ttk.Radiobutton(f, text=text, value=val, variable=self.v).pack(anchor="w", pady=(6 if val == "open"
                                                                                           else 2, 0))
        return None

    def apply(self) -> bool:
        self.result = self.v.get()
        return True


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
        ttk.Label(f, text="Leave blank to use the TimeBooks Output folder next to the workbook.",
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
