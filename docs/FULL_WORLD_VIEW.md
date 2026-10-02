# Complete World View engine in Apex

Apex now hosts the full God's Eye View application at `/world/engine/`, from the pinned upstream revision `e7707d9a0f34d9fbffc300023c319f95caa5be30`. Selecting Earth, clicking the Earth globe, and the Command shortcut enter the full view through `/world`. The Command renderer pauses during entry; returning restores it without a separate dashboard navigation. Nested frames pass microphone, autoplay, fullscreen and USB capabilities through; the browser still asks for device permission. The original basic globe and its saved projects remain at `/world/basic`.

This is the original runtime, not a feature-by-feature approximation. All runtime code, data, models, provider adapters, settings, scene tools, tests, and license notices are retained under `integrations/gods-eye-view`. Only documentation demonstration media are excluded. `APEX_INVENTORY.json` records the Git blob hash of every included upstream file; Apex modifications are separate `apex-*` adapters.

## Included experience

The original catalog contains 26 visible layers: satellites; flights; military flights; local ADS-B/SDR; vessels; traffic; transit; bikeshare; cameras; recent imagery; mapped ALPR cameras; mapped installations; datacenters; submarine cables; dams; launches; earthquakes; fires; fire perimeters; wind; radar; satellite clouds; lightning; cyclones; directions; and radio. The source includes sensor styles, detection and HUD controls, cockpit and nearby contacts, aircraft models, satellite passes/trails, weather history, camera projections/viewsheds, annotation/drawing, navigation, route flight, scene authoring/import/export and share links. Availability and observation/simulation labels follow upstream.

The original realtime voice experience remains available, alongside Apex's Ask Celine panel. Celine uses the active Apex model and all 30 original schema-defined world actions. Tool results return to the model before it can confirm completion; tools cannot execute computer commands or change credentials. Optional spoken replies use the existing local Voicebox/Celine service. Browser microphone permission and speech recognition support are still needed. Celine stops the original realtime session before taking control to avoid competing scene actions.

## Setup and lifecycle

Run `Setup-Apex-World.cmd` once on a fresh checkout. It verifies the pinned inventory, checks Node 24.14+ (24.x) or 26.x, installs the locked npm dependencies and builds the production assets. The engine starts on demand when World View opens, behind Apex's authenticated gateway on an ephemeral loopback port. The private backend rejects direct requests without its internal credential. Apex owns the process; a parent watchdog stops an orphan, and failed startups are bounded. Closing World View removes the renderer and voice UI; the cached backend stays available until Apex exits.

The production backend installs the original provider middleware and local key settings handlers. It avoids the development dependency scanner. Changing provider settings rebuilds browser-exposed map configuration and refreshes the view. No credentials are committed. The engine's ignored `.env` is separate from Apex's `.env`; inherited provider credentials remain externally managed. Logs are in `.mcp-runtime/world-engine.log`.

## Access boundaries

The public entry shell issues a random, HttpOnly, same-site, scoped eight-hour engine session after ordinary Apex authentication. Revoked device tokens or a changed owner token invalidate that session. Apex credentials and cookies are never forwarded to the Node backend. Writes require the Apex origin (or an explicit bearer credential for local API clients). Provider key edits require the local owner; paired devices cannot edit keys. Requests cannot access environment files, Git metadata, or Vite filesystem escape routes. Shared URLs show the sign-in shell when required, preserving view query/hash state.

## What inclusion does not prove

Ask Celine uses Apex's configured API model provider, including local Ollama models. A Claude subscription login alone does not supply this isolated tool-calling API. The provider must be available separately; failures show an explicit error. Real audio still needs the existing Celine/Voicebox server.

Credentials and accounts are still required for photorealistic map providers, AIS vessels, FIRMS fire feeds, TomTom live flow, and original realtime voice. Public feeds can time out or be rate limited. Local ADS-B needs compatible physical hardware and browser permission. The original traffic simulation, sensor effects, camera pose estimates, reconstructed launch trajectories, and modelled satellite positions retain their upstream labels; they are not new observations.

Real Celine audio/microphone, photorealistic cities, all credential-backed feeds, SDR hardware, prolonged sessions and every imported scene are not certified by unit tests. Existing basic-globe notes/routes are preserved, but are not automatically migrated into the different upstream annotation format. Upstream updates are reviewed and pinned rather than silently downloaded at runtime.

## Licenses

The source is MIT with Bilawal Sidhu's original notice. Third-party data/models keep their individual licenses and attributions. TeleGeography cable data and the Bhote Koshi imagery/derived data include noncommercial restrictions; their presence does not turn them into MIT data. Read the included `LICENSE`, `DATA_SOURCES.md`, `THIRD_PARTY_NOTICES.md`, and model/event notices before distributing a commercial build.

## Setup and updates in Apex

Open `/setup` from Home, System & updates, or the World View toolbar. The owner-only setup screen reuses the original provider registry and credential store, connects the existing Composio catalog, and links to account authorizations in Apps. Inherited provider keys remain managed by their original store. Blank fields keep saved keys; values are cleared after submission and never echoed back. Browser map keys still need provider origin restrictions.

Celine diagnostics contact the local health and profile endpoints only. A ready server is not a verified speaker: Test Celine generates a short local sample for you to play. The model row reports configured credentials rather than claiming a model call succeeded. Saved provider keys are not live-feed certification. Hardware permission and compatible ADS-B/SDR equipment remain required.

Check God’s Eye update contacts the fixed upstream GitHub repository and compares its main revision with the installed pin. Checks are cached for 15 minutes; a failed check reports unavailable, not current. The checker does not fetch or execute new code. Apex releases are pulled through the existing fast-forward update control and need a restart; a new upstream pin also needs locked dependency setup and a production build. Neither Apex nor God’s Eye installs updates automatically. The offline worker never caches engine responses or its private APIs.
