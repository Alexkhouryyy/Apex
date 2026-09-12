# Forge — from a design to a thing a machine can make

Phase 12 of the blueprint, and the whole phase lives in one adjective:

> A designed object reaches a **validated manufacturable** representation.

Apex could already design. `board_create` builds a measured primitive in
Blender and `agent/assets.py` keeps every export as an immutable version with
the command that produced it. What came out was `.glb` — a format for *looking*
at things. Send one to a printer and nothing happens.

Forge answers the three questions between a mesh and a part.

## Using it

Talk to it. The tool is `apex_forge` and it has three actions.

```
"Apex, make me a 40 by 60 millimetre cylinder I can print"
"Apex, is the phone stand printable?"
"Apex, export the phone stand as a 3MF"
```

From the shell, the same thing:

```bash
python3 -c "
from agent import core
print(core._execute_tool('apex_forge', {
    'action': 'make', 'shape': 'cylinder',
    'dims_mm': {'diameter': 40, 'height': 60}, 'title': 'Spacer'}))"
```

`make` needs no Blender. It builds the solid itself, in millimetres.

Files land beside the design they came from, as a new version of the same
object: `~/.apex/props/created/<slug>/v2.3mf`. An export is the same object
converted, not a separate thing with its own history, so its lineage points
back at the version it came from.

## What it checks

| Check | Fails when | Why it matters |
|---|---|---|
| watertight | an edge is used once (a hole) or three or more times | an open surface has no inside for a slicer to fill |
| normals | two faces share an edge in the same direction, or the enclosed volume is negative | part of the surface is inside out, and the part slices inverted |
| volume | it encloses none | a zero-thickness sheet is not an object |
| wall thickness | the thinnest measured wall is under two nozzle widths | that wall cannot be extruded |
| build volume | it does not fit the machine | — |
| scale | the largest dimension is under 1 mm | almost always a unit error; the message gives the same numbers read as metres and as inches |
| overhangs | *warns* past the support angle | printable with supports, not unaided |

Overhang angle is measured from vertical, the way a slicer's support threshold
is: a wall is 0°, a ceiling is 90°. The face the part rests on is excluded — it
is the steepest possible overhang by that measure and needs no support at all,
and a checker that flagged the bottom of every cube would be ignored by
Tuesday.

## The third state

Every check has four outcomes, not two:

```
ok        measured, within limits
warn      measured, printable but needs attention
fail      measured, outside limits
unknown   NOT measured
```

A report containing any `unknown` is **`unverified`**, never
`manufacturable` — and `unverified` does not export. This is the one design
decision worth arguing about, so here is the argument: the failure this
codebase keeps finding is a check that runs, finds nothing because it never
really looked, and reports success. Collapsing "I could not measure this" into
"this passed" is how a validator becomes indistinguishable from no validator.

`export` refuses before it writes. A refused export leaves no file and records
no version, so the file and the verdict cannot come apart. `force` overrides it
and still returns the failing report, because a human may legitimately want the
file to repair elsewhere.

## The machine

"Is this manufacturable" has no answer in the abstract. A 0.3 mm wall is
impossible on a 0.4 mm nozzle and routine on a 0.1 mm one. In `.env`:

```
FORGE_NOZZLE_MM=0.4
FORGE_MIN_WALL_MM=          # blank = two nozzle widths
FORGE_OVERHANG_DEG=45
FORGE_BUILD_X_MM=256
FORGE_BUILD_Y_MM=256
FORGE_BUILD_Z_MM=256
FORGE_DEFAULT_FORMAT=3mf
```

## Units, which is the real problem

No mesh format in common use states its unit. **STL never has.** glTF's
convention is metres, Blender's is whatever the scene says, and a `50` in a
file is fifty of something nobody wrote down. The most common failure in
desktop manufacturing is a part that arrives 25.4× or 1000× wrong, and it is a
*silent* failure: the file is valid, the print succeeds, the object is useless.

Forge's answers:

* Everything inside the module is millimetres, always. Conversion happens once,
  at the reader, and the reader records what it assumed — that line is printed
  on the report, so a wrong guess is visible in one line rather than at the end
  of a four-hour print.
* The bounding box is reported in millimetres **and** inches, so a unit mix-up
  is obvious to a human without any cleverness.
* **3MF is the default output**, because it declares `unit="millimeter"` inside
  the package. It is the first point in the whole chain where the unit stops
  being a convention and becomes part of the data. STL is offered because every
  machine eats it, not because it is good.

## What it does not do

* **No STEP, no solid model.** Everything here is a triangle mesh. A
  half-implemented STEP writer that emits files CAD opens wrong is worse than
  none.
* **No slicing**, no time or material estimate. The volume is exact; what a
  machine does with it is the slicer's business.
* **Thickness is sampled perpendicular to each face.** That can over-report the
  true minimum and never under-report it, so a `fail` is trustworthy and an
  `ok` means "nothing thin was found". The report says `thinnest measured` for
  exactly that reason.
* **No draft angles, tool access or undercuts.** The checks describe a
  fused-filament machine, which is what the defaults describe.

## How it is verified

130 tests. Every guard in the module was confirmed by reverting it
individually and watching a test fail — and the first version of that audit was
itself wrong, passing a pytest flag that does not exist here, so every "caught"
was an argparse error rather than a test failure. The corrected run found one
real gap: the STL **writer**'s face normals were untested, because the test that
looked like it covered them was proving the reader ignores stored normals.

Reader and writer agreeing proves nothing on its own — two halves that share a
wrong idea of a format round-trip perfectly. So `tests/fixtures/forge/` holds a
`.glb` and a `.stl` written by `trimesh`, an unrelated library, with their
correct dimensions written down. trimesh is **not a dependency of Apex**; what
is committed is its output.
