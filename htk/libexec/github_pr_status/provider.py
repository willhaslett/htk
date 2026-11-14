"""Watch authored and assigned GitHub PRs using the optional authenticated gh CLI."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import sys
from pathlib import Path
import re
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cli_support import ArgumentParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from github_pr_status import presentation


PR_FIELDS = """
      number title url state isDraft headRefName mergedAt closedAt updatedAt
      author { login } repository { nameWithOwner }
      assignees(first: 100) { nodes { login } pageInfo { hasNextPage } }
"""
SEARCH_QUERY = """query($query: String!, $cursor: String) {
  search(query: $query, type: ISSUE, first: 50, after: $cursor) {
    issueCount pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { FIELDS } }
  }
}"""
HISTORY_QUERY = SEARCH_QUERY.replace("FIELDS", PR_FIELDS)
CURRENT_QUERY = SEARCH_QUERY.replace("FIELDS", PR_FIELDS + """
      reviewDecision reviewRequests(first: 1) { totalCount }
      headRefOid commits(last: 1) {
        nodes { commit { oid statusCheckRollup { state } } }
      }
""")


def gh_page(host, query, cursor, document=HISTORY_QUERY):
    # --input keeps GraphQL text separate from the search variable named query.
    body = json.dumps({"query": document, "variables": {"query": query, "cursor": cursor}})
    result = subprocess.run(["gh", "api", "--hostname", host, "graphql", "--input", "-"],
                            input=body, capture_output=True, text=True, timeout=20)
    if result.returncode:
        # Service response bodies can contain private data or credentials.
        raise ValueError(f"GitHub request failed for {host}; check gh auth status")
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("errors"):
            raise ValueError("GitHub returned an API error")
        page = payload["data"]["search"]
        if (not isinstance(page, dict) or not isinstance(page.get("nodes"), list)
                or not isinstance(page.get("pageInfo"), dict)
                or not isinstance(page["pageInfo"].get("hasNextPage"), bool)):
            raise ValueError("GitHub returned an incomplete search response")
        return page
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("GitHub returned an incomplete response") from error


def recent_history(host, user, merged, fetch=gh_page):
    """Prove the top ten by event time, even when old PRs get new comments."""
    query = f"is:pr author:{user} is:closed {'is:merged' if merged else 'is:unmerged'} sort:updated-desc"
    event = "mergedAt" if merged else "closedAt"
    cursor = None
    cursors = set()
    found = {}
    scanned = 0
    while True:
        page = fetch(host, query, cursor)
        nodes = page.get("nodes", [])
        if not isinstance(nodes, list) or any(not isinstance(p, dict) or not p.get("updatedAt") for p in nodes):
            raise ValueError("GitHub history contains incomplete PRs")
        scanned += len(nodes)
        for pr in nodes:
            if bool(pr.get("mergedAt")) == merged and pr.get(event):
                found[pr["url"]] = pr
        ordered = sorted(found.values(), key=lambda p: (p[event], p["url"]), reverse=True)
        info = page.get("pageInfo", {})
        # Unseen PRs' event times cannot exceed their updatedAt. Keep ties.
        if len(ordered) >= 10 and nodes and min(p["updatedAt"] for p in nodes) < ordered[9][event]:
            return ordered[:10]
        if not info.get("hasNextPage"):
            if page.get("issueCount", scanned) > scanned:
                raise ValueError("GitHub search ended before exact recent history was established")
            return ordered[:10]
        next_cursor = info.get("endCursor")
        if not next_cursor or next_cursor in cursors or scanned >= 1000:
            raise ValueError("GitHub search limit reached before exact recent history was established")
        cursors.add(next_cursor)
        cursor = next_cursor


def current_prs(host, user, qualifier, fetch=None):
    fetch = fetch or (lambda h, q, c: gh_page(h, q, c, CURRENT_QUERY))
    query = f"is:pr is:open {qualifier}:{user} sort:updated-desc"
    cursor = None
    cursors = set()
    found = {}
    scanned = 0
    while True:
        page = fetch(host, query, cursor)
        nodes = page.get("nodes", [])
        if not isinstance(nodes, list) or any(not isinstance(pr, dict) or not pr.get("url") for pr in nodes):
            raise ValueError("GitHub returned incomplete current PRs")
        scanned += len(nodes)
        found.update((pr["url"], pr) for pr in nodes)
        info = page.get("pageInfo", {})
        if not info.get("hasNextPage"):
            if page.get("issueCount", scanned) > scanned:
                raise ValueError("GitHub search ended before all current PRs were read")
            return list(found.values())
        cursor = info.get("endCursor")
        if not cursor or cursor in cursors or scanned >= 1000:
            raise ValueError("GitHub search limit reached before all current PRs were read")
        cursors.add(cursor)


def build_summary(pr):
    nodes = (pr.get("commits") or {}).get("nodes") or []
    commit = nodes[-1].get("commit") if nodes else None
    rollup = (commit or {}).get("statusCheckRollup")
    if not rollup:
        return "", "muted"
    if not pr.get("headRefOid") or commit.get("oid") != pr["headRefOid"]:
        return "Unknown head", "warning"
    value = rollup.get("state") or "UNKNOWN"
    return {"SUCCESS": ("Passed", "success"), "FAILURE": ("Failed", "error"),
            "ERROR": ("Error", "error"), "PENDING": ("Pending", "warning"),
            "EXPECTED": ("Expected", "warning")}.get(value, (value.replace("_", " ").title(), "normal"))


def github_pr(pr, host, *, history=False):
    assignees = pr.get("assignees") or {}
    if assignees.get("pageInfo", {}).get("hasNextPage"):
        raise ValueError("A PR has more than 100 assignees")
    decision = pr.get("reviewDecision")
    phase = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested",
             "REVIEW_REQUIRED": "in_review"}.get(decision)
    if not phase:
        phase = "in_review" if (pr.get("reviewRequests") or {}).get("totalCount") else "open"
    if history:
        phase = "merged" if pr.get("mergedAt") else "closed"
    return {"origin": host, "repo": pr["repository"]["nameWithOwner"], "pr_num": pr["number"],
            "html_url": pr["url"], "title": pr["title"], "branch": pr["headRefName"],
            "author": (pr.get("author") or {}).get("login"),
            "state": "closed" if history else pr["state"].lower(), "phase": phase,
            "wip": pr.get("isDraft"), "merged_at": pr.get("mergedAt"),
            "assignees": [a["login"] for a in assignees.get("nodes", [])],
            "updated_at": datetime.fromisoformat(pr["updatedAt"]).timestamp() if pr.get("updatedAt") else 0,
            "build_summary": ("", "muted") if history else build_summary(pr)}


def history_pr(pr, host):
    return github_pr(pr, host, history=True)


def emit(args):
    with ThreadPoolExecutor(max_workers=4) as pool:
        opened = pool.submit(current_prs, args.hostname, args.user, "author")
        assigned = pool.submit(current_prs, args.hostname, args.user, "assignee")
        merged = pool.submit(recent_history, args.hostname, args.user, True)
        closed = pool.submit(recent_history, args.hostname, args.user, False)
        current = {"open": [github_pr(pr, args.hostname) for pr in opened.result()],
                   "reviews": [github_pr(pr, args.hostname) for pr in assigned.result()]}
        return presentation.make_list(current,
            [history_pr(pr, args.hostname) for pr in merged.result()],
            [history_pr(pr, args.hostname) for pr in closed.result()], args.user,
            f"Build = head commit check summary · GitHub: {args.hostname}")


def main(argv=None):
    parser = ArgumentParser(prog=os.environ.get("HTK_CLI_PROG", "github-pr-status"), description=__doc__)
    parser.add_argument("--user", required=True, help="GitHub login to show")
    parser.add_argument("--hostname", default="github.com", help="Authenticated gh host (default: github.com)")
    parser.add_argument("--refresh", type=float, default=10)
    parser.add_argument("--once", action="store_true", help="Print the current list and exit")
    parser.add_argument("--json", action="store_true", help="Emit one list-view JSON document and exit")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.user):
        parser.error("--user must be a login name")
    if args.json:
        try:
            print(json.dumps(emit(args), ensure_ascii=False))
            return 0
        except (OSError, ValueError, subprocess.SubprocessError, KeyError, TypeError) as error:
            print(f"PR status unavailable: {error}", file=sys.stderr)
            return 1
    renderer = Path(__file__).resolve().parents[1] / "list_view/viewer.py"
    command = [sys.executable, str(renderer), "--select-links", "--refresh", str(args.refresh), "--timeout", "60"]
    if args.once or not (sys.stdin.isatty() and sys.stdout.isatty()):
        command.append("--once")
    command += ["--", sys.executable, str(Path(__file__).resolve()), "--json", "--user", args.user,
                "--hostname", args.hostname]
    os.execv(sys.executable, command)


if __name__ == "__main__":
    raise SystemExit(main())
