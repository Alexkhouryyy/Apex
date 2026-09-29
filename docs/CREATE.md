# Create with voice and hands

Say what to make. Apex builds it on the board from named parts. Shape it by
voice or with your hands, then take it apart in the study.

## 1. Build it

> "Build me a gearbox: a base plate, two shafts, a 24-tooth drive gear and a
> smaller driven gear."

`board_build` composes the object from parts. Every part is a primitive fitted
into a box, sized in centimetres:

| Shape | Notes |
|---|---|
| box, sphere, cylinder, cone, torus | As before. |
| **gear** | A spur gear lying flat: `size` is [diameter, thickness, diameter]. `teeth` 6–80 (default 16). `hole` is the bore as a share of the width, 0–0.9 (default 0.25). |
| **tube** | A hollow cylinder. `hole` 0.05–0.9 (default 0.6). |
| **wedge** | A ramp: full base, full back wall, sloping face toward the front. |

Colours: plain names (red, silver, gold, brass, copper, steel, steel blue,
navy, teal, wood…) or `#rrggbb`.

## 2. Shape it by voice

`board_edit` makes one precise change and saves it as a **new version**, so
Undo brings the previous one back. The parts you didn't touch stay where they
are.

| Say | Edit |
|---|---|
| "make the shaft red", "make the gears brass" | `color` |
| "move this up 5 cm", "move the motor mount left 3 cm" | `move` |
| "make the drive gear bigger", "make it twice as big" | `scale` (a part, or `all` for the whole build, which keeps its proportions and its floor) |
| "make the shaft 30 cm long" | `resize` |
| "turn the fin 45 degrees" | `rotate` |
| "remove the fins", "duplicate the wheel" | `remove`, `duplicate` |
| "add a knob on top" | `add` |
| "make this a gear with 30 teeth" | `shape` |
| "call it the output shaft" | `rename` |

**Which part:**
- **By name:** "the drive gear". A word matches its plural ("fins"), and when
  a name matches several parts Apex asks which one, unless you say all of them.
- **By number:** "part 3".
- **"This":** the part you **clicked or pinch-tapped** (a quick pinch in parts
  mode) in the last 8 seconds.

## 3. Shape it with your hands

Turn on **parts mode** ("take it apart" or `board_parts`), then:
- **Move a part:** pinch it and drag.
- **Resize it:** add a second hand and change the spread.
- **Select it:** a quick pinch-tap, so "make this red" knows which part.

Every hand edit is a new version too.

## 4. Study it

> "Study it."

`board_study` puts the build into the study library under **Built by you**,
using its own part names, and opens it:
- take it apart;
- isolate and section parts;
- keep notes;
- ask Céline about any part.

Céline can pass a short explanation for each part as notes; they are labelled
AI-drafted, as with imported models.

## Limits, honestly

- **Builds are simple shapes put together.** Good for ideas, mechanisms and
  explaining how things fit; not engineering CAD. There are no fillets,
  threads, holes through arbitrary shapes or exact gear meshing.
- **"This" needs a selection.** It only works after you click or pinch-tap a
  part; otherwise Apex asks which part you mean.
- **Studying a build copies it.** Later edits on the board don't change the
  study copy; say "study it" again for a fresh one.
