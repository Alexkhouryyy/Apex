# Voices: yours, Celine's, anyone who agrees

Apex's own local voice servers can speak in any number of cloned voices. A
voice is about 25 seconds of natural speech plus the exact words said in it.
There's no training and no new model: the same Qwen3-TTS model that speaks as
Celine builds each clone from its recording, on your GPU, for free.

The voice model (Qwen3-TTS) is the mouth. The language model (DeepSeek, or
your local Qwen text model) is the brain, and it has no voice of its own.

## Make a voice

1. Start Apex with `Start-Apex-Celine-Fast.cmd`, as usual.
2. Open `http://127.0.0.1:7860/voices`. The companion links to it as
   **+ New voice**, next to the voice picker.
3. Type a name (for example `Alex`), then press **Record** and read the script
   out loud. Or upload a recording you already have.
4. Apex checks the recording and tells you in plain words what to fix:
   - length (20–30 seconds is best);
   - distortion;
   - too quiet;
   - background noise;
   - whether the words match the text.

   It listens with local Whisper to compare the words, and **Use these words**
   copies what it heard into the text box. A wrong transcript is the most
   common cause of a bad clone.
5. Tick the consent box and press **Save voice**. Then **Try** it, or pick it in
   the companion's voice list. It works straight away; you don't need to
   restart.

**Tips**
- The clone copies the recording's tone. Read it warm and relaxed if that's
  how you want it to sound.
- Use a quiet room, and hold the phone or mic about a hand's width away.
- Recording turns off the browser's echo cancellation, noise suppression and
  automatic volume, because they reshape a voice and the clone would copy that.

## Where voices live

```
%USERPROFILE%\apex-voices\        (APEX_VOICES_DIR to move it)
    alex\reference.wav            the recording (wav, flac, ogg, mp3, m4a or webm)
    alex\transcript.txt           exactly the words in it
    alex\voice.json               the display name, and the checks when it was saved
```

You can also make a folder by hand. The servers re-read this folder on every
request.

**Celine's original recording** (`Downloads\celine.ogg`) keeps working as the
voice `CELINE` with no folder. A `celine` folder replaces it. Celine is the
default voice when a request doesn't name one, and her personality comes with
her voice. Other voices use Apex's usual personality.

**Removing** a voice moves its folder to `apex-voices\.trash`, so you can put it
back.

## Rules

- **Only clone people who agree.** Saving asks you to confirm it. Recordings
  stay on this computer.
- Saving and removing voices need the master dashboard token. A per-device
  token, such as a phone's, can use voices but can't make or remove them.

## Limits

- **Voicebox app:** these voices are for Apex's own servers (`Start-Apex-Celine*.cmd`).
  If Apex is set to use the Voicebox desktop app instead, that app keeps its
  own voices; add them there. The Voices page tells you which one Apex is
  using.
- **One voice at a time.** One sentence is generated at a time on the GPU,
  whichever voice it is.
- **First sentence in a new voice** takes about a second longer while its
  clone is prepared. After that it's cached.
- **Changing the script:** the Voices page offers a ready-made script, which
  you can change. The text box must match what you actually say.
