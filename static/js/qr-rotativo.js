/* ── QR Rotativo: poll periódico ao backend para gerar novo token + QR ──
   CSP-safe (nada de inline). Só precisa de fetch + DOM API. */
(function () {
  'use strict';

  var root = document.querySelector('[data-qr-rotativo]');
  if (!root) return;

  var frame = document.getElementById('qrFrame');
  var status = document.getElementById('qrStatus');
  var countEl = document.getElementById('qrCountdown');
  var urlEl = document.getElementById('qrUrl');
  var modeBtns = root.querySelectorAll('.btn-mode');

  var modeAtual = root.dataset.tipoInicial || 'auto';
  var refreshSec = 45;     // será sobreposto pelo response
  var ttlSec = 60;         // idem
  var nextRefreshAt = 0;
  var pollTimer = null;
  var countTimer = null;

  function setMode(novo) {
    if (modeAtual === novo) return;
    modeAtual = novo;
    modeBtns.forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.mode === novo ? 'true' : 'false');
    });
    refreshNow();
  }

  modeBtns.forEach(function (btn) {
    btn.addEventListener('click', function () { setMode(btn.dataset.mode); });
  });

  function setStatus(msg, level) {
    if (!status) return;
    status.textContent = msg || '';
    status.className = 'qr-status' + (level ? ' qr-status-' + level : '');
  }

  function renderSvg(svg, url) {
    if (frame) frame.innerHTML = svg;
    if (urlEl) urlEl.textContent = url;
  }

  function fetchToken() {
    return fetch('/operations/qr-rotativo/token?tipo=' + encodeURIComponent(modeAtual), {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
    }).then(function (r) {
      if (!r.ok) throw new Error('http_' + r.status);
      return r.json();
    });
  }

  function refreshNow() {
    setStatus('A gerar novo QR…', 'info');
    fetchToken()
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        renderSvg(data.svg, data.url);
        if (typeof data.ttl_seconds === 'number') ttlSec = data.ttl_seconds;
        // Refresh ligeiramente antes do TTL expirar (margem de 15s).
        refreshSec = Math.max(15, ttlSec - 15);
        nextRefreshAt = Date.now() + refreshSec * 1000;
        setStatus('QR pronto — válido ~' + ttlSec + 's', 'ok');
      })
      .catch(function (err) {
        setStatus('Erro a gerar QR: ' + err.message + ' (a tentar novamente)', 'error');
        nextRefreshAt = Date.now() + 5000;
      });
  }

  function tickCountdown() {
    var remaining = Math.max(0, Math.ceil((nextRefreshAt - Date.now()) / 1000));
    if (countEl) countEl.textContent = remaining;
    if (remaining <= 0) {
      refreshNow();
    }
  }

  function start() {
    refreshNow();
    countTimer = setInterval(tickCountdown, 1000);
  }

  // Quando aba volta a foco: refresh imediato (token pode ter expirado)
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && Date.now() >= nextRefreshAt - 1000) refreshNow();
  });

  // Inicia
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
