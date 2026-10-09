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
        self._raw_history: list[dict] = []
        self.context_selection: dict | None = None
        from uuid import uuid4
        self.archive_id = uuid4().hex

    def add_user(self, content: list | str) -> None:
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content: list) -> None:
        from agent.context_graph import plain
        self.messages.append({"role": "assistant", "content": plain(content)})

    def get_messages(self) -> list[dict]:
        return self.messages

    def raw_history(self) -> list[dict]:
        import copy
        return copy.deepcopy(self._raw_history or self.messages)

    def _select_context(self):
        from agent.context_graph import ContextGraph, save_archive
        import copy
        # The graph always selects from the full canonical conversation retained
        # by this path, including messages omitted on earlier selections.
        if (self.context_selection is None or self.messages[:self.context_selection["selected_count"]]
                != self.context_selection["messages"]):
            if self.context_selection is not None:
                from uuid import uuid4
                self.archive_id = uuid4().hex
            self._raw_history = copy.deepcopy(self.messages)
        else:
            self._raw_history.extend(copy.deepcopy(self.messages[self.context_selection["selected_count"]:]))
        query = next((str(m.get("content", "")) for m in reversed(self._raw_history)
                      if m.get("role") == "user"), "")
        graph = ContextGraph.from_messages(self._raw_history)
        save_archive(self.archive_id, self._raw_history)
        selection = graph.select(query, getattr(config, "CONTEXT_SELECTION_BYTES", 64000))
        self.messages = selection["messages"]
        self.context_selection = {**copy.deepcopy(selection), "selected_count": len(self.messages)}

    def revive(self, ids):
        from agent.context_graph import ContextGraph
        return ContextGraph.from_messages(self._raw_history).revive(ids)

    def restore_archive(self, ident):
        from agent.context_graph import load_archive
        self.messages = load_archive(ident)
        self._raw_history = []
        self.archive_id = ident
        self.context_selection = None

    def maybe_summarize(self, client: anthropic.Anthropic) -> None:
        from agent import plugins, apocalypse
        offline = apocalypse.enabled()
        keep = 6 if offline else _KEEP_MESSAGES
        if len(self.messages) < (12 if offline else _SUMMARY_THRESHOLD):
            return
        if getattr(config, "REVERSIBLE_CONTEXT_ENABLED", False):
            try:
                self._select_context()
            except (ValueError, TypeError) as exc:
                print(f"[Memory] reversible selection skipped ({type(exc).__name__}); keeping current history")
            return
        import copy
        try:
            handled, result = (False, None) if apocalypse.enabled() else plugins.provider_call(
                'context', 'summarize', messages=copy.deepcopy(self.messages[:-_KEEP_MESSAGES]), summary=self.summary)
            if handled:
                if not isinstance(result, str) or not result.strip() or len(result) > 20000:
                    raise ValueError('Context engine must return a non-empty summary of at most 20,000 characters.')
                self.summary = result
                self.messages = self.messages[-_KEEP_MESSAGES:]
                return
        except Exception as exc:
            print(f'[Plugins] Context engine failed ({type(exc).__name__}); keeping full history.')
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

        if offline:
            conversation_text = conversation_text[-18000:]
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
                max_tokens=512 if offline else 1024,
                messages=[{"role": "user", "content": prompt}],
            )
        except (anthropic.APIError, OpenAIAPIError, apocalypse.OfflineUnavailable) as e:
            print(f"[Resilience] conversation summarization skipped ({type(e).__name__}); keeping full history")
            return

        text = resp.content[0].text.strip()
        new_summary, facts = _parse_compression_response(text)

        self.summary = new_summary[:2000] if offline else new_summary
        self.messages = self.messages[-keep:]

        # Flush durable facts to longterm so they survive across sessions
        from agent import memory_governance
        if memory_governance.current() is not None:
            facts = []  # summarizer output is not explicit owner approval
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
