#!/usr/bin/env bash
# scripts/smoke_test.sh — Validação automatizada pós-deploy.
#
# Corre em <30s. Verifica que o sistema está OPERACIONAL após deploy:
#   1. /health responde 200 com status=ok
#   2. /login responde 200 com CSRF token
#   3. /api/* exige auth (sem token → 403)
#   4. Security headers presentes (CSP, X-Frame-Options, HSTS em prod)
#   5. /metrics endpoint NÃO existe (foi removido — regressão check)
#   6. Static assets servidos correctamente
#
# Uso:
#   ./scripts/smoke_test.sh http://localhost:8080
#   ./scripts/smoke_test.sh https://refeicoes.exemplo.pt
#   BASE_URL=https://staging.refeicoes.pt ./scripts/smoke_test.sh
#
# Exit code 0 = tudo OK; >0 = pelo menos uma check falhou.

set -uo pipefail

# Cores
if [[ -t 1 && "${NO_COLOR:-}" != "1" ]]; then
  R='\033[0;31m'; G='\033[0;32m'; Y='\033[0;33m'; B='\033[0;34m'; X='\033[0m'
else
  R=''; G=''; Y=''; B=''; X=''
fi

BASE_URL="${1:-${BASE_URL:-http://localhost:8080}}"
BASE_URL="${BASE_URL%/}"  # strip trailing /

PASS=0
FAIL=0
WARN=0

check() {
  local name="$1"; shift
  if "$@" >/tmp/smoke.out 2>&1; then
    printf "${G}✓${X} %s\n" "$name"
    PASS=$((PASS + 1))
  else
    printf "${R}✗${X} %s\n" "$name"
    sed 's/^/    /' /tmp/smoke.out
    FAIL=$((FAIL + 1))
  fi
}

warn_check() {
  local name="$1"; shift
  if "$@" >/tmp/smoke.out 2>&1; then
    printf "${G}✓${X} %s\n" "$name"
    PASS=$((PASS + 1))
  else
    printf "${Y}⚠${X} %s (não bloqueante)\n" "$name"
    WARN=$((WARN + 1))
  fi
}

printf "${B}═══════════════════════════════════════════════════${X}\n"
printf "${B}Smoke test: ${BASE_URL}${X}\n"
printf "${B}═══════════════════════════════════════════════════${X}\n\n"

# ── 1. Health endpoint ───────────────────────────────────────────────────────
check "GET /health → 200 + status=ok" bash -c "
  resp=\$(curl -fsS '${BASE_URL}/health' 2>&1)
  # Tolerante a variações de whitespace em \"status\":\"ok\"
  echo \"\$resp\" | grep -qE '\"status\"[[:space:]]*:[[:space:]]*\"ok\"' || {
    echo 'health response:'; echo \"\$resp\"; exit 1;
  }
"

# ── 2. Login page renders + CSRF token ───────────────────────────────────────
check "GET /login → 200 + CSRF token presente" bash -c "
  resp=\$(curl -fsS '${BASE_URL}/login' 2>&1)
  echo \"\$resp\" | grep -q 'csrf_token' || {
    echo 'sem csrf_token no body';
    echo \"\$resp\" | head -50;
    exit 1;
  }
"

# ── 3. API endpoints exigem auth ─────────────────────────────────────────────
for endpoint in /api/backup-cron /api/autopreencher-cron /api/unlock-expired; do
  check "POST ${endpoint} sem token → 403" bash -c "
    code=\$(curl -s -o /dev/null -w '%{http_code}' -X POST '${BASE_URL}${endpoint}')
    [[ \"\$code\" == '403' ]] || { echo \"got HTTP \$code, esperava 403\"; exit 1; }
  "
done

# ── 4. Security headers ──────────────────────────────────────────────────────
check "Header X-Frame-Options presente" bash -c "
  curl -sI '${BASE_URL}/health' | grep -qi 'X-Frame-Options:'
"

check "Header Content-Security-Policy presente" bash -c "
  curl -sI '${BASE_URL}/health' | grep -qi 'Content-Security-Policy:'
"

check "Header X-Content-Type-Options: nosniff" bash -c "
  curl -sI '${BASE_URL}/health' | grep -qi 'X-Content-Type-Options:.*nosniff'
"

# HSTS só em produção (HTTPS) — warn-check em vez de hard-check
if [[ "$BASE_URL" == https://* ]]; then
  check "Header Strict-Transport-Security (HSTS) presente em HTTPS" bash -c "
    curl -sI '${BASE_URL}/health' | grep -qi 'Strict-Transport-Security:.*max-age='
  "
else
  printf "${Y}⚠${X} HSTS não verificado (BASE_URL não é HTTPS — esperado em dev)\n"
  WARN=$((WARN + 1))
fi

# ── 5. /metrics endpoint NÃO deve existir (foi removido) ─────────────────────
check "/metrics NÃO existe (regressão — Prometheus removido)" bash -c "
  code=\$(curl -s -o /dev/null -w '%{http_code}' '${BASE_URL}/metrics')
  # 404 ou 400 (CSRF middleware) = esperado. 200 = endpoint não foi removido!
  [[ \"\$code\" != '200' ]] || { echo \"endpoint /metrics ainda responde 200 — regressão\"; exit 1; }
"

# ── 6. Static assets ─────────────────────────────────────────────────────────
check "GET /static/css/app.css → 200" bash -c "
  code=\$(curl -s -o /dev/null -w '%{http_code}' '${BASE_URL}/static/css/app.css')
  [[ \"\$code\" == '200' ]] || { echo \"got HTTP \$code\"; exit 1; }
"

# ── 7. HTTPS redirect (se configurado) ───────────────────────────────────────
if [[ "$BASE_URL" == http://* && "${SKIP_HTTPS_CHECK:-}" != "1" ]]; then
  warn_check "HTTPS redirect (X-Forwarded-Proto: http → 301)" bash -c "
    code=\$(curl -s -o /dev/null -w '%{http_code}' \
      -H 'X-Forwarded-Proto: http' \
      '${BASE_URL}/login')
    # Em produção: 301. Em dev sem ENV=production: 200 (normal)
    [[ \"\$code\" == '301' ]] || { echo \"got HTTP \$code (esperado 301 em prod)\"; exit 1; }
  "
fi

# ── Summary ──────────────────────────────────────────────────────────────────
printf "\n${B}═══════════════════════════════════════════════════${X}\n"
if [[ $FAIL -eq 0 ]]; then
  printf "${G}✓ SMOKE TEST OK${X} — %d passed, %d warnings\n" "$PASS" "$WARN"
  printf "${B}═══════════════════════════════════════════════════${X}\n\n"
  cat <<'NEXT'
Próximos passos manuais (não automatizáveis):
  □ Login com admin real → confirmar dashboard carrega
  □ Marcar uma refeição → confirmar persistiu na BD
  □ Aluno descarrega .ics → confirmar importável em Calendar
  □ Backup manual: docker compose exec app flask backup
  □ Restore test num clone da BD (ver RUNBOOK.md § Teste de restore)
NEXT
  exit 0
else
  printf "${R}✗ SMOKE TEST FALHOU${X} — %d passed, %d FAILED, %d warnings\n" "$PASS" "$FAIL" "$WARN"
  printf "${R}═══════════════════════════════════════════════════${X}\n"
  cat <<NEXT

Investigar logs:
  docker compose logs --tail=100 app
  # ou
  curl -s ${BASE_URL}/health | jq

Se for um deploy novo: dá-lhe ≥60s para arrancar e tenta de novo.
NEXT
  exit 1
fi
