# three.js 0.160.0, vendored

Copied unmodified from the `three@0.160.0` npm package (MIT — see `LICENSE`).

The local module graph includes the three files `/board` loads: `three.module.min.js`, `GLTFLoader.js`, and the one file it imports,
`BufferGeometryUtils.js`, which imports only `three`. The assembly study also
loads `examples/jsm/controls/OrbitControls.js`, copied unmodified from the same
0.160.0 package. It imports only `three`. The study's hologram view (bloom)
also loads, all copied unmodified from the same 0.160.0 package:
`examples/jsm/postprocessing/{EffectComposer,RenderPass,ShaderPass,MaskPass,Pass,UnrealBloomPass,OutputPass}.js`
and `examples/jsm/shaders/{CopyShader,LuminosityHighPassShader,OutputShader}.js`.
They import only `three` and each other.

Why vendored: `/board` used to import these from cdn.jsdelivr.net. With no
internet — or the CDN down, or a network that blocks it — the module failed to
resolve and **nothing on the page ran**: no cards, no hand cursors, no readout.
A self-hosted agent's own interface should not depend on a third party being
reachable. `tests/test_board_offline.py` fails if board.html ever imports from
the network again.

To upgrade: `npm install three@<version>`, copy these four paths, update the
version in this file and in the import map comment in board.html, and run
`node scripts/check_board_readout_ui.cjs` plus the offline test.
