"""Check GitHub for a newer TimeBooks release and download its Setup.exe.

A release is a GitHub Release on REPO tagged ``vX.Y.Z`` with ``TimeBooks-Setup.exe`` attached. The app
compares the tag with ``__version__``; installing is done by the downloaded Setup.exe (``--update``), which
waits for the app to close, replaces the program files (workbooks, PIN and settings are kept) and restarts it.
Nothing about the user or their data is sent: the only request is an anonymous read of the public release list.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

REPO = "Debauchery1994/Timebooks"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
SETUP_ASSET = "TimeBooks-Setup.exe"
TIMEOUT = 15
_CHUNK = 64 * 1024
# Release downloads start on github.com and redirect to GitHub's asset storage.
_DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}


class UpdateError(Exception):
    """Friendly, user-facing reason an update check or download failed."""


@dataclass
class ReleaseInfo:
    version: str          # "1.2.0" (tag without the leading "v")
    name: str             # release title
    notes: str            # release description (Markdown text)
    page_url: str         # release page in the browser
    setup_url: str        # TimeBooks-Setup.exe download, "" if the release has no installer attached
    setup_size: int = 0
    sha256: str = ""      # from GitHub's asset digest when available


def parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.2.3' / '1.2' -> (1, 2, 3) / (1, 2, 0); None for anything else (e.g. '1.2-beta')."""
    m = re.fullmatch(r"\s*[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?\s*", text or "")
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def _get(url: str, opener: Callable, accept: str):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "TimeBooks-Updater"})
    return opener(req, timeout=TIMEOUT)


def fetch_latest(opener: Callable = urllib.request.urlopen) -> ReleaseInfo:
    """The newest published (non-draft, non-prerelease) release. Raises UpdateError."""
    try:
        with _get(API_URL, opener, "application/vnd.github+json") as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No published releases were found on GitHub yet.") from exc
        if exc.code in (403, 429):
            raise UpdateError("GitHub is limiting update checks right now. Try again in an hour.") from exc
        raise UpdateError(f"GitHub returned an error ({exc.code}). Try again later.") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError("Could not reach GitHub. Check the internet connection and try again.") from exc
    except ValueError as exc:
        raise UpdateError("GitHub sent a response that could not be read. Try again later.") from exc

    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        raise UpdateError(f"The latest release has an unexpected version tag ({tag or 'none'}).")
    info = ReleaseInfo(version=tag.lstrip("vV").strip(), name=str(data.get("name") or tag),
                       notes=str(data.get("body") or "").strip(),
                       page_url=str(data.get("html_url") or RELEASES_PAGE), setup_url="")
    for asset in data.get("assets") or []:
        if str(asset.get("name", "")).lower() == SETUP_ASSET.lower():
            info.setup_url = str(asset.get("browser_download_url") or "")
            info.setup_size = int(asset.get("size") or 0)
            digest = str(asset.get("digest") or "")
            if digest.lower().startswith("sha256:"):
                info.sha256 = digest[7:].lower()
            break
    return info


def check(current: str, opener: Callable = urllib.request.urlopen) -> ReleaseInfo | None:
    """The latest release if it is newer than `current`, else None. Raises UpdateError."""
    info = fetch_latest(opener)
    return info if is_newer(info.version, current) else None


def _allowed_download(url: str) -> bool:
    u = urlparse(url)
    return (u.scheme == "https" and u.hostname == "github.com"
            and u.path.startswith(f"/{REPO}/releases/download/"))


def download(info: ReleaseInfo, dest_dir: str | Path, progress: Callable[[int, int], None] | None = None,
             opener: Callable = urllib.request.urlopen) -> Path:
    """Download the release's Setup.exe into dest_dir and verify its size and checksum. Raises UpdateError."""
    if not info.setup_url:
        raise UpdateError(f"Version {info.version} has no installer attached. Download it from the "
                          f"releases page instead.")
    if not _allowed_download(info.setup_url):
        raise UpdateError("The installer link does not point to this app's GitHub releases, so it was not "
                          "downloaded.")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"TimeBooks-Setup-{info.version}.exe"
    part = out.with_suffix(".part")
    sha = hashlib.sha256()
    done = 0
    try:
        with _get(info.setup_url, opener, "application/octet-stream") as resp:
            host = urlparse(resp.geturl()).hostname if hasattr(resp, "geturl") else "github.com"
            if host not in _DOWNLOAD_HOSTS:
                raise UpdateError("The download was redirected somewhere unexpected, so it was stopped.")
            total = info.setup_size or int(resp.headers.get("Content-Length") or 0)
            with open(part, "wb") as f:
                while chunk := resp.read(_CHUNK):
                    f.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise UpdateError("The download did not finish. Check the internet connection and try again.") from exc
    if info.setup_size and done != info.setup_size:
        part.unlink(missing_ok=True)
        raise UpdateError("The download was incomplete. Try again.")
    if info.sha256 and sha.hexdigest() != info.sha256:
        part.unlink(missing_ok=True)
        raise UpdateError("The downloaded installer failed its integrity check, so it was not run.")
    part.replace(out)
    return out
