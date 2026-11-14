"""Watch list data from a file or a cancelable, nonoverlapping command."""

from dataclasses import dataclass, replace
from datetime import datetime
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time

from list_model import Document, clean, parse_document

MAX_OUTPUT = 4 * 1024 * 1024
MAX_ERROR = 64 * 1024


class Cancelled(Exception):
    pass


def stop_process(process):
    """The provider and its ordinary child commands share a disposable group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    # Also reap child commands that outlived their provider or ignored SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def fetch(command, timeout, cancelled):
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, (output, MAX_OUTPUT))
            selector.register(process.stderr, selectors.EVENT_READ, (errors, MAX_ERROR))
            while selector.get_map() or process.poll() is None:
                if cancelled.is_set():
                    raise Cancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"provider timed out after {timeout:g}s")
                for key, _ in selector.select(min(0.05, remaining)):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    target, limit = key.data
                    target.extend(data)
                    if len(target) > limit:
                        raise ValueError("provider output exceeded its size limit")
            if process.returncode:
                detail = clean(errors.decode("utf-8", errors="replace")).strip()[:500]
                raise RuntimeError(f"provider exited {process.returncode}" + (f": {detail}" if detail else ""))
        return parse_document(output)
    finally:
        stop_process(process)
        process.stdout.close()
        process.stderr.close()


def read_file(path):
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError(f"not a regular list file: {path}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_OUTPUT + 1)
    if len(raw) > MAX_OUTPUT:
        raise ValueError("list file exceeded its size limit")
    return parse_document(raw)


@dataclass(frozen=True)
class Snapshot:
    document: Document | None = None
    updated_at: datetime | None = None
    error: str | None = None


class Watcher:
    def __init__(self, command=None, refresh=10, timeout=25, *, file=None):
        if bool(command) == (file is not None):
            raise ValueError("supply a command or a file")
        self.command, self.file = list(command or []), file
        self.refresh, self.timeout = refresh, timeout
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.state = Snapshot()
        self.thread = threading.Thread(target=self.run, name="list-source", daemon=True)

    def snapshot(self):
        with self.lock:
            return self.state

    def update(self, **changes):
        with self.lock:
            self.state = replace(self.state, **changes)

    def start(self):
        self.thread.start()

    def run(self):
        while not self.cancelled.is_set():
            started = time.monotonic()
            try:
                document = (read_file(self.file) if self.file is not None else
                            fetch(self.command, self.timeout, self.cancelled))
            except Cancelled:
                break
            except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                self.update(error=clean(str(error)))
            else:
                self.update(document=document, updated_at=datetime.now().astimezone(), error=None)
            self.cancelled.wait(max(0, self.refresh - (time.monotonic() - started)))

    def close(self):
        self.cancelled.set()
        if self.thread.ident is not None:
            self.thread.join()
