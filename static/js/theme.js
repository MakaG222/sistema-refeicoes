/* Theme toggle — persistência em localStorage, CSP-safe (`script-src 'self'`).
 *
 * Como funciona:
 *   - Chave localStorage: "theme" ∈ {"light","dark"}. Ausente = seguir SO (prefers-color-scheme).
 *   - Aplica `data-theme` ao <html> no load (antes do primeiro paint se possível).
 *   - Toggle: botão com `[data-theme-toggle]` alterna light ↔ dark e grava.
 *   - Expõe `window.setTheme(mode)` e `window.getTheme()` para testes/consumidores.
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'theme';
  var VALID = { light: 1, dark: 1 };

  function getStored() {
    try {
      var v = localStorage.getItem(STORAGE_KEY);
      return VALID[v] ? v : null;
    } catch (_) { return null; }
  }

  function setStored(mode) {
    try {
      if (mode === null) localStorage.removeItem(STORAGE_KEY);
      else if (VALID[mode]) localStorage.setItem(STORAGE_KEY, mode);
    } catch (_) { /* ignore quota / private mode */ }
  }

  function applyTheme(mode) {
    var root = document.documentElement;
    if (VALID[mode]) root.setAttribute('data-theme', mode);
    else root.removeAttribute('data-theme');
    updateToggleLabel();
  }

  function currentTheme() {
    var attr = document.documentElement.getAttribute('data-theme');
    if (VALID[attr]) return attr;
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      return 'dark';
    }
    return 'light';
  }

  function toggleTheme() {
    var next = currentTheme() === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setStored(next);
  }

  function updateToggleLabel() {
    var btn = document.querySelector('[data-theme-toggle]');
    if (!btn) return;
    var isDark = currentTheme() === 'dark';
    // Mostra o ícone do tema futuro (o que vai aplicar ao clicar).
    btn.textContent = isDark ? '☀️' : '🌙';
    btn.setAttribute('aria-label', isDark ? 'Ativar modo claro' : 'Ativar modo escuro');
    btn.setAttribute('aria-pressed', isDark ? 'true' : 'false');
  }

  // Aplicar tema guardado antes do primeiro paint (reduz flash).
  applyTheme(getStored());

  function init() {
    var btn = document.querySelector('[data-theme-toggle]');
    if (btn) btn.addEventListener('click', toggleTheme);
    updateToggleLabel();

    // Se user não escolheu, seguir OS em tempo real.
    if (window.matchMedia) {
      var mq = window.matchMedia('(prefers-color-scheme: dark)');
      var listener = function () { if (!getStored()) updateToggleLabel(); };
      if (mq.addEventListener) mq.addEventListener('change', listener);
      else if (mq.addListener) mq.addListener(listener);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.setTheme = function (mode) {
    applyTheme(mode);
    setStored(VALID[mode] ? mode : null);
  };
  window.getTheme = currentTheme;
})();
