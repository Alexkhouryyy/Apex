/* Shared collapsed summary: one layer cannot erase another layer's warning. */
(() => {
  'use strict';
  const states = new Map();
  window.ApexLayerStatus = (name, state) => {
    if (state) states.set(name, state); else states.delete(name);
    document.getElementById('world-layer-summary').textContent = 'Live layers' +
      [...states].map(([label, value]) => ` · ${label} ${value}`).join('');
  };
})();
