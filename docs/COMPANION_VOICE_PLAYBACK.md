# Companion voice playback

Restart Apex and reload Companion after updating main. No new model or dependency
installation is needed. The existing local Qwen service and Celine profile remain.

Companion requests speech in sentence-sized sections up to 180 characters. It
plays the first section while preparing at most one next section. This reduces
the text synthesized before first playback; it does not increase GPU inference
speed. Pauses may remain between sections. Text generation still finishes before
speech starts. This is section playback, not model-level audio streaming.

Send, Talk and new chat are guarded while voice work is busy. Stop immediately
silences playback and discards remaining sections, but an in-flight GPU request
must finish before another voice turn starts. Status explains that wait. HTTP
abort alone cannot cancel Qwen inference. Keep one Companion tab open; other
clients can still contend for the local service.

Ordinary conversation is prompted to use one or two short sentences; requested
detailed answers remain available. Code blocks are shown on screen and replaced
with a brief notice in speech.

Validation: check_speech_queue.cjs covers first-section playback, ordered prefetch,
stop/drain and errors. check_companion_ui.cjs covers UI guards, stale audio,
error visibility and existing interactions. check_handsfree_ui.cjs covers echo
pause, microphone reuse and automatic re-listening. These tests simulate media;
Windows GPU timing and actual browser playback need verification on the laptop.
