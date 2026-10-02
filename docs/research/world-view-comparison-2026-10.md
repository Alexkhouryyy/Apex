# Apex World View compared with God's Eye View

Reviewed 2026-10-02. Apex baseline: `422865a0ad5cd84b2169d293b392933af2ff8560`. God's Eye View baseline: `e7707d9a0f34d9fbffc300023c319f95caa5be30`. This is a source comparison, not a benchmark of every upstream service or a claim that credentials are configured.

## Verdict

Apex does not yet provide the same experience. Its World View is a globe/navigation foundation with a small attributed imagery adapter. That adapter does not bring across the upstream entity layers, scene context, voice actions or terrain system.

| Capability | Apex at reviewed baseline | Reference implementation | Priority |
|---|---|---|---|
| Globe, basemap, place search | Present; ellipsoid globe, reference satellite imagery, OSM, coordinate/place search | Present with broader scene controls | Keep |
| Aircraft, satellites, earthquakes | No live entity layers | Separate ingestion, propagation and rendering modules | Critical |
| Other feeds | No vessels, weather, fires, camera, radio or transit layers | Broad registry of sources and layers; availability differs by provider | Select useful feeds after core layers |
| Freshness and provenance | No live entity state to label | Ingestion and source modules distinguish timestamps, refreshes and availability | Critical alongside feeds |
| Entity selection and tracking | Geographic point marker only | Tracked entity readouts and layer summaries | Critical |
| Assistant scene context | No World View entity context or structured Celine actions | Context store and voice action schemas | Critical for Apex |
| Terrain / 3D cities | Ellipsoid only | Terrain and optional 3D map integrations | Important; requires provider setup |
| Routes, annotations, scene persistence | Camera and selection saved locally; no reusable world project | Broader scene tooling | Next |
| Sensor effects / cockpit / director | Absent | Broader visualization and presentation modes | Lower priority than reliable data |

## What to build first

Ship one complete workflow: **Earth → Flights → select a plane → ask Celine about that selection → follow it → return to Command.** Add satellite propagation and earthquakes through the same layer interface. Every layer needs source attribution, observation or calculation time, freshness, coverage, explicit unavailable/stale states, cancellation and retry.

Reference satellite imagery is not live satellite video. Calculated satellite positions are not direct live observations. A thermal shader is not a thermal measurement. Forecast weather differs from observations. Simulated traffic must be labeled separately from observed positions. Repository licensing does not replace imagery/data-provider terms.

## Entry flow audit and implemented change

1. **Find World View — improved.** The old entry sat far below the Earth. The patch places a labeled portal at the Earth, makes a deliberate Earth click open it, and adds a header shortcut available from any selected planet.
2. **Enter World View — checked.** The panel expands from the Earth position, retains native link fallback and keyboard activation, and honors reduced motion. Dragging, multi-touch, right-click and clicking another planet do not accidentally open it. World resources load only when opened; the Command globe pauses while the panel is visible.
3. **Return to Command — checked.** Close/escape unloads the iframe and restores focus to the initiating control. Reopening retains the existing locally saved camera/selection.
4. **Use a small screen — checked.** The mobile portal measured approximately 166 × 51 CSS pixels at a 390 px viewport, without page overflow. Header actions wrap. This is interaction verification, not a full accessibility certification.

## Validation and delivery

The existing World View DOM checks and new Command-entry DOM checks passed. Five World View backend tests passed with one existing Starlette/AnyIO deprecation warning. Browser checks used a local isolated preview of the reviewed source, including the real Cesium globe, Byblos search, keyboard entry, return focus, saved camera restoration and Mars exclusion. The preview uses fixture APIs rather than the user's private Apex database or account configuration.

The original entry patch was expanded in the follow-up below. The table above records the reviewed upstream baseline before these additions. The final patch baseline is `922baf7`, preserving the incoming voice-recording update. Windows denies Git metadata writes in that checkout. The checked updater checks the reviewed `922baf7` baseline, applies this patch, preserves the six existing provenance-audit files, reruns focused checks, and prepares the existing merge helper's manifest. It does not publish a commit automatically.

## Primary sources

- [Apex baseline World View documentation](https://github.com/Alexkhouryyy/Apex/blob/422865a0ad5cd84b2169d293b392933af2ff8560/docs/WORLD_VIEW.md)
- [God's Eye View README](https://github.com/bilawalsidhu/gods-eye-view/blob/e7707d9a0f34d9fbffc300023c319f95caa5be30/README.md)
- [Data source registry](https://github.com/bilawalsidhu/gods-eye-view/blob/e7707d9a0f34d9fbffc300023c319f95caa5be30/DATA_SOURCES.md)
- [Flight ingestion](https://github.com/bilawalsidhu/gods-eye-view/blob/e7707d9a0f34d9fbffc300023c319f95caa5be30/src/layers/flights/ingestion.js)
- [Earthquake source](https://github.com/bilawalsidhu/gods-eye-view/blob/e7707d9a0f34d9fbffc300023c319f95caa5be30/src/layers/earthquakes/source.js)
- [Scene context store](https://github.com/bilawalsidhu/gods-eye-view/blob/e7707d9a0f34d9fbffc300023c319f95caa5be30/src/data/contextStore.js)
- [Voice action schemas](https://github.com/bilawalsidhu/gods-eye-view/blob/e7707d9a0f34d9fbffc300023c319f95caa5be30/src/voice/actionSchemas.js)
- [Terrain module](https://github.com/bilawalsidhu/gods-eye-view/blob/e7707d9a0f34d9fbffc300023c319f95caa5be30/src/maps/terrain.js)

## Follow-up: core gap implementation

The expanded bundle now includes live regional flights, USGS earthquakes, calculated CelesTrak station positions, source/freshness/coverage metadata, entity selection/following, server-resolved Celine chat context, explicit confirmed world commands, optional terrain and OSM buildings, and saved/importable/exportable notes/routes. Public smoke requests returned 26 aircraft and 43 earthquakes; the browser later showed 27 aircraft and 43 earthquakes. CelesTrak TLS timed out, so that layer is truthfully unavailable in this environment; SGP4 behavior is verified with test orbital elements.

The 98 existing answer regressions passed, and focused World View and DOM checks cover the new boundaries and interactions. Real Celine speech/microphone and ion buildings require the user's running services/account. Broader provider catalog, photorealistic tiles, full satellite catalogs/trails, shared scenes and hand navigation remain outside this core implementation. It is not a claim of full God's Eye View parity.

Installation remains the checked owner-run updater because Git metadata writes are blocked here. It also installs the standard SGP4 dependency and prepares the existing merge helper for publishing separately. No main commit or push has been made by this implementation.

The final expanded patch was rebased onto `922baf7d4b169d8b5bf421120246111b8b5bf2b8` after main advanced during implementation; the new voices routes and voice library are preserved.
