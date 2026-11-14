#!/usr/bin/env python3
"""Create a numbered Herdr workspace with one MANAGER pane.

Requires Herdr 0.9.5+. Optionally start Codex with --codex.
Herdr custom commands supply HERDR_BIN_PATH, HERDR_SOCKET_PATH, and
HERDR_ACTIVE_PANE_CWD. Use active context, never inherited HERDR_PANE_ID.
Codex starts with its normal settings and native startup dialogs intact.
On failure, keep the new workspace for inspection; do not retry creation.
Retry only an explicit agent_pane_busy rejection for up to 15 seconds while
the new shell initializes. Pin retries to the created terminal, stop if an
agent appears, and never retry an ambiguous timeout or a submitted launch.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


class HerdrError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message}")


def invoke(herdr, *args):
    result = subprocess.run(
        [herdr, *args], capture_output=True, text=True, timeout=45
    )
    if result.returncode:
        for output in (result.stderr, result.stdout):
            try:
                error = json.loads(output)["error"]
                raise HerdrError(error["code"], error["message"])
            except (ValueError, KeyError, TypeError):
                continue
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()
                           or f"Herdr exited with status {result.returncode}")
    return json.loads(result.stdout)["result"] if result.stdout.strip() else {}


def next_workspace_name(workspaces):
    if not isinstance(workspaces, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("label"), str)
            for item in workspaces):
        raise ValueError("workspace list must contain objects with string labels")
    numbers = [int(item["label"]) for item in workspaces if re.fullmatch(r"[0-9]+", item["label"])]
    return f"{max(numbers, default=0) + 1:02d}"


def create_workspace(herdr, socket, cwd=None, focus=True):
    # Shell shortcuts run concurrently. Serialize allocation against the same
    # server; no persistent counter and no lock held while Codex starts.
    with os.fdopen(os.open(socket + ".workspace-create.lock", os.O_CREAT | os.O_RDWR, 0o600), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        name = next_workspace_name(invoke(herdr, "workspace", "list")["workspaces"])
        args = ["workspace", "create", "--label", name, "--no-focus"]
        if cwd:
            args.extend(["--cwd", cwd])
        created = invoke(herdr, *args)
        workspace_id = created["workspace"]["workspace_id"]
        print(f"Created {name} ({workspace_id})", file=sys.stderr, flush=True)
        invoke(herdr, "tab", "rename", created["tab"]["tab_id"], "MANAGER")
        manager = created["root_pane"]
        invoke(herdr, "pane", "rename", manager["pane_id"], "MANAGER")
        if focus:
            invoke(herdr, "workspace", "focus", workspace_id)
        return {**created, "manager_pane": manager, "shell_panes": []}


def start_codex(herdr, pane, name, shell_timeout=15):
    pane_id = pane["pane_id"]
    terminal_id = pane["terminal_id"]
    deadline = time.monotonic() + shell_timeout
    retry = False
    while True:
        if retry:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"new shell in {pane_id} was not available within "
                    f"{shell_timeout} seconds; workspace kept for inspection"
                )
            current = invoke(herdr, "pane", "get", pane_id)["pane"]
            if current["terminal_id"] != terminal_id or current.get("agent"):
                raise RuntimeError(f"pane {pane_id} changed or already has an agent; stopping")
        try:
            return invoke(herdr, "agent", "start", name,
                          "--kind", "codex", "--pane", pane_id)
        except HerdrError as error:
            if error.code != "agent_pane_busy":
                raise
            # Herdr rejects busy panes before registering an agent or sending
            # any input. Only this definite rejection is safe to retry.
            if not retry:
                print(f"Waiting for the new shell in {pane_id}", file=sys.stderr, flush=True)
            retry = True
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", action="store_true", help="start Codex in the new MANAGER pane")
    parser.add_argument("--no-focus", action="store_true", help="leave the current workspace focused")
    parser.add_argument("--cwd", default=os.environ.get("HERDR_ACTIVE_PANE_CWD"),
                        help="initial directory (default: the shortcut's active pane directory)")
    args = parser.parse_args(argv)
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    socket = os.environ.get("HERDR_SOCKET_PATH")
    if not socket:
        parser.error("HERDR_SOCKET_PATH is required (supplied by Herdr shell shortcuts)")
    socket = os.environ["HERDR_SOCKET_PATH"] = str(Path(socket).expanduser().absolute())
    created = create_workspace(herdr, socket, args.cwd, focus=not args.no_focus)
    result = {"workspace_id": created["workspace"]["workspace_id"],
              "label": created["workspace"]["label"], "tab_id": created["tab"]["tab_id"],
              "pane_id": created["manager_pane"]["pane_id"],
              "shell_pane_ids": [pane["pane_id"] for pane in created["shell_panes"]]}
    if args.codex:
        name = "codex-" + uuid.uuid4().hex[:12]
        print(f"Starting {name} in {result['pane_id']}", file=sys.stderr, flush=True)
        result["agent"] = start_codex(herdr, created["manager_pane"], name)
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError,
            subprocess.TimeoutExpired) as error:
        print(f"new-herdr-workspace: {error}", file=sys.stderr)
        sys.exit(1)
