/* ── Meal Editor (aluno/editar) — event delegation, zero inline JS ── */

(function() {
  'use strict';

  function updateEstufaVisibility(meal) {
    var h = document.getElementById('h_' + meal);
    var estufa = document.getElementById(meal === 'almoco' ? 'alm_estufa_row' : 'jan_estufa_row');
    if (!estufa) return;
    if (h.value) {
      estufa.style.display = 'flex';
    } else {
      estufa.style.display = 'none';
      var he = document.getElementById('h_' + meal + '_estufa');
      var mark = document.getElementById(meal === 'almoco' ? 'alm_estufa_mark' : 'jan_estufa_mark');
      if (he) he.value = '0';
      if (mark) { mark.textContent = ''; mark.style.background = ''; mark.style.borderColor = ''; mark.style.color = ''; }
    }
  }

  function syncJantar() {
    var antes = document.querySelector('input[name=licenca][value=antes_jantar]');
    var jr = document.getElementById('jan_row');
    var jer = document.getElementById('jan_estufa_row');
    if (!jr) return;
    if (antes && antes.checked) {
      jr.style.opacity = '.4'; jr.style.pointerEvents = 'none';
      document.getElementById('h_jantar').value = '';
      jr.querySelectorAll('.sw-pill').forEach(function(p) { p.classList.remove('sw-sel'); });
      jr.classList.remove('sw-on');
      if (jer) jer.style.display = 'none';
    } else {
      jr.style.opacity = '1'; jr.style.pointerEvents = 'auto';
      updateEstufaVisibility('jantar');
    }
  }

  document.addEventListener('click', function(e) {
    // Toggle meal (PA, Lanche)
    var toggle = e.target.closest('[data-toggle-meal]');
    if (toggle) {
      var key = toggle.getAttribute('data-meal');
      var h = document.getElementById('h_' + key);
      var on = h.value === '1' || h.value === 'on';
      h.value = on ? '0' : '1';
      toggle.classList.toggle('sw-on', !on);
      toggle.querySelector('.sw-mark').textContent = on ? '' : '\u2713';
      return;
    }

    // Pill selector (Almoço/Jantar tipo)
    var pill = e.target.closest('[data-pill-meal]');
    if (pill) {
      var meal = pill.getAttribute('data-pill-meal');
      var val = pill.getAttribute('data-pill-val');
      var h2 = document.getElementById('h_' + meal);
      var row = pill.closest('.sw-pill-row');
      var pills = row.querySelectorAll('.sw-pill');
      if (h2.value === val) {
        h2.value = '';
        pills.forEach(function(p) { p.classList.remove('sw-sel'); });
        row.classList.remove('sw-on');
      } else {
        h2.value = val;
        pills.forEach(function(p) { p.classList.remove('sw-sel'); });
        pill.classList.add('sw-sel');
        row.classList.add('sw-on');
      }
      updateEstufaVisibility(meal);
      return;
    }

    // Estufa toggle
    var est = e.target.closest('[data-estufa]');
    if (est) {
      var emeal = est.getAttribute('data-estufa');
      var he = document.getElementById('h_' + emeal + '_estufa');
      var on2 = he.value === '1';
      he.value = on2 ? '0' : '1';
      if (!on2) {
        est.textContent = '\u2713'; est.style.background = '#f39c12'; est.style.borderColor = '#f39c12'; est.style.color = '#fff';
      } else {
        est.textContent = ''; est.style.background = ''; est.style.borderColor = ''; est.style.color = '';
      }
    }
  });

  // Licença radio: highlight active + block jantar se antes_jantar
  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('[data-lic] input[type=radio]').forEach(function(r) {
      r.addEventListener('change', function() {
        document.querySelectorAll('[data-lic]').forEach(function(l) { l.classList.remove('sw-on'); });
        r.closest('[data-lic]').classList.add('sw-on');
        syncJantar();
      });
    });
  });
})();
