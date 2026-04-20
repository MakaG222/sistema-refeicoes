/* Service Worker registration — CSP-safe (sem inline).
 * Best-effort: se falhar, PWA/offline desliga mas a app continua normal.
 */
(function () {
  'use strict';
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', function () {
    navigator.serviceWorker
      .register('/static/sw.js', { scope: '/' })
      .catch(function () { /* sem rede ou browser antigo — ignorar */ });
  });
})();
