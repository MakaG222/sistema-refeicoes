/* ── Tab switcher (admin/companhias) — event delegation ──────────── */
(function() {
  'use strict';

  function showTab(id) {
    ['turmas','atribuir','promocao','mover'].forEach(function(t) {
      var el = document.getElementById('tab-' + t);
      if (el) el.style.display = (t === id) ? '' : 'none';
    });
    document.querySelectorAll('.year-tab').forEach(function(el) {
      el.classList.toggle('active', el.getAttribute('href') === '#' + id);
    });
  }

  // Click on tab links
  document.addEventListener('click', function(e) {
    var tab = e.target.closest('.year-tab[href^="#"]');
    if (!tab) return;
    e.preventDefault();
    var id = tab.getAttribute('href').replace('#', '');
    showTab(id);
  });

  // Show tab by hash or first
  document.addEventListener('DOMContentLoaded', function() {
    var hash = window.location.hash.replace('#', '') || 'turmas';
    showTab(hash);
  });
})();
