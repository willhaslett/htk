#!/usr/bin/env python3
"""Move the focused Herdr pane to a new or existing tab, preserving its session.

The shortcut supplies HERDR_ACTIVE_PANE_ID at invocation. Session snapshots
provide display order, which need not match the tabs' internal numbers.
This helper uses only Python's standard library and the installed Herdr CLI.
"""

import argparse
import curses
import json
import os
import shutil
import subprocess
import sys


def invoke(*args):
    executable = os.environ.get('HERDR_BIN_PATH') or shutil.which('herdr')
    if not executable:
        raise RuntimeError('Herdr is not on PATH')
    try:
        result = subprocess.run([executable, *args], capture_output=True,
                                text=True, timeout=15)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('Herdr timed out; inspect the tabs before retrying') from error
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Herdr failed')
    response = json.loads(result.stdout)
    if 'error' in response:
        raise RuntimeError(str(response['error']))
    return response['result']


def context(snapshot, pane_id):
    pane = next((p for p in snapshot['panes'] if p['pane_id'] == pane_id), None)
    if pane is None:
        raise ValueError('The source pane is no longer present')
    tabs = [t for t in snapshot['tabs'] if t['workspace_id'] == pane['workspace_id']]
    if not any(t['tab_id'] == pane['tab_id'] for t in tabs):
        raise ValueError('The source tab is no longer present')
    return pane, tabs


def move_to_tab(call, original, destination=None, focus=True, new_label='SESSION'):
    snapshot = call('api', 'snapshot')['snapshot']
    pane, tabs = context(snapshot, original['pane_id'])
    if any(pane[key] != original[key] for key in ('terminal_id', 'workspace_id', 'tab_id')):
        raise ValueError('The source pane changed while the picker was open; reopen it')
    if destination == pane['tab_id']:
        return
    if destination is not None and not any(t['tab_id'] == destination for t in tabs):
        raise ValueError('The destination tab is no longer in this workspace; reopen the picker')
    for layout in snapshot['layouts']:
        if layout['tab_id'] in (pane['tab_id'], destination) and layout.get('zoomed'):
            raise ValueError('Unzoom the source and destination tabs before moving')
    # One native move; no staging, process restart or mutation retry.
    target = (('--tab', destination, '--split', 'right') if destination is not None
              else ('--new-tab', '--workspace', pane['workspace_id'], '--label', new_label))
    return call('pane', 'move', pane['pane_id'], *target,
                '--focus' if focus else '--no-focus')


def label(text):
    return ''.join(c if c.isprintable() else ' ' for c in str(text))


def tab_label(index, tab, source):
    suffix = '  (CURRENT)' if tab['tab_id'] == source['tab_id'] else ''
    return f"{index + 1:>2}  {label(tab.get('label') or 'UNTITLED')}{suffix}"


def picker(screen, tabs, source):
    curses.curs_set(0)
    screen.keypad(True)
    accent = curses.A_BOLD
    muted = curses.A_DIM
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_YELLOW, -1)
        curses.init_pair(2, 147 if curses.COLORS >= 256 else curses.COLOR_CYAN, -1)
        accent = curses.color_pair(1) | curses.A_BOLD
        muted = curses.color_pair(2)
    selected = next((i for i, tab in enumerate(tabs)
                     if tab['tab_id'] != source['tab_id']), 0)
    digits = ''
    message = ''
    top = 0
    while True:
        screen.erase()
        height, width = screen.getmaxyx()

        def put(y, text, style=0):
            if 0 <= y < height and width > 2:
                try:
                    screen.addnstr(y, 1, text, width - 2, style)
                except curses.error:
                    pass  # A terminal resize can race this frame.

        put(0, 'MOVE PANE TO TAB', accent)
        put(1, label(source.get('label') or source['pane_id']), muted)
        visible = max(1, height - 7)
        top = max(0, min(top, selected))
        if selected >= top + visible:
            top = selected - visible + 1
        for i in range(top, min(len(tabs), top + visible)):
            style = curses.A_REVERSE | accent if i == selected else 0
            if tabs[i]['tab_id'] == source['tab_id']:
                style |= curses.A_DIM
            put(3 + i - top, tab_label(i, tabs[i], source), style)
        put(height - 3, message or 'Numbers follow tabs from left to right.', muted)
        put(height - 2, f'Tab number: {digits}' if digits else
            'Number or j/k / arrows   Enter move   Esc cancel', muted)
        screen.refresh()
        key = screen.get_wch()
        if key in ('\x1b', '\x03'):
            return None
        if key == curses.KEY_RESIZE:
            continue
        if key in ('\n', '\r', curses.KEY_ENTER):
            if digits:
                number = int(digits)
                if not 1 <= number <= len(tabs):
                    message = f'Choose a number from 1 to {len(tabs)}.'
                    continue
                selected = number - 1
            if tabs[selected]['tab_id'] == source['tab_id']:
                message = 'Choose another tab.'
                continue
            return tabs[selected]['tab_id']
        if isinstance(key, str) and key in '0123456789':
            digits = (digits + key)[:len(str(len(tabs))) + 1]
            number = int(digits)
            if 1 <= number <= len(tabs):
                selected = number - 1
            message = ''
        elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
            digits = digits[:-1]
            message = ''
        elif key in ('j', curses.KEY_DOWN, 'k', curses.KEY_UP):
            selected = (selected + (1 if key in ('j', curses.KEY_DOWN) else -1)) % len(tabs)
            digits = message = ''
        elif key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
            selected = max(0, min(len(tabs) - 1, selected +
                                 (visible if key == curses.KEY_NPAGE else -visible)))
            digits = message = ''


def show_message(text):
    print(text, flush=True)
    if sys.stdin.isatty():
        try:
            input('\nPress Enter to return: ')
        except (EOFError, KeyboardInterrupt):
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pane', default=os.environ.get('HERDR_ACTIVE_PANE_ID'))
    parser.add_argument('--list', action='store_true', help='print choices without opening a picker')
    destination_options = parser.add_mutually_exclusive_group()
    destination_options.add_argument('--tab', help='move directly to a destination tab ID')
    destination_options.add_argument('--new-tab', action='store_true', help='extract into a new tab in this workspace')
    parser.add_argument('--label', default='SESSION', help='new tab label (with --new-tab)')
    parser.add_argument('--no-focus', action='store_true')
    args = parser.parse_args(argv)
    if not args.pane:
        parser.error('--pane or HERDR_ACTIVE_PANE_ID is required')
    try:
        snapshot = invoke('api', 'snapshot')['snapshot']
        source, tabs = context(snapshot, args.pane)
        if args.list:
            for i, tab in enumerate(tabs):
                print(tab_label(i, tab, source))
            return 0
        if args.new_tab:
            move_to_tab(invoke, source, focus=not args.no_focus, new_label=args.label)
            return 0
        if args.tab:
            destination = args.tab
        elif len(tabs) < 2:
            show_message('There are no other tabs in this workspace.')
            return 0
        else:
            destination = curses.wrapper(picker, tabs, source)
        if destination is not None:
            move_to_tab(invoke, source, destination, focus=not args.no_focus)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, curses.error) as error:
        show_message(f'Move pane: {error}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
