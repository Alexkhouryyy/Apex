# Apex's character

Apex can appear as a full-body character instead of the orb. It's a
new android made just for Apex:
- **Body:** dark armour with a teal glowing edge.
- **Heart:** a glowing core in its chest, which is the orb.
- **Face:** a visor with eyes that blink and follow your pointer, and a
  mouth that moves with the voice actually playing.

It's built from shapes in code with three.js, which Apex already includes.
There's no model file to download, and it isn't anyone's likeness.

## Turn it on

Companion → settings → **Apex appears as: Character**. The choice is
remembered on that device.

| Where | What you see |
| --- | --- |
| Companion, normal view | Head and shoulders, in the orb's place |
| Voice mode | The whole body, large |
| Car orb screen (`/drive#orb`) | The whole body, filling the screen |

## What it does

| Apex is | The character |
| --- | --- |
| Idle | Breathes, shifts its weight, blinks, follows your pointer |
| Listening | Leans in and tilts its head; the core turns blue |
| Thinking | Looks up and brings a hand to its face; the core turns violet and spins |
| Speaking | The mouth opens with each syllable; it talks with its hands, more when louder; the core brightens with the voice |

**The mouth follows the real audio**, whichever voice is speaking:
- **Celine (streamed):** Apex measures the audio as it plays.
- **OpenAI or recorded voice:** a copy of the clip is measured, and the clip
  itself plays untouched.
- **Device voice:** the browser gives no audio to measure, so each spoken
  word pulses the mouth.

**Reduced motion** (your system setting) turns off the sway and gestures. The
mouth and blinks stay.

**No WebGL** (some old phones and car browsers): the orb stays, and the
setting says *Character (needs WebGL)*.

## Checks

- `scripts/check_avatar_pose.cjs`: the body language as numbers.
  - The mouth moves only while speaking, and opens wider when louder.
  - Each state reads differently, and gestures grow with loudness.
  - Reduced motion keeps only the mouth and blinks.
  - About 15 blinks a minute.
- `scripts/check_avatar_ui.cjs`: the setting.
  - Off by default and remembered.
  - Waits for its code to load and follows Apex's state.
  - The device voice moves the mouth.
  - Switching back releases it, and without WebGL the orb stays.
- `scripts/check_voice_stream_ui.cjs`: Celine's streamed audio goes through
  the meter and still reaches the speakers.
- `scripts/check_avatar_browser.cjs` (optional, needs Chromium): real WebGL
  and real audio. For both a clip and streamed voice, the mouth opens on
  syllables and closes between them.

Every rule above was broken once on purpose, and a check failed each time.

## Next, if you want them

- **Visemes:** mouth shapes per sound (O, E, M…) instead of open/closed, from
  the reply text and its timing.
- **Your look or Celine's:** the same body, styled after a person, only with
  that person's agreement.
- **On the board:** the character standing in the 3D workspace, pointing at
  what it's talking about.
