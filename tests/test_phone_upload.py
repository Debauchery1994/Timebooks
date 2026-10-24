"""Phone photos over Wi-Fi: the upload page answers only at its one-time code and saves pictures / PDFs only."""
from __future__ import annotations

import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from timecard import phone_upload


def _multipart(files: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = "----tbtest"
    body = b""
    for name, data in files:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{name}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n"
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


class PhoneUploadTests(unittest.TestCase):
    def test_upload_saves_pictures_only_and_needs_the_code(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            got: list[Path] = []
            srv = phone_upload.PhoneUploadServer(d, got.append, host="127.0.0.1").start()
            try:
                self.assertIn("Send receipts", urllib.request.urlopen(srv.url, timeout=5).read().decode())
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/wrong/", timeout=5)
                ctx.exception.close()
                body, ctype = _multipart([("IMG_1.JPG", b"jpeg"), ("notes.txt", b"x"), ("../evil.pdf", b"%PDF")])
                req = urllib.request.Request(srv.url + "upload", data=body, headers={"Content-Type": ctype})
                self.assertIn("Sent 2 receipts", urllib.request.urlopen(req, timeout=5).read().decode())
            finally:
                srv.stop()
            self.assertEqual(sorted(p.suffix for p in got), [".jpg", ".pdf"])
            self.assertTrue(all(p.parent == Path(d) for p in got))   # no path tricks in file names
            self.assertTrue(phone_upload.qr_image(srv.url) is None or phone_upload.qr_image(srv.url).size[0] > 50)


if __name__ == "__main__":
    unittest.main()
