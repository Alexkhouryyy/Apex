# Clone Plan — Local Voice Dictation ("Scribe")

_Target studied: Wispr Flow. Produced with the `clone-app` skill, 2026-07._
_Clean-room functional reimplementation. We replicate **capabilities**, not code, assets, or branding — hence the clone is called **Scribe**, not any variant of the original name._

---

## ⚠️ Sourcing honesty

The target-research agent was **blocked from fetching primary sources** (403 on every host, including `wisprflow.ai`, from this environment). Everything in §1 comes from **search-result snippets and search-engine summaries**, not pages read directly. Treat product specifics as *second-hand but corroborated across multiple independent snippets*. The technical decisions in §2–§4 do **not** depend on those specifics — they were derived independently and are separately cited.

---

## 1. Functional teardown

### What it is
Not a transcription tool — a **"say it, get what you would have typed" tool**. It sits system-wide above the OS and types finished prose into whatever field has focus. The company reportedly optimizes for **zero-edit rate** (outputs needing no correction) rather than word error rate. That reframing is the whole product.

### The core loop
1. Cursor in any text field, any app — no app switch, no separate window
2. **Press and hold** a global hotkey (default `Fn` on macOS, `Ctrl+Win` on Windows)
3. Speak naturally — filler words, false starts, and mid-sentence self-corrections all allowed; **no** spoken punctuation commands
4. **Release** — this is the commit event
5. **Nothing appears while you speak.** Per the product's own docs this is deliberate: *"You won't see words appear live… which is what lets Flow clean up filler words, punctuation, and self-corrections using the full context."*
6. ~0.7–2s later the cleaned text is inserted at the cursor
7. If you later type over a word to fix it, the corrected spelling is auto-added to your personal dictionary

> **The key architectural insight of the entire teardown:** *not* streaming is a feature. Refusing to show partial text is what buys the right to **retroactively rewrite** the whole utterance. Every "magic" behavior below is downstream of that one decision. A streaming dictation engine literally cannot do backtrack.

### Feature inventory
| Feature | Tier | Notes |
|---|---|---|
| System-wide PTT insertion at cursor | **core** | The entire product surface |
| Smart Formatting (punctuation, caps, grammar, filler removal) | **core** | Can't even be disabled on most platforms — they consider it *the* product |
| **Backtrack** (self-correction resolution) | **core** | "meet at 5 — actually, 6" → "meet at 6". Most-cited magic moment |
| Context awareness (active app, surrounding text, screen) | **core** | Fixes proper nouns, code identifiers, register |
| Tone adaptation per app | **core** | Casual in Slack, structured in email. Most-praised differentiator |
| Self-populating personal dictionary | **core** | Learns from your corrections silently |
| 100+ languages, auto-detect, code-switching | core | |
| Command Mode (select text → speak an edit) | important | Turns input method into voice text-editor |
| Hands-free toggle mode | important | Accessibility / long-form |
| Snippets (voice-triggered expansion) | nice-to-have | |

### Why people pay (the honest differentiator)
Free OS dictation transcribes **verbatim** and can't clean up. Raw Whisper is **a model, not an input method** — no hotkey, no injection, no context. What's sold is the gap between "what you said" and "what you meant," plus the system-wide plumbing nobody wants to build.

### The 80/20 line → **this is v1**
> **Hold a key → speak → release → clean, formatted text appears at your cursor.**

Everything else (Command Mode, snippets, multilingual, mobile) is post-v1.

---

## 2. Architecture map + latency budget

Perceived budget: **≤1.5s from key-release to text-on-screen.** Beyond ~2s it feels broken. Budget allocated per stage:

| # | Stage | Tech choice | Budget | Failure mode |
|---|---|---|---|---|
| 1 | Global PTT hotkey (press **and** release) | `pynput` (Win/mac), `python-evdev` (Linux) | <10ms | Dictation never starts/stops |
| 2 | Mic capture while held | `sounddevice` InputStream + `threading.Event` stop | streaming | Truncated audio |
| 3 | **STT** | **Parakeet TDT 0.6B** (INT8) via `transcribe.cpp` | **300–600ms** | Wrong words |
| 4 | **LLM cleanup** | Ollama, small warm model (1–3B) | **≤600ms** ⚠️ | Slow or over-rewrites |
| 5 | Focus restore + injection | **Clipboard paste + restore** | 30–80ms | Text into wrong app |

### Decision 1 — Parakeet, not Whisper (this reverses my starting assumption)
Structural, not a few percent. **Whisper always processes a fixed 30-second window** — a 10-second utterance costs the same encoder pass as a 30-second one. **Parakeet (FastConformer-TDT transducer) scales with actual audio length**, which is exactly what short PTT utterances need.

- Parakeet TDT 0.6B v3 INT8: **~0.3–0.6s CPU-only** for a 10s utterance (~30× real-time); ~0.15–0.4s on Apple Silicon via MLX
- Whisper small, same hardware: ~1.2–2.0s. Whisper large-v3: unusable (~11s)
- **And it's more accurate**: 6.32% vs 7.44% aggregate WER vs Whisper large-v3 — at 600M params vs 1550M
- **Emits punctuation and capitalization natively** ← this is load-bearing, see Phase 0
- ~730MB disk, ~1.5–2GB resident, CC-BY-4.0 (commercial OK, carry attribution)
- Fallback for weak hardware: **Moonshine v2 Small Streaming** (123M, MIT) — 73ms on a MacBook Pro, 165ms Linux x86

> Apex's existing `voice/stt.py` is faster-whisper. It's excellent for the *agent* (long-form, accuracy-first) but is the wrong shape for dictation. Keep both; pick per use case.

### Decision 2 — Clipboard paste, not synthetic typing
The industry has converged here and Apex currently does the wrong thing:
- **Speed**: paste is one keystroke regardless of length (~30–80ms) vs ~1.5s to type 300 chars. Apex's `type_text` uses `xdotool --delay=30` → a 300-char paragraph takes **~9 seconds of visible typing**. Unusable.
- **Correctness**: the clipboard carries UTF-16 natively, so emoji/CJK/accents/RTL all just work. Synthetic typing hits Windows surrogate-pair bugs, X11 keymap-borrowing races, and garbles on AZERTY/QWERTZ.
- Must **save and restore** the user's prior clipboard.

### Decision 3 — Per-OS strategy pattern from day one
No single Python library does this well on all three OSes. Every shipping tool examined (Handy, whisper-writer, nerd-dictation, whisper-overlay) converged on per-OS backends behind one interface. Define `Hotkey`, `Recorder`, `Injector`, `FocusProbe` protocols with 3 small backends each. **Forcing one abstraction is the documented main failure mode.**

### ⚠️ Two gotchas that will silently break this
1. **Windows**: a low-level hook callback exceeding `LowLevelHooksTimeout` (300ms default) gets the hook **silently removed — no error, no exception**. PTT just dies. **Hard rule: the hotkey callback does nothing but `event.set()`**; all work on a worker thread.
2. **Self-retrigger**: check pynput's `injected` flag or our own synthetic paste keystroke re-triggers PTT in a loop.
3. Pick a **non-modifier** default key (Right Ctrl / F13). Modifier-only PTT needs CGEventTap on macOS and leaks modifiers during injection.

---

## 3. Reuse audit — what Apex already gives us

**~50% built on Linux/X11 · ~30% Windows · ~25% macOS.** Apex has nearly all the *scaffolding* and nearly none of the *dictation-specific behavior*.

| Stage | % | Asset | Grade |
|---|---|---|---|
| STT | ~85% | `voice/stt.py` — `_get_model`, `warm_up()` (burns a silent transcribe so first real call is hot) | drop-in *(but see Parakeet)* |
| LLM cleanup | ~70% | `provider.complete('ollama/…')` works **today**, no changes (`provider.py:419`, Ollama via OpenAI-compatible endpoint) | drop-in |
| Cleanup prompt | — | `agent/documents.py:118` `_EDIT_SYS` — *"return ONLY the revised text, no preamble"* is exactly the anti-preamble guard needed | needs-adaptation |
| Tray / app shell | ~75% | `app/tray.py` (5 states map 1:1 onto ours), `ResidentState`, rotating logs, `app/autostart.py` (**genuinely cross-platform** — the only such module) | near drop-in |
| Mic capture | ~55% | `voice/stt.py:53` stream/queue/RMS pattern | needs-adaptation |
| App context | ~50% | `agent/app_context.py:82` detection (has real Windows support) | needs-adaptation |
| Text injection | ~30% | `tools/computer.py:49` — X11-only, 30ms/char | needs replacing |
| **PTT hotkey** | **~20%** | `app/hotkey.py` wrapper is good; mechanism is wrong | **rewrite core** |

### The blocking gap
**`pynput.GlobalHotKeys` is press-only — it exposes no release event, and nothing in the entire repo listens for a key-up.** Push-to-talk *is* press-and-release. The defining mechanic of the product is **0% built**. Swap to `keyboard.Listener(on_press=…, on_release=…)`.

Also genuinely missing: recording that stops on an external signal (all four capture sites self-terminate on RMS silence — wrong for hold-to-release), clipboard **write** (`pyperclip.copy` appears nowhere), focus capture/restore, Windows/macOS injection, and any custom-vocabulary biasing (`stt.py:16` passes only `language` + `beam_size`).

---

## 4. Phased build plan

### Phase 0 — Walking skeleton `[do]` · ~1 day
Prove the pipeline and measure real latency. One OS (whichever you're on). Ugly is fine.
- `Hotkey`/`Recorder`/`Injector`/`FocusProbe` protocols + one backend each
- PTT press/release via `keyboard.Listener`; callback **only** sets an Event
- Record while held → Parakeet → clipboard-paste inject
- **No LLM yet** — Parakeet emits punctuation and capitalization natively, so this is already usable
- **Done when**: hold key, speak, release, correct punctuated text appears in a text editor. Log the measured per-stage latency.

### Phase 1 — Make it good `[do]` · ~2 days
- Add LLM cleanup via `provider.complete('ollama/…')` with a **dictation-specific** prompt: filler removal, false-start/backtrack resolution, spoken-punctuation handling — built on `documents.py:_EDIT_SYS`
- **Keep the model warm** (Ollama `keep_alive`) — cold-load costs more than inference
- **Make cleanup skippable per-utterance** if it blows the budget (the Phase-0 fast path stays available)
- Tray icon states via `app/tray.py`; focus capture/restore; clipboard save/restore
- **Done when**: filler words and self-corrections are gone, end-to-end ≤1.5s

### Phase 2 — Make it ours `[do]` · ~2 days
- **Per-app formatting rules** — reuse `app_context.py` detection, rewrite the 9 profile bodies from *agent persona* into *output shaping* ("in a terminal: bare command, no capitalization, no trailing period")
- **Personal dictionary** → Parakeet/sherpa-onnx hotword biasing + cleanup-prompt injection
- Dictation history + undo + re-inject-last (reuse `app/audit.py`)
- **Apex-native differentiator the original can't have**: dictate straight into Apex's Documents editor, and let the local agent act on what you said

### Phase 3 — Polish `[do]` · ~3 days
Remaining OS backends (evdev on Linux/Wayland; macOS Accessibility + Input Monitoring permissions), settings UI, hotkey rebinding, mic device picker, `autostart.py` install.

### Definition of "v1 done"
> **I hold a key anywhere on my machine, speak a messy sentence with an "um" and a self-correction, release, and clean formatted text appears at my cursor in under 1.5 seconds — fully offline.**

---

## 5. The hard problem (prove this first)

**LLM cleanup latency is the only stage whose budget is genuinely at risk.** STT (~0.5s) and injection (~0.08s) are solved by the choices above. A local 1–3B model generating ~50 tokens on CPU can take 1–3s — which alone blows the entire budget.

Three mitigations, in order:
1. **Parakeet already punctuates and capitalizes**, so cleanup is *optional*, not load-bearing. Phase 0 ships without it.
2. Smallest viable warm model (Qwen2.5 1.5B / Llama 3.2 1B / Gemma 3 1B), `keep_alive` pinned.
3. Tiered: skip the LLM for short/clean utterances; use it only when disfluency is detected.

**Spike this in Phase 0** — measure cleanup latency on the actual target hardware before building around it.

## 6. Legal boundary (carried from the skill)
Functional reimplementation only. No proprietary code, decompilation, assets, or branding; the clone ships as **Scribe**. Parakeet is CC-BY-4.0 (attribution required); transcribe.cpp MIT; Moonshine MIT.
