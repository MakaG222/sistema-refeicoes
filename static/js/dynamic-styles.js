/* Apply dynamic CSS from data attributes (CSP-safe: no inline style="") */
(function () {
  'use strict';
  /* Chart bars: data-h="30" → height:30px */
  document.querySelectorAll('[data-h]').forEach(function (el) {
    el.style.height = el.getAttribute('data-h') + 'px';
  });
  /* Occupancy bars: data-pct="75" → width:75% */
  document.querySelectorAll('[data-pct]').forEach(function (el) {
    el.style.width = el.getAttribute('data-pct') + '%';
  });
})();
