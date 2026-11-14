"""Render list data from a watched JSON file, an in-memory value or a command."""

import argparse
from pathlib import Path
import curses
from dataclasses import dataclass, field, replace
from datetime import datetime
import math
import os
import sys
import shutil
import signal
import subprocess
import termios
import threading
import time

from list_model import Cell, Row, Section, clip, fit, parse_document, text_width
from list_watcher import Snapshot, Watcher, fetch, read_file
import selection_style as focus
import item_shortcuts as shortcuts

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cli_support import ArgumentParser

LINK_END = "\x1b]8;;\x1b\\"
LINK_STYLES = {"normal": "0;4;36", "muted": "0;4;2", "info": "0;4;36", "success": "0;4;32",
               "warning": "0;4;33", "error": "0;4;31"}
INTERVAL_SLOT_WIDTH = len("every 999s")  # Align ordinary intervals across separate views.


@dataclass(frozen=True)
class LinkTarget:
    key: tuple
    start: int
    stop: int
    row: Row | None = None


def row_key(section, row, occurrences, now=None):
    href = next((cell.href for cell in row.cells if cell.href and cell.display_text(now).strip()), None)
    if href:
        identity = (section.title, href)
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        return (*identity, occurrence)


@dataclass
class LinkSelection:
    key: tuple | None = None
    targets: list = field(default_factory=list)
    ensure_visible: bool = False
    path: list = field(default_factory=list)
    select_first: bool = False

    def view(self, document):
        """Resolve the open path against fresh data, retaining child order from its source."""
        for depth, key in enumerate(self.path):
            occurrences, entries = {}, []
            if document:
                for section in document.sections:
                    for row in section.rows:
                        identity = row_key(section, row, occurrences)
                        if identity:
                            entries.append((identity, row))
            match = next((entry for entry in entries if entry[0] == key), None)
            if match is None:
                match = next((entry for entry in entries if entry[0][1] == key[1]), None)
            if match is None or match[1].children is None:
                del self.path[depth:]
                self.key = None
                self.ensure_visible = True
                break
            identity, row = match
            self.path[depth] = identity
            link_index = next(index for index, cell in enumerate(row.cells) if cell.href == identity[1])
            parent_link = Row(tuple(Cell(row.link_label, href=identity[1]) if index == link_index else Cell("")
                                    for index in range(len(row.cells))))
            children = tuple(replace(child, cells=(replace(child.cells[0], text="  " + child.cells[0].text),
                                                   *child.cells[1:])) for child in row.children)
            document = replace(document, sections=(Section(row.cells[link_index].text.strip(),
                                                           (parent_link, *children), False),),
                               columns=(replace(document.columns[0], label="Item"), *document.columns[1:]),
                               empty_state=None, empty_state_centered=False)
        return document

    def enter(self):
        selected = next((target for target in self.targets if target.key == self.key), None)
        if selected is None:
            return None
        if selected.row is not None and selected.row.children is not None:
            self.path.append(selected.key)
            self.key = None
            self.select_first = self.ensure_visible = True
            return None
        return self.href

    def back(self):
        if not self.path:
            return False
        self.key = self.path.pop()
        self.select_first = False
        self.ensure_visible = True
        return True

    def update(self, targets):
        self.targets = targets
        selected = next((target for target in targets if target.key == self.key), None)
        if selected is None and self.key:
            # A PR may move between current, merged and closed sections.
            selected = next((target for target in targets if target.key[1] == self.key[1]), None)
        if self.select_first and targets:
            selected = targets[0]
            self.select_first = False
        self.key = selected.key if selected else None
        return selected

    def move(self, direction):
        if not self.targets:
            return
        index = next((index for index, target in enumerate(self.targets) if target.key == self.key), None)
        if index is None:
            index = 0 if direction > 0 else len(self.targets) - 1
        else:
            index = (index + direction) % len(self.targets)
        self.key = self.targets[index].key
        self.ensure_visible = True

    @property
    def href(self):
        return self.key[1] if self.key else None


def column_widths(document, available, now=None):
    """Keep one shared column grid; compress flexible/wide columns first."""
    count = len(document.columns)
    gap = 2 if available >= count * 3 else 1
    budget = max(0, available - gap * (count - 1))
    widths = []
    for index, column in enumerate(document.columns):
        natural = max([text_width(column.label)] + [text_width(row.cells[index].display_text(now))
                      for section in document.sections for row in section.rows])
        widths.append(max(column.min_width, column.width or min(48, natural)))
    while sum(widths) > budget:
        choices = [index for index, column in enumerate(document.columns)
                   if widths[index] > column.min_width]
        if choices:
            index = max(choices, key=lambda index: (bool(document.columns[index].flex),
                                                    widths[index] - document.columns[index].min_width))
        else:
            # A small pane can be narrower than the advertised minimum widths.
            choices = [index for index, width in enumerate(widths) if width > 0]
            index = max(choices, key=lambda index: (widths[index], index))
        widths[index] -= 1
    flexible = [index for index, column in enumerate(document.columns) if column.flex]
    remaining = budget - sum(widths)
    if remaining and flexible:
        weight = sum(document.columns[index].flex for index in flexible)
        for index in flexible:
            extra = remaining * document.columns[index].flex // weight
            widths[index] += extra
        widths[flexible[0]] += budget - sum(widths)
    return widths, gap


def wrap_text(text, width):
    """Wrap ordinary display text by terminal-cell width, including wide glyphs."""
    if width <= 0:
        return [""]
    if text.startswith("  ↳ ") and width >= 6:
        parts = wrap_text(text[4:], width - 4)
        return ["  ↳ " + parts[0], *("    " + part for part in parts[1:])]
    lines = []
    while text_width(text) > width:
        chunk = clip(text, width)
        if not chunk:
            # A single wide glyph cannot fit in a one-cell column.
            lines.append("…")
            text = text[1:]
            continue
        split = chunk.rfind(" ")
        count = split if split > 0 else len(chunk)
        lines.append(text[:count].rstrip())
        text = text[count:].lstrip()
    lines.append(text)
    return lines


def has_links(document):
    return bool(document and any(cell.href and cell.display_text().strip()
                                for section in document.sections for row in section.rows for cell in row.cells))


def table_lines(document, available, targets=None, *, now=None, numbered=False, top_level=True):
    if document.empty_state:
        state = document.empty_state
        return [], [[(clip(state.text, available), state.tone, None)]]
    now = time.time() if now is None else now
    gutter = min(shortcuts.WIDTH, available) if numbered else 0
    widths, gap = column_widths(document, available - gutter, now)

    def line(cells):
        segments = []
        for index, ((text, tone, href), width) in enumerate(zip(cells, widths)):
            if index:
                segments.append((" " * gap, "normal", None))
            segments.append((fit(text, width, document.columns[index].align), tone, href))
        return segments

    header = line([(column.label, "heading", None) for column in document.columns])
    if gutter:
        header.insert(0, (" " * gutter, "normal", None))
    body = []
    occurrences = {}
    item_index = 0
    for section in document.sections:
        if body:
            body.append([])
        heading = section.title + (f" ({len(section.rows)})" if section.show_count else "")
        if heading:
            heading_line = [(clip(heading, available - gutter), "heading", None)]
            if gutter:
                heading_line.insert(0, (" " * gutter, "normal", None))
            body.append(heading_line)
        if section.rows:
            for row in section.rows:
                start = len(body)
                identity = row_key(section, row, occurrences, now) if numbered or targets is not None else None
                index = item_index if identity else None
                if identity:
                    item_index += 1
                parts = [wrap_text(cell.display_text(now), width) if column.wrap else [cell.display_text(now)]
                         for cell, column, width in zip(row.cells, document.columns, widths)]
                for number in range(max(map(len, parts))):
                    segments = line([(parts[index][number] if number < len(parts[index]) else "", cell.tone, cell.href)
                                     for index, cell in enumerate(row.cells)])
                    if gutter:
                        prefix = shortcuts.prefix(index if number == 0 else None, top_level=top_level)
                        segments.insert(0, (clip(prefix, gutter), "muted", None))
                    body.append(segments)
                if targets is not None and identity:
                    targets.append(LinkTarget(identity, start, len(body), row))
        else:
            body.append([("  (none)", "muted", None)])
    if not body:
        body = [[("No items", "muted", None)]]
    return header, body


def plain(document, width=140):
    if document.empty_state and document.empty_state_centered:
        return document.empty_state.text
    header, body = table_lines(document, max(1, width))
    lines = [document.title, ""]
    if header:
        lines.append("".join(text for text, _, _ in header).rstrip())
    lines.extend("".join(text for text, _, _ in line).rstrip() for line in body)
    if document.footer:
        lines += ["", document.footer]
    return "\n".join(lines)


def status_text(snapshot, refresh):
    if snapshot.error:
        last = ("last good " + snapshot.updated_at.strftime("%H:%M:%S")
                if snapshot.updated_at else "no successful update")
        return f"STALE · {last} · {snapshot.error}", "warning"
    if snapshot.updated_at:
        message = "Updated " + snapshot.updated_at.strftime("%H:%M:%S")
        if refresh is not None:
            interval = f"every {refresh:g}s"
            message += " · " + interval.ljust(INTERVAL_SLOT_WIDTH)
        return message, "muted"
    return "Loading…", "muted"


def write_hyperlinks(stream, links, colors):
    """Overlay terminal links after curses, preserving its cursor and attributes.

    The text was already clipped and drawn by curses. Padding stays outside the
    hyperlink; escape sequences are never passed through curses.addstr.
    """
    if not links or stream is None:
        return
    parts = ["\x1b7", LINK_END]
    for y, x, text, tone, href in links:
        style = LINK_STYLES[tone] if ("link" if tone == "normal" else tone) in colors else "0;4"
        parts.append(f"\x1b[{y + 1};{x + 1}H\x1b[{style}m\x1b]8;;{href}\x1b\\{text}{LINK_END}")
    parts.extend(("\x1b[0m", LINK_END, "\x1b8"))
    stream.write("".join(parts))
    stream.flush()


def draw(screen, snapshot, offset=0, colors=None, refresh=10, link_output=None, framed=True,
         redraw_table=True, selection=None, *, clear_screen=None, now=None, numbered=False):
    colors = colors or {}
    height, width = screen.getmaxyx()
    # curses does not track OSC8 attributes. A physical clear before changed
    # frames removes old click targets after refresh, scroll, resize or removal.
    if redraw_table and clear_screen is not False:
        screen.clear()
    links = []

    def put(y, x, text, tone="normal", limit=None, href=None):
        if not 0 <= y < height or not 0 <= x < width:
            return
        text = clip(text, min(width - x, limit if limit is not None else width))
        if text:
            label = text.strip()
            leading = text[:len(text) - len(text.lstrip())]
            link_x = x + text_width(leading)
            try:
                if href and label:
                    if leading:
                        screen.addstr(y, x, leading, colors.get(tone, 0))
                    style = colors.get("link" if tone == "normal" else tone, 0) | curses.A_UNDERLINE
                    screen.addstr(y, link_x, label, style)
                    padding = text[len(leading) + len(label):]
                    if padding:
                        screen.addstr(y, link_x + text_width(label), padding, colors.get(tone, 0))
                else:
                    screen.addstr(y, x, text, colors.get(tone, 0))
            except curses.error:
                pass  # Writing the bottom-right cell can report cursor overflow.
            if href and label:
                links.append((y, link_x, label, tone, href))

    document = selection.view(snapshot.document) if selection is not None else snapshot.document
    empty_state = document.empty_state if document else None
    if empty_state and document.empty_state_centered:
        if selection is not None:
            selection.update([])
            selection.ensure_visible = False
        # A cached empty success cannot claim the source is empty during an outage.
        label = "Data unavailable" if snapshot.error else empty_state.text
        label = clip(label, max(0, width))
        if redraw_table:
            put(max(0, (height - 1) // 2), max(0, (width - text_width(label)) // 2),
                label, "warning" if snapshot.error else empty_state.tone)
            if snapshot.error and height > 1:
                status, tone = status_text(snapshot, refresh)
                put(height - 1, 0, status, tone)
        screen.refresh()
        return 0
    if height < (7 if framed else 4) or width < 12:
        if selection is not None:
            # No visible rows means there is no keyboard activation target.
            selection.targets = []
        if empty_state:
            put(0, 0, empty_state.text, empty_state.tone)
        else:
            put(0, 0, "List · enlarge pane", "heading")
        screen.refresh()
        return 0
    left = 2 if framed else 0
    available = width - 4 if framed else width
    table_left, table_width = focus.content_span(left, available) if selection is not None else (left, available)
    header_y = 2 if framed else 0
    body_y = header_y if empty_state else header_y + 2
    footer_y = height - 2 if framed else height - 1
    page = footer_y - body_y
    status, tone = status_text(snapshot, refresh)
    if framed and redraw_table:
        put(0, 0, "╭" + "─" * (width - 2) + "╮", "heading")
        put(height - 1, 0, "╰" + "─" * (width - 2) + "╯", "heading")
        for y in range(1, height - 1):
            put(y, 0, "│", "heading")
            put(y, width - 1, "│", "heading")
        put(0, left, " " + (document.title if document else "List") + " ", "heading", available)
    if framed:
        put(1, left, status.ljust(available), tone, available)
    targets = []
    if document:
        header, body = table_lines(document, table_width, targets if selection is not None else None, now=now,
                                   numbered=numbered, top_level=not selection or not selection.path)
    else:
        header = []
        body = [[("Waiting for provider" if snapshot.error else "Loading…", "muted", None)]]
    selected = selection.update(targets) if selection is not None else None
    if selected and selection.ensure_visible:
        if selected.start < offset:
            offset = selected.start
        elif min(selected.stop, selected.start + page) > offset + page:
            offset = min(selected.stop, selected.start + page) - page
    if selection is not None:
        selection.ensure_visible = False
    offset = min(max(0, offset), max(0, len(body) - page))
    if redraw_table:
        x = table_left
        for text, tone, href in header:
            put(header_y, x, text, tone, table_left + table_width - x)
            x += text_width(text)
        if not empty_state:
            put(header_y + 1, 1 if framed else 0, "─" * (width - 2 if framed else width), "heading")
        for y, line in enumerate(body[offset:offset + page], body_y):
            x = table_left
            for text, tone, href in line:
                put(y, x, text, tone, table_left + table_width - x, href)
                x += text_width(text)
            if selected and selected.start <= offset + y - body_y < selected.stop:
                focus.draw_edges(put, y, left, available)
    footer = (("q/Ctrl-C close · Esc back · j/k select · Enter open" if selection.path else
               "q/Ctrl-C close · j/k select · Enter open") if selection is not None else
              "q/Ctrl-C close · j/k ↑/↓ scroll")
    if len(body) > page:
        footer += f" · {offset + 1}–{min(offset + page, len(body))}/{len(body)}"
    if document and document.footer:
        footer += " · " + document.footer
    if not framed:
        footer = status + " · " + footer
    put(footer_y, left, footer.ljust(available), "warning" if snapshot.error else "muted", available)
    screen.refresh()
    write_hyperlinks(link_output, links, colors)
    return offset


def move(key, offset, page, quit_keys=()):
    if key in (ord("q"), ord("Q"), 3, *quit_keys):
        return None
    if key in (ord("j"), curses.KEY_DOWN):
        return offset + 1
    if key in (ord("k"), curses.KEY_UP):
        return max(0, offset - 1)
    if key in (curses.KEY_NPAGE, ord(" ")):
        return offset + page
    if key in (curses.KEY_PPAGE, ord("b")):
        return max(0, offset - page)
    if key in (curses.KEY_HOME, ord("g")):
        return 0
    if key in (curses.KEY_END, ord("G")):
        return 10**12
    return offset


def open_url(url):
    subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_screen(screen, source, refresh, quit_keys=(), select_links=False):
    curses.raw()
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.keypad(True)
    screen.timeout(100)
    colors = {"muted": curses.A_DIM, "heading": curses.A_BOLD, focus.TONE: curses.A_BOLD}
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        for index, (tone, foreground) in enumerate((("heading", curses.COLOR_CYAN),
                ("success", curses.COLOR_GREEN), ("warning", curses.COLOR_YELLOW),
                ("error", curses.COLOR_RED), (focus.TONE, focus.ANSI_COLOR)), 1):
            curses.init_pair(index, foreground, background)
            colors[tone] = curses.color_pair(index) | (curses.A_BOLD if tone == "heading" else 0)
        colors["info"] = colors["link"] = curses.color_pair(1)
    offset = 0
    selection = LinkSelection() if select_links else None
    last_frame = None
    # Herdr already supplies a labeled pane and border around this terminal.
    framed = not bool(os.environ.get("HERDR_PANE_ID"))
    keys = shortcuts.CursesKeys()
    try:
        sys.stdout.write(shortcuts.ENABLE)
        sys.stdout.flush()
        while True:
            snapshot, size = source(), screen.getmaxyx()
            if selection is None and has_links(snapshot.document):
                selection = LinkSelection()
            document = selection.view(snapshot.document) if selection is not None else snapshot.document
            numbered = has_links(document)
            selected_key = selection.key if selection is not None else None
            path = tuple(selection.path) if selection is not None else ()
            now = time.time()
            timers = tuple(cell.display_text(now) for section in document.sections
                           for row in section.rows for cell in row.cells if cell.elapsed_since is not None
                           ) if document else ()
            table_width = max(1, size[1] - (4 if framed else 0))
            if selection is not None:
                _, table_width = focus.content_span(0, table_width)
            if numbered:
                table_width = max(1, table_width - shortcuts.WIDTH)
            layout = (column_widths(document, table_width, now)
                      if timers and size[1] >= 12 else None)
            if ((snapshot, size, offset, selected_key, timers, layout, path) != last_frame or
                    (selection is not None and selection.ensure_visible)):
                clear_screen = (last_frame is None or size != last_frame[1] or offset != last_frame[2]
                                or snapshot.document != last_frame[0].document
                                or selected_key != last_frame[3]
                                or layout != last_frame[5]
                                or path != last_frame[6]
                                or (snapshot.document is not None and snapshot.document.empty_state_centered
                                    and snapshot.error != last_frame[0].error)
                                or (selection is not None and selection.ensure_visible)
                                or (snapshot.document is None and bool(snapshot.error) != bool(last_frame[0].error)))
                redraw_table = clear_screen or timers != last_frame[4]
                if selection is not None and last_frame and (size != last_frame[1] or
                        snapshot.document != last_frame[0].document):
                    selection.ensure_visible = True
                offset = draw(screen, snapshot, offset, colors, refresh, sys.stdout, framed=framed,
                              redraw_table=redraw_table, selection=selection, clear_screen=clear_screen, now=now,
                              numbered=numbered)
                last_frame = (snapshot, size, offset, selection.key if selection is not None else None, timers, layout, path)
            for key in keys.feed(screen.getch()):
                if isinstance(key, tuple) and key[0] == "open_index":
                    if selection is not None and key[1] < len(selection.targets):
                        open_url(selection.targets[key[1]].key[1])
                elif selection is not None and selection.targets and key in (ord("j"), curses.KEY_DOWN, ord("k"), curses.KEY_UP):
                    selection.move(1 if key in (ord("j"), curses.KEY_DOWN) else -1)
                elif selection is not None and key in (10, 13, curses.KEY_ENTER):
                    url = selection.enter()
                    if url:
                        open_url(url)
                    elif selection.ensure_visible:
                        offset = 0
                elif selection is not None and key == 27 and selection.back():
                    offset = 0
                else:
                    offset = move(key, offset, max(1, screen.getmaxyx()[0] - (6 if framed else 3)), quit_keys)
                if offset is None:
                    return
    finally:
        # Close any link even if output or an external signal interrupted a frame.
        try:
            sys.stdout.write(LINK_END + shortcuts.DISABLE)
            sys.stdout.flush()
        except OSError:
            pass


def positive_seconds(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive number") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return number


def interrupted(*_args):
    raise KeyboardInterrupt


def main(argv=None, quit_keys=()):
    parser = ArgumentParser(prog=os.environ.get("HTK_CLI_PROG", "list-view"), description=__doc__)
    parser.add_argument("--refresh", type=positive_seconds, metavar="SECONDS",
                        help="polling interval (default: 1 for files, 10 for commands)")
    parser.add_argument("--timeout", type=positive_seconds, default=25, metavar="SECONDS",
                        help="provider timeout (default: 25)")
    parser.add_argument("--data", metavar="JSON", help="display fixed JSON data in memory")
    parser.add_argument("--file", metavar="PATH", help="watch a JSON data file; edits appear automatically")
    parser.add_argument("--once", action="store_true", help="print plain text and exit")
    parser.add_argument("--select-links", action="store_true",
                        help="j/k selects linked rows; Enter shows children or opens the row's first link")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARG...]")
    options = parser.parse_args(argv)
    command = options.command[1:] if options.command[:1] == ["--"] else options.command
    if sum((options.data is not None, options.file is not None, bool(command))) != 1:
        parser.error("supply exactly one of --file PATH, --data JSON or -- COMMAND [ARG...]")
    refresh = options.refresh if options.refresh is not None else (1 if options.file is not None else 10)
    watcher = None
    old_handlers = {signum: signal.signal(signum, interrupted) for signum in (signal.SIGTERM, signal.SIGHUP)}
    try:
        document = parse_document(options.data) if options.data is not None else None
        if options.once:
            document = document or (read_file(options.file) if options.file is not None else
                                    fetch(command, options.timeout, threading.Event()))
            print(plain(document, shutil.get_terminal_size((140, 24)).columns))
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error("an interactive terminal is required (or use --once)")
        if document:
            snapshot = Snapshot(document=document, updated_at=datetime.now().astimezone())
            source, refresh = lambda: snapshot, None
        else:
            watcher = Watcher(command, refresh, options.timeout, file=options.file)
            watcher.start()
            source = watcher.snapshot
        os.environ.setdefault("ESCDELAY", "25")
        previous = termios.tcgetattr(sys.stdin.fileno())
        try:
            curses.wrapper(run_screen, source, refresh, quit_keys, options.select_links)
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, previous)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, curses.error) as error:
        parser.exit(1, f"list-view: {error}\n")
    finally:
        if watcher:
            watcher.close()
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
