"""Windows-only transparent, non-activating, click-through drawing surface.

Runs on its own main GUI thread. JSON lines on stdin request bounded shapes;
stdout acknowledges actual canvas updates. No screenshot or model is loaded.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import queue
import sys
import threading

from agent.screen_guidance import COLORS, validate_shapes

TRANSPARENT = '#010203'
CLICK_THROUGH = 0x80000 | 0x20 | 0x08000000 | 0x80


def configure_dpi():
    if os.name == 'nt':
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def configure_window(window):
    """Use pointer-sized declarations: default ctypes int truncates x64 HWNDs."""
    user = ctypes.windll.user32
    user.GetParent.argtypes, user.GetParent.restype = [wintypes.HWND], wintypes.HWND
    get = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
    put = user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.SetWindowLongW
    get.argtypes, get.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
    put.argtypes, put.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
    hwnd = user.GetParent(window.winfo_id()) or window.winfo_id()
    put(hwnd, -20, get(hwnd, -20) | CLICK_THROUGH)
    from app.window_capture import exclude_from_capture
    exclude_from_capture(hwnd)
    return hwnd


def paint(canvas, frame, shapes):
    width, height = frame['width'], frame['height']
    for s in validate_shapes(shapes):
        points = [(round(x * (width - 1)), round(y * (height - 1))) for x, y in s['points']]
        coords = [v for point in points for v in point]
        color, kind = COLORS[s['color']], s['type']
        if kind in {'rectangle', 'ellipse'}:
            x1, y1, x2, y2 = coords
            box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
            fn = canvas.create_rectangle if kind == 'rectangle' else canvas.create_oval
            fn(*box, outline=color, width=3)
        elif kind == 'polygon':
            canvas.create_polygon(*coords, outline=color, fill='', width=3)
        elif kind != 'label':
            canvas.create_line(*coords, fill=color, width=3, smooth=kind == 'curve',
                               arrow='last' if kind == 'arrow' else 'none', arrowshape=(14, 18, 6))
        if s['label']:
            x, y = points[-1] if kind == 'arrow' else points[0]
            label = canvas.create_text(max(4, min(x + 8, width - 8)), max(4, min(y + 8, height - 8)),
                                       text=s['label'], fill=color, anchor='nw', font=('Segoe UI', 13, 'bold'),
                                       width=max(40, min(320, width-12)))
            box = canvas.bbox(label)
            if box:
                dx = max(0, 4-box[0]) if box[0] < 4 else min(0, width-4-box[2])
                dy = max(0, 4-box[1]) if box[1] < 4 else min(0, height-4-box[3])
                if dx or dy:
                    canvas.move(label, dx, dy)
                    box = canvas.bbox(label)
                bg = canvas.create_rectangle(box[0]-4, box[1]-2, box[2]+4, box[3]+2,
                                             fill='#111827', outline=color)
                canvas.tag_lower(bg, label)


def main():
    if os.name != 'nt':
        raise RuntimeError('Native screen drawings require Windows.')
    configure_dpi()
    import tkinter as tk
    root = tk.Tk()
    root.title('Apex Screen Guidance')
    root.withdraw()
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    root.attributes('-transparentcolor', TRANSPARENT)
    canvas = tk.Canvas(root, bg=TRANSPARENT, highlightthickness=0)
    canvas.pack(fill='both', expand=True)
    pending = queue.Queue(maxsize=32)
    expiry = None

    def reader():
        for line in sys.stdin:
            if len(line) > 100_000:
                continue
            try:
                pending.put(json.loads(line), timeout=2)
            except (ValueError, queue.Full):
                pass
        pending.put({'kind': 'quit'})

    def clear():
        nonlocal expiry
        if expiry:
            root.after_cancel(expiry)
            expiry = None
        canvas.delete('all')
        root.withdraw()

    def tick():
        nonlocal expiry
        try:
            while True:
                item = pending.get_nowait()
                result = {'seq': item.get('seq'), 'ok': False}
                try:
                    clear()
                    if item['kind'] == 'quit':
                        root.destroy()
                        return
                    if item['kind'] == 'draw':
                        frame = item['frame']
                        if any(type(frame[k]) is not int for k in ('left', 'top', 'width', 'height')):
                            raise ValueError('Invalid desktop geometry.')
                        if not 1 <= min(frame['width'], frame['height']) or max(frame['width'], frame['height']) > 20000:
                            raise ValueError('Invalid desktop geometry.')
                        seconds = item['seconds']
                        if type(seconds) not in (int, float) or not 2 <= seconds <= 30:
                            raise ValueError('Invalid drawing lifetime.')
                        root.geometry(f"{frame['width']}x{frame['height']}")
                        paint(canvas, frame, item['shapes'])
                        # Geometry strings interpret negative positions from the
                        # right/bottom. MoveWindow uses actual virtual-screen coordinates.
                        root.update_idletasks()
                        hwnd = configure_window(root)
                        root.deiconify()
                        user = ctypes.windll.user32
                        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
                        user.SetWindowPos(hwnd, wintypes.HWND(-1), frame['left'], frame['top'],
                                          frame['width'], frame['height'], 0x10 | 0x40)
                        root.update_idletasks()
                        expiry = root.after(round(seconds * 1000), clear)
                        result['hwnd'] = int(hwnd)
                    result['ok'] = True
                except Exception as exc:
                    clear()
                    result['error'] = str(exc)[:300]
                print(json.dumps(result), flush=True)
        except queue.Empty:
            pass
        root.after(25, tick)

    threading.Thread(target=reader, daemon=True).start()
    root.after(25, tick)
    root.mainloop()


if __name__ == '__main__':
    main()
