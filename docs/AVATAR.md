# Apex's character

Apex can appear as a full-body character instead of the orb. There are two
ways to get one:

1. **The built-in suit, Apex Mk I:** made in code for Apex, nothing to
   download.
2. **A model of your own:** any rigged humanoid 3D model. Drop it in a
   folder, and Apex drives it with the same body language, voice and glow.

## Turn it on

Companion → settings → **Apex appears as: Character**. The choice is
remembered on that device.

| Where | What you see |
| --- | --- |
| Companion, normal view | Head and shoulders, in the orb's place |
| Voice mode | The whole body, large |
| Car orb screen (`/drive#orb`) | The whole body on its projector base, filling the screen |

## Apex Mk I

An armoured suit, built from about 70 parts in code (`avatar-suit.js`):
- **Under-suit:** dark, with a fine hexagonal weave.
- **Armour plates:** gunmetal with a clear coat, reflecting a studio
  environment. They include a curved breastplate with pectoral plates,
  segmented abdominal bands, layered shoulder armour, gauntlets, knee guards,
  shin ridges and boots.
- **Energy lines:** teal seams that really glow. The glow is applied only to
  lit parts, so the armour stays crisp and the page shows through behind it.
- **Helmet:** a faceted dark mask under a polished skull cap, angled glowing
  eye slits, swept-back fins and a vocal grille.
- **Reactor:** the orb as a glowing chest core, with spinning rings.
- **Projector base:** spinning rings and a faint light beam.

**Suit-up:** when it appears, the plates fly in and lock on from the feet up,
then the eyes and reactor power on. It waits until the first frame has
actually drawn, so you see the whole sequence even if the graphics card takes
a moment to warm up.

| Apex is | The suit |
| --- | --- |
| Idle | Breathes, shifts its weight, follows your pointer; teal energy |
| Listening | Leans in and tilts its head; eyes brighten; the energy turns blue |
| Thinking | Hand to its face, looks up; a scan sweeps across the eyes; violet energy; the core spins faster |
| Speaking | The vocal grille moves like a sound meter with the real voice; it talks with its hands, more when louder |

**The grille follows the real audio**, whichever voice is speaking:
- **Celine:** measured as it streams.
- **OpenAI or recorded voice:** measured from a copy of the clip.
- **Device voice:** pulsed on each spoken word.

**Reduced motion** (your system setting) skips the suit-up, sway and
gestures. **A slow device** turns the glow off by itself rather than stutter.
**No WebGL:** the orb stays, and the setting says *Character (needs WebGL)*.

## Your own model (for a film-quality look)

A suit built in code tops out at good game quality. For a film-quality look,
use a model sculpted by an artist or by an AI 3D generator:

1. Get a **rigged humanoid** in `.glb`. Any of these works:
   - **An AI 3D generator** that exports a rigged character, from a text
     description like *"sleek armoured android, gunmetal and teal energy
     lines, original design"*.
   - **Any humanoid mesh, auto-rigged with Mixamo** (Adobe, free with an
     account), then exported to `.glb`, for example with Blender.
   - **A 3D artist**, or a model you've bought with a licence for your use.
2. Save it as `dashboard/static/avatars/apex.glb`. Models here aren't
   committed.
3. Reload the companion. Apex uses it instead of the Mk I.

**What it drives:**
- **Bones, found by name:** Mixamo (`mixamorig:LeftForeArm`), VRoid/VRM
  (`J_Bip_L_LowerArm`) and Blender (`UpperArm.L`).
- **Arms:** brought down from a T- or A-pose, then the same poses as the suit.
- **The mouth,** if the model has a `jawOpen` / `mouthOpen` / `viseme_aa`
  shape key; otherwise a jaw bone; otherwise only head and hands move with
  the voice.
- **Glow:** parts with glowing materials get it.

**Tested with:**
- **A standard Mixamo rig:** right size, arms down, every pose correct.
- **A cartoon robot with a non-standard rig:** loads and moves, but its arms
  bend oddly. Odd rigs may need a standard skeleton (re-rigging in Mixamo
  fixes that).

**Don't use real Iron Man or Batman models.** They belong to Marvel and DC.
Aim for that quality with an original design.

## Checks

- `scripts/check_avatar_pose.cjs`: the body language as numbers.
  - States, gestures, blinks, reduced motion, eye glow, the thinking scan and
    the grille bars.
  - Bone names across Mixamo, VRoid and Blender.
- `scripts/check_avatar_ui.cjs`: the setting.
  - Off by default and remembered; waits for its code; follows Apex's state.
  - The device voice moves it; it's released on Orb.
  - Your own model is used when it's there.
  - Without WebGL, the orb stays.
- `scripts/check_voice_stream_ui.cjs`: Celine's audio is metered and still
  reaches the speakers.
- `scripts/check_avatar_browser.cjs` (optional, needs Chromium): real 3D and
  real audio; the voice level rises on syllables and falls between them.

Checked by eye in real Chromium:
- every state;
- the suit-up mid-sequence;
- the car and phone layouts;
- a Mixamo model and a cartoon robot loaded through the model slot.
