"""Shared terminal focus treatment, independent of curses or frame rendering.

Reserve the same span for selected and unselected content so navigation never
moves text. The terminal palette supplies yellow; these markers have no fill and
do not change the content's color, links or emphasis.
"""

TONE = "focus"
ANSI_COLOR = 3  # The terminal's yellow palette entry, also understood by curses.
SGR = str(30 + ANSI_COLOR)
LEFT = "["
RIGHT = "]"


def content_span(x, width, *, padding=1):
    """Return the content origin/width inside the reserved marker gutters."""
    width = max(0, width)
    if width < 4:
        # Compact calendar cells can fit a left bracket and a two-digit date.
        return x + min(1, width), max(0, width - 1)
    gutter = min(1 + padding, width // 2)
    return x + gutter, max(0, width - 2 * gutter)


def draw_edges(put, y, x, width):
    """Draw unlinked brackets with any renderer's put(y, x, text, tone) callback."""
    if width > 0:
        put(y, x, LEFT, TONE)
    if width >= 4:
        put(y, x + width - 1, RIGHT, TONE)
