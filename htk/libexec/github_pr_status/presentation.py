"""Shared compact PR lists, independent of their API or authentication.

Adapters supply plain records with origin, repo, pr_num, html_url, title, branch,
state (open/closed), phase, wip (draft), assignees (logins), updated_at (epoch),
and optional build_summary (text, list-view tone). Adapters normalize their
service's phases before presentation, for example in_review or unassigned.
History records are already normalized and ordered by their event time.
"""
import re


def identity(pr):
    return (pr.get("origin"), pr.get("repo"), pr.get("pr_num"))


def agent_id(pr):
    title = pr.get("title", "")
    labels = re.findall(r"\[([A-Z][0-9]+)\]", title)
    if labels:
        return ",".join(dict.fromkeys(labels))
    prefix = re.match(r"^([A-Z][0-9]+):\s", title)
    if prefix:
        return prefix.group(1)
    branch = re.search(r"(?:^|/)([A-Z][0-9]+)(?:-|$)", pr.get("branch", ""))
    return branch.group(1) if branch else "—"


def status(pr):
    if pr.get("phase") == "merged" or pr.get("merged_at"):
        return "Merged", "success"
    if pr.get("state") == "closed":
        return "Closed", "muted"
    if pr.get("wip") is True:
        return "Draft", "warning" if pr.get("assignees") else "muted"
    phase = pr.get("phase") or "unknown"
    text = {"in_review": "In review",
            "approved": "Approved", "merge_queue": "Merge queue", "merging": "Merging",
            "merge_failed": "Merge failed", "merge_aborted": "Merge aborted"}.get(phase, phase.replace("_", " ").capitalize())
    return text, {"approved": "success", "merge_failed": "error", "merge_aborted": "warning"}.get(phase, "normal")


def row(pr):
    state, state_tone = status(pr)
    build_text, build_tone = pr.get("build_summary", ("", "muted"))
    label = agent_id(pr)
    number = str(pr.get("pr_num", "?"))
    identifier = {"text": number if label == "—" else label}
    if pr.get("html_url"):
        identifier["href"] = pr["html_url"]
    return {"cells": {
        "pr": identifier, "title": pr.get("title", ""),
        "reviewers": {"text": ", ".join(pr.get("assignees") or []),
                      "tone": "warning" if pr.get("wip") and pr.get("assignees") else "normal"},
        "status": {"text": state, "tone": state_tone},
        "build": {"text": build_text, "tone": build_tone},
    }}


def make_list(current, merged, closed, user, footer):
    opened = {identity(pr): pr for pr in current["open"] if pr.get("state") == "open"}
    assigned = {identity(pr): pr for pr in current["reviews"] if pr.get("state") == "open"}
    assigned.update({key: pr for key, pr in opened.items() if user in (pr.get("assignees") or [])})

    def order(pr):
        label = status(pr)[0]
        ranks = {"In review": 0, "Approved": 1, "Merge queue": 2, "Merging": 3,
                 "Draft": 5, "Unassigned": 6}
        rank = 4 if label == "Draft" and pr.get("assignees") else ranks.get(label, 7)
        return rank, label, -(pr.get("updated_at") or 0), identity(pr)

    return {"version": 1, "title": "PR STATUS", "columns": [
        {"key": "pr", "label": "PR", "width": 7, "min_width": 7},
        {"key": "title", "label": "Title", "width": 48, "min_width": 16, "flex": 1},
        {"key": "reviewers", "label": "Assigned", "width": 11, "min_width": 8},
        {"key": "status", "label": "Status", "width": 15, "min_width": 10},
        {"key": "build", "label": "Build", "width": 8, "min_width": 6},
    ], "sections": [
        {"title": "MY OPEN PRS", "rows": [row(pr) for pr in sorted(opened.values(), key=order)]},
        {"title": "ASSIGNED TO ME", "rows": [row(pr) for pr in sorted(assigned.values(), key=order)]},
        {"title": "MERGED", "show_count": False, "rows": [row(pr) for pr in merged]},
        {"title": "CLOSED", "show_count": False, "rows": [row(pr) for pr in closed]},
    ], "footer": footer}
