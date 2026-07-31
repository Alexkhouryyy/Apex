---
name: clone-app
description: Reverse-engineer any app, website, or product into a buildable local-first reimplementation plan. Use when the user wants to clone, replicate, recreate, self-host, or build their own version of an existing product ("how does X work and how do we build it", "make our own X", "local alternative to X", "clone X"). Produces a functional teardown, architecture map, reuse audit against the existing codebase, and a phased build plan.
---

# Clone App — functional reimplementation procedure

Turn "I want our own X" into a plan that can actually be built this week.

The output is always four artifacts, in this order. Do not skip straight to code —
the teardown is what makes the build small.

---

## Rule 0 — Clean-room, functional only (non-negotiable)

You are replicating **what a product does**, never **what a product is made of**.

**Allowed** — and the whole point:
- Studying observable behavior, public docs, marketing pages, reviews, user reports
- Reimplementing *functionality* from a written description (functionality and APIs
  are not copyrightable — reimplementation is how most software gets built)
- Using open-source models, libraries, and your own original code

**Never**:
- Copying proprietary source, decompiled binaries, or leaked internals
- Copying assets, icons, fonts, or copyrighted content
- Reusing the product's **name, logo, or branding** for the clone — pick a new name
- Scraping behind auth/paywalls, or violating a site's ToS to obtain internals
- Presenting the clone as the original, or as affiliated with it

If a request needs any of the "never" list, say so plainly and build the functional
version instead. Note the boundary in the plan so it isn't quietly crossed later.

---

## Step 1 — Functional teardown

Answer these before touching architecture:

1. **What is it, in one sentence?**
2. **What is the core loop?** The single interaction the user repeats. Write it as
   numbered steps from the user's point of view. Everything else is decoration.
3. **Feature inventory**, each tagged:
   - `core` — remove it and the product is pointless
   - `important` — users would miss it
   - `nice-to-have` — polish, retention, or upsell
4. **Why do people pay for it?** The honest differentiator. Usually *not* the
   obvious capability — it's the quality bar, the latency, or the friction removed.
   Free alternatives usually exist; name why they lose.
5. **The 80/20 line.** Which subset delivers 80% of the value? That subset **is v1**.

> Research discipline: separate **verified** (cite the URL) from **inferred** from
> observable behavior, from **unknown**. Never assert a proprietary internal as fact.
> "Unknowns" is a required section — an honest gap list beats a confident guess.

---

## Step 2 — Architecture map

Decompose the core loop into **pipeline stages** — from the user's trigger to the
delivered result. For each stage record:

| Field | Why it matters |
|---|---|
| What it does | The stage's job in one line |
| Candidate tech | 2–3 real options, local-first preferred |
| Latency budget | ms allowed here (see below) |
| Failure mode | What the user sees when it breaks |

**Budget the latency before choosing tech.** Take the product's perceived
responsiveness, then divide it across stages. A stage whose budget no candidate can
meet is the *actual* hard problem of the clone — find it now, not in week three.

Also decide **where each stage runs**: on-device, local network, or cloud API.
Local-first is the default; every cloud dependency needs a justification and a
degraded-mode answer for when it's unreachable.

---

## Step 3 — Reuse audit (do this before writing any code)

**Search the existing codebase before proposing anything new.** Most "new products"
are 60–80% already present as parts. For every pipeline stage, ask what we already
have.

Grade each hit:
- `drop-in` — call it as-is
- `needs-adaptation` — right shape, wrong interface
- `inspiration-only` — the pattern, not the code

Then produce the honest list of stages we have **nothing** for. That list, not the
whole product, is the actual build. State a "% already built" figure and justify it
with `file:line` evidence — do not credit the codebase for things it doesn't have.

This step routinely turns a "3-week app" into a "2-day wiring job." It is the
highest-value step in the procedure; never skip it.

---

## Step 4 — Phased build plan

Phase the work so **something works end-to-end as early as possible**.

- **Phase 0 — Walking skeleton.** The core loop end-to-end, ugly, one platform,
  hardcoded where needed. Proves the pipeline and exposes the real latency.
- **Phase 1 — Make it good.** Hit the latency budget. Add the `core` features.
- **Phase 2 — Make it yours.** The differentiators, including any the original
  lacks (this is where a clone becomes worth having).
- **Phase 3 — Polish.** `important` features, other platforms, packaging.

Every phase needs: goal, work items, effort estimate, dependencies, and a
**testable definition of done**. Mark items needing a user decision as `[decide]`
and everything else `[do]`.

Close with **"v1 done" as a single testable sentence** — e.g. "I hold a key, speak,
and clean text appears in the focused app in under 2 seconds, offline."

---

## Step 5 — Build it

Standard engineering rules apply: deterministic cores get tests; wire into the
existing app's conventions rather than inventing parallel ones; ship in reviewable
commits.

**Prove the riskiest assumption first.** Usually latency or an OS integration —
spike it in Phase 0 before building around it.

---

## Output format

Write the plan to `docs/clones/<target>-plan.md` and give the user a short summary
containing: the 80/20 v1 definition, the % already built, the hard problem, and the
recommended first move. Keep the full detail in the file, not the chat reply.

## Anti-patterns

- **Cloning the feature list instead of the core loop.** You ship 40 half-features
  and none of the magic.
- **Skipping the reuse audit.** Rebuilding what you already own.
- **Choosing tech before budgeting latency.** The rewrite is expensive.
- **Believing marketing copy about internals.** Verify or mark unknown.
- **Copying the name/branding.** Free legal problem, zero benefit.
