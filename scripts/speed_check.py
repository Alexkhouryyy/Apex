"""Why is Apex slow? Time each half on this machine, in under a minute.

    .venv\\Scripts\\python scripts\\speed_check.py

"I said hi and it was still thinking two minutes later" has two possible
halves, and they need different fixes:

1. the BRAIN — the model (DeepSeek or Claude) taking long to start answering.
   Timed twice: a bare "hi", and "hi" carrying Apex's full tool list, which is
   what every real turn sends. Fast bare and slow with tools means Apex's own
   request is the weight; slow both means the provider or the connection.
2. the VOICE — Celine's Qwen server generating the audio. Timed on one short
   sentence and compared with how long that audio plays: a voice that takes
   longer to make than to play cannot keep up, however it is streamed.

Spends a few tokens on your configured model. Changes nothing.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VOICE_URL = os.getenv("VOICEBOX_URL") or "http://127.0.0.1:17494"
SENTENCE = "Hi there, I'm here and listening."


def time_brain(client, model: str, tools: list | None) -> dict:
    """Seconds to the first word and to the end, streaming, as core.py does."""
    kwargs = {"model": model, "max_tokens": 60,
              "system": "You are Apex. Reply in one short sentence.",
              "messages": [{"role": "user", "content": "hi"}]}
    if tools:
        kwargs["tools"] = tools
    start = time.perf_counter()
    first = None
    text = ""
    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            if getattr(event, "type", "") == "content_block_delta":
                piece = getattr(getattr(event, "delta", None), "text", "") or ""
                if piece and first is None:
                    first = time.perf_counter() - start
                text += piece
    return {"first_word": first, "total": time.perf_counter() - start, "text": text.strip()}


def time_voice(url: str = VOICE_URL, text: str = SENTENCE, timeout: float = 600) -> dict:
    """Seconds to generate `text`, and how long the audio it made plays."""
    body = json.dumps({"text": text, "profile_id": "celine", "engine": "qwen",
                       "language": "en"}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/generate/stream", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        audio = r.read()
    took = time.perf_counter() - start
    with wave.open(io.BytesIO(audio)) as w:
        plays = w.getnframes() / float(w.getframerate())
    return {"generate": took, "plays": plays, "ratio": took / plays if plays else None}


def verdict_brain(first: float | None) -> str:
    if first is None:
        return "no words came back"
    if first <= 2:
        return "fine"
    if first <= 6:
        return "slow"
    return "TOO SLOW"


def verdict_voice(ratio: float | None) -> str:
    if ratio is None:
        return "no audio"
    if ratio <= 0.5:
        return "fine — makes audio much faster than it plays"
    if ratio <= 1.0:
        return "tight — only just faster than it plays"
    return (f"TOO SLOW — takes {ratio:.1f}x longer to make than to play, so it can "
            "never keep up; see docs/FAST_VOICE_TEST.md")


def main() -> int:
    import config
    from agent import provider
    model = getattr(config, "AGENT_MODEL", "")
    print(f"Model: {model}\n")

    try:
        client = provider.get_client(model)
    except Exception as e:
        print(f"[brain] could not build a client for {model}: {e}")
        client = None
    if client is not None:
        try:
            bare = time_brain(client, model, None)
            print(f"[brain] bare 'hi':            first word {bare['first_word'] or 0:6.2f}s, "
                  f"done {bare['total']:6.2f}s  -> {verdict_brain(bare['first_word'])}")
        except Exception as e:
            print(f"[brain] bare 'hi' FAILED: {type(e).__name__}: {e}")
            bare = None
        try:
            from agent.core import TOOLS
            size = len(json.dumps(TOOLS))
            full = time_brain(client, model, TOOLS)
            print(f"[brain] 'hi' + {len(TOOLS)} tools ({size // 1000} KB): first word "
                  f"{full['first_word'] or 0:6.2f}s, done {full['total']:6.2f}s  -> "
                  f"{verdict_brain(full['first_word'])}")
            if bare and bare["first_word"] and full["first_word"] and \
                    full["first_word"] > 3 * bare["first_word"] + 1:
                print("        The tool list is what makes it slow, not the provider.")
        except Exception as e:
            print(f"[brain] 'hi' + tools FAILED: {type(e).__name__}: {e}")

    print()
    try:
        v = time_voice()
        print(f"[voice] '{SENTENCE}': made in {v['generate']:.1f}s, plays for "
              f"{v['plays']:.1f}s  -> {verdict_voice(v['ratio'])}")
    except urllib.error.URLError as e:
        print(f"[voice] Celine's server at {VOICE_URL} is not reachable ({e.reason}). "
              "Start Apex with Start-Apex-Celine.cmd and run this again.")
    except Exception as e:
        print(f"[voice] FAILED: {type(e).__name__}: {e}")
    print("\nPaste all of this back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
