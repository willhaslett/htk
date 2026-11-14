"""Give the current Codex session a workspace-and-timestamp name."""

import argparse
from pathlib import Path
from datetime import datetime
import json
import os
import sys
import queue
import subprocess
import threading
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cli_support import ArgumentParser

def session_name(workspace, now=None):
    workspace = workspace.strip()
    if not workspace or any(ord(c) < 32 for c in workspace):
        raise ValueError("Supply a nonempty workspace name without control characters.")
    stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d%H%M%S")
    return f"{workspace}-{stamp}"


def current_workspace():
    if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_WORKSPACE_ID"):
        raise ValueError("Outside Herdr, supply the workspace name with --workspace NAME.")
    result = subprocess.run(
        [os.environ.get("HERDR_BIN_PATH") or "herdr", "workspace", "get", os.environ["HERDR_WORKSPACE_ID"]],
        text=True, capture_output=True, timeout=10,
    )
    if result.returncode:
        raise ValueError("Could not read the calling Herdr workspace: " + result.stderr.strip())
    try:
        label = json.loads(result.stdout)["result"]["workspace"]["label"]
        if not isinstance(label, str) or not label.strip():
            raise ValueError("The calling Herdr workspace has no name; supply --workspace NAME.")
        return label
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Herdr returned no workspace label; supply --workspace NAME.") from error


def rename_thread(thread_id, name, *, command=("codex", "app-server", "--stdio"), timeout=10):
    """Use the supported metadata API; do not start or resume an agent turn."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, bufsize=1)
    messages = queue.Queue()

    def read_messages():
        try:
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_messages, daemon=True)
    reader.start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def request(request_id, method, params):
        send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise ValueError(f"Codex {method} timed out; no retry was made.") from error
            if message is None:
                raise ValueError("Codex app-server closed the connection.")
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise ValueError(f"Codex {method}: {message['error'].get('message', message['error'])}")
            return message.get("result")

    try:
        request(1, "initialize", {"clientInfo": {"name": "daily_toolkit_session_name", "version": "0.1.0"}})
        send({"method": "initialized", "params": {}})
        request(2, "thread/name/set", {"threadId": thread_id, "name": name})
        result = request(3, "thread/read", {"threadId": thread_id, "includeTurns": False})
        if not isinstance(result, dict) or result.get("thread", {}).get("name") != name:
            raise ValueError("The rename was acknowledged, but its saved name could not be verified.")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()


def main():
    parser = ArgumentParser(prog=os.environ.get("HTK_CLI_PROG", "name-session"), description=__doc__)
    parser.add_argument("--workspace", help="Workspace name; defaults to the calling Herdr workspace")
    parser.add_argument("--dry-run", action="store_true", help="Print the proposed name without renaming")
    args = parser.parse_args()
    try:
        name = session_name(args.workspace if args.workspace is not None else current_workspace())
        if not args.dry_run:
            thread_id = os.environ.get("CODEX_THREAD_ID")
            if not thread_id:
                raise ValueError("Run inside the Codex session to name it (CODEX_THREAD_ID is missing).")
            rename_thread(thread_id, name)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
