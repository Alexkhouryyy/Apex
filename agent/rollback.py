"""Auto-rollback for skill rewrites that hurt approval rate.

check_rewrites() is called:
  - At the end of every reflection.consolidate() cycle
  - On demand via POST /api/outcomes/check-rollback

Algorithm per rewrite:
  1. Skip if not enough post-rewrite rated turns yet (< MIN_RATED_TURNS)
  2. Compute post-rewrite approval_rate using skill_rate_in_window()
  3. If delta (post - pre) < -DROP_THRESHOLD → rollback
  4. If post_rated_turns >= CONFIRM_TURNS and no drop → mark 'confirmed'

Both thresholds and the minimum turn count are configurable via config.py or
fall back to the defaults below.
"""
import time

import config
from agent import longterm

# How many rated turns must accumulate after the rewrite before we evaluate.
MIN_RATED_TURNS: int = getattr(config, "ROLLBACK_MIN_RATED_TURNS", 5)

# If approval rate drops by more than this fraction (0–1) → auto-rollback.
DROP_THRESHOLD: float = getattr(config, "ROLLBACK_DROP_THRESHOLD", 0.20)

# After this many rated turns with no drop, mark the rewrite as 'confirmed'.
CONFIRM_TURNS: int = getattr(config, "ROLLBACK_CONFIRM_TURNS", 20)


def check_rewrites(dry_run: bool = False) -> dict:
    """Evaluate all active rewrites and auto-rollback the bad ones.

    Returns a summary dict with keys:
      checked, rolled_back, confirmed, skipped_not_enough_data
    """
    from agent import skills as skills_mod, outcomes

    now = time.time()
    with longterm._conn() as c:
        rows = c.execute(
            "SELECT id, name, ts, old_source, pre_approval_rate, pre_rated_turns "
            "FROM skill_rewrites WHERE status = 'active' ORDER BY ts ASC",
        ).fetchall()

    checked = rolled_back = confirmed = skipped = 0

    for rewrite_id, name, rewrite_ts, old_source, pre_rate, pre_rated in rows:
        # Query post-rewrite stats
        post = outcomes.skill_rate_in_window(name, start_ts=rewrite_ts, end_ts=None)
        post_rate = post["approval_rate"]
        post_turns = post["rated_turns"]

        checked += 1

        if post_turns < MIN_RATED_TURNS:
            skipped += 1
            continue

        # Always persist the latest post stats
        if not dry_run:
            with longterm._conn() as c:
                c.execute(
                    "UPDATE skill_rewrites SET post_approval_rate = ?, post_rated_turns = ? WHERE id = ?",
                    (post_rate, post_turns, rewrite_id),
                )

        # Evaluate: rollback?
        if post_rate is not None and pre_rate is not None:
            delta = post_rate - pre_rate
        elif post_rate is not None:
            # No pre-rate baseline — use a fixed floor (50 %) to catch clear regressions
            delta = post_rate - 0.5
        else:
            skipped += 1
            continue

        if delta < -DROP_THRESHOLD:
            reason = (
                f"approval rate dropped from {_fmt(pre_rate)} to {_fmt(post_rate)} "
                f"({delta:+.0%}, threshold {DROP_THRESHOLD:.0%}) "
                f"over {post_turns} rated turns"
            )
            if not dry_run:
                _do_rollback(rewrite_id, name, old_source, reason, skills_mod)
            rolled_back += 1
            print(f"[Rollback] {'(dry) ' if dry_run else ''}rolling back skill {name!r}: {reason}")

        elif post_turns >= CONFIRM_TURNS:
            if not dry_run:
                with longterm._conn() as c:
                    c.execute(
                        "UPDATE skill_rewrites SET status = 'confirmed' WHERE id = ?",
                        (rewrite_id,),
                    )
            confirmed += 1

    return {
        "checked": checked,
        "rolled_back": rolled_back,
        "confirmed": confirmed,
        "skipped_not_enough_data": skipped,
        "dry_run": dry_run,
        "ts": now,
    }


def _do_rollback(rewrite_id: int, name: str, old_source: str, reason: str, skills_mod) -> None:
    """Write the old source back to disk, reload, and mark the DB row rolled_back."""
    if not old_source:
        print(f"[Rollback] No old_source for rewrite #{rewrite_id} — cannot restore.")
        return
    try:
        path = skills_mod._skill_path(name)
        path.write_text(old_source, encoding='utf-8')
        skills_mod._load(name)
    except Exception as e:
        print(f"[Rollback] File restore failed for {name!r}: {e}")
        return
    with longterm._conn() as c:
        c.execute(
            "UPDATE skill_rewrites SET status = 'rolled_back', rollback_ts = ?, rollback_reason = ? "
            "WHERE id = ?",
            (time.time(), reason, rewrite_id),
        )
    # Tell the user a self-rewrite was reverted — this is a safety event worth knowing.
    try:
        from agent import notify as _notify
        _notify.notify(
            "Apex rolled back a skill",
            f"Reverted “{name}” — {reason}.",
            kind="evolution", priority="high", url="/?tab=evolution",
            dedup_key=f"evo-rollback-{rewrite_id}",
        )
    except Exception as e:
        print(f"[Rollback] notify failed: {e}")


def list_rewrites(days: int = 30) -> list[dict]:
    """Return all rewrite records in the window, newest first."""
    cutoff = time.time() - days * 86400
    with longterm._conn() as c:
        rows = c.execute(
            """SELECT id, ts, name, trigger, pre_approval_rate, pre_rated_turns,
                      post_approval_rate, post_rated_turns, status, rollback_ts, rollback_reason
               FROM skill_rewrites WHERE ts >= ? ORDER BY ts DESC""",
            (cutoff,),
        ).fetchall()
    return [
        {
            "id": r[0], "ts": r[1], "name": r[2], "trigger": r[3],
            "pre_approval_rate": r[4], "pre_rated_turns": r[5],
            "post_approval_rate": r[6], "post_rated_turns": r[7],
            "status": r[8], "rollback_ts": r[9], "rollback_reason": r[10],
            "delta": _delta(r[4], r[6]),
        }
        for r in rows
    ]


def _fmt(rate) -> str:
    return f"{rate:.0%}" if rate is not None else "n/a"


def _delta(pre, post) -> float | None:
    if pre is None or post is None:
        return None
    return round(post - pre, 3)
