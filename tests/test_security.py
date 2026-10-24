"""Tests for the Employees-tab PIN store."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from timecard import security


class PinTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"TIMEBOOKS_SETTINGS_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_no_pin_initially(self) -> None:
        self.assertFalse(security.has_pin())
        self.assertFalse(security.verify_pin("1234"))

    def test_set_and_verify(self) -> None:
        security.set_pin("4821")
        self.assertTrue(security.has_pin())
        self.assertTrue(security.verify_pin("4821"))
        self.assertFalse(security.verify_pin("4822"))
        self.assertFalse(security.verify_pin(""))

    def test_pin_not_stored_in_plain_text(self) -> None:
        security.set_pin("4821")
        text = Path(self._tmp.name, "pin.json").read_text()
        self.assertNotIn("4821", text)
        self.assertTrue({"salt", "hash", "iterations"} <= set(json.loads(text)))

    def test_forgot_pin_with_security_question(self) -> None:
        security.set_pin("1111")
        security.set_recovery("What was the name of your first pet?", "  Fluffy  McGee ")
        text = Path(self._tmp.name, "pin.json").read_text()
        self.assertNotIn("fluffy", text.lower())
        self.assertTrue(security.has_recovery())
        self.assertEqual(security.recovery_question(), "What was the name of your first pet?")
        with self.assertRaises(PermissionError):
            security.reset_pin_with_answer("Rex", "2222")
        self.assertTrue(security.verify_pin("1111"))
        with self.assertRaises(ValueError):
            security.reset_pin_with_answer("fluffy mcgee", "12")  # bad new PIN
        security.reset_pin_with_answer("FLUFFY mcgee", "2222")  # case/spacing ignored
        self.assertTrue(security.verify_pin("2222"))
        self.assertTrue(security.has_recovery())  # question survives the PIN change

    def test_answer_lockout_after_repeated_failures(self) -> None:
        security.set_pin("1111")
        security.set_recovery("City?", "Springfield")
        for _ in range(security.MAX_ANSWER_TRIES):
            with self.assertRaises(PermissionError):
                security.reset_pin_with_answer("wrong", "2222")
        self.assertGreater(security.answer_lockout_remaining(), 0)
        with self.assertRaises(PermissionError):  # even the right answer is refused while locked
            security.reset_pin_with_answer("Springfield", "2222")
        self.assertTrue(security.verify_pin("1111"))

    def test_recovery_validation(self) -> None:
        with self.assertRaises(LookupError):
            security.reset_pin_with_answer("x", "1234")
        with self.assertRaises(ValueError):
            security.set_recovery("", "answer")
        with self.assertRaises(ValueError):
            security.set_recovery("Q?", " a ")

    def test_validation(self) -> None:
        for bad in ("123", "123456789", "12a4", " 1234", ""):
            self.assertIsNotNone(security.validate_pin(bad), bad)
            with self.assertRaises(ValueError):
                security.set_pin(bad)
        self.assertIsNone(security.validate_pin("0000"))
        self.assertIsNone(security.validate_pin("12345678"))

    def test_change_pin_requires_current(self) -> None:
        security.set_pin("1111")
        with self.assertRaises(PermissionError):
            security.change_pin("2222", "3333")
        self.assertTrue(security.verify_pin("1111"))
        security.change_pin("1111", "3333")
        self.assertTrue(security.verify_pin("3333"))
        self.assertFalse(security.verify_pin("1111"))

    def test_corrupt_file_counts_as_no_pin(self) -> None:
        Path(self._tmp.name, "pin.json").write_text("not json")
        self.assertFalse(security.has_pin())
        self.assertFalse(security.verify_pin("1234"))


if __name__ == "__main__":
    unittest.main()
