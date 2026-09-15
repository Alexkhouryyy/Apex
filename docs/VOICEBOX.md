# Local Qwen speech for Apex

Apex connects to the Voicebox desktop application on the same computer through
`http://127.0.0.1:17493`. Keep Voicebox running with Qwen CustomVoice 1.7B downloaded.
Apex does not install a second Qwen model or modify Voicebox's Python environment.
DeepSeek remains the reasoning provider; only speech synthesis runs in Qwen.
Local speech has no per-request API fee. Reasoning API usage still costs money.

## Windows setup

Stop Apex with Ctrl+C, leave Voicebox open, then run from CMD:

```bat
cd /d "%USERPROFILE%\Apex" && git pull --ff-only origin main && .venv\Scripts\python.exe scripts\setup_voicebox.py && .venv\Scripts\python.exe main.py --text
```

The setup verifies actual WAV generation, plays a short Windows audio test, backs
up the project .env, and sets TTS_ENGINE=voicebox. It preserves brain credentials,
memory paths and other settings. No new package install is required.
On first use it creates/reuses a dedicated `Apex Qwen Local` preset with Ryan.
Existing profiles and recordings are not changed. First model load can take minutes.

Open http://127.0.0.1:7860/companion. Local Qwen is the default browser voice.
Keep Speak replies enabled. Hands-free enables automatic mic turns after browser
permission; Proactive enables optional screen comments when sharing a screen.
The /drive page uses the same voice controls. The main dashboard also offers
Local Qwen under Voice, with a separate Speak replies checkbox.
Terminal voice mode uses TTS_ENGINE=voicebox (omit --text when starting Apex).

## Which voices Apex offers

One rule, `voicebox.resolve_engine`, decides it — and the same rule runs on both
the list and the speech, so **anything shown in the picker works, and anything
not shown is refused if its id is sent directly.** Those used to be four
different rules in four places, and the dropdown was a suggestion rather than a
statement about what would play.

Offered: Qwen presets (`preset_engine` of `qwen` or `qwen_custom_voice`) and
cloned voices. Not offered: presets built on another engine, and imported
recordings.

A voice type Apex has never seen is still offered and driven as a clone. That is
deliberate, and it is the one place this does not follow Apex's usual
deny-by-default rule: Voicebox owns this vocabulary, not Apex, so refusing an
unrecognised type would break a working voice the first time Voicebox ships a
new kind. There is no safety question here to justify that risk. If a future
voice kind needs different handling, `resolve_engine` is the single line to
change.

If a profile you pinned with `VOICEBOX_PROFILE`, or one remembered in a
browser, stops being usable, Apex says so by name and says what kind of voice it
is — it does not report it as missing.

## Select your voice

In Companion, select an existing Qwen voice in **Qwen profile**. This choice is
remembered in that browser. If Voicebox was closed at page load, reopen it and
change the Voice dropdown to refresh the list, or reload the page.
To set a profile for terminal, main dashboard and the Companion default:

```bat
.venv\Scripts\python.exe scripts\setup_voicebox.py --profile "Exact Voicebox Profile Name"
```

Unique profile names or IDs are accepted; ambiguous names are rejected.
CustomVoice uses preset speakers. Recorded voice clones require Qwen Base 1.7B
installed in Voicebox; choosing a cloned profile sends engine=qwen. Apex does
not silently replace a missing clone with a preset. VOICEBOX_SPEAKER controls
the automatically created preset (default Ryan); edit the project's .env to change it.
VOICEBOX_PROFILE overrides the automatic preset. VOICEBOX_URL must be loopback HTTP.

## Behavior and limits

- Calls /profiles and /generate/stream with model_size=1.7B. Returned WAV plays
  in the browser/device making the Apex request, not automatically on the laptop.
- No cloud TTS fallback. Voicebox failures appear as errors; text replies remain.
- Does not invoke Voicebox's Compose/personality LLM; speaks Apex's exact reply.
- One synthesis at a time per Apex process, 4000-character/32-MB limits, finite timeout.
- Stop suppresses late playback and stops current Companion audio. Voicebox may
  finish an already-running GPU inference after the browser stops waiting.
- The stream endpoint returns audio after synthesis; this is not low-latency
  token-by-token speech. Hands-free remains turn-taking, not full duplex barge-in.
- A phone can play the returned audio through Apex, provided its connection and
  browser permissions work and the laptop/Voicebox stay awake. No phone Qwen install.
- Real Windows/GPU speed, audio quality and microphone echo require a laptop test.

Validated against Voicebox backend routes and schemas:
https://github.com/jamiepine/voicebox/blob/main/backend/routes/generations.py
https://github.com/jamiepine/voicebox/blob/main/backend/models.py
