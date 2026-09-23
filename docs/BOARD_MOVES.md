# What your hands can do on /board

Open `http://127.0.0.1:7860/board`. Press **D** (or add `?diag=1`) for the
live readout, which shows what the tracker sees and why a move did or did not
happen.

| Move | How | What happens |
|---|---|---|
| **Grab** | Pinch thumb to index on a card and hold a beat | It follows your hand |
| **Scale / rotate** | Grab with both hands, pull apart or twist | Bigger, smaller, turned |
| **Cancel** | Open your palm and hold it still while holding | The card goes back where it was |
| **Point** | Open hand over a card | Amber dashed ring — this is what "this" means |
| **"Make this blue"** | Point (or select), then talk in *Talk to Apex* | Apex acts on the card you pointed at |
| **Throw away** | Grab, flick toward an edge, let go while moving | Gone. Say **"undo"** to bring it back home |
| **Next / previous** | Open-hand swipe right / left | Steps the selection through your cards |
| **Summon Apex** | Open-hand swipe up | Opens the Apex panel, listening |
| **Show me …** | "Show me my calendar" with a hand up | The card appears **at your hand** |

## The rules that stop these fighting each other

- **Swipes never fire while you're pinching or holding.** Dragging a card fast
  is a swipe to the recognizer; this is what stops it paging the selection out
  from under you.
- **Swipes pause for a moment after you let go**, because the tail of a throw
  is a fast directional movement too.
- **A throw needs speed *and* direction.** A brisk move that ends mid-board is a
  drag. A slow slide to the edge is a drag. Only fast-and-off-the-board throws.
- **Two hands never throw.** Two hands on a card is how you hold it carefully.
- **"This" is remembered for 8 seconds** — you point, *then* speak. Apex is told
  how long ago you pointed, and asks when it's unclear which card you mean.
- **Every throw is undoable**, back to where the card was picked up, not to the
  edge it was flung at.

## Tuning

These first values have not been measured on anyone's hand yet. If throws
misfire, change them in `agent/board.py` (they are named and commented):
`FLICK_SPEED`, `FLICK_PROJECT_SECONDS`, `POINT_MEMORY_SECONDS`.

Swipes are mapped in `HANDTRACK_GESTURE_ACTIONS`. **If your `.env` has that
line from an older copy of `.env.example`, it overrides the new defaults and
swipes will do nothing** — delete the line, or add
`swipe_up:board:summon,swipe_left:board:prev,swipe_right:board:next`.

Each gesture has a 3-second cooldown (`HANDTRACK_GESTURE_COOLDOWN_SECONDS`), so
one swipe in each direction every 3 seconds.

## Works offline

`/board` no longer needs the internet. three.js 0.160.0 (MIT) is bundled under
`dashboard/static/vendor/three/`, and `.js` is always served as JavaScript,
even on Windows machines whose registry says `text/plain` (ES modules refuse
to run otherwise). `tests/test_board_offline.py` fails if anyone adds a CDN
import back. Opening `/board?token=…&diag=1` now removes only the token from
the address bar, so the diag panel is still there after a reload.

## Wave, pinch-hold, swipe-down

These were only wired in `--resident` mode, so everywhere else they did nothing
without saying so. They now work in every mode, and each one writes what
happened to the dashboard's event feed:

| Gesture (default action) | `--wake` | voice | `--text` / `--tui` |
|---|---|---|---|
| wave (`wake`), pinch-hold (`listen`) | starts listening | "already listening" | "ignored — no microphone" |
| swipe-down (`stop`) | cuts Voicebox speech mid-sentence; other TTS engines finish the sentence first | same | same (nothing is spoken, so "nothing to stop") |
