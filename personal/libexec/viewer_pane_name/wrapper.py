"""Temporarily name the caller's Herdr pane while a toolkit viewer runs."""

from contextlib import contextmanager
import json
import os
import signal
import subprocess
import sys


NAMES = {"github-pr-status": "PRS", "status-list": "STATUS",
         "snapshot-list": "STATUS", "list-view": "LIST", "mdview": "MDVIEW"}


def viewer_name(command, args):
    if (os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_PANE_ID") or
            not (sys.stdin.isatty() and sys.stdout.isatty())):
        return None
    # Provider arguments after -- can contain their own --json or help flags.
    options = args[:args.index("--")] if "--" in args else args
    if any(arg in ("--help", "-h", "--once", "--json", "--workspaces", "--projects", "--dry-run") for arg in options):
        return None
    return NAMES.get(command)


def herdr(*args):
    result = subprocess.run(["herdr", *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, check=True, timeout=2)
    return json.loads(result.stdout)


@contextmanager
def pane_name(name):
    try:
        pane = herdr("pane", "current", "--current")["result"]["pane"]
        pane_id, original = pane["pane_id"], pane.get("label")
        if not isinstance(pane_id, str) or (original is not None and not isinstance(original, str)):
            raise ValueError("invalid pane metadata")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        yield
        return
    try:
        try:
            herdr("pane", "rename", pane_id, name)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass  # Naming is optional; the viewer still runs.
        yield
    finally:
        try:
            herdr("pane", "rename", pane_id, original or "--clear")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


def run(command):
    """Keep the viewer's terminal and exit status, and let it handle shutdown."""
    child = subprocess.Popen(command)

    def forward(signum, _frame):
        try:
            child.send_signal(signum)
        except ProcessLookupError:
            pass

    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        code = child.wait()
        return code if code >= 0 else 128 - code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv=None):
    command, *launch = list(sys.argv[1:] if argv is None else argv)
    # launch is [python, script, arguments...], assembled by the toolkit dispatcher.
    name = viewer_name(command, launch[2:])
    if name is None:
        os.execv(launch[0], launch)
    with pane_name(name):
        return run(launch)


if __name__ == "__main__":
    raise SystemExit(main())
