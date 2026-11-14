"""Per-document discovery and browser-page lifetime, without browser automation."""

from contextlib import contextmanager
import fcntl
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit


class WindowBinding:
    """A live stream holds the binding; pagehide releases it on close/reload.

    An open stream never expires just because a page is hidden or its JavaScript
    timers are throttled. The grace period lets reloads reconnect to the same URL.
    """

    def __init__(self, *, grace=3, opening_timeout=30, clock=time.monotonic):
        self.grace = grace
        self.opening_timeout = opening_timeout
        self.clock = clock
        self.changed = threading.Condition()
        self.pages = {}
        self.departed = set()
        self.deadline = None
        self.ever_connected = False
        self.opening = False
        self.focus_version = 0
        self.stopped = False

    def _phase(self):
        if self.stopped or (self.deadline is not None and self.clock() >= self.deadline):
            return "expired"
        if self.pages:
            return "active"
        if self.ever_connected:
            return "closing"
        return "opening" if self.opening else "waiting"

    def claim(self, *, open_window=False, focus=False):
        with self.changed:
            phase = self._phase()
            if phase in {"expired", "closing"}:
                return phase
            if focus:
                self.focus_version += 1
                self.changed.notify_all()
            if phase == "waiting" and open_window:
                self.opening = True
                self.deadline = self.clock() + self.opening_timeout
                return "open"
            return "reuse"

    def open_failed(self):
        with self.changed:
            if not self.ever_connected:
                self.opening = False
                self.deadline = None

    def connect(self, page):
        with self.changed:
            if self._phase() == "expired" or page in self.departed:
                return None
            stream = object()
            self.pages[page] = stream
            self.ever_connected = True
            self.deadline = None
            self.changed.notify_all()
            return stream

    def disconnect(self, page, stream=None):
        with self.changed:
            if stream is not None and self.pages.get(page) is not stream:
                return  # An old connection must not release a reconnected page.
            self.pages.pop(page, None)
            if stream is None:
                self.departed.add(page)
            if not self.pages:
                self.ever_connected = True
                self.deadline = self.clock() + self.grace
            self.changed.notify_all()

    def phase(self):
        with self.changed:
            return self._phase()

    def stop(self):
        with self.changed:
            self.stopped = True
            self.changed.notify_all()


class DocumentRegistry:
    """Serialize launches of the same canonical file, including separate CLIs.

    Records only locate the server. The server's browser connection decides
    whether the document still has a window; a surviving process isn't enough.
    """

    def __init__(self, source, directory=None, *, presentation="browser"):
        self.source = source.resolve()
        self.directory = directory or Path(os.environ.get("XDG_STATE_HOME") or
                                            Path.home() / ".local/state") / "htk/mdview"
        self.key = hashlib.sha256(os.fsencode(self.source)).hexdigest()
        if presentation == "window":
            self.key += "-window"
        self.record = self.directory / f"{self.key}.json"

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(self.directory / f"{self.key}.lock", "a", opener=self._private) as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    @staticmethod
    def _private(path, flags):
        return os.open(path, flags, 0o600)

    def read(self):
        try:
            record = json.loads(self.record.read_text())
            if record["source"] == str(self.source):
                return record["url"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def write(self, url):
        temporary = self.record.with_suffix(".tmp")
        with open(temporary, "w", opener=self._private) as handle:
            json.dump({"source": str(self.source), "url": url}, handle)
        temporary.replace(self.record)

    def remove(self, url):
        with self.locked():
            if self.read() == url:
                self.record.unlink(missing_ok=True)

    def request(self, url, route, data=None):
        # Never follow a URL from a damaged/stale registry to another host.
        try:
            address = urlsplit(url)
            if (address.scheme != "http" or address.hostname != "127.0.0.1" or
                    not address.port or not re.fullmatch(r"/[A-Za-z0-9_-]+/", address.path)):
                return None
            connection = HTTPConnection("127.0.0.1", address.port, timeout=2)
            try:
                connection.request("POST" if data is not None else "GET", address.path + route,
                                   body=json.dumps(data) if data is not None else None,
                                   headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                result = json.loads(response.read(8192))
                if response.status == 200 and result.get("source") == str(self.source):
                    return result
            finally:
                connection.close()
        except (OSError, HTTPException, ValueError, TypeError, AttributeError):
            pass
        return None
