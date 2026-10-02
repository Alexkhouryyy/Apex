# Apex World View

From Command, click Earth or its World View portal; the header shortcut works from any planet. Earth expands into a full-screen panel. Dragging or multi-touch navigation does not enter it. Reduced motion skips the animation. Command/Escape returns focus and unloads the world renderer. The standalone page is `/world`.

## Live workflow

1. Choose a place, then enable **Flights near view**, **Space stations** or **Earthquakes**. Feeds are off until enabled. Each layer has source, coverage, fetch time, freshness and failure status.
2. Click an entity or find it in the searchable entity list. Inspect its ID, coordinates, altitude, event/position time and available measurements. Use Follow to follow it. Positions update from the source; Apex does not invent flight motion between observations. Flight positions older than two minutes cannot be followed.
3. Open Ask Celine. Questions use the ordinary Apex chat service with server-resolved entity context and enabled-layer summaries. Typed commands `show flights`, `hide satellites`, `follow selected`, `stop following` and `reset globe` execute directly and confirm results. Other questions use the agent; the context explicitly tells it not to claim unexecuted scene actions.
4. Microphone input is optional and uses the browser's speech-recognition service, which may be unavailable or use a browser vendor's remote processing. Typed questions remain available. Speak uses the existing local Voicebox/Celine service and configured voice profile; this page does not start the voice server or download voice models.

## Sources, bounds and failure behavior

- **OpenSky Network:** current state vectors from a fixed HTTPS endpoint for a quantized 5° × 5° region around the view, cached for one minute. Receiver coverage is incomplete. Anonymous requests have limited provider credits; Apex budgets 300 requests per UTC day per process, or 3,000 when OAuth credentials are configured. Longer sessions may require account access. Set `OPENSKY_CLIENT_ID` and `OPENSKY_CLIENT_SECRET` on the host for OAuth; tokens are exchanged/refreshed server-side and never returned to the page. Restarting Apex resets the local budget, not the provider quota.
- **USGS:** M2.5+ earthquake events in the past 24 hours, cached for one minute. Event time differs from feed-fetch time. Depth is reported in kilometres and is not used as a negative rendering height.
- **CelesTrak:** the space-stations GP group, fetched at most once every two hours after successful receipt, capped at 150 rendered objects. SGP4 calculates current positions from orbital elements. These are calculated positions, not observed telemetry. Orbital epoch is always shown; epochs older than three days are marked aged and cannot be followed, and elements older than seven days are omitted. The general active-satellite catalog and orbital trails are not included yet.
- Requests use fixed destinations, validate coordinates/data, cap decompressed responses at 2 MB, use finite timeouts and coalesce concurrent calls. No dashboard credential is forwarded. Provider retry windows are respected. An outage retains last-known records with a stale label; no response becomes an unavailable layer, never a claim that no aircraft/events exist. Disabling a layer removes its entities, cancels its request and timer, and retains saved project items. Backgrounding pauses refreshes; closing disposes rendering/audio/recognition and cancels requests.
- Geographic search uses Photon/OpenStreetMap, with existing authenticated query validation, caching and explicit result selection. Reference imagery is not live satellite video.

## Terrain and 3D

The flat ellipsoid is the default. Terrain is an explicit Re:Earth/Mapterhorn provider choice with credit; provider failures retain the flat globe. Optional OSM 3D buildings use Cesium ion asset 96188 and a scoped token entered for this view only; it is not saved. Ion permissions, quotas and geographic coverage apply. OSM buildings are not photorealistic Google 3D cities. No ion account is created automatically.

## Projects

Mark a location or select an entity, enter a name, and save a note. Draw route collects up to 100 clicked locations and saves a named line. Camera/map/location and up to 200 notes/routes persist in this browser. Export/import a bounded JSON project to move it elsewhere. Import validates schema and coordinates before changing the project; Undo restores earlier project changes. These are browser-local projects, not shared cloud scenes or authoritative survey data.

## Dependencies and attribution

CesiumJS 1.124.0 loads from jsDelivr on entry; its credit widget remains accessible. Install `sgp4>=2.24,<3` from requirements.txt for satellite propagation. No provider data is bundled. The original small imagery adapter retains the upstream God's Eye View MIT notice at `dashboard/static/world/UPSTREAM-LICENSE.txt`; new layer code is implemented for Apex from provider documentation. Cesium is Apache 2.0, SGP4 is MIT, and Re:Earth/Mapterhorn terrain is attributed CC BY 4.0. Imagery and feed terms apply independently of repository licensing.

Primary documentation: [OpenSky REST API](https://openskynetwork.github.io/opensky-api/rest.html), [CelesTrak GP formats](https://celestrak.org/NORAD/documentation/gp-data-formats.php), [USGS GeoJSON](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php).

The empty `/world` shell is public, while layer/search/chat/speech APIs use existing dashboard authentication. Token entry is only sent to Cesium ion for the explicitly selected 3D provider. No bulk tile downloader, arbitrary URL proxy, synthetic live feed or cloud voice fallback is added.

## Validation and remaining work

World backend tests cover auth, malformed records, caching/coalescing, outage/stale retention, retry windows, request budget, payload limits, OAuth isolation, SGP4 positions and server-resolved chat context. The three World View DOM suites cover navigation, race/failure behavior, entry/focus/renderer lifecycle, selection, aging/tracking, cancellation, project state and terrain races.

Real browser checks use Cesium and public flights/USGS feeds in an isolated loopback preview. Agent-context behavior is verified with a test agent; actual Celine audio, microphone recognition and account-backed ion buildings still need checks in running Apex. CelesTrak had TLS timeouts on this machine during this review; no fake satellite data was substituted. These checks do not certify all Lenovo hand gestures or continuous multi-hour provider availability.

Remaining broader parity work: additional weather/vessel/fire/camera/transit sources, the full satellite catalog and trails, photorealistic cities, assistant tool-driven world actions beyond the confirmed commands, shared scenes and hand navigation. The reusable layer interface is in place for those additions.
