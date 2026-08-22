"""Wake-word matching, which decided when to listen and had no test.

`tests/test_resident.py` covers mute/unmute, so docs/APEX_GAP_ANALYSIS.md was
wrong to call this row UNPROVEN — it was PARTIAL. What had no test was the part
that matters: the predicate deciding whether a transcript is addressed to Apex.

It was `any(p in text for p in self.wake_phrases)` — a substring test, inside
_run(), behind sounddevice and faster_whisper, unreachable without a microphone.
With "apex" among the phrases, every sentence *containing* the word woke it.
"""
from __future__ import annotations

import pytest

from voice.wake import DEFAULT_WAKE_PHRASES, matches_wake_phrase

PH = DEFAULT_WAKE_PHRASES


# ── it wakes when addressed ───────────────────────────────────────────────────

@pytest.mark.parametrize("said", [
    "apex",
    "apex what is the time",
    "apex, what is the time",
    "hey apex",
    "hey apex remind me at six",
    "yo apex",
    "okay apex play something",
    "ok apex, stop",
    "  APEX  ",                     # whisper casing and padding
    "apex listen",
])
def test_wakes_when_addressed(said):
    assert matches_wake_phrase(said, PH) is True, f"failed to wake on {said!r}"


# ── it does not wake when merely mentioned ────────────────────────────────────

@pytest.mark.parametrize("said", [
    "the apex predator",
    "we are apex the apex predator in the ai scene",
    "i was telling him about apex yesterday",
    "have you tried apex",
    "apexes are cool",              # substring inside a longer word
    "the apexization of everything",
    "",
])
def test_does_not_wake_when_only_mentioned(said):
    assert matches_wake_phrase(said, PH) is False, (
        f"woke on {said!r} — Apex would start listening while you talk about it"
    )


def test_a_mid_sentence_mention_is_not_a_command():
    """The regression, stated plainly. This is the whole defect."""
    assert matches_wake_phrase("i think the apex predator is a shark", PH) is False


def test_longest_phrase_does_not_shadow_a_shorter_one():
    """'hey apex' and 'apex' both appear in the list; both must wake."""
    assert matches_wake_phrase("hey apex", PH)
    assert matches_wake_phrase("apex", PH)


def test_empty_phrase_list_never_wakes():
    assert matches_wake_phrase("apex", []) is False


def test_a_blank_phrase_does_not_match_everything(monkeypatch):
    """An empty string is a prefix of every utterance. Left unguarded it would
    turn the wake word into 'always listening'."""
    assert matches_wake_phrase("anything at all", ["", "apex"]) is False


# ── the continuation split (app/resident.py) ──────────────────────────────────

def _extract(transcript, phrases=None):
    from app.resident import _extract_request
    return _extract_request(transcript, phrases or PH)


def test_bare_wake_word_yields_no_continuation():
    """resident.py opens the microphone when this is empty. If it returned the
    wake word itself, Apex would answer the word 'apex' as a question."""
    assert _extract("apex") == ""
    assert _extract("hey apex") == ""


def test_continuation_is_returned_without_the_wake_phrase():
    assert _extract("apex what is the weather") == "what is the weather"
    assert _extract("hey apex, remind me at six") == "remind me at six"


def test_longest_phrase_wins_so_no_wake_words_survive_into_the_request():
    """`_extract_request` sorts phrases longest-first. "hey apex ..." does not
    discriminate — both orders strip to the same place, because everything after
    the shorter phrase is identical. "apex listen" does: matching bare "apex"
    first leaves the word "listen" at the head of the request, and Apex answers
    a command that begins with a wake word it was supposed to consume.

    The first version of this test used the "hey apex" case and passed happily
    with the sort reversed — a green check over a path that was never exercised.
    """
    assert _extract("apex listen to this") == "to this"
    assert _extract("hey apex turn off the lights") == "turn off the lights"


def test_empty_transcript_is_empty():
    assert _extract("") == ""
