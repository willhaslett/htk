#!/usr/bin/env python3
"""Directional pane creation/placement and confirmed pane close for Herdr 0.9.5.

Keyboard commands use HERDR_ACTIVE_PANE_ID, never the server's inherited pane.
Moves preserve terminals, staging through a temporary tab because Herdr cannot
move within one tab. Left/up placement uses a final native position swap.
No terminal is closed during a move. A failed move leaves its terminal alive;
inspect PANE MOVE TEMP before retrying after a lost response.
Pane close refuses any detected agent or session, independent of its status.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import socket
import sys
import uuid


def request(socket_path, method, params):
    request_id = 'pane-control:' + uuid.uuid4().hex
    payload = {'id': request_id, 'method': method, 'params': params}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(socket_path)
        connection.sendall(json.dumps(payload).encode() + b'\n')
        with connection.makefile('rb') as reader:
            line = reader.readline(2 * 1024 * 1024)
    if not line.endswith(b'\n'):
        raise RuntimeError('Incomplete or oversized Herdr response; inspect layout before retrying')
    response = json.loads(line)
    if response.get('id') != request_id:
        raise RuntimeError('Unexpected Herdr response ID')
    if 'error' in response:
        raise RuntimeError(str(response['error']))
    return response['result']


def split_axis(direction):
    return 'right' if direction in ('left', 'right') else 'down'


def perpendicular_target(layout, pane_id, direction):
    """Choose a nearby perpendicular pane to allow reorienting a split.

    Already at the requested edge of a simple row/column: no-op. A stacked
    pair can become side by side, and a side-by-side pair can become stacked.
    """
    source = next(p['rect'] for p in layout['panes'] if p['pane_id'] == pane_id)
    axis, size, cross, cross_size = (
        ('x', 'width', 'y', 'height') if direction in ('left', 'right')
        else ('y', 'height', 'x', 'width')
    )
    candidates = []
    for pane in layout['panes']:
        if pane['pane_id'] == pane_id:
            continue
        target = pane['rect']
        overlap = min(source[axis] + source[size], target[axis] + target[size]) - max(source[axis], target[axis])
        if overlap <= 0:
            continue
        distance = abs(source[cross] + source[cross_size] / 2 - target[cross] - target[cross_size] / 2)
        alignment = abs(source[axis] + source[size] / 2 - target[axis] - target[size] / 2)
        candidates.append((distance, alignment, pane['pane_id']))
    return min(candidates)[2] if candidates else None


def place(call, pane, target_pane_id, direction, focus):
    result = call('pane.move', {
        'pane_id': pane['pane_id'],
        'destination': {'type': 'new_tab', 'workspace_id': pane['workspace_id'], 'label': 'PANE MOVE TEMP'},
        'focus': False,
    })['move_result']
    if not result['changed']:
        raise RuntimeError('Temporary move did not change the layout')
    moved_id = result['pane']['pane_id']
    try:
        result = call('pane.move', {
            'pane_id': moved_id,
            'destination': {'type': 'tab', 'tab_id': pane['tab_id'], 'target_pane_id': target_pane_id,
                            'split': split_axis(direction), 'ratio': 0.5},
            'focus': False,
        })['move_result']
        if not result['changed']:
            raise RuntimeError('Return move did not change the layout')
        moved_id = result['pane']['pane_id']
        if direction in ('left', 'up'):
            call('pane.swap', {'source_pane_id': moved_id, 'target_pane_id': target_pane_id})
        if focus:
            call('pane.focus', {'pane_id': moved_id})
    except Exception as error:
        raise RuntimeError(f'Move interrupted; terminal {moved_id} remains alive. Inspect its tab before retrying: {error}') from error
    return result['pane']


def move(call, pane, direction, focus):
    neighbor = call('pane.neighbor', {'pane_id': pane['pane_id'], 'direction': direction})['neighbor']
    layout = neighbor['layout']
    if layout.get('zoomed'):
        raise ValueError('Unzoom this pane before moving it')
    target = neighbor.get('neighbor_pane_id') or perpendicular_target(layout, pane['pane_id'], direction)
    if target is None:
        return {'changed': False, 'pane': pane}
    moved = place(call, pane, target, direction, focus)
    return {'changed': True, 'pane': moved}


def create(call, pane, direction, focus):
    params = {'target_pane_id': pane['pane_id'], 'direction': split_axis(direction), 'focus': False}
    cwd = pane.get('foreground_cwd') or pane.get('cwd')
    if cwd:
        params['cwd'] = cwd
    new_pane = call('pane.split', params)['pane']
    new_id = new_pane['pane_id']
    call('pane.rename', {'pane_id': new_id, 'label': 'SHELL'})
    if direction in ('left', 'up'):
        call('pane.swap', {'source_pane_id': pane['pane_id'], 'target_pane_id': new_id})
    if focus:
        call('pane.focus', {'pane_id': new_id})
    return {'changed': True, 'pane': new_pane}


def identity(pane):
    return pane['terminal_id'], pane.get('agent'), pane.get('agent_session')


def has_agent_session(pane):
    # An agent may not have reported its session ID yet. Conversely, session
    # identity may be present before/without the agent-kind label. Either
    # signal blocks close, including idle/done/unknown and suspended agents.
    return bool(pane.get('agent')) or pane.get('agent_session') is not None


def blocked_close(pane):
    label = pane.get('label') or pane.get('terminal_title_stripped') or pane['pane_id']
    print(f'Close blocked: {label} has an open agent session.\n')
    print('Exit the agent normally before closing this pane.')
    print('This applies even when the agent is idle or done.\n')
    try:
        input('Press Enter to return: ')
    except (EOFError, KeyboardInterrupt):
        pass
    return {'changed': False, 'reason': 'open_agent_session'}


def close(call, pane):
    if has_agent_session(pane):
        return blocked_close(pane)
    label = pane.get('label') or pane.get('terminal_title_stripped') or pane['pane_id']
    print(f"Close pane {label} ({pane['pane_id']})?\n")
    print('This ends the shell and any running process in this pane.')
    print('Other panes will remain open.\n')
    try:
        answer = input('Type CLOSE to confirm; Enter cancels: ')
    except (EOFError, KeyboardInterrupt):
        return {'changed': False}
    if answer.strip() != 'CLOSE':
        return {'changed': False}
    current = call('pane.get', {'pane_id': pane['pane_id']})['pane']
    if has_agent_session(current):
        return blocked_close(current)
    if identity(current) != identity(pane):
        raise RuntimeError('Pane occupant changed during confirmation; nothing was closed')
    call('pane.close', {'pane_id': pane['pane_id']})
    return {'changed': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'move', 'close'))
    parser.add_argument('direction', nargs='?', choices=('left', 'down', 'up', 'right'))
    parser.add_argument('--pane', default=os.environ.get('HERDR_ACTIVE_PANE_ID'))
    parser.add_argument('--socket', default=os.environ.get('HERDR_SOCKET_PATH'))
    parser.add_argument('--no-focus', action='store_true')
    args = parser.parse_args(argv)
    if not args.pane or not args.socket:
        parser.error('--pane/--socket or HERDR_ACTIVE_PANE_ID/HERDR_SOCKET_PATH are required')
    if args.action != 'close' and args.direction is None:
        parser.error('create and move require a direction')
    if args.action == 'close' and args.direction is not None:
        parser.error('close does not take a direction')
    call = lambda method, params: request(args.socket, method, params)
    try:
        # Serialize repeated moves/splits before reading a layout and staging.
        # Never hold this lock while waiting for a close confirmation.
        if args.action == 'close':
            pane = call('pane.get', {'pane_id': args.pane})['pane']
            result = close(call, pane)
        else:
            lock_path = Path(args.socket).with_name(Path(args.socket).name + '.pane-control.lock')
            with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), 'w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                pane = call('pane.get', {'pane_id': args.pane})['pane']
                operation = create if args.action == 'create' else move
                result = operation(call, pane, args.direction, not args.no_focus)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration) as error:
        print(f'pane-control: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
