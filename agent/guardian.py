"""Guardian Angel — decision-moment detection and mini-council intervention.

Runs alongside the general awareness review loop. Every 15 seconds it checks
recent awareness events for high-stakes *commitment moments* — the narrow window
before you send a heated email, confirm a purchase, push a destructive commit,
or paste credentials somewhere wrong.

When a moment fires:
1. Pattern-matched in pure Python (<1 ms, no API call)
2. User memory pulled from longterm for context
3. Two fast models asked IN PARALLEL (Haiku + GPT-4o-mini, ~5-8 s)
4. Synthesised to a single verdict or silence
5. Delivered as a desktop toast + spoken one-liner

Each moment type is debounced: the same pattern won't fire again for
GUARDIAN_COOLDOWN_MINUTES to avoid nagging.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

import config


# ── Patterns ─────────────────────────────────────────────────────────────────

ANGER_WORDS = re.compile(
    r"\b(frustrat|unacceptable|ridiculous|absurd|terrible|awful|hate|angry|"
    r"infuriat|disgusting|outrag|incompetent|useless|pathetic|idiot)\w*\b",
    re.IGNORECASE,
)

SHOPPING_TITLE = re.compile(
    r"(checkout|cart|payment|order|amazon|ebay|shopify|etsy|buy now|place order)",
    re.IGNORECASE,
)

PRICE_RE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d{1,2})?)", re.IGNORECASE)

COMMIT_TITLE = re.compile(
    r"(git\s+push|force\s+push|merge\s+conflict|pull\s+request|rebase)",
    re.IGNORECASE,
)

COMMIT_CLIPBOARD = re.compile(
    r"(git\s+push\s+(-u\s+origin\s+\S+\s+)?--force|git\s+reset\s+--hard|"
    r"DROP\s+TABLE|DELETE\s+FROM|rm\s+-rf)",
    re.IGNORECASE,
)

CALENDAR_TITLE = re.compile(
    r"(google calendar|outlook calendar|calendar\.google|teams meeting|zoom meeting)",
    re.IGNORECASE,
)

MESSAGE_TITLE = re.compile(
    r"(gmail|outlook|yahoo mail|hotmail|slack|discord|teams|mail\.app|"
    r"thunderbird|protonmail)",
    re.IGNORECASE,
)

SENSITIVE_CLIPBOARD = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|"
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----|"
    r"(?<![A-Za-z0-9])[A-Za-z0-9]{32,64}(?![A-Za-z0-9])\b)",
)


@dataclass
class MomentMatch:
    kind: str
    description: str     # human-readable, e.g. "typing an angry email"
    query_hint: str      # hint for longterm.recall() to pull relevant memory
    confidence: float
    context: dict = field(default_factory=dict)


def _night_multiplier() -> float:
    """Return 2.0 between 23:00–04:00, else 1.0."""
    h = time.localtime().tm_hour
    return 2.0 if h >= 23 or h < 4 else 1.0


def _extract_prices(text: str) -> list[float]:
    vals = []
    for m in PRICE_RE.finditer(text):
        try:
            vals.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return vals


def match_patterns(events: list[dict]) -> Optional[MomentMatch]:
    """Pure Python pattern matching over recent events. Returns first match or None."""
    window = [e for e in events if time.time() - e["ts"] <= 30]
    if not window:
        return None

    window_title = ""
    clipboard_text = ""
    for e in reversed(window):
        if e["source"] == "window" and not window_title:
            window_title = e["content"]
        if e["source"] == "clipboard" and not clipboard_text:
            # strip "Copied: " prefix
            clipboard_text = e["content"].removeprefix("Copied: ")

    nm = _night_multiplier()

    # 1. Sensitive credential paste — highest priority
    if clipboard_text and SENSITIVE_CLIPBOARD.search(clipboard_text):
        return MomentMatch(
            kind="sensitive_paste",
            description="pasting what looks like a credential or secret key",
            query_hint="password security credentials API key",
            confidence=0.95,
        )

    # 2. Angry message
    if MESSAGE_TITLE.search(window_title) and clipboard_text and len(clipboard_text) >= 60:
        anger_count = len(ANGER_WORDS.findall(clipboard_text))
        if anger_count >= 1:
            return MomentMatch(
                kind="angry_message",
                description="drafting a message that sounds heated or angry",
                query_hint="email communication tone conflict",
                confidence=min(0.90, 0.70 + anger_count * 0.10),
                context={"anger_count": anger_count},
            )

    # 3. Destructive git/code action
    if COMMIT_TITLE.search(window_title) or (clipboard_text and COMMIT_CLIPBOARD.search(clipboard_text)):
        return MomentMatch(
            kind="destructive_commit",
            description="about to run a potentially destructive git or SQL operation",
            query_hint="git version control code deployment",
            confidence=0.80,
        )

    # 4. Big purchase (night boost)
    if SHOPPING_TITLE.search(window_title):
        prices = _extract_prices(clipboard_text) if clipboard_text else []
        big = any(p >= 100 for p in prices)
        conf = (0.80 if big else 0.75) * nm
        if conf >= getattr(config, "GUARDIAN_THRESHOLD", 0.70):
            return MomentMatch(
                kind="night_purchase",
                description="about to make an online purchase" + (" for over $100" if big else " late at night"),
                query_hint="shopping spending budget finance",
                confidence=conf,
            )

    # 5. Calendar accept/decline
    if CALENDAR_TITLE.search(window_title):
        cb_lower = clipboard_text.lower()
        if any(w in cb_lower for w in ("accept", "decline", "maybe", "tentative")):
            return MomentMatch(
                kind="calendar_conflict",
                description="responding to a calendar invitation",
                query_hint="schedule calendar meeting commitment",
                confidence=0.70,
            )

    return None


# ── Guardian Angel ────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are Guardian Angel, a silent advisor watching over the user's shoulder. "
    "The user is about to commit an action. Your job is to give exactly ONE sentence "
    "of sharp, specific advice — or say exactly PROCEED if there's no concern. "
    "Be direct. No greeting, no hedging, no follow-up questions."
)

_USER_TMPL = (
    "The user is {description}.\n\n"
    "Relevant memory about this user:\n{memory}\n\n"
    "Give ONE sentence of advice or say exactly PROCEED."
)


class GuardianAngel:
    def __init__(
        self,
        speak_fn: Callable[[str], None],
        tray_notify_fn: Callable[[str, str], None],
        recall_fn: Callable[[str, int], str],
    ):
        self.speak = speak_fn
        self.tray_notify = tray_notify_fn
        self.recall = recall_fn

        self._cooldowns: dict[str, float] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="guardian")

        # Guardian Angel log (kept in memory; server reads it)
        self._log: list[dict] = []
        self._log_lock = threading.Lock()

    def check(self, events: list[dict]) -> None:
        """Called from the awareness review loop every 15 s. Non-blocking."""
        if not getattr(config, "GUARDIAN_ANGEL_ENABLED", True):
            return
        match = match_patterns(events)
        if match is None:
            return
        if not self._should_fire(match.kind):
            return
        # Spawn intervention thread so we don't block the review loop
        threading.Thread(
            target=self._intervene,
            args=(match,),
            daemon=True,
            name=f"guardian-{match.kind}",
        ).start()

    def _should_fire(self, kind: str) -> bool:
        cooldown = getattr(config, "GUARDIAN_COOLDOWN_MINUTES", 20) * 60
        with self._lock:
            last = self._cooldowns.get(kind, 0.0)
            if time.time() - last < cooldown:
                return False
            self._cooldowns[kind] = time.time()
            return True

    def _intervene(self, match: MomentMatch) -> None:
        try:
            # Pull memory context
            try:
                memory_ctx = self.recall(match.query_hint, 4)
            except Exception:
                memory_ctx = "No specific memory available."

            user_msg = _USER_TMPL.format(
                description=match.description,
                memory=memory_ctx or "No specific memory available.",
            )

            models = getattr(config, "GUARDIAN_MODELS", ["claude-haiku-4-5", "gpt-5-mini"])
            responses: list[str] = []

            from agent import provider as _prov

            def _ask(model: str) -> str:
                try:
                    return _prov.complete(model, _SYSTEM, user_msg, max_tokens=120)
                except Exception as e:
                    return f"PROCEED"  # fail safe — don't alarm user on API error

            futures = {self._executor.submit(_ask, m): m for m in models}
            for fut in as_completed(futures, timeout=15):
                try:
                    responses.append(fut.result())
                except Exception:
                    pass

            verdict = self._synthesise(responses)
            if verdict is None:
                return

            self._deliver(match, verdict)
        except Exception as e:
            print(f"[Guardian] Intervention error: {e}")

    def _synthesise(self, responses: list[str]) -> Optional[str]:
        """Both say PROCEED → silence. Otherwise pick the sharper/more specific warning."""
        if not responses:
            return None
        warnings = [r for r in responses if not r.strip().upper().startswith("PROCEED")]
        if not warnings:
            return None
        # Pick the longer (more specific) warning
        return max(warnings, key=len)

    def _deliver(self, match: MomentMatch, verdict: str) -> None:
        print(f"[Guardian] {match.kind}: {verdict}")
        try:
            self.tray_notify("Guardian Angel", verdict)
        except Exception:
            pass
        try:
            self.speak(f"Guardian Angel. {verdict}")
        except Exception:
            pass
        # Cross-device reach (phone/PWA/other tabs) via the notification hub.
        try:
            from agent import notify as _notify
            _notify.notify("Guardian Angel", verdict, kind="guardian",
                           priority="high", url="/?tab=telemetry",
                           dedup_key=f"guardian:{match.kind}")
        except Exception:
            pass
        entry = {
            "ts": time.time(),
            "kind": match.kind,
            "description": match.description,
            "verdict": verdict,
        }
        with self._log_lock:
            self._log.append(entry)
            if len(self._log) > 100:
                self._log = self._log[-100:]

    def recent_log(self, limit: int = 10) -> list[dict]:
        with self._log_lock:
            return list(reversed(self._log[-limit:]))

    def set_enabled(self, value: bool) -> None:
        config.GUARDIAN_ANGEL_ENABLED = value
