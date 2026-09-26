# OpenMotor reference asset

Source: [eMotres/OpenMotor-Hardware](https://github.com/eMotres/OpenMotor-Hardware/tree/1e1e56d7cf64ea393793ca5c06189251f87b6e98),
commit `1e1e56d7cf64ea393793ca5c06189251f87b6e98`, file
`CAD/CIAG_2_28 125_25.step`. Retrieved 2026-09-26.

The original compressed STEP (`source.step.gz`), derived viewer asset
(`dashboard/static/models/openmotor.glb.gz`) and derived CAD manifest
(`data/assemblies/openmotor-125.json`) are supplied under **CERN-OHL-W-2.0**,
with the upstream licence preserved in `LICENSE.txt`. Attribution: eMotres /
OpenMotor-Hardware contributors. These assets are separately licensed from
Apex's application code. No affiliation, endorsement or independent engineering
review is claimed. Source location and license are also exposed in the viewer.

Uncompressed STEP SHA-256:
`0f6737f1ddba820376e88298cf05725de36048f03c227714bf391e7cf21b07d3`.
The Git blob hash matches the pinned source tree:
`f54e2b73804a6ae0697a591c742ce414817b6df2`.

## Conversion and evidence

Conversion by the Apex project on 2026-09-26: STEP import, tessellation and glTF
export with cadquery-ocp 8.0.1.0.0 (OCCT bindings), linear deflection 0.25 in
source millimetres, angular deflection 0.35 radians. Original STEP is unchanged.
Gzip transport preserves the exported bytes. The glTF uses metre coordinates;
the viewer normalizes scale for navigation, with approximate millimetre bounds
reported separately. Interactive moves are view edits, not dimensioned CAD edits.

The importer found 616 solid occurrences; all passed BRepCheck_Analyzer. The
export contains 151 scene nodes and 135 mesh occurrences. A geometry-free
`Insulation` node was skipped by the exporter. Several meshes contain multiple
solids. Part names and hierarchy come from the source; no inferred mechanical
connections or material properties have been added. Displayed sizes use
transformed mesh bounding boxes, not toleranced drawings.

Rebuild with `python scripts/build_reference_motor.py` after installing the
optional developer packages `cadquery-ocp==8.0.1.0.0` and `numpy`. These packages
are not needed by the Apex runtime. The script checks the source hash and solid
validity, then rebuilds the derived glTF and manifest. A source/geometry change
requires a new manifest geometry revision and review of the saved-project
compatibility policy.

Passing a solid-validity check only establishes kernel-level geometric
consistency. Performance, materials, winding correctness, fits, tolerances,
manufacturability and physical dimensions have not been independently verified
by Apex. The source assembly must not inherit the educational brushed motor's
commutation explanation: they are separate models.
