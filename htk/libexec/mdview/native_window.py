"""Optional macOS window adapter; reuse the existing document server and assets."""

import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


SOURCE = Path(__file__).with_name("native-window.swift")


def executable():
    if sys.platform != "darwin":
        raise OSError("The standalone window requires macOS; use --browser on this host")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "htk/mdview"
    identity = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:24]
    directory = cache / identity
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / "htk-mdview"
    # Separate CLI invocations may try to open different documents at once.
    with (directory / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not target.is_file():
            handle, temporary = tempfile.mkstemp(prefix=".build-", dir=directory)
            os.close(handle)
            try:
                result = subprocess.run(
                    ["/usr/bin/swiftc", str(SOURCE), "-o", temporary,
                     "-module-cache-path", str(cache / "modules")],
                    capture_output=True, text=True, timeout=60)
                if result.returncode:
                    raise OSError("Cannot build the macOS preview window with the installed Swift tools")
                os.chmod(temporary, 0o700)
                os.replace(temporary, target)
            except subprocess.TimeoutExpired:
                raise OSError("Building the macOS preview window timed out") from None
            finally:
                Path(temporary).unlink(missing_ok=True)
    return target


def open_window(url, title, *, focus=False):
    return subprocess.Popen(
        [str(executable()), url, title, *(["--focus"] if focus else [])],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
