"""The Constellation — a standing panel of 12 domain-expert "planets".

Apex (the core AgentCore) is the Sun. Orbiting it are 12 specialists, each an
expert in one domain (Finance, Health, Engineer, Strategist, Devil's Advocate…),
each with its own persistent memory that grows over time. On a query, the
relevant planets answer IN PARALLEL from their own expertise + memory, then the
Sun synthesizes their takes into one answer — noting where the experts split.

Design (deliberately mirrors agent/council.py):
  - Each planet is ONE constrained model call (the council-member pattern), not a
    full tool-using sub-agent — so convening 4 planets costs ~4 calls, not ~120.
    A planet's "tools" are a descriptive lens in its system prompt, not live tools.
  - Fan-out/fan-in via ThreadPoolExecutor; the Sun's synthesis reuses council's
    `_parse_verdict` verbatim (keep the "Where the council split:" wording).
  - Persistence: each planet keeps an Obsidian-visible journal at
    ~/Documents/Apex/Planets/{Display}.md AND tagged rows in the semantic
    `memories` table (tags="planet:{key}"). After a convene, a cheap off-thread
    Haiku pass distills one durable fact per planet.

Invocation:
  - Manual: the `consult_experts` tool, the `/experts` command, POST /api/constellation.
  - Auto: core.run() folds a synthesized briefing into high-stakes turns when
    CONSTELLATION_AUTO is on (route() returns [] for low-stakes → zero cost).
"""
from __future__ import annotations
import concurrent.futures
import json
import re
import threading
import time
from dataclasses import dataclass, field

import config
from agent import longterm, provider, router, telemetry, vault
from agent.council import _parse_verdict  # reused verbatim — same verdict format


# ── Planet model ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Planet:
    key: str          # stable id, used in tags/tools: "finance"
    display: str      # "Finance"
    codename: str     # celestial theme: "Mercury"
    glyph: str        # "☿"
    pack: str         # "life" | "maker" | "mind"
    domain: str       # one-line description of the lens
    reaches: str      # what this expert naturally reaches for (descriptive only)
    system: str       # full specialized system prompt
    model: str        # provider model id


def _flagship() -> str:
    return getattr(config, "AGENT_MODEL", "claude-opus-5")


def _planet_model() -> str:
    return getattr(config, "CONSTELLATION_PLANET_MODEL", "claude-sonnet-5")


_SYS_TEMPLATE = (
    "You are {codename} {glyph}, the {display} specialist in Apex's Constellation — "
    "a standing panel of expert advisors orbiting the user's AI. You answer ONLY "
    "through the lens of {display}: {domain}. Give your sharpest, most honest, most "
    "specific take on the user's question. Be direct, not diplomatic — if their "
    "thinking is wrong from a {display} standpoint, say so plainly. State your key "
    "assumption and the ONE thing you'd most want to know next. A {display} expert "
    "naturally reaches for {reaches}; you don't have those tools live here, so reason "
    "from expertise. Keep it tight: 4-8 sentences, then end with a single line that "
    "starts with '**Bottom line:**'."
)

_DEVIL_SYS = (
    "You are Pluto ♇, the Devil's Advocate in Apex's Constellation. Your job is to "
    "find the strongest case AGAINST whatever the user is leaning toward. Steelman the "
    "opposite choice. Surface the failure mode, hidden cost, or risk everyone else is "
    "ignoring. Do not be contrarian for its own sake — be the smartest skeptic in the "
    "room, specific about what could go wrong and how likely it is. Keep it tight: 4-8 "
    "sentences, then end with a single line that starts with "
    "'**The risk you're underweighting:**'."
)

_SUN_SYS = (
    "You are Apex — the Sun at the center of a constellation of expert specialists. "
    "Each expert below answered through the lens of their own domain. Read every take "
    "and write the single best, decisive answer for the user, weaving together the "
    "strongest points and resolving conflicts between experts. Speak in one clear "
    "voice; do not just list what each expert said. Output the final answer first. "
    "Then add two short lines, exactly in this form:\n"
    "Confidence: high|medium|low — a few words on why.\n"
    "Where the council split: the key disagreement between experts and your ruling, "
    "or \"the council agreed\" if there was none."
)

_LEARN_SYS = (
    "You distill exactly one durable, reusable fact about the user from an expert "
    "exchange, or reply SKIP. Be precise and conservative — only save things that will "
    "still be true and useful next month."
)

_LEARN_PROMPT = (
    "You are the {display} expert ({domain}). Below is a user question and your answer. "
    "Extract ONE durable, reusable fact about THIS USER worth remembering for future "
    "{display} questions — a stable preference, constraint, situation, or goal. If there "
    "is nothing durable worth saving, reply with exactly: SKIP. Under 200 characters, "
    "written as a standalone fact.\n\nQUESTION:\n{question}\n\nYOUR ANSWER:\n{answer}"
)


# (key, display, codename, glyph, pack, domain, reaches, model-or-None)
# model None -> default planet model (Sonnet). The two planets where reasoning depth
# most changes the answer (Strategist, Devil's Advocate) run on the flagship.
_SPEC = [
    # --- Life pack ---
    ("finance", "Finance", "Mercury", "☿", "life",
     "money, budgets, investing, and the financial consequences of a choice",
     "spreadsheets, cash-flow models, and market data", None),
    ("health", "Health", "Venus", "♀", "life",
     "physical and mental wellbeing, energy, sleep, fitness, and sustainable habits",
     "health metrics, sleep and training logs, and medical guidelines", None),
    ("career", "Career", "Earth", "⊕", "life",
     "career trajectory, jobs, promotions, and professional growth",
     "org charts, compensation benchmarks, and industry trends", None),
    ("relationships", "Relationships", "Luna", "☾", "life",
     "relationships, family, friendships, communication, and social dynamics",
     "the perspective of everyone involved and the long arc of the relationship", None),
    # --- Maker pack ---
    ("engineer", "Engineer", "Mars", "♂", "maker",
     "software architecture, code quality, systems, and technical tradeoffs",
     "codebases, architecture diagrams, profilers, and tests", None),
    ("researcher", "Researcher", "Ceres", "⚳", "maker",
     "evidence, sources, prior art, and what is actually known versus assumed",
     "papers, primary sources, search, and structured notes", None),
    ("writer", "Writer", "Vesta", "⚶", "maker",
     "clear writing, messaging, tone, and how words land with a reader",
     "drafts, outlines, and a ruthless editing pass", None),
    ("designer", "Designer", "Pallas", "⚴", "maker",
     "design, user experience, visual clarity, and how something looks and feels",
     "mockups, design systems, and real user flows", None),
    # --- Mind pack ---
    ("strategist", "Strategist", "Jupiter", "♃", "mind",
     "long-term strategy, priorities, leverage, and the big picture",
     "goal maps, second-order effects, and a five-year horizon", "flagship"),
    ("analyst", "Analyst", "Saturn", "♄", "mind",
     "data, numbers, tradeoffs, and rigorous comparison of options",
     "datasets, decision matrices, and explicit pros-and-cons", None),
    ("devils_advocate", "Devil's Advocate", "Pluto", "♇", "mind",
     "the strongest case against the plan, and the risks everyone is ignoring",
     "red-team thinking and pre-mortems", "flagship"),
    ("synthesizer", "Synthesizer", "Neptune", "♆", "mind",
     "the through-line across domains and reconciling competing priorities into one path",
     "every other expert's view and the user's overall situation", None),
]


def _build_registry() -> dict[str, Planet]:
    out: dict[str, Planet] = {}
    for key, display, codename, glyph, pack, domain, reaches, model_tag in _SPEC:
        if key == "devils_advocate":
            system = _DEVIL_SYS
        else:
            system = _SYS_TEMPLATE.format(
                codename=codename, glyph=glyph, display=display,
                domain=domain, reaches=reaches,
            )
        model = _flagship() if model_tag == "flagship" else _planet_model()
        out[key] = Planet(key, display, codename, glyph, pack, domain, reaches, system, model)
    return out


PLANETS: dict[str, Planet] = _build_registry()


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ConstellationResult:
    question: str
    final_answer: str
    takes: list[dict] = field(default_factory=list)     # [{key, display, codename, glyph, text}]
    planets: list[dict] = field(default_factory=list)    # [{key, display, codename, glyph}]
    confidence: str | None = None
    confidence_note: str | None = None
    disagreement: str | None = None


# ── Routing (pure heuristic, no LLM — mirrors router.classify_query) ───────────

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "finance": ("money", "budget", "invest", "investment", "savings", "salary", "cost",
                "price", "afford", "debt", "loan", "mortgage", "retire", "retirement",
                "tax", "taxes", "income", "expense", "financial", "finance", "cash",
                "spend", "fund", "401k", "stock", "stocks", "portfolio", "rent", "raise"),
    "health": ("health", "sleep", "diet", "exercise", "workout", "fitness", "weight",
               "stress", "anxiety", "doctor", "medical", "nutrition", "calorie", "mental",
               "energy", "tired", "burnout", "gym", "wellbeing", "habit", "habits"),
    "career": ("job", "career", "promotion", "boss", "quit", "resign", "hire", "hiring",
               "interview", "resume", "offer", "manager", "coworker", "employer",
               "startup", "company", "freelance", "work", "raise"),
    "relationships": ("relationship", "partner", "friend", "friends", "family", "wife",
                      "husband", "girlfriend", "boyfriend", "marriage", "married", "dating",
                      "conflict", "parent", "parents", "kid", "kids", "child", "love",
                      "breakup", "divorce", "social"),
    "engineer": ("code", "bug", "architecture", "system", "database", "api", "deploy",
                 "refactor", "performance", "scale", "infrastructure", "server", "build",
                 "software", "programming", "technical", "stack", "framework", "latency",
                 "test", "tests"),
    "researcher": ("research", "study", "studies", "learn", "source", "sources", "evidence",
                   "paper", "papers", "investigate", "fact", "facts", "literature",
                   "citation", "find out", "verify"),
    "writer": ("write", "writing", "draft", "essay", "email", "copy", "blog", "article",
               "message", "prose", "edit", "wording", "tone", "content", "narrative",
               "story", "headline", "post"),
    "designer": ("design", "ui", "ux", "layout", "visual", "aesthetic", "brand", "logo",
                 "color", "colour", "typography", "interface", "mockup", "style", "theme"),
    "strategist": ("strategy", "strategic", "plan", "planning", "goal", "goals", "vision",
                   "long-term", "long term", "roadmap", "priority", "priorities",
                   "direction", "big picture", "mission", "objective", "leverage"),
    "analyst": ("analyze", "analyse", "data", "metric", "metrics", "number", "numbers",
                "compare", "comparison", "evaluate", "tradeoff", "trade-off", "tradeoffs",
                "assess", "measure", "quantify", "roi", "statistics", "benchmark"),
    "devils_advocate": ("risk", "risky", "downside", "fail", "failure", "mistake", "regret",
                        "worst case", "worst-case", "danger", "concern", "pitfall"),
    "synthesizer": ("holistic", "overall", "everything", "balance", "juggle", "competing",
                    "altogether", "in general"),
}

_KEYWORD_RES: dict[str, re.Pattern] = {
    key: re.compile(r"\b(" + "|".join(re.escape(k) for k in kws) + r")\b", re.I)
    for key, kws in _KEYWORDS.items() if kws
}

_DECISION_RE = re.compile(
    r"\b(should\s+i|should\s+we|i\s+should|we\s+should|whether|vs\.?|versus|"
    r"better\s+to|worth\s+(it|the|\w+ing)|or\s+should|decide|decision|choose|"
    r"choosing|pick\s+between|which\s+(one|option|is\s+better)|trade-?offs?|"
    r"pros\s+and\s+cons)\b",
    re.I,
)

_MIND_DEFAULT = ("strategist", "analyst", "devils_advocate", "synthesizer")


def _score(key: str, text: str) -> int:
    rx = _KEYWORD_RES.get(key)
    return len(rx.findall(text)) if rx else 0


def route(text: str, *, force: bool = False) -> list[Planet]:
    """Select which planets should weigh in. Pure heuristic, no LLM call.

    Returns [] for low-stakes queries when force=False (the auto path), so
    auto-convene costs nothing on ordinary turns. force=True (manual) skips the
    high-stakes gate and always returns a panel.
    """
    t = (text or "").strip()
    if not t:
        return []

    scored = [(_score(k, t), k) for k in PLANETS]
    scored = [(s, k) for (s, k) in scored if s > 0]
    is_decision = bool(_DECISION_RE.search(t))
    distinct_packs = {PLANETS[k].pack for _, k in scored}

    if not force:
        # Auto-convene when it's a clear decision, OR a high-stakes query that
        # genuinely spans multiple life/work domains. Everything else: no convene.
        high_stakes = bool(router._COMPLEX_KEYWORDS.search(t))
        fire = bool(scored) and (is_decision or (high_stakes and len(distinct_packs) >= 2))
        if not fire:
            return []

    if not scored:
        # Manual convene with no domain match → the general-purpose Mind pack.
        return [PLANETS[k] for k in _MIND_DEFAULT][: _cap()]

    # In auto mode the Sun already synthesizes, so drop the redundant Synthesizer.
    if not force:
        trimmed = [(s, k) for (s, k) in scored if k != "synthesizer"]
        scored = trimmed or scored

    scored.sort(key=lambda x: x[0], reverse=True)
    cap = _cap()

    # First pass: one per pack for diversity (don't return 4 Life planets).
    selected: list[str] = []
    seen_packs: set[str] = set()
    for _s, k in scored:
        if len(selected) >= cap:
            break
        pack = PLANETS[k].pack
        if pack in seen_packs:
            continue
        selected.append(k)
        seen_packs.add(pack)

    # Second pass: fill remaining slots with the next highest scorers.
    if len(selected) < cap:
        for _s, k in scored:
            if len(selected) >= cap:
                break
            if k not in selected:
                selected.append(k)

    # A decision without its skeptic isn't a real decision.
    if is_decision and "devils_advocate" not in selected:
        if len(selected) >= cap:
            selected[-1] = "devils_advocate"
        else:
            selected.append("devils_advocate")

    return [PLANETS[k] for k in selected]


def _cap() -> int:
    return max(1, int(getattr(config, "CONSTELLATION_MAX_PLANETS", 4)))


# ── Model call helper (telemetry-tracked, provider-agnostic) ──────────────────

def _complete(model: str, system: str, user: str, max_tokens: int, call_site: str) -> str:
    """One constrained completion with cost/usage logged to usage_log."""
    client = provider.get_client(model)
    resp = telemetry.create(
        client, call_site=call_site, model=model, max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return "".join(
        getattr(b, "text", "") for b in getattr(resp, "content", [])
        if getattr(b, "type", "") == "text"
    ).strip()


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_planet_memory(planet: Planet, question: str) -> str:
    """Pull a planet's own past learnings: semantic recall + recent journal lines."""
    parts: list[str] = []
    try:
        hits = longterm.recall(query=question, limit=8)
        mine = [h for h in hits if f"planet:{planet.key}" in (h.get("tags") or "")][:5]
        if mine:
            parts.append("What you've learned about the user before:\n" +
                         "\n".join(f"- {h['content']}" for h in mine))
    except Exception:
        pass
    try:
        journal = vault.read_note(planet.display, folder="Planets") or ""
        body = re.sub(r"^---\n.*?\n---\n", "", journal, flags=re.DOTALL)
        lines = [ln for ln in body.splitlines() if ln.strip()][-20:]
        if lines:
            parts.append("Your recent notes:\n" + "\n".join(lines))
    except Exception:
        pass
    return "\n\n".join(parts)


def _persist_planet_memory(planet: Planet, question: str, answer: str) -> None:
    """Distill one durable fact from this planet's take and save it (off-thread)."""
    if not answer or answer.startswith("["):  # skip failed takes
        return
    try:
        prompt = _LEARN_PROMPT.format(
            display=planet.display, domain=planet.domain,
            question=question[:800], answer=answer[:1500],
        )
        note = _complete(
            getattr(config, "CONSTELLATION_MEMORY_MODEL", config.PROACTIVE_MODEL),
            _LEARN_SYS, prompt, max_tokens=200, call_site="agent.constellation/learn",
        ).strip()
    except Exception:
        return
    if not note or note.upper().startswith("SKIP"):
        return
    note = note[:200]
    try:
        longterm.remember(note, kind="note", tags=f"planet:{planet.key}")
    except Exception:
        pass
    try:
        today = time.strftime("%Y-%m-%d")
        vault.append_note(planet.display, f"**{today}** — {note}", folder="Planets")
    except Exception:
        pass


def _launch_learning(planets: list[Planet], question: str, takes: list[dict]) -> None:
    take_by_key = {t["key"]: t["text"] for t in takes}

    def _worker():
        for p in planets:
            _persist_planet_memory(p, question, take_by_key.get(p.key, ""))

    threading.Thread(target=_worker, daemon=True, name="ConstellationLearn").start()


# ── Convene ───────────────────────────────────────────────────────────────────

def _emit(cb, *args) -> None:
    if cb:
        try:
            cb(*args)
        except Exception:
            pass


def _run_planet(planet: Planet, question: str) -> str:
    mem = _load_planet_memory(planet, question)
    user = (mem + "\n\n" if mem else "") + f"QUESTION:\n{question}"
    return _complete(planet.model, planet.system, user,
                     max_tokens=700, call_site="agent.constellation/planet")


def _synthesize(question: str, takes: list[dict]):
    blocks = [f"=== {t['codename']} — the {t['display']} expert ===\n{t['text']}"
              for t in takes]
    user = f"QUESTION:\n{question}\n\nEXPERT TAKES:\n\n" + "\n\n".join(blocks)
    try:
        final = _complete(
            getattr(config, "CONSTELLATION_SYNTH_MODEL", _flagship()),
            _SUN_SYS, user, max_tokens=2000, call_site="agent.constellation/synthesis",
        )
    except Exception as e:
        final = f"[Synthesis failed: {e}]"
    return _parse_verdict(final)


def convene(question: str, planets: list[Planet] | None = None, client=None,
            on_progress=None, on_answer=None, on_planet_start=None) -> ConstellationResult:
    """Run the constellation: relevant planets answer in parallel, the Sun synthesizes.

    planets — explicit Planet list, or None to auto-select via route(force=True).
    on_planet_start(list[dict]) — fired once with the chosen roster (skeleton cards).
    on_answer(key, text) — fired as each planet finishes (live streaming).
    The `client` argument is accepted for call-site compatibility but unused: each
    planet uses the right provider client for its own model.
    """
    if planets is None:
        planets = route(question, force=True)
    if not planets:
        return ConstellationResult(question=question,
                                   final_answer="No experts matched this query.")

    roster = [{"key": p.key, "display": p.display, "codename": p.codename, "glyph": p.glyph}
              for p in planets]
    _emit(on_planet_start, roster)
    _emit(on_progress, f"Convening {len(planets)} experts: "
                       f"{', '.join(p.display for p in planets)}...")

    takes: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(planets)) as ex:
        futs = {ex.submit(_run_planet, p, question): p for p in planets}
        for fut in concurrent.futures.as_completed(futs):
            p = futs[fut]
            try:
                text = fut.result()
            except Exception as e:
                text = f"[{p.display} failed to respond: {e}]"
            takes.append({"key": p.key, "display": p.display, "codename": p.codename,
                          "glyph": p.glyph, "text": text})
            _emit(on_answer, p.key, text)

    # Restore planet order for stable display.
    order = {p.key: i for i, p in enumerate(planets)}
    takes.sort(key=lambda t: order.get(t["key"], 99))

    _emit(on_progress, "The Sun is synthesizing the experts' answers...")
    final, confidence, note, disagreement = _synthesize(question, takes)

    if getattr(config, "CONSTELLATION_LEARN", True):
        _launch_learning(planets, question, takes)

    return ConstellationResult(
        question=question, final_answer=final, takes=takes, planets=roster,
        confidence=confidence, confidence_note=note, disagreement=disagreement,
    )


def _planet_brief(p: Planet) -> dict:
    return {"key": p.key, "display": p.display, "codename": p.codename,
            "glyph": p.glyph, "pack": p.pack, "domain": p.domain}


# ── 1:1 chat: experts get HANDS (skills they can run + forge) ─────────────────
#
# A convene is cheap advice (one model call per planet). But in a direct 1:1
# chat the expert becomes a tool-using agent: it can list & run skills, and when
# it's missing one it can FORGE a new skill (offline skills install instantly;
# networked ones stage for one-time approval). This is what lets the Designer
# actually build & deploy a website instead of saying "I can't".

_CHAT_HANDS = (
    "\n\n--- YOU HAVE HANDS HERE ---\n"
    "In this 1:1 chat you can ACT, not just advise. You have skills you can run, "
    "and you can forge new skills when you're missing one.\n"
    "When the user asks you to DO something (build, deploy, fetch, generate a file, "
    "call a service):\n"
    "1) Call list_skills to see what you can run right now.\n"
    "2) If a skill fits, run it with run_skill.\n"
    "3) If nothing fits, call acquire_skill with a precise description (set "
    "needs_network=true if it needs the internet or an external service), then run "
    "the new skill.\n"
    "To build & deploy a website: write the COMPLETE HTML/CSS yourself, then call "
    "run_skill with name='publish_website' and inputs={'site_name': <slug>, "
    "'files': {'index.html': '<full html>', 'style.css': '<full css>'}}. It returns "
    "a live URL — give that URL to the user.\n"
    "Never claim you 'can't' do something before trying list_skills and "
    "acquire_skill. After acting, tell the user exactly what you did and share any URL."
)

_CHAT_TOOLS = [
    {
        "name": "list_skills",
        "description": "List the skills (real tools) you can run right now. Call this "
                       "first whenever the user asks you to DO something concrete.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_skill",
        "description": "Run one of your skills by exact name with an inputs object. "
                       "Returns the skill's result (e.g. a live website URL).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name from list_skills."},
                "inputs": {"type": "object", "description": "Inputs for the skill."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "acquire_skill",
        "description": "Forge a brand-new skill when you lack one for the task. "
                       "Describe the capability precisely. Offline skills install "
                       "instantly; set needs_network=true for anything using the "
                       "internet/an external API (those need one-time user approval).",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What the new skill must do."},
                "needs_network": {"type": "boolean", "description": "True if it needs the internet."},
            },
            "required": ["description"],
        },
    },
]


def _chat_tools_on() -> bool:
    return bool(getattr(config, "CONSTELLATION_CHAT_TOOLS", True))


def _text_of(resp) -> str:
    return "".join(
        getattr(b, "text", "") for b in getattr(resp, "content", [])
        if getattr(b, "type", "") == "text"
    ).strip()


def _block_to_dict(b) -> dict:
    t = getattr(b, "type", "")
    if t == "text":
        return {"type": "text", "text": getattr(b, "text", "")}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input or {}}
    return {"type": "text", "text": ""}


def _dispatch_expert_tool(client, planet: Planet, name: str, inputs: dict) -> str:
    """Run one tool the expert called. Never raises — returns a string for the model."""
    from agent import skills as _skills, self_mod as _selfmod, skill_forge as _forge
    try:
        if name == "list_skills":
            sk = _skills.list_skills()
            forged = [{"name": t["name"], "description": t["description"]}
                      for t in _selfmod.get_dynamic_tools()]
            return json.dumps({"skills": sk, "forged": forged}, indent=2)

        if name == "run_skill":
            sn = (inputs.get("name") or "").strip()
            si = inputs.get("inputs") or {}
            if not sn:
                return "run_skill needs a 'name'."
            out = _skills.run_skill(sn, si)
            if out.startswith("[Skills] No skill named"):
                dyn = _selfmod.dispatch(sn, si)   # fall back to forged/dynamic tools
                if dyn is not None:
                    return dyn
            return out

        if name == "acquire_skill":
            desc = (inputs.get("description") or "").strip()
            if not desc:
                return "acquire_skill needs a 'description'."
            return _forge.acquire(
                client, desc, allow_network=bool(inputs.get("needs_network")),
                trigger=f"expert:{planet.key}",
            )

        return f"Unknown tool {name!r}."
    except Exception as e:
        return f"Tool {name!r} error: {e}"


def _chat_agentic(client, planet: Planet, system: str, messages: list[dict]) -> tuple[str, list[dict]]:
    """Tool-using loop for 1:1 expert chat. Returns (reply, actions)."""
    steps = max(1, int(getattr(config, "EXPERT_SKILL_MAX_STEPS", 6)))
    convo = list(messages)
    actions: list[dict] = []

    for _ in range(steps):
        resp = telemetry.create(
            client, call_site="agent.constellation/chat", model=planet.model,
            max_tokens=1400, system=system, messages=convo, tools=_CHAT_TOOLS,
        )
        blocks = list(getattr(resp, "content", []) or [])
        tool_uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
        if getattr(resp, "stop_reason", "") != "tool_use" and not tool_uses:
            return _text_of(resp), actions

        convo.append({"role": "assistant", "content": [_block_to_dict(b) for b in blocks]})
        results = []
        for tu in tool_uses:
            out = _dispatch_expert_tool(client, planet, tu.name, tu.input or {})
            actions.append({"tool": tu.name, "input": tu.input or {}, "result": out[:600]})
            results.append({"type": "tool_result", "tool_use_id": tu.id,
                            "content": out[:6000]})
        convo.append({"role": "user", "content": results})

    # Out of steps — ask for a final wrap-up with no further tools.
    resp = telemetry.create(
        client, call_site="agent.constellation/chat", model=planet.model,
        max_tokens=900, system=system, messages=convo,
    )
    return (_text_of(resp) or "[Reached the action-step limit — ask me to continue.]"), actions


def chat_with_planet(planet_key: str, message: str,
                     history: list[dict] | None = None) -> dict:
    """Hold a direct 1:1 conversation with a single expert planet.

    In a 1:1 chat the planet is a tool-using agent: it can run skills and forge
    new ones to actually act for the user (build/deploy/fetch/...), not just
    advise. It answers in character, with its own persistent memory loaded, and
    distills a durable fact from the exchange off-thread.

    history — [{role: 'user'|'assistant', content: str}, …], oldest first,
              EXCLUDING the current `message` (which is appended here).
    Returns {planet: {...}, reply: str, actions: [...]} or {error: str}.
    """
    planet = PLANETS.get(planet_key)
    if planet is None:
        return {"error": f"Unknown expert: {planet_key!r}"}

    # Override the "you don't have those tools live here" advisory clause for chat.
    system = planet.system.replace(
        "you don't have those tools live here, so reason from expertise.",
        "and here you can actually use and forge tools to do the work, not just advise.",
    )
    mem = _load_planet_memory(planet, message)
    if mem:
        system += "\n\n--- YOUR MEMORY ABOUT THIS USER (from past consults) ---\n" + mem
    if _chat_tools_on():
        system += _CHAT_HANDS

    messages: list[dict] = []
    for turn in (history or [])[-10:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    actions: list[dict] = []
    try:
        client = provider.get_client(planet.model)
        if _chat_tools_on():
            reply, actions = _chat_agentic(client, planet, system, messages)
        else:
            resp = telemetry.create(
                client, call_site="agent.constellation/chat", model=planet.model,
                max_tokens=900, system=system, messages=messages,
            )
            reply = _text_of(resp)
    except Exception as e:
        return {"planet": _planet_brief(planet), "error": str(e)}

    if getattr(config, "CONSTELLATION_LEARN", True) and reply:
        threading.Thread(
            target=lambda: _persist_planet_memory(planet, message, reply),
            daemon=True, name="ConstellationChatLearn",
        ).start()

    return {"planet": _planet_brief(planet), "reply": reply, "actions": actions}


def convene_briefing(question: str, planets: list[Planet], client=None) -> str:
    """Compact synthesized briefing for the auto path — folded into the user turn."""
    result = convene(question, planets=planets)
    if not result.final_answer or result.final_answer.startswith("No experts"):
        return ""
    experts = ", ".join(p["display"] for p in result.planets)
    briefing = (
        "[EXPERT BRIEFING — Apex's internal constellation of specialists was consulted "
        f"({experts}) and their answers synthesized. Use this to inform your answer; "
        "speak in your own voice, and do not quote this briefing verbatim.]\n\n"
        + result.final_answer
    )
    if result.disagreement and result.disagreement.lower() != "the council agreed":
        briefing += f"\n\nWhere the experts split: {result.disagreement}"
    maxc = int(getattr(config, "CONSTELLATION_BRIEFING_MAXCHARS", 1500))
    return briefing[:maxc]


# ── Lifecycle / introspection ─────────────────────────────────────────────────

def init() -> str:
    """Ensure the vault's Planets/ folder exists. Called once at startup."""
    try:
        vault._ensure()
    except Exception:
        pass
    return f"{len(PLANETS)} expert planets ready (Sun + Life/Maker/Mind packs)"


def list_planets() -> list[dict]:
    """The full roster, for the dashboard."""
    return [
        {"key": p.key, "display": p.display, "codename": p.codename, "glyph": p.glyph,
         "pack": p.pack, "domain": p.domain, "model": p.model}
        for p in PLANETS.values()
    ]
