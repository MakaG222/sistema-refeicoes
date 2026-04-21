/* Toast system — CSP-safe (`script-src 'self'`), no inline handlers.
 *
 * Public API:
 *   window.showToast(text, level='info', opts={})   -> HTMLElement
 *   window.dismissToast(toastEl)
 *
 * Auto-reads flash messages injected as JSON scripts:
 *   <script type="application/json" data-toast>{"msg":"...","level":"ok"}</script>
 * Rendered by utils/helpers.py:flash_as_toast().
 *
 * Levels: 'ok' | 'error' | 'warn' | 'info' (default).
 * Default auto-hide: 5000ms. Pass opts.timeout=0 to disable auto-hide.
 */
(function () {
  'use strict';

  var ALLOWED_LEVELS = { ok: 1, error: 1, warn: 1, info: 1 };

  function ensureContainer() {
    var c = document.getElementById('toast-container');
    if (c) return c;
    c = document.createElement('div');
    c.id = 'toast-container';
    c.className = 'toast-container';
    c.setAttribute('role', 'region');
    c.setAttribute('aria-label', 'Notificações');
    c.setAttribute('aria-live', 'polite');
    c.setAttribute('aria-atomic', 'false');
    document.body.appendChild(c);
    return c;
  }

  function dismissToast(el) {
    if (!el || !el.parentNode) return;
    el.classList.add('is-leaving');
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 220);
  }

  function showToast(text, level, opts) {
    if (typeof text !== 'string' || !text) return null;
    level = ALLOWED_LEVELS[level] ? level : 'info';
    opts = opts || {};
    var timeout = typeof opts.timeout === 'number' ? opts.timeout : 5000;

    var container = ensureContainer();
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + level;
    toast.setAttribute('role', level === 'error' ? 'alert' : 'status');

    var msg = document.createElement('span');
    msg.className = 'toast-msg';
    msg.textContent = text; // textContent prevents HTML injection
    toast.appendChild(msg);

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast-close';
    close.setAttribute('aria-label', 'Fechar notificação');
    close.textContent = '×';
    close.addEventListener('click', function () { dismissToast(toast); });
    toast.appendChild(close);

    container.appendChild(toast);

    if (timeout > 0) {
      setTimeout(function () { dismissToast(toast); }, timeout);
    }
    return toast;
  }

  window.showToast = showToast;
  window.dismissToast = dismissToast;

  // Auto-consume <script type="application/json" data-toast> blocks on load
  function consumeFlashes() {
    var scripts = document.querySelectorAll('script[type="application/json"][data-toast]');
    for (var i = 0; i < scripts.length; i++) {
      try {
        var data = JSON.parse(scripts[i].textContent || '{}');
        if (data && data.msg) {
          showToast(data.msg, data.level || 'info');
        }
      } catch (_) { /* swallow parse errors */ }
      if (scripts[i].parentNode) scripts[i].parentNode.removeChild(scripts[i]);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', consumeFlashes);
  } else {
    consumeFlashes();
  }
})();
