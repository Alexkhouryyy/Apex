"""The optional half that answers while the laptop is off.

Step 8 of `docs/PHASE_6_7_PLAN.md`. Separate from `server.py` on purpose: the
mailbox holds no model key and does no reasoning, and if you never run this
file it never will. Answering is opt-in at the level of *which processes you
start*, not a flag inside one that could be flipped by a config you did not
write.

## What it may do, which is very little

Reads the working context the laptop pushed (`GET /context`), asks a model, and
POSTs the answer back to `/reply`. That is all. It touches no account, runs no
command, opens no file.

When an answer would require an action — send that email, run that build — it
does not do it. It records a **request** alongside the answer, and the laptop
turns the request into a queued task that goes through `safety.check`,
`mcp_policy.enforce` and `subagent_scope.check` at execution time on the
laptop. The cloud can want something to happen; it cannot be the thing that
approves it.

## Why the reply is plaintext

`outbox` items come from your devices and are sealed with a key this box does
not have. A reply is written *here*, by something that had to read the context
to produce it, so sealing it would be theatre — the same process holds both
halves. Two origins, two trust levels, two tables.

The consequence the laptop has to respect, and does: **a reply is data, never an
instruction.** If this box were compromised, an attacker could write a reply
saying "run rm -rf /". `agent/relay.py` files replies as content and never as
commands, and requests become queued tasks rather than executed ones — so the
worst a hostile relay achieves is a wrong answer and a task you can see sitting
in the queue.

## Configuration

    RELAY_SERVER_URL     where server.py is (default http://127.0.0.1:8799)
    RELAY_SERVER_TOKEN   the same shared secret the laptop uses
    ANTHROPIC_API_KEY    the model key. Only this file needs it.
    RELAY_ANSWER_MODEL   default claude-haiku-4-5-20251001

`ANTHROPIC_API_KEY` on this box is a real cost of letting the cloud answer, and
it is why this is a separate file: the mailbox alone needs no such thing.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

SERVER = os.getenv("RELAY_SERVER_URL", "http://127.0.0.1:8799").rstrip("/")
TOKEN = os.getenv("RELAY_SERVER_TOKEN", "")
MODEL = os.getenv("RELAY_ANSWER_MODEL", "claude-haiku-4-5-20251001")
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

SYSTEM = (
    "You are answering on behalf of Apex while its owner's laptop is offline. "
    "You have a short summary of what Apex knows — not its full memory, and no "
    "ability to act. Answer from what you were given and say plainly when you "
    "do not know rather than guessing.\n\n"
    "You cannot send messages, run commands, open files or touch any account. "
    "If answering properly would need one of those, say so and put it in "
    "`requests`; the laptop decides whether to do it when it comes back.\n\n"
    'Reply as JSON: {"answer": "...", "requests": [{"tool": "...", '
    '"why": "..."}]}. `requests` is usually empty.'
)


def _call(path: str, method: str = "GET", body: bytes | None = None) -> bytes:
    req = urllib.request.Request(
        f"{SERVER}{path}", data=body, method=method,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def context() -> dict:
    try:
        return json.loads(_call("/context").decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


def ask_model(question: str, ctx_text: str, *, call=None) -> dict:
    """Ask, and always come back with a dict. `call` is injectable for tests —
    the interesting logic here is what happens to a bad answer, not the HTTP."""
    payload = json.dumps({
        "model": MODEL, "max_tokens": 1024, "system": SYSTEM,
        "messages": [{"role": "user", "content":
                      f"What Apex knows:\n{ctx_text}\n\nQuestion: {question}"}],
    }).encode()

    if call is None:
        def call(body: bytes) -> bytes:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages", data=body,
                method="POST",
                headers={"x-api-key": API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()

    raw = json.loads(call(payload).decode())
    text = "".join(b.get("text", "") for b in raw.get("content", [])
                   if b.get("type") == "text").strip()

    # A model asked for JSON does not always send JSON. Falling back to the raw
    # text is right: a slightly malformed answer is still an answer, and
    # discarding it would turn "the model was chatty" into "the cloud is down".
    try:
        parsed = json.loads(text[text.index("{"):text.rindex("}") + 1])
        answer = str(parsed.get("answer") or "").strip() or text
        requests = parsed.get("requests") or []
    except Exception:
        answer, requests = text, []
    if not isinstance(requests, list):
        requests = []
    return {"answer": answer, "requests": requests[:10]}


def answer(question: str, *, call=None) -> dict:
    ctx = context()
    text = ((ctx.get("context") or {}).get("text") or "").strip()
    if not text:
        out = {"answer": ("The laptop has not sent me anything to work from "
                          "yet, so I have nothing to answer with."),
               "requests": []}
    else:
        out = ask_model(question, text, call=call)
    out["question"] = question
    _call("/reply", "POST", json.dumps(out).encode())
    return out


def main(argv: list) -> int:
    if not TOKEN:
        print("RELAY_SERVER_TOKEN is not set.", file=sys.stderr)
        return 2
    if not API_KEY:
        print("ANTHROPIC_API_KEY is not set. This file is the only part of the "
              "relay that needs one — the mailbox does not.", file=sys.stderr)
        return 2
    if len(argv) < 2:
        print("usage: python answer.py \"your question\"", file=sys.stderr)
        return 2
    out = answer(" ".join(argv[1:]))
    print(out["answer"])
    if out["requests"]:
        print("\nQueued for the laptop:", json.dumps(out["requests"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
