"""PIN lock for the Employees tab, with a security question for a forgotten PIN.

The PIN and the recovery answer are set by the installer (or on first use) and kept only as salted
PBKDF2 hashes in %APPDATA%\\TimeCardPro\\pin.json (or $TIMECARDPRO_SETTINGS_DIR). This keeps casual
onlookers out of the app's employee records; it does not encrypt the workbook itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from . import paths

MIN_LEN, MAX_LEN = 4, 8
MAX_ANSWER_TRIES = 5
ANSWER_LOCKOUT_SECONDS = 15 * 60
_ITERATIONS = 200_000

SECURITY_QUESTIONS = [
    "What was the name of your first pet?",
    "What city were you born in?",
    "What was your mother's maiden name?",
    "What was the make of your first car?",
    "What street did you grow up on?",
    "What was the name of your elementary school?",
]


def settings_dir() -> Path:
    return paths.settings_dir()  # %APPDATA%\TimeCardPro, or the drive's Data\Settings when portable


def pin_file() -> Path:
    return settings_dir() / "pin.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(pin_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    path = pin_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _hash(secret: str, salt: bytes, iterations: int = _ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, iterations)


def _check(secret: str, salt_hex: Any, hash_hex: Any, iterations: Any) -> bool:
    try:
        expected = bytes.fromhex(hash_hex)
        actual = _hash(secret, bytes.fromhex(salt_hex), int(iterations or _ITERATIONS))
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def _normalize_answer(answer: str) -> str:
    return " ".join(answer.lower().split())


# ---------------------------------------------------------------- PIN

def validate_pin(pin: str) -> str | None:
    """Problem with a proposed PIN, or None if it is acceptable."""
    if not pin.isdigit():
        return "The PIN must contain digits only."
    if not MIN_LEN <= len(pin) <= MAX_LEN:
        return f"The PIN must be {MIN_LEN} to {MAX_LEN} digits."
    return None


def has_pin() -> bool:
    data = _load()
    return bool(data.get("hash") and data.get("salt"))


def set_pin(pin: str) -> None:
    """Store a new PIN (raises ValueError if it fails validate_pin). Keeps the security question."""
    problem = validate_pin(pin)
    if problem:
        raise ValueError(problem)
    data = _load()
    salt = secrets.token_bytes(16)
    data.update(salt=salt.hex(), hash=_hash(pin, salt).hex(), iterations=_ITERATIONS)
    _save(data)


def verify_pin(pin: str) -> bool:
    data = _load()
    return _check(pin, data.get("salt"), data.get("hash"), data.get("iterations"))


def change_pin(current: str, new: str) -> None:
    """Replace the PIN; raises PermissionError if `current` is wrong, ValueError if `new` is invalid."""
    if not verify_pin(current):
        raise PermissionError("The current PIN is incorrect.")
    set_pin(new)


# ---------------------------------------------------------------- forgot PIN

def validate_answer(answer: str) -> str | None:
    if len(_normalize_answer(answer)) < 2:
        return "Enter an answer of at least 2 characters."
    return None


def set_recovery(question: str, answer: str) -> None:
    """Store the security question and a hash of its answer (case and spacing are ignored)."""
    question = question.strip()
    if not question:
        raise ValueError("Choose or type a security question.")
    problem = validate_answer(answer)
    if problem:
        raise ValueError(problem)
    data = _load()
    salt = secrets.token_bytes(16)
    data.update(question=question, answer_salt=salt.hex(),
                answer_hash=_hash(_normalize_answer(answer), salt).hex(),
                answer_failures=0, answer_locked_until=0)
    _save(data)


def has_recovery() -> bool:
    data = _load()
    return bool(data.get("question") and data.get("answer_hash"))


def recovery_question() -> str | None:
    return _load().get("question") or None


def answer_lockout_remaining() -> int:
    """Seconds until another answer may be tried (0 when not locked out)."""
    return max(0, int(float(_load().get("answer_locked_until", 0)) - time.time()))


def reset_pin_with_answer(answer: str, new_pin: str) -> None:
    """Set a new PIN if `answer` matches the security question.

    Raises PermissionError for a wrong answer or during a lockout (after MAX_ANSWER_TRIES wrong
    answers in a row), LookupError if no question is set, ValueError if `new_pin` is invalid.
    """
    data = _load()
    if not (data.get("question") and data.get("answer_hash")):
        raise LookupError("No security question is set up on this computer.")
    if answer_lockout_remaining():
        minutes = -(-answer_lockout_remaining() // 60)
        raise PermissionError(f"Too many wrong answers. Try again in {minutes} minute(s).")
    problem = validate_pin(new_pin)
    if problem:
        raise ValueError(problem)
    if not _check(_normalize_answer(answer), data.get("answer_salt"), data.get("answer_hash"),
                  data.get("iterations")):
        failures = int(data.get("answer_failures", 0)) + 1
        data["answer_failures"] = 0 if failures >= MAX_ANSWER_TRIES else failures
        if failures >= MAX_ANSWER_TRIES:
            data["answer_locked_until"] = time.time() + ANSWER_LOCKOUT_SECONDS
        _save(data)
        raise PermissionError("That answer is incorrect.")
    data.update(answer_failures=0, answer_locked_until=0)
    _save(data)
    set_pin(new_pin)
