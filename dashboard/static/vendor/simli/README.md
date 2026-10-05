# Simli browser client, bundled

`simli.bundle.js` is `simli-client` 3.0.2 (MIT, Simli) with its dependencies,
chiefly `livekit-client` 2.22.3 (Apache-2.0), bundled into one browser file that
sets `window.ApexSimli = {SimliClient, LogLevel}`. Exact versions of every
dependency are in `package-lock.json`; licence texts are beside it, and each
package's own licence comments are kept at the end of the bundle.

Why bundled and vendored: the npm package is CommonJS for build tools, and the
companion must not load code from a CDN at runtime.

Built on 2026-10-05 with:

    npm install simli-client@3.0.2 esbuild@0.25.10
    # simli-client 3.0.2 requires "./Client" but ships "client.js": it only
    # resolves on case-insensitive file systems. For the build only:
    ln -s client.js node_modules/simli-client/dist/Client.js
    npx esbuild entry.js --bundle --minify --format=iife --target=es2020 --legal-comments=eof --outfile=simli.bundle.js

sha256 of the bundle: `781a72ab197c0175793cbc6685ee2c4ddbcb756408fb531eae0c6e8221ae442e`
