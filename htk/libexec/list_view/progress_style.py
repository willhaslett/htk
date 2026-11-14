"""Compact progress bars shared by terminal views, without terminal escapes."""

WIDTH = 6
TONE = "info"  # The terminal's cyan palette entry; no bright/bold treatment.


def bar(current=None, total=None, *, width=WIDTH):
    """Use heavy dashes for completed work and light dashes for the remainder.

    A dashed track means progress is unknown, distinct from a known zero. Values
    are clamped to the track; it fills completely only when current reaches total.
    """
    width = max(0, width)
    if current is None or total is None or total <= 0:
        return "┄" * width
    filled = max(0, min(width, int(current * width // total)))
    return "━" * filled + "─" * (width - filled)
