# Study library

The study page (`/study`) is no longer motor-only. It opens any subject in
the library, and everything else works the same on each one: take apart,
select, isolate, hide, section, hands, Ask Céline, notes and saved projects.

| Subject | Parts | Motion | Geometry |
| --- | --- | --- | --- |
| Turbofan jet engine | 20 | Spin the spools (fan and core turn at different speeds) | Generated, illustrative |
| Human heart | 16 | Beat (atria, then ventricles; valves light when open) | Generated, illustrative |
| Car engine (inline-four, DOHC 16-valve) | 32 | Run the engine (crank, rods, pistons, cams, valves; firing order 1-3-4-2) | Generated, illustrative |
| Brushed DC motor | 14 | Rotor motion | Built in the page, illustrative |
| OpenMotor 125/25 | 135 | — | Real CAD (CERN-OHL-W-2.0) |

Choose a subject from **Study library** at the top of the component list,
open `/study?model=<id>`, or ask Céline ("open the heart", "show me how a jet
engine works"). The companion's `assembly_study` tool lists whatever is in the
library, and is told to say so rather than substitute when nothing matches.

## What the three new subjects are, honestly

- **Original models generated from code** by `scripts/build_study_models.py`
  (numpy only, deterministic). No meshes were downloaded, so there are no
  third-party licence terms on them.
- **Illustrative, not to scale.** They're detailed enough to learn from:
  - the jet engine has 22 twisted fan blades and 9 compressor stages, with a
    combustor, fuel nozzles and turbine stages;
  - the heart has all four valves with chordae, and coronary arteries that
    follow its surface;
  - the car engine has a counterweighted crankshaft, 16 valves with springs
    and phased cam lobes.

  They are not CAD, not medical imaging, and not simulations.
- **The explanations are AI-drafted from the listed general references and
  not yet reviewed.** Every subject says this in its caption, its
  limitations and its *Engineering readiness* panel. Céline is told to say so
  when relying on them, and not to diagnose for the heart.
- **Casings are cut away** (jet engine, car engine block) so the inside is
  visible. At rest the car engine shows one instant, with pistons 1 and 4 at
  the top.

## Motion

Press the motion button (or ask Céline to run it). Motion plays on the
assembled subject; taking it apart, isolating or moving a part stops it.
Pausing keeps the frame you stopped on, so you can study one instant; it comes
back when you reassemble, and a saved project keeps it.

- **Car engine: Run the engine.** Two crank turns take four seconds, slowed
  right down. The crankshaft and flywheel turn. Each piston follows the exact
  slider-crank position for its crank angle, and each connecting rod swings to
  stay on both its crankpin and its wrist pin. The camshafts turn at half
  speed, and each valve pair is pushed down by its lobe the way a flat bucket
  tappet really is: the lobe's lowest point, not its radius. Each valve is open
  for about 258° of crank, peaks 115° from top dead centre, and overlaps
  slightly with the other valve around TDC, like a real engine. Every piston
  glows orange as its power stroke begins, in firing order 1-3-4-2. The caption
  says which cylinder is on which stroke. The block, head, valve cover, gasket,
  intake manifold and oil pan turn see-through while it runs, so the
  internals show, and you can click straight through them.
  - *What doesn't move:* the timing belt and its sprockets, the accessory
    drive, and the valve springs. The springs ride with the valves instead of
    compressing. The glow marks the power stroke; it doesn't simulate
    combustion.
- **Heart: Beat.** One beat takes 1.6 seconds, slowed down. The atria squeeze
  first, then the ventricles. The coronary arteries and septum move with the
  ventricles. Each valve lights green while it is open: the tricuspid and
  mitral valves while the ventricles fill, and the aortic and pulmonary valves
  while they eject. Both sets are never open at once. The conduction system
  flashes as the beat starts and again as the impulse reaches the ventricles,
  and the caption names each phase, including the two moments when all four
  valves are shut ("lub", "dub").
  - The valves are inside the chambers: turn on **Section** to watch them.
  - The chambers shrink uniformly toward the valve plane. That's a teaching
    simplification, not real wall motion.
- **Jet engine: Spin the spools**, unchanged.

The timing is checked by `tests/test_study_motion.py` and
`scripts/check_study_motion.mjs`:

- piston travel is one stroke;
- rods stay on their crankpins and wrist pins;
- the cam lobes touch the tappets all the way round;
- the valves open on the right strokes;
- the power strokes fire in order 1-3-4-2;
- the heart valves never short-circuit;
- the page's own `pose()`, run with real Three.js, keeps each rod on its
  piston.

## Adding a subject

A subject is two files:

- `data/assemblies/<id>.json`: the manifest. The id must be lowercase letters,
  digits and hyphens, and must match the file name.
- `dashboard/static/models/<id>.glb.gz`: gzipped glTF binary geometry, with one
  node per component.

Manifest fields the page uses:

- `title`, `subtitle`, `category`, `order` and `summary`: shown in the
  library picker.
- `fidelity`, `caption`, `limitations`, `validation` and `sources`: stated
  honestly on the page.
- `asset` (`/api/study/model/<id>/asset`) and `asset_file`: the geometry file.
- `parts[]`: `id`, `node` (glTF node index), `name`, `group`, `purpose`,
  `connection`, `model_note`, `source` (a `sources[].id`), and optionally
  `explode` (the direction it moves when taken apart, in model units).
- `motion` (optional). With it, the motion button works for that subject. It
  comes in one of two forms:
  - **Spin:** `label`, `axis`, `pivot`, and `parts` (component id to relative
    speed).
  - **Tracks:** `label`, `cycle_seconds`, `samples` (N), optionally `hint` (a
    status line shown on start) and `ghost` (ids that turn see-through while it
    plays), `phases` (`from`, `to`, `text`: the caption over the cycle), and
    `tracks`. `tracks` maps a component id to `pivot`, plus any of:
    - `axis` and `angle[N+1]`: a turn in radians about the axis through the
      pivot;
    - `scale[N+1]`: a uniform scale about the pivot;
    - `offset[N+1][3]`;
    - `glow[N+1]` (0–1) with `glow_color`.

    Samples cover the cycle from u = 0 to 1, both ends included, in model
    units. The page interpolates between them (`dashboard/static/study-motion.js`).
    A generator module returns this, before orientation, as `build()`'s second
    value; `build_study_models.py` orients it.

To add a generated subject, write a module in `scripts/study_models/` that
returns `Part`s, register it in `scripts/build_study_models.py`, and run:

    python scripts/build_study_models.py

Any geometry change must bump that subject's `geometry_revision`, because
saved studies pin the manifest's hash. `tests/test_study_library.py` checks
that the committed files are exactly what the script builds, that every
component maps to a named node, and that every source and label is present.

## Importing your own models

On the study page, choose **＋ Import a 3D model** (under the library picker).

**File.** One self-contained glTF 2.0 file, up to 150 MB: a `.glb`, or a
`.gltf` with everything embedded. Most model sites offer a glTF download.
Sketchfab, for example, offers "glTF", which gives a `.glb` or a zip holding a
`.gltf`. For STL, OBJ, FBX or STEP, convert first, for example in Blender with
File → Export → glTF 2.0.

**Refused, with a reason and a fix:**
- Draco or meshopt compression, which the viewer can't decode yet. Re-export
  without compression, or run `npx @gltf-transform/cli copy in.glb out.glb`.
- Files that link to separate texture or data files. Export a single `.glb`.
- Old glTF 1.0 files.
- Files with no geometry.

**Parts come from the file's own node tree.** Wrapper nodes that exporters
add ("Sketchfab_model", "root", "GLTF_SceneRootNode") are skipped. Named
objects become the parts, and a generic name like "Object_4" takes its named
parent's name. **Split into** has two settings:
- **Main assemblies** stops at the first level with enough named pieces.
  The real OpenMotor CAD gives 19 parts: Stator, Rotor, Magnet asm, Winding,
  8 radiators…
- **Every piece** goes down to each mesh and groups repeats. The same file
  gives 26 parts, including "Magnet ×28", "Tooth ×24" and "Coil ×24".

A part list is only as good as the names the author gave. A file that is one
single mesh stays one piece and can't be taken apart; the page says so.

**Taking it apart.** Imported files have no take-apart directions, so Apex
finds the axis the parts are stacked along and spreads them in order along
it. For fully concentric parts it uses the model's longest side, and it also
pushes off-centre parts outward. This suits motors, gearboxes, wheels and
most mechanisms. It is automatic, not a hand-made exploded view.

**Notes.** An import starts with no explanations. **Draft notes with AI**
sends the part names and groups, with no geometry, to your background model:
- It asks for what each part is and how it connects, to flag guesses, never
  to invent figures, and never to give medical advice.
- The notes are labelled AI-drafted and not reviewed everywhere they appear.
- They are stored beside the model (`<id>.notes.json`), so drafting them never
  changes the manifest, and saved studies of the model keep opening.
- **Redraft notes** replaces them.

**Where imports live.** In `~/.apex/study` (on Windows
`C:\Users\<you>\.apex\study`), or `APEX_STUDY_DIR`. Never in the repository.
**Remove** deletes the file, manifest and notes; saved studies of it can then
no longer be opened.

**Céline** can open an import by name straight away; her study tool refreshes
when the library changes.

**Licences.** Only import what you're allowed to use. The dialog records where
a model came from and its licence, and both show under *About this
illustration*.

**Checks:**
- `tests/test_study_import.py`: file safety, part detection (including the
  real OpenMotor CAD), storage, notes and removal, plus the API.
- `scripts/check_study_import.mjs`: the take-apart layout.
- `scripts/check_study_import_browser.cjs` (optional, real browser): imports
  the OpenMotor CAD through the dialog, takes it apart, drafts notes with a
  stand-in and removes it.
