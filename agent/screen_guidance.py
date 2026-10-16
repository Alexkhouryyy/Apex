"""Temporary annotations bound to one fresh host screenshot and owner turn."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import math
import secrets
import time

from agent import desktop_companion

_frame = ContextVar('apex_screen_guidance_frame', default=None)
COLORS = {'cyan': '#22d3ee', 'red': '#fb7185', 'yellow': '#facc15', 'green': '#4ade80', 'white': '#ffffff'}
KINDS = {'arrow', 'line', 'rectangle', 'ellipse', 'polygon', 'curve', 'label'}


def frame_info(monitor: dict) -> dict:
    geometry = {k: int(monitor[k]) for k in ('left', 'top', 'width', 'height')}
    if min(geometry['width'], geometry['height']) < 1 or max(geometry['width'], geometry['height']) > 20000:
        raise ValueError('Invalid screen dimensions.')
    return {'id': secrets.token_hex(16), 'captured_at': time.monotonic(), **geometry}


def usable(frame: dict | None) -> bool:
    state = desktop_companion.status()
    return bool(frame and 0 <= time.monotonic() - frame['captured_at'] < 120 and state['ready'] and state['shown']
                and state['mode'] == 'screen')


@contextmanager
def turn(frame: dict | None):
    token = _frame.set(frame if usable(frame) else None)
    try:
        yield
    finally:
        _frame.reset(token)


def available() -> bool:
    return usable(_frame.get())


def instructions() -> str:
    frame = _frame.get()
    if not usable(frame):
        return ''
    return (f'\nTemporary screen guidance is available for this host screenshot. Frame ID: {frame["id"]}. '
            'Point on your own, the way a friend beside the user would: whenever your answer refers to something '
            'visible in this screenshot (a button, menu, field, setting, error, link, line of code, piece on a board), '
            'call screen_draw to mark it in the same turn, without waiting to be asked to draw. One target: a single '
            'arrow ending on it, or an ellipse/rectangle around it. Several steps: mark each one, with short labels '
            '"1", "2", "3" in order. Mark only what you can actually see; when nothing on the screen is relevant, '
            'or the target is hidden or unclear, draw nothing and say what to look for. '
            'Coordinates are normalized 0..1 across THIS screenshot, with (0,0) at its top left. '
            'Keep it to the few marks that help; they are click-through and expire, so the user never has to clear '
            'them. Say "I marked it" only after the tool confirms the drawing. '
            'A drawing only explains; it does not click, type or grant permission to act.\n')


def validate_shapes(shapes) -> list[dict]:
    if not isinstance(shapes, list) or not 1 <= len(shapes) <= 24:
        raise ValueError('Provide 1–24 screen shapes.')
    out, total = [], 0
    for shape in shapes:
        if not isinstance(shape, dict) or shape.get('type') not in KINDS:
            raise ValueError('Unknown screen shape type.')
        points = shape.get('points')
        if not isinstance(points, list) or not 1 <= len(points) <= 128:
            raise ValueError('Each shape needs 1–128 normalized points.')
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or any(
                    type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in point):
                raise ValueError('Points must be finite [x,y] values between 0 and 1.')
        count, kind = len(points), shape['type']
        if ((kind in {'rectangle', 'ellipse', 'arrow'} and count != 2)
                or (kind in {'line', 'curve'} and count < 2)
                or (kind == 'polygon' and count < 3) or (kind == 'label' and count != 1)):
            raise ValueError('That shape has the wrong number of points.')
        label = shape.get('label', '')
        if not isinstance(label, str) or len(label) > 100 or (kind == 'label' and not label.strip()):
            raise ValueError('Labels must be plain text up to 100 characters.')
        color = shape.get('color', 'cyan')
        if not isinstance(color, str) or color not in COLORS:
            raise ValueError('Color is cyan, red, yellow, green or white.')
        total += count
        if total > 512:
            raise ValueError('Too many screen points.')
        out.append({'type': kind, 'points': points, 'color': color, 'label': label})
    return out


def draw(frame_id: str, shapes, seconds: float = 15) -> dict:
    frame = _frame.get()
    if not usable(frame) or frame_id != frame['id']:
        raise ValueError('Use the frame ID of this fresh host screenshot. Browser shares and stale frames cannot draw on the desktop.')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 2 <= seconds <= 30:
        raise ValueError('Screen drawings expire after 2–30 seconds.')
    return desktop_companion.draw(frame, validate_shapes(shapes), float(seconds))
