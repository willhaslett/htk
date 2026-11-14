"""Shared numbered-item labels and enhanced terminal Control-digit shortcuts."""

import re
import time

DIGITS = "1234567890"
WIDTH = 3  # A modifier hint, one digit and a space; stable for every row.
ENABLE = "\x1b[>1u"
DISABLE = "\x1b[<u"


def index_for_key(code, modifiers, event=1):
    """Return an absolute zero-based item index for an exact Control key press.

    `modifiers` is the Kitty modifier bitmask (wire parameter minus one).
    Legacy control bytes collide with Escape and other keys, so aren't decoded.
    """
    if modifiers == 4 and str(event) == "1" and code in map(ord, DIGITS):
        return DIGITS.index(chr(code))
    return None


def label(index, *, top_level=True):
    """The first ten items have shortcuts; item ten uses the 0 key."""
    if index is None or not 0 <= index < len(DIGITS):
        return ""
    return "⌃" + DIGITS[index]


def prefix(index, *, top_level=True):
    """A fixed-width, unlinked gutter before an item's own content."""
    return label(index, top_level=top_level).rjust(WIDTH - 1) + " "


class CursesKeys:
    """Add CSI-u input to curses while retaining its existing decoded keys.

    Feed each getch result, including idle -1 ticks. Escape waits briefly for a
    sequence, then retains its normal back action. No global bindings change.
    """

    def __init__(self):
        self.pending = ""
        self.since = 0

    def feed(self, key, now=None):
        now = time.monotonic() if now is None else now
        if key == -1:
            if self.pending == "\x1b" and now - self.since >= 0.04:
                self.pending = ""
                return [27]
            if now - self.since >= 0.2:
                self.pending = ""
            return []
        if not self.pending:
            if key == 27:
                self.pending, self.since = "\x1b", now
                return []
            return [key]
        if self.pending == "\x1b" and key != ord("["):
            self.pending = ""
            return [27, *self.feed(key, now)]
        if not 0 <= key < 128 or len(self.pending) >= 128:
            self.pending = ""
            return [key] if key >= 256 else []
        self.pending += chr(key)
        if len(self.pending) <= 2 or not "@" <= chr(key) <= "~":
            return []
        sequence, self.pending = self.pending, ""
        match = re.fullmatch(r"\x1b\[(\d+)(?::[\d:]*)?(?:;(\d+)(?::(\d+))?)?u", sequence)
        if not match:
            return []  # curses handles ordinary arrows and function keys.
        code = int(match[1])
        modifiers, event = int(match[2] or 1) - 1, int(match[3] or 1)
        index = index_for_key(code, modifiers, event)
        if index is not None:
            return [("open_index", index)]
        if event == 3:
            return []
        if code == ord("c") and modifiers == 4:
            return [3]
        if modifiers in (0, 1) and code < 128:
            return [ord(chr(code).upper()) if modifiers == 1 else code]
        return []
