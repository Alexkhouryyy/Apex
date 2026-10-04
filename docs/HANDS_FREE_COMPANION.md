# Hands-free companion and proactive comments

Start Apex and open `/companion` on your laptop. Enable **Hands-free conversation**
once and allow the microphone. Speak normally, pause, and Apex transcribes and
sends the utterance automatically. After the spoken answer ends, listening resumes.
You do not need to press Talk for each turn. The existing tap-to-record control
remains available with hands-free off.

Defaults are **Local Whisper** transcription and **Device voice** output. This
combination needs no OpenAI key. Whisper runs on the Apex host, including when the
microphone is in a remote browser. The existing `faster-whisper` dependency supplies
it; first use may download the configured `WHISPER_MODEL` (currently `base`, CPU).
Local transcription detects language automatically. Audio clips are processed in
memory, not saved by this endpoint. Transcribed messages join the companion thread.
OpenAI transcription and OpenAI output remain explicit alternatives requiring its key.
This does not integrate Voicebox yet.

## Speech model (recommended)

By default, hands-free decides you are talking by **loudness**. A fan, traffic
or typing can count as speech, and it waits 1.2 s of quiet before sending.

The **speech model** fixes both. It's the Silero voice-activity model running
in the browser, on the same microphone stream; nothing extra is sent anywhere.
- It tells a voice from noise, so a loud fan or a door doesn't count as speech.
- It sends **0.7 s** after your last word instead of 1.2 s.
- Soft word endings stay inside your turn.

Install it once (about 14 MB). The versions are pinned, and every file is
checked against npm's published hash before it's kept:

```cmd
Setup-Apex-Speech-Model.cmd
```

Then reload the companion. In settings, **Hands-free detects** should say
**Speech (model)**, and while listening the note says *Listening · speak
naturally · speech model*.

**When the model isn't used:** if it isn't installed, a file has changed, or
it fails to start or stops reporting, hands-free uses loudness. The note says
*loudness (speech model off)*; hover over it for the reason. Choose
**Loudness** in settings to turn the model off.

**Talking over Celine** still needs loud speech: her own voice through the
speakers is speech too, so the model alone can't tell you apart.

**Whether it's actually faster** is measured, not assumed. Each hands-free
turn records which detector heard it. After about 10 turns with each:

```cmd
.venv\Scripts\python -m agent.voice_timing
```

The report shows *transcript back* for loudness and for the speech model side
by side. The 0.5 s saving should show up there; if it doesn't, it isn't real.

## Proactive comments

1. Share the actual window or screen you want Apex to see.
2. Enable **Proactive comments** separately.
3. Choose a 30-second, one-minute, or two-minute checking interval.

A snapshot accompanies each check. Apex is instructed to say only one new useful
thing, otherwise remain silent. Checks wait while you are speaking, while a reply
is running or playing, while text is drafted, and for 20 seconds after an interaction.
They remain enabled until you turn them off, stop screen sharing, press Stop, close
or reload the page, or a check fails. Existing Apex spending caps still apply;
periodic vision calls use API credits even when the answer is silence.

The server forces automatic checks into a no-tool mode, even if the client asks
for Work mode. They get one main model call with at most 400 output tokens.
Automatic silence is hidden from the conversation view and saved chat history.
Useful comments are saved so you can discuss them later. Explicit replies from
you use your chosen Discuss/Work mode and the existing tool permissions.

## Controls and practical limits

- **Stop** disables hands-free and proactive comments, discards an unfinished
  recording or late transcript, stops playback and requests cancellation of the
  current agent turn. Completed host actions remain in effect.
- Turning hands-free off releases microphone tracks. Both automatic modes start
  off on every page load. Browser microphone and screen permission prompts still
  require your initial interaction.
- Listening pauses while Apex thinks and speaks, then resumes. With
  **Interrupt by talking** on, talking over a reply stops it.
- Detection is browser-local: the speech model, or audio energy without it,
  followed by Whisper's speech filter. It is not speaker identification: other
  people, game audio or video with speech can trigger it. Use headphones, and
  tune Microphone sensitivity if needed.
- Speech is sent after 0.7 s of silence with the speech model, or about 1.2 s
  with loudness. Each recording segment is capped at 30 seconds. Silence-only recordings are discarded locally. Network,
  CPU inference, model latency and voice playback determine actual response time.
- Keep the page open; Float can keep the companion visible. A suspended browser,
  sleeping laptop or lost microphone stops the experience. Background browser
  timing and game capture must be tested on your laptop.
- Screen comments use periodic snapshots, not continuous video or real-time game
  telemetry. Apex cannot reliably call out split-second events from these snapshots.

## Verification

`scripts/check_speech_detector_ui.cjs` covers the speech model's decisions:
- loud non-speech is ignored, and the turn ends 0.7 s after speech;
- soft endings keep the turn open;
- loudness takes over when the model is missing, failing or stalled;
- barge-in needs loud speech;
- timing records the detector.

It scripts the model's output; the model's real accuracy on your microphone
is what the timing report measures.

Deterministic media/DOM tests cover silence discard, automatic speech submission,
microphone reuse, playback isolation, re-listening, stop during transcription,
proactive snapshots and quiet responses. Server tests enforce no-tool automatic
checks, local transcription routing, authentication, upload/duration limits and
bounded transcription concurrency. These tests do not establish acoustic accuracy,
Windows gaming performance, recognition quality or live provider latency.

API references used: [browser microphone permissions](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia),
[local audio analysis](https://developer.mozilla.org/en-US/docs/Web/API/AnalyserNode/getFloatTimeDomainData),
and [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
