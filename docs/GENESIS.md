# Concept Genesis — hypotheses that could be wrong

Phase 13 of the blueprint. Its success check is four words, each of which a
fluent paragraph can counterfeit:

> **Testable novel hypotheses with evidence and critique.**

## The problem this solves is not generation

Generation is one API call. Ask any model for five novel testable hypotheses
about anything and it will produce five, immediately, and they will read
beautifully. They will have the cadence of science. Some will be restatements
of the prompt, some will be unfalsifiable, some will cite sources that do not
exist — and **nothing about the text will tell you which.**

That makes this the easiest subsystem in Apex to fake, and the one where "it
works" and "it produces output" are hardest to tell apart. So `agent/genesis.py`
is not a generator with validation bolted on. It is a **gate**, in the same
shape as `agent/mcp_policy.py`: the model proposes, and four checks that never
consult the model's opinion of itself decide what survives.

Most proposals are rejected. That is the system working.

## Using it

```
"Apex, propose some hypotheses about why my battery life collapsed"
"Apex, what did you predict that I can check now?"
"Apex, hypothesis 4 — the 45C group finished at 99%. That refutes it."
```

The tool is `apex_hypothesis`: `propose`, `due`, `show`, `list`, `observe`.
`propose` runs the whole pipeline in one call — generate, critique, review —
rather than leaving three separate calls for a model to remember. A half-run
pipeline is the failure to avoid: hypotheses that were generated and never
attacked would sit in the table looking exactly like ones that survived
something.

Every hypothesis must cite `sources` you supply. Without them the tool refuses
rather than producing a pile of rejections.

## The four gates

**1. Structure** — pure. No model, no network, no database.

| Rejected | Why |
|---|---|
| "X **may** improve Y" | compatible with every possible observation, so no result can bear on it |
| "We should investigate X" | advice cannot be refuted |
| "Does X improve Y?" | a question cannot be refuted |
| refuted by "if the data does not support it" | names no observation; restates what falsity means |
| refuted by "if X does not improve Y" | the claim negated — a tautology with a lab coat on |

The last one is checked by looking at what the refutation *adds*: a real one
names a measurement, a population, a threshold or a time that the claim does
not. Fewer than two new content words and it is a restatement.

Note what is **not** rejected: "often", "on average", "tends to". Those are
falsifiable with a threshold, and a general vagueness detector would throw out
most real empirical claims.

**2. Evidence** — every citation must resolve to a real source.

A fabricated citation has an author, a year and a plausible title. It reads
exactly like a real one, and the only test is to try to fetch it. So an
unresolvable id is fatal, not a warning.

And a hypothesis where nobody looked for *disconfirming* evidence fails.
Recording "searched, found none" is a perfectly good answer; recording nothing
is not. Confirmation bias, made mechanical.

**3. Novelty — and the asymmetry is the whole design.**

The claim is compared against everything it could have been copied from: the
source passages it was shown, Apex's memories, the vault, and every prior
hypothesis. The source passages matter most — a model asked to be original
about material it was just given will, more often than any other failure, hand
back a paraphrase, and a paraphrase of an input is extraction, not genesis.

With an embedding model, similarity establishes both *"this is a duplicate"*
and *"this is not"*. Without one, only word overlap can be measured — and:

> **Word overlap proves a duplicate. Its absence proves nothing.**

A paraphrase shares almost no words with its source. So the lexical mode may
return `not novel`, and may **never** return `novel`. It returns `unknown`.

This is the same asymmetry `agent/mcp_policy.py` applies to a server's
self-description: evidence may make a verdict stricter, never looser. It is
also the concrete reason to have `sentence-transformers` working on your
machine — it turns an `unknown` into a real answer.

**4. Critique** — attacked, by something that is not itself.

A model critiquing its own proposal shares every blind spot with it: the
correlated-knowledge problem `agent/consensus.py` was written to measure. So:

- a second model is configured and was not used → **fail** (the gate was skipped)
- only one model exists → **unknown** (the critique is real, and correlated)
- any `fatal` objection → **fail**

Those two cases are different facts about the world and get different answers.
Collapsing them would either punish a single-model install for its
configuration or let a multi-model install quietly self-review.

## Never `standing` by default

```
proposed    generated, not yet judged
rejected    failed a gate — the objection says which
unverified  a gate could not run
standing    passed everything; testable and untested
supported   an observation matched the prediction
refuted     an observation matched the refutation
```

A gate that cannot run returns `unknown`, and any `unknown` makes the
hypothesis **`unverified`, never `standing`**. `agent/forge.py` and
`agent/capabilities.py` made the same choice for the same reason.

**A rejection is a diagnosis, not a sentence.** "Never attacked" is cured by
attacking it; an unresolvable citation is cured by citing something real. A
rejected hypothesis can be reviewed again. A *resolved* one cannot — once an
observation has been made the verdict belongs to the world, and the gates do
not get to overrule the test.

## The part that makes it testable rather than test-shaped

A hypothesis nobody ever resolves is a note with extra fields. `observe()`
closes the loop, and the transition rules are Popper's:

```
standing/unverified + prediction  ->  supported
standing/unverified + refutation  ->  refuted
supported           + refutation  ->  refuted
refuted             + anything    ->  still refuted
```

**A supported hypothesis can be refuted later by one contrary observation. A
refuted one is not restored by a confirming one.** You do not get to un-refute
a claim by finding a case where it held, and a system that allowed it would
drift towards whatever it had looked at most.

`due()` lists what is waiting on an observation, **oldest first** — a
prediction made three weeks ago is the one whose answer is now available, and a
newest-first list would bury it under today's ideas forever.

And `refuted` is reported as *"which is the system working"*. It is the only
status that proves the whole apparatus did its job: something was claimed, it
could have been wrong, and it was found to be wrong. A system that treated
refutation as failure would quietly stop producing refutable claims.

## How it is verified

129 tests, and nearly all of them are about rejecting things. Every guard was
confirmed by reverting it individually and watching a test fail. That audit
found two real problems:

- `test_no_evidence_at_all_is_a_guess` passed with its guard removed, because
  an empty evidence list also trips the contrary-evidence rule. It was
  asserting a verdict without asserting the reason.
- The line that looked like it kept a refutation standing —
  `if before == REFUTED: after = REFUTED` — was doing nothing, because the
  branch below already excluded `refuted`. Removing it changed no behaviour at
  all. It has been deleted rather than left in place looking load-bearing.

The semantic novelty path is exercised with injected deterministic vectors,
because this build machine cannot download model weights. That is stated rather
than hidden: `check_novelty` reports which mode it ran in and how many entries
it compared against, so a silently empty corpus shows up as `compared: 0`
instead of as a clean pass.
