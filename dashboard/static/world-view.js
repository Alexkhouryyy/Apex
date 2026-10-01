/* Apex World View — navigation foundation. No live telemetry or mic capture. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const KEY = 'apex.world.view.v1';
  const BASE = 'https://cdn.jsdelivr.net/npm/cesium@1.124.0/Build/Cesium/';
  const cities = {
    byblos: [34.123, 35.651], jbeil: [34.123, 35.651], beirut: [33.894, 35.502],
    rome: [41.903, 12.496], paris: [48.857, 2.352], london: [51.507, -.128],
    tokyo: [35.676, 139.65], 'new york': [40.713, -74.006], dubai: [25.204, 55.271],
  };
  const controls = [...document.querySelectorAll('button[data-place]'), $('world-reset'),
    $('world-search-button'), $('world-map')];
  let viewer = null, enginePromise = null, selected = null, map = 'satellite';
  let earthquakes = null;
  let engineReady = Boolean(window.Cesium);
  let initializing = false, disposed = false, searchId = 0, searchController = null;
  let mapId = 0, removeMapErrors = null, tileErrors = 0, saved = null;
  const number = (v, lo, hi) => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;
  function validLocation(v) {
    return v && number(v.lat, -90, 90) && number(v.lng, -180, 180);
  }
  function readState() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY));
      if (!v || v.version !== 1 || !validLocation(v.camera) ||
          !number(v.camera.height, 100, 50000000) ||
          !number(v.camera.heading, -Math.PI * 2, Math.PI * 2) ||
          !number(v.camera.pitch, -Math.PI / 2, Math.PI / 2) ||
          !number(v.camera.roll, -Math.PI * 2, Math.PI * 2)) return null;
      if (!['satellite', 'streets', 'outline'].includes(v.map)) return null;
      if (v.selected && (!validLocation(v.selected) || typeof v.selected.label !== 'string')) v.selected = null;
      return v;
    } catch (_) { return null; }
  }
  function status(text) { $('world-status').textContent = text; }
  function save() {
    if (!viewer || viewer.isDestroyed()) return;
    const p = viewer.camera.positionCartographic;
    const camera = {lat: Cesium.Math.toDegrees(p.latitude), lng: Cesium.Math.toDegrees(p.longitude),
      height: p.height, heading: viewer.camera.heading, pitch: viewer.camera.pitch, roll: viewer.camera.roll};
    $('world-position').textContent = `${camera.lat.toFixed(3)}°, ${camera.lng.toFixed(3)}° · ${(camera.height / 1000).toFixed(1)} km`;
    try {
      localStorage.setItem(KEY, JSON.stringify({version: 1, camera, map, selected}));
    } catch (_) { status('Your browser could not save this view. You can continue exploring.'); }
  }
  function loadEngine() {
    if (engineReady && window.Cesium) return Promise.resolve();
    if (enginePromise) return enginePromise;
    window.CESIUM_BASE_URL = BASE;
    enginePromise = new Promise((resolve, reject) => {
      const css = document.createElement('link');
      const script = document.createElement('script');
      css.rel = 'stylesheet'; css.href = BASE + 'Widgets/widgets.css';
      script.src = BASE + 'Cesium.js'; script.crossOrigin = 'anonymous';
      let remaining = 2, done = false;
      const timer = setTimeout(fail, 20000);
      function fail() {
        if (done) return;
        done = true; clearTimeout(timer); css.remove(); script.remove();
        enginePromise = null;
        reject(new Error('The globe could not load. Check your internet connection and try again.'));
      }
      function ready() {
        if (done) return;
        if (--remaining === 0) {
          if (!window.Cesium) return fail();
          done = true; engineReady = true; clearTimeout(timer); resolve();
        }
      }
      script.onload = css.onload = ready;
      script.onerror = css.onerror = fail;
      document.head.append(css, script);
    });
    return enginePromise;
  }
  function setLocation(location, fly = true) {
    if (!viewer || !validLocation(location)) return;
    earthquakes?.clearSelection();
    selected = {lat: location.lat, lng: location.lng, label: String(location.label || 'Selected location').slice(0, 240)};
    viewer.entities.removeAll();
    viewer.entities.add({position: Cesium.Cartesian3.fromDegrees(selected.lng, selected.lat),
      point: {pixelSize: 10, color: Cesium.Color.CYAN, outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2, disableDepthTestDistance: Number.POSITIVE_INFINITY}});
    $('world-selected-name').textContent = selected.label;
    $('world-selected-coords').textContent = `${selected.lat.toFixed(5)}°, ${selected.lng.toFixed(5)}°`;
    document.querySelector('.world-readout').hidden = false;
    if (fly) viewer.camera.flyTo({destination: Cesium.Cartesian3.fromDegrees(selected.lng, selected.lat, 90000),
      duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1.2});
    viewer.scene.requestRender(); save();
  }
  async function changeMap(next, fallback = false) {
    if (!viewer || !['satellite', 'streets', 'outline'].includes(next)) return;
    const id = ++mapId;
    removeMapErrors?.(); removeMapErrors = null; tileErrors = 0;
    map = next; $('world-map').value = next;
    viewer.imageryLayers.removeAll();
    viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#153141');
    viewer.scene.requestRender(); save();
    if (next === 'outline') { status('Globe only · map imagery is off.'); return; }
    status(fallback ? 'Satellite imagery unavailable. Loading the street map…' : 'Loading map imagery…');
    let timer;
    try {
      const provider = await Promise.race([window.ApexWorldImagery[next](),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Map timeout')), 12000); })]);
      if (disposed || id !== mapId || !viewer) return;
      viewer.imageryLayers.addImageryProvider(provider);
      removeMapErrors = provider.errorEvent.addEventListener(() => {
        if (disposed || id !== mapId || ++tileErrors < 3) return;
        if (next === 'satellite') changeMap('streets', true);
        else status('Street map tiles are unavailable. You can keep navigating or choose Globe only.');
      });
      viewer.scene.requestRender();
      status(fallback ? 'Using the street map because satellite imagery was unavailable.' : '');
    } catch (_) {
      if (disposed || id !== mapId) return;
      if (next === 'satellite') await changeMap('streets', true);
      else status('Map imagery is unavailable. You can keep navigating or choose Globe only.');
    } finally { clearTimeout(timer); }
  }
  async function initialize() {
    if (initializing || disposed || viewer) return;
    initializing = true;
    $('world-loading').hidden = false; $('world-retry').hidden = true;
    $('world-loading').querySelector('h2').textContent = 'Opening Earth…';
    $('world-loading').querySelector('p').textContent = 'Loading the globe.';
    try {
      await loadEngine();
      if (disposed) return;
      Cesium.Ion.defaultAccessToken = '';
      viewer = new Cesium.Viewer('world-globe', {
        baseLayer: false, terrainProvider: new Cesium.EllipsoidTerrainProvider(),
        animation: false, timeline: false, baseLayerPicker: false, geocoder: false,
        homeButton: false, sceneModePicker: false, navigationHelpButton: false,
        fullscreenButton: false, infoBox: false, selectionIndicator: false,
        requestRenderMode: true, maximumRenderTimeChange: Infinity, shouldAnimate: false,
      });
      viewer.targetFrameRate = 45;
      viewer.scene.globe.enableLighting = false;
      viewer.scene.screenSpaceCameraController.minimumZoomDistance = 100;
      viewer.scene.screenSpaceCameraController.maximumZoomDistance = 50000000;
      viewer.camera.moveEnd.addEventListener(save);
      viewer.screenSpaceEventHandler.setInputAction(event => {
        // The selected place pin can cover an event marker. Inspect the small,
        // bounded pick stack so a second click still opens the event details.
        if (earthquakes && viewer.scene.drillPick(event.position, 8).some(picked => earthquakes.pick(picked))) return;
        const hit = viewer.camera.pickEllipsoid(event.position, viewer.scene.globe.ellipsoid);
        if (!hit) return;
        const pos = Cesium.Cartographic.fromCartesian(hit);
        setLocation({lat: Cesium.Math.toDegrees(pos.latitude), lng: Cesium.Math.toDegrees(pos.longitude)}, false);
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
      saved = readState();
      const c = saved?.camera;
      viewer.camera.setView({destination: Cesium.Cartesian3.fromDegrees(c?.lng ?? 35.65, c?.lat ?? 25, c?.height ?? 19000000),
        orientation: {heading: c?.heading ?? 0, pitch: c?.pitch ?? -Math.PI / 2, roll: c?.roll ?? 0}});
      if (saved?.selected) setLocation(saved.selected, false);
      earthquakes = window.ApexEarthquakes?.create({viewer, Cesium, selectLocation: setLocation}) || null;
      controls.forEach(el => { el.disabled = false; });
      $('world-loading').querySelector('p').textContent = 'Preparing Earth geometry and map tiles…';
      // Engine load does not mean the terrain workers have produced a globe.
      // Keep visible progress until a rendered frame has finished its tile work.
      const removeReady = viewer.scene.postRender.addEventListener(() => {
        if (viewer && !viewer.isDestroyed() && viewer.scene.globe.tilesLoaded) {
          $('world-loading').hidden = true;
          removeReady();
        }
      });
      syncVisibility();
      viewer.scene.renderError.addEventListener(() => {
        status('Globe rendering stopped. Reload World View to retry.');
        controls.forEach(el => { el.disabled = true; });
        earthquakes?.destroy(); earthquakes = null;
      });
      await changeMap(saved?.map || 'satellite');
      syncVisibility();
    } catch (error) {
      earthquakes?.destroy(); earthquakes = null;
      if (viewer && !viewer.isDestroyed()) viewer.destroy();
      viewer = null;
      $('world-loading').hidden = false;
      $('world-loading').querySelector('h2').textContent = 'Earth could not open';
      $('world-loading').querySelector('p').textContent = error.message || 'WebGL may be unavailable. Try again in a browser with graphics acceleration enabled.';
      $('world-retry').hidden = false;
    } finally { initializing = false; }
  }
  function syncVisibility() {
    if (!viewer || viewer.isDestroyed()) return;
    viewer.useDefaultRenderLoop = !document.hidden;
    earthquakes?.setVisible(!document.hidden);
    if (!document.hidden) { viewer.resize(); viewer.scene.requestRender(); }
  }
  function invalidateSearch() {
    ++searchId; searchController?.abort(); searchController = null;
    $('world-results').replaceChildren();
  }
  function localPlace(query) {
    const city = cities[query.toLowerCase()];
    if (city) return {lat: city[0], lng: city[1], label: query};
    if (!/^[+-]?\d+(?:\.\d+)?\s*,\s*[+-]?\d+(?:\.\d+)?$/.test(query)) return null;
    const [lat, lng] = query.split(',').map(Number);
    if (!validLocation({lat, lng})) throw new Error('Latitude must be −90 to 90 and longitude −180 to 180.');
    return {lat, lng, label: 'Coordinates'};
  }
  $('world-search').addEventListener('submit', async event => {
    event.preventDefault();
    if (!viewer) return;
    invalidateSearch(); const id = searchId;
    const query = $('world-query').value.trim();
    try {
      const place = localPlace(query);
      if (place) { setLocation(place); status(''); return; }
      if (query.length < 3) throw new Error('Enter at least three characters, or latitude, longitude.');
      status('Searching places…');
      searchController = new AbortController();
      const token = localStorage.getItem('apex_token') || '';
      const response = await fetch('/api/world/search?q=' + encodeURIComponent(query), {
        signal: searchController.signal, headers: token ? {Authorization: 'Bearer ' + token} : {},
      });
      if (id !== searchId || disposed) return;
      if (response.status === 401) throw new Error('Sign in through Command to use online place search. Coordinates and saved cities still work.');
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Place search is unavailable. Try coordinates or a saved city.');
      }
      const data = await response.json();
      if (id !== searchId || disposed) return;
      const places = Array.isArray(data.results) ? data.results.filter(p => validLocation(p) && typeof p.label === 'string').slice(0, 5) : [];
      status(places.length ? 'Choose a result · Photon / OpenStreetMap' : 'No matching places found. Try another name or coordinates.');
      for (const place of places) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = place.label;
        button.addEventListener('click', () => { invalidateSearch(); setLocation(place); status(''); });
        $('world-results').append(button);
      }
    } catch (error) {
      if (id === searchId && !disposed && error.name !== 'AbortError') status(error.message);
    }
  });
  $('world-query').addEventListener('input', () => { invalidateSearch(); status(''); });
  document.querySelectorAll('button[data-place]').forEach(button => button.addEventListener('click', () => {
    invalidateSearch(); setLocation(localPlace(button.dataset.place)); status('');
  }));
  $('world-map').addEventListener('change', () => changeMap($('world-map').value));
  $('world-clear').addEventListener('click', () => {
    earthquakes?.clearSelection();
    selected = null; viewer?.entities.removeAll(); document.querySelector('.world-readout').hidden = true;
    viewer?.scene.requestRender(); save();
  });
  $('world-reset').addEventListener('click', () => {
    invalidateSearch(); viewer.camera.cancelFlight();
    viewer.camera.setView({destination: Cesium.Cartesian3.fromDegrees(35.65, 25, 19000000),
      orientation: {heading: 0, pitch: -Math.PI / 2, roll: 0}});
    save(); viewer.scene.requestRender(); status('');
  });
  function back(event) {
    save();
    if (parent !== window) { event?.preventDefault(); parent.postMessage({type: 'apex.world.close'}, location.origin); }
  }
  $('world-back').addEventListener('click', back);
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && parent !== window) back(event); });
  $('world-retry').addEventListener('click', initialize);
  document.addEventListener('visibilitychange', syncVisibility);
  window.addEventListener('pagehide', event => {
    save(); invalidateSearch();
    earthquakes?.setVisible(false);
    if (event.persisted) { if (viewer) viewer.useDefaultRenderLoop = false; return; }
    disposed = true; ++mapId; removeMapErrors?.();
    earthquakes?.destroy(); earthquakes = null;
    if (viewer && !viewer.isDestroyed()) viewer.destroy();
    viewer = null;
  });
  window.addEventListener('pageshow', event => { if (event.persisted) syncVisibility(); });
  initialize();
})();
