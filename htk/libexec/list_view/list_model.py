"""The small, provider-independent list-view document format."""

from dataclasses import dataclass
import json
import math
import re
import time
import unicodedata
from urllib.parse import urlsplit

TONES = frozenset({"normal", "muted", "info", "success", "warning", "error"})
ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")


def clean(text):
    """Provider text is data, never terminal instructions."""
    return "".join(" " if char.isspace() else char for char in ANSI.sub("", text)
                   if char.isspace() or not unicodedata.category(char).startswith("C"))


def cell_width(char):
    return 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WF" else 1)


def text_width(text):
    return sum(cell_width(char) for char in text)


def clip(text, width):
    result, used = [], 0
    for char in clean(text):
        size = cell_width(char)
        if used + size > width:
            break
        if size or result:
            result.append(char)
            used += size
    return "".join(result)


def fit(text, width, align="left"):
    text = clean(text)
    if text_width(text) > width:
        text = clip(text, max(0, width - 1)) + ("…" if width else "")
    padding = max(0, width - text_width(text))
    left = padding // 2 if align == "center" else padding if align == "right" else 0
    return " " * left + text + " " * (padding - left)


def duration_text(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes}m {seconds:02d}s"


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    width: int | None = None
    min_width: int = 3
    flex: int = 0
    wrap: bool = False
    align: str = "left"


@dataclass(frozen=True)
class Cell:
    text: str
    tone: str = "normal"
    href: str | None = None
    elapsed_since: float | None = None

    def display_text(self, now=None):
        if self.elapsed_since is None:
            return self.text
        return duration_text((time.time() if now is None else now) - self.elapsed_since)


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    children: tuple["Row", ...] | None = None
    link_label: str = "Open in browser"


@dataclass(frozen=True)
class Section:
    title: str
    rows: tuple[Row, ...]
    show_count: bool = True


@dataclass(frozen=True)
class Document:
    title: str
    columns: tuple[Column, ...]
    sections: tuple[Section, ...]
    footer: str = ""
    empty_state: Cell | None = None
    empty_state_centered: bool = False


def fields(value, required, optional, location):
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    missing = set(required) - value.keys()
    unknown = value.keys() - set(required) - set(optional)
    if missing:
        raise ValueError(f"{location} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{location} unknown fields: {', '.join(sorted(unknown))}")


def string(value, location, nonempty=False):
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise ValueError(f"{location} must be {'a nonempty' if nonempty else 'a'} string")
    return clean(value)


def tone(value, location):
    if not isinstance(value, str) or value not in TONES:
        raise ValueError(f"{location} must be one of {', '.join(sorted(TONES))}")
    return value


def array(value, location):
    if not isinstance(value, list):
        raise ValueError(f"{location} must be an array")
    return value


def href(value, location):
    """Only absolute web URLs can become terminal hyperlink targets."""
    if not isinstance(value, str) or any(char.isspace() or unicodedata.category(char).startswith("C")
                                         for char in value):
        raise ValueError(f"{location} must be an HTTP(S) URL without whitespace or controls")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and parsed.port != 0
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{location} must be an absolute HTTP(S) URL")
    return value


def parse_document(raw):
    """Validate once at the boundary, then retain an immutable document."""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(f"invalid JSON: {error}") from None
    if isinstance(value, list) or (isinstance(value, dict) and "rows" in value):
        value = expand_rows(value)
    fields(value, ("version", "title", "columns", "sections"), ("footer", "empty_state"), "document")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("document.version must be 1")
    columns, keys = [], set()
    for index, item in enumerate(array(value["columns"], "columns")):
        where = f"columns[{index}]"
        fields(item, ("key", "label"), ("width", "min_width", "flex", "wrap", "align"), where)
        key = string(item["key"], where + ".key", True)
        if key != item["key"] or key in keys:
            raise ValueError(f"{where}.key must be unique and contain no terminal controls")
        keys.add(key)
        options = {}
        for option in ("width", "min_width", "flex"):
            if option in item:
                number = item[option]
                if type(number) is not int or not 1 <= number <= 10000:
                    raise ValueError(f"{where}.{option} must be an integer from 1 to 10000")
                options[option] = number
        if item.get("width", 10000) < item.get("min_width", 3):
            raise ValueError(f"{where}.width must be at least min_width")
        if "wrap" in item:
            if type(item["wrap"]) is not bool:
                raise ValueError(f"{where}.wrap must be a boolean")
            options["wrap"] = item["wrap"]
        if "align" in item:
            if item["align"] not in ("left", "center", "right"):
                raise ValueError(f"{where}.align must be left, center or right")
            options["align"] = item["align"]
        columns.append(Column(key, string(item["label"], where + ".label"), **options))
    if not columns or len(columns) > 32:
        raise ValueError("columns must contain between 1 and 32 columns")
    def parse_rows(items, where, depth=0):
        if depth > 12:
            raise ValueError("row children may nest at most 12 levels")
        rows = []
        for row_index, row in enumerate(array(items, where)):
            row_where = f"{where}[{row_index}]"
            fields(row, ("cells",), ("tone", "children", "link_label"), row_where)
            fields(row["cells"], keys, (), row_where + ".cells")
            row_tone = tone(row.get("tone", "normal"), row_where + ".tone")
            cells = []
            for column in columns:
                cell = row["cells"][column.key]
                cell_where = row_where + ".cells." + column.key
                if isinstance(cell, str):
                    cells.append(Cell(clean(cell), row_tone))
                else:
                    fields(cell, ("text",), ("tone", "href", "elapsed_since"), cell_where)
                    elapsed = cell.get("elapsed_since")
                    if "elapsed_since" in cell and (type(elapsed) not in (int, float)
                            or not math.isfinite(elapsed) or elapsed < 0):
                        raise ValueError(f"{cell_where}.elapsed_since must be a finite nonnegative Unix timestamp")
                    cells.append(Cell(string(cell["text"], cell_where + ".text"),
                                      tone(cell.get("tone", row_tone), cell_where + ".tone"),
                                      href(cell["href"], cell_where + ".href") if "href" in cell else None,
                                      elapsed))
            children = None
            if "children" in row:
                if not any(cell.href and cell.text.strip() for cell in cells):
                    raise ValueError(f"{row_where}.children requires a visible parent link")
                children = parse_rows(row["children"], row_where + ".children", depth + 1)
            link_label = string(row.get("link_label", "Open in browser"), row_where + ".link_label", True)
            if not link_label.strip():
                raise ValueError(f"{row_where}.link_label must contain visible text")
            rows.append(Row(tuple(cells), children, link_label))
        return tuple(rows)

    sections = []
    for index, item in enumerate(array(value["sections"], "sections")):
        where = f"sections[{index}]"
        fields(item, ("title", "rows"), ("show_count",), where)
        show_count = item.get("show_count", True)
        if type(show_count) is not bool:
            raise ValueError(f"{where}.show_count must be a boolean")
        rows = parse_rows(item["rows"], where + ".rows")
        sections.append(Section(string(item["title"], where + ".title"), tuple(rows), show_count))
    empty_state = None
    centered = False
    if "empty_state" in value:
        state = value["empty_state"]
        fields(state, ("text",), ("tone", "center"), "empty_state")
        centered = state.get("center", False)
        if type(centered) is not bool:
            raise ValueError("empty_state.center must be a boolean")
        empty_state = Cell(string(state["text"], "empty_state.text", True),
                           tone(state.get("tone", "normal"), "empty_state.tone"))
        if not empty_state.text.strip():
            raise ValueError("empty_state.text must contain visible text")
        if any(section.rows for section in sections):
            raise ValueError("empty_state requires sections with no rows")
    return Document(string(value["title"], "title", True), tuple(columns), tuple(sections),
                    string(value.get("footer", ""), "footer"), empty_state, centered)


def expand_rows(value):
    """Simple data needs no version, column declarations, sections or cell wrappers."""
    if isinstance(value, list):
        value = {"rows": value}
    fields(value, ("rows",), ("title", "footer"), "document")
    rows = array(value["rows"], "rows")
    if all(isinstance(row, str) for row in rows):
        rows = [{"Item": row} for row in rows]
    elif not all(isinstance(row, dict) and row for row in rows):
        raise ValueError("rows must contain only strings or only nonempty objects")
    keys = list(dict.fromkeys(key for row in rows for key in row)) or ["Item"]
    if len(keys) > 32:
        raise ValueError("columns must contain between 1 and 32 columns")
    return {"version": 1, "title": value.get("title", "List"),
            "columns": [{"key": key, "label": key, "flex": 1, "wrap": True} for key in keys],
            "sections": [{"title": "", "show_count": False,
                          "rows": [{"cells": {key: row.get(key, "") for key in keys}} for row in rows]}],
            "footer": value.get("footer", "")}
