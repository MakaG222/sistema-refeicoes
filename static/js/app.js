/* ── Global: data-confirm handler ────────────────────────────────── */
/* Replaces inline onclick="return confirm(...)" and onsubmit="return confirm(...)"
   Usage: <button data-confirm="Are you sure?">  or  <form data-confirm="..."> */
document.addEventListener('click', function(e) {
  var el = e.target.closest('[data-confirm]');
  if (!el) return;
  var msg = el.getAttribute('data-confirm');
  if (!confirm(msg)) {
    e.preventDefault();
    e.stopPropagation();
  }
});
document.addEventListener('submit', function(e) {
  var form = e.target.closest('form[data-confirm]');
  if (!form) return;
  var msg = form.getAttribute('data-confirm');
  if (!confirm(msg)) {
    e.preventDefault();
    e.stopPropagation();
  }
});

/* ── Global: data-href handler ──────────────────────────────────── */
/* Replaces inline onclick="window.location='...'"
   Usage: <div data-href="/some/path" style="cursor:pointer"> */
document.addEventListener('click', function(e) {
  var el = e.target.closest('[data-href]');
  if (!el) return;
  window.location = el.getAttribute('data-href');
});
