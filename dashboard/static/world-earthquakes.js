/* Opt-in USGS snapshots. Separate data source keeps place selection independent. */
(() => {
  'use strict';
  const KEY = 'apex.world.earthquakes.v1';
  const STALE = 5 * 60 * 1000;
  const $ = id => document.getElementById(id);
  const num = (v, low, high) => typeof v === 'number' && Number.isFinite(v) && v >= low && v <= high;
  const utc = value => new Date(value).toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC');
  function normalize(data) {
    const now = Date.now();
    if (!data || data.source !== 'USGS' || !Array.isArray(data.events) ||
        !num(data.generated_at, 946684800000, now + STALE) ||
        !num(data.fetched_at, 946684800000, now + STALE)) throw new Error('Invalid USGS snapshot.');
    const records = new Map();
    for (const e of data.events.slice(0, 300)) {
      if (!e || typeof e.id !== 'string' || !/^[A-Za-z0-9._-]{1,80}$/.test(e.id) ||
          !num(e.lat, -90, 90) || !num(e.lng, -180, 180) || !num(e.magnitude, 2.5, 10) ||
          !num(e.depth_km, -10, 1000) || !num(e.time, data.generated_at - 86400000, data.generated_at + STALE) ||
          !num(e.updated, e.time, now + STALE) || typeof e.place !== 'string') continue;
      records.set(e.id, {...e, place: e.place.slice(0, 240),
        review_status: ['automatic', 'reviewed'].includes(e.review_status) ? e.review_status : 'unknown',
        url: 'https://earthquake.usgs.gov/earthquakes/eventpage/' + e.id});
    }
    return {...data, events: [...records.values()].sort((a, b) => b.time - a.time)};
  }
  window.ApexEarthquakes = {create({viewer, Cesium, selectLocation, reportState = (name, state) => {
    $('world-layer-summary').textContent = state ? `Live layers · ${name} ${state}` : 'Live layers';
  }}) {
    const toggle = $('quake-toggle'), refreshButton = $('quake-refresh');
    const source = new Cesium.CustomDataSource('Apex earthquakes');
    viewer.dataSources.add(source);
    let enabled = false, visible = !document.hidden, disposed = false, busy = false;
    let snapshot = null, records = new Map(), selection = null, forcedStale = false;
    let generation = 0, controller = null, timer = null, requestTimeout = null, notice = '';
    try { enabled = localStorage.getItem(KEY) === 'on'; } catch (_) {}
    toggle.checked = enabled; toggle.disabled = false;
    const isStale = () => Boolean(snapshot && (forcedStale || snapshot.stale || snapshot.refresh_failed ||
      Date.now() - snapshot.generated_at > STALE || Date.now() - snapshot.fetched_at > STALE));
    function details() {
      $('quake-details').hidden = !selection;
      if (!selection) return;
      $('quake-magnitude').textContent = selection.magnitude.toFixed(1);
      $('quake-depth').textContent = `${selection.depth_km.toFixed(1)} km`;
      for (const [id, value] of [['quake-occurred', selection.time], ['quake-updated', selection.updated]]) {
        $(id).dateTime = new Date(value).toISOString(); $(id).textContent = utc(value);
      }
      $('quake-review').textContent = selection.review_status;
      $('quake-source').href = selection.url;
      const stale = isStale() || !enabled || !records.has(selection.id);
      $('quake-detail-status').dataset.state = stale ? 'stale' : 'current';
      $('quake-detail-status').textContent = !enabled ? 'USGS event snapshot · layer is off.' :
        !records.has(selection.id) ? 'USGS event snapshot · no longer in the current feed.' :
        isStale() ? 'USGS event snapshot · feed is stale.' : 'USGS event · current feed snapshot.';
    }
    function updateStatus() {
      const el = $('quake-status');
      reportState('Earthquakes', !enabled ? '' : isStale() ? 'stale' :
        !snapshot && notice && !busy ? 'unavailable' : 'on');
      refreshButton.disabled = !enabled || busy || !visible;
      el.dataset.state = isStale() ? 'stale' : 'current';
      el.textContent = !enabled ? 'Off · enable to load the USGS feed.' : busy ?
        'Refreshing USGS earthquakes…' + (isStale() ? ' · stale snapshot retained' : '') :
        snapshot ? `${isStale() ? 'Stale' : 'Current'} snapshot · ${snapshot.events.length} events${snapshot.truncated ? ' (limited to the latest 300)' : ''}${notice ? ' · ' + notice : ''}` : notice || 'Waiting for USGS…';
      $('quake-times').textContent = enabled && snapshot ?
        `USGS generated ${utc(snapshot.generated_at)} · fetched ${utc(snapshot.fetched_at)}` : '';
      details();
    }
    function clearSelection() { selection = null; details(); }
    const locationOf = event => ({lat: event.lat, lng: event.lng,
      label: `M ${event.magnitude.toFixed(1)} · ${event.place}`});
    function inspect(id, fly = true) {
      const event = records.get(id);
      if (!enabled || !event) return false;
      selectLocation(locationOf(event), fly);
      selection = event; details(); return true;
    }
    function render() {
      records = new Map(snapshot.events.map(e => [e.id, e]));
      source.entities.removeAll();
      for (const e of snapshot.events) {
        source.entities.add({id: 'earthquake:' + e.id,
          position: Cesium.Cartesian3.fromDegrees(e.lng, e.lat, 1000),
          point: {pixelSize: Math.min(18, 5 + e.magnitude * 1.5),
            color: Cesium.Color.fromCssColorString(e.magnitude >= 5 ? '#ff78c7' : e.magnitude >= 4 ? '#ff8866' : '#ffd18a'),
            outlineColor: Cesium.Color.BLACK, outlineWidth: 1}});
      }
      $('quake-events').replaceChildren();
      for (const e of snapshot.events.slice(0, 8)) {
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = `M ${e.magnitude.toFixed(1)} · ${e.place}`;
        button.addEventListener('click', () => inspect(e.id)); $('quake-events').append(button);
      }
      if (selection && records.has(selection.id)) {
        const revised = records.get(selection.id);
        selectLocation(locationOf(revised), false); selection = revised;
      }
      updateStatus(); viewer.scene.requestRender();
    }
    function stop() {
      ++generation; clearTimeout(timer); timer = null; clearTimeout(requestTimeout); requestTimeout = null;
      controller?.abort(); controller = null; busy = false;
    }
    function schedule() {
      clearTimeout(timer);
      if (!disposed && enabled && visible) timer = setTimeout(() => {updateStatus(); refresh();}, 60000);
    }
    async function refresh() {
      if (disposed || !enabled || !visible || busy) return;
      clearTimeout(timer); const id = ++generation; busy = true; notice = ''; updateStatus();
      controller = new AbortController();
      const requestController = controller;
      const abortTimer = setTimeout(() => requestController.abort(), 15000);
      requestTimeout = abortTimer;
      try {
        let token = ''; try { token = localStorage.getItem('apex_token') || ''; } catch (_) {}
        const response = await fetch('/api/world/layers/earthquakes', {
          signal: controller.signal, headers: token ? {Authorization: 'Bearer ' + token} : {}, cache: 'no-store',
        });
        if (disposed || id !== generation) return;
        if (response.status === 401) throw new Error('Sign in through Command, then refresh this layer.');
        if (!response.ok) throw new Error('USGS refresh unavailable.');
        const data = normalize(await response.json());
        if (disposed || id !== generation) return;
        snapshot = data; forcedStale = false;
        notice = data.refresh_failed ? 'USGS refresh unavailable; showing the last snapshot' : '';
        render();
      } catch (error) {
        if (disposed || id !== generation) return;
        forcedStale = true;
        notice = error.name === 'AbortError' ? 'USGS request timed out.' : error.message || 'USGS refresh unavailable.';
      } finally {
        clearTimeout(abortTimer);
        if (!disposed && id === generation) {busy = false; controller = null; requestTimeout = null; updateStatus(); schedule();}
      }
    }
    function toggleLayer() {
      enabled = toggle.checked; stop(); notice = '';
      try { localStorage.setItem(KEY, enabled ? 'on' : 'off'); } catch (_) {}
      if (!enabled) {source.entities.removeAll(); $('quake-events').replaceChildren();}
      else if (snapshot) render();
      updateStatus(); viewer.scene.requestRender(); if (enabled) refresh();
    }
    toggle.addEventListener('change', toggleLayer); refreshButton.addEventListener('click', refresh);
    updateStatus(); if (enabled) refresh();
    return {
      selectedId: () => selection?.id ?? null,
      clearSelection,
      pick(picked) {
        const entity = picked?.id;
        if (!enabled || !entity || typeof entity.id !== 'string' || !entity.id.startsWith('earthquake:') ||
            source.entities.getById(entity.id) !== entity) return false;
        return inspect(entity.id.slice('earthquake:'.length), false);
      },
      setVisible(next) {
        if (disposed || visible === next) return;
        visible = next;
        if (!visible) stop();
        else if (enabled) refresh();
        updateStatus();
      },
      destroy() {
        if (disposed) return;
        disposed = true; stop(); reportState('Earthquakes', '');
        toggle.removeEventListener('change', toggleLayer); refreshButton.removeEventListener('click', refresh);
        toggle.disabled = true; refreshButton.disabled = true;
        if (!viewer.isDestroyed()) viewer.dataSources.remove(source, true);
      },
    };
  }};
})();
