"""Bounded browser audio transcription using local Whisper by default."""
import io
import threading
import config

_lock = threading.Lock()


def decode_bounded(data):
    import av
    import numpy as np
    chunks, samples = [], 0
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                array = converted.to_ndarray().reshape(-1)
                samples += len(array)
                if samples > 65 * 16000:
                    raise ValueError('Audio exceeds the 65-second limit.')
                chunks.append(array)
    if not chunks:
        raise ValueError('Audio contains no decodable samples.')
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe(data: bytes, name: str, engine: str) -> str:
    audio = decode_bounded(data)
    if engine == 'openai':
        if not config.OPENAI_API_KEY:
            raise ValueError('OpenAI transcription needs OPENAI_API_KEY. Select local transcription instead.')
        from openai import OpenAI
        with OpenAI(api_key=config.OPENAI_API_KEY) as client:
            result = client.audio.transcriptions.create(model='whisper-1', file=(name, data))
        return (result.text or '').strip()
    if engine != 'local':
        raise ValueError('Unknown transcription engine.')
    # Serialise model loading/inference; consume the segment generator inside lock.
    with _lock:
        from voice.stt import _get_model
        segments, _ = _get_model().transcribe(audio, beam_size=1,
            vad_filter=True, condition_on_previous_text=False,
            vad_parameters={'min_silence_duration_ms':500})
        return ' '.join(s.text.strip() for s in segments if getattr(s, 'no_speech_prob', 0) < .6).strip()
