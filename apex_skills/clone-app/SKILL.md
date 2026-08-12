---
name: clone-app
description: Reverse-engineer any app, website, or product into a buildable local-first reimplementation plan. Use when the user wants to clone, replicate, recreate, self-host, or build their own version of an existing product ("how does X work and how do we build it", "make our own X", "local alternative to X", "clone X"). Produces a functional teardown, architecture map, reuse audit, and phased build plan.
created: 2026-08-12
use_count: 0
last_used_at: null
---

# Clone App — functional reimplementation procedure

Turn "I want our own X" into a plan that can actually be built this week.

Produce four artifacts, in this order. Do not skip to code — the teardown is what
makes the build small.

---

## Rule 0 — Clean-room, functional only (non-negotiable)

Replicate **what a product does**, never **what it is made of**.

**Allowed** — and the whole point:
- Studying observable behaviour, public docs, marketing pages, reviews, user reports
- Reimplementing *functionality* from a written description (functionality and APIs
  are not copyrightable — reimplementation is how most software gets built)
- Using open-source models, libraries, and your own original code

**Never**:
- Copying proprietary source, decompiled binaries, or leaked internals
- Copying assets, icons, fonts, or copyrighted content
- Reusing the product's **name, logo, or branding** — pick a new name
- Scraping behind auth/paywalls, or violating a site's ToS to obtain internals
- Presenting the clone as the original, or as affiliated with it

If a request needs any of the "never" list, say so plainly and build the
functional version instead.

---

## Step 1 — Functional teardown

Use `web_search` and `web_browse` on the official site, docs, reviews, and forum
threads. For a big target, `spawn_subagent` one researcher per angle (features,
architecture, pricing, complaints) and merge their findings.

Answer before touching architecture:

1. **What is it, in one sentence?**
2. **What is the core loop?** The single interaction the user repeats, as numbered
   steps from the user's point of view. Everything else is decoration.
3. **Feature inventory**, each tagged `core` (remove it and the product is
   pointless), `important` (users would miss it), or `nice-to-have`.
4. **Why do people pay?** Usually *not* the obvious capability — it is the quality
   bar, the latency, or the friction removed. Free alternatives usually exist; name
   why they lose.
5. **The 80/20 line.** Which subset delivers 80% of the value? That subset **is v1**.

> Separate **verified** (cite the URL) from **inferred** from observable behaviour,
> from **unknown**. Never assert a proprietary internal as fact. An honest gap list
> beats a confident guess.

---

## Step 2 — Architecture map

Decompose the core loop into **pipeline stages**, from trigger to delivered result.
For each: what it does, 2–3 candidate technologies (local-first preferred), its
**latency budget**, and its failure mode.

**Budget the latency before choosing tech.** Take the product's perceived
responsiveness and divide it across stages. A stage whose budget nothing can meet
is the *actual* hard problem — find it now, not in week three.

Decide where each stage runs: on-device, local network, or cloud. Local-first is
the default; every cloud dependency needs a justification and a degraded-mode answer.

---

## Step 3 — Reuse audit (before writing any code)

**Search this machine before proposing anything new.** Most "new products" are
60–80% already present as parts.

Use `bash` with `grep -rn` / `find`, and `read_file` on what you find. Check both
this codebase and any installed skills. For every pipeline stage, ask what already
exists, and grade each hit: `drop-in` (call it as-is), `needs-adaptation` (right
shape, wrong interface), `inspiration-only` (the pattern, not the code).

Then list the stages with **nothing** behind them. That list, not the whole
product, is the actual build. State a "% already built" figure and justify it with
file:line evidence — do not credit the codebase for things it does not have.

This step routinely turns a "3-week app" into a "2-day wiring job." Never skip it.

---

## Step 4 — Phased build plan

Phase so something works end-to-end as early as possible.

- **Phase 0 — Walking skeleton.** The core loop end-to-end, ugly, one platform,
  hardcoded where needed. Proves the pipeline and exposes the real latency.
- **Phase 1 — Make it good.** Hit the latency budget; add the `core` features.
- **Phase 2 — Make it yours.** The differentiators, including any the original
  lacks. This is where a clone becomes worth having.
- **Phase 3 — Polish.** `important` features, other platforms, packaging.

Each phase needs a goal, work items, an effort estimate, and a **testable
definition of done**. Close with **"v1 done" as one testable sentence**.

---

## Step 5 — Deliver

Write the plan with `document_write` (title: "<Target> — clone plan") so it lands
in Documents where it can be refined, and tell the user the four headline facts:
the 80/20 v1 definition, the % already built, the hard problem, and the
recommended first move. Keep the detail in the document, not the reply.

If asked to build it, **prove the riskiest assumption first** — usually latency or
an OS integration. Spike it in Phase 0 before building around it.

## Anti-patterns

- **Cloning the feature list instead of the core loop** — you ship 40 half-features
  and none of the magic.
- **Skipping the reuse audit** — rebuilding what you already own.
- **Choosing tech before budgeting latency** — the rewrite is expensive.
- **Believing marketing copy about internals** — verify or mark unknown.
- **Copying the name or branding** — free legal problem, zero benefit.
