"""Phone photos over Wi-Fi: a tiny upload page served on the local network while the window is open.

The phone (on the same Wi-Fi) opens http://<this PC>:<port>/<code>, takes pictures of receipts and sends them; they are
saved to a folder and handed to the Expenses tab as drafts. Nothing goes over the internet. The page only answers at
the one-time code in the address, accepts pictures and PDFs only, and stops when the window closes.
"""
from __future__ import annotations

import re
import secrets
import socket
import threading
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import expenses

MAX_UPLOAD = 40 * 1024 * 1024   # bytes per send (a few phone photos)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>TimeBooks receipts</title><style>body{font-family:system-ui,sans-serif;margin:24px;font-size:18px}
input,button{font-size:20px;margin:12px 0;width:100%}button{padding:14px}</style></head><body>
<h2>Send receipts to TimeBooks</h2><form method="post" action="upload" enctype="multipart/form-data">
<input type="file" name="photo" accept="image/*,application/pdf" capture="environment" multiple>
<button type="submit">Send</button></form><p>{msg}</p></body></html>"""


def local_ip() -> str:
    """This PC's address on the local network (no data is sent; the socket only picks the outgoing interface)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def save_parts(content_type: str, body: bytes, dest: Path) -> list[Path]:
    """Save the pictures / PDFs from a multipart form body into dest (safe, unique names). Others are ignored."""
    msg = BytesParser(policy=email_policy).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
    saved = []
    for part in msg.iter_parts() if msg.is_multipart() else []:
        name = part.get_filename() or ""
        suffix = Path(name).suffix.lower() or {"image/jpeg": ".jpg", "image/png": ".png",
                                               "image/heic": ".heic"}.get(part.get_content_type(), "")
        data = part.get_payload(decode=True) or b""
        if suffix not in expenses.RECEIPT_TYPES or not data:
            continue
        stem = re.sub(r"[^A-Za-z0-9 _.-]+", "", Path(name).stem)[:40] or "photo"
        target = dest / f"Phone {datetime.now():%Y-%m-%d %H%M%S} {stem}{suffix}"
        n = 2
        while target.exists():
            target = target.with_name(f"{target.stem} ({n}){suffix}")
            n += 1
        target.write_bytes(data)
        saved.append(target)
    return saved


class PhoneUploadServer:
    """Serves the upload page at http://<ip>:<port>/<code>/ until stop(). on_file(path) runs for each saved file
    (on the server's thread: hand it to the UI with a queue)."""

    def __init__(self, dest: str | Path, on_file: Callable[[Path], None], host: str | None = None):
        self.dest = Path(dest)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.code = secrets.token_urlsafe(6)
        self.host = host or local_ip()
        server = self

        self.stopped = False

        class Handler(BaseHTTPRequestHandler):
            timeout = 30   # a phone that stalls mid-send doesn't hold the connection open

            def log_message(self, *args) -> None:   # no console output
                pass

            def _ok_path(self, tail: str = "") -> bool:
                """Only the page at the one-time code (and its upload address) answers."""
                return self.path.split("?")[0].rstrip("/") == f"/{server.code}" + (f"/{tail}" if tail else "")

            def _page(self, msg: str, status: int = 200) -> None:
                body = PAGE.replace("{msg}", msg).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if not self._ok_path():
                    self.send_error(404)
                    return
                self._page("Take a picture of each receipt, then Send.")

            def do_POST(self) -> None:
                if not self._ok_path("upload"):
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_UPLOAD:
                    self._page("That was too big to send at once. Send fewer photos at a time.", 413)
                    return
                body = self.rfile.read(length)
                if server.stopped:   # the window closed while this was sending: don't leave a file with no draft
                    self._page("TimeBooks stopped receiving. Open From my phone again and resend.", 503)
                    return
                files = save_parts(self.headers.get("Content-Type", ""), body, server.dest)
                for f in files:
                    on_file(f)
                self._page(f"Sent {len(files)} receipt{'s' if len(files) != 1 else ''}. Take more, or close this page."
                           if files else "No pictures were received. Choose or take a photo first.")

        self.httpd = ThreadingHTTPServer((self.host, 0), Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/{self.code}/"

    def start(self) -> "PhoneUploadServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.httpd.shutdown()
        self.httpd.server_close()


def qr_image(text: str, box: int = 6):
    """A QR code picture (PIL) for `text`, or None when the qrcode package isn't available."""
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(box_size=box, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image()
