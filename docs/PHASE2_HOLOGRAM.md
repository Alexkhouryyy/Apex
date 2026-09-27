# Phase 2a: the hologram look (motor study)

The motor study (`/study`) now looks and responds like a hologram. This pass
changes only how things **look and sound**. Every grab, move and release still
goes through the same gesture rules as before (`study-hands.js`), and nothing
new is saved or sent to the server.

## What you see

- **Your hand, drawn in light.** All 21 joints of the tracked hand, as a
  glowing skeleton over the model, placed where your cursor is (reach and
  centring apply). The thumb and index tips brighten as they close, a
  filament between them strengthens, and a ring closes when the tracker
  decides it's a pinch. The glow is decoration only: whether it *is* a pinch
  is still the tracker's decision. A hand not seen for about a third of a
  second fades out rather than freezing.
- **The hologram style.** Glowing cyan edges on every part, bloom (soft light
  spill), a dark space with depth fog, and a lit grid floor.
- **Parts that react.** The part under your hand glows as you hover, brighter
  as the hover completes. When it's ready to grab it **rises slightly toward
  you**, a visual effect only that is never saved. A grab flashes.
- **Sound.** Soft synthesized tones for "ready", grab, release, cancel and
  a mode switch. No audio files. The browser allows sound only after you
  click the page or press a key.

## Controls

- **◈ Futuristic / ◇ Normal** in the header is the Apex-wide look switch (see
  *The Apex look* below). Futuristic shows the hologram and Normal shows the
  plain view. **Sound on / off** mutes the tones. Both are remembered in this
  browser.
- `prefers-reduced-motion` turns off the rise and flash animations.

## Performance safeguards

The hologram costs frame time. Slow frames delay the page's hand requests
until they are discarded as stale, and hands that lag feel far worse than a
missing glow. Three safeguards:

1. **No graphics acceleration: it starts off.** The browser reports its 3D
   renderer, and software renderers (SwiftShader, llvmpipe, Microsoft Basic
   Render Driver) keep the 3D hologram off, saying why. The rest of the
   futuristic look still applies. There is no override: on software rendering
   the hologram costs working hands.
2. **Slow frames drop the glow first.** If frames stay slower than 1/25 s for
   two seconds, bloom switches off. The edges and the drawn hand stay
   ("Glow paused…").
3. **Still slow: the hologram pauses** for the session ("Hologram
   paused…"). Switching the look to Normal and back retries.

Why this matters, measured on this GPU-less test machine: with the hologram
on, the study's real-browser check passed **1 of 4** runs, because hand
control timed out. With it off, 3 of 3. With safeguard 1, the unmodified
check passes 3 of 3. On a real GPU (the RTX 4070 laptop) frames should take a
few milliseconds, but that hasn't been measured yet.

## The Apex look: Futuristic or Normal

One switch for every page: the dashboard, companion, workspace board (and the
car view inside it) and the motor study.

- **Futuristic** (the default) layers a hologram style over each page: a faint
  projection grid, scanlines, glowing panel edges with HUD corner brackets,
  glowing headings, technical mono labels, and lit buttons and inputs. The
  companion's orb gets an orbiting ring. The motor study adds its 3D
  hologram. The dashboard's glow follows its chosen colour palette.
- **Normal** is each page exactly as it was before this layer existed.

**Switching.** Use the **◈ Futuristic / ◇ Normal** button: bottom-right on most
pages, top-right on a phone, in the header on the study. The choice is saved
in this browser (`apex.look`), applied before the page paints so there is no
flash, and followed by other open tabs. Pages embedded in another page (the
companion inside the board) don't get a second button.

**Implementation.** `dashboard/static/theme.js` sets `<html data-look>` and
fires an `apex:look` event, which the study listens for.
`dashboard/static/theme.css` scopes every rule to
`html[data-look="futuristic"]` and moves or resizes nothing. The one
exception: labels use a mono font with wider spacing, so a label can get
slightly wider. `scripts/check_theme_ui.cjs` enforces all of this and checks
that each page loads both files in the right place.

## Verification

- `scripts/check_study_holo.mjs`, run with the real three.js: pinch glow
  range, governor (steady slowness trips it, a brief hitch doesn't),
  sound silent until the page is touched, mute remembered, the drawn hand
  (all bones and joints, fade, no partial skeletons), and scene edges, glow,
  lift and toggle. Checked by breaking the code on purpose: lift-while-held,
  glow not restored, governor never trips, toggle leaves edges, no fade.
  Each fails the check.
- `tests/test_handtrack.py::TestHandJoints`: the tracker sends all 21 joints,
  mirrored like the cursor, clamped, and all-or-nothing.
- The existing real-browser study check passes with the hologram on.
- Three.js post-processing files are vendored unmodified from 0.160.0
  (see `vendor/three/README.md`).

## Seen while building this (to check with recordings)

In a synthetic hand where the **index** fingertip moves to the thumb, the
pointer shifts about 25 px on pinch and can land off the hovered part. The
armed-part margin is 20 px. Pinching by bringing the **thumb** to the index
keeps the pointer still. Real recordings should show which way people
actually pinch. If the index moves, the fix is to aim with a point between the
fingers, or to freeze the aim during pinch closure.

## Phase 2b: moving it with your hands

Three gestures, on every subject in the library (built-in and imported).

**Pull it apart with two hands.**
- Pinch with both hands at about the same moment (within 0.45 s) and pull
  them apart: the model separates live, following a spring with a slight
  bounce.
- Push your hands together to reassemble. Hands (nearly) touching always
  means fully assembled, wherever you started.
- Pulling apart by about half the view width goes from assembled to fully
  apart. Small tremors (a 2% dead zone) never move it, and it snaps to fully
  closed or open near the ends.
- **Let go to keep it.** Only then is the separation saved (one undoable
  step).
- **A fist cancels** and returns to where you started. So does a hand
  hidden for more than 0.18 s, or a tracking jump.
- It never starts while one hand is holding a part: single-hand grabs win.
- The camera's zoom-out after separating doesn't block the next pull.

**Spin with momentum.**
- Hover an open hand over empty space until it reads *empty space · pinch
  and drag to spin*, then pinch and drag to turn the view.
- Let go while still moving and it keeps turning, slowing to a stop in a
  second or two.
- Speed is measured over the last 0.12 s of the drag. A hand that stopped
  before letting go doesn't coast, and speed is capped so a tracking glitch
  can't fling the view.
- A new pinch, a mouse drag or Reset view stops it. Spinning changes the
  view only, never a component.
- With reduced motion turned on, there's no coasting.

**"Take this apart" while pointing.**
- While an open hand rests on a part (the ready ring), the study remembers
  it for 8 seconds, as the board does.
- Céline gets it as `pointed_part` with `seconds_ago`, and is told:
  - "this" and "that" mean the pointed part when it's recent, otherwise the
    selection;
  - to ask when the two disagree;
  - "take this apart" means select it, then separate the model (isolating
    it only if asked).
- Only the window that owns hand control can report pointing.

**Timing on slow machines.** Timed animations (the camera zoom, easing
between separations, the spring) now run on real elapsed time. Before this,
a slow renderer stretched a 0.65 s camera move into several seconds, and hand
grabs were refused ("wait for motion to stop") the whole time.

**Checks:**
- `scripts/check_study_gestures.mjs` (in CI): the gesture rules, the
  spring's overshoot and settling, and coasting.
- `tests/test_study_pointing.py`: pointing memory, age and ownership, and
  Céline's prompt.
- `scripts/check_study_gestures_browser.cjs` (optional, real browser):
  synthetic two-hand frames through the real endpoint. It checks that pulling
  saves only on release, pushing together reassembles and a fist writes
  nothing. It also checks that a continuous flick keeps the view spinning and
  that the pointed part reaches Céline's context.

**Not verified yet: how it feels with a real camera.** The gains (half a view
width for fully apart, the spring's bounce, 2.2/s friction) are first
choices. Tune them after trying them.
