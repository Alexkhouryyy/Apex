"""Measure the hardware Apex depends on, on THIS laptop, and keep a dated report.

    python -m tools.hardware_check            # quick: nothing to do but wait (~20 s)
    python -m tools.hardware_check --demo     # also: show your hand, speak, listen
    Test-Apex-Hardware.cmd                    # the same, from Explorer

`tools/doctor.py` answers "is Apex set up right?" (keys, settings, services).
This answers "does the hardware under it work, and how well?": webcam and hand
detection, microphone level, speakers, GPU memory for Celine's voice, virtual
memory (the paging-file error 1455), disk space, display scale, screen capture,
Celine's voice speed and the PC's own voice.

It follows the doctor's rule: report only what was OBSERVED. A camera counts
when frames arrived, a voice when audio came back. Nothing is changed.

Every run is saved under %LOCALAPPDATA%\\Apex\\hardware\\ (text + JSON) and the
console shows what changed since the previous run, so "hands got worse after
the update" or "the GPU has less free memory with Chrome open" is a fact you
can compare, not a feeling.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OK, WARN, BAD, SKIP = "ok", "warn", "bad", "skip"
MARK = {OK: "[ ok ]", WARN: "[warn]", BAD: "[FAIL]", SKIP: "[skip]"}
CHECK_TIMEOUT = 45          # seconds one check may take before it is reported as stuck
VOICE_GPU_MB = 4000         # Celine's voice model wants about this much free GPU memory
VOICE_MEMORY_GB = 6         # RAM + free page file needed to load it (paging-file error 1455)
WINDOWS = os.name == "nt"

CHECKS: list = []


def check(order: int, key: str, title: str, demo_only: bool = False):
    def deco(fn):
        CHECKS.append({"order": order, "key": key, "title": title, "fn": fn, "demo_only": demo_only})
        return fn
    return deco


class Ctx:
    """What a check may use: demo mode, and a way to ask the person something."""
    def __init__(self, demo=False, ask=None, say=None):
        self.demo = demo
        self.ask = ask or (lambda question: input(question + " ").strip().lower())
        self.say = say or (lambda text: print("         " + text, flush=True))


def _gb(n):
    return round(n / 1e9, 1)


# ── The machine ──────────────────────────────────────────────────────────────

@check(10, "memory", "Memory for Celine's voice (RAM + paging file)")
def _memory(ctx):
    import psutil
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    headroom = vm.available + sw.free
    m = {"ram_total_gb": _gb(vm.total), "ram_free_gb": _gb(vm.available),
         "pagefile_total_gb": _gb(sw.total), "pagefile_free_gb": _gb(sw.free)}
    line = (f"RAM {m['ram_free_gb']} of {m['ram_total_gb']} GB free; "
            f"paging file {m['pagefile_free_gb']} of {m['pagefile_total_gb']} GB free")
    if headroom < VOICE_MEMORY_GB * 1e9:
        return WARN, (line + f"\n-> under {VOICE_MEMORY_GB} GB to spare: loading Celine's voice can fail with "
                      "'paging file is too small' (error 1455). Close big programs, or enlarge the paging "
                      "file (docs/CELINE.md)."), m
    return OK, line, m


@check(11, "disks", "Free disk space")
def _disks(ctx):
    drives = {}
    system = os.environ.get("SystemDrive", "C:") + "\\" if WINDOWS else "/"
    drives["system"] = system
    library = Path(os.environ.get("APEX_APOCALYPSE_HOME", r"D:\Apex-Apocalypse" if WINDOWS else "/"))
    if library.anchor and Path(library.anchor).exists() and library.anchor.lower() != system.lower():
        drives["library"] = library.anchor
    m, lines, state = {}, [], OK
    for role, root in drives.items():
        u = shutil.disk_usage(root)
        m[f"{role}_free_gb"] = _gb(u.free)
        lines.append(f"{root} {_gb(u.free)} GB free of {_gb(u.total)}")
        if role == "system" and u.free < 15e9:
            state = WARN
            lines.append(f"-> under 15 GB free on {root}: Windows, Docker and the paging file need room.")
    if "library" not in drives and library.anchor.lower() != system.lower():
        lines.append(f"the offline library drive ({library.anchor}) is not connected")
    return state, "\n".join(lines), m


@check(12, "gpu", "GPU (for Celine's voice)")
def _gpu(ctx):
    exe = shutil.which("nvidia-smi")
    if not exe:
        return WARN, "no NVIDIA GPU found (nvidia-smi missing): Celine's local voice needs one.", {}
    out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.used,driver_version",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
    if out.returncode != 0 or not out.stdout.strip():
        return BAD, f"nvidia-smi failed: {out.stderr.strip()[:200]}", {}
    name, total, used, driver = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
    free = int(float(total)) - int(float(used))
    m = {"gpu_name": name, "gpu_total_mb": int(float(total)), "gpu_free_mb": free}
    line = f"{name}: {free} of {int(float(total))} MB free (driver {driver})"
    if free < VOICE_GPU_MB:
        return WARN, (line + f"\n-> under {VOICE_GPU_MB} MB free: Celine's voice may not load. "
                      "Close what nvidia-smi lists as using the GPU."), m
    return OK, line, m


@check(13, "display", "Displays and scale (where the Companion bubble goes)")
def _display(ctx):
    if not WINDOWS:
        return SKIP, "Windows only", {}
    import ctypes
    from ctypes import wintypes
    user, shcore = ctypes.windll.user32, ctypes.windll.shcore
    try:
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass
    found = []

    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

    def each(monitor, _dc, rect, _data):
        x, y = wintypes.UINT(), wintypes.UINT()
        shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(x), ctypes.byref(y))
        r = rect.contents
        found.append((r.right - r.left, r.bottom - r.top, round(x.value / 96 * 100)))
        return True
    user.EnumDisplayMonitors(None, None, proc(each), 0)
    if not found:
        return BAD, "Windows reported no displays", {}
    lines = [f"monitor {i + 1}: {w}x{h} at {scale}%" for i, (w, h, scale) in enumerate(found)]
    m = {"monitors": len(found), "scale_percent": found[0][2]}
    if len({s for _, _, s in found}) > 1:
        lines.append("-> monitors use different scales: the Companion bubble can sit slightly off on the "
                     "secondary one.")
        return WARN, "\n".join(lines), m
    return OK, "\n".join(lines), m


@check(14, "screen", "Screen capture (what Celine sees)")
def _screen(ctx):
    import mss
    started = time.monotonic()
    with mss.mss() as grab:
        shot = grab.grab(grab.monitors[1])
    ms = round((time.monotonic() - started) * 1000)
    m = {"capture_ms": ms, "capture_size": f"{shot.width}x{shot.height}"}
    return OK, f"captured {shot.width}x{shot.height} in {ms} ms", m


# ── Camera and hands ─────────────────────────────────────────────────────────

def _open_camera(index):
    os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")     # a missing camera is reported once, plainly
    import cv2
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if WINDOWS else cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    return cap


@check(20, "camera", "Webcam")
def _camera(ctx):
    import config
    index = getattr(config, "CAMERA_DEVICE_INDEX", 0)
    cap = _open_camera(index)
    if cap is None:
        return WARN, (f"camera {index} would not open. Either there is none, or something is using it "
                      "(Apex's own hand tracker holds it while Apex runs: that is fine)."), {}
    try:
        frames, size, started = 0, "", time.monotonic()
        while time.monotonic() - started < 2.0:
            ok, frame = cap.read()
            if ok and frame is not None:
                frames += 1
                size = f"{frame.shape[1]}x{frame.shape[0]}"
        fps = round(frames / max(time.monotonic() - started, 1e-6), 1)
    finally:
        cap.release()
    m = {"camera_fps": fps, "camera_size": size}
    if frames == 0:
        return BAD, f"camera {index} opened but sent no frames (driver, privacy shutter, or another app).", m
    if fps < 10:
        return WARN, f"camera {index}: {size} at {fps} fps: hand tracking wants 15+.", m
    return OK, f"camera {index}: {size} at {fps} fps", m


@check(21, "hands", "Hand detection (show your hand)", demo_only=True)
def _hands(ctx):
    import config
    from agent import handtrack
    ok, why = handtrack.available()
    if not ok:
        return BAD, why, {}
    if not handtrack._model_on_disk():
        handtrack.ensure_model()
    if not handtrack._model_on_disk():
        return BAD, "the hand model could not be downloaded; check the connection and run again.", {}
    import cv2
    import mediapipe as mp
    index = getattr(config, "CAMERA_DEVICE_INDEX", 0)
    cap = _open_camera(index)
    if cap is None:
        return WARN, f"camera {index} would not open (quit Apex first: its tracker holds the camera).", {}
    landmarker, used, _note = handtrack.build_landmarker(num_hands=2)
    ctx.say("Hold one hand up in front of the camera and keep it there for 5 seconds...")
    frames = seen = 0
    started = time.monotonic()
    try:
        while time.monotonic() - started < 5.0:
            ok_, frame = cap.read()
            if not ok_ or frame is None:
                continue
            frames += 1
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            result = landmarker.detect_for_video(image, int((time.monotonic() - started) * 1000) + frames)
            if result.hand_landmarks:
                seen += 1
    finally:
        cap.release()
        try:
            landmarker.close()
        except Exception:
            pass
    rate = round(100 * seen / frames) if frames else 0
    m = {"hand_seen_percent": rate, "hand_frames": frames, "hand_inference": used}
    line = f"hand seen in {seen} of {frames} frames ({rate}%), inference on {used}"
    if frames == 0:
        return BAD, "no frames arrived from the camera", m
    if rate < 60:
        return WARN, line + "\n-> under 60%: more light, hand closer, or a plainer background.", m
    return OK, line, m


# ── Sound ────────────────────────────────────────────────────────────────────

@check(30, "microphone", "Microphone")
def _microphone(ctx):
    import numpy as np
    import sounddevice as sd
    device = sd.query_devices(kind="input")
    seconds = 3.0 if ctx.demo else 1.0
    if ctx.demo:
        ctx.say("Say a sentence out loud now (3 seconds)...")
    else:
        ctx.say("Listening for 1 second to measure the noise level (nothing is saved).")
    rate = int(device.get("default_samplerate") or 16000)
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32")
    sd.wait()
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    to_db = lambda v: round(20 * np.log10(max(v, 1e-9)), 1)
    m = {"mic_name": device.get("name", ""), "mic_peak_db": to_db(peak), "mic_rms_db": to_db(rms)}
    line = f"{device.get('name', 'default input')}: peak {m['mic_peak_db']} dBFS, level {m['mic_rms_db']} dBFS"
    if peak < 1e-4:
        return BAD, line + "\n-> silence: the microphone is muted, blocked in Windows privacy settings, or not the default.", m
    if ctx.demo and m["mic_peak_db"] < -30:
        return WARN, line + "\n-> your voice was quiet: move closer or raise the input level in Windows sound settings.", m
    return OK, line, m


@check(31, "speakers", "Speakers (you will hear a short tone)", demo_only=True)
def _speakers(ctx):
    import numpy as np
    import sounddevice as sd
    device = sd.query_devices(kind="output")
    rate = int(device.get("default_samplerate") or 44100)
    t = np.arange(int(0.6 * rate)) / rate
    sd.play((0.2 * np.sin(2 * np.pi * 440 * t)).astype("float32"), rate)
    sd.wait()
    heard = ctx.ask("Did you hear a tone? (y/n)").startswith("y")
    m = {"speaker_name": device.get("name", ""), "tone_heard": heard}
    if not heard:
        return BAD, f"{device.get('name', 'default output')}: the tone played but was not heard (volume, mute, wrong device).", m
    return OK, f"{device.get('name', 'default output')}: tone heard", m


@check(32, "celine_voice", "Celine's voice (local server)")
def _celine_voice(ctx):
    import urllib.request
    url = "http://127.0.0.1:17494"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/health", timeout=3) as r:
            health = json.loads(r.read(65536))
    except Exception as e:
        return WARN, (f"not running ({type(e).__name__}). Start-Apex-All starts it; this is only a problem if "
                      "Apex is running and she should be speaking."), {}
    if health.get("service") != "apex-qwen":
        return WARN, "something else answers on port 17494, not Celine's voice server.", {}
    if not health.get("model_loaded"):
        return WARN, "running but still warming up; run this again in a few minutes.", {}
    import asyncio
    import config
    from voice.voicebox import synthesize
    saved = config.VOICEBOX_URL, config.VOICEBOX_PROFILE
    config.VOICEBOX_URL, config.VOICEBOX_PROFILE = url, config.VOICEBOX_PROFILE or "celine"
    try:
        started = time.monotonic()
        audio = asyncio.run(asyncio.wait_for(synthesize("This is a hardware check."), 60))
    finally:
        config.VOICEBOX_URL, config.VOICEBOX_PROFILE = saved
    seconds = round(time.monotonic() - started, 2)
    m = {"voice_seconds": seconds, "voice_bytes": len(audio)}
    if ctx.demo:
        from voice import tts
        tts._play(audio)
    line = f"ready; a short sentence took {seconds} s to make"
    if seconds > 6:
        return WARN, line + "\n-> slow: the GPU may be shared with other programs.", m
    return OK, line, m


@check(33, "pc_voice", "The PC's own voice (the fallback)")
def _pc_voice(ctx):
    import pyttsx3
    engine = pyttsx3.init()
    voices = engine.getProperty("voices") or []
    m = {"pc_voices": len(voices)}
    if not voices:
        return BAD, "no system voices installed: the fallback could not speak.", m
    if ctx.demo:
        engine.say("This is the PC's own voice.")
        engine.runAndWait()
    return OK, f"{len(voices)} voice(s) available, e.g. {getattr(voices[0], 'name', '?')}", m


# ── Apex itself ──────────────────────────────────────────────────────────────

@check(40, "apex", "Apex and its desktop pieces")
def _apex(ctx):
    import urllib.request
    import config
    port = getattr(config, "DASHBOARD_PORT", 7860)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            running = json.loads(r.read(65536)).get("service") == "apex"
    except Exception:
        running = False
    missing = []
    for mod, what in (("webview", "the desktop Companion window"), ("pynput", "global hotkeys")):
        try:
            __import__(mod)
        except Exception:
            missing.append(f"{mod} ({what})")
    m = {"apex_running": running}
    line = ("Apex is running" if running else "Apex is not running") + f" (dashboard port {port})"
    if missing:
        return WARN, line + "\nnot installed: " + "; ".join(missing) + "\n-> Setup-Apex-Screen-Companion.cmd", m
    return OK, line + "; Companion window and hotkeys are installed", m


@check(41, "internet", "Internet (for cloud models)")
def _internet(ctx):
    import urllib.error
    import urllib.request
    started = time.monotonic()
    try:
        urllib.request.urlopen(urllib.request.Request("https://api.anthropic.com", method="HEAD"), timeout=8)
    except urllib.error.HTTPError:
        pass                                    # an HTTP answer still means the line works
    except Exception as e:
        return WARN, f"no answer from api.anthropic.com ({type(e).__name__}): cloud models will not work right now.", {}
    ms = round((time.monotonic() - started) * 1000)
    m = {"internet_ms": ms}
    if ms > 2000:
        return WARN, f"answered in {ms} ms: slow; replies will start late.", m
    return OK, f"api.anthropic.com answered in {ms} ms", m


# ── Running and reporting ────────────────────────────────────────────────────

def run_check(entry, ctx, timeout=CHECK_TIMEOUT):
    """One check, never fatal: an exception or a hang becomes its own result."""
    box = {}

    def go():
        try:
            box["r"] = entry["fn"](ctx)
        except ModuleNotFoundError as e:
            box["r"] = (SKIP, f"{e.name} is not installed", {})
        except Exception as e:
            box["r"] = (BAD, f"the check itself failed: {type(e).__name__}: {str(e)[:200]}", {})
    worker = threading.Thread(target=go, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return BAD, f"took longer than {timeout} s (a device or driver is not answering)", {}
    return box["r"]


def report_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".apex")
    return Path(base) / "Apex" / "hardware"


def compare(previous: dict, current: dict) -> list[str]:
    """Plain-language changes in the numbers since the previous report."""
    lines = []
    old = {k: v for r in previous.get("results", []) for k, v in r.get("measures", {}).items()}
    for r in current["results"]:
        for k, v in r.get("measures", {}).items():
            before = old.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(before, (int, float)) \
                    and not isinstance(before, bool) and before != v:
                lines.append(f"{k}: {before} -> {v}")
    prev_state = {r["key"]: r["state"] for r in previous.get("results", [])}
    for r in current["results"]:
        if r["key"] in prev_state and prev_state[r["key"]] != r["state"]:
            lines.append(f"{r['title']}: {prev_state[r['key']]} -> {r['state']}")
    return lines


def main(argv=None, ctx=None, out_dir=None) -> int:
    parser = argparse.ArgumentParser(description="Measure the hardware Apex depends on.")
    parser.add_argument("--demo", action="store_true", help="also the hands-on checks: hand, voice, tone")
    parser.add_argument("--only", action="append", help="run just these checks (by key)")
    args = parser.parse_args(argv)
    ctx = ctx or Ctx(demo=args.demo)
    print("\n  Apex hardware check" + (" (with demo)" if args.demo else "") + "\n", flush=True)
    results, worst = [], OK
    for entry in sorted(CHECKS, key=lambda e: e["order"]):
        if args.only and entry["key"] not in args.only:
            continue
        if entry["demo_only"] and not args.demo:
            continue
        print(f"  ...  {entry['title']}", flush=True)
        state, detail, measures = run_check(entry, ctx)
        print(f"  {MARK[state]} {entry['title']}", flush=True)
        for line in str(detail).split("\n"):
            print(f"         {line}", flush=True)
        print(flush=True)
        results.append({"key": entry["key"], "title": entry["title"], "state": state,
                        "detail": detail, "measures": measures})
        if state == BAD:
            worst = BAD
        elif state == WARN and worst == OK:
            worst = WARN
    stamp = datetime.now()
    report = {"when": stamp.isoformat(timespec="seconds"), "demo": args.demo, "results": results}
    folder = Path(out_dir) if out_dir else report_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        earlier = sorted(folder.glob("*.json"))
        changes = compare(json.loads(earlier[-1].read_text(encoding="utf-8")), report) if earlier else []
        name = stamp.strftime("%Y-%m-%d_%H%M%S")
        (folder / f"{name}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        text = "\n".join(f"{MARK[r['state']]} {r['title']}\n         " + str(r["detail"]).replace("\n", "\n         ")
                         for r in results)
        (folder / f"{name}.txt").write_text(f"Apex hardware check, {report['when']}\n\n{text}\n", encoding="utf-8")
        if changes:
            print("  Changed since the previous check:", flush=True)
            for line in changes:
                print(f"    {line}", flush=True)
            print(flush=True)
        print(f"  Report saved: {folder / (name + '.txt')}\n", flush=True)
    except OSError as e:
        print(f"  Could not save the report ({e}).\n", flush=True)
    if worst == BAD:
        print("  A [FAIL] above is hardware Apex needs that is not working. Each says what to try.\n")
    elif worst == WARN:
        print("  Nothing is broken; the [warn] lines are worth a look.\n")
    else:
        print("  All hardware checked out.\n")
    return 1 if worst == BAD else 0


if __name__ == "__main__":
    raise SystemExit(main())
