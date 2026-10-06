# The live photoreal face (Simli)

This is the top tier. Apex's face is a photoreal person or character,
streamed live, with lips, jaw and head moving naturally as Apex talks:
- **Apex's brain and voice:** it says exactly what Apex says, in Apex's own
  voice, including Celine's.
- **Starts talking within about a second:** Celine's streamed voice goes in as
  it's made.
- **Rendered on Simli's GPUs:** your laptop does nothing but play the stream.

| | Live face (Simli) | Video (MuseTalk, [VIDEO_AVATAR.md](VIDEO_AVATAR.md)) |
| --- | --- | --- |
| Look | Photoreal, whole face moves | Photoreal footage; only the mouth area is redrawn |
| Wait before it speaks | About a second, streamed | One sentence's render time |
| Runs | Simli's servers | Your RTX 4070 |
| Cost | Pay per minute | Free |
| Privacy | Apex's speech audio goes to Simli | Nothing leaves your PC |
| Works offline | No | Yes |

Use the live face for the best look. Keep MuseTalk as the free, private
fallback.

## Why Simli

I compared the real-time avatar services: Tavus, HeyGen's interactive
avatars, D-ID and Simli.
- **Tavus, HeyGen and D-ID:** mostly full conversation products, with their
  own AI and voice, or driven by text.
- **Simli:** takes **your audio** and returns the face. That's what Apex
  needs, because it keeps Apex's memory, tools, personality and Celine's voice
  instead of replacing them.

Its browser client is open source (MIT). Apex serves a pinned copy, built
with its dependencies into one file
(`dashboard/static/vendor/simli/`, see the README there).

## Set it up

1. Make a Simli account at simli.com, and **check the current per-minute
   price** there. A session is billed while it's open.
2. In Simli's dashboard, **create a face** from a photo or short video, and
   copy its **face ID**.
   - Use an original character, yourself, or someone who has agreed.
   - Don't use real people who haven't agreed, or studio characters.
3. Copy your **API key**.
4. Put both into Apex, on your PC:

```cmd
.venv\Scripts\python scripts\set_env_key.py SIMLI_API_KEY <your key>
.venv\Scripts\python scripts\set_env_key.py SIMLI_FACE_ID <the face id>
```

5. Restart Apex. In the companion, choose **Apex appears as: Live face
   (Simli)**.
6. Talk to Apex with **Local Qwen** (Celine, streamed: the fastest) or
   **OpenAI voice**.

Never paste the key into a chat; `set_env_key.py` writes it to `.env`, which
Git ignores.

**Optional settings in `.env`:**

| Setting | Default | What it does |
| --- | --- | --- |
| `SIMLI_MAX_SESSION` | `1800` | Longest session in seconds; Simli bills while it's open |
| `SIMLI_MAX_IDLE` | `180` | Simli closes a session after this many silent seconds |
| `SIMLI_MODEL` | Simli's default | A specific Simli model, if your account offers more than one |

## How it works, and what's protected

```
Apex writes a sentence → your voice server speaks it
   → the companion sends that audio (16 kHz) to Simli over the session
   → Simli streams back the face saying it, with the sound (WebRTC)
```

- **The key never reaches a browser.** The companion asks Apex for a session.
  Apex sends the key to Simli and gives the page only a short-lived session
  token. Other websites can't ask Apex for a session.
- **You only pay while it's being used.** Simli bills while a session is open:
  - After 90 seconds of quiet, or as soon as you switch away from the page,
    Apex hangs up and keeps the face's last frame on screen.
  - The moment you type, press the mic, start talking hands-free or send a
    message, it reconnects in the background, usually before the reply is
    ready.
  - If a reply is ready first, it waits for the face. If the face can't
    reconnect, that reply plays as plain audio and the live face is tried
    again next time.
  - A hang-up on Simli's side (its idle limit, a network blip) is handled
    the same way.
- **Stop or talking over Apex** clears whatever the face still has to say.
- **If anything fails, Apex still speaks,** as plain audio, and the orb comes
  back with the reason on the setting. That covers no key, no credit, no
  network, a dropped connection, or a clip that can't be decoded.
- **Works on the car's orb screen too,** through Apex's private address (the
  key stays on your PC).

## Checks

- `tests/test_live_avatar.py`: the session route, against a fake Simli on a
  real port.
  - The key goes only to Simli, in its header.
  - The page gets the token and network servers, never the key.
  - Sessions are capped.
  - Refusals, no key, no network and failed network-server lookups each
    become a clear reason.
  - Other sites are refused.
  - The vendored bundle matches its recorded hash.
- `scripts/check_live_avatar_ui.cjs`:
  - The 16 kHz conversion is exact however the stream is cut, and keeps
    pitch.
  - The face counts as done only when its queued speech has played and gone
    quiet; a pause mid-speech isn't the end.
  - Clip voices and Celine's streamed voice both reach the face, with no
    sample lost.
  - Stop clears it.
  - It hangs up when quiet or hidden, and opens one paid session however many
    things ask at once.
  - It reconnects on typing, waits for a reconnect that's still in progress,
    and speaks as plain audio when it can't reconnect.
  - When Simli isn't set up, or drops, the orb returns and speech plays as
    audio.
  - Every rule was broken once on purpose and caught.
- `scripts/check_live_avatar_browser.cjs` (optional, needs Chromium): the real
  bundle loads and builds a client. When Simli can't be reached, the
  companion goes back to the orb with the reason.

**Not checked here:** a real Simli session. The build machine has no key,
and Simli's servers aren't reachable from it. The first session on your PC
is the real test. Send me what you see, and the setting's message if it
falls back.
