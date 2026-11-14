#!/usr/bin/env python3
"""Move the active Herdr workspace one group up or down (Herdr 0.9.5+).

The managed toolkit runtime supplies Python. Herdr shell commands supply
HERDR_SOCKET_PATH and HERDR_ACTIVE_WORKSPACE_ID. The latter identifies the
invoking workspace; HERDR_WORKSPACE_ID can refer to the server's launching pane.

Run `herdr-move-workspace up|down`; bind it to any preferred shortcut.

Linked worktrees move with their primary workspace. Movement stops at either
end. The public workspace.move_block API preserves focus and persists order.
Use --dry-run to inspect a planned move without changing the session. There is
no mutation retry: a lost response does not prove that a move was not applied.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import sys
import uuid


def request(socket_path, method, params):
    """Exchange one newline-delimited JSON request with the selected server."""
    request_id = "workspace-reorder:" + uuid.uuid4().hex
    payload = {"id": request_id, "method": method, "params": params}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(socket_path)
        connection.sendall(json.dumps(payload).encode() + b"\n")
        with connection.makefile("rb") as reader:
            line = reader.readline(2 * 1024 * 1024)
    if not line.endswith(b"\n"):
        raise RuntimeError("Herdr returned an incomplete or oversized response")
    response = json.loads(line)
    if response.get("id") != request_id:
        raise RuntimeError("Herdr returned an unexpected response ID")
    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"{error['code']}: {error['message']}")
    return response["result"]


def move_plan(workspaces, workspace_id, direction):
    """Return workspace.move_block parameters, or None at the list boundary."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be up or down")
    selected = next(
        (workspace for workspace in workspaces
         if workspace["workspace_id"] == workspace_id), None
    )
    if selected is None:
        raise ValueError(f"workspace {workspace_id!r} is no longer present")

    roots = [workspace for workspace in workspaces
             if not (workspace.get("worktree") or {}).get("is_linked_worktree")]
    groups = []
    for root in roots:
        members = [root["workspace_id"]]
        worktree = root.get("worktree")
        if worktree:
            members.extend(
                workspace["workspace_id"] for workspace in workspaces
                if (workspace.get("worktree") or {}).get("is_linked_worktree")
                and workspace["worktree"]["repo_key"] == worktree["repo_key"]
            )
        groups.append(members)

    index = next((i for i, group in enumerate(groups)
                  if workspace_id in group), None)
    if index is None:
        raise ValueError("the linked worktree's primary workspace is missing")
    destination = index + (-1 if direction == "up" else 1)
    if not 0 <= destination < len(groups):
        return None
    # Down means insert before the following group's successor, or append.
    before_index = destination if direction == "up" else destination + 1
    return {
        "workspace_ids": groups[index],
        "before_workspace_id": (
            groups[before_index][0] if before_index < len(groups) else None
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("direction", choices=("up", "down"))
    parser.add_argument("--socket", default=os.environ.get("HERDR_SOCKET_PATH"),
                        help="server socket (default: HERDR_SOCKET_PATH)")
    parser.add_argument("--workspace", default=os.environ.get("HERDR_ACTIVE_WORKSPACE_ID"),
                        help="workspace ID (default: active command context or server focus)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.socket:
        parser.error("HERDR_SOCKET_PATH or --socket is required")

    # Serialize repeated keypresses before reading order and computing a move.
    lock_path = Path(args.socket).with_name(Path(args.socket).name + ".workspace-reorder.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = request(args.socket, "workspace.list", {})
            workspaces = result["workspaces"]
            workspace_id = args.workspace or next(
                (workspace["workspace_id"] for workspace in workspaces
                 if workspace.get("focused")), None
            )
            if not workspace_id:
                raise ValueError("there is no active workspace")
            params = move_plan(workspaces, workspace_id, args.direction)
            if args.dry_run:
                print(json.dumps({"method": "workspace.move_block", "params": params}))
            elif params is not None:
                request(args.socket, "workspace.move_block", params)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"move-workspace: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
