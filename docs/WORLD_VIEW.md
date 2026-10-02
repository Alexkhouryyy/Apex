# Apex World View

This guide describes the Basic globe at `/world/basic`. The main `/world` entry now opens the full pinned God's Eye View engine. See [Full World View](FULL_WORLD_VIEW.md) for its 26 layers, 30 scene actions, setup and verification limits.

From Command, click Earth or its World View portal; the header shortcut works from any planet. Earth expands into a full-screen panel. Dragging or multi-touch navigation does not enter it. Reduced motion skips the animation. Command/Escape returns focus and unloads the world renderer. The standalone page is `/world`.

## Live workflow

1. Choose a place, then enable **Flights near view**, **Space stations** or **Earthquakes**. Feeds are off until enabled. Each layer has source, coverage, fetch time, freshness and failure status.
2. Click an entity or find it in the searchable entity list. Inspect its ID, coordinates, altitude, event/position time and available measurements. Use Follow to follow it. Positions update from the source; Apex does not invent flight motion between observations. Flight positions older than two minutes cannot be followed.
3. Open Ask Celine. Questions use the ordinary Apex chat service with server-resolved entity context and enabled-layer summaries. Typed commands `show flights`, `hide satellites`, `follow selected`, `stop following` and `reset globe` execute directly and confirm results. Other questions use the agent; the context explicitly tells it not to claim unexecuted scene actions.
4. Microphone input is optional and uses the browser's speech-recognition service, which may be unavailable or use a browser vendor's remote processing. Typed questions remain available. Speak uses the existing local Voicebox/Celine service and configured voice profile; this page does not start the voice server or download voice models.

Voice and hand control are later stages. This is a globe and reference imagery,
not photorealistic 3D buildings or live satellite video.

## Milestone 2a: USGS earthquakes

Open **Live layers** and enable **Earthquakes · M2.5+ · past day**. The layer
starts off. Its enabled preference is remembered in this browser; no feed is
requested until it is enabled. It uses the official USGS summary feed:
<https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson>.

- Markers show surface epicenters, with size following magnitude. Depth is
  reported in the selected event's details, not used to place symbols underground.
- Select a marker without moving the camera, or choose one of the eight latest
  events to fly to it. The detail card shows magnitude, depth, occurrence and
  revision times in UTC, review status, and a link to the official USGS event.
- Feed generation and Apex fetch times are distinct. A snapshot older than five
  minutes, or a failed refresh, is visibly stale. On failure the last successful
  snapshot keeps its original timestamps. With no snapshot, the UI reports that
  the feed is unavailable; an empty successful feed reports zero events.
- Refreshes run once per minute while enabled and visible. Hidden pages pause
  requests; switching off aborts pending work and removes markers. Closing World
  View disposes the layer. Late responses cannot turn it back on.
- The current browser retains a selected event snapshot when the layer is off
  or the event leaves the feed, with that state labeled. Choosing another place
  or clearing the selection removes the event details. Reopening restores the
  generic selected location, not a saved copy of the feed's event metadata.

All layers start off by default. Weather, vessel and camera feeds are not implemented.

## Milestone 2b: regional aircraft

Enable **Aircraft · regional** in **Live layers**. It starts off and remembers its
switch and area in this browser. The initial area is Byblos. Select another place,
or clear the selection and navigate the globe, then choose **Load this area**.
Navigation alone leaves the loaded region fixed. The provider receives only the
rounded area center; Apex does not forward dashboard credentials.

- ADSB.lol supplies reported positions within 250 nautical miles (463 km).
  Coverage depends on receiver reception and can be incomplete. Zero reported
  aircraft means an empty feed, not proof of an empty sky.
- Arrows follow ground track; a round symbol means track is unknown. Symbols use
  reported geometric altitude when present, otherwise barometric altitude. Both
  are estimates; rendering uses a minimum 100 m height for legibility.
- Choose one of the eight latest aircraft to fly to it, or click a marker to
  inspect it without moving the camera. Details include ICAO, callsign in the
  heading, registration, type, altitude in feet with its basis, speed in knots,
  ground track and the UTC time of the last position report. Unknown fields stay
  unknown. No origins/destinations or historical flight routes are inferred.
- Refreshes run every 30 seconds while visible. Individual positions older than
  30 seconds turn amber and dim; whole snapshots older than 90 seconds or with a
  failed refresh are stale. A failure retains original positions and timestamps.
  Movement is not predicted or animated between samples.
- Source, fetch and individual position times remain distinct. Turning off
  aircraft removes their markers while preserving earthquake markers. Switching
  the loaded area clears its old aircraft before requesting the new area.
- ADSB.lol attribution and its ODbL data-license link are visible in the controls.
  See <https://www.adsb.lol/docs/open-data/api/> and the provider's route/schema
  source at <https://github.com/adsblol/api>. Field units follow the provider's
  readsb format: <https://github.com/wiedehopf/readsb/blob/dev/README-json.md>.

The authenticated `/api/world/layers/flights?lat=…&lng=…` endpoint calls only the
fixed ADSB.lol radius route. It validates finite coordinates, timestamps, IDs,
regional distance and fields, streams at most 2 MiB, processes up to 3,000 records
and returns up to 500 aircraft. A shared lock, 30-second region cache, 16-region
limit and five-second spacing for uncached areas bound requests. Provider 429
responses apply a shared cooldown, respecting numeric Retry-After up to 24 hours.
There is no persistent aircraft database or history export.

## Milestone 2c: station orbit estimates

Enable **Satellites · space stations** in **Live layers**. It starts off. It loads
CelesTrak's `stations` group in OMM JSON format and uses pinned satellite.js 6.0.2
for SGP4 propagation. Positions are **calculated estimates**, not observations.
The library loads locally only when enabled. Names are from the source group,
which can include docked craft, cargo vehicles and other associated objects.

- Positions update once a second while visible. Select a point to inspect it
  without moving the camera, or choose a station from the list to fly to it.
- Only the selected object's next orbit is drawn. The 121-point path is predicted
  from the same elements and recalculated each minute. No historical track or
  satellite video is presented.
- Details distinguish element epoch, element age, Apex fetch time and calculation
  time. Altitude uses kilometers above the reference ellipsoid; speed uses inertial
  kilometers per second. The period is derived from mean motion.
- Elements older than three days receive an age warning. Propagation beyond 14
  days from epoch, a decayed solution or invalid output removes its position/path.
  Failed fetches preserve the original elements and fetch timestamp and show stale
  status. The age threshold is an app warning, not an accuracy guarantee.
- Disabling satellites removes their markers and orbit without affecting aircraft
  or earthquakes. Hiding/closing the view stops propagation, source polling and
  in-flight requests. Camera position is not moved by station updates.
- CelesTrak attribution and per-object element links are visible. OMM JSON supports
  up to nine-digit catalog IDs; no legacy five-digit TLE conversion is used.

The authenticated `/api/world/layers/satellites` route uses one fixed HTTPS source,
no redirects or forwarded credentials, a 512 KiB stream bound, at most 256 parsed
records and 64 returned objects. Validated elements and a two-hour source cooldown
are saved beside the memory database as `*.world-stations.json`. Set
`APEX_WORLD_STATIONS_CACHE` to override this public-data cache location. An Apex
restart reuses it; a failed storage write prevents a source request. The dashboard
server's single process shares a lock across windows. Deployments with separate
hosts/processes need a shared source broker before adding more workers.

CelesTrak's usage policy requires reusing data and stopping queries on errors:
<https://celestrak.org/usage-policy.php>. Every source error pauses automatic
upstream requests. **Retry source** explicitly resumes via an authenticated POST,
while preserving the original two-hour cooldown. Corrupt cache files require
storage repair rather than silently re-downloading.

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

`/api/world/layers/earthquakes` also requires dashboard authentication. It streams
one fixed HTTPS source with a 10-second timeout, no redirects and no forwarded
credentials. Responses are capped at 2 MiB, processing at 1,500 records, and
rendering at the 300 latest validated events. IDs, geometry, timestamps, magnitude
and depth are validated; event links are built from the validated ID. A shared
lock and a 60-second cache/retry interval bound requests across multiple windows.
USGS generated times are preserved even when the service returns older data.

## Terrain, buildings, saved places and Ask Celine

The panel on the right sits on top of the three layers above. It doesn't fetch
any layer data itself.

- **Terrain**: Re:Earth / Mapterhorn (CC BY 4.0). If you switch it off before
  it finishes loading, the late result is ignored. With terrain on, clicking
  marks the ground where it really is.
- **OSM 3D buildings**: these need your own scoped Cesium ion token. It's typed
  in each time and never saved, and without it the switch stays off.
- **World project**: save a note at the selected place, or draw a route by
  clicking places on the globe.
  - Up to 200 items, with undo and export/import as JSON.
  - Stored in this browser only.
  - Saved notes are separate from the location pin, so moving the pin never
    moves a note.
- **Ask Celine** answers with the layers you have on and the record you
  selected.
  - The page sends only ids, such as `flights:abc123`. The server looks the
    record up in that layer's own cache (`dashboard/world_live.py`), so
    anything the page claims about a record is ignored.
  - For a selected space station, the server calculates where it is now with
    `sgp4`.
  - Commands answered without the model: show/hide flights (or aircraft),
    satellites (or stations) and earthquakes, and reset globe. They flip the
    same switches you would.
  - Spoken answers use your local Celine voice when it's running.

These were merged from a parallel World View build. Its own feeds (OpenSky
aircraft, server-calculated station dots, a second earthquake feed), its entity
finder and its "follow" were removed so each layer exists once. Follow and a
cross-layer finder can come back on top of the layers above.

## Verification

```sh
python -m pytest tests/test_world_view.py -q
python -m pytest tests/test_world_layers.py tests/test_world_flights.py tests/test_world_satellites.py tests/test_world_live.py -q
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_view_ui.cjs
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_earthquakes_ui.cjs
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_flights_ui.cjs
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_satellites_ui.cjs
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_live_ui.cjs
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_entry_ui.cjs
```

Backend checks cover auth, input validation, malformed provider records,
credential isolation, caching, request spacing and provider outage responses.
DOM checks cover navigation, camera/selection restoration, coordinate bounds,
stale search results, escaped labels, fallback, visibility pause and disposal.
Earthquake checks cover opt-in, auth, malformed records, bounded streams and
refreshes, safe source links, marker/detail selection, retained stale snapshots,
cache aging, hidden-page pause, canceled responses and disposal.

World backend tests cover auth, malformed records, caching/coalescing, outage/stale retention, retry windows, request budget, payload limits, OAuth isolation, SGP4 positions and server-resolved chat context. The three World View DOM suites cover navigation, race/failure behavior, entry/focus/renderer lifecycle, selection, aging/tracking, cancellation, project state and terrain races.

Real browser checks use Cesium and public flights/USGS feeds in an isolated loopback preview. Agent-context behavior is verified with a test agent; actual Celine audio, microphone recognition and account-backed ion buildings still need checks in running Apex. CelesTrak had TLS timeouts on this machine during this review; no fake satellite data was substituted. These checks do not certify all Lenovo hand gestures or continuous multi-hour provider availability.

It needs Playwright/Chromium and Python dependencies. Set `APEX_TEST_PYTHON`
and `APEX_CHROMIUM_PATH` if those runtimes are outside PATH. Set
`APEX_WORLD_SCREENSHOTS` to a directory to capture desktop, location and mobile
screenshots. In environments where Chromium needs the runtime's Node proxy
transport, use `APEX_TEST_PROXY_FETCH=1 NODE_USE_ENV_PROXY=1`; TLS verification
remains enabled. The test uses real Cesium and imagery, with unrelated Command
API responses stubbed; it does not call the assistant or use a real account.
The earthquake response is deterministic by default. Set
`APEX_TEST_LIVE_QUAKES=1` to exercise the real USGS route and live provider. It
then injects a refresh failure to verify that event markers and original
timestamps remain available with a stale label. Aircraft use deterministic data
by default; `APEX_TEST_LIVE_FLIGHTS=1` exercises the live ADSB.lol backend. The
regression checks aircraft picking, unit labels, retained stale snapshots,
coexisting layers and mobile detail access. Real Cesium entity picking is
checked independently of the location-selection marker.

Satellite DOM checks use the actual vendored SGP4 library and test a published
reference vector, OMM/TLE agreement, kilometer-to-meter rendering, element age
limits, a single predicted orbit, source failure retention, layer coexistence,
late response cancellation and hidden-page disposal. The real browser uses a
deterministic OMM by default. Set `APEX_TEST_STATIONS_CACHE` to a valid saved source
cache for the actual backend/real-source-data path; this avoids downloading the
same CelesTrak group again during repeated verification.

## Lenovo comfort check before merge

The optional `APEX_TEST_COMFORT=1` browser run checks 1920×1080 and 1366×768
layout, reduced-motion camera navigation, idle rendering, one-second orbit
updates without camera movement, and three open/close cycles under four-times
CPU throttling. These checks use headless software WebGL in the cloud. They do
not establish Lenovo frame rate, battery use, fan noise or physical comfort.

On 2026-10-01 the cloud run passed with zero page errors: zero rendered frames
during four seconds idle, 15 rendered frames during three seconds of satellite
updates with a fixed camera, immediate reduced-motion navigation, both desktop
sizes, and three reopen cycles under four-times CPU throttling. No layer requests
continued after closure. The final targeted backend suite passed all 42 checks;
aircraft/core DOM checks passed. Earlier complete checks passed 3,269 Python tests
and 43 Node checks; four new address-validation cases are included in the final
CI run. The first Windows launch exposed a second dotenv search that could load a broken
parent file. The corrected bootstrap passes five regression cases using real
python-dotenv; the previous bootstrap reproduces the null-character failure.
The corrected CMD launcher still needs its Windows retry.

Test the complete `feat/world-satellites` branch in a separate Windows worktree
so the existing checkout stays intact. Stop the old Apex process first so port
7860 is free. With the usual checkout at `%USERPROFILE%\Apex`, run in Command
Prompt:

```bat
cd /d "%USERPROFILE%\Apex"
git fetch origin
git worktree add "%USERPROFILE%\Apex-world-test" origin/feat/world-satellites
cd /d "%USERPROFILE%\Apex-world-test"
scripts\test_world_view_windows.cmd
```

The launcher reuses a local Python environment when found and reads only the
worktree `.env`, or the existing sibling Apex `.env` if the worktree has none.
It then disables automatic dotenv discovery in the launched process so main and
config cannot accidentally load a different parent `.env`. It does not copy,
modify or print credentials. Python-dotenv 1.2 or later is required. An explicit `APEX_TEST_PYTHON` path
can override environment detection. Open `http://127.0.0.1:7860` and use
**Open World View**. Keep the Command Prompt running. World View uses mouse,
touchpad and keyboard controls; hand/voice globe navigation is a later milestone.

Spend at least five minutes on the Lenovo:

1. With layers off, drag, wheel/pinch zoom and right-drag tilt. Check for jumps,
   fatigue and readable controls at normal Windows scaling. Visit Byblos and
   enter another city or coordinates. Confirm provider credits are reachable.
2. Enable earthquakes, regional aircraft and station satellites. Inspect one of
   each, switch selections and turn layers off/on. Camera updates must not pull
   you away. Readouts should stay reachable and stale warnings understandable.
3. Close/reopen three times; use Escape from the search field. Confirm focus
   returns to Command, your location/map are restored, and nothing gets stuck.
4. Watch Task Manager's browser CPU/GPU before opening, while navigating, after
   30 seconds idle, and after closing. Compare with the same Command baseline;
   GPU activity should settle and no World View frame should remain after close.
5. Return to Board/Study and try the existing hand controls for a minute. Check
   that opening/closing World View has not made those controls less comfortable.
   Note visible stalls, fan changes, warmth, and any wrist/shoulder strain.

Record browser, Windows display scaling, plugged-in/battery status, smoothness,
selection comfort, idle/closed behavior, and any issue with its exact action.
Physical Lenovo results are pending until the user reports them. Merge #23,
then #24, then #25 only after that check passes, and re-check main CI afterward.

## Next milestones

1. Structured scene context and confirmed commands for Apex's assistant.
2. Comfortable opt-in hand navigation using Apex's existing tracking pipeline.
3. Additional provider layers and photorealistic sources after their terms,
   configuration and performance have been checked.
