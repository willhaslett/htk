"""Read the newest persisted snapshot table, without changing workspace state."""
import html
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit


ENTRY = re.compile(r"^\s*(?:\*\*\[(?:Workspace|Project) (?:status|snapshot)\b|#{2,6}\s+(?:\d{4}-\d{2}-\d{2}\b|.*\b(?:status|snapshot)\b))", re.I)
LINK = re.compile(r"(?<!!)\[([^\]\n]+)\]\((<[^>]+>|(?:[^()\n]|\([^()\n]*\))+)\)")
AUTOLINK = re.compile(r"<(https?://[^\s<>]+)>")
URL = re.compile(r"https?://[^\s<>]+")
HEADERS = {
    # Keep the legacy field key and accepted headers for saved reports.
    "work stream": "thread", "thread": "thread", "work": "thread", "act": "act",
    "current state": "state", "current position": "state", "status": "state",
    "next action or dependency": "next", "next action": "next", "next steps": "next",
    "next condition or action": "next",
}


def web_url(value):
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and parsed.port != 0
    except ValueError:
        return False


def cell_links(markdown):
    """Small inline conversion for snapshot cells, not a Markdown renderer."""
    links = []

    def remember(label, target):
        if not any(existing == target for _, existing in links):
            links.append((label, target))

    def linked(match):
        label, target = match.groups()
        target = html.unescape(target.strip())
        target = target[1:-1] if target.startswith("<") and target.endswith(">") else target.split(' "', 1)[0]
        if web_url(target):
            remember(label, target)
            return label
        # Local evidence paths remain readable even though OSC8 supports web links only.
        return label + " (" + target + ")" if target and not target.startswith("#") else label

    text = LINK.sub(linked, markdown)
    text = AUTOLINK.sub(lambda match: (remember(match.group(1), match.group(1)) or match.group(1)), text)
    text = re.sub(r"<br\s*/?>", " · ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"\\([\\|*_\[\]])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Also retain bare URLs alongside Markdown links, without mistaking a link's
    # display label or local-path text for another clickable destination.
    remaining = AUTOLINK.sub("", LINK.sub("", markdown))
    remaining = re.sub(r"<[^>]+>", "", remaining)
    for raw in URL.findall(remaining):
        target = html.unescape(raw.rstrip(".,;"))
        if web_url(target):
            remember(target, target)
    result = {"text": text}
    if len(links) == 1:
        result["href"] = links[0][1]
    return result, links


def cell(markdown):
    return cell_links(markdown)[0]


def visible_lines(text):
    """Ignore fenced examples when finding entry headings and table rows."""
    result, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            result.append("")
        else:
            result.append("" if fence else line)
    return result


def split_row(line):
    """Split a table row while retaining escaped and inline-code pipes."""
    line = line.strip()
    if "|" not in line:
        return None
    fields, current, code, index = [], [], 0, 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            current.extend(line[index:index + 2])
            index += 2
            continue
        if char == "`":
            end = index
            while end < len(line) and line[end] == "`":
                end += 1
            size = end - index
            code = 0 if size == code else size if not code else code
            current.append(line[index:end])
            index = end
            continue
        if char == "|" and not code:
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    fields.append("".join(current).strip())
    if line.startswith("|"):
        fields = fields[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        fields = fields[:-1]
    return fields


def newest_table(text):
    lines = visible_lines(text)
    entries = [index for index, line in enumerate(lines) if ENTRY.match(line)]
    start = entries[0] if entries else 0
    end = entries[1] if len(entries) > 1 else len(lines)
    heading = cell(lines[start])["text"] if entries else "Latest status"
    # The entry's linked timestamp points at its own archive, not cell evidence.
    heading = re.sub(r"\s+\([^)]*\)$", "", heading).lstrip("# ")
    for index in range(start, end - 1):
        headers = split_row(lines[index])
        separator = split_row(lines[index + 1])
        if not headers or not separator or len(headers) != len(separator):
            continue
        if not all(re.fullmatch(r":?-{3,}:?", part.replace(" ", "")) for part in separator):
            continue
        keys = [HEADERS.get(cell(label)["text"].casefold()) for label in headers]
        if "thread" not in keys or "state" not in keys:
            continue
        if len([key for key in keys if key]) != len(set(key for key in keys if key)):
            raise ValueError("Snapshot table has duplicate columns")
        rows = []
        for number in range(index + 2, end):
            values = split_row(lines[number])
            if values is None:
                break
            if len(values) != len(keys):
                raise ValueError(f"Snapshot table row {number + 1} has {len(values)} cells; expected {len(keys)}")
            cells = {"thread": {"text": ""}, "act": {"text": ""}, "state": {"text": ""}, "next": {"text": ""}}
            supplements = []
            for key, value in zip(keys, values):
                if key:
                    cells[key], links = cell_links(value)
                    if key == "thread" and cells[key]["text"].startswith("↳ "):
                        cells[key]["text"] = "  " + cells[key]["text"]
                    if len(links) > 1:
                        for label, target in links:
                            linked_cells = {name: {"text": ""} for name in cells}
                            linked_cells[key] = {"text": "  ↳ " + cell(label)["text"], "href": target}
                            supplements.append({"cells": linked_cells})
            if cells["act"]["text"]:
                cells["act"]["tone"] = "warning"
            rows.append({"cells": cells})
            rows.extend(supplements)
        return heading, rows
    raise ValueError("No snapshot work stream table found in the newest entry")


def document(path, title=None):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    heading, rows = newest_table(text)
    first_heading = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), None)
    return {"version": 1, "title": title or (cell(first_heading)["text"] if first_heading else "WORKSPACE STATUS"),
            "columns": [
                {"key": "act", "label": "Act", "width": 3, "min_width": 3, "align": "center"},
                {"key": "thread", "label": "Work stream", "width": 30, "min_width": 12, "flex": 1, "wrap": True},
                {"key": "state", "label": "Current state", "width": 40, "min_width": 14, "flex": 1, "wrap": True},
                {"key": "next", "label": "Next action or dependency", "width": 48, "min_width": 18, "flex": 2, "wrap": True},
            ], "sections": [{"title": heading, "show_count": False, "rows": rows}],
            "footer": str(path)}
