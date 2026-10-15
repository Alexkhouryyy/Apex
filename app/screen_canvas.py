"""A user-controlled transparent sketch surface, separate from AI guidance."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from html import escape
import json
import os
from pathlib import Path

from app.screen_overlay import TRANSPARENT, configure_dpi

PALETTE = {'cyan': '#67e8f9', 'yellow': '#fde047', 'pink': '#f9a8d4', 'white': '#ffffff'}


def export_svg(width, height, strokes):
    """Export only user-created marks, never the underlying desktop pixels."""
    elements = []
    for stroke in strokes:
        kind, points, color = stroke['kind'], stroke['points'], PALETTE[stroke['color']]
        if kind == 'text':
            x, y = points[0]
            elements.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="22" font-family="sans-serif">{escape(stroke["text"])}</text>')
        elif kind == 'box':
            (x, y), (a, b) = points
            elements.append(f'<rect x="{min(x,a)}" y="{min(y,b)}" width="{abs(a-x)}" height="{abs(b-y)}" fill="none" stroke="{color}" stroke-width="3"/>')
        elif kind == 'ellipse':
            (x, y), (a, b) = points
            elements.append(f'<ellipse cx="{(x+a)/2}" cy="{(y+b)/2}" rx="{abs(a-x)/2}" ry="{abs(b-y)/2}" fill="none" stroke="{color}" stroke-width="3"/>')
        else:
            mark = ' marker-end="url(#arrow)"' if kind == 'arrow' else ''
            coordinates = ' '.join(f'{x},{y}' for x, y in points)
            elements.append(f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"{mark}/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>' + ''.join(elements) + '</svg>')


def _segment_distance(p, a, b) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def hit(stroke, point, radius=12) -> bool:
    """Whether `point` lands on this mark (for the eraser)."""
    x, y = point
    kind, pts = stroke['kind'], stroke['points']
    if kind == 'text':
        (tx, ty), = pts[:1]
        w = 10 * max(1, len(stroke.get('text', '')))
        return tx - radius <= x <= tx + w + radius and ty - radius <= y <= ty + 24 + radius
    if kind in ('box', 'ellipse'):
        (a, b), (c, d) = pts[0], pts[-1]
        return min(a, c) - radius <= x <= max(a, c) + radius and min(b, d) - radius <= y <= max(b, d) + radius
    if len(pts) == 1:
        return _segment_distance(point, pts[0], pts[0]) <= radius
    return any(_segment_distance(point, pts[i], pts[i + 1]) <= radius for i in range(len(pts) - 1))


class Marks:
    """Your marks, with an undo history that covers adding, erasing and clearing.
    Pure (no Tk), so the eraser and undo are tested on any OS."""
    MAX_MARKS, MAX_POINTS = 100, 20000

    def __init__(self):
        self.items: list[dict] = []
        self._done: list[tuple] = []
        self._undone: list[tuple] = []

    def full(self) -> bool:
        return len(self.items) >= self.MAX_MARKS or sum(len(s['points']) for s in self.items) >= self.MAX_POINTS

    def add(self, stroke: dict) -> None:
        self.items.append(stroke)
        self._done.append(('add', stroke))
        self._undone.clear()

    def erase_at(self, point, radius=12) -> bool:
        for i in range(len(self.items) - 1, -1, -1):     # topmost first
            if hit(self.items[i], point, radius):
                stroke = self.items.pop(i)
                self._done.append(('erase', i, stroke))
                self._undone.clear()
                return True
        return False

    def clear(self) -> None:
        if self.items:
            self._done.append(('clear', self.items[:]))
            self.items.clear()
            self._undone.clear()

    def undo(self) -> bool:
        if not self._done:
            return False
        action = self._done.pop()
        if action[0] == 'add':
            self.items.remove(action[1])
        elif action[0] == 'erase':
            self.items.insert(action[1], action[2])
        else:
            self.items[:] = action[1]
        self._undone.append(action)
        return True

    def redo(self) -> bool:
        if not self._undone:
            return False
        action = self._undone.pop()
        if action[0] == 'add':
            self.items.append(action[1])
        elif action[0] == 'erase':
            self.items.remove(action[2])
        else:
            self.items.clear()
        self._done.append(action)
        return True


def set_editing(root, enabled):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetParent.argtypes, user.GetParent.restype = [wintypes.HWND], wintypes.HWND
    get = user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.GetWindowLongW
    put = user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user.SetWindowLongW
    get.argtypes, get.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
    put.argtypes, put.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
    hwnd = user.GetParent(root.winfo_id()) or root.winfo_id()
    style = get(hwnd, -20) | 0x80000 | 0x80
    style = style & ~(0x20 | 0x08000000) if enabled else style | 0x20 | 0x08000000
    put(hwnd, -20, style)
    return hwnd


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--frame', required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--companion-hwnd',type=int,default=0)
    args = parser.parse_args(argv)
    if os.name != 'nt':
        raise RuntimeError('The transparent sketch surface requires Windows.')
    frame = json.loads(args.frame)
    if any(type(frame.get(k)) is not int for k in ('left', 'top', 'width', 'height')) or not 1 <= min(frame['width'], frame['height']) <= max(frame['width'], frame['height']) <= 20000:
        raise ValueError('Invalid screen geometry.')
    configure_dpi()
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog
    root = tk.Tk(); root.title('Apex shared sketch'); root.overrideredirect(True)
    root.attributes('-topmost', True); root.attributes('-transparentcolor', TRANSPARENT)
    canvas = tk.Canvas(root, bg='#111318', highlightthickness=0)
    canvas.pack(fill='both', expand=True)
    root.geometry(f"{frame['width']}x{frame['height']}")
    root.update_idletasks()
    hwnd = set_editing(root, True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.SetWindowPos(hwnd, wintypes.HWND(-1), frame['left'], frame['top'], frame['width'], frame['height'], 0x10 | 0x40)
    # A second, excluded toolbar remains clickable when marks become click-through.
    toolbar = tk.Toplevel(root); toolbar.title('Apex drawing tools'); toolbar.attributes('-topmost', True)
    toolbar.transient(root)
    toolbar.resizable(False, False)
    toolbar.geometry(f'+{max(0, frame["left"]+20)}+{max(0, frame["top"]+20)}')
    toolbar.update_idletasks()
    from app.window_capture import exclude_from_capture
    user.GetParent.argtypes, user.GetParent.restype = [wintypes.HWND], wintypes.HWND
    exclude_from_capture(user.GetParent(toolbar.winfo_id()) or toolbar.winfo_id())
    tool, color = tk.StringVar(value='pen'), tk.StringVar(value='cyan')
    marks, current = Marks(), []
    editing = True
    notice = tk.StringVar(value='Draw on this canvas · Done reveals the screen')
    backdrop = None

    def capture_backdrop():
        nonlocal backdrop, hwnd
        # Color-keyed blank pixels cannot receive mouse input on Windows.
        # Edit over a frozen screen image, then Done restores the live,
        # click-through desktop with just the user-created marks visible.
        toolbar.withdraw(); root.withdraw(); root.update_idletasks()
        import mss
        from PIL import Image, ImageTk
        from app.window_capture import without_surfaces
        with without_surfaces([args.companion_hwnd] if args.companion_hwnd else [],args.parent_pid), mss.mss() as screen:
            shot = screen.grab({k:frame[k] for k in ('left','top','width','height')})
            backdrop = ImageTk.PhotoImage(Image.frombytes('RGB',shot.size,shot.rgb))
        root.deiconify()
        hwnd = set_editing(root, editing)
        user.SetWindowPos(hwnd,wintypes.HWND(-1),frame['left'],frame['top'],frame['width'],frame['height'],0x10 | 0x40)
        toolbar.deiconify()
        toolbar.lift(); toolbar.update_idletasks()
        exclude_from_capture(user.GetParent(toolbar.winfo_id()) or toolbar.winfo_id())

    def redraw(preview=None):
        canvas.delete('all')
        if editing and backdrop is not None:
            canvas.create_image(0,0,image=backdrop,anchor='nw')
        for stroke in marks.items + ([preview] if preview else []):
            points = [v for point in stroke['points'] for v in point]
            tint = PALETTE[stroke['color']]
            if stroke['kind'] == 'text':
                canvas.create_text(*points, text=stroke['text'], fill=tint, anchor='nw', font=('Segoe UI', 16))
            elif stroke['kind'] == 'box':
                x, y, a, b = points
                canvas.create_rectangle(min(x,a), min(y,b), max(x,a), max(y,b), outline=tint, width=3)
            elif stroke['kind'] == 'ellipse':
                x, y, a, b = points
                canvas.create_oval(min(x,a), min(y,b), max(x,a), max(y,b), outline=tint, width=3)
            elif len(points) >= 4:
                canvas.create_line(*points, fill=tint, width=3, capstyle='round',
                                   arrow='last' if stroke['kind'] == 'arrow' else 'none')

    def toggle():
        nonlocal editing
        editing = not editing
        current.clear()
        canvas.configure(bg='#111318' if editing else TRANSPARENT)
        if editing: capture_backdrop()
        set_editing(root, editing)
        done.configure(text='Done' if editing else 'Edit')
        notice.set('Draw on this canvas · Done reveals the screen' if editing else 'Marks stay visible · speak to Companion or work in your app')
        redraw()

    def press(event):
        if editing and tool.get() == 'eraser':
            if marks.erase_at((event.x, event.y)): redraw()
            return
        if not editing or marks.full():
            notice.set('100 marks maximum · Undo or Clear to continue'); return
        current[:] = [(event.x, event.y)]
        if tool.get() == 'text':
            text = simpledialog.askstring('Write on the screen', 'Text (up to 500 characters):', parent=toolbar)
            if text and len(text) <= 500:
                marks.add({'kind':'text', 'points':current[:], 'color':color.get(), 'text':text}); redraw()
            current.clear()

    def drag(event):
        if not current or not editing or tool.get() == 'eraser': return
        point = (max(0,min(event.x,frame['width'])), max(0,min(event.y,frame['height'])))
        if tool.get() == 'pen':
            if len(current) < 2048 and current[-1] != point: current.append(point)
        else: current[1:] = [point]
        redraw({'kind':tool.get(), 'points':current[:], 'color':color.get()})

    def release(event):
        if len(current) >= 2:
            marks.add({'kind':tool.get(), 'points':current[:], 'color':color.get()}); redraw()
        current.clear()

    def undo():
        if marks.undo(): redraw()
    def restore():
        if marks.redo(): redraw()
    def clear():
        marks.clear(); current.clear(); redraw()
    def save():
        filename = filedialog.asksaveasfilename(parent=toolbar, title='Save your drawing',
                                               defaultextension='.svg', filetypes=[('SVG drawing', '*.svg')])
        if filename:
            try:
                Path(filename).write_text(export_svg(frame['width'],frame['height'],marks.items), encoding='utf-8')
                notice.set('Drawing saved · underlying screen pixels were not saved')
            except OSError as exc: messagebox.showerror('Could not save drawing', str(exc), parent=toolbar)

    for label, value in [('Pen','pen'),('Box','box'),('Circle','ellipse'),('Arrow','arrow'),('Text','text'),('Eraser','eraser')]:
        tk.Radiobutton(toolbar,text=label,value=value,variable=tool,indicatoron=False).pack(side='left')
    tk.OptionMenu(toolbar,color,*PALETTE).pack(side='left')
    for label, callback in [('Undo',undo),('Redo',restore),('Clear',clear),('Save SVG',save)]:
        tk.Button(toolbar,text=label,command=callback).pack(side='left')
    done = tk.Button(toolbar,text='Done',command=toggle); done.pack(side='left')
    tk.Button(toolbar,text='Close',command=root.destroy).pack(side='left')
    # Keep status out of captures as part of the separate toolbar.
    toolbar.bind('<Escape>',lambda event: toggle() if editing else root.destroy())
    root.bind('<Escape>',lambda event: toggle() if editing else root.destroy())
    canvas.bind('<ButtonPress-1>',press); canvas.bind('<B1-Motion>',drag); canvas.bind('<ButtonRelease-1>',release)
    toolbar.protocol('WM_DELETE_WINDOW',root.destroy)
    root.bind('<FocusIn>',lambda event:toolbar.lift(),add=True)
    def watch():
        from app.companion_desktop import parent_alive
        if not parent_alive(args.parent_pid): root.destroy()
        else: root.after(1000,watch)
    root.after(1000,watch)
    capture_backdrop(); redraw(); root.update_idletasks()
    toolbar.lift(); toolbar.focus_force(); toolbar.update_idletasks()
    exclude_from_capture(user.GetParent(toolbar.winfo_id()) or toolbar.winfo_id())
    print(json.dumps({'ready':True,'window':int(hwnd),'toolbar':int(user.GetParent(toolbar.winfo_id()) or toolbar.winfo_id()),
                      'canvas':canvas.winfo_id(),'done':done.winfo_id()}),flush=True)
    root.mainloop()


if __name__ == '__main__':
    main()
