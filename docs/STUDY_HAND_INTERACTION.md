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
