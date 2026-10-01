# Apex World View

## Milestone 1: globe navigation

From **Command**, choose **Open World View** on the planet stage. It opens a
full-screen panel. **Command** or Escape returns to the same dashboard. The
standalone page is `/world`; modifier-clicking the entry can open another tab.

- Drag the globe, scroll/pinch to zoom, and right-drag to tilt.
- Choose Byblos, Beirut or Rome, or enter a saved city or `latitude, longitude`.
- Other place names use an explicit submitted search through Photon. Search
  results are choices; the first result is never silently selected.
- Click the Earth to mark an ellipsoid-based location. This is a coordinate
  selection, not a live observed event or terrain survey.
- Switch between satellite imagery, street map and globe only. Imagery failure
  falls back to streets; a globe remains usable if imagery is unavailable.
- Camera, map choice and selected coordinate are stored in this browser. They
  survive reopening/reloading. Reset globe recenters the camera; Clear selection
  removes the marker.

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

No flight, satellite, weather, vessel or camera feeds are enabled yet.

## Dependencies and integration

CesiumJS **1.124.0** and its assets are loaded from jsDelivr only when World View
opens. No Node installation or map/API key is required on the Apex host. Internet
access is needed to load Cesium and map imagery. If the engine fails to load,
the page offers retry and return controls. Map-only requests go directly to the
provider from the browser; no Apex token is attached to those requests.

The dedicated iframe avoids loading the globe in Command. Closing the panel
unloads its renderer, while backgrounding the page pauses its render loop.
Cesium uses render-on-demand and CSS-pixel resolution. The initial terrain is
an ellipsoid; elevation and photorealistic tiles are outside this milestone.

The small imagery adapter in `dashboard/static/world/imagery.js` is adapted
from [God's Eye View](https://github.com/bilawalsidhu/gods-eye-view), pinned at
`e7707d9a0f34d9fbffc300023c319f95caa5be30`, `src/maps/imagery.js`.
It uses Cesium's browser global instead of a bundler import and omits ion
providers. The upstream MIT license is retained beside it. The full upstream
application, its credentials, datasets and 3D models are not bundled.

## Sources and attribution

- CesiumJS: Apache 2.0; retain its asset notices and credit widget.
- Esri World Imagery: Esri/Maxar/Earthstar/GIS User Community attribution is
  displayed by the provider. Service/data terms apply independently of code.
- OpenStreetMap tiles: OSM contributor credit remains visible; use is subject
  to the public tile service's policy. No bulk/offline tile downloader is added.
- Photon place search: results identify Photon/OpenStreetMap. An explicit
  remote search sends the entered place name to Photon from the Apex host.

`/world` is a public empty shell like `/board`. `/api/world/search` remains
behind existing dashboard authentication. It calls one fixed HTTPS endpoint,
does not follow redirects or forward credentials, validates returned points,
caches up to 128 searches for 10 minutes and spaces uncached requests by at
least one second. It does not accept a provider URL from the user.

`/api/world/layers/earthquakes` also requires dashboard authentication. It streams
one fixed HTTPS source with a 10-second timeout, no redirects and no forwarded
credentials. Responses are capped at 2 MiB, processing at 1,500 records, and
rendering at the 300 latest validated events. IDs, geometry, timestamps, magnitude
and depth are validated; event links are built from the validated ID. A shared
lock and a 60-second cache/retry interval bound requests across multiple windows.
USGS generated times are preserved even when the service returns older data.

## Verification

```sh
python -m pytest tests/test_world_view.py -q
python -m pytest tests/test_world_layers.py -q
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_view_ui.cjs
NODE_PATH=/path/to/jsdom/node_modules node scripts/check_world_earthquakes_ui.cjs
```

Backend checks cover auth, input validation, malformed provider records,
credential isolation, caching, request spacing and provider outage responses.
DOM checks cover navigation, camera/selection restoration, coordinate bounds,
stale search results, escaped labels, fallback, visibility pause and disposal.
Earthquake checks cover opt-in, auth, malformed records, bounded streams and
refreshes, safe source links, marker/detail selection, retained stale snapshots,
cache aging, hidden-page pause, canceled responses and disposal.

An optional real Cesium/WebGL regression starts an isolated dashboard process:

```sh
node scripts/check_world_view_browser.cjs
```

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
timestamps remain available with a stale label. Real Cesium entity picking is
checked independently of the location-selection marker.

Before calling this Lenovo-verified, open it there and check wheel/pinch/tilt,
search a new landmark, close/reopen, reload, resize to phone dimensions, disable
network access, and confirm that provider credits remain accessible and GPU
activity drops when World View is closed.

## Next milestones

1. Source-aware live flights and propagated satellites, each with
   explicit timestamps, unavailable/stale states and independent layer switches.
2. Structured scene context and confirmed commands for Apex's assistant.
3. Comfortable opt-in hand navigation using Apex's existing tracking pipeline.
4. Additional provider layers and photorealistic sources after their terms,
   configuration and performance have been checked.
