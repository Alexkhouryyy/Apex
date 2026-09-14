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
                _speak_voicebox(text)
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


def _speak_voicebox(text):
    import asyncio
    import io
    import wave
    import numpy as np
    import sounddevice as sd
    import time
    from voice.voicebox import synthesize
    audio = asyncio.run(synthesize(text))
    if interrupt_mod.is_interrupted():
        return
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        if wav.getsampwidth() != 2:
            raise ValueError('Voicebox terminal playback requires PCM16 WAV.')
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
        samples = samples.reshape(-1, wav.getnchannels())
        sd.play(samples, wav.getframerate())
        try:
            while sd.get_stream().active and not interrupt_mod.is_interrupted():
                time.sleep(0.05)
        finally:
            sd.stop()
