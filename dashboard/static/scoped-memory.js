/* Owner-controlled evidence with explicit source and deletion receipts. */
(() => {
  const root = document.getElementById('scoped-memory-list');
  if (!root) return;
  const status = document.getElementById('scoped-memory-status');
  async function load() {
    try {
      const memories = await api('/api/research/memory');
      root.replaceChildren();
      for (const memory of memories) {
        const card = document.createElement('div');card.className = 'card';
        const content = document.createElement('p');content.textContent = memory.text;
        const detail = document.createElement('p');
        detail.textContent = `${memory.domain} · ${memory.purposes.join(', ')} · ${memory.inferred ? 'Inferred' : 'Observed'} · ${memory.source}`;
        const provenance = document.createElement('small');
        provenance.textContent = `Evidence ${memory.id}; sources: ${memory.provenance.join(', ')}${memory.expires_at ? '; expires '+new Date(memory.expires_at*1000).toLocaleString() : ''}`;
        const forget = document.createElement('button');forget.type = 'button';forget.textContent = 'Forget';
        forget.addEventListener('click', async () => {
          forget.disabled = true;
          try {
            const receipt = await api(`/api/research/memory/${encodeURIComponent(memory.id)}/forget`, {method:'POST'});
            await load();status.textContent = `Deleted ${receipt.deleted.length} evidence records, including linked views.`;
          } catch (error) {status.textContent = error.message;forget.disabled = false;}
        });
        card.append(content, detail, provenance, forget);root.append(card);
      }
      if (!memories.length) root.textContent = 'No approved evidence saved.';
      status.textContent = '';
    } catch (error) {status.textContent = error.message;}
  }
  const form = document.getElementById('scoped-memory-form');
  form.addEventListener('submit', async event => {
    event.preventDefault();const submit = form.querySelector('button');submit.disabled = true;
    try {
      await api('/api/research/memory', {method:'POST',body:JSON.stringify({text:form.elements.text.value,domain:form.elements.domain.value,purposes:[form.elements.purpose.value]})});
      form.elements.text.value = '';await load();
    } catch (error) {status.textContent = error.message;}
    finally {submit.disabled = false;}
  });
  window.ApexScopedMemory = {load};
})();
