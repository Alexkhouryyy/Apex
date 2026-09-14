"""Conversation memory with tiered compression and longterm persistence.

Tiers:
  1. Working memory — recent messages kept verbatim (_KEEP_MESSAGES).
  2. Rolling summary — accumulated across compressions; injected as context_prefix().
  3. Longterm flush — during compression, durable facts are extracted and saved
     to longterm.memories so they survive across sessions and can be recalled.

The agent already has explicit `remember`/`recall` tools for deliberate saves.
This module handles the automatic/mechanical path so facts aren't silently lost
when the verbatim window rolls over.
"""
import json
import anthropic
from openai import APIError as OpenAIAPIError
import config
from agent import telemetry, longterm

_SUMMARY_THRESHOLD = 30   # messages before compression triggers
_KEEP_MESSAGES = 12       # messages to keep verbatim after compression

_COMPRESSION_PROMPT = """\
Compress the following conversation segment into a rolling context summary.

{existing_summary_block}\
Conversation to compress:
{conversation_text}

Output ONLY a JSON object with exactly two keys:
- "summary": A 4-8 sentence rolling summary that incorporates any existing \
summary with this new segment. Preserve key decisions, preferences, facts, \
projects, and action items.
- "save_to_memory": A JSON array of 0-5 concise strings — durable facts or \
user preferences worth saving for future sessions (e.g. "User prefers concise \
answers", "Working on project X in Python 3.11"). Skip small talk and \
ephemeral details. Use [] if nothing is worth saving long-term.

Output only the JSON object, no other text.\
"""


class Memory:
    def __init__(self):
        self.messages: list[dict] = []
        self.summary: str = ""

    def add_user(self, content: list | str) -> None:
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: list) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def get_messages(self) -> list[dict]:
        return self.messages

    def maybe_summarize(self, client: anthropic.Anthropic) -> None:
        if len(self.messages) < _SUMMARY_THRESHOLD:
            return

        conversation_text = "\n".join(
            f"{m['role'].upper()}: "
            + (m["content"] if isinstance(m["content"], str)
               else " ".join(
                   b.get("text", "[image]") if isinstance(b, dict) else getattr(b, "text", "[block]")
                   for b in m["content"]
               ))
            for m in self.messages
        )

        existing_summary_block = (
            f"Existing rolling summary (extend, do not discard):\n{self.summary}\n\n"
            if self.summary else ""
        )

        prompt = _COMPRESSION_PROMPT.format(
            existing_summary_block=existing_summary_block,
            conversation_text=conversation_text,
        )

        try:
            resp = telemetry.create(
                client,
                call_site="agent.memory/maybe_summarize",
                model=config.PROACTIVE_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.APIError, OpenAIAPIError) as e:
            print(f"[Resilience] conversation summarization skipped ({type(e).__name__}); keeping full history")
            return

        text = resp.content[0].text.strip()
        new_summary, facts = _parse_compression_response(text)

        self.summary = new_summary
        self.messages = self.messages[-_KEEP_MESSAGES:]

        # Flush durable facts to longterm so they survive across sessions
        for fact in facts:
            try:
                longterm.remember(fact, kind="conversation", importance=6)
            except Exception as e:
                print(f"[Memory] failed to save fact to longterm: {e}")

    def context_prefix(self) -> str:
        if self.summary:
            return f"[Earlier conversation summary: {self.summary}]\n\n"
        return ""


def _parse_compression_response(text: str) -> tuple[str, list[str]]:
    """Extract (summary, facts) from the compression response.

    Returns the full raw text as the summary if JSON parsing fails — the
    turn is never broken by a malformed model response.
    """
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end])
            summary = str(parsed.get("summary", "")).strip()
            raw = parsed.get("save_to_memory") or []
            facts = [str(f).strip() for f in raw if isinstance(f, str) and str(f).strip()]
            if summary:
                return summary, facts
        except (json.JSONDecodeError, Exception):
            pass
    # Graceful fallback: treat the whole response as a plain summary
    return text, []
