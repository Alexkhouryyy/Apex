# Study hand interaction and camera mirror — 2026-09-26

## Request

Improve hand tracking in the workspace and motor study so fingers are detected
reliably, movement feels smooth, and grabbing/releasing parts takes little
effort. Diagnose missed gestures and jitter, improve the tracking and interaction
code, and make control states clear. Preserve protection against accidental
actions and distinguish automated results from real Lenovo testing. Add an
optional camera mirror that can be hidden without stopping hand tracking.

## Changes

- Camera scheduling counts capture and inference time toward the configured
  frame interval. Previously each detection added a complete extra interval.
  Slow inference lowers the rate naturally; there is no catch-up queue. Detector
  timestamps use actual monotonic time. Study freshness includes capture and
  inference time, and the input gate also includes request round-trip time.
- Study freezes a held part for up to 180 ms of missing-hand observations. Only
  the same identity, still pinched and without a large jump, can continue it.
  A hand returning open cancels rather than applying an unseen release. Stale
  input, a closed fist, a jump, or longer loss cancels. A release requires fresh
  open observations at least 55 ms apart; one noisy frame cannot apply a move.
- Open hover arms a part in 200 ms. Hand picking samples within a 20 CSS pixel
  margin. Pinch closure prefers the already armed part within that margin;
  distant targets do not qualify. The initial movement plane uses the cursor's
  own projection to prevent snapping from the assisted hit. Mouse picks remain
  exact. Existing per-hand adaptive smoothing and calibrated pinch thresholds
  are retained.
- **Enable hands / Pause hands** labels state the available action. The status
  names the interaction mode and hovered component. The ring distinguishes
  ready and pinched states. **Hand setup** offers an optional live fingertip
  guide, pinch ratio/threshold feedback, and a link to existing calibration.
  The guide is visual feedback, not an alternative gesture input. Explore /
  select selects a part; choose Move across view, Move in depth, or Turn
  component for manipulation.
- **Camera mirror / Hide camera** controls a small live preview. It reuses the
  hand tracker's already-owned camera, in the same orientation as tracking;
  it never opens a competing camera or changes gesture ownership. Off by
  default, authenticated, no-store responses; no preview files are saved. Hide
  clears the image, aborts requests and stops polling. Hidden tabs suspend it.
  Unavailable-camera errors are shown rather than leaving a frozen picture.
  It requires Apex hand tracking to be running on the host.

## Evidence and limits

Synthetic detector tests exercise frame budgets and confirm detector age includes
inference time. Gesture sequence checks cover loss/recovery, noisy release,
fists, stale data, identity changes, and rearming. Real Three.js raycasts verify
narrow-part assistance and its distance bound. Mirror lifecycle tests cover
opt-in requests, authentication, hide/abort/cleanup, tab suspension, and errors.
The real server/WebGL check covers study manipulation, JPEG preview, the finger
guide, hiding the mirror with hands still enabled, and returning to the board.

These tests do not establish real camera recognition accuracy. No confidence
threshold was loosened to manufacture a higher grab rate. Lighting, occlusion,
camera resolution and actual Lenovo performance still require a physical test.
No hand images, coordinates, or fingertip data are added to exported diagnostics.

## Lenovo acceptance check

1. Enable hands, open Hand setup and show the finger guide. Confirm detected
   index/thumb positions and whether pinch changes at your calibrated threshold.
2. Show/hide Camera mirror while hands stay enabled. Check that the preview's
   orientation follows your movement. Calibrate from Hand setup if required;
   returning from another tab deliberately requires Enable hands again.
3. Choose Move across view, hover until Ready, pinch, move, then release. Repeat
   on both hands, narrow parts, and the detailed CAD model. Record misses and
   unintended actions; do not infer accuracy from the number of passing tests.
4. Briefly occlude the held hand: position should freeze and recover. Longer
   loss or a fist cancels. A different hand must never inherit the held part.
5. Export an opt-in Device check report and note comfort/lag during 10 minutes
   of real use. The timings remain diagnostics, not end-to-end latency proof.

## Follow-up: mode switching and comfort

Alex's Lenovo feedback after PR #9: grabbing, moving, releasing and tracking
are better; switching actions and arm comfort are now the main friction.

- A mode bar inside the study viewport offers Select, Move, Turn, Depth,
  Orbit and Zoom. It appears while hands are enabled. Hold the same open
  hand over a button for 650 ms; progress fills the button, and the selected
  mode is marked. Moving through the bar or holding a pinch does not switch.
  Mode hover never runs while a component/view is held or a commit is pending.
  Leave the bar and arm a fresh pinch to act. Mouse and keyboard buttons and
  the existing dropdown remain available.
- Orbit and Zoom use the same deliberate hover/pinch/release gate as part
  movement, including loss, stale input, fist, and jump cancellation. Cancel
  restores the original camera; confirmed release keeps the view. Camera
  gestures do not write component transforms or study history. Saved projects
  already capture camera position and target. Zoom respects existing limits.
- Hand setup & comfort offers Full, Less (1.5×) and Small (2×) reach. Defaults
  preserve the existing mapping. Centre here waits two seconds, then requires
  650 ms of fresh, steady open-hand observations to map a comfortable hand
  position to the viewport centre. Rest an elbow and keep the hand visible.
  No component action can run during centring; eight seconds without success
  times out. Reset reach returns to full camera mapping.
- Mapping also moves the finger guide, but physical jump protection uses the
  original camera coordinates. Reach settings stay only in the current tab
  until reload, and never enter diagnostics or saved projects. Larger gains
  reduce travel but magnify small movements, so they remain an explicit choice.
- This pass changes the motor study controls. It does not alter the shared
  detector, pinch thresholds, board controls, or Céline's voice performance.

Verification covers reach mapping, fresh/identity-stable hover switching, held
mode lock, raw jump cancellation, real Three.js orbit/zoom and cancellation,
and the real server/browser flow for hovering a mode, a camera gesture with
no component revision, centring, reduced reach, reset, and pause. Existing
study save/restart, CAD movement, mirror and diagnostics checks also run.
Automated camera samples do not establish ergonomic comfort or recognition
accuracy on the Lenovo.

### Lenovo check for this pass

1. Enable hands. Open Hand setup & comfort. Start with Less reach (1.5×),
   choose Centre here, then rest your arm and hold an open hand comfortably
   within the camera view. Wait for the centring confirmation.
2. Hover the mode bar's Move button until selected. Return to a component,
   hover, pinch, move and release. Repeat with Turn, Depth, Orbit and Zoom.
   Zoom moves in/out when you move your pinched hand up/down.
3. While holding, pass over the mode bar: it must keep the current action.
   Cancel an orbit/zoom with a fist: the previous view should return.
4. Lower your hand between completed actions. Compare Full versus Less reach
   for ten minutes. Try Small only if more reach reduction is needed; return
   to Full if precision suffers. Report shoulder comfort, accidental mode
   switches and how often you still need the mouse.
