"""Isolated Windows streaming benchmark. Does not change Apex's voice provider."""
import argparse
import json
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


def setup():
    env = Path.home() / 'apex-qwen-fast-env'
    python = env / 'Scripts' / 'python.exe'
    for port in (17493, 17494):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError('Close Voicebox and the Apex/Qwen launcher first to free GPU memory.')
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(env)], check=True)
    # v2: pins transformers==5.16.1. Bumped so an existing v1 install (which
    # got 5.17.0) runs the pip step again and is corrected; everything is in
    # pip's cache by then, so only transformers changes.
    marker = env / 'apex-fast-setup-v2.json'
    if not marker.exists():
        print('Installing a separate GPU environment. This needs several GB of disk space.', flush=True)
        pip = [str(python), '-m', 'pip', '--isolated', 'install', '--timeout', '120', '--retries', '5']
        subprocess.run(pip + ['torch==2.9.1', 'torchaudio==2.9.1', '--index-url',
                             'https://download.pytorch.org/whl/cu128'], check=True)
        constraints = env / 'apex-constraints.txt'
        # transformers 5.17.0 breaks qwen-tts-hf 0.1.1.post1: its weight
        # initialiser now looks up the shared "default" RoPE function before
        # the module's own, qwen-tts-hf replaces that shared function with one
        # that reads config.rope_theta, and Mimi's config has no rope_theta —
        # "'MimiConfig' object has no attribute 'rope_theta'" on load. 5.16.1
        # uses Mimi's own function. Reproduced on both versions before pinning.
        constraints.write_text('torch==2.9.1\ntorchaudio==2.9.1\ntransformers==5.16.1\n',
                               encoding='utf-8')
        subprocess.run(pip + ['faster-qwen3-tts==0.4.0', 'sounddevice', 'soundfile',
                             '-c', str(constraints), '--index-url', 'https://pypi.org/simple'], check=True)
        subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
        marker.write_text('{"setup": 2}', encoding='utf-8')
    subprocess.run([str(python), str(Path(__file__).resolve()), '--run'], check=True)


class Player:
    """Write queued chunks on a separate thread so playback cannot block generation."""
    def __init__(self, rate):
        self.rate = rate
        self.queue = queue.Queue()
        self.error = None
        self.first_write = None
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def work(self):
        try:
            import sounddevice as sd
            with sd.OutputStream(samplerate=self.rate, channels=1, dtype='float32') as stream:
                while True:
                    chunk = self.queue.get()
                    if chunk is None:
                        break
                    if self.first_write is None:
                        self.first_write = time.perf_counter()
                    stream.write(chunk.reshape(-1, 1))
        except Exception as exc:
            self.error = exc

    def close(self):
        self.queue.put(None)
        self.thread.join(timeout=120)
        if self.thread.is_alive():
            raise RuntimeError('Audio device did not finish playback within 120 seconds.')
        if self.error:
            raise RuntimeError(f'Playback failed: {self.error}')


def measure(chunks, player_factory=Player):
    import numpy as np
    started = time.perf_counter()
    audio = []
    player = None
    first = None
    rate = None
    try:
        for chunk, sr, _ in chunks:
            chunk = np.asarray(chunk, dtype=np.float32).reshape(-1).copy()
            if not len(chunk):
                continue
            if first is None:
                first = time.perf_counter() - started
                rate = sr
                player = player_factory(sr)
                print(f'First audio chunk: {first:.2f}s', flush=True)
            if sr != rate:
                raise RuntimeError('Sample rate changed within the response.')
            audio.append(chunk)
            player.queue.put(chunk)
        elapsed = time.perf_counter() - started
    finally:
        if player:
            player.close()
    if not audio:
        raise RuntimeError('The model returned no audio.')
    result = np.concatenate(audio)
    duration = len(result) / rate
    return result, rate, {'first_chunk_seconds': first,
        'playback_write_seconds': player.first_write - started if player.first_write else None,
        'generation_seconds': elapsed, 'audio_seconds': duration,
        'real_time_factor': elapsed / duration}


def benchmark():
    import numpy as np
    import soundfile as sf
    import torch
    from faster_qwen3_tts import FasterQwen3TTS
    from qwen_server import TRANSCRIPT
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA is unavailable in the fast environment.')
    reference = Path.home() / 'Downloads' / 'celine.ogg'
    if not reference.is_file():
        raise FileNotFoundError(f'Reference recording missing: {reference}')
    output = Path.home() / 'apex-fast-voice-results'
    output.mkdir(exist_ok=True)
    audio, rate = sf.read(reference, dtype='float32')
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if not 0 < len(audio) / rate <= 120 or not np.isfinite(audio).all():
        raise ValueError('Reference must be valid audio, between 0 and 120 seconds.')
    ref_wav = output / 'reference.wav'
    sf.write(ref_wav, audio, rate)
    print('Loading cached 1.7B Base model; missing model files may download.', flush=True)
    started = time.perf_counter()
    model = FasterQwen3TTS.from_pretrained('Qwen/Qwen3-TTS-12Hz-1.7B-Base')
    report = {'gpu': torch.cuda.get_device_name(0), 'load_seconds': time.perf_counter() - started,
              'runs': [], 'note': 'TTS only, not full Apex conversation latency. First write is not measured acoustic onset.'}
    text = "Hi Alex. I'm here and ready to talk. What are we working on today?"
    for index in range(3):
        print(f'\nRun {index + 1}: ' + ('initial graph preparation included' if index == 0 else 'warm model'), flush=True)
        torch.manual_seed(42)
        wave, sr, metrics = measure(model.generate_voice_clone_streaming(
            text=text, language='English', ref_audio=str(ref_wav), ref_text=TRANSCRIPT,
            chunk_size=8, max_new_tokens=512))
        sf.write(output / f'celine-{index + 1}.wav', wave, sr, subtype='PCM_16')
        report['runs'].append(metrics)
        (output / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(metrics, indent=2), flush=True)
    print(f'\nResults saved: {output / "results.json"}', flush=True)
    print('Apex still uses your original voice service. This was a separate speed test.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        benchmark() if args.run else setup()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f'Fast voice test stopped: {exc}', file=sys.stderr)
        sys.exit(1)
