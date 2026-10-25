"""Phone clock-in: hashed PINs, filling the next In / Out, and the page's PIN handling and lockouts."""
from __future__ import annotations

import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from datetime import time as t

from timecard import timeclock
from timecard.models import TimeEntry

DAY = date(2026, 10, 26)


class PinTests(unittest.TestCase):
    def test_pins_are_hashed_and_checked(self) -> None:
        h = timeclock.hash_clock_pin("4821")
        self.assertNotIn("4821", h)
        self.assertTrue(timeclock.check_clock_pin("4821", h))
        self.assertFalse(timeclock.check_clock_pin("4822", h))
        self.assertFalse(timeclock.check_clock_pin("4821", "garbage"))
        self.assertIsNone(timeclock.validate_clock_pin("0042"))
        self.assertIsNotNone(timeclock.validate_clock_pin("42"))
        self.assertIsNotNone(timeclock.validate_clock_pin("12a4"))


class PunchTests(unittest.TestCase):
    def test_fills_the_next_slot_in_order(self) -> None:
        e = TimeEntry(DAY, "E1")
        seen = []
        for hh, mm in ((8, 0), (10, 0), (10, 15), (12, 0), (12, 30), (17, 0)):
            e, what = timeclock.punch(e, datetime(2026, 10, 26, hh, mm))
            seen.append(what)
        self.assertEqual(seen, ["IN", "OUT", "IN", "OUT", "IN", "OUT"])
        self.assertEqual((e.in1, e.out3), (t(8), t(17)))
        full, why = timeclock.punch(e, datetime(2026, 10, 26, 18, 0))
        self.assertIsNone(full)
        self.assertIn("All three", why)

    def test_a_double_tap_isnt_a_second_punch(self) -> None:
        e, _ = timeclock.punch(TimeEntry(DAY, "E1"), datetime(2026, 10, 26, 8, 2, 10))
        again, why = timeclock.punch(e, datetime(2026, 10, 26, 8, 2, 40))
        self.assertIsNone(again)
        self.assertIn("Already clocked IN at 8:02 AM", why)


class PunchRuleTests(unittest.TestCase):
    def test_time_off_or_typed_hours_days_are_refused(self) -> None:
        from decimal import Decimal
        for e in (TimeEntry(DAY, "E1", hours_type="PTO"), TimeEntry(DAY, "E1", hours_type="Closed"),
                  TimeEntry(DAY, "E1", salary_hours=Decimal("8"))):
            new, why = timeclock.punch(e, datetime(2026, 10, 26, 8, 0))
            self.assertIsNone(new)
            self.assertIn("Tell the office", why)

    def test_double_tap_across_a_minute_and_an_earlier_time(self) -> None:
        e, _ = timeclock.punch(TimeEntry(DAY, "E1"), datetime(2026, 10, 26, 8, 2, 50))
        self.assertIsNone(timeclock.punch(e, datetime(2026, 10, 26, 8, 3, 20))[0])   # 30 s later, next minute
        self.assertIsNone(timeclock.punch(e, datetime(2026, 10, 26, 7, 0))[0])        # earlier than the last punch
        self.assertIsNotNone(timeclock.punch(e, datetime(2026, 10, 26, 8, 10))[0])

    def test_overnight_out_closes_yesterdays_shift(self) -> None:
        y = TimeEntry(DAY, "E1", in1=t(22, 0))
        now = datetime(2026, 10, 27, 1, 30)
        self.assertTrue(timeclock.open_overnight(y, now))
        new, what = timeclock.punch(y, now, overnight=True)
        self.assertEqual((what, new.out1), ("OUT", t(1, 30)))
        self.assertFalse(timeclock.open_overnight(TimeEntry(DAY, "E1", in1=t(8), out1=t(17)), now))
        self.assertFalse(timeclock.open_overnight(TimeEntry(DAY, "E1", in1=t(6)), datetime(2026, 10, 27, 23, 0)))


class ClockPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pin_hash = timeclock.hash_clock_pin("1234")
        self.srv = timeclock.ClockServer("127.0.0.1", 0, "CODE123", "Acme Auto").start()
        self.stop_ui = threading.Event()

        def ui_loop():   # stands in for the Tk loop answering punches
            while not self.stop_ui.is_set():
                self.srv.serve_pending(lambda pin: (True, "Ann Lee: clocked IN at 8:02 AM")
                                       if timeclock.check_clock_pin(pin, self.pin_hash)
                                       else (False, "That PIN wasn't recognised."))
                time.sleep(0.02)
        threading.Thread(target=ui_loop, daemon=True).start()

    def tearDown(self) -> None:
        self.stop_ui.set()
        self.srv.stop()

    def _post(self, pin: str):
        req = urllib.request.Request(self.srv.url + "punch", data=urllib.parse.urlencode({"pin": pin}).encode())
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            exc.close()
            return exc.code, body

    def test_right_pin_clocks_and_wrong_pins_lock_the_phone(self) -> None:
        with urllib.request.urlopen(self.srv.url, timeout=5) as r:
            page = r.read().decode()
        self.assertIn("Acme Auto", page)
        self.assertEqual(r.headers["Server"].strip(), "TimeBooks")
        status, body = self._post("1234")
        self.assertEqual(status, 200)
        self.assertIn("clocked IN", body)
        for _ in range(timeclock.TRIES_PER_PHONE):
            self.assertEqual(self._post("9999")[0], 403)
        status, body = self._post("1234")   # locked out now, even with the right PIN
        self.assertEqual(status, 429)
        self.assertIn("Too many wrong PINs", body)

    def test_a_burst_of_guesses_cant_get_past_the_limits(self) -> None:
        from concurrent.futures import ThreadPoolExecutor
        checked = []
        real = self.srv.serve_pending
        self.srv.serve_pending = lambda handle: real(lambda pin: (checked.append(pin), handle(pin))[1])
        with ThreadPoolExecutor(60) as pool:
            codes = list(pool.map(lambda i: self._post(f"{i:04d}")[0], range(120)))
        self.assertLessEqual(len(checked), timeclock.TRIES_PER_PHONE)   # only a handful ever reach the PIN check
        self.assertIn(429, codes)

    def test_wrong_addresses_stop_the_page(self) -> None:
        for _ in range(timeclock.MAX_BAD_PATHS):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.srv.port}/nope/", timeout=5).close()
            except urllib.error.HTTPError as exc:
                exc.close()
        for _ in range(50):
            if self.srv.stopped:
                break
            time.sleep(0.05)
        self.assertTrue(self.srv.stopped)


if __name__ == "__main__":
    unittest.main()
