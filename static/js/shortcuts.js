/* Keyboard shortcuts — globais, CSP-safe (`script-src 'self'`).
 *
 * Registados:
 *   Ctrl/Cmd+S    → submit primeiro <form data-primary-form> da página
 *   Ctrl/Cmd+P    → window.print() em páginas com [data-printable]
 *   ?             → abre/fecha overlay de ajuda (<dialog id="shortcuts-help">)
 *   Esc           → fecha overlay de ajuda
 *
 * Nota: desactivado quando o utilizador está a escrever (input/textarea/contenteditable).
 */
(function () {
  'use strict';

  function isTyping(el) {
    if (!el) return false;
    var tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function openHelp() {
    var dlg = document.getElementById('shortcuts-help');
    if (!dlg) return;
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
  }

  function closeHelp() {
    var dlg = document.getElementById('shortcuts-help');
    if (!dlg) return;
    if (typeof dlg.close === 'function') dlg.close();
    else dlg.removeAttribute('open');
  }

  function toggleHelp() {
    var dlg = document.getElementById('shortcuts-help');
    if (!dlg) return;
    if (dlg.hasAttribute('open')) closeHelp();
    else openHelp();
  }

  document.addEventListener('keydown', function (e) {
    var mod = e.ctrlKey || e.metaKey;

    // Ctrl/Cmd+S — submit primary form
    if (mod && !e.shiftKey && !e.altKey && (e.key === 's' || e.key === 'S')) {
      var form = document.querySelector('form[data-primary-form]');
      if (form) {
        e.preventDefault();
        if (typeof form.requestSubmit === 'function') form.requestSubmit();
        else form.submit();
      }
      return;
    }

    // Ctrl/Cmd+P — print em páginas printable (deixa default do browser nas outras)
    if (mod && !e.shiftKey && !e.altKey && (e.key === 'p' || e.key === 'P')) {
      var printable = document.querySelector('[data-printable]');
      if (printable) {
        // Deixa o browser fazer o print dialog normalmente — não preventDefault.
        // Este hook existe só para consistência futura (podia chamar window.print()).
      }
      return;
    }

    // Esc — fecha ajuda
    if (e.key === 'Escape') {
      var dlgEsc = document.getElementById('shortcuts-help');
      if (dlgEsc && dlgEsc.hasAttribute('open')) {
        e.preventDefault();
        closeHelp();
      }
      return;
    }

    // ? — toggle ajuda (só se não estiver a escrever)
    if (e.key === '?' && !isTyping(e.target)) {
      e.preventDefault();
      toggleHelp();
      return;
    }
  });

  // Click no backdrop fecha o dialog
  document.addEventListener('click', function (e) {
    var dlg = document.getElementById('shortcuts-help');
    if (!dlg || !dlg.hasAttribute('open')) return;
    if (e.target === dlg) closeHelp();
    var closer = e.target.closest('[data-close-shortcuts]');
    if (closer) closeHelp();
  });

  window.openShortcutsHelp = openHelp;
  window.closeShortcutsHelp = closeHelp;
})();
