"""The phone clock-in QR code: show it, print it as a sign for the wall, or make a new code if it ever leaks."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from .. import phone_upload, timeclock
from .common import log, open_file

if TYPE_CHECKING:
    from .app import App


class ClockQrDialog(tk.Toplevel):
    def __init__(self, master, app: "App"):
        super().__init__(master)
        self.app = app
        self.title("Phone clock-in")
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.body = ttk.Frame(self, padding=18)
        self.body.pack(fill="both", expand=True)
        self.fill()

    def fill(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        f, clock = self.body, self.app.clock
        if clock is None:
            ttk.Label(f, text="Phone clock-in is off. Turn it on in Company Settings > Pay > Phone clock-in, and set "
                      "each person's clock PIN on the Employees tab.", wraplength=380).pack()
            ttk.Button(f, text="Close", command=self.destroy).pack(pady=(10, 0))
            return
        img = phone_upload.qr_image(clock.url, box=7)
        if img is not None:
            from PIL import ImageTk
            self._qr = ImageTk.PhotoImage(img)
            tk.Label(f, image=self._qr, bd=0).pack()
        ttk.Label(f, text="Scan with the phone's camera (same Wi-Fi), type your 4-digit clock PIN, tap Clock.",
                  wraplength=400).pack(pady=(10, 2))
        e = ttk.Entry(f, width=48, justify="center")
        e.insert(0, clock.url)
        e.configure(state="readonly")
        e.pack()
        ttk.Label(f, text="Works while TimeBooks is open with this workbook. Business Wi-Fi only (not public "
                  "Wi-Fi). If Windows asks about the firewall, allow private networks. Time cards still need your "
                  "OK before payroll.", style="Muted.TLabel", wraplength=400).pack(pady=(8, 4))
        b = ttk.Frame(f)
        b.pack(pady=(6, 0))
        ttk.Button(b, text="Print sign…", style="Accent.TButton", command=self.print_sign).pack(side="left")
        ttk.Button(b, text="New code…", command=self.new_code).pack(side="left", padx=6)
        ttk.Button(b, text="Close", command=self.destroy).pack(side="left")

    def print_sign(self) -> None:
        """A one-page PDF to print and put on the wall: company name, the QR code, how to clock in."""
        out = self.app.output_dir()
        if out is None or self.app.clock is None:
            return
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen import canvas
            path = out / "Clock-in QR code.pdf"
            c = canvas.Canvas(str(path), pagesize=letter)
            w, h = letter
            c.setFont("Helvetica-Bold", 28)
            c.drawCentredString(w / 2, h - 100, self.app.book.company.company_name)
            c.setFont("Helvetica", 22)
            c.drawCentredString(w / 2, h - 140, "Clock in / out")
            img = phone_upload.qr_image(self.app.clock.url, box=12)
            if img is not None:
                c.drawImage(ImageReader(img), w / 2 - 180, h - 540, 360, 360)
            c.setFont("Helvetica", 16)
            for i, line in enumerate(("1. Open your phone's camera and point it at the code.",
                                      "2. Type your 4-digit clock PIN and tap Clock.",
                                      "Use the shop Wi-Fi.")):
                c.drawCentredString(w / 2, h - 590 - 28 * i, line)
            c.save()
        except Exception as exc:  # noqa: BLE001 - shown to the owner
            log.warning("Clock-in sign failed: %s", exc)
            messagebox.showwarning("Phone clock-in", f"The sign couldn't be made:\n{exc}", parent=self)
            return
        open_file(path)

    def new_code(self) -> None:
        if not messagebox.askyesno("Phone clock-in", "Make a new clock-in code? The printed QR code stops working "
                                   "and you'll need to print the new one.", parent=self):
            return
        self.app.book.company.clock_code = timeclock.new_code()
        self.app.mark_dirty()
        self.app.apply_clock()
        self.fill()
