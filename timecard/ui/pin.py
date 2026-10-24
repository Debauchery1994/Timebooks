"""PIN lock: change / forgot PIN, the security question, and the lock overlay used by locked tabs."""
from __future__ import annotations

import tkinter as tk
import time as _time
from datetime import time
from tkinter import messagebox, ttk

from .. import security
from .common import F, ModalDialog


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
