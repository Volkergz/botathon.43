// Muestra el valor de cada barra al pasar el puntero. Los datos también están en tablas.
(function () {
  const tip = document.getElementById('tip');
  if (!tip) return;
  document.addEventListener('mousemove', e => {
    const t = e.target.closest('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip;
    tip.hidden = false;
    tip.style.left = Math.min(e.clientX + 12, innerWidth - 270) + 'px';
    tip.style.top = (e.clientY + 14) + 'px';
  });
})();
