"""Is this recording good enough to clone a voice from?

A cloned voice copies its reference: clipping, a hum, a quiet mumble or a room
echo all come back in every sentence it says, and a transcript that does not
match the words makes the clone worse than any of those. So a new voice is
measured before it is saved, and the person is told what to fix in plain words.

`problems` block saving; `warnings` do not.
"""
from __future__ import annotations

import io
import re
import wave
from difflib import SequenceMatcher

RATE = 24000                    # what the Qwen voice model works at
MIN_SECONDS, MAX_SECONDS = 6.0, 90.0
BEST = (12.0, 45.0)
MAX_BYTES = 12_000_000


class _Unusable(ValueError):
    pass


def decode(data: bytes):
    """Any browser or phone recording (webm/ogg/mp4/wav/mp3) -> mono float32 at RATE."""
    import av
    import numpy as np
    if not data:
        raise ValueError('The recording is empty.')
    if len(data) > MAX_BYTES:
        raise ValueError('The recording is too large; keep it under a minute and a half.')
    chunks, samples = [], 0
    try:
        with av.open(io.BytesIO(data)) as container:
            resampler = av.AudioResampler(format='s16', layout='mono', rate=RATE)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    array = converted.to_ndarray().reshape(-1)
                    samples += len(array)
                    if samples > (MAX_SECONDS + 5) * RATE:
                        raise _Unusable('The recording is longer than a minute and a half. 20–30 seconds is best.')
                    chunks.append(array)
    except _Unusable:
        raise
    except Exception as exc:                     # PyAV's errors subclass ValueError too
        raise ValueError('That file is not audio Apex can read. Record again, or use a WAV, MP3, OGG or M4A file.') from exc
    if not chunks:
        raise ValueError('The recording has no sound in it.')
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _frames_db(audio, size):
    import numpy as np
    n = len(audio) // size
    if n == 0:
        return np.array([-120.0])
    frames = audio[: n * size].reshape(n, size)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-6))


def trim(audio, pad=0.25):
    """Cut the silence before the first word and after the last, keeping a little air."""
    size = int(RATE * 0.03)
    db = _frames_db(audio, size)
    floor = float(sorted(db)[len(db) // 10])
    loud = [i for i, v in enumerate(db) if v > max(floor + 10, -50)]
    if not loud:
        return audio
    start = max(0, loud[0] * size - int(pad * RATE))
    end = min(len(audio), (loud[-1] + 1) * size + int(pad * RATE))
    return audio[start:end]


def _words(text):
    return re.findall(r"[a-z0-9']+", text.lower().replace('’', "'"))


def match(said: str, heard: str) -> float:
    """How closely the words heard in the recording follow the transcript (0-1)."""
    a, b = _words(said), _words(heard)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def analyse(audio, transcript: str = '', heard: str | None = None) -> dict:
    import numpy as np
    seconds = len(audio) / RATE
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    clipped = float((np.abs(audio) >= 0.985).mean()) if len(audio) else 0.0
    db = np.sort(_frames_db(audio, int(RATE * 0.03)))
    speech_db = float(db[int(len(db) * 0.95) - 1]) if len(db) > 1 else float(db[0])
    noise_db = float(db[len(db) // 10])
    snr = speech_db - noise_db
    problems, warnings = [], []
    if seconds < MIN_SECONDS:
        problems.append(f'Too short ({seconds:.0f} s). Read the whole script: 20–30 seconds is best.')
    elif seconds > MAX_SECONDS:
        problems.append(f'Too long ({seconds:.0f} s). Keep it under a minute and a half; 20–30 seconds is best.')
    elif not BEST[0] <= seconds <= BEST[1]:
        warnings.append(f'{seconds:.0f} seconds. It works, but 20–30 seconds gives the most natural voice.')
    if clipped > 0.001:
        problems.append('The sound is distorting (too loud for the mic). Move back a little or turn the mic level down, then record again.')
    if speech_db < -38:
        problems.append('Too quiet. Move closer to the mic or speak up a little.')
    elif speech_db < -30:
        warnings.append('A little quiet. Closer to the mic would sound better.')
    if snr < 12:
        problems.append('Too much background noise. Find a quieter room (no fan, TV or music) and record again.')
    elif snr < 20:
        warnings.append('Some background noise. It will be cloned too; a quieter room sounds better.')
    result = dict(seconds=round(seconds, 1), peak_db=round(20 * np.log10(max(peak, 1e-6)), 1),
                  speech_db=round(speech_db, 1), noise_db=round(noise_db, 1), snr_db=round(snr, 1),
                  clipped_pct=round(clipped * 100, 3), heard=heard, match=None,
                  problems=problems, warnings=warnings)
    if not transcript.strip():
        problems.append('Type exactly what you said in the recording.')
    elif heard is not None:
        score = match(transcript, heard)
        result['match'] = round(score, 2)
        if score < 0.6:
            problems.append("The words don't match the transcript. Fix the text to what you actually said (Apex heard it below), or read the script again.")
        elif score < 0.85:
            warnings.append('A few words differ from the transcript. Make the text exactly what you said; a wrong transcript is the main cause of a bad clone.')
    return result


def to_wav(audio) -> bytes:
    import numpy as np
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes())
    return out.getvalue()
