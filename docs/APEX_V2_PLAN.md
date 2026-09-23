# Apex v2 — the plan

Written 2026-09-23. **No v2 code before the two v1 gates below are met.** That
was the decision ("no rewrite — prove the relay and the pinch first; v2 is the
upgrade"), and this document holds it to that.

v2 is an **upgrade, not a rewrite**. Everything in `docs/APEX_BLUEPRINT_STATUS.md`
stays; v2 is a small number of claims Apex can make that the big assistants
cannot, each with a success check measured the way the blueprint measures a
phase — by observation, never by the existence of code.

---

## 1. The honest starting point

**Where Apex cannot win.** Apex rents its intelligence. Whatever model it runs
— DeepSeek, Claude, GPT, Gemini — the company that makes that model has the
same model in its own app, with more engineers, more polish and more users.
Apex will not out-think ChatGPT, Gemini or the Claude apps, and a v2 that tries
("smarter answers", "more tools", "more tabs") loses by construction.

**Where v1 already is.** Breadth is not the problem: 104 tools, 27 dashboard
tabs, six messaging channels, a council, Forge, Genesis, a relay, a hand-tracked
board. The problem is that most of it is *built* and much less of it is
*proven* — the gap analysis exists because "never wired up" and "working
perfectly" looked identical from outside. More breadth makes that worse.

**Where Apex can win.** Things a cloud assistant is not built to be, because it
lives in someone else's data centre:

- **It is in the room.** It holds your camera, your screen, your microphone and
  your hands, continuously, on your machine — not a tab you open.
- **It is yours.** One memory on your disk, readable only by your key, that
  outlives any model you swap in or out.
- **It shows its proof.** The whole codebase is organised around one rule —
  `unknown` is never `pass` — and no mainstream assistant tells you, per
  action, what it checked and what it merely assumed.

v2 turns those three into claims a stranger could test in five minutes.

---

## 2. The gates (finish v1 first)

| Gate | Check | Status |
|---|---|---|
| **G1 — Pinch** | 20 grabs per hand on `/board`: **≥ 18/20 on each hand**, then two-hand scale works | 70% measured before the hysteresis and hand-identity fixes; **not re-measured** |
| **G2 — Relay** | Relay on an always-on machine, `python -m agent.relay --check` all green, then **lid shut, phone still answers from Apex's memory** | Proven on localhost only |

Nothing in section 3 starts until both are met. If G1 fails, the readout names
the failing number and that gets fixed — not the next feature.

---

## 3. The three pillars

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

**Work:**
1. Instrument the whole turn end to end and measure 20 turns *before* changing
   anything. Your `Test-Apex-Fast-Voice.cmd` benchmark measures the TTS stage
   alone; its `first_chunk_seconds` tells us whether streaming Qwen can meet
   0.4 s on your GPU.
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

## 4. What v2 does not do

- **No new tools, tabs or channels** until the three checks pass. Breadth is
  v1's strength and its liability.
- **Not a rewrite.** Each pillar is built on the modules that exist.
- **Not CarPlay.** Phase 11 is gated on Apple, not on Apex.
- **Not a model race.** Model choice stays a setting (`AGENT_MODEL`); Apex
  gets better answers when the models do, for free.

## 5. Constraints v2 must not break

- Local-first: memory stays on your disk; the relay only ever holds sealed
  snapshots and an allowlisted, redacted context page.
- Model-agnostic: nothing in v2 may assume one provider.
- Deny-by-default for anything outward-facing, as everywhere else in Apex.
- Three-state verdicts: `unknown` is never reported as `pass`.

## 6. Order

1. **G1** — your 20+20 grab test.
2. **G2** — relay on an always-on machine, lid-shut test.
3. **Pillar 1** — measure the voice turn, then make it sub-second-ish.
4. **Pillar 2** — the no-keyboard session.
5. **Pillar 3** — receipts.

Pillar 1 comes before 2 on purpose: press-to-talk and summon are only magic if
the answer starts within a second or two.

When all three checks pass, v2 is done — and the claim to put next to any other
assistant is three sentences a stranger can verify: *it answers as fast as a
person, it works with your hands on your own screen, and it shows you proof
for everything it did.*
