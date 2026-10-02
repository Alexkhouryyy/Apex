/* The car's link to the Apex PC (the /drive page).

   In a car the connection drops: tunnels, dead zones, a phone switching
   towers. A reply that is still being worked on must survive that, and the
   driver must be told plainly whether Apex can be reached at all. */
(function (root) {
  'use strict';

  // What is new in a job's text since the last poll. The server only ever
  // appends while a reply streams; anything else (a final cleaned-up text)
  // is a reset, and nothing more is fed to the voice from it.
  function textDelta(previous, next) {
    previous = previous || ''; next = next || '';
    if (next.startsWith(previous)) return {delta: next.slice(previous.length), reset: false};
    return {delta: '', reset: true};
  }

  // Fetch, riding out a dropped connection for up to `graceMs`. A network
  // failure is retried with growing pauses; an answer from the server (even
  // an error status) is returned, because the server is reachable and the
  // caller must decide. `onState('reconnecting'|'connected')` drives the UI.
  async function patient(fetchOnce, {graceMs = 90000, sleep, onState = () => {}, now = () => Date.now()} = {}) {
    sleep = sleep || (ms => new Promise(r => setTimeout(r, ms)));
    const started = now();
    let wait = 500, lost = false;
    while (true) {
      try {
        const result = await fetchOnce();
        if (lost) onState('connected');
        return result;
      } catch (exc) {
        if (exc && exc.status) throw exc;            // the server answered: not a connection problem
        if (now() - started >= graceMs) {
          const error = new Error(`Lost the connection to your Apex for ${Math.round(graceMs / 1000)} seconds. ` +
            'The task keeps running on your PC; reconnect to pick it up.');
          error.offline = true;
          throw error;
        }
        if (!lost) { lost = true; onState('reconnecting'); }
        await sleep(wait);
        wait = Math.min(5000, wait * 1.6);
      }
    }
  }

  // One look at the link: what to show the driver.
  function describe(result) {
    if (result.ok) {
      return {state: 'online', text: `Connected to your Apex · ${Math.round(result.ms)} ms`};
    }
    if (result.status === 401) {
      return {state: 'auth', text: 'Apex answered but needs your token. Enter it to continue.'};
    }
    if (result.status) {
      return {state: 'trouble', text: `Apex answered with an error (${result.status}). It may still be starting.`};
    }
    return {state: 'offline', text: result.deviceOffline
      ? 'This device has no internet connection.'
      : "Can't reach your Apex. Check that the PC is on and awake, Apex is running, and this device can reach it (Tailscale on)."};
  }

  const api = {textDelta, patient, describe};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ApexCarLink = api;
})(globalThis);
