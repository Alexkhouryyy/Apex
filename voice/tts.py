import threading
import config
from voice import interrupt as interrupt_mod

_engine = None
_lock = threading.Lock()
_speaking = threading.Event()


def _get_pyttsx3():
    global _engine
    if _engine is None:
        import pyttsx3
        _engine = pyttsx3.init()
        _engine.setProperty("rate", config.TTS_RATE)
        voices = _engine.getProperty("voices")
        for v in voices:
            if "english" in v.name.lower() or "en" in v.id.lower():
                _engine.setProperty("voice", v.id)
                break
    return _engine


def speak(text: str, interruptible: bool = True) -> bool:
    """Speak text aloud. Returns True if completed, False if interrupted."""
    if not text.strip():
        return True

    with _lock:
        _speaking.set()
        interrupt_mod.reset()

        engine = _get_pyttsx3() if config.TTS_ENGINE == "pyttsx3" else None
        stop_watcher = (
            interrupt_mod.start_listening_for_interrupt(tts_engine=engine)
            if interruptible else lambda: None
        )

        try:
            if config.TTS_ENGINE == "voicebox":
                _speak_voicebox_or_fallback(text)
            elif config.TTS_ENGINE == "openai" and config.OPENAI_API_KEY:
                _speak_openai(text)
            elif config.TTS_ENGINE == "elevenlabs" and config.ELEVENLABS_API_KEY:
                _speak_elevenlabs(text)
            else:
                _speak_pyttsx3(text)
        finally:
            stop_watcher()
            _speaking.clear()

        return not interrupt_mod.is_interrupted()


def is_speaking() -> bool:
    return _speaking.is_set()


def _speak_pyttsx3(text: str) -> None:
    engine = _get_pyttsx3()
    engine.say(text)
    engine.runAndWait()


def _speak_openai(text: str) -> None:
    import subprocess
    from openai import OpenAI
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    resp = client.audio.speech.create(
        model="tts-1",
        voice=config.OPENAI_TTS_VOICE,
        input=text,
    )
    audio = resp.content
    proc = subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-"],
        input=audio,
        capture_output=True,
    )
    if proc.returncode != 0:
        _speak_pyttsx3(text)


def _speak_elevenlabs(text: str) -> None:
    import requests
    import subprocess

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{config.ELEVENLABS_VOICE_ID}/stream"
    headers = {"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    resp = requests.post(url, json=payload, headers=headers, stream=True)
    resp.raise_for_status()

    audio = b"".join(resp.iter_content(chunk_size=4096))
    proc = subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-"],
        input=audio,
        capture_output=True,
    )
    if proc.returncode != 0:
        _speak_pyttsx3(text)


_fallback_noted = False
_celine_down_until = 0.0
CELINE_COOLDOWN = 30.0      # after a failure, speak with the PC voice for this long before asking Celine again
SYNTH_BUDGET = 20.0         # seconds one sentence may take to synthesize before the PC voice takes over
BUSY_RETRY_SECONDS = 4.0    # Celine busy with another reply: wait this long for her before falling back
MAX_CHUNK = 4000            # voice/voicebox.synthesize accepts at most this many characters


class PlaybackFailed(RuntimeError):
    """Celine's audio was made but could not be played. `started` says whether
    any of it may already have been heard."""
    def __init__(self, message, started):
        super().__init__(message)
        self.started = started


def _chunks(text, limit=MAX_CHUNK):
    """Split long replies at sentence ends so each piece fits Celine's limit."""
    text = text.strip()
    pieces = []
    while len(text) > limit:
        cut = max(text.rfind(mark, 0, limit) for mark in ('. ', '! ', '? ', '\n'))
        if cut <= 0:
            cut = text.rfind(' ', 0, limit)          # no sentence end: at least never cut a word
        cut = cut + 1 if cut > 0 else limit
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


class CelineBusy(RuntimeError):
    """Celine is up but making another reply; not an outage."""


def _is_busy(exc) -> bool:
    text = str(exc)
    return isinstance(exc, ValueError) and ('already generating' in text or text.startswith('Voicebox 409'))


def _synthesize(text):
    """Celine's audio for `text`, within SYNTH_BUDGET, cancelled by barge-in;
    retries briefly while she is busy with another reply, then raises
    CelineBusy. Safe to call from inside a running event loop."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _synthesize_here(text)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_synthesize_here, text).result()


def _synthesize_here(text):
    import asyncio
    import time
    from voice.voicebox import synthesize

    async def bounded():
        task = asyncio.ensure_future(synthesize(text))
        end = asyncio.get_running_loop().time() + SYNTH_BUDGET
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.2)
                if done:
                    return task.result()
                if interrupt_mod.is_interrupted():
                    raise InterruptedError('interrupted')
                if asyncio.get_running_loop().time() > end:
                    raise TimeoutError(f'no audio after {SYNTH_BUDGET:.0f} s')
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    busy_until = time.monotonic() + BUSY_RETRY_SECONDS
    while True:
        try:
            return asyncio.run(bounded())
        except ValueError as exc:
            if not _is_busy(exc):
                raise
            if time.monotonic() < busy_until and not interrupt_mod.is_interrupted():
                time.sleep(0.5)
                continue
            raise CelineBusy(str(exc)) from exc


def _play(audio):
    import io
    import time
    import wave
    started = False
    try:
        import numpy as np
        import sounddevice as sd          # can fail to load (PortAudio missing): nothing was heard
        with wave.open(io.BytesIO(audio), 'rb') as wav:
            if wav.getsampwidth() != 2:
                raise ValueError('Voicebox terminal playback requires PCM16 WAV.')
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
            samples = samples.reshape(-1, wav.getnchannels())
        sd.play(samples, wav.getframerate())
        try:
            while sd.get_stream().active and not interrupt_mod.is_interrupted():
                started = True                # the device is really playing it now
                time.sleep(0.05)
        finally:
            sd.stop()
    except Exception as exc:
        raise PlaybackFailed(f'{type(exc).__name__}: {exc}', started) from exc


def _speak_voicebox(text):
    """Celine's voice only, no fallback (kept for callers that want exactly that)."""
    audio = _synthesize(text)
    if not interrupt_mod.is_interrupted():
        _play(audio)


def _speak_system(text):
    """The PC's own voice, stoppable by barge-in and the stop gesture like Celine's.
    Never raises: when even this voice fails, say so in the log instead of
    killing the caller (a streamed reply, the resident loop)."""
    try:
        engine = _get_pyttsx3()
    except Exception as exc:
        print(f"[TTS] Neither Celine's voice nor this PC's voice could speak ({type(exc).__name__}: "
              f"{str(exc)[:120]}). Unspoken: {text[:80]!r}")
        return
    done = threading.Event()

    def watch():
        while not done.wait(0.05):
            if interrupt_mod.is_interrupted():
                try:
                    engine.stop()
                except Exception:
                    pass
                return
    threading.Thread(target=watch, daemon=True, name="TTSFallbackInterrupt").start()
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as exc:
        print(f"[TTS] Neither Celine's voice nor this PC's voice could speak ({type(exc).__name__}: "
              f"{str(exc)[:120]}). Unspoken: {text[:80]!r}")
        global _engine
        try:
            engine.stop()
        except Exception:
            pass
        _engine = None                        # a wedged engine is rebuilt for the next sentence
    finally:
        done.set()


def _note_outage(exc):
    global _fallback_noted
    if not _fallback_noted:
        if isinstance(exc, TimeoutError):
            print(f"[TTS] Celine's voice is slow right now (no audio after {SYNTH_BUDGET:.0f} s); "
                  "using this PC's own voice for a while.")
        else:
            print(f"[TTS] Celine's voice is unavailable ({type(exc).__name__}: {str(exc)[:120]}); "
                  "using this PC's own voice until it answers again.")
        _fallback_noted = True


def _speak_voicebox_or_fallback(text):
    """Celine's local voice when it answers; this PC's own voice when it doesn't.

    The local server can be warming up, busy, out of GPU memory or stopped.
    Saying the reply in the system voice beats saying nothing. A failure starts a
    short cool-down so later sentences don't each wait for the same failure, and
    a sentence is never said twice: once Celine's audio has started playing, a
    playback error is logged, not repeated in the PC voice."""
    import time
    global _fallback_noted, _celine_down_until
    for piece in _chunks(text):
        if interrupt_mod.is_interrupted():
            return
        if time.monotonic() < _celine_down_until:
            _speak_system(piece)
            continue
        try:
            audio = _synthesize(piece)
        except CelineBusy:
            # Up, but making another reply: this sentence in the PC voice, no
            # cool-down and no outage notice, so the next one asks her again.
            if not interrupt_mod.is_interrupted():
                _speak_system(piece)
            continue
        except Exception as exc:
            if interrupt_mod.is_interrupted():
                return
            _celine_down_until = time.monotonic() + CELINE_COOLDOWN
            _note_outage(exc)
            _speak_system(piece)
            continue
        if interrupt_mod.is_interrupted():
            return
        try:
            _play(audio)
        except PlaybackFailed as exc:
            if exc.started:
                print(f"[TTS] Celine's audio stopped part-way ({exc}); not repeating the sentence.")
            else:
                # Nothing was heard (audio device or library missing): same as an outage.
                _celine_down_until = time.monotonic() + CELINE_COOLDOWN
                _note_outage(exc)
                _speak_system(piece)
            continue
        _celine_down_until = 0.0
        _fallback_noted = False
