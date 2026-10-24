"""Offline receipt reading: Windows OCR plus rules that pick out vendor, date, total, tax and items.

Everything here works without a network connection.

OCR (``ocr_image``) uses the OCR engine built into Windows 10/11 (``Windows.Media.Ocr``). Two ways to reach it:

* ``winrt``: the PyWinRT projection packages (``winrt-runtime`` + ``winrt-Windows.Media.Ocr`` ...), in process,
  roughly 10-60 ms per pass. Preferred; used whenever the packages import.
* ``powershell``: Windows PowerShell 5.1 runs a small embedded script that loads the image through WinRT and prints
  JSON. No extra Python packages, but about 0.3 s per pass for starting PowerShell, and it can be blocked by
  locked-down IT policies. Used as a fallback.

Set the environment variable ``TIMECARD_OCR_BACKEND`` to ``winrt`` or ``powershell`` to force one.

The image is prepared with Pillow first (EXIF rotation, greyscale, auto-contrast), then read up to three times:
straightened if Windows reports the text at an angle, and enlarged if the letters are small. The words are then
regrouped into printed rows by their positions, because Windows OCR returns a wide price column as separate "lines".

Field rules (``extract_fields``) work on plain text lines, so they can be tested without OCR and reused for text
from any other source. Money is always ``Decimal`` rounded to cents (ROUND_HALF_UP), never float.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Iterable, Iterator, Sequence

__all__ = [
    "OcrUnavailableError", "ReceiptReadError", "OcrWord", "OcrLine", "OcrResult", "Field", "LineItem",
    "ReceiptFields", "IMAGE_SUFFIXES", "PDF_SUFFIXES", "ocr_available", "ocr_backend", "ocr_image",
    "extract_fields", "read_receipt", "parse_money",
]

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".jfif", ".bmp", ".dib", ".tif", ".tiff", ".gif", ".webp"}
PDF_SUFFIXES = {".pdf"}

HIGH = "high"
LOW = "low"
CENT = Decimal("0.01")


class ReceiptReadError(Exception):
    """The file could not be read as a receipt image (missing, unsupported or damaged)."""


class OcrUnavailableError(ReceiptReadError):
    """Windows OCR can't be used on this computer (not Windows, no OCR language, or both routes blocked)."""


# --------------------------------------------------------------------------------------------------------------
# OCR results
# --------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class OcrWord:
    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class OcrLine:
    """One printed row of the receipt, left to right. ``box`` is (x, y, width, height) in the analysed image."""

    text: str
    words: list[OcrWord]
    box: tuple[float, float, float, float]


@dataclass
class OcrResult:
    lines: list[OcrLine]
    engine: str = ""                 # "winrt" or "powershell"
    language: str = ""
    text_angle: float | None = None  # degrees Windows reported for the first pass
    image_size: tuple[int, int] = (0, 0)   # size of the image the boxes refer to
    scale: float = 1.0               # analysed image size / original image size
    rotated: float = 0.0             # degrees the image was turned before the final pass
    passes: int = 0
    seconds: float = 0.0

    @property
    def text_lines(self) -> list[str]:
        return [ln.text for ln in self.lines]

    @property
    def text(self) -> str:
        return "\n".join(self.text_lines)


# --------------------------------------------------------------------------------------------------------------
# OCR backends
# --------------------------------------------------------------------------------------------------------------

def _winrt_modules():
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    return Language, BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap, OcrEngine, DataWriter


def _winrt_importable() -> bool:
    if sys.platform != "win32":
        return False
    try:
        _winrt_modules()
    except Exception:
        return False
    return True


def _powershell_exe() -> str | None:
    if sys.platform != "win32":
        return None
    root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if exe.is_file():
        return str(exe)
    return shutil.which("powershell.exe")


_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) {
  $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $null = $task.Wait(-1); $task.Result }
$lang = $env:TIMECARD_OCR_LANG
if ($env:TIMECARD_OCR_PROBE) {
  $langs = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
  ConvertTo-Json -Compress -InputObject @{ languages = $langs }
  return
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($lang))
if ($null -eq $engine) { throw "No Windows OCR language $lang" }
$results = @()
foreach ($p in $env:TIMECARD_OCR_FILES.Split('|')) {
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  try {
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  } finally { $stream.Dispose() }
  $lines = @($res.Lines | ForEach-Object { ,@($_.Words | ForEach-Object {
    @{ t = $_.Text; x = $_.BoundingRect.X; y = $_.BoundingRect.Y; w = $_.BoundingRect.Width; h = $_.BoundingRect.Height } }) })
  $results += ,@{ angle = $res.TextAngle; lines = $lines }
}
ConvertTo-Json -Compress -Depth 8 -InputObject @($results)
"""

# A raw recognition: (text angle or None, [[OcrWord, ...] per Windows line])
_RawPass = tuple[float | None, list[list[OcrWord]]]


class _WinRtBackend:
    name = "winrt"

    def __init__(self) -> None:
        (self._Language, self._Alpha, self._Fmt, self._SoftwareBitmap, self._OcrEngine,
         self._DataWriter) = _winrt_modules()
        self._engines: dict[str, object] = {}

    def languages(self) -> list[str]:
        return [lang.language_tag for lang in self._OcrEngine.available_recognizer_languages]

    @property
    def max_dimension(self) -> int:
        return int(self._OcrEngine.max_image_dimension)

    def _engine(self, language: str):
        if language not in self._engines:
            engine = self._OcrEngine.try_create_from_language(self._Language(language))
            if engine is None:
                raise OcrUnavailableError(f"Windows OCR has no '{language}' language installed.")
            self._engines[language] = engine
        return self._engines[language]

    def recognize(self, images: Sequence, language: str) -> list[_RawPass]:
        engine = self._engine(language)
        out: list[_RawPass] = []
        for img in images:
            writer = self._DataWriter()
            writer.write_bytes(img.convert("L").tobytes())
            bitmap = self._SoftwareBitmap.create_copy_from_buffer(
                writer.detach_buffer(), self._Fmt.GRAY8, img.width, img.height, self._Alpha.IGNORE)
            result = _run_async(engine.recognize_async(bitmap))
            lines = []
            for line in result.lines:
                words = []
                for word in line.words:
                    r = word.bounding_rect
                    words.append(OcrWord(word.text, float(r.x), float(r.y), float(r.width), float(r.height)))
                if words:
                    lines.append(words)
            angle = result.text_angle
            out.append((float(angle) if angle is not None else None, lines))
        return out


def _run_async(awaitable):
    async def wait():
        return await awaitable
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(wait())
    # Called from inside a running event loop (unusual for this tkinter app): use a helper thread.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, wait()).result()


class _PowerShellBackend:
    name = "powershell"
    max_dimension = 10000   # OcrEngine.MaxImageDimension on Windows 10/11

    def __init__(self, exe: str) -> None:
        self._exe = exe
        self._encoded = base64.b64encode(_PS_SCRIPT.encode("utf-16-le")).decode("ascii")

    def _run(self, env_extra: dict[str, str], timeout: float) -> object:
        env = dict(os.environ)
        env.update(env_extra)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [self._exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", self._encoded],
                capture_output=True, env=env, timeout=timeout, creationflags=flags, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrUnavailableError(f"Windows PowerShell OCR did not run: {exc}") from exc
        out = proc.stdout.decode("utf-8-sig", "replace").strip()
        if proc.returncode != 0 or not out:
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise OcrUnavailableError("Windows PowerShell OCR failed: " + (err[0] if err else f"exit {proc.returncode}"))
        try:
            return json.loads(out.splitlines()[-1])
        except ValueError as exc:
            raise OcrUnavailableError("Windows PowerShell OCR returned unreadable output.") from exc

    def languages(self) -> list[str]:
        data = self._run({"TIMECARD_OCR_PROBE": "1", "TIMECARD_OCR_LANG": ""}, timeout=60)
        langs = data.get("languages", []) if isinstance(data, dict) else []
        return [langs] if isinstance(langs, str) else list(langs)

    def recognize(self, images: Sequence, language: str) -> list[_RawPass]:
        with tempfile.TemporaryDirectory(prefix="tc-ocr-") as tmp:
            paths = []
            for i, img in enumerate(images):
                p = os.path.join(tmp, f"pass{i}.png")
                img.convert("RGB").save(p, "PNG", compress_level=1)
                paths.append(p)
            data = self._run({"TIMECARD_OCR_FILES": "|".join(paths), "TIMECARD_OCR_LANG": language},
                             timeout=60 + 15 * len(paths))
        if isinstance(data, dict):
            data = [data]
        out: list[_RawPass] = []
        for item in data:
            lines = []
            raw_lines = item.get("lines") or []
            for raw in raw_lines:
                if isinstance(raw, dict):     # PowerShell unwrapped a one-word line
                    raw = [raw]
                words = [OcrWord(str(w["t"]), float(w["x"]), float(w["y"]), float(w["w"]), float(w["h"]))
                         for w in raw if isinstance(w, dict) and w.get("t")]
                if words:
                    lines.append(words)
            angle = item.get("angle")
            out.append((float(angle) if angle is not None else None, lines))
        return out


_backend_cache: dict[str, object] = {}


def _get_backend(name: str | None = None):
    name = (name or os.environ.get("TIMECARD_OCR_BACKEND") or "auto").strip().lower()
    if name in _backend_cache:
        return _backend_cache[name]
    backend = None
    if sys.platform != "win32":
        raise OcrUnavailableError("Receipt reading uses Windows OCR, which needs Windows 10 or 11.")
    if name in ("auto", "winrt") and _winrt_importable():
        backend = _WinRtBackend()
    if backend is None and name in ("auto", "powershell"):
        exe = _powershell_exe()
        if exe:
            backend = _PowerShellBackend(exe)
    if backend is None:
        raise OcrUnavailableError(f"No Windows OCR route available (requested: {name}).")
    _backend_cache[name] = backend
    return backend


_available_cache: dict[tuple[str, str], bool] = {}


def ocr_available(language: str = "en-US", backend: str | None = None) -> bool:
    """True when Windows OCR can read ``language`` text on this computer (result is cached)."""
    key = (backend or os.environ.get("TIMECARD_OCR_BACKEND") or "auto", language.lower())
    if key not in _available_cache:
        try:
            langs = [lang.lower() for lang in _get_backend(backend).languages()]
            _available_cache[key] = language.lower() in langs or any(
                lang.split("-")[0] == language.lower().split("-")[0] for lang in langs)
        except Exception:
            _available_cache[key] = False
    return _available_cache[key]


def ocr_backend(backend: str | None = None) -> str | None:
    """Name of the OCR route that would be used ("winrt" / "powershell"), or None if there is none."""
    try:
        return _get_backend(backend).name
    except OcrUnavailableError:
        return None


# --------------------------------------------------------------------------------------------------------------
# Loading and preparing the image
# --------------------------------------------------------------------------------------------------------------

def _load_image(path: Path, pdf_dpi: int = 300):
    from PIL import Image, ImageOps, UnidentifiedImageError

    if not path.is_file():
        raise ReceiptReadError(f"Receipt file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise ReceiptReadError("Reading PDF receipts needs the pypdfium2 package.") from exc
        try:
            pdf = pdfium.PdfDocument(str(path))
            try:
                if len(pdf) == 0:
                    raise ReceiptReadError(f"The PDF has no pages: {path.name}")
                page = pdf[0]
                img = page.render(scale=pdf_dpi / 72, grayscale=True).to_pil()
                page.close()
            finally:
                pdf.close()
        except ReceiptReadError:
            raise
        except Exception as exc:
            raise ReceiptReadError(f"Couldn't open the PDF {path.name}: {exc}") from exc
        return img.convert("L")
    try:
        with Image.open(path) as im:
            im.seek(0)   # first frame of a multi-page TIFF/GIF
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P", "PA"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            return im.convert("L")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ReceiptReadError(f"Couldn't open {path.name} as an image: {exc}") from exc


def _prepare(img, max_dim: int):
    from PIL import ImageOps

    img = ImageOps.autocontrast(img, cutoff=1)
    if max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
    return img


def _scaled(img, factor: float, max_dim: int):
    from PIL import Image

    factor = min(factor, max_dim / max(img.size))
    if abs(factor - 1) < 0.05:
        return img, 1.0
    size = (max(1, round(img.width * factor)), max(1, round(img.height * factor)))
    return img.resize(size, Image.LANCZOS), factor


def _rotated(img, degrees: float):
    from PIL import Image

    # Windows reports the angle the text is tilted clockwise; Pillow rotates counter-clockwise for positive degrees.
    return img.rotate(degrees, resample=Image.BICUBIC, expand=True, fillcolor=255)


# --------------------------------------------------------------------------------------------------------------
# Regrouping words into printed rows
# --------------------------------------------------------------------------------------------------------------

def _skew(raw_lines: list[list[OcrWord]], angle: float | None) -> float:
    """Slope (dy/dx) of the printed rows: from the words of each OCR line, else from Windows' text angle."""
    import math

    slopes = []
    for words in raw_lines:
        if len(words) >= 2:
            a, b = words[0], words[-1]
            dx = (b.x + b.w / 2) - (a.x + a.w / 2)
            if dx > 4 * max(a.h, b.h, 1):
                slopes.append(((b.y + b.h / 2) - (a.y + a.h / 2)) / dx)
    if len(slopes) >= 3:
        return statistics.median(slopes)
    if angle:
        return math.tan(math.radians(angle))
    return 0.0


def _rows(raw_lines: list[list[OcrWord]], angle: float | None = None) -> list[OcrLine]:
    """Merge Windows OCR lines that sit on the same printed row (e.g. a description and its price column).

    Rows on a slightly tilted receipt drift up or down across the page, so positions are levelled first.
    """
    if not raw_lines:
        return []
    slope = _skew(raw_lines, angle)
    groups = []   # each: [top, bottom, words] with top/bottom levelled to x = 0
    for words in raw_lines:
        top = min(w.y - slope * (w.x + w.w / 2) for w in words)
        bottom = max(w.y + w.h - slope * (w.x + w.w / 2) for w in words)
        groups.append([top, bottom, list(words)])
    groups.sort(key=lambda g: (g[0] + g[1]) / 2)
    rows: list[list] = []
    for g in groups:
        centre = (g[0] + g[1]) / 2
        height = g[1] - g[0]
        best = None
        for row in rows[-3:]:
            overlap = min(row[1], g[1]) - max(row[0], g[0])
            row_height = row[1] - row[0]
            row_centre = (row[0] + row[1]) / 2
            if overlap > 0.5 * min(height, row_height) and abs(centre - row_centre) < 0.6 * max(height, row_height):
                best = row
        if best is None:
            rows.append(g)
        else:
            best[0] = min(best[0], g[0])
            best[1] = max(best[1], g[1])
            best[2].extend(g[2])
    lines = []
    for top, bottom, words in rows:
        words.sort(key=lambda w: w.x)
        text = " ".join(w.text for w in words)
        x0 = min(w.x for w in words)
        x1 = max(w.x + w.w for w in words)
        y0 = min(w.y for w in words)
        y1 = max(w.y + w.h for w in words)
        lines.append(OcrLine(text=text, words=words, box=(x0, y0, x1 - x0, y1 - y0)))
    return lines


_MONEY_HINT = re.compile(r"\d[.,]\s?\d\d(?!\d)")


_SCORE_WORDS = re.compile(r"^(TOTAL|SUBTOTAL|TAX|DUE|AMOUNT|BALANCE|CHANGE|CASH|VISA|DATE)\W*$", re.I)
_DATE_HINT = re.compile(r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{2,4}")


def _pass_score(raw_lines: list[list[OcrWord]]) -> tuple[int, int]:
    """How useful a recognition looks: clean amounts, dates and receipt keywords first, then readable characters."""
    words = [w.text for line in raw_lines for w in line]
    useful = sum(1 for w in words if _MONEY_HINT.search(w) or _DATE_HINT.search(w) or _SCORE_WORDS.match(w))
    return (useful, sum(sum(c.isalnum() for c in w) for w in words))


def _median_height(raw_lines: list[list[OcrWord]]) -> float:
    heights = [w.h for line in raw_lines for w in line if w.h > 0]
    return statistics.median(heights) if heights else 0.0


# Windows OCR reads best when capital letters are roughly this many pixels tall.
_TARGET_HEIGHT = 32.0
_SMALL_HEIGHT = 22.0


def ocr_image(path: str | os.PathLike, *, language: str = "en-US", backend: str | None = None) -> OcrResult:
    """Read the text of a receipt image (PNG/JPG/BMP/TIFF/GIF/WebP) or the first page of a PDF with Windows OCR.

    Raises ``OcrUnavailableError`` when Windows OCR can't be used and ``ReceiptReadError`` for a bad file.
    """
    started = time.perf_counter()
    path = Path(path)
    ocr = _get_backend(backend)
    original = _load_image(path)
    max_dim = getattr(ocr, "max_dimension", 10000)
    base = _prepare(original, max_dim)
    base_scale = base.width / original.width if original.width else 1.0

    # Pass 1 at the prepared size.
    (angle, raw), = ocr.recognize([base], language)
    first_angle = angle
    best = (raw, base, base_scale, 0.0, angle)
    passes = 1
    img = base
    rotated = 0.0

    # Pass 2: straighten when the text is tilted (row grouping needs level rows; OCR also reads better).
    if angle is not None and 0.4 <= abs(angle) <= 45:
        img = _rotated(base, angle)
        rotated = angle
        (angle2, raw2), = ocr.recognize([img], language)
        passes += 1
        if _pass_score(raw2) >= _pass_score(raw):
            best = (raw2, img, base_scale, rotated, angle2)
            raw = raw2
        else:
            img, rotated = base, 0.0

    # Pass 3: enlarge small print (or try 2x when nothing was found at all). Very small print is tried at two
    # sizes in one batch, because the best enlargement differs between fonts.
    height = _median_height(raw)
    factor = _TARGET_HEIGHT / height if 0 < height < _SMALL_HEIGHT else (2.0 if not raw else 1.0)
    if factor > 1.05:
        factors = [min(factor, 4.0)]
        if factor > 2.6:
            factors.append(2.0)
        bigs = [b for b in (_scaled(img, f, max_dim) for f in factors) if b[1] > 1.0]
        if bigs:
            for (angle3, raw3), (big, used) in zip(ocr.recognize([b[0] for b in bigs], language), bigs):
                passes += 1
                if _pass_score(raw3) >= _pass_score(best[0]):
                    best = (raw3, big, base_scale * used, rotated, angle3)

    raw, final_img, scale, rotated, final_angle = best
    return OcrResult(
        lines=_rows(raw, final_angle), engine=ocr.name, language=language, text_angle=first_angle,
        image_size=final_img.size, scale=scale, rotated=rotated, passes=passes,
        seconds=time.perf_counter() - started)


# --------------------------------------------------------------------------------------------------------------
# Field extraction
# --------------------------------------------------------------------------------------------------------------

@dataclass
class Field:
    """One extracted value with how sure the rules are and the receipt line it came from."""

    value: object = None
    confidence: str = LOW
    source: str | None = None
    line_index: int | None = None
    note: str = ""

    @property
    def found(self) -> bool:
        return self.value is not None

    @property
    def needs_review(self) -> bool:
        return self.value is None or self.confidence != HIGH


@dataclass
class LineItem:
    """A purchased line. Unpacks like a tuple: ``description, amount = item``."""

    description: str
    amount: Decimal
    confidence: str = HIGH
    source: str = ""
    line_index: int = -1

    def __iter__(self) -> Iterator:
        return iter((self.description, self.amount))


@dataclass
class ReceiptFields:
    vendor: Field = field(default_factory=Field)
    date: Field = field(default_factory=Field)
    total: Field = field(default_factory=Field)
    subtotal: Field = field(default_factory=Field)
    tax: Field = field(default_factory=Field)
    tip: Field = field(default_factory=Field)
    payment_method: Field = field(default_factory=Field)   # "Cash" / "Card" / "Check" / "Other" (Expenses sheet)
    items: list[LineItem] = field(default_factory=list)
    items_confidence: str = LOW      # high when the items add up to the subtotal (or total)
    warnings: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    ocr: OcrResult | None = None

    SCALAR_FIELDS = ("vendor", "date", "total", "subtotal", "tax", "tip", "payment_method")

    @property
    def confidence(self) -> dict[str, str]:
        out = {name: getattr(self, name).confidence for name in self.SCALAR_FIELDS}
        out["items"] = self.items_confidence
        return out

    def needs_review(self) -> list[str]:
        """Field names a person should look at: the main fields when missing or low, others only when low."""
        names = [n for n in ("vendor", "date", "total") if getattr(self, n).needs_review]
        names += [n for n in ("subtotal", "tax", "tip", "payment_method")
                  if getattr(self, n).found and getattr(self, n).confidence != HIGH]
        if self.items and self.items_confidence != HIGH:
            names.append("items")
        return names

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {n: getattr(self, n).value for n in self.SCALAR_FIELDS}
        out["items"] = [(i.description, i.amount) for i in self.items]
        return out


# ---- text normalisation ------------------------------------------------------------------------------------------

_DIGITISH = {"O": "0", "o": "0", "Q": "0", "D": "0", "e": "0", "ø": "0", "Ø": "0", "€": "0", "°": "0",
             "I": "1", "l": "1", "|": "1", "i": "1", "!": "1", "L": "1",
             "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9", "q": "9"}
_DIGITISH_TOKEN = re.compile(r"^[$\-(]?[0-9OoQDeøØ€°IlL|i!SsBZzgq]*([.,:/\-][0-9OoQDeøØ€°IlL|i!SsBZzgq]*)*[\-)]?$")
_LETTER_FIX = {"0": "O", "1": "I", "5": "S", "8": "B", "4": "A", "|": "I", "$": "S"}


def _fix_digit_token(token: str) -> str:
    """'I.e4' -> '1.04', 'le/24/26' -> '10/24/26'; leaves real words alone."""
    if not _DIGITISH_TOKEN.match(token):
        return token
    core = token.strip("$-()")
    real_digits = sum(c.isdigit() for c in core)
    has_sep = any(c in ".,:/" for c in core)
    if real_digits == 0 and not (has_sep and len(core) <= 3):
        return token
    if real_digits == 0 and not has_sep:
        return token
    # Mostly letters (e.g. "Sl" or "Ice") is a word, not a number.
    if real_digits < len([c for c in core if c.isalnum()]) / 2 and not has_sep:
        return token
    return "".join(_DIGITISH.get(c, c) for c in token)


def _digit_fixed(line: str) -> str:
    return " ".join(_fix_digit_token(t) for t in line.split())


def _keyword_tokens(line: str) -> list[str]:
    """Upper-case word tokens with digit look-alikes inside words fixed: 'T0TAL' -> 'TOTAL'."""
    tokens = []
    for tok in re.split(r"[^A-Za-z0-9$|]+", line.upper()):
        if not tok:
            continue
        letters = sum(c.isalpha() for c in tok)
        if letters >= 2 and letters < len(tok) and not re.fullmatch(r"[A-Z]+\d{1,2}", tok):
            tok = "".join(_LETTER_FIX.get(c, c) for c in tok)
        tokens.append(tok)
    return tokens


def _edit1(a: str, b: str) -> bool:
    """True when a and b differ by at most one inserted, deleted or changed letter."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if b[:i] + b[i + 1:] == a:
            return True
    return False


_FUZZY_WORDS = ("TOTAL", "SUBTOTAL", "BALANCE", "AMOUNT", "CHANGE", "TENDERED")


def _canonical(line: str) -> tuple[str, bool]:
    """Keyword string for a line ('SUB TOTAL' style), plus whether a keyword was only matched approximately."""
    fuzzy = False
    out = []
    for tok in _keyword_tokens(line):
        if len(tok) >= 4 and tok.isalpha() and tok not in _FUZZY_WORDS and tok not in _KNOWN_WORDS:
            for word in _FUZZY_WORDS:
                if len(tok) >= len(word) - 1 and _edit1(tok, word):
                    tok, fuzzy = word, True
                    break
        out.append(tok)
    return " " + " ".join(out) + " ", fuzzy


# Real words that are one letter away from a keyword and must not be "corrected".
_KNOWN_WORDS = {"TOTE", "TOTES", "TOTALS", "AMOUNTS", "BALANCED", "CHANGED", "CHANGES", "TENDER", "TOTA"}


# ---- money -------------------------------------------------------------------------------------------------------

_MONEY = re.compile(
    r"(?<![\d.,/:])"
    r"(?P<neg1>-\s?|\(\s?)?"
    r"(?P<cur>\$|USD\s?)?\s?(?P<neg2>-\s?)?"
    r"(?P<int>\d{1,3}(?:,\s?\d{3})+|\d+)"
    r"(?:\s?[.,]\s?|(?<=\d)\s(?=\d\d(?:\D|$)))"
    r"(?P<dec>\d{2})"
    r"(?![\d%/:]|\.\d|,\d)"
    r"(?P<neg3>\s?-|\s?\)|\s?CR\b)?",
    re.IGNORECASE)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _money_matches(text: str) -> list[tuple[Decimal, re.Match]]:
    out = []
    for m in _MONEY.finditer(text):
        dec_sep_space = re.search(r"\d\s(?=\d\d)", m.group(0)) and not re.search(r"[.,]", m.group(0)[-6:])
        if dec_sep_space and not m.group("cur"):
            continue   # "12 34" without a $ is two numbers, not $12.34
        if m.group("int").startswith("0") and len(m.group("int")) > 1 and "," not in m.group("int"):
            continue   # "0042" style card digits / codes
        whole = re.sub(r"[,\s]", "", m.group("int"))
        value = _quantize(Decimal(f"{whole}.{m.group('dec')}"))
        negative = bool(m.group("neg1") or m.group("neg2") or m.group("neg3"))
        if negative and m.group("neg1") and m.group("neg1").startswith("(") and not (m.group("neg3") or "").strip().startswith(")"):
            negative = False
        out.append((-value if negative else value, m))
    return out


def parse_money(text: str) -> Decimal | None:
    """The last money amount in ``text`` ("$1,234.50", "84.17-", "(3.00)") as a Decimal, else None."""
    found = _money_matches(text) or _money_matches(_digit_fixed(text))
    return found[-1][0] if found else None


def _line_amount(raw: str, fixed: str) -> tuple[Decimal | None, bool]:
    """(last amount on the line, whether OCR look-alike fixes were needed to read it)."""
    found = _money_matches(raw)
    if found:
        return found[-1][0], False
    found = _money_matches(fixed)
    if found:
        return found[-1][0], True
    return None, False


# ---- dates -------------------------------------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
_MON = r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*\.?"
_DATE_PATTERNS = [
    ("ymd", re.compile(r"(?<!\d)(20\d\d)\s?([/\-.])\s?(\d{1,2})\s?\2\s?(\d{1,2})(?!\d)")),
    ("mdy", re.compile(r"(?<![\d.])(\d{1,2})\s?([/\-.])\s?(\d{1,2})\s?\2\s?(\d{4}|\d{2})(?![\d.]\d|\d)")),
    ("Mdy", re.compile(r"\b" + _MON + r"\s?(\d{1,2})(?:ST|ND|RD|TH)?,?\s+'?(\d{4}|\d{2})(?!\d)", re.I)),
    ("dMy", re.compile(r"(?<!\d)(\d{1,2})\s?-?\s?" + _MON + r"\s?-?,?\s?'?(\d{4}|\d{2})(?!\d)", re.I)),
]
_DATE_IGNORE_CONTEXT = re.compile(r"\b(RETURN|RETURNS|EXPIRES?|EXP|VALID|BEFORE|UNTIL|BY|DUE)\b", re.I)


def _year(text: str) -> int:
    y = int(text)
    return 2000 + y if y < 100 else y


def _dates_in(text: str, today: date) -> list[tuple[date, bool]]:
    """Valid dates in a line, with a flag saying the reading was unusual (day-first)."""
    found: list[tuple[int, date, bool]] = []
    for kind, pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            unusual = False
            try:
                if kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(3)), int(m.group(4))
                elif kind == "mdy":
                    if m.group(2) == "." and len(m.group(4)) == 2:
                        continue   # "12.34.56" is more likely a number than a date
                    a, b, y = int(m.group(1)), int(m.group(3)), _year(m.group(4))
                    mo, d = a, b
                    if a > 12 and b <= 12:   # day-first (not US) - possible but unusual
                        mo, d, unusual = b, a, True
                elif kind == "Mdy":
                    mo, d, y = _MONTHS[m.group(1).upper()[:3]], int(m.group(2)), _year(m.group(3))
                else:
                    d, mo, y = int(m.group(1)), _MONTHS[m.group(2).upper()[:3]], _year(m.group(3))
                value = date(y, mo, d)
            except (ValueError, KeyError):
                continue
            if not (2000 <= value.year and value <= date(today.year + 1, today.month, min(today.day, 28))):
                continue
            found.append((m.start(), value, unusual))
    found.sort(key=lambda t: t[0])
    return [(v, u) for _, v, u in found]


# ---- line classification -----------------------------------------------------------------------------------------

_LABELS = [
    # (kind, priority for totals, pattern on the canonical keyword string)
    ("subtotal", 0, re.compile(r" SUB ?-?TOTAL | SUB TTL | SUBTTL | MERCHANDISE TOTAL | NET SALES ")),
    ("change", 0, re.compile(r" CHANGE | CHG ")),
    ("tip_hint", 0, re.compile(r"(?=.* (TIP|GRATUITY) )(?=.*%)")),
    ("tip", 0, re.compile(r" TIP | GRATUITY | TIPS ")),
    ("tax", 0, re.compile(r" (SALES |STATE |COUNTY |CITY |LOCAL )?(TAX|TAX\d|TX|GST|HST|PST|VAT|TAXES)( TOTAL)? ")),
    ("discount", 0, re.compile(r" (SAVINGS|SAVED|SAVE|DISCOUNT|DISC|COUPON|REWARDS?|POINTS|PROMO) ")),
    ("due", 3, re.compile(r" (AMOUNT|AMT|BALANCE|BAL|TOTAL) DUE | GRAND TOTAL | TOTAL AMOUNT DUE | PLEASE PAY ")),
    ("tender", 0, re.compile(r" (CASH|TENDER|TENDERED|PAYMENT|PAID|VISA|MASTERCARD|MC|AMEX|DISCOVER|DEBIT|CREDIT|"
                             r"EBT|GIFT CARD|APPLE PAY|GOOGLE PAY) ")),
    ("total", 2, re.compile(r" (TOTAL|TTL|TOT|BALANCE) ")),
    ("amount", 1, re.compile(r" (AMOUNT|AMT|SALE AMOUNT|PURCHASE|SALE TOTAL|FUEL SALE) ")),
]
_NOT_TAX = re.compile(r" (TAXABLE|NONTAXABLE|NON|EXEMPT|PRE|BEFORE|W/O|WITHOUT|EXCL) ")
_NOT_TOTAL = re.compile(r" (ITEMS?|QTY|QUANTITY|COUNT|UNITS|PIECES|SOLD|POINTS|REMAINING|AVAILABLE|PREVIOUS|PREV|GIFT) ")
_CARD_WORDS = {"VISA": "Visa", "MASTERCARD": "Mastercard", "MC": "Mastercard", "AMEX": "American Express",
               "AMERICAN EXPRESS": "American Express", "DISCOVER": "Discover", "DEBIT": "Debit card",
               "CREDIT": "Credit card", "EBT": "EBT", "APPLE PAY": "Apple Pay", "GOOGLE PAY": "Google Pay",
               "GIFT CARD": "Gift card", "CARD": "Card", "CHIP": "Card", "CONTACTLESS": "Card", "TAP": "Card"}


def _classify(canon: str) -> tuple[str | None, int]:
    for kind, prio, pattern in _LABELS:
        if pattern.search(canon):
            if kind == "tax" and _NOT_TAX.search(canon):
                continue
            if kind == "total" and _NOT_TOTAL.search(canon):
                continue
            if kind == "tender" and re.search(r" (TOTAL|TTL) ", canon) and not re.search(r" (CASH|TENDER\w*) ", canon):
                # "VISA TOTAL"/"TOTAL CREDIT": treat as a total line
                return "total", 2
            return kind, prio
    return None, 0


# ---- vendor ------------------------------------------------------------------------------------------------------

_PHONE = re.compile(r"\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}\b")
_URL = re.compile(r"(WWW\.|HTTPS?:|\.COM\b|\.NET\b|\.ORG\b|\.US\b|@)", re.I)
_STREET = re.compile(
    r"\b(ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|HWY|HIGHWAY|DR|DRIVE|LN|LANE|PKWY|PARKWAY|WAY|CT|COURT|PL|PLAZA|SUITE|"
    r"STE|UNIT|ROUTE|RTE|PIKE|CIR|TRL|SQ|FLOOR|FL)\b\.?", re.I)
_CITY_ZIP = re.compile(r"\b[A-Z]{2}\.?\s+\d{5}(-\d{4})?\b|,\s*[A-Z]{2}\b", re.I)
_STORE_NO = re.compile(r"\b(STORE|STR|ST#|LOC|LOCATION|BRANCH|REG|REGISTER|TERM|TERMINAL|TRANS|TRAN|CASHIER|"
                       r"OPERATOR|OP|SERVER|TABLE|GUEST|ORDER|INVOICE|RECEIPT|TICKET|CHECK|MERCHANT|MID|TID)\b"
                       r"\s*(#|NO\.?|NUM|ID|:)?\s*\d*", re.I)
_SKIP_VENDOR = re.compile(
    r"^\W*(WELCOME|WELCOME TO|THANK|THANKS|CUSTOMER COPY|MERCHANT COPY|CARDHOLDER COPY|COPY|RECEIPT|SALES? RECEIPT|SALE|"
    r"ORIGINAL|DUPLICATE|REPRINT|ORDER|INVOICE|TRANSACTION RECORD|\*+)\W*$", re.I)
_WELCOME_PREFIX = re.compile(r"^\W*(WELCOME\s+TO|THANK\s+YOU\s+FOR\s+SHOPPING\s+AT)\s+", re.I)


def _vendor_candidate(line: str) -> str | None:
    text = re.sub(r"^[^\w]+|[^\w.)'!]+$", "", _WELCOME_PREFIX.sub("", line))
    if not text or _SKIP_VENDOR.match(text):
        return None
    letters = sum(c.isalpha() for c in text)
    if letters < 3 or letters < 0.5 * len(text.replace(" ", "")):
        return None
    if _PHONE.search(text) or _URL.search(text):
        return None
    if re.match(r"^\d+\s+\w+", text) and (_STREET.search(text) or len(text.split()) <= 6):
        return None   # "123 Main St", "4410 W Lake"
    if _CITY_ZIP.search(text) and re.search(r"\d{5}", text):
        return None
    if re.search(r",\s*[A-Z]{2}\s*$", text):
        return None   # "Springfield, IL"
    if _STORE_NO.match(text) and re.search(r"\d", text):
        return None
    if re.search(r"\b(TEL|PHONE|FAX|OPEN|HOURS|MON|TUE|TUES|WED|THU|FRI|SAT|SUN)\b", text, re.I):
        return None
    if any(_dates_in(text, date.today())) or _money_matches(text):
        return None
    canon, _ = _canonical(text)
    if _classify(canon)[0] in ("subtotal", "total", "tax", "due", "tender", "change"):
        return None
    return re.sub(r"\s+", " ", text)


def _match_known(name: str, known: Iterable[str]) -> str | None:
    import difflib

    def norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", "".join(_keyword_tokens(s.replace("&", " AND "))))
    target = norm(name)
    best, best_ratio = None, 0.0
    for k in known:
        nk = norm(k)
        if not nk:
            continue
        if nk == target or (len(nk) >= 4 and (nk in target or target in nk)):
            ratio = 1.0
        else:
            ratio = difflib.SequenceMatcher(None, nk, target).ratio()
        if ratio > best_ratio:
            best, best_ratio = k, ratio
    return best if best_ratio >= 0.82 else None


# ---- the extractor -----------------------------------------------------------------------------------------------

@dataclass
class _Line:
    index: int
    raw: str
    fixed: str
    canon: str
    fuzzy: bool
    kind: str | None
    priority: int
    amount: Decimal | None
    amount_fixed: bool


def _analyse(lines: Sequence[str]) -> list[_Line]:
    out = []
    for i, raw in enumerate(lines):
        raw = raw.strip()
        fixed = _digit_fixed(raw)
        canon, fuzzy = _canonical(raw)
        kind, prio = _classify(canon)
        amount, amount_fixed = _line_amount(raw, fixed)
        out.append(_Line(i, raw, fixed, canon, fuzzy, kind, prio, amount, amount_fixed))
    return out


def _amount_for(lines: list[_Line], ln: _Line) -> tuple[Decimal | None, _Line | None, bool]:
    """Amount on a labelled line, or on the next line or two when the amount was printed (or read) below it."""
    if ln.amount is not None:
        return ln.amount, ln, ln.amount_fixed
    for nxt in lines[ln.index + 1: ln.index + 3]:
        if nxt.amount is not None and nxt.kind is None and sum(c.isalpha() for c in nxt.raw.replace("USD", "")) <= 1:
            return nxt.amount, nxt, nxt.amount_fixed
        if nxt.raw:
            break
    return None, None, False


def _field(value, confident: bool, ln: _Line | None, note: str = "") -> Field:
    return Field(value=value, confidence=HIGH if confident else LOW,
                 source=ln.raw if ln else None, line_index=ln.index if ln else None, note=note)


def extract_fields(ocr_text_lines: Sequence[str] | str | OcrResult, *, known_vendors: Iterable[str] = (),
                   today: date | None = None) -> ReceiptFields:
    """Pick vendor, date, total, subtotal, tax, tip, items and payment method out of receipt text lines.

    ``known_vendors`` (e.g. the Vendors list) are preferred when one matches a top line. ``today`` bounds dates
    (no receipt dates more than a year ahead); it defaults to the current date.
    """
    ocr = ocr_text_lines if isinstance(ocr_text_lines, OcrResult) else None
    if ocr is not None:
        texts = ocr.text_lines
    elif isinstance(ocr_text_lines, str):
        texts = ocr_text_lines.splitlines()
    else:
        texts = list(ocr_text_lines)
    today = today or date.today()
    lines = _analyse(texts)
    result = ReceiptFields(lines=[ln.raw for ln in lines], ocr=ocr)
    known_vendors = list(known_vendors)

    # ---- vendor: first meaningful line near the top -------------------------------------------------------
    first_label = next((ln.index for ln in lines if ln.kind in ("subtotal", "total", "due", "tax")), len(lines))
    top = [ln for ln in lines[: min(len(lines), 10, max(first_label, 1))]]
    vendor_field = Field()
    if known_vendors:
        for ln in top[:8]:
            match = _match_known(ln.raw, known_vendors) if ln.raw else None
            if match:
                vendor_field = _field(match, True, ln, "matched the vendor list")
                break
    if not vendor_field.found:
        for pos, ln in enumerate(top):
            name = _vendor_candidate(ln.raw)
            if name:
                # OCR often misspells store names ("FRESH hARKET", "THE I-ME DEPOT") in ways rules can't see, so
                # a name is only "high" when it matches the vendor list (which learns from corrections).
                vendor_field = _field(name, False, ln, "new vendor: check the spelling" if pos <= 2 else
                                      "not on the first lines")
                break
    result.vendor = vendor_field

    # ---- date: first plausible date ----------------------------------------------------------------------
    date_hits: list[tuple[date, bool, _Line]] = []   # (date, sure, line)
    for ln in lines:
        context_skip = _DATE_IGNORE_CONTEXT.search(ln.raw)
        for value, unusual in _dates_in(ln.raw, today):
            date_hits.append((value, not unusual and not context_skip, ln))
        if not _dates_in(ln.raw, today):
            for value, unusual in _dates_in(ln.fixed, today):
                date_hits.append((value, False, ln))
    def policy_date(hit) -> bool:   # "RETURN BY 11/19/26", or a return-policy table under such a heading
        ln = hit[2]
        before = lines[ln.index - 1].raw if ln.index > 0 else ""
        return bool(_DATE_IGNORE_CONTEXT.search(ln.raw) or _DATE_IGNORE_CONTEXT.search(before))

    usable = [h for h in date_hits if not policy_date(h)] or date_hits
    if usable:
        value, sure, ln = usable[0]
        # Another date before this one, or within a week after it, makes the choice uncertain; dates well after
        # it are return-by / expiry dates.
        others = {h[0] for h in usable if h[0] != value and (h[0] < value or (h[0] - value).days <= 7)}
        note = "other dates on the receipt: " + ", ".join(sorted(d.isoformat() for d in others)) if others else ""
        result.date = _field(value, sure and not others, ln, note)

    # ---- totals block ------------------------------------------------------------------------------------
    def amounts_of(kind: str) -> list[tuple[Decimal, _Line, bool, _Line]]:
        out = []
        for ln in lines:
            if ln.kind == kind:
                value, src, fixed = _amount_for(lines, ln)
                if value is not None:
                    out.append((value, src, fixed or ln.fuzzy, ln))
        return out

    subtotals = amounts_of("subtotal")
    if subtotals:
        value, src, shaky, label = subtotals[-1]
        result.subtotal = _field(value, not shaky, src)

    taxes = amounts_of("tax")
    if taxes:
        combined = [t for t in taxes if re.search(r" (TOTAL TAX|TAX TOTAL|TAXES) ", t[3].canon)]
        if combined:
            value, src, shaky, _ = combined[-1]
            result.tax = _field(value, not shaky, src)
        else:
            distinct = []
            for t in taxes:
                if t[3].index not in [d[3].index for d in distinct]:
                    distinct.append(t)
            value = _quantize(sum((t[0] for t in distinct), Decimal("0")))
            shaky = any(t[2] for t in distinct)
            result.tax = _field(value, not shaky, distinct[0][1],
                                "sum of several tax lines" if len(distinct) > 1 else "")
            if len(distinct) > 1:
                result.tax.source = " | ".join(t[1].raw for t in distinct)

    tips = amounts_of("tip")
    if tips:
        value, src, shaky, _ = tips[-1]
        result.tip = _field(value, not shaky, src)

    discounts = [ln for ln in lines if ln.kind == "discount" and ln.amount is not None]

    total_candidates: list[tuple[int, Decimal, _Line, bool]] = []
    for kind in ("due", "total", "amount"):
        for value, src, shaky, label in amounts_of(kind):
            total_candidates.append((label.priority, value, src, shaky))
    total_field = Field()
    if total_candidates:
        nonzero = [c for c in total_candidates if c[1] != 0] or total_candidates
        level = max(c[0] for c in nonzero)
        at_level = [c for c in nonzero if c[0] == level]
        prio, value, src, shaky = max(at_level, key=lambda c: (abs(c[1]), c[2].index))
        distinct = {c[1] for c in at_level}
        sure = not shaky
        note = ""
        if len(distinct) > 1 and not tips:
            sure = False
            note = "several different totals on the receipt"
        total_field = _field(value, sure, src, note)
    else:
        cards = [ln for ln in lines if ln.kind == "tender" and ln.amount is not None
                 and not re.search(r" (CASH|TENDER|TENDERED) ", ln.canon)]
        if cards:
            ln = cards[-1]
            total_field = _field(ln.amount, False, ln, "no TOTAL line; used the card payment amount")
        elif result.subtotal.found and result.tax.found:
            total_field = Field(_quantize(result.subtotal.value + result.tax.value), LOW, None, None,
                                "no TOTAL line; subtotal + tax")
        else:
            excluded = {"change", "tender", "tip_hint", "discount", "tax", "tip"}
            amounts = [ln for ln in lines if ln.amount is not None and ln.kind not in excluded]
            if amounts:
                ln = max(amounts, key=lambda x: x.amount)
                total_field = _field(ln.amount, False, ln, "no TOTAL line; used the largest amount")
    result.total = total_field

    # Cross-check: subtotal + tax (+ tip) - discounts should equal the total.
    if result.total.found and result.subtotal.found:
        expected = result.subtotal.value + (result.tax.value or 0)
        with_tip = expected + (result.tip.value or 0)
        if result.total.value in (_quantize(expected), _quantize(with_tip)):
            if result.total.confidence == LOW and not result.total.note.startswith("no TOTAL"):
                result.total.confidence = HIGH
                result.total.note = ""
            for f in (result.subtotal, result.tax):
                if f.found and f.confidence == LOW:
                    f.confidence = HIGH
        elif not discounts and result.total.value > 0:
            result.warnings.append(
                f"subtotal {result.subtotal.value} + tax {result.tax.value or Decimal('0.00')} does not equal the "
                f"total {result.total.value}")
            result.total.confidence = LOW
            if result.tax.found:
                result.tax.confidence = LOW
    if result.total.found and result.total.confidence == HIGH:
        # A card payment that disagrees with the total is worth a look.
        cards = [ln for ln in lines if ln.kind == "tender" and ln.amount is not None
                 and not re.search(r" (CASH|TENDER|TENDERED|CHANGE) ", ln.canon) and ln.amount > 0]
        if cards and all(c.amount != result.total.value for c in cards) and not tips:
            result.warnings.append(f"card payment {cards[-1].amount} differs from the total {result.total.value}")
            result.total.confidence = LOW

    # ---- payment method ----------------------------------------------------------------------------------
    pay = Field()
    for ln in lines:
        canon = ln.canon
        for word, label in _CARD_WORDS.items():
            if f" {word} " in canon and (ln.kind == "tender" or ln.amount is not None or "*" in ln.raw
                                         or re.search(r"X{3,}|\d{4}\s*$", ln.raw.upper())
                                         or word in ("VISA", "MASTERCARD", "AMEX", "DISCOVER", "APPLE PAY",
                                                     "GOOGLE PAY", "EBT")):
                if word in ("CARD", "CHIP", "TAP", "CONTACTLESS", "CREDIT", "DEBIT") and ln.kind not in ("tender",) \
                        and not re.search(r"\*{2,}|X{3,}|\d{4}\s*$", ln.raw.upper()):
                    continue
                pay = _field("Card", ln.kind == "tender" or word in ("VISA", "MASTERCARD", "AMEX", "DISCOVER"),
                             ln, label)
                break
        if pay.found:
            break
    if not pay.found:
        for ln in lines:
            if re.search(r" CASH ", ln.canon) and ln.kind in ("tender", None) and " BACK " not in ln.canon:
                pay = _field("Cash", True, ln)
                break
            if re.search(r" (CHECK|CHEQUE) ", ln.canon) and ln.amount is not None and \
                    not re.search(r" (GUEST|NO|NUMBER) |#", ln.canon + ln.raw):
                pay = _field("Check", False, ln)
                break
    result.payment_method = pay

    # ---- items: lines ending in a price, above the totals --------------------------------------------------
    stop = next((ln.index for ln in lines if ln.kind in ("subtotal", "total", "due", "tax", "tender", "change")),
                len(lines))
    start = (result.vendor.line_index + 1) if result.vendor.line_index is not None else 0
    items: list[LineItem] = []
    for ln in lines[start:stop]:
        if ln.amount is None or ln.kind in ("tip_hint", "tip"):
            continue
        text = ln.raw if not ln.amount_fixed else ln.fixed
        matches = _money_matches(text)
        if not matches:
            continue
        m = matches[-1][1]
        tail = text[m.end():].strip()
        if tail and not re.fullmatch(r"[A-Z]{1,2}|\*|[A-Z]\s?\*|-|[A-Z]{1,2}\s\w|\(?[A-Z]\)?", tail, re.I):
            continue   # the price isn't at the end of the line
        desc = text[: m.start()].strip(" .:-*$\t")
        if _dates_in(desc, today) or _PHONE.search(desc):
            continue
        qty_only = re.fullmatch(r"(\d+(\.\d+)?\s*(LB|LBS|KG|OZ|EA|@|X|/)?\s*)*[@xX/]?\s*\$?\d*[.,]?\d*\s*(/?\s*(LB|EA|KG))?", desc, re.I)
        if (not desc or qty_only) and ln.index - 1 >= start:   # "BANANAS" / "2 @ 0.59  1.18"
            prev = lines[ln.index - 1]
            if prev.amount is None and prev.kind is None and sum(c.isalpha() for c in prev.raw) >= 2:
                desc = (prev.raw + (" " + desc if desc else "")).strip()
        if sum(c.isalpha() for c in desc) < 2:
            continue
        if ln.kind == "discount" and matches[-1][0] > 0:
            amount = -matches[-1][0]
        else:
            amount = matches[-1][0]
        confident = not ln.amount_fixed and sum(c.isalpha() for c in desc) >= 3
        items.append(LineItem(re.sub(r"\s+", " ", desc), amount, HIGH if confident else LOW, ln.raw, ln.index))
    result.items = items
    if items:
        item_sum = _quantize(sum((i.amount for i in items), Decimal("0")))
        target = result.subtotal.value if result.subtotal.found else None
        if target is None and result.total.found and not result.tax.found:
            target = result.total.value
        result.items_confidence = HIGH if target is not None and item_sum == target else LOW
        if target is not None and item_sum != target:
            result.warnings.append(f"items add up to {item_sum}, not {target}")
    return result


def read_receipt(path: str | os.PathLike, *, language: str = "en-US", known_vendors: Iterable[str] = (),
                 backend: str | None = None) -> ReceiptFields:
    """OCR a receipt file and extract its fields (the review screen shows ``needs_review()`` highlighted)."""
    ocr = ocr_image(path, language=language, backend=backend)
    return extract_fields(ocr, known_vendors=known_vendors)
