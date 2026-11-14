#!/usr/bin/env python3
"""Show direct shortcuts and compact workspace controls in a transient popup."""

import argparse
import curses
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib


DIRECTIONS = ('h', 'j', 'k', 'l')
MODIFIERS = ('shift', 'ctrl', 'alt', 'cmd')
ALIASES = {'super': 'cmd', 'command': 'cmd', 'option': 'alt', 'control': 'ctrl'}
LAYERS = [frozenset(row) for row in (
    ('cmd',), ('shift', 'cmd'), ('alt',), ('alt', 'cmd'),
    ('shift', 'alt', 'cmd'), ('ctrl', 'alt', 'cmd'),
    ('shift', 'ctrl', 'alt', 'cmd'),
)]
SYMBOLS = {'shift': '⇧', 'ctrl': '⌃', 'alt': '⌥', 'cmd': '⌘'}
ACTION_LABELS = {
    'new_workspace': 'New workspace', 'new_tab': 'New tab',
    'cycle_pane_next': 'Next pane', 'cycle_pane_previous': 'Previous pane',
    'zoom': 'Zoom pane', 'resize_mode': 'Resize panes',
    'reload_config': 'Reload configuration', 'goto': 'Go to',
    'previous_tab': 'Previous tab', 'next_tab': 'Next tab',
    'next_workspace': 'Next workspace', 'previous_workspace': 'Previous workspace',
    'move_tab_previous': 'Move tab left', 'move_tab_next': 'Move tab right',
    **{f'{action}_{direction}': f'{label} {direction}'
       for action, label in [('focus_pane', 'Focus pane'), ('swap_pane', 'Swap pane'),
                             ('resize_pane', 'Resize pane')]
       for direction in ('left', 'down', 'up', 'right')},
}
REFERENCE_LABELS = {
    'Previous tab': ('Navigation', 'Tabs & workspaces', 'left'),
    'Next tab': ('Navigation', 'Tabs & workspaces', 'right'),
    'Previous workspace': ('Navigation', 'Tabs & workspaces', 'up'),
    'Next workspace': ('Navigation', 'Tabs & workspaces', 'down'),
    'Move tab left': ('Move', 'Tabs & workspaces', 'left'),
    'Move tab right': ('Move', 'Tabs & workspaces', 'right'),
    'Move workspace up': ('Move', 'Tabs & workspaces', 'up'),
    'Move workspace down': ('Move', 'Tabs & workspaces', 'down'),
    **{f'{action} pane {direction}': (group, 'Panes', direction)
       for action, group in [('Focus', 'Navigation'), ('Move', 'Move'),
                            ('Swap', 'Swap'), ('Create', 'Create'), ('Resize', 'Resize')]
       for direction in ('left', 'down', 'up', 'right')},
}
COLUMNS = (('left', '← Left'), ('right', '→ Right'), ('up', '↑ Up'), ('down', '↓ Down'))
STANDARD_ACTIONS = (
    'New workspace', 'New workspace with agent', 'New tab',
    'Close workspace', 'Close tab', 'Close pane',
    'Rename workspace', 'Rename tab', 'Rename pane',
    'Move pane to new tab', 'Move pane to existing tab',
    'Next pane', 'Previous pane', 'Zoom pane', 'Resize panes',
    'Toggle sidebar', 'Workspace picker', 'Go to', 'Switch tab', 'New worktree',
    'Open notification target', 'Edit scrollback', 'Copy mode',
    'Keyboard shortcuts', 'Settings', 'Help', 'Reload configuration', 'Detach',
)
DISPLAY_LABELS = {
    'New numbered workspace': 'New workspace',
    'New numbered workspace with Codex': 'New workspace with agent',
    'New workspace with Codex': 'New workspace with agent',
    'Close tab with agent confirmation': 'Close tab',
    'Close pane (blocks open agent sessions)': 'Close pane',
    'Move focused pane to new tab': 'Move pane to new tab',
    'Move focused pane to existing tab': 'Move pane to existing tab',
    'Show directional shortcuts': 'Keyboard shortcuts',
    'Show keyboard shortcuts': 'Keyboard shortcuts',
    'Create split right': 'Create pane right',
    'Create split below': 'Create pane down',
}


def clean(text):
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in str(text)).split())


def direct_chord(binding):
    """Read a direct chord; leader sequences are not direct shortcuts."""
    if not isinstance(binding, str):
        return None
    parts = [ALIASES.get(p.lower().strip(), p.lower().strip()) for p in binding.split('+')]
    if not parts or not parts[-1]:
        return None
    mods = parts[:-1]
    if len(set(mods)) != len(mods) or any(m not in MODIFIERS for m in mods):
        return None
    return frozenset(mods), parts[-1]


def chord(binding):
    parsed = direct_chord(binding)
    return parsed if parsed and parsed[1] in DIRECTIONS else None


def configured_entries(config):
    """Yield labels and keys, without reading or evaluating command bodies."""
    keys = config.get('keys', {})
    actions = [(ACTION_LABELS.get(action, action.replace('_', ' ').capitalize()), value)
               for action, value in keys.items() if action not in ('prefix', 'command')]
    actions.extend((command.get('description') or 'Custom command', command.get('key', ''))
                   for command in keys.get('command', []))
    for label, values in actions:
        for key in values if isinstance(values, list) else [values]:
            if isinstance(key, str) and clean(key):
                yield clean(label), clean(key)


def layer_order(mods):
    return (LAYERS.index(mods), 0) if mods in LAYERS else (
        len(LAYERS), sum(1 << i for i, name in enumerate(MODIFIERS) if name in mods))


def bindings(config):
    """Read configured actions only; never evaluate a shell command or invent defaults."""
    rows = {}

    def add(keys, label):
        for key in keys if isinstance(keys, list) else [keys]:
            parsed = direct_chord(key) if label in REFERENCE_LABELS else chord(key)
            if parsed:
                mods, direction = parsed
                cell = rows.setdefault(mods, {}).setdefault(direction, [])
                label = clean(label)
                if label and label not in cell:
                    cell.append(label)

    for label, key in configured_entries(config):
        add(key, label)

    return [(mods, {key: ' / '.join(labels) for key, labels in cells.items()})
            for mods, cells in sorted(rows.items(), key=lambda item: layer_order(item[0]))]


def layer_label(mods):
    return ''.join(SYMBOLS[name] for name in MODIFIERS if name in mods) or 'Unmodified'


def config_path():
    override = os.environ.get('HERDR_CONFIG_PATH')
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'herdr/config.toml'


def key_label(key):
    return {'left': '←', 'right': '→', 'up': '↑', 'down': '↓', 'tab': 'Tab',
            'enter': 'Enter', 'return': 'Enter', 'escape': 'Esc', 'esc': 'Esc',
            'space': 'Space', 'slash': '/', 'backspace': 'Backspace'}.get(key, key.upper())


def shortcut_label(binding):
    parsed = direct_chord(binding)
    if parsed:
        mods, key = parsed
        return (layer_label(mods) if mods else '') + key_label(key)
    return clean(binding)


def shortcut_order(binding):
    parsed = direct_chord(binding)
    return (layer_order(parsed[0]) if parsed else (len(LAYERS) + 1, 0),
            parsed[1] if parsed else binding)


def display_label(description):
    return DISPLAY_LABELS.get(description, description).replace('Focus pane ', 'Pane navigation ')


def leader_binding(binding):
    return any(part.strip().lower() in ('prefix', 'leader') for part in binding.split('+'))


def pair_table(entries, headings, width):
    """Wrap two columns without dropping shortcut or action text."""
    if not entries:
        return []
    if width < 24:
        return [(line, 'body') for left, right in entries
                for line in textwrap.wrap(left + '  ' + right, width)]
    left_width = min(max(len(headings[0]), *(len(left) for left, _ in entries)),
                     max(8, width // 2 - 2))
    right_width = width - left_width - 3
    result = [(headings[0].ljust(left_width) + '   ' + headings[1], 'header')]
    for left, right in entries:
        left_lines = textwrap.wrap(left, left_width)
        right_lines = textwrap.wrap(right, right_width)
        for index in range(max(len(left_lines), len(right_lines))):
            a = left_lines[index] if index < len(left_lines) else ''
            b = right_lines[index] if index < len(right_lines) else ''
            result.append(((a.ljust(left_width) + '   ' + b).rstrip(), 'body'))
    return result


def boxed(lines, width):
    if width < 5:
        return lines
    inner_width = width - 4
    return [('╭' + '─' * (width - 2) + '╮', 'border'),
            *((f'│ {text.ljust(inner_width)} │', style) for text, style in lines),
            ('╰' + '─' * (width - 2) + '╯', 'border')]


def reference(rows, width, config=None):
    """Frame directional controls first, then the ordinary shortcut list."""
    width = max(1, width)
    content_width = width - 4 if width >= 5 else width
    groups = {}
    direction_keys = {}
    for mods, cells in rows:
        for key, description in cells.items():
            meaning = REFERENCE_LABELS.get(description)
            if meaning is None:
                continue
            group, scope, direction = meaning
            direction_keys.setdefault(direction, key)
            groups.setdefault((mods, group, scope), {}).setdefault(direction, []).append((key, description))

    result = []
    covered = set()
    table = []
    for (mods, group, scope), cells in groups.items():
        # Different assignments cannot honestly share the singleton direction row.
        if any([key for key, _ in assignments] != [direction_keys[direction]]
               for direction, assignments in cells.items()):
            continue
        if scope == 'Tabs & workspaces':
            objects = []
            if set(cells) & {'left', 'right'}:
                objects.append('tabs ↔')
            if set(cells) & {'up', 'down'}:
                objects.append('workspaces ↕')
            label = f'{group} · ' + ' / '.join(objects)
        else:
            label = 'Pane navigation' if group == 'Navigation' else f'{group} panes'
        if len(cells) != 4:
            label += ' (' + ', '.join(direction for direction, _ in COLUMNS if direction in cells) + ' only)'
        table.append((mods, label))
        covered.update((mods, key, description)
                       for assignments in cells.values() for key, description in assignments)

    controls = []
    if table:
        directions = '     '.join(f'{label} {key_label(direction_keys[direction])}'
                                   for direction, label in COLUMNS if direction in direction_keys)
        controls.extend((line, 'legend') for line in textwrap.wrap(directions, content_width))
        controls.append(('', 'body'))
        levels = {mods: level for level, mods in enumerate(sorted(
            {mods for mods, _ in table}, key=layer_order), 1)}
        table.sort(key=lambda row: levels[row[0]])
        controls.extend(pair_table([(f'{levels[mods]} {layer_label(mods)}', label)
                                    for mods, label in table], ('LEVEL', 'ACTION'), content_width))

    entries = list(configured_entries(config)) if config is not None else [
        (description, '+'.join([name for name in MODIFIERS if name in mods] + [key]))
        for mods, cells in rows for key, description in cells.items()]
    normal = {label: set() for label in STANDARD_ACTIONS}
    for description, binding in entries:
        parsed = direct_chord(binding)
        if parsed and (*parsed, description) in covered:
            continue
        label = display_label(description)
        if leader_binding(binding):
            # Keep a blank action row when only a leader assignment exists.
            if description not in REFERENCE_LABELS and label not in REFERENCE_LABELS:
                normal.setdefault(label, set())
            continue
        normal.setdefault(label, set()).add(binding)
    normal_rows = []
    for label, keys in normal.items():
        shortcuts = list(dict.fromkeys(shortcut_label(key)
                                       for key in sorted(keys, key=shortcut_order)))
        normal_rows.append((' / '.join(shortcuts), label))
    if controls:
        result.extend(boxed(controls, width))
        result.append(('', 'body'))
    result.extend(boxed(pair_table(normal_rows, ('SHORTCUT', 'ACTION'), content_width), width))
    return result


def popup(screen, rows, path, config):
    curses.curs_set(0)
    screen.keypad(True)
    styles = {'header': curses.A_BOLD, 'muted': curses.A_DIM, 'body': 0,
              'legend': curses.A_BOLD, 'border': curses.A_DIM}
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        styles['header'] |= curses.color_pair(1)
        styles['legend'] |= curses.color_pair(2)
        styles['border'] |= curses.color_pair(1)
    offset = 0
    while True:
        screen.erase()
        height, width = screen.getmaxyx()

        def put(y, x, text, style=0):
            if 0 <= y < height and 0 <= x < width - 1:
                try:
                    screen.addnstr(y, x, text, width - x - 1, style)
                except curses.error:
                    pass  # A resize may race this frame.

        put(0, 2, 'KEYBOARD SHORTCUTS', styles['header'])
        put(1, 2, 'Blank = no direct custom assignment · read-only', styles['muted'])
        lines = reference(rows, max(1, width - 4), config)
        visible = max(1, height - 7)
        offset = min(offset, max(0, len(lines) - visible))
        for i, (text, style) in enumerate(lines[offset:offset + visible]):
            put(3 + i, 2, text, styles[style])
            framed = text.startswith('│ ')
            if framed:
                put(3 + i, 2, '│', styles['border'])
                put(3 + i, 2 + len(text) - 1, '│', styles['border'])
            if style == 'body' and text.strip() and curses.has_colors():
                start = 2 if framed else 0
                content = text[start:-2] if framed else text
                for shortcut in re.finditer(r'^(?:\d+ [⇧⌃⌥⌘]+|\S+)', content):
                    put(3 + i, 2 + start + shortcut.start(), shortcut.group(),
                        curses.color_pair(2) | curses.A_BOLD)
        put(height - 3, 2, 'Read from ' + str(path), styles['muted'])
        footer = 'Esc / Enter / q close'
        if len(lines) > visible:
            footer += '   j/k scroll · Space/b page'
        put(height - 2, 2, footer, styles['muted'])
        screen.refresh()
        key = screen.get_wch()
        if key in ('\x1b', '\x03', '\n', '\r', 'q', 'Q', curses.KEY_ENTER):
            return
        if key in ('j', curses.KEY_DOWN):
            offset += 1
        elif key in ('k', curses.KEY_UP):
            offset = max(0, offset - 1)
        elif key in (' ', curses.KEY_NPAGE):
            offset += visible
        elif key in ('b', curses.KEY_PPAGE):
            offset = max(0, offset - visible)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=config_path())
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--print', action='store_true', help='print the reference without opening a popup')
    mode.add_argument('--json', action='store_true', help='inspect the configured directional bindings')
    mode.add_argument('--open', action='store_true', help='open the linked Herdr plugin popup')
    args = parser.parse_args(argv)
    if args.open:
        binary = os.environ.get('HERDR_BIN_PATH') or shutil.which('herdr')
        if not binary:
            parser.error('Herdr is not on PATH')
        return subprocess.run([binary, 'plugin', 'pane', 'open', '--plugin', 'htk-vim.keymap',
                               '--entrypoint', 'grid'], check=False).returncode
    try:
        with args.config.expanduser().open('rb') as source:
            config = tomllib.load(source)
        rows = bindings(config)
        if args.json:
            print(json.dumps([{'layer': layer_label(mods), 'modifiers': sorted(mods),
                               'bindings': cells} for mods, cells in rows], ensure_ascii=False, indent=2))
        elif args.print or not sys.stdin.isatty() or not sys.stdout.isatty():
            print('\n'.join(text for text, _ in reference(rows, 106, config)))
        else:
            os.environ.setdefault('ESCDELAY', '25')
            curses.wrapper(popup, rows, args.config, config)
        return 0
    except (OSError, ValueError, curses.error) as error:
        print(f'Cannot show shortcuts: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
