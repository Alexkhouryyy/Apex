/* Regional position snapshots; no simulated movement or inferred flight routes. */
(() => {
  'use strict';
  const KEY = 'apex.world.flights.v1', $ = id => document.getElementById(id);
  const num = (v, lo, hi) => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;
  const utc = ms => new Date(ms).toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC');
  const optional = (v, lo, hi) => num(v, lo, hi) ? v : null;
  function normalize(data) {
    const now = Date.now();
    if (!data || data.source !== 'ADSB.lol' || !Array.isArray(data.aircraft) ||
        !num(data.generated_at, 946684800000, now + 30000) || !num(data.fetched_at, 946684800000, now + 30000) ||
        !num(data.area?.lat, -90, 90) || !num(data.area?.lng, -180, 180) || data.area.radius_nm !== 250)
      throw new Error('Invalid aircraft snapshot.');
    const records = new Map(), text = (v, n) => typeof v === 'string' ? v.slice(0, n) : '';
    for (const a of data.aircraft.slice(0, 500)) {
      if (!a || typeof a.id !== 'string' || !/^[0-9a-f]{6}$/.test(a.id) || !num(a.lat, -90, 90) || !num(a.lng, -180, 180) ||
          !num(a.position_at, data.generated_at - 120000, data.generated_at)) continue;
      records.set(a.id, {...a, callsign: text(a.callsign, 16), registration: text(a.registration, 20),
        aircraft_type: text(a.aircraft_type, 12), position_source: text(a.position_source, 24),
        on_ground: a.on_ground === true, altitude_ft: optional(a.altitude_ft, -2000, 100000),
        altitude_kind: ['geometric', 'barometric'].includes(a.altitude_kind) ? a.altitude_kind : 'unknown',
        speed_knots: optional(a.speed_knots, 0, 2000), track_deg: optional(a.track_deg, 0, 360)});
    }
    return {...data, aircraft: [...records.values()]};
  }
  const ARROW = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="M12 2L21 21L12 16L3 21Z" fill="#77dfff" stroke="#07131d" stroke-width="2"/></svg>');
  const UNKNOWN = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><circle cx="12" cy="12" r="6" fill="#77dfff" stroke="#07131d" stroke-width="2"/></svg>');
  window.ApexFlights = {create({viewer, Cesium, selectLocation, getArea, reportState = () => {}}) {
    const toggle = $('flight-toggle'), refreshButton = $('flight-refresh'), areaButton = $('flight-area');
    const source = new Cesium.CustomDataSource('Apex flights'); viewer.dataSources.add(source);
    let enabled = false, visible = !document.hidden, disposed = false, busy = false;
    let area = {lat: 34.1, lng: 35.6}, snapshot = null, records = new Map(), selection = null;
    let forcedStale = false, generation = 0, controller = null, timer = null, timeout = null, notice = '';
    try {
      const saved = JSON.parse(localStorage.getItem(KEY));
      if (num(saved?.lat, -90, 90) && num(saved?.lng, -180, 180)) area = {lat: saved.lat, lng: saved.lng};
      enabled = saved?.enabled === true;
    } catch (_) {}
    toggle.checked = enabled; toggle.disabled = false;
    const stale = () => Boolean(snapshot && (forcedStale || snapshot.stale || snapshot.refresh_failed ||
      Date.now() - snapshot.generated_at > 90000 || Date.now() - snapshot.fetched_at > 90000));
    const aged = a => stale() || Date.now() - a.position_at > 30000;
    const name = a => a.callsign || a.registration || a.id.toUpperCase();
    const locationOf = a => ({lat: a.lat, lng: a.lng, label: `${name(a)} · aircraft`});
    function details() {
      $('flight-details').hidden = !selection;
      if (!selection) return;
      const a = selection;
      for (const [id, value] of Object.entries({
        'flight-icao': a.id.toUpperCase(), 'flight-registration': a.registration || 'Unknown',
        'flight-type': a.aircraft_type || 'Unknown',
        'flight-altitude': a.on_ground ? 'On ground' : a.altitude_ft === null ? 'Unknown' : `${Math.round(a.altitude_ft).toLocaleString()} ft · ${a.altitude_kind}`,
        'flight-speed': a.speed_knots === null ? 'Unknown' : `${Math.round(a.speed_knots)} kn · ground speed`,
        'flight-track': a.track_deg === null ? 'Unknown' : `${Math.round(a.track_deg)}° · ground track`,
        'flight-position-source': a.position_source || 'Unknown',
      })) $(id).textContent = value;
      $('flight-position-time').dateTime = new Date(a.position_at).toISOString();
      $('flight-position-time').textContent = utc(a.position_at);
      const isOld = !enabled || !records.has(a.id) || aged(a);
      $('flight-detail-status').dataset.state = isOld ? 'stale' : 'current';
      $('flight-detail-status').textContent = !enabled ? 'Position snapshot · layer is off.' :
        !records.has(a.id) ? 'Last position · no longer in this area’s current feed.' :
        isOld ? 'Last reported position · stale. No movement is predicted.' : 'Recent reported position · updates every 30 seconds.';
    }
    function updateStatus() {
      refreshButton.disabled = areaButton.disabled = !enabled || busy || !visible;
      const hasOld = snapshot?.aircraft.some(aged);
      reportState('Flights', !enabled ? '' : stale() || hasOld ? 'stale' : !snapshot && notice && !busy ? 'unavailable' : 'on');
      $('flight-status').dataset.state = stale() || hasOld ? 'stale' : 'current';
      $('flight-status').textContent = !enabled ? 'Off · enable to load regional aircraft.' : busy ? 'Loading aircraft…' :
        snapshot ? `${stale() ? 'Stale' : 'Current'} snapshot · ${snapshot.aircraft.length} aircraft${snapshot.truncated ? ' (limited to 500)' : ''}${hasOld && !stale() ? ' · older positions dimmed' : ''}${notice ? ' · ' + notice : ''}` : notice || 'Waiting for aircraft…';
      $('flight-region').textContent = `Loaded area: ${area.lat.toFixed(1)}°, ${area.lng.toFixed(1)}° · 250 nautical miles`;
      $('flight-times').textContent = enabled && snapshot ? `Provider ${utc(snapshot.generated_at)} · fetched ${utc(snapshot.fetched_at)}` : '';
      for (const a of records.values()) {
        const entity = source.entities.getById('flight:' + a.id);
        if (entity) entity.billboard.color = aged(a) ? Cesium.Color.fromCssColorString('#ffcf86').withAlpha(0.55) : Cesium.Color.WHITE;
      }
      details(); viewer.scene.requestRender();
    }
    function clearSelection() { selection = null; details(); }
    function inspect(id, fly = true) {
      const a = records.get(id); if (!enabled || !a) return false;
      selectLocation(locationOf(a), fly); selection = a; details(); return true;
    }
    function render() {
      records = new Map(snapshot.aircraft.map(a => [a.id, a])); source.entities.removeAll();
      for (const a of records.values()) source.entities.add({id: 'flight:' + a.id,
        position: Cesium.Cartesian3.fromDegrees(a.lng, a.lat, Math.max(100, a.on_ground ? 0 : (a.altitude_ft ?? 0) * 0.3048)),
        billboard: {image: a.track_deg === null ? UNKNOWN : ARROW, width: 18, height: 18, rotation: -(a.track_deg ?? 0) * Math.PI / 180,
          alignedAxis: new Cesium.Cartesian3(-Math.sin(a.lat * Math.PI / 180) * Math.cos(a.lng * Math.PI / 180),
            -Math.sin(a.lat * Math.PI / 180) * Math.sin(a.lng * Math.PI / 180), Math.cos(a.lat * Math.PI / 180)), color: Cesium.Color.WHITE}});
      $('flight-events').replaceChildren();
      for (const a of snapshot.aircraft.slice(0, 8)) {
        const b = document.createElement('button'); b.type = 'button'; b.textContent = name(a);
        b.addEventListener('click', () => inspect(a.id)); $('flight-events').append(b);
      }
      if (selection && records.has(selection.id)) {
        const revised = records.get(selection.id); selectLocation(locationOf(revised), false); selection = revised;
      }
      updateStatus();
    }
    function stop() {
      ++generation; clearTimeout(timer); timer = null; clearTimeout(timeout); timeout = null;
      controller?.abort(); controller = null; busy = false;
    }
    function schedule() {
      clearTimeout(timer); if (!disposed && enabled && visible) timer = setTimeout(() => {updateStatus(); refresh();}, 30000);
    }
    async function refresh() {
      if (disposed || !enabled || !visible || busy) return;
      clearTimeout(timer); const id = ++generation; busy = true; notice = ''; updateStatus();
      const requestController = new AbortController(); controller = requestController;
      const abortTimer = setTimeout(() => requestController.abort(), 15000); timeout = abortTimer;
      try {
        let token = ''; try { token = localStorage.getItem('apex_token') || ''; } catch (_) {}
        const response = await fetch(`/api/world/layers/flights?lat=${area.lat}&lng=${area.lng}`, {
          signal: requestController.signal, headers: token ? {Authorization: 'Bearer ' + token} : {}, cache: 'no-store'});
        if (disposed || id !== generation) return;
        if (response.status === 401) throw new Error('Sign in through Command, then refresh this layer.');
        if (!response.ok) throw new Error(response.status === 503 ? 'Aircraft feed unavailable; try again shortly.' : 'Aircraft refresh unavailable.');
        const data = normalize(await response.json());
        if (disposed || id !== generation) return;
        if (Math.abs(data.area.lat - area.lat) > 0.11 || Math.abs(data.area.lng - area.lng) > 0.11) throw new Error('Aircraft snapshot is for another area.');
        snapshot = data; area = {...data.area}; forcedStale = false;
        notice = data.refresh_failed ? 'refresh unavailable; last positions retained' : '';
        render();
      } catch (e) {
        if (disposed || id !== generation) return;
        forcedStale = true; notice = e.name === 'AbortError' ? 'Aircraft request timed out.' : e.message;
      } finally {
        clearTimeout(abortTimer);
        if (!disposed && id === generation) {busy = false; controller = null; timeout = null; updateStatus(); schedule();}
      }
    }
    function save() { try {localStorage.setItem(KEY, JSON.stringify({enabled, lat: area.lat, lng: area.lng}));} catch (_) {} }
    function changeArea() {
      const next = getArea(); if (!num(next?.lat, -90, 90) || !num(next?.lng, -180, 180)) return;
      stop(); area = {lat: Math.round(next.lat * 10) / 10, lng: Math.round(next.lng * 10) / 10};
      // A new area must never be labeled with the previous region's aircraft.
      snapshot = null; records.clear(); clearSelection(); source.entities.removeAll(); $('flight-events').replaceChildren();
      forcedStale = false; save(); updateStatus(); refresh();
    }
    function toggleLayer() {
      enabled = toggle.checked; stop(); notice = ''; save();
      if (!enabled) {source.entities.removeAll(); $('flight-events').replaceChildren();}
      else if (snapshot) render();
      updateStatus(); if (enabled) refresh();
    }
    toggle.addEventListener('change', toggleLayer); refreshButton.addEventListener('click', refresh); areaButton.addEventListener('click', changeArea);
    updateStatus(); if (enabled) refresh();
    return {clearSelection, pick(picked) {
      const entity = picked?.id;
      return Boolean(enabled && entity && typeof entity.id === 'string' && entity.id.startsWith('flight:') &&
        source.entities.getById(entity.id) === entity && inspect(entity.id.slice(7), false));
    }, setVisible(next) {
      if (disposed || visible === next) return; visible = next;
      if (!visible) stop(); else if (enabled) refresh(); updateStatus();
    }, destroy() {
      if (disposed) return; disposed = true; stop(); reportState('Flights', '');
      toggle.removeEventListener('change', toggleLayer); refreshButton.removeEventListener('click', refresh); areaButton.removeEventListener('click', changeArea);
      toggle.disabled = refreshButton.disabled = areaButton.disabled = true;
      if (!viewer.isDestroyed()) viewer.dataSources.remove(source, true);
    }};
  }};
})();
