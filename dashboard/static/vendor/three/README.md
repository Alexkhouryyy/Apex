# three.js 0.160.0, vendored

Copied unmodified from the `three@0.160.0` npm package (MIT — see `LICENSE`).

Only the three files `/board` actually loads are here, so the module graph is
closed: `three.module.min.js`, `GLTFLoader.js`, and the one file it imports,
`BufferGeometryUtils.js`, which imports only `three`.

Why vendored: `/board` used to import these from cdn.jsdelivr.net. With no
internet — or the CDN down, or a network that blocks it — the module failed to
resolve and **nothing on the page ran**: no cards, no hand cursors, no readout.
A self-hosted agent's own interface should not depend on a third party being
reachable. `tests/test_board_offline.py` fails if board.html ever imports from
the network again.

To upgrade: `npm install three@<version>`, copy the same three paths, update the
version in this file and in the import map comment in board.html, and run
`node scripts/check_board_readout_ui.cjs` plus the offline test.
