"""Dictation refuses changed fields; sketch exports remain inert and local."""
import os
from types import SimpleNamespace

import pytest

from app import desktop_dictation as dictation
from app.companion_desktop import Bridge
from app.screen_canvas import export_svg


TARGET = {'window':123,'focus':456,'pid':789,'element_id':[1,2,3],'title':'Notes'}


def test_restoring_action_during_cold_boot_preserves_queued_hotkey(monkeypatch):
    from agent import desktop_companion as desktop
    monkeypatch.setattr(desktop,'_preferred_mode','screen')
    monkeypatch.setattr(desktop,'_mode','screen')
    clears=[];monkeypatch.setattr(desktop,'clear',lambda:clears.append(True))
    desktop.choose_mode('screen')
    assert desktop._mode=='screen' and clears==[]
    desktop.choose_mode('dictate')
    assert desktop._mode=='idle' and clears==[True]


@pytest.mark.parametrize('mode,expected',[('dictate','idle'),('screen','screen')])
def test_chat_ends_dictation_but_keeps_screen_question_context(monkeypatch,mode,expected):
    from agent import desktop_companion as desktop
    monkeypatch.setattr(desktop,'_mode',mode)
    monkeypatch.setattr(desktop,'clear',lambda:None)
    desktop.expanded()
    assert desktop._mode==expected


def test_dictation_pins_the_field_and_never_sends_enter_tab_or_clipboard(monkeypatch):
    sent=[]
    monkeypatch.setattr(dictation,'current_target',lambda:TARGET)
    def type_text(text,delay_ms,check_abort,batch_size):
        assert batch_size==32
        check_abort();sent.append(text)
    monkeypatch.setattr('tools.windows_input.type_unicode',type_text)
    session=dictation.Session(TARGET,7)
    assert session.insert('Hello 😀\r\nworld\t',7)=={'inserted':True,'characters':14}
    assert sent==['Hello 😀 world ']
    session.stop()
    with pytest.raises(RuntimeError,match='stopped'):session.insert('later',7)
    assert len(sent)==1


@pytest.mark.parametrize('field',['window','focus','pid','element_id'])
def test_focus_changes_refuse_insertion_and_cancel_session(field,monkeypatch):
    monkeypatch.setattr(dictation,'current_target',lambda:{**TARGET,field:999})
    monkeypatch.setattr('tools.windows_input.type_unicode',lambda *a,**kw:pytest.fail('Must not type'))
    session=dictation.Session(TARGET,7)
    with pytest.raises(RuntimeError,match='field changed'):session.insert('private text',7)
    assert session.cancelled.is_set()


def test_focus_change_mid_segment_stops_remaining_characters(monkeypatch):
    sent=[]
    monkeypatch.setattr(dictation,'current_target',lambda:TARGET if not sent else {**TARGET,'focus':999})
    def type_text(text,delay_ms,check_abort,batch_size):
        for char in text:check_abort();sent.append(char)
    monkeypatch.setattr('tools.windows_input.type_unicode',type_text)
    with pytest.raises(RuntimeError,match='partial'):dictation.Session(TARGET,7).insert('hello',7)
    assert sent==['h']


@pytest.mark.parametrize('text',['','\x00oops','\x1b[31m','x'*20001,'\ud800'])
def test_invalid_dictation_cannot_reach_the_keyboard(text):
    with pytest.raises(ValueError):dictation.safe_text(text)


def test_stale_generation_cannot_write(monkeypatch):
    monkeypatch.setattr(dictation,'current_target',lambda:TARGET)
    with pytest.raises(RuntimeError,match='stopped'):dictation.Session(TARGET,7).insert('old transcript',6)


def test_bridge_dictation_is_private_and_requires_user_activation(monkeypatch):
    bridge=Bridge(SimpleNamespace(url='http://127.0.0.1:7860'),'secret')
    bridge._window=SimpleNamespace(get_current_url=lambda:'http://127.0.0.1:7860/companion')
    bridge._dictation=SimpleNamespace(insert=lambda text,generation:{'inserted':True},stop=lambda:None)
    with pytest.raises(ValueError):bridge.dictate('hello',3)
    bridge._activation=True
    assert bridge.dictate('hello',3)['inserted']
    bridge._window.get_current_url=lambda:'https://other.example/companion'
    with pytest.raises(ValueError):bridge.dictate('hello',3)


@pytest.mark.parametrize('password',[False,True])
def test_window_target_uses_accessibility_identity_and_refuses_passwords(monkeypatch,password):
    def pid(hwnd,ref):ref._obj.value=123;return 9
    def info(thread,ref):ref._obj.focus=456;return True
    def title(hwnd,buffer,size):buffer.value='Notes';return 5
    user=SimpleNamespace(GetForegroundWindow=lambda:789,GetWindowThreadProcessId=pid,
                         GetGUIThreadInfo=info,GetWindowTextW=title)
    monkeypatch.setattr(dictation,'os',SimpleNamespace(name='nt'))
    monkeypatch.setattr(dictation.ctypes,'WinDLL',lambda *args,**kw:user,raising=False)
    element=SimpleNamespace(Current=SimpleNamespace(IsPassword=password),GetRuntimeId=lambda:[42,17])
    monkeypatch.setattr(dictation,'_automation_class',lambda:SimpleNamespace(FocusedElement=element))
    if password:
        with pytest.raises(RuntimeError,match='regular text'):dictation.current_target()
    else:
        assert dictation.current_target()=={'window':789,'focus':456,'pid':123,'element_id':[42,17],'title':'Notes'}


def test_sketch_svg_escapes_text_and_uses_only_marks():
    from xml.etree import ElementTree as ET
    svg=export_svg(1920,1080,[{'kind':'text','points':[(10,20)],'color':'cyan','text':'<script>alert("x")</script>'},
                              {'kind':'box','points':[(80,90),(10,20)],'color':'yellow'},
                              {'kind':'arrow','points':[(1,2),(3,4)],'color':'pink'}])
    root=ET.fromstring(svg)
    assert root.find('{http://www.w3.org/2000/svg}text').text=='<script>alert("x")</script>'
    box=root.find('{http://www.w3.org/2000/svg}rect')
    assert box.attrib['width']=='70' and box.attrib['x']=='10'
    assert '<script>' not in svg and '<image' not in svg


@pytest.mark.skipif(os.name!='nt',reason='Real Windows GUI and Unicode input')
def test_real_windows_dictation_writes_unicode_into_selected_field(tmp_path):
    """Owned Tk app, actual foreground HWND, actual SendInput; no provider."""
    pytest.importorskip('clr',reason='Desktop Companion optional Windows dependency')
    import json, subprocess, sys, time
    script=tmp_path/'field.py'; result=tmp_path/'text.json'
    script.write_text('''import tkinter as tk,json,sys,ctypes
from ctypes import wintypes
r=tk.Tk();r.title("Apex dictation acceptance field");r.attributes("-topmost",True)
e=tk.Entry(r);e.pack();r.update();r.deiconify();r.lift();e.focus_force();r.update()
u=ctypes.WinDLL("user32");u.GetParent.argtypes=[wintypes.HWND];u.GetParent.restype=wintypes.HWND
h=u.GetParent(r.winfo_id()) or r.winfo_id();u.SetForegroundWindow.argtypes=[wintypes.HWND];u.SetForegroundWindow(h)
print("ready",flush=True)
def save():
 open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(e.get()));r.after(100,save)
r.after(100,save);r.mainloop()
''',encoding='utf-8')
    child=subprocess.Popen([sys.executable,'-u',str(script),str(result)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='ready'
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            target=dictation.current_target()
            if target['pid']==child.pid:break
            time.sleep(.05)
        assert target['pid']==child.pid,'Windows did not focus the acceptance field'
        dictation.Session(target,1).insert('Hello café 😀',1)
        while time.monotonic()<deadline:
            try:
                if json.loads(result.read_text())=='Hello café 😀':break
            except (OSError,ValueError):pass
            time.sleep(.05)
        assert json.loads(result.read_text())=='Hello café 😀'
    finally:
        child.terminate();child.wait(timeout=5)


@pytest.mark.skipif(os.name!='nt',reason='Real transparent Windows canvas')
def test_real_windows_canvas_edit_done_and_capture():
    import ctypes, json, queue, subprocess, sys, threading, time
    from ctypes import wintypes
    import mss
    from app.screen_overlay import configure_dpi
    configure_dpi()
    artifact_dir=os.environ.get('COMPANION_GUI_ARTIFACTS')
    def screenshot(name):
        if artifact_dir:
            from pathlib import Path
            from mss.tools import to_png
            path=Path(artifact_dir);path.mkdir(parents=True,exist_ok=True)
            with mss.MSS() as screen:image=screen.grab({'left':0,'top':0,'width':640,'height':480})
            to_png(image.rgb,image.size,output=str(path/name))
    child=subprocess.Popen([sys.executable,'-u','-m','app.screen_canvas','--frame',
                            json.dumps({'left':0,'top':0,'width':640,'height':480}),
                            '--parent-pid',str(os.getpid())],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    replies=queue.Queue()
    threading.Thread(target=lambda:replies.put(child.stdout.readline()),daemon=True).start()
    try:
        ready=json.loads(replies.get(timeout=15));assert ready['ready']
        user=ctypes.WinDLL('user32')
        get=user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else user.GetWindowLongW
        get.argtypes,get.restype=[wintypes.HWND,ctypes.c_int],ctypes.c_ssize_t
        user.GetWindowDisplayAffinity.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        affinity=wintypes.DWORD()
        assert user.GetWindowDisplayAffinity(ready['toolbar'],ctypes.byref(affinity))
        assert affinity.value==0x11,'Toolbar must stay out of questions'
        assert not get(ready['window'],-20)&0x20,'Edit must accept mouse strokes'
        user.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
        def move(x,y):
            point=wintypes.POINT(x,y)
            assert user.ClientToScreen(ready['canvas'],ctypes.byref(point))
            user.SetCursorPos(point.x,point.y)
        move(100,100);time.sleep(.05)
        user.mouse_event(0x2,0,0,0,0);time.sleep(.05)
        move(200,200);time.sleep(.1)
        user.mouse_event(0x4,0,0,0,0)
        time.sleep(.15)
        screenshot('canvas-edit.png')
        rect=wintypes.RECT();user.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        assert user.GetWindowRect(ready['done'],ctypes.byref(rect))
        user.SetCursorPos(rect.left+10,rect.top+10)
        time.sleep(.05)
        user.mouse_event(0x2,0,0,0,0);user.mouse_event(0x4,0,0,0,0)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline and not get(ready['window'],-20)&0x20:time.sleep(.05)
        screenshot('canvas-done.png')
        assert get(ready['window'],-20)&0x20,'Done must pass mouse input back to the app'
        assert user.GetWindowDisplayAffinity(ready['window'],ctypes.byref(affinity))
        assert affinity.value==0,'User marks must be visible in Companion screen questions'
        time.sleep(.2)
        with mss.MSS() as screen:shot=screen.grab({'left':148,'top':148,'width':5,'height':5})
        assert any(abs(r-103)<8 and abs(g-232)<8 and abs(b-249)<8 for r,g,b in
                   zip(shot.rgb[::3],shot.rgb[1::3],shot.rgb[2::3])),'The actual captured screen must contain the user pen stroke'
    finally:
        child.terminate();child.wait(timeout=5)
