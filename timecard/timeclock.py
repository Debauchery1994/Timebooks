"""Clock in / out from a phone: a fixed QR code for a small page on the local network; the person types their 4-digit
clock PIN and TimeBooks fills their next empty In / Out for today.

The page only answers at the code in its address (kept in Company Settings so a printed QR keeps working; a new code
can be made if it leaks), only while TimeBooks is open with that workbook, and a PIN can only clock someone in or out:
the owner still approves time cards. Wrong PINs lock that phone out for a while, and too many stop the page.
"""
from __future__ import annotations

import hashlib
import hmac
import queue
import secrets
import threading
import time as _time
from dataclasses import replace
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs

from .models import TimeEntry

PUNCH_FIELDS = ("in1", "out1", "in2", "out2", "in3", "out3")
_ITERATIONS = 60_000          # per PIN check; with a lockout this keeps guessing slow without making clocking slow
TRIES_PER_PHONE = 5           # wrong PINs from one phone before it waits
LOCKOUT_SECONDS = 5 * 60
MAX_WRONG_TOTAL = 40          # wrong PINs from anywhere before the page stops (someone is guessing)
MAX_BAD_PATHS = 30            # wrong addresses before the page stops (someone is guessing the code)
DOUBLE_TAP_MINUTES = 1        # a second punch within about a minute is a double tap, not a new punch
MAX_WAITING = 8               # PIN checks waiting for TimeBooks at once; more are told to try again
OVERNIGHT_HOURS = 16          # an Out this long after yesterday's last In still closes yesterday's shift


# ---- clock PINs (stored hashed on the employee's row)

def validate_clock_pin(pin: str) -> str | None:
    return None if len(pin) == 4 and pin.isdigit() else "The clock PIN must be exactly 4 digits."


def hash_clock_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_clock_pin(pin: str, stored: str) -> bool:
    try:
        _algo, iterations, salt_hex, hash_hex = stored.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual.hex(), hash_hex)


def new_code() -> str:
    """The secret part of the clock-in address (printed in the QR code)."""
    return secrets.token_urlsafe(9)


# ---- punching

def punch(entry: TimeEntry, now: datetime, overnight: bool = False) -> tuple[TimeEntry | None, str]:
    """Fill the next empty In / Out of `entry` with `now` (to the minute). Returns the new entry and what happened
    ("IN" / "OUT"), or (None, reason) when nothing was recorded. `overnight`: `entry` is yesterday's and this Out
    closes a shift that ran past midnight (so it may be earlier than the In)."""
    if (entry.hours_type or "Worked").strip().lower() not in ("", "worked") or entry.salary_hours is not None:
        return None, "Today is entered by the office (time off or typed hours). Tell the office."
    t = time(now.hour, now.minute)
    filled = [f for f in PUNCH_FIELDS if getattr(entry, f) is not None]
    if filled and not overnight:
        last = getattr(entry, filled[-1])
        gap = (now.hour * 60 + now.minute) - (last.hour * 60 + last.minute)
        if 0 <= gap <= DOUBLE_TAP_MINUTES:
            kind = "IN" if filled[-1].startswith("in") else "OUT"
            return None, f"Already clocked {kind} at {_fmt(last)}."
        if gap < 0:
            return None, f"The last punch today is {_fmt(last)}, later than now. Tell the office."
    free = next((f for f in PUNCH_FIELDS if getattr(entry, f) is None), None)
    if free is None:
        return None, "All three In/Out pairs for today are used. Tell the office."
    return replace(entry, **{free: t}), "IN" if free.startswith("in") else "OUT"


def open_overnight(yesterday: TimeEntry, now: datetime) -> bool:
    """True when yesterday's card ends with an In (no Out yet) recent enough that `now` is the end of that shift."""
    filled = [f for f in PUNCH_FIELDS if getattr(yesterday, f) is not None]
    if not filled or not filled[-1].startswith("in"):
        return False
    last_in = datetime.combine(yesterday.work_date, getattr(yesterday, filled[-1]))
    return 0 < (now - last_in).total_seconds() <= OVERNIGHT_HOURS * 3600


def _fmt(t: time) -> str:
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


# ---- the page

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Clock in / out</title><style>
body{{font-family:system-ui,sans-serif;margin:24px;font-size:20px;text-align:center}}
input{{font-size:40px;width:6em;text-align:center;letter-spacing:.3em;margin:16px 0}}
button{{font-size:24px;padding:14px 40px}}.msg{{margin-top:20px;font-weight:bold}}</style></head><body>
<h2>{company}</h2><p>Clock in / out</p>
<form method="post" action="punch"><input name="pin" type="password" inputmode="numeric" pattern="[0-9]{{4}}"
maxlength="4" autocomplete="off" autofocus placeholder="PIN"><br><button type="submit">Clock</button></form>
<p class="msg">{msg}</p></body></html>"""


class ClockServer:
    """Serves the clock-in page at http://<host>:<port>/<code>/. `handle(pin)` decides a punch and must run on the UI
    thread: requests wait (briefly) for the UI loop to answer them through `requests` (see `serve_pending`)."""

    def __init__(self, host: str, port: int, code: str, company: str):
        self.code, self.company = code, company
        self.requests: queue.Queue = queue.Queue()
        self._waiting: dict[str, int] = {}   # PIN checks in progress per phone (counted before they're checked)
        self.stopped = False
        self.stopped_reason = ""
        self._fails: dict[str, list[float]] = {}
        self._wrong_total = 0
        self._bad_paths = 0
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            timeout = 15
            # Note: guesses are counted when they arrive (not when answered), so sending many at once can't get
            # past the per-phone or overall limits.
            server_version = "TimeBooks"
            sys_version = ""

            def log_message(self, *args) -> None:
                pass

            def _path_ok(self, tail: str = "") -> bool:
                return self.path.split("?")[0].rstrip("/") == f"/{server.code}" + (f"/{tail}" if tail else "")

            def _page(self, msg: str, status: int = 200) -> None:
                import html
                body = PAGE.format(company=html.escape(server.company), msg=html.escape(msg)).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _refuse(self) -> None:
                with server._lock:
                    server._bad_paths += 1
                    stop = server._bad_paths >= MAX_BAD_PATHS and not server.stopped
                self.send_error(404)
                if stop:
                    server._stop_for("Too many wrong addresses were tried, so phone clock-in was stopped.")

            def do_GET(self) -> None:
                if server.stopped or not self._path_ok():
                    self._refuse()
                    return
                self._page("Type your 4-digit PIN.")

            def do_POST(self) -> None:
                if server.stopped or not self._path_ok("punch"):
                    self._refuse()
                    return
                phone = self.client_address[0]
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if not 0 < length <= 200:
                    self._page("Type your 4-digit PIN.", 400)
                    return
                pin = (parse_qs(self.rfile.read(length).decode("ascii", "ignore")).get("pin") or [""])[0].strip()
                if validate_clock_pin(pin):
                    self._page("Type your 4-digit PIN.", 400)
                    return
                refusal = server._admit(phone)
                if refusal:
                    self._page(refusal, 429)
                    return
                answer: dict = {}
                done = threading.Event()
                try:
                    server.requests.put((pin, answer, done))
                    done.wait(10)
                finally:
                    server._settle(phone, wrong=bool(answer) and not answer.get("ok"))
                if not answer:
                    self._page("TimeBooks didn't answer. Try again.", 503)
                elif answer.get("ok"):
                    self._page(answer["message"])
                else:
                    self._page(answer.get("message") or "That PIN wasn't recognised.", 403)

        ThreadingHTTPServer.daemon_threads = True
        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.block_on_close = False   # closing TimeBooks never waits on a phone mid-request
        self.host, self.port = host, self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/{self.code}/"

    def start(self) -> "ClockServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.httpd.shutdown()
        self.httpd.server_close()

    def _stop_for(self, reason: str) -> None:
        self.stopped_reason = reason
        threading.Thread(target=self.stop, daemon=True).start()

    def _admit(self, phone: str) -> str:
        """Let a PIN check in, counting it now (before it's answered), or say why not. Guesses in progress count
        against the phone's and the overall limits, so a burst of guesses can't slip past them."""
        with self._lock:
            now = _time.monotonic()
            recent = [t for t in self._fails.get(phone, []) if now - t < LOCKOUT_SECONDS]
            self._fails[phone] = recent
            in_flight = self._waiting.get(phone, 0)
            if len(recent) + in_flight >= TRIES_PER_PHONE:
                wait = int(LOCKOUT_SECONDS - (now - recent[0])) if recent else 60
                return f"Too many wrong PINs. Try again in {wait // 60 + 1} minute(s)."
            if self._wrong_total + sum(self._waiting.values()) >= MAX_WRONG_TOTAL or self.stopped:
                return "Phone clock-in is paused. Tell the office."
            if sum(self._waiting.values()) >= MAX_WAITING:
                return "Busy. Wait a moment and try again."
            self._waiting[phone] = in_flight + 1
            return ""

    def _settle(self, phone: str, wrong: bool) -> None:
        """A PIN check finished: no longer in progress; a wrong PIN is remembered for the lockout."""
        with self._lock:
            self._waiting[phone] = max(self._waiting.get(phone, 1) - 1, 0)
            if wrong:
                self._fails.setdefault(phone, []).append(_time.monotonic())
                self._wrong_total += 1
            stop = self._wrong_total >= MAX_WRONG_TOTAL and not self.stopped
        if stop:
            self._stop_for("Too many wrong PINs were tried, so phone clock-in was stopped. Turn it on again in "
                           "Company Settings.")

    def serve_pending(self, handle: Callable[[str], tuple[bool, str]]) -> None:
        """Answer waiting punches (call from the UI thread): handle(pin) -> (recognised, message for the phone)."""
        while not self.requests.empty():
            pin, answer, done = self.requests.get()
            if self.stopped:   # stopped meanwhile (e.g. too many guesses): answer without checking the PIN
                answer["ok"], answer["message"] = True, "Phone clock-in is paused. Tell the office."
                done.set()
                continue
            try:
                answer["ok"], answer["message"] = handle(pin)
            except Exception:  # noqa: BLE001 - the phone gets an answer whatever happens
                answer["ok"], answer["message"] = True, "Something went wrong. Tell the office."
            done.set()
