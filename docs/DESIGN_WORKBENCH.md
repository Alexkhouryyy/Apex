# Measured design on the Windows workspace

Open `/board` and choose **Design**, or open `/board?design=1`. It uses the
existing board, model files, component selection, voice partner, gesture
controls and Undo history. Opening the panel starts neither a camera nor a
microphone. Numeric creation and editing need no AI account or new model.

## A complete first workflow

1. Choose **Measured build**, give it a unique name, and enter physical
   dimensions in millimetres. Start from a primitive or the round-enclosure
   example. The example has separate Base, Wall and Lid components, with the
   lid beside the base; it is not a validated mating or electronics design.
2. Choose **Frame object** for a larger view. Turning or resizing the view
   does not change physical dimensions. Select a component in the list or
   on the model. **Edit parts with hands** uses the existing configured hand
   tracker; mouse and numeric controls remain available without a camera.
3. Change the component's dimensions, position or rotation. X/Y/Z are the
   board's width/height/depth axes. **Save new version** writes a new GLB and
   keeps the old source file. Undo and Redo restore the actual geometry and
   placement. Unsaved fields remain in this tab when switching components.
4. **Ask Céline** opens the existing board conversation. Its selected-object
   context carries the component you chose. For example: “Make this lid
   42 millimetres across, keep it 2 millimetres thick, and explain the change.”
   Voice, recognition and visual reasoning depend on your configured
   microphone/model/voice services; their availability is not implied by the
   numeric controls working.
5. Choose a check scope, then **Run print checks** or **Check & export file**.
   The existing Forge validator applies your configured printer dimensions,
   nozzle, thickness and overhang settings. Failed or unknown checks create
   no file. A successful export offers an authenticated download of a real
   3MF or STL, linked to its exact source version and selected component.

The selected export scope is placed on a print bed, preserving size and
orientation while converting glTF's Y-up/metre coordinates to Z-up/millimetres.
3MF records its unit; STL does not. Exporting an assembly does not fuse parts
or establish that they mate, have clearance, or form a useful mechanism.
Check and export individual components when preparing separate parts.

## Reference pictures

Expand **Visual reference** to upload a PNG, JPEG or WebP, or take the current
frame from your already configured hand camera. Uploads are reduced to a
bounded JPEG in this tab. No new camera permission is requested by this panel.
If a camera frame is unavailable, use a photo.

Choose **Use in my next question** to stage the reference in the partner.
This neither sends an AI request nor turns on voice. Your next explicit
question includes it. Completed questions consume their own reference;
failed requests retain it. Remove, replace, or switch workspaces to clear a
staged picture. The image does not provide physical dimensions or a 3D scan.
No automatic CNC job, toolpath, machining duration or machine action is claimed.

## Conflicts and limits

If voice, hands, Undo, another window or a workspace switch changes the source
while an inspector draft is open, that draft stays visible. Apply is disabled
until you explicitly reload the saved version. Measurements cannot overwrite
an active gesture. Late inspection and check responses cannot attach to a
different object or workspace.

Only Apex builds with an editable recipe support precise component changes.
Imported GLB/glTF objects can be explored through Study; this panel does not
invent an editable CAD history for them. These models are triangle meshes,
not parametric boundary-representation solids. The current checks describe
sampled print geometry, not dimensional inspection of a real part, mechanical
fit, electrical correctness, a CNC setup, or safety certification.

## Verification

`tests/test_design_workbench.py` checks real file versions, millimetre/axis
conversion, unchanged siblings, Undo/Redo, selected-source lineage, stale
requests, owner/origin restrictions, gesture conflicts, and refusal to export
unknown results. `scripts/check_design_workbench_ui.cjs` checks draft and
reference behavior with the actual inspector module. The extended Companion
harness checks explicit image staging and consumption.

`scripts/check_design_workbench_browser.cjs` starts a disposable real backend
and uses Chromium to create, edit, export/download, Undo, recover a stale
window, and stage/remove a reference. It records desktop and narrow-layout
screenshots, without calling a model or starting a real microphone/camera.
Set `APEX_DESIGN_ARTIFACTS` to retain files, `APEX_TEST_PYTHON` to select an
existing Python environment, and `APEX_BROWSER_EXECUTABLE` to reuse a browser.
The normal CI IDE jobs run this check on Windows and Linux.

Real spoken changes, recognition quality, gesture accuracy on your webcam,
and the fit of an actual fabricated part remain laptop/hardware acceptance.
