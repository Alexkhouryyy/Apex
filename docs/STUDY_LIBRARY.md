# Study library

The study page (`/study`) is no longer motor-only. It opens any subject in
the library, and everything else works the same on each one: take apart,
select, isolate, hide, section, hands, Ask Céline, notes and saved projects.

| Subject | Parts | Motion | Geometry |
| --- | --- | --- | --- |
| Turbofan jet engine | 20 | Spin the spools (fan and core turn at different speeds) | Generated, illustrative |
| Human heart | 16 | — | Generated, illustrative |
| Car engine (inline-four, DOHC 16-valve) | 21 | — | Generated, illustrative |
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
  visible. The car engine shows one instant, with pistons 1 and 4 at the top.
  Its crankshaft doesn't turn, because turning it without moving the pistons
  would show something false.

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
- `motion` (optional): `label`, `axis`, `pivot`, and `parts` (component id to
  relative speed). With it the motion button works for that subject.

To add a generated subject, write a module in `scripts/study_models/` that
returns `Part`s, register it in `scripts/build_study_models.py`, and run:

    python scripts/build_study_models.py

Any geometry change must bump that subject's `geometry_revision`, because
saved studies pin the manifest's hash. `tests/test_study_library.py` checks
that the committed files are exactly what the script builds, that every
component maps to a named node, and that every source and label is present.

Real models (a GLB from Sketchfab, GrabCAD, NASA 3D and so on) will fit the
same manifest. An importer that reads a GLB's node names into a draft
manifest is the planned next step. Check each model's licence before
committing it.
