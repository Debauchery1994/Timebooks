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
