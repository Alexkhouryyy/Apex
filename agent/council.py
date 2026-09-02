"""Multi-model council — Claude, GPT, and Gemini debate to reach the best answer.

Flow:
  1. Opening statements — each member answers the question independently.
  2. Debate — each member reads every answer, critiques them, and revises
     or defends its own.
  3. Synthesis — a chair model consolidates the debate into one best answer.

Members are included only when their provider has an API key configured, so
the council degrades gracefully (minimum 2 members required). Callers may pass
an `on_answer` callback to stream the debate live as each member responds.
"""
from __future__ import annotations
import concurrent.futures
import re
from dataclasses import dataclass, field

import config
from agent import provider


# (model, display label) — one flagship model per provider.
# Gemini uses the flash tier, not pro: pro is paid-tier only, so flash keeps the
# council usable on Google's free tier.
_ROSTER = [
    ("claude-opus-5", "Claude"),
    ("gpt-5.1", "GPT"),
    ("gemini-3-flash", "Gemini"),
]
_CHAIR = "claude-opus-5"

_OPENING_SYS = (
    "You are one member of an expert council that will debate to find the best "
    "possible answer. Give your strongest, most honest answer to the question. "
    "Be specific and concrete. State any key assumptions or uncertainties."
)

_DEBATE_SYS = (
    "You are {label}, a member of an expert council. You answered a question; so "
    "did the other members. Read every answer critically — including your own. "
    "Point out concrete flaws, gaps, or mistakes. Then give your revised, best "
    "answer. Change your mind if another member is right; defend your position "
    "with reasons if you still disagree. Be direct, not diplomatic."
)

_CHAIR_SYS = (
    "You are the chair of an expert council. The members debated a question. "
    "Read the full transcript and write the single best possible answer. "
    "Integrate the strongest points from every member. "
    "Output the final answer first — clear and decisive. Then add two short "
    "lines, exactly in this form:\n"
    "Confidence: high|medium|low — a few words on why.\n"
    "Where the council split: the key disagreement and your ruling, or "
    "\"the council agreed\" if there was none."
)

# Presets tailor the council's framing to a kind of question. Each preset adds
# an instruction fragment to the opening prompt and to the chair prompt.
_PRESETS = {
    "general": {"label": "General", "opening": "", "chair": ""},
    "decision": {
        "label": "Decision",
        "opening": (
            " This is a decision. Weigh the concrete options, name the "
            "trade-offs, and commit to a clear recommendation."
        ),
        "chair": " End with a one-line recommendation the reader can act on.",
    },
    "code-review": {
        "label": "Code review",
        "opening": (
            " This is a code review. Hunt for bugs, edge cases, security "
            "issues, and unclear logic. Quote the specific lines you mean."
        ),
        "chair": " Group the final answer into Must-fix, Should-fix, and Nits.",
    },
    "fact-check": {
        "label": "Fact-check",
        "opening": (
            " This is a fact-check. Separate what is well-established from "
            "what is uncertain. Flag any claim you cannot verify."
        ),
        "chair": (
            " Open with a verdict — True, False, Mixed, or Unverifiable — "
            "then the reasoning."
        ),
    },
}


@dataclass
class CouncilResult:
    question: str
    final_answer: str
    transcript: list[dict] = field(default_factory=list)  # [{round, label, text}]
    members: list[str] = field(default_factory=list)
    confidence: str | None = None        # "high" | "medium" | "low"
    confidence_note: str | None = None   # the short reason after the dash
    disagreement: str | None = None      # the council-split text, or "the council agreed"
    # Measured agreement across the *opening* answers — see agent/consensus.py.
    # Deliberately separate from `confidence`, which is the chair's own opinion:
    # a chair that read three similar answers will report high confidence for
    # the same reason the answers were similar, so it cannot be the check.
    agreement: dict | None = None


_CONFIDENCE_RE = re.compile(
    r"^\s*\**\s*Confidence\s*\**\s*:\s*\**\s*(high|medium|low)\b"
    r"(?:\s*[—–\-:]\s*(.+?))?\s*\**\s*$",
    re.IGNORECASE,
)
_SPLIT_RE = re.compile(
    r"^\s*\**\s*Where the council (?:split|agreed)\s*\**\s*:\s*(.+?)\s*\**\s*$",
    re.IGNORECASE,
)


def _parse_verdict(text: str) -> tuple[str, str | None, str | None, str | None]:
    """Pull `Confidence:` and `Where the council split:` lines out of the chair
    output. Returns (clean_answer, confidence, confidence_note, disagreement).

    The chair is prompted to end with these two lines, but it's an LLM — if
    either line is missing, the corresponding field is None and the answer
    text is returned with whatever lines remain.
    """
    confidence: str | None = None
    confidence_note: str | None = None
    disagreement: str | None = None
    kept: list[str] = []
    for line in text.splitlines():
        m = _CONFIDENCE_RE.match(line)
        if m:
            confidence = m.group(1).lower()
            note = (m.group(2) or "").strip().rstrip(".") or None
            confidence_note = note
            continue
        m = _SPLIT_RE.match(line)
        if m:
            disagreement = m.group(1).strip().strip('"').strip("'")
            continue
        kept.append(line)
    clean = "\n".join(kept).rstrip()
    return clean, confidence, confidence_note, disagreement


def roster() -> list[dict]:
    """The full council roster with per-member API-key availability.

    If OLLAMA_COUNCIL_MODEL is set (e.g. ollama/llama3.1), that local model is
    appended as a fourth member; it is available when OLLAMA_BASE_URL is set.
    """
    entries = list(_ROSTER)
    ollama_model = config.OLLAMA_COUNCIL_MODEL or ""
    if ollama_model:
        entries.append((ollama_model, "Ollama"))

    out = []
    for model, label in entries:
        p = provider.provider_for(model)
        available = (
            (p == "anthropic" and bool(config.ANTHROPIC_API_KEY))
            or (p == "openai" and bool(config.OPENAI_API_KEY))
            or (p == "gemini" and bool(config.GEMINI_API_KEY))
            or (p == "ollama" and bool(config.OLLAMA_BASE_URL))
        )
        out.append({"model": model, "label": label, "available": available})
    return out


def available_members() -> list[tuple[str, str]]:
    """Return council members whose provider has an API key configured."""
    return [(m["model"], m["label"]) for m in roster() if m["available"]]


def preset_names() -> list[dict]:
    """Available council presets, for the UI."""
    return [{"id": k, "label": v["label"]} for k, v in _PRESETS.items()]


def _ask_all(members, sys_for, user_for, max_tokens=1500, round_no=0, on_answer=None) -> dict:
    """Call every member in parallel. Returns {label: text}.

    If on_answer is given, it is called as on_answer(round_no, label, text) the
    moment each member finishes — so callers can stream the debate live.
    """
    out: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(members)) as ex:
        futs = {
            ex.submit(provider.complete, model, sys_for(label), user_for(label), max_tokens): label
            for model, label in members
        }
        for fut in concurrent.futures.as_completed(futs):
            label = futs[fut]
            try:
                text = fut.result()
            except Exception as e:
                text = f"[{label} failed to respond: {e}]"
            out[label] = text
            if on_answer:
                try:
                    on_answer(round_no, label, text)
                except Exception:
                    pass
    return out


def _format_answers(answers: dict) -> str:
    return "\n\n".join(f"--- {label} ---\n{text}" for label, text in answers.items())


def convene(question: str, rounds: int = 1, panel: list[str] | None = None,
            preset: str = "general", on_progress=None, on_answer=None,
            on_round_start=None) -> CouncilResult:
    """Run the council.

    rounds  — number of debate rounds after the opening.
    panel   — optional list of model ids to limit the council to.
    preset  — one of _PRESETS; tailors the framing of the debate.
    on_answer(round, label, text) — called as each member responds (live).
    on_round_start(round, labels) — called just before each round begins, so
        the dashboard can pre-render skeleton cards with status pills.
    """
    members = available_members()
    if panel:
        members = [(m, l) for (m, l) in members if m in panel]
    if len(members) < 2:
        return CouncilResult(
            question=question,
            final_answer=(
                "The council needs at least 2 members with API keys configured. "
                "Add OPENAI_API_KEY and/or GEMINI_API_KEY to .env, or widen the panel."
            ),
            members=[label for _, label in members],
        )

    pre = _PRESETS.get(preset) or _PRESETS["general"]
    opening_sys = _OPENING_SYS + pre["opening"]
    chair_sys = _CHAIR_SYS + pre["chair"]
    member_labels = [label for _, label in members]

    def _progress(msg: str):
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    def _round_start(round_no: int):
        if on_round_start:
            try:
                on_round_start(round_no, list(member_labels))
            except Exception:
                pass

    transcript: list[dict] = []

    # Round 0 — opening statements
    _progress(f"Opening round — {len(members)} members answering in parallel...")
    _round_start(0)
    answers = _ask_all(
        members,
        sys_for=lambda _l: opening_sys,
        user_for=lambda _l: question,
        round_no=0,
        on_answer=on_answer,
    )
    for label, text in answers.items():
        transcript.append({"round": 0, "label": label, "text": text})

    # Debate rounds
    for r in range(1, max(0, rounds) + 1):
        _progress(f"Debate round {r} — members critiquing and revising...")
        _round_start(r)
        debate_user = (
            f"QUESTION:\n{question}\n\n"
            f"ANSWERS SO FAR:\n{_format_answers(answers)}\n\n"
            "Critique the answers, then give your revised best answer."
        )
        answers = _ask_all(
            members,
            sys_for=lambda label: _DEBATE_SYS.format(label=label),
            user_for=lambda _l: debate_user,
            round_no=r,
            on_answer=on_answer,
        )
        for label, text in answers.items():
            transcript.append({"round": r, "label": label, "text": text})

    # Synthesis
    _progress("Chair synthesizing the final answer...")
    blocks = []
    for entry in transcript:
        tag = "Opening" if entry["round"] == 0 else f"Debate {entry['round']}"
        blocks.append(f"=== {entry['label']} ({tag}) ===\n{entry['text']}")
    chair_user = f"QUESTION:\n{question}\n\nCOUNCIL TRANSCRIPT:\n\n" + "\n\n".join(blocks)
    try:
        final = provider.complete(_CHAIR, chair_sys, chair_user, max_tokens=2000)
    except Exception as e:
        final = f"[Chair synthesis failed: {e}]"

    clean, confidence, confidence_note, disagreement = _parse_verdict(final)

    try:
        from agent import consensus
        measured = consensus.agreement(transcript)
    except Exception as e:
        # Never lose a completed council answer to a measurement fault.
        print(f"[Council] agreement measurement failed: {e}")
        measured = None

    # Record what this run actually showed, so "is a council worth it for
    # this kind of question" stops being a matter of opinion. Isolated: a
    # bookkeeping failure must not cost the answer that was just produced.
    try:
        from agent import council_stats as _cstats
        _cstats.record_run(question, member_labels, measured, confidence)
    except Exception as e:
        print(f"[Council] could not record the run: {e}")

    return CouncilResult(
        question=question,
        final_answer=clean,
        transcript=transcript,
        members=member_labels,
        confidence=confidence,
        confidence_note=confidence_note,
        disagreement=disagreement,
        agreement=measured,
    )
