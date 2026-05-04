#!/usr/bin/env bash
# scripts/preflight.sh — Validação local antes de promover para staging/produção.
#
# Corre em ~3 minutos. Para cada gate, exit imediato se falhar (fail-fast).
# Inclui:
#   1. ruff check + format
#   2. bandit (security audit)
#   3. pip-audit (dependency vulnerabilities)
#   4. pytest full suite
#   5. docker build (catches Dockerfile/requirements issues)
#
# Uso:
#   ./scripts/preflight.sh           # todas as gates
#   SKIP_DOCKER=1 ./scripts/preflight.sh  # ignora docker build (mais rápido)
#
# Não toca em estado externo (sem network calls excepto pip-audit).
# Output coloured + resumo final.

set -euo pipefail

# Cores (NO_COLOR=1 desliga)
if [[ -t 1 && "${NO_COLOR:-}" != "1" ]]; then
  R='\033[0;31m'; G='\033[0;32m'; Y='\033[0;33m'; B='\033[0;34m'; X='\033[0m'
else
  R=''; G=''; Y=''; B=''; X=''
fi

step() { printf "\n${B}▶ %s${X}\n" "$1"; }
ok()   { printf "${G}✓ %s${X}\n" "$1"; }
fail() { printf "${R}✗ %s${X}\n" "$1" >&2; exit 1; }
warn() { printf "${Y}⚠ %s${X}\n" "$1"; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Activar venv se existir
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

START=$(date +%s)

# ── 1. ruff lint ─────────────────────────────────────────────────────────────
step "1/5 ruff check"
if ruff check . >/dev/null 2>&1; then
  ok "ruff check clean"
else
  ruff check . || true
  fail "ruff encontrou problemas — corrige antes de prosseguir"
fi

step "2/5 ruff format --check"
if ruff format --check . >/dev/null 2>&1; then
  ok "ruff format clean"
else
  warn "Ficheiros precisam de formatação — a aplicar:"
  ruff format .
  ok "Formatação aplicada (commita as mudanças)"
fi

# ── 3. bandit security audit ─────────────────────────────────────────────────
step "3/5 bandit (security)"
# Bandit retorna 0 se não há issues à severidade configurada (-ll = Low+).
# Não usar `-q` aqui: suprime a linha "No issues identified" e os warnings
# vão para stderr, o que torna grep frágil. Exit code é o sinal canónico.
if bandit -r core/ blueprints/ utils/ app.py -ll >/tmp/bandit.log 2>&1; then
  ok "bandit clean"
else
  echo "─── bandit output ───"
  cat /tmp/bandit.log
  echo "─── /bandit output ──"
  fail "bandit encontrou issues — revê antes de prosseguir"
fi

# ── 4. pip-audit (dependency vulns) ──────────────────────────────────────────
step "4/5 pip-audit (dependency vulnerabilities)"
if command -v pip-audit >/dev/null 2>&1; then
  # Audita o venv activo (mais fiável que --disable-pip + --no-deps com -r).
  # Exit 0 = sem vulns; >0 = encontrou vulns. Não bloqueante (warn-only) —
  # vulns em deps transitivas são comuns e nem sempre aplicáveis.
  if pip-audit --strict >/tmp/pip-audit.log 2>&1; then
    ok "pip-audit clean"
  else
    echo "─── pip-audit output ───"
    cat /tmp/pip-audit.log
    echo "─── /pip-audit output ──"
    warn "pip-audit encontrou vulnerabilidades — revê (não bloqueia preflight)"
  fi
else
  warn "pip-audit não instalado — instala com: pip install pip-audit"
fi

# ── 5. pytest full suite ─────────────────────────────────────────────────────
step "5/5 pytest (full suite)"
if python -m pytest tests/ -q --tb=no 2>&1 | tee /tmp/pytest.log | tail -1 | grep -qE "[0-9]+ passed"; then
  N=$(tail -1 /tmp/pytest.log | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+")
  ok "${N} testes passaram"
else
  tail -20 /tmp/pytest.log
  fail "pytest falhou — corrige antes de prosseguir"
fi

# ── 6. Docker build (skip-able) ──────────────────────────────────────────────
if [[ "${SKIP_DOCKER:-}" == "1" ]]; then
  warn "Docker build saltado (SKIP_DOCKER=1)"
else
  step "6/6 docker build (catches Dockerfile/deps issues)"
  if ! command -v docker >/dev/null 2>&1; then
    warn "Docker não instalado — saltado"
  else
    if docker build -t sistema-refeicoes:preflight . >/tmp/docker-build.log 2>&1; then
      SIZE=$(docker image inspect sistema-refeicoes:preflight --format '{{.Size}}' | awk '{print int($1/1024/1024)}')
      ok "Docker image build OK (${SIZE} MB)"
    else
      tail -30 /tmp/docker-build.log
      fail "docker build falhou — vê /tmp/docker-build.log"
    fi
  fi
fi

ELAPSED=$(($(date +%s) - START))
printf "\n${G}═══════════════════════════════════════════════════${X}\n"
printf "${G}✓ PREFLIGHT OK (%ds)${X} — pronto para promover\n" "$ELAPSED"
printf "${G}═══════════════════════════════════════════════════${X}\n\n"

cat <<'NEXT'
Próximos passos:
  1. git status               # confirmar working tree limpo
  2. git push                 # se ainda não pushed
  3. Deploy de staging:
       docker compose up -d --build
       # ou: railway up
  4. Após arranque (≥30s):
       ./scripts/smoke_test.sh http://staging.example.pt
NEXT
