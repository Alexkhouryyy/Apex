"""User preference distillation — compresses thumbs-up/down history into a usable digest.

Runs as part of reflection.consolidate(). The digest is stored in the world_state
table under key 'user_prefs' and injected into the system prompt so every response
benefits from accumulated preference signals.
"""
import json
import time
from typing import Optional

import config
from agent import longterm, telemetry

_PREFS_KEY = "user_prefs"
_DISTILL_INTERVAL = 7 * 86400  # only rebuild weekly


def _turn_text(content: str | None) -> str:
    """Extract text from the JSON blocks recorded by telemetry.log_turn."""
    if not content:
        return ""
    try:
        blocks = json.loads(content)
    except (TypeError, ValueError):
        return ""
    if isinstance(blocks, str):
        return blocks
    if isinstance(blocks, dict):
        blocks = [blocks]
    if not isinstance(blocks, list):
        return ""
    return "\n".join(block["text"] for block in blocks
                     if isinstance(block, dict) and block.get("type", "text") == "text"
                     and isinstance(block.get("text"), str))


def _last_distill_ts() -> float:
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT updated_at FROM world_state WHERE key = ?", (_PREFS_KEY,)
            ).fetchone()
        return row[0] if row else 0.0
    except Exception:
        return 0.0


def get() -> str:
    """Return the current preference digest, or empty string if none yet."""
    try:
        with longterm._conn() as c:
            row = c.execute(
                "SELECT value FROM world_state WHERE key = ?", (_PREFS_KEY,)
            ).fetchone()
        return row[0] if row else ""
    except Exception:
        return ""


def distill(client, force: bool = False) -> str:
    """Distill thumbs-up/down feedback into a preference digest.

    Skips if called within _DISTILL_INTERVAL unless force=True.
    Stores result in world_state table. Returns the digest text.
    """
    now = time.time()
    if not force and now - _last_distill_ts() < _DISTILL_INTERVAL:
        return get()

    cutoff = now - 90 * 86400  # last 90 days of feedback
    try:
        with longterm._conn() as c:
            # Each turn has separate user/assistant rows containing JSON blocks.
            # Pick the latest row of each role without duplicating the feedback
            # when an assistant has made several tool calls during that turn.
            query = """SELECT tf.comment,
                       (SELECT tl.content_json FROM turn_log tl
                        WHERE tl.session_id = tf.session_id AND tl.turn_index = tf.turn_index
                          AND tl.role = 'user' ORDER BY tl.id DESC LIMIT 1),
                       (SELECT tl.content_json FROM turn_log tl
                        WHERE tl.session_id = tf.session_id AND tl.turn_index = tf.turn_index
                          AND tl.role = 'assistant' ORDER BY tl.id DESC LIMIT 1)
                   FROM turn_feedback tf
                   WHERE tf.rating = ? AND tf.ts >= ?
                   ORDER BY tf.ts DESC LIMIT 30"""
            positives = c.execute(query, (1, cutoff)).fetchall()
            negatives = c.execute(query, (-1, cutoff)).fetchall()
    except Exception as e:
        print(f"[Prefs] DB query failed: {e}")
        return get()

    if not positives and not negatives:
        return get()

    def _fmt(rows: list) -> str:
        lines = []
        for comment, user_text, asst_text in rows:
            user_text, asst_text = _turn_text(user_text), _turn_text(asst_text)
            parts = []
            if user_text:
                parts.append(f"Q: {str(user_text)[:100]}")
            if asst_text:
                parts.append(f"A: {str(asst_text)[:100]}")
            if comment:
                parts.append(f"Note: {comment}")
            if parts:
                lines.append(" | ".join(parts))
        return "\n".join(lines[:20]) or "(none)"

    prompt = (
        "You are an AI agent reviewing your own past performance to understand what the user likes.\n\n"
        f"POSITIVE FEEDBACK (thumbs up, {len(positives)} examples):\n{_fmt(positives)}\n\n"
        f"NEGATIVE FEEDBACK (thumbs down, {len(negatives)} examples):\n{_fmt(negatives)}\n\n"
        "Write a concise preference digest (max 250 words) with these sections:\n"
        "1. Communication style (length, tone, format)\n"
        "2. Content preferences (detail level, examples, caveats)\n"
        "3. What to avoid (patterns from thumbs down)\n"
        "4. Other clear patterns\n\n"
        "Be specific and actionable. Each point should change a concrete behavior."
    )

    try:
        resp = telemetry.create(
            client,
            call_site="agent.prefs/distill",
            model=config.PROACTIVE_MODEL,
            max_tokens=350,
            messages=[{"role": "user", "content": prompt}],
        )
        digest = resp.content[0].text.strip()
    except Exception as e:
        print(f"[Prefs] Distillation failed: {e}")
        return get()

    try:
        with longterm._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO world_state (key, value, updated_at) VALUES (?, ?, ?)",
                (_PREFS_KEY, digest, now),
            )
    except Exception as e:
        print(f"[Prefs] Failed to persist digest: {e}")

    return digest
