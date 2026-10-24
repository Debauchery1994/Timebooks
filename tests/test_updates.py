"""Tests for the update check (timecard.updates) and the installer's --update mode. No network: a fake opener
stands in for urllib."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from timecard import __version__, paths, updates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import installer  # noqa: E402

SETUP_URL = f"https://github.com/{updates.REPO}/releases/download/v9.0.0/TimeBooks-Setup.exe"
PAYLOAD = b"MZ fake installer " * 5000


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str = "https://api.github.com/x", headers: dict | None = None):
        super().__init__(body)
        self._url, self.headers = url, headers or {}

    def geturl(self) -> str:
        return self._url


def release_json(tag="v9.0.0", assets=None, body="Bug fixes") -> bytes:
    if assets is None:
        assets = [{"name": "TimeBooks-Setup.exe", "browser_download_url": SETUP_URL, "size": len(PAYLOAD),
                   "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()}]
    return json.dumps({"tag_name": tag, "name": f"TimeBooks {tag}", "body": body,
                       "html_url": f"https://github.com/{updates.REPO}/releases/tag/{tag}",
                       "assets": assets}).encode()


def opener_returning(body: bytes, **kw):
    seen = []

    def opener(req, timeout):
        seen.append(req)
        return FakeResponse(body, **kw)
    opener.seen = seen
    return opener


def opener_raising(exc):
    def opener(req, timeout):
        raise exc
    return opener


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(updates.API_URL, code, "err", {}, None)


class VersionTests(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(updates.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updates.parse_version("1.2"), (1, 2, 0))
        self.assertEqual(updates.parse_version("V2"), (2, 0, 0))
        for bad in ("", "latest", "1.2-beta", "1.2.3.4", None):
            self.assertIsNone(updates.parse_version(bad))

    def test_is_newer_compares_numbers_not_text(self) -> None:
        self.assertTrue(updates.is_newer("v1.10.0", "1.9.0"))
        self.assertTrue(updates.is_newer("1.1.1", "1.1"))
        self.assertFalse(updates.is_newer("1.1.0", "1.1.0"))
        self.assertFalse(updates.is_newer("1.0.9", "1.1.0"))
        self.assertFalse(updates.is_newer("nightly", "1.0.0"))

    def test_app_version_is_parseable(self) -> None:
        self.assertIsNotNone(updates.parse_version(__version__))
        self.assertEqual(installer.VERSION, __version__)


class CheckTests(unittest.TestCase):
    def test_newer_release_is_returned_with_installer(self) -> None:
        op = opener_returning(release_json())
        info = updates.check("1.1.0", op)
        self.assertEqual(info.version, "9.0.0")
        self.assertEqual(info.setup_url, SETUP_URL)
        self.assertEqual(info.setup_size, len(PAYLOAD))
        self.assertEqual(info.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        self.assertEqual(info.notes, "Bug fixes")
        self.assertEqual(op.seen[0].full_url, updates.API_URL)
        self.assertIn("TimeBooks", op.seen[0].get_header("User-agent"))

    def test_same_or_older_release_is_none(self) -> None:
        self.assertIsNone(updates.check("9.0.0", opener_returning(release_json())))
        self.assertIsNone(updates.check("10.0", opener_returning(release_json())))

    def test_release_without_installer(self) -> None:
        info = updates.check("1.0.0", opener_returning(release_json(assets=[{"name": "notes.txt"}])))
        self.assertEqual(info.setup_url, "")

    def test_friendly_errors(self) -> None:
        cases = {http_error(404): "No published releases", http_error(403): "limiting",
                 http_error(500): "500", urllib.error.URLError("offline"): "internet connection",
                 TimeoutError(): "internet connection"}
        for exc, text in cases.items():
            with self.subTest(exc=exc), self.assertRaises(updates.UpdateError) as cm:
                updates.check("1.0.0", opener_raising(exc))
            self.assertIn(text, str(cm.exception))
        with self.assertRaises(updates.UpdateError):
            updates.check("1.0.0", opener_returning(b"<html>not json"))
        with self.assertRaises(updates.UpdateError):
            updates.check("1.0.0", opener_returning(release_json(tag="nightly")))


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.info = updates.check("1.0.0", opener_returning(release_json()))

    def dl(self, body=PAYLOAD, url="https://objects.githubusercontent.com/asset", **kw):
        return updates.download(self.info, self.dir, opener=opener_returning(body, url=url), **kw)

    def test_downloads_and_verifies(self) -> None:
        seen = []
        out = self.dl(progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(out, self.dir / "TimeBooks-Setup-9.0.0.exe")
        self.assertEqual(out.read_bytes(), PAYLOAD)
        self.assertEqual(seen[-1], (len(PAYLOAD), len(PAYLOAD)))
        self.assertFalse(list(self.dir.glob("*.part")))

    def test_bad_checksum_is_rejected_and_removed(self) -> None:
        tampered = b"X" + PAYLOAD[1:]
        with self.assertRaises(updates.UpdateError) as cm:
            self.dl(body=tampered)
        self.assertIn("integrity", str(cm.exception))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_short_download_is_rejected(self) -> None:
        with self.assertRaises(updates.UpdateError):
            self.dl(body=PAYLOAD[:-10])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_only_this_repos_release_links_are_downloaded(self) -> None:
        for url in ("http://github.com/" + updates.REPO + "/releases/download/v9/x.exe",
                    "https://github.com/someone/else/releases/download/v9/x.exe",
                    "https://evil.example.com/" + updates.REPO + "/releases/download/v9/x.exe"):
            self.info.setup_url = url
            with self.subTest(url=url), self.assertRaises(updates.UpdateError):
                self.dl()

    def test_unexpected_redirect_is_stopped(self) -> None:
        with self.assertRaises(updates.UpdateError) as cm:
            self.dl(url="https://evil.example.com/asset")
        self.assertIn("redirected", str(cm.exception))

    def test_cancel_from_progress_callback_cleans_up(self) -> None:
        def cancel(done, total):
            raise updates.UpdateError("cancelled")
        with self.assertRaises(updates.UpdateError):
            self.dl(progress=cancel)
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_no_installer_attached(self) -> None:
        self.info.setup_url = ""
        with self.assertRaises(updates.UpdateError):
            self.dl()


class InstallerUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        payload_dir = self.root / "res"
        payload_dir.mkdir()
        with zipfile.ZipFile(payload_dir / "payload.zip", "w") as z:
            z.writestr("TimeBooks.exe", "new exe")
            z.writestr("_internal/lib.dll", "new lib")
        for p in (mock.patch.object(installer, "resource", lambda name: payload_dir / name),
                  mock.patch.dict(os.environ, {"TIMEBOOKS_SETTINGS_DIR": str(self.root / "unused"),
                                               "APPDATA": str(self.root / "AppData")}),   # never the real one
                  mock.patch.object(paths, "_migrated", False)):
            p.start()
            self.addCleanup(p.stop)

    def test_portable_copy_is_updated_in_place_and_data_kept(self) -> None:
        folder = self.root / "USB" / paths.PORTABLE_FOLDER
        (folder / "_internal").mkdir(parents=True)
        (folder / "TimeBooks.exe").write_text("old exe")
        (folder / "_internal" / "old.dll").write_text("old")
        (folder / paths.PORTABLE_MARKER).write_text("portable")
        wb = paths.portable_data_dir(folder) / "Workbooks" / "Acme 2026.xlsx"
        wb.parent.mkdir(parents=True)
        wb.write_text("workbook")
        exe = installer.update(folder / "TimeBooks.exe")
        self.assertEqual(exe.read_text(), "new exe")
        self.assertFalse((folder / "_internal" / "old.dll").exists())
        self.assertEqual(wb.read_text(), "workbook")
        self.assertEqual(Path(os.environ["TIMEBOOKS_SETTINGS_DIR"]),
                         paths.portable_data_dir(folder) / "Settings")

    def test_installed_copy_keeps_existing_shortcut_choices(self) -> None:
        desktop, start = self.root / "Desktop.lnk", self.root / "Start.lnk"
        start.write_text("lnk")   # Start Menu shortcut exists, Desktop one was removed by the user
        with mock.patch.object(installer, "DESKTOP", desktop), mock.patch.object(installer, "START_MENU", start), \
                mock.patch.object(installer, "install", return_value=Path("x.exe")) as inst:
            installer.update(self.root / "Programs" / "TimeBooks" / "TimeBooks.exe")
        inst.assert_called_once_with(False, True)

    def test_retries_while_files_are_locked(self) -> None:
        calls = []

        def flaky(*a):
            calls.append(a)
            if len(calls) < 3:
                raise PermissionError("in use")
            return Path("x.exe")
        with mock.patch.object(installer, "install", side_effect=flaky):
            installer.update(self.root / "TimeBooks.exe", delay_s=0)
        self.assertEqual(len(calls), 3)
        with mock.patch.object(installer, "install", side_effect=PermissionError("in use")), \
                self.assertRaises(PermissionError):
            installer.update(self.root / "TimeBooks.exe", retries=2, delay_s=0)

    def test_wait_for_exit_of_finished_process(self) -> None:
        import subprocess
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertTrue(installer.wait_for_exit(p.pid, timeout_s=5))


if __name__ == "__main__":
    unittest.main()
