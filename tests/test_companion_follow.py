"""The desktop Companion bubble that follows the cursor, and the drawing canvas's
eraser, ellipse and undo history. Placement, easing, the follow loop and the
marks are pure, so they run on any OS; the Windows glue is exercised on a PC."""
from types import SimpleNamespace

import pytest

from app import follow
from app.companion_desktop import Bridge, FOLLOW_SIZE
from app.screen_canvas import Marks, export_svg, hit

AREA = (0, 0, 1920, 1040)        # a 1080p screen above a 40 px taskbar
SIZE = (320, 220)


def covers(pos, size, cursor):
    x, y = pos
    return x <= cursor[0] < x + size[0] and y <= cursor[1] < y + size[1]


@pytest.mark.parametrize('cursor', [(500, 400), (1900, 400), (500, 1030), (1900, 1030), (0, 0), (960, 520)])
def test_the_bubble_stays_on_screen_and_never_covers_the_pointer(cursor):
    x, y = follow.follow_position(cursor, SIZE, AREA)
    assert AREA[0] <= x and x + SIZE[0] <= AREA[2]
    assert AREA[1] <= y and y + SIZE[1] <= AREA[3]
    assert not covers((x, y), SIZE, cursor)


def test_it_prefers_below_right_and_flips_at_the_edges():
    assert follow.follow_position((500, 400), SIZE, AREA) == (500 + follow.OFFSET, 400 + follow.OFFSET)
    x, _ = follow.follow_position((1900, 400), SIZE, AREA)
    assert x + SIZE[0] <= 1900, 'near the right edge it goes left of the pointer'
    _, y = follow.follow_position((500, 1030), SIZE, AREA)
    assert y + SIZE[1] <= 1030, 'near the taskbar it goes above the pointer'


def test_a_second_monitor_uses_its_own_work_area():
    right_screen = (1920, 0, 3840, 1040)
    x, y = follow.follow_position((2000, 300), SIZE, right_screen)
    assert x >= 1920 and x + SIZE[0] <= 3840


def test_easing_moves_part_way_then_snaps():
    assert follow.smooth(None, (100, 100)) == (100, 100)
    step = follow.smooth((0, 0), (100, 0))
    assert 0 < step[0] < 100
    assert follow.smooth((99, 0), (100, 0)) == (100, 0)


def test_the_follow_loop_moves_only_when_on_and_not_paused():
    cursor = [500, 400]
    moves = []
    f = follow.Follower(lambda: tuple(cursor), lambda x, y: AREA, lambda: SIZE,
                        lambda x, y: moves.append((x, y)), enabled=False)
    assert f.tick() is False and moves == [], 'off: never moves'
    f.set_enabled(True)
    assert f.tick() is True and moves[-1] == (528, 428), 'first tick seats it beside the pointer'
    n = len(moves)
    assert f.tick() is False and len(moves) == n, 'a still pointer does not move the window'
    f.paused = True
    cursor[:] = [800, 500]
    assert f.tick() is False and len(moves) == n, 'paused (typing, hidden): stays put'
    f.paused = False
    assert f.tick() is True and moves[-1] != (528, 428)


def test_a_failing_move_or_pointer_never_kills_the_loop():
    def boom(*a):
        raise OSError('window gone')
    f = follow.Follower(lambda: (500, 400), lambda x, y: AREA, lambda: SIZE, boom, enabled=True)
    assert f.tick() is False
    f2 = follow.Follower(boom, lambda x, y: AREA, lambda: SIZE, lambda x, y: None, enabled=True)
    assert f2.tick() is False


# ---------------------------------------------------------------- the bridge's Follow button

class Window:
    def __init__(self):
        self.sizes, self.moves = [], []
    def get_current_url(self):
        return 'http://127.0.0.1:7860/companion?desktop=1'
    def resize(self, w, h):
        self.sizes.append((w, h))
    def move(self, x, y):
        self.moves.append((x, y))


def test_follow_shrinks_to_the_bubble_and_off_returns_to_the_corner():
    bridge = Bridge(SimpleNamespace(url='http://127.0.0.1:7860'), 'master-secret')
    bridge._window = Window()
    bridge._follower = follow.Follower(lambda: (0, 0), lambda x, y: AREA, lambda: SIZE, lambda x, y: None)
    assert bridge.follow(True) == {'follow': True}
    assert bridge._window.sizes[-1] == FOLLOW_SIZE and bridge._follower.enabled and not bridge._follower.paused
    assert bridge.follow(False) == {'follow': False}
    assert bridge._window.sizes[-1] == (420, 300)
    assert bridge.connect()['follow'] is False and bridge.connect()['can_follow'] is True


def test_follow_needs_the_local_companion_page_and_the_windows_follower():
    bridge = Bridge(SimpleNamespace(url='http://127.0.0.1:7860'), 'master-secret')
    bridge._window = SimpleNamespace(get_current_url=lambda: 'https://example.com/companion')
    with pytest.raises(ValueError):
        bridge.follow(True)                       # another site can't move your window
    bridge._window = Window()
    with pytest.raises(ValueError, match='Windows'):
        bridge.follow(True)                       # no follower (not Windows)


# ---------------------------------------------------------------- drawing: circle, eraser, undo

def stroke(kind, pts, **extra):
    return {'kind': kind, 'points': pts, 'color': 'cyan', **extra}


def test_the_eraser_removes_the_mark_you_click_and_undo_brings_it_back():
    m = Marks()
    m.add(stroke('pen', [(10, 10), (100, 10)]))
    m.add(stroke('box', [(200, 200), (300, 260)]))
    m.add(stroke('text', [(400, 400)], text='hello'))
    assert m.erase_at((55, 12)) is True
    assert [s['kind'] for s in m.items] == ['box', 'text'], 'the pen line under the click is gone'
    assert m.erase_at((900, 900)) is False, 'empty space erases nothing'
    assert m.undo() is True and [s['kind'] for s in m.items] == ['pen', 'box', 'text'], 'undo restores it in place'
    assert m.redo() is True and [s['kind'] for s in m.items] == ['box', 'text']


def test_clear_is_undoable_and_redo_is_dropped_by_a_new_mark():
    m = Marks()
    m.add(stroke('pen', [(0, 0), (5, 5)]))
    m.add(stroke('arrow', [(0, 0), (50, 50)]))
    m.clear()
    assert m.items == []
    m.undo()
    assert len(m.items) == 2, 'Clear can be undone'
    m.undo()
    m.add(stroke('ellipse', [(0, 0), (40, 20)]))
    assert m.redo() is False, 'a new mark ends the redo chain'


def test_the_eraser_takes_the_topmost_mark_first():
    m = Marks()
    m.add(stroke('box', [(0, 0), (100, 100)]))
    m.add(stroke('ellipse', [(20, 20), (80, 80)]))
    m.erase_at((50, 50))
    assert [s['kind'] for s in m.items] == ['box']


def test_hit_testing_by_kind():
    assert hit(stroke('pen', [(0, 0), (100, 0)]), (50, 8))
    assert not hit(stroke('pen', [(0, 0), (100, 0)]), (50, 40))
    assert hit(stroke('ellipse', [(0, 0), (40, 20)]), (20, 10))
    assert hit(stroke('text', [(10, 10)], text='abc'), (20, 20))


def test_limits_and_the_circle_exports_as_an_svg_ellipse():
    m = Marks()
    for i in range(Marks.MAX_MARKS):
        m.add(stroke('pen', [(i, 0), (i, 1)]))
    assert m.full()
    svg = export_svg(800, 600, [stroke('ellipse', [(10, 20), (110, 80)])])
    assert '<ellipse cx="60.0" cy="50.0" rx="50.0" ry="30.0"' in svg


# ---------------------------------------------------------------- global hotkeys: draw and follow

from agent import desktop_companion as desktop


@pytest.fixture
def running(monkeypatch):
    monkeypatch.setattr(desktop, '_process', SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(desktop, '_events', [])
    monkeypatch.setattr(desktop, '_seq', 0)
    monkeypatch.setattr(desktop, '_is_muted', lambda: False)


def test_the_draw_and_follow_hotkeys_reach_the_window(running):
    desktop.on_draw_hotkey()
    desktop.on_follow_hotkey()
    assert [e['kind'] for e in desktop._events] == ['sketch', 'follow']


def test_muted_hotkeys_do_nothing(running, monkeypatch):
    monkeypatch.setattr(desktop, '_is_muted', lambda: True)
    desktop.on_draw_hotkey()
    desktop.on_follow_hotkey()
    assert desktop._events == []


def test_start_binds_the_main_draw_and_follow_hotkeys(monkeypatch):
    import config
    import importlib.util
    import subprocess
    from app import hotkey
    bound = {}

    class Keys:
        def bind(self, combo, fn): bound[combo] = fn
        def start(self): return True
        def stop(self): pass
    monkeypatch.setattr(hotkey, 'GlobalHotkeys', Keys)
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: SimpleNamespace(poll=lambda: None, pid=4242))
    for name in ('_process', '_session', '_ready', '_heartbeat', '_shown', '_url', '_mode', '_keys'):
        monkeypatch.setattr(desktop, name, getattr(desktop, name))
    monkeypatch.setattr(desktop, '_process', None)
    monkeypatch.setattr(config, 'COMPANION_HOTKEY', '<f8>')
    monkeypatch.setattr(config, 'COMPANION_DRAW_HOTKEY', '<ctrl>+<alt>+d')
    monkeypatch.setattr(config, 'COMPANION_FOLLOW_HOTKEY', '')        # empty turns one off
    desktop.start('http://127.0.0.1:7860')
    assert bound == {'<f8>': desktop.on_hotkey, '<ctrl>+<alt>+d': desktop.on_draw_hotkey}


def test_connect_tells_the_page_its_hotkeys(monkeypatch):
    import config
    monkeypatch.setattr(config, 'COMPANION_DRAW_HOTKEY', '<ctrl>+<alt>+d')
    monkeypatch.setattr(config, 'COMPANION_FOLLOW_HOTKEY', '<ctrl>+<alt>+f')
    bridge = Bridge(SimpleNamespace(url='http://127.0.0.1:7860'), 'master-secret')
    bridge._window = Window()
    info = bridge.connect()
    assert info['draw_hotkey'] == '<ctrl>+<alt>+d' and info['follow_hotkey'] == '', 'no follower: no follow hotkey shown'
    bridge._follower = follow.Follower(lambda: (0, 0), lambda x, y: AREA, lambda: SIZE, lambda x, y: None)
    assert bridge.connect()['follow_hotkey'] == '<ctrl>+<alt>+f'


# ---------------------------------------------------------------- automatic: follows, and flies to what she marks

def test_the_bubble_follows_by_default(monkeypatch):
    import importlib
    import config
    monkeypatch.delenv('COMPANION_FOLLOW_CURSOR', raising=False)
    try:
        assert importlib.reload(config).COMPANION_FOLLOW_CURSOR is True
        monkeypatch.setenv('COMPANION_FOLLOW_CURSOR', 'false')
        assert importlib.reload(config).COMPANION_FOLLOW_CURSOR is False, 'still possible to start in the corner'
    finally:
        monkeypatch.undo()
        importlib.reload(config)


FRAME = {'id': 'f', 'left': 1920, 'top': 0, 'width': 1920, 'height': 1080}


def test_the_focus_is_an_arrow_tip_or_a_marks_centre():
    arrow = [{'type': 'label', 'points': [[.1, .1]], 'label': 'x'},
             {'type': 'arrow', 'points': [[.2, .2], [.5, .25]]}]
    assert follow.focus_point(FRAME, arrow) == (1920 + 960, 270), 'the arrow tip, on the second monitor'
    box = [{'type': 'rectangle', 'points': [[.1, .1], [.3, .5]]}]
    assert follow.focus_point(FRAME, box) == (1920 + 384, 324)
    assert follow.focus_point(FRAME, []) is None


def test_pointing_moves_the_bubble_beside_the_mark_then_back_to_the_pointer():
    now = [0.0]
    moves = []
    f = follow.Follower(lambda: (100, 100), lambda x, y: AREA, lambda: SIZE,
                        lambda x, y: moves.append((x, y)), enabled=True, clock=lambda: now[0])
    f.tick()
    assert moves[-1] == (128, 128)
    f.point_at(1500, 600, 10)
    for _ in range(40):
        f.tick()
    target = follow.follow_position((1500, 600), SIZE, AREA)
    assert moves[-1] == target and not covers(target, SIZE, (1500, 600)), 'beside what she marked, not over it'
    now[0] = 11
    for _ in range(40):
        f.tick()
    assert moves[-1] == (128, 128), 'the mark expired: back beside the pointer'


def test_a_confirmed_drawing_points_the_bubble_only_when_following():
    from app.companion_desktop import point_bubble
    bridge = SimpleNamespace(_follower=follow.Follower(lambda: (0, 0), lambda x, y: AREA, lambda: SIZE,
                                                       lambda x, y: None, enabled=True))
    item = {'frame': FRAME, 'shapes': [{'type': 'ellipse', 'points': [[.4, .4], [.6, .6]]}], 'seconds': 12}
    assert point_bubble(bridge, item, {'ok': False}) is False, 'an unconfirmed drawing moves nothing'
    assert bridge._follower._focus is None
    assert point_bubble(bridge, item, {'ok': True}) is True
    assert bridge._follower._focus[0] == (1920 + 960, 540)
    bridge._follower.set_enabled(False)
    bridge._follower._focus = None
    assert point_bubble(bridge, item, {'ok': True}) is False, 'Follow off: the bubble stays in its corner'
    assert point_bubble(SimpleNamespace(), item, {'ok': True}) is False, 'no follower (not Windows)'


def test_celine_is_told_to_point_without_being_asked(monkeypatch):
    import time
    from agent import screen_guidance as guidance
    monkeypatch.setattr(desktop, 'status', lambda: {'ready': True, 'shown': True, 'mode': 'screen'})
    frame = {'id': 'abc123', 'captured_at': time.monotonic()}
    with guidance.turn(frame):
        text = guidance.instructions()
    assert 'abc123' in text and 'without waiting to be asked' in text
    assert 'draw nothing' in text, 'and holds back when the screen is not relevant'
