"""Shared command/toggle layout, independent of terminal and input handling.

Consumers map semantic tones to their palette, paint the spans, and dispatch
actions from the targets. No ANSI escapes, callbacks or application state live
here. Focus gutters stay reserved so focusing a control never moves its text.
"""

from dataclasses import dataclass
from typing import Literal

from list_model import clean, clip, text_width
import selection_style as focus_style

BINDING_WIDTH = 8
MAX_MODIFIERS = 3
MODIFIERS = "⌘⇧⌥⌃"
# Eight binding columns, a space, ten label/indicator columns, two focus edges.
MIN_CELL_WIDTH = BINDING_WIDTH + 1 + 10 + 2


@dataclass(frozen=True)
class Control:
    action: str
    label: str
    binding: str = ""
    kind: Literal["command", "toggle"] = "command"
    active: bool = False
    enabled: bool = True
    starts_row: bool = False

    def __post_init__(self):
        if self.kind not in ("command", "toggle"):
            raise ValueError("Control kind must be command or toggle")
        binding = clean(self.binding)
        if len(binding) - len(binding.lstrip(MODIFIERS)) > MAX_MODIFIERS:
            raise ValueError("Control binding supports at most three modifier symbols")


@dataclass(frozen=True)
class Span:
    x: int
    y: int
    text: str
    tone: str


@dataclass(frozen=True)
class Target:
    x: int
    y: int
    width: int
    action: str


@dataclass(frozen=True)
class Cell:
    x: int
    y: int
    width: int
    control: Control


@dataclass(frozen=True)
class Layout:
    height: int
    spans: tuple[Span, ...]
    targets: tuple[Target, ...]
    cells: tuple[Cell, ...] = ()


def _cells(controls, width):
    """Keep column boundaries stable, including a partially filled final row."""
    controls = tuple(controls)
    largest_group = group_size = 0
    for control in controls:
        if control.starts_row:
            largest_group, group_size = max(largest_group, group_size), 0
        group_size += 1
    columns = min(max(1, largest_group, group_size), max(1, width // MIN_CELL_WIDTH))
    cells = []
    row = column = 0
    for control in controls:
        if column == columns or control.starts_row and column:
            row, column = row + 1, 0
        x = column * width // columns
        cells.append(Cell(x, row, (column + 1) * width // columns - x, control))
        column += 1
    return tuple(cells)


def _label(text, width):
    text = clean(text)
    if text_width(text) > width:
        return clip(text, max(0, width - 1)) + ("…" if width else "")
    return text


def _binding(text, width):
    text = clean(text)
    modifiers = len(text) - len(text.lstrip(MODIFIERS))
    if modifiers and modifiers < len(text):
        text = text[:modifiers] + " " + text[modifiers:].lstrip()
    return clip(text, width)


def _render_cell(cell, focused):
    control = cell.control
    spans = []
    body_x, body_width = focus_style.content_span(cell.x, cell.width, padding=0)
    tone = ("disabled" if not control.enabled else
            "muted" if control.kind == "toggle" and not control.active else "normal")
    # If even the fixed binding field cannot fit, retain a usable label/action.
    binding_width = BINDING_WIDTH if body_width >= BINDING_WIDTH + 2 else 0
    if binding_width:
        binding = _binding(control.binding, binding_width)
        if binding:
            spans.append(Span(body_x + binding_width - text_width(binding), cell.y, binding, tone))
    label_x = body_x + (binding_width + 1 if binding_width else 0)
    label_width = body_width - (label_x - body_x)
    indicator = min(2, label_width) if control.kind == "toggle" else 0
    if indicator:
        spans.append(Span(label_x, cell.y, "●", "success" if control.active else "muted"))
    label = _label(control.label, label_width - indicator)
    if label:
        spans.append(Span(label_x + indicator, cell.y, label, tone))
    visible = bool(label or indicator)
    if control.action == focused:
        focus_style.draw_edges(lambda row, column, text, tone: spans.append(Span(column, row, text, tone)),
                               cell.y, cell.x, cell.width)
    target = Target(cell.x, cell.y, cell.width, control.action) if control.enabled and visible else None
    return spans, target


def layout(controls, width, *, focused=None):
    """Lay controls out row-first in as many equal-width columns as fit.

    Each cell has an eight-column right-aligned binding followed by a left-aligned
    label. Toggle dots lead that label column; commands have no dot. Narrow bars
    use fewer columns. Bindings and labels clip within their own fields, and
    focus does not change geometry. Unavailable controls have no click target.
    A control with starts_row=True begins a new row using the same grid columns.
    """
    if width <= 0:
        return Layout(0, (), ())
    cells = _cells(controls, width)
    spans, targets = [], []
    for cell in cells:
        content, target = _render_cell(cell, focused)
        spans.extend(content)
        if target:
            targets.append(target)
    return Layout(cells[-1].y + 1 if cells else 0, tuple(spans), tuple(targets), cells)
