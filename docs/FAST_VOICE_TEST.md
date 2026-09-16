# Experimental streaming voice test

Close the running Apex/Qwen launcher and Voicebox, then double-click
`Test-Apex-Fast-Voice.cmd`. Uses the existing `apex-qwen-env` Python to create
`%USERPROFILE%\apex-qwen-fast-env`; neither the working environment nor Apex's
voice settings are modified. The first install requires several GB for CUDA
PyTorch and dependencies. It reuses Hugging Face's model cache; missing model
files can download. No cloud GPU or paid service is created.

This uses Faster Qwen3-TTS 0.4.0, a community implementation:
https://github.com/andimarafioti/faster-qwen3-tts
Its qwen-tts-hf dependency must not share an environment with upstream qwen-tts.

Reference: `%USERPROFILE%\Downloads\celine.ogg`, with the same transcript as
`scripts/qwen_server.py`. Change that transcript if the recording differs.
Three identical requests run through the persistent 1.7B Base model. Run 1
includes lazy CUDA graph preparation; runs 2 and 3 measure warmed performance.
Audio is queued to one playback stream per reply, without blocking generation.

Read `%USERPROFILE%\apex-fast-voice-results\results.json`:
- first_chunk_seconds: time until actual audio samples arrive from the model.
- playback_write_seconds: first write to the audio device, not acoustic onset.
- generation_seconds: total TTS work; excludes waiting for playback to finish.
- real_time_factor: generation time divided by audio length; below 1 is faster
  than real time. Low first-chunk latency alone does not guarantee gap-free audio.

These are TTS measurements, excluding speech recognition, DeepSeek, networking,
and browser buffering. WAVs and a converted reference stay on this laptop.
Actual Windows GPU speed and voice quality must be checked on the laptop; local
unit tests verify chunk ordering and cleanup only. If GPU memory runs out, stop
other GPU applications and send the error before changing model sizes.

This test does not switch Companion to streaming. Once quality and timing are
acceptable, its server and browser audio path both need the streaming integration.
The normal `Start-Apex-Celine.cmd` remains the launcher for existing Apex.
