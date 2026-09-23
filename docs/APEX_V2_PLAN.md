# Apex v2 — the plan

Written 2026-09-23. **No v2 code before the two v1 gates below are met.** That
was the decision ("no rewrite — prove the relay and the pinch first; v2 is the
upgrade"), and this document holds it to that.

---

## 0. The mission

> **Every person gets an AI they own, that helps them invent, and that proves
> everything it does.**

v2 is not aimed at other assistants. Competing with the big assistants, or
with agent projects like Hermes and OpenClaw, is a race decided by whoever
rents the best model this month — Apex rents its intelligence like everyone
else, and a v2 aimed there moves nobody anywhere. The mission is three things
the world does not have yet, and Apex already holds a working seed of each:

| | What the world has now | What Apex makes normal | The seed already in Apex |
|---|---|---|---|
| **Ownership** | People rent their AI; the company keeps the memory | Your AI's memory lives on your disk, opens only with your key, and outlives any model you swap in | One SQLite brain; `agent/relay.py` sealing; any-model adapter |
| **Invention** | Turning an idea into a tested claim or a made object takes a lab, a workshop and training | Anyone goes from a spoken idea to a tested hypothesis or a validated, manufacturable object | `agent/genesis.py`, `agent/forge.py` |
| **Trust** | Agents act in the world and you take their word for it | Every action carries a receipt anyone can check, and `unknown` is never reported as done | Three-state verdicts across Forge, Genesis, `relay --check` |

**The guard against the breadth trap.** "All three, equal" is the easiest
mission in the world to fail by starting nine things and proving none. So the
mission is judged by ONE flagship demonstration that needs all three at once
(section 4), plus one proof per leg. Nothing counts as mission progress unless
it moves one of those checks.

**The mission sits on a foundation.** Nobody invents with an AI that takes
minutes to answer and drops what you hand it. The three pillars in section 3
are that foundation, and they come first.

---

## 1. The honest starting point

**Where v1 already is.** Breadth is not the problem: 104 tools, 27 dashboard
tabs, six messaging channels, a council, Forge, Genesis, a relay, a hand-tracked
board. The problem is that most of it is *built* and much less of it is
*proven* — the gap analysis exists because "never wired up" and "working
perfectly" looked identical from outside. More breadth makes that worse, and
a big mission is the most tempting excuse for more breadth there is.

**What Apex is that a cloud assistant is not built to be:**

- **It is in the room.** It holds your camera, your screen, your microphone and
  your hands, continuously, on your machine — not a tab you open.
- **It is yours.** One memory on your disk, readable only by your key, that
  outlives any model you swap in or out.
- **It shows its proof.** The whole codebase is organised around one rule —
  `unknown` is never `pass`.

## 2. The gates (finish v1 first)

| Gate | Check | Status |
|---|---|---|
| **G1 — Pinch** | 20 grabs per hand on `/board`: **≥ 18/20 on each hand**, then two-hand scale works | **Reported passed by the user, 2026-09-23**, after the hysteresis and hand-identity fixes. The per-hand counts were not recorded, so the margin over 18/20 is unknown |
| **G2 — Relay** | Relay on an always-on machine, `python -m agent.relay --check` all green, then **lid shut, phone still answers from Apex's memory** | Proven on localhost only |

Nothing in section 3 starts until both are met. If G1 fails, the readout names
the failing number and that gets fixed — not the next feature.

---

## 3. The foundation — three pillars

Each pillar is one sentence a user would say, one measurable check, and the
work that check implies. The checks are the contract; the work list is a guess
and will change when the first measurement comes in.

### Pillar 1 — "It talks like a person"

**Check:** from the moment you stop speaking to the first sound of Apex's
reply, **median ≤ 1.5 s, 90th percentile ≤ 2.5 s**, over 20 real turns on the
Windows laptop, with Celine's voice. And you can cut it off mid-sentence by
talking or swiping down, and it stops within 0.3 s.

**Why first:** Apex is voice-first, and today a Celine reply can take
*minutes*. No board move matters while talking to it is slow. It is also the
thing you feel every single day.

**Where the time goes** (to be measured, not assumed — every stage gets a
timestamp in one log line per turn):

| Stage | Budget |
|---|---|
| End of speech detected | 0.3 s |
| Speech to text (faster-whisper, local) | 0.3 s |
| Model's first sentence (DeepSeek, streaming) | 0.5 s |
| First audio chunk from TTS (Qwen / Voicebox, streaming) | 0.4 s |

**Measuring it (built 2026-09-23):** every spoken companion turn now records
six stages, in milliseconds since you stopped talking, on the browser's own
clock — transcript back, first word of the reply, whole reply written, voice
requested, first audio received, first sound — plus the server's own time for
speech-to-text and for the first voice section. The last turn shows as one
line under the message box. After 20 spoken turns:

    .venv\Scripts\python -m agent.voice_timing

prints the median and 90th percentile per stage and the verdict: `unknown`
under 20 turns, then `pass` or `fail` against this check. Code:
`agent/voice_timing.py`, `dashboard/static/companion.js`.

**Known before measuring, from the code:** the companion does not request the
first voice section until the model has finished the *whole* reply, tool
calls included, so "first sound" can never be earlier than "whole reply
written". And hands-free waits 1.2 s of silence before sending. The numbers
will say how much each of those costs.

**Work:**
1. ~~Instrument the whole turn end to end~~ — done; measure 20 turns *before*
   changing anything. Your `Test-Apex-Fast-Voice.cmd` benchmark measures the
   TTS stage alone; its `first_chunk_seconds` tells us whether streaming Qwen
   can meet 0.4 s on your GPU.
2. Stream TTS by sentence into playback (the companion already queues
   sentences; the local Qwen server does not stream yet).
3. Barge-in that works with speakers, not only headphones.

### Pillar 2 — "I work with my hands, it works on the board"

**Check:** a 5-minute session, no keyboard, no mouse:
- summon Apex with a swipe and it is **listening** (not "tap the mic");
- "show me my calendar" — the card appears at your hand;
- grab, move, scale with two hands, throw away, "undo";
- point at a card, "make this bigger" — the right card changes;
- press a card — Apex talks about it, first sound within Pillar 1's budget.

**Pass:** every step works first try in 4 of 5 sessions, and **zero actions you
did not ask for** (no stray swipe, no stray summon, no dropped card).

**Why:** this is the Jarvis moment, and it is the one no cloud assistant can
copy — it needs the camera on your machine, all the time.

**Work:** summon starts the mic; press-to-talk (a short, still pinch — only
after G1 shows grabs no longer drop, or dropped grabs will talk); Apex puts
its answers on the board as cards rather than only in chat.

### Pillar 3 — "It shows me its receipts"

**Check:** for every action Apex takes on your behalf in a day (a file written,
a message sent, a card changed, a calendar event), you can open one page and
see, per action: what was done, what evidence says it worked, and what is
`unknown`. **Zero actions reported as done that the evidence does not
support** — spot-checked on 20 random actions.

**Why:** this is what the codebase already believes (Forge refuses to export
`unverified`; Genesis returns `unknown` rather than `novel`; `relay --check`
names every stage). v2 makes it visible to the user, not just to the tests.
It is the reason to trust an agent that acts on your computer.

**Work:** one receipts page over the existing audit trails (`mcp_audit`,
outcomes, board history); a per-action verdict (`ok` / `fail` / `unknown`);
the model is told to report `unknown` rather than claim success.

---

## 4. The mission proofs

v2 is done when the flagship demo passes and each leg has its own proof. Each
check is something observed, never the existence of code.

### The flagship demo — all three at once

**A person who is not an engineer, on their own machine, with their own Apex:**
1. says an idea out loud ("a phone stand that holds my phone at 60°", or "does
   my plant grow faster by the window?");
2. Apex turns it into either a Forge design that passes validation, or a
   Genesis hypothesis with a stated way it could be wrong;
3. it is made or tested — the part is printed, or the observation is recorded
   and the hypothesis is marked supported or refuted;
4. Apex exports a **receipt bundle**: what was asked, what was done, the
   checks and their verdicts, the files;
5. **a stranger, on a different machine, verifies the bundle** with a
   standalone checker and gets the same verdicts.

**Pass:** 3 different non-engineers, one session each, 60 minutes or less,
every step above observed. A refuted hypothesis counts as a pass — Genesis
reports `refuted` as the system working.

### Ownership — proof

- A stranger sets up Apex from the README on a clean machine in **30 minutes
  or less, without help**.
- Switch the model mid-conversation (`/model`), and Apex still remembers
  everything from before the switch.
- **One command exports all of your data, and one command deletes all of it** —
  and after the delete, nothing of yours is left on disk or on the relay.
- G2 passes: lid shut, phone still answers from your own memory.

### Invention — proof

The flagship demo, with at least one **printed** Forge part and at least one
**recorded real-world observation** that moved a Genesis hypothesis.

### Trust — proof

- The receipt format is written up as an open spec (`docs/RECEIPTS_SPEC.md`),
  under Apex's MIT licence, so any agent can emit it.
- The standalone checker verifies a bundle **without Apex installed**.
- Tamper test: change one byte of a receipt and the checker says so.
- Whether other projects adopt the spec is not in Apex's control, so it is
  not the check. Making it easy to adopt is.

---

## 5. What v2 does not do

- **No new tools, tabs or channels** unless they move one of the checks in
  sections 3 and 4. Breadth is v1's strength and its liability.
- **Not a rewrite.** Each pillar is built on the modules that exist.
- **Not CarPlay.** Phase 11 is gated on Apple, not on Apex.
- **Not a model race, and not a feature race** with other assistants or agent
  projects. Model choice stays a setting (`AGENT_MODEL`); Apex gets better
  answers when the models do, for free.

## 6. Constraints v2 must not break

- Local-first: memory stays on your disk; the relay only ever holds sealed
  snapshots and an allowlisted, redacted context page.
- Model-agnostic: nothing in v2 may assume one provider.
- Deny-by-default for anything outward-facing, as everywhere else in Apex.
- Three-state verdicts: `unknown` is never reported as `pass`.

## 7. Order

1. **G1** — your 20+20 grab test.
2. **G2** — relay on an always-on machine, lid-shut test.
3. **Pillar 1** — measure the voice turn, then make it fast.
4. **Pillar 2** — the no-keyboard session.
5. **Pillar 3** — receipts. This is also the start of the Trust leg: the
   receipts page and the receipt bundle are the same data.
6. **Ownership** — clean-machine setup, export all, delete all.
7. **The flagship demo** — first with you, then with three non-engineers.

Pillar 1 comes before 2 on purpose: press-to-talk and summon are only magic if
the answer starts within a second or two. And the flagship demo comes last on
purpose: it is the proof, and it can only pass on a foundation that works.

When it passes, the claim is not "better than another assistant". It is that
**an ordinary person, with an AI they own, invented something real and can
prove it** — and that is a claim about the future, not about a leaderboard.
