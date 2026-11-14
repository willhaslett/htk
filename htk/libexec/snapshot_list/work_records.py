"""Project persisted work records or text into the shared status display."""

import re
import unicodedata
from urllib.parse import urlsplit

URL = re.compile(r"https?://[^\s<>]+")
INLINE_LINK = re.compile(r'(?<!!)\[([^\]\n]+)\]\((https?://(?:[^\s()<>]|\([^\s()<>]*\))+)(?:\s+"[^"]*")?\)')
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")
LIST_ITEM = re.compile(r"(?:[-*+] |\d+[.)] )")


def clean_text(value):
    return "".join(char for char in ANSI.sub("", value) if not unicodedata.category(char).startswith("C"))


def valid_link(value):
    if any(char.isspace() or unicodedata.category(char).startswith("C") for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in ("http", "https") and parsed.hostname and parsed.port != 0)
    except ValueError:
        return False


def extract_links(raw):
    """Retain Markdown labels and collect each distinct web target in order."""
    links = []

    def remember(label, target):
        if not any(link[1] == target for link in links):
            links.append((label, target))

    def markdown(match):
        label, target = match.groups()
        if not valid_link(target):
            return match.group(0)
        remember(clean_text(label), target)
        return clean_text(label)

    label = INLINE_LINK.sub(markdown, raw)

    def bare(match):
        original = match.group(0)
        target = original.rstrip(".,;")
        while target.endswith(")") and target.count(")") > target.count("("):
            target = target[:-1]
        if not valid_link(target):
            return original
        remember(target, target)
        return original[len(target):]

    label = URL.sub(bare, label)
    label = re.sub(r"<\s*>", "", label)
    return clean_text(label).rstrip(), links


def source_tone(raw):
    """Color only explicit source markers; work IDs are never statuses."""
    for marker in re.findall(r"\[([^\]]*)\]", raw):
        status = marker.upper().strip()
        if re.fullmatch(r"[A-Z]+\d+", status):
            continue
        if status in {"X", "DONE", "MERGED"}:
            return "muted"
        if status in {"APPROVED", "READY"} or status.startswith("READY "):
            return "success"
        if re.match(r"^(?:BLOCKED|WAITING|PAUSED|PENDING|IN PROGRESS)(?:$|[: ])", status):
            return "warning"
    return "normal"


def source_document(text, title, path):
    """Adapt source text to list-view without changing the task contents."""
    sections = []

    def section_for_rows():
        if not sections:
            sections.append({"title": "Tasks", "show_count": False, "rows": []})
        return sections[-1]

    def add_row(label, tone="normal", target=None):
        cell = {"text": label, "tone": tone}
        if target:
            cell["href"] = target
        section_for_rows()["rows"].append({"cells": {"task": cell}})

    for raw in text.expandtabs(4).splitlines():
        if not raw.strip():
            continue
        label, links = extract_links(raw)
        heading = (raw.startswith("#") or bool(re.fullmatch(r"- (?:\d{4}-\d{2}-\d{2}|Today)", label))
                   or (label and not raw.startswith(" ") and not LIST_ITEM.match(raw) and not links))
        if heading:
            heading_label = re.sub(r"^#+\s*", "", label)
            if sections and not sections[-1]["rows"]:
                sections[-1]["title"] += " / " + heading_label
            else:
                sections.append({"title": heading_label, "show_count": False, "rows": []})
            for link_label, target in links:
                add_row("  ↳ " + link_label, target=target)
            continue
        tone = source_tone(label)
        indent = " " * (len(raw) - len(raw.lstrip()))
        if len(links) == 1:
            link_label, target = links[0]
            # A bare link or a bullet containing only a link still needs a label.
            if re.fullmatch(r"\s*(?:[-*+]|\d+[.)])?\s*(?:\[[ xX]\]\s*)?", label):
                label = label + (" " if label.strip() else "") + link_label
                if not label.startswith(indent):
                    label = indent + label
            add_row(label, tone, target)
        else:
            if label.strip():
                add_row(label, tone)
            for link_label, target in links:
                add_row(indent + "  ↳ " + link_label, target=target)
    if not sections:
        section_for_rows()
    return {"version": 1, "title": title,
            "columns": [{"key": "task", "label": "Task", "min_width": 8, "flex": 1, "wrap": True}],
            "sections": sections, "footer": str(path)}


def task_document(source, title, path, hide_completed=False):
    """One compact projection; task data contains no renderer instructions."""
    finished = {"done", "completed", "canceled", "cancelled"}

    def visible(task):
        return (not hide_completed or task["state"].strip().lower() not in finished
                or any(visible(child) for child in task.get("children", [])))

    rows = []

    def add_tasks(tasks, depth=0):
        for task in tasks:
            if not visible(task):
                continue
            prefix = "  " * depth + "↳ " if depth else ""
            state = task["state"]
            row_tone = "muted" if state.strip().lower() in finished else "normal"
            cells = {"task": prefix + task["id"] + "  " + task["title"],
                     "state": {"text": state, "tone": row_tone if row_tone == "muted"
                               else source_tone("[" + state + "]")},
                     "next": task.get("next", "")}
            # Reuse the text adapter's link handling, keeping each URL available.
            links = []
            for key in ("task", "next"):
                label, targets = extract_links(cells[key])
                if len(targets) == 1:
                    link_label, target = targets[0]
                    cells[key] = {"text": label or link_label, "href": target}
                else:
                    cells[key] = label
                    links.extend((key, label, target) for label, target in targets)
            rows.append({"cells": cells, "tone": row_tone})
            for key, label, target in links:
                extra = {"task": "", "state": "", "next": ""}
                extra[key] = {"text": "  " * depth + "  ↳ " + label, "href": target}
                rows.append({"cells": extra, "tone": row_tone})
            add_tasks(task.get("children", []), depth + 1)

    add_tasks(source["tasks"])
    return {"version": 1, "title": title or source["title"],
            "columns": [
                {"key": "task", "label": "Task", "width": 32, "min_width": 18,
                 "flex": 1, "wrap": True},
                {"key": "state", "label": "State", "width": 18, "min_width": 12, "wrap": True},
                {"key": "next", "label": "Next / waiting", "min_width": 24, "flex": 3, "wrap": True}],
            "sections": [{"title": "", "show_count": False, "rows": rows}], "footer": str(path)}
