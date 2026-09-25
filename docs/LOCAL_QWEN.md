# Celine voice without Voicebox

## Fast, streamed Celine (use this)

Measured on the RTX 4070 laptop: the original server below made each whole
section before returning a byte — **19.8 s for 2.1 s of speech**. The faster
engine streams: **first audio in 0.94–0.99 s** once warm, generated slightly
faster than it plays (real-time factor ~0.9).

1. Run `Test-Apex-Fast-Voice.cmd` once. It installs the engine into
   `%USERPROFILE%\apex-qwen-fast-env` (your working setup is untouched) and
   plays three test sentences.
2. From then on start Apex with **`Start-Apex-Celine-Fast.cmd`**. The first
   start after a reboot warms the GPU up for a minute or two before it says
   `CELINE READY (streaming)`; after that each section starts in about a second.
3. In the companion (`Ctrl+F5` once), choose Local Qwen and CELINE as before.

`scripts/qwen_fast_server.py` serves the same routes plus `/generate/pcm`,
which sends 16-bit PCM as it is generated. Apex relays it at
`/api/speak/stream`, and the companion plays each piece as it arrives. With a
voice server that cannot stream (this original one, or the Voicebox app),
`/api/speak/stream` answers 404 and the page uses `/api/speak` as before.

The margin is small: at a real-time factor of ~0.9 Celine is made only just
faster than she speaks, so a GPU busy with something else can cause short gaps.

## Original (whole sections)

The direct Qwen service uses the already installed Windows environment at
`%USERPROFILE%\apex-qwen-env` and reference `%USERPROFILE%\Downloads\celine.ogg`.
It requires CUDA PyTorch and qwen-tts. Alex verified GPU inference and the clone
in this environment before this launcher was added. No voice recording is
committed. The default transcript matches that reference; for a different
recording use qwen_server.py --reference PATH --transcript UTF8_TEXT_PATH.

## Start

Close the old Apex console first. Pull main, then double-click
`Start-Apex-Celine.cmd` in the repository. Keep its console open. It starts Qwen,
prepares the clone prompt once, waits for readiness, then starts Apex --text.
The model remains in GPU memory across replies. Ctrl+C stops both processes.
If startup fails, read the console; no paid service is substituted.

Open http://127.0.0.1:7860/companion, sign in with your existing dashboard token,
choose Local Qwen and CELINE (or Apex default voice), and enable spoken replies.
Enable Hands-free and allow the microphone for automatic turns. Existing Stop,
screen-sharing and proactive controls remain available. You can create a
desktop shortcut to the CMD file. This does not register Windows login startup.

The launcher sets TTS_ENGINE=voicebox, VOICEBOX_URL=http://127.0.0.1:17494,
VOICEBOX_PROFILE=celine only in the Apex child process. The value 'voicebox'
selects the existing compatible HTTP adapter; the Voicebox app is not used.
Brain keys, dashboard authentication and .env are preserved. Starting main.py
alone does not select this service: use the launcher.

## Limits and verification

The loopback-only service implements GET /health, GET /profiles and POST
/generate/stream. Despite the compatibility route name, it returns a completed
WAV, not incremental speech. It rejects browser Origin headers, non-loopback
Hosts, oversized requests, unknown profiles and concurrent generation. Requests
arrive through Apex's authenticated dashboard. Stop can silence playback but
does not cancel GPU inference. Long replies may hit Apex's existing 300-second
timeout or the generation token cap. Keep conversational replies short.

First load can retrieve missing Hugging Face files. Warm inference still takes
time: persistent loading is not a promise of instant speech. Phone access still
requires secure access to this running laptop; laptop-off voice is not provided.

Tests use a fake inference engine and validate real WAV protocol, repeated
requests, errors, size/origin/host checks and concurrency. Actual Windows
launcher lifecycle and Companion-to-Qwen audio must be verified on the laptop.
