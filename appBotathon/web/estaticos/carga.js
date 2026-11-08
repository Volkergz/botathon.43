// Actualiza los pasos del bot mientras la carga está en curso.
(function () {
  const lista = document.getElementById('pasos');
  if (!lista || lista.dataset.terminada === '1') return;
  const estado = document.getElementById('estado');

  function pintar(datos) {
    if (datos.pasos.length) {
      lista.replaceChildren(...datos.pasos.map(p => {
        const li = document.createElement('li');
        if (p.estado === 'ERROR') li.className = 'mal';
        const marca = document.createElement('span');
        marca.setAttribute('aria-hidden', 'true');
        marca.textContent = p.estado === 'ERROR' ? '✕' : '✓';
        li.append(marca, ' ' + p.paso + (p.filas ? ` (${p.filas} ${p.filas === '1' ? 'fila' : 'filas'})` : ''));
        return li;
      }));
    }
    estado.textContent = datos.rotulo;
  }

  async function revisar() {
    try {
      const r = await fetch(`/api/cargas/${lista.dataset.carga}`, { credentials: 'same-origin' });
      if (r.status === 401) { location.href = '/login'; return; }
      if (r.ok) {
        const datos = await r.json();
        pintar(datos);
        if (datos.terminada) { location.reload(); return; }
      }
    } catch (e) { /* se reintenta en el siguiente ciclo */ }
    setTimeout(revisar, 1500);
  }
  setTimeout(revisar, 800);
})();
