"""Exercise streaming order and errors without loading a GPU model."""
import importlib.util
from pathlib import Path
import queue
import time
import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('fast_voice_benchmark', Path(__file__).parents[1] / 'scripts/test_fast_qwen.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class FakePlayer:
    def __init__(self, rate):
        self.queue = queue.Queue()
        self.first_write = time.perf_counter()
        self.closed = False
    def close(self):
        self.closed = True


def test_chunks_play_before_generation_completes():
    players = []
    def factory(rate):
        players.append(FakePlayer(rate))
        return players[-1]
    def chunks():
        yield np.array([1, 2]), 2, {}
        assert players[0].queue.qsize() == 1
        yield np.array([3, 4]), 2, {}
    audio, rate, metrics = module.measure(chunks(), factory)
    assert list(audio) == [1, 2, 3, 4]
    assert metrics['audio_seconds'] == 2
    assert metrics['first_chunk_seconds'] <= metrics['generation_seconds']
    assert players[0].closed


def test_generation_error_closes_player():
    player = FakePlayer(2)
    def chunks():
        yield np.ones(2), 2, {}
        raise ValueError('generation failed')
    with pytest.raises(ValueError, match='generation failed'):
        module.measure(chunks(), lambda rate: player)
    assert player.closed


def test_empty_generation_is_error():
    with pytest.raises(RuntimeError, match='no audio'):
        module.measure(iter([]))


def test_transformers_is_pinned_to_a_version_that_loads_celine():
    """transformers 5.17.0 crashes qwen-tts-hf 0.1.1.post1 on load
    ("'MimiConfig' object has no attribute 'rope_theta'"), found on the
    user's laptop and reproduced here on 5.17.0, not on 5.16.1. The pin must
    stay, and the setup marker must be newer than the install that got 5.17."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "scripts" / "test_fast_qwen.py").read_text()
    assert "transformers==5.16.1" in src
    assert "apex-fast-setup-v2.json" in src and "apex-fast-setup-v1.json" not in src
