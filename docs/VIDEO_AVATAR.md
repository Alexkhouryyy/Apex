# Apex as a photoreal face

> **The top tier is [LIVE_AVATAR.md](LIVE_AVATAR.md)** (Simli): the whole face moves and it starts talking within about a second, but it's paid and uses the cloud. This page is the free, private option that runs on your own GPU.

Apex can appear as a real-looking person or character on video, whose
mouth moves with what Apex says. It isn't 3D drawn in the browser. It's real
footage of the character, with the mouth area redrawn for every sentence by
an AI lip-sync model, **MuseTalk**, running on your RTX 4070. Because only the
mouth is redrawn, everything else stays exactly as photoreal as the footage.

| | |
| --- | --- |
| **Looks** | As real as the video you give it |
| **Moves** | The idle loop's natural motion (blinks, small head moves); the mouth follows each word |
| **Runs on** | Your PC's GPU, locally. Nothing goes to a cloud service |
| **Seen on** | The companion, voice mode and the car's orb screen, through Apex. A phone or car browser only plays the clips |
| **Cost** | Free (MIT licence); about 10 GB of downloads once |

## How it works

```
Apex writes a sentence → your voice server speaks it (WAV)
   → the avatar server (scripts/avatar_server.py, MuseTalk) redraws the mouth on the idle video to match
   → the companion plays that clip, with the sound, over the idle loop → back to the loop
```

- **While one sentence plays, the next is being rendered.**
- **No jumps:** a clip normally starts on the first frame of the face video,
  while the idle loop could be anywhere, so the head would jump. Here, the
  first clip of a reply starts on the frame the loop will be showing when the
  clip is ready, predicted from how long recent clips took. Each later clip
  starts where the one before ended. When talking stops, the loop carries on
  from the last spoken frame. The loop itself plays forwards then backwards,
  so it never snaps back to the start either.
- **Measured:** each reply's render time is recorded with Apex's voice timing.
  `.venv\Scripts\python -m agent.voice_timing` shows it as *(server: first
  video clip)*, beside the voice's own time.
- **Plain audio as the fallback:** if a clip can't be made or played, the
  sentence plays as audio. Apex is never silent because of the video.
- **No streaming in video mode:** Celine's streamed voice (sound starting
  mid-sentence) is off, because the face needs each sentence whole. Expect
  roughly the time to render one sentence before Apex starts talking.
  MuseTalk claims 30+ frames a second on a V100, a datacentre GPU in roughly
  your laptop's range. **This hasn't been measured on your laptop yet.**
- **Device voice:** the browser's built-in voice gives no audio to lip-sync,
  so only the idle loop plays.

## Set it up (once)

### 1. Install (about 10 GB, 20-40 minutes)

```cmd
Setup-Apex-Video-Avatar.cmd
```

It installs into `%USERPROFILE%\apex-video-avatar`, with its own Python,
because MuseTalk needs older library versions than Apex. MuseTalk is pinned
to the exact version reviewed in
[OPEN_SOURCE_REGISTER.md](OPEN_SOURCE_REGISTER.md). Weights come from
Hugging Face itself; MuseTalk's own Windows script uses an unofficial
mirror, and this doesn't.

**Prerequisites:** Python 3.10 (from python.org, with the *py launcher*
ticked) and Git for Windows. If a step fails, fix what it says and run the
setup again; finished steps are skipped. Check progress with:

```cmd
Setup-Apex-Video-Avatar.cmd --check
```

### 2. Make the character's idle video

This is the part that decides how good it looks. You need **5–10 seconds of
the character**:
- looking at the camera, face clearly visible and front-on;
- blinking and moving a little;
- **mouth closed**;
- with a still background and even light.

**Ways to get one:**

| Way | How |
| --- | --- |
| **An original AI character** (recommended) | 1. Make a photoreal portrait with an image model: describe an original person or character, never a real one. 2. Animate it into an 8-second clip with an image-to-video model, prompting for *"looking at camera, subtle head movement, natural blinking, mouth closed, static camera"*. |
| **Yourself** | Film 10 seconds on your phone in good light, looking into the lens, mouth closed. |
| **Someone else** | Only with their clear permission. That includes Celine. |

**Don't use a real person who hasn't agreed.** That covers celebrities and
characters owned by studios, such as Iron Man or Batman.

Then turn it into the loop MuseTalk wants (25 fps, at most 10 s, at most 720p
tall). That takes one command, and it works with any phone or AI video:

```cmd
Start-Apex-Video-Avatar.cmd --make-idle C:\path\to\your-video.mp4
```

It writes `%USERPROFILE%\apex-video-avatar\apex-idle.mp4`.

### 3. Start it, beside Apex

```cmd
Start-Apex-Video-Avatar.cmd
```

The first start prepares the face (face detection and masks), which takes a
few minutes; later starts reuse it. Wait for `AVATAR READY (musetalk)`.

### 4. Use it

Companion → settings → **Apex appears as: Video (photoreal)**. Then talk
to Apex with **Local Qwen** or **OpenAI voice**.

**No GPU yet?** `Start-Apex-Video-Avatar.cmd --engine still` plays the idle
loop with the voice but without lip-sync, which is useful to check
everything else works.

## When something's wrong

| You see | Why |
| --- | --- |
| The option says *Video (avatar server off)* | The avatar server isn't running, or `AVATAR_URL` in `.env` doesn't match its port (default `http://127.0.0.1:17495`) |
| `No face was found in the video` | The face is too small, turned away or dark. Use a closer, front-on, well-lit shot |
| The mouth area looks smeared | The source video's mouth wasn't closed, or the face is very small in the frame. Crop closer, or reshoot |
| Long pause before each reply | The clip is still rendering. Check the speed in the avatar server's console (`X-Render-Ms`), and send me the numbers |
| Sound, but the face doesn't move | That sentence's clip failed, and Apex fell back to audio (by design). The avatar server's console says why |

## Checks

- `tests/test_video_avatar.py` covers everything except the MuseTalk engine:
  - clips in both browser formats (MP4 for Chrome, Edge and Safari; WebM for
    others), matching the voice's length;
  - the idle-video maker;
  - the server's rules: only this PC can use it, and only from Apex, never
    from a web page; sizes and formats;
  - Apex's routes against a real avatar server on a real port;
  - the installer's step tracking, and that it never uses a mirror.
- `scripts/check_video_avatar_sync.cjs`: the frame choices.
  - The look-ahead.
  - Chaining sentence to sentence.
  - The loop resuming at the last spoken frame.
  - A new reply, or a Stop, going back to the loop.
  - Every rule broken once on purpose and caught.
- `scripts/check_video_avatar_ui.cjs`:
  - each sentence becomes a clip;
  - streaming is off in video mode;
  - audio is the fallback when a clip can't be made or played;
  - the orb stays, with a reason, when the server is off.
- `scripts/check_video_avatar_browser.cjs` (optional, needs Chromium): real
  clips from a real avatar server, played in a real browser, including the
  fallback.

**Not checked here:** the MuseTalk engine itself, which needs your GPU and
its weights, so it hasn't run yet. It follows MuseTalk's own real-time script
step for step. The first run on your laptop is the real test; send me the
console output either way.
