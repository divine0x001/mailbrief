#!/usr/bin/env bash
# MailBrief — installation en une commande.
#   ./install.sh
# À la fin, il ne reste qu'à remplir .env et lancer ./install.sh --finish
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT"

FINISH=false
[[ "${1:-}" == "--finish" ]] && FINISH=true

hr() { printf '%s\n' "──────────────────────────────────────────────"; }
ok() { printf '  ✅ %s\n' "$1"; }
step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }

# ── 1. Python ──────────────────────────────────────────────────────────
step "Environnement Python"
if ! command -v python3 >/dev/null; then
  echo "  ❌ python3 introuvable. Installe-le : brew install python3" >&2
  exit 1
fi
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  ok "venv créé"
else
  ok "venv déjà présent"
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
ok "dépendances installées"

# ── 2. Configuration ───────────────────────────────────────────────────
step "Configuration"
if [[ ! -f .env ]]; then
  cp .env.example .env
  ok ".env créé depuis .env.example"
else
  ok ".env déjà présent (non écrasé)"
fi

# ── 3. Ollama ──────────────────────────────────────────────────────────
step "IA locale (Ollama)"
MODEL="$(grep '^OLLAMA_MODEL=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true)"
MODEL="${MODEL:-qwen3:8b}"
if command -v ollama >/dev/null; then
  ok "ollama installé"
  if curl -s --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
    ok "serveur Ollama en marche"
  else
    echo "  ⚠️  serveur arrêté — lance : ollama serve"
  fi
  # Note : grep -q ici couperait le pipe (SIGPIPE) et pipefail renverrait 141,
  # ce qui ferait croire à tort que le modèle est absent. On consomme tout.
  if ollama list 2>/dev/null | grep "^${MODEL}" >/dev/null; then
    ok "modèle ${MODEL} présent"
  else
    echo "  ⚠️  modèle absent — lance : ollama pull ${MODEL}"
  fi
else
  echo "  ⚠️  ollama absent — installe-le : https://ollama.com"
fi

# ── 4. Tests ───────────────────────────────────────────────────────────
step "Tests"
if .venv/bin/python -m unittest discover -s tests >/tmp/mailbrief-tests.log 2>&1; then
  ok "$(grep -oE 'Ran [0-9]+ tests' /tmp/mailbrief-tests.log | head -1)"
else
  echo "  ❌ tests en échec :" >&2
  tail -20 /tmp/mailbrief-tests.log >&2
  exit 1
fi

# ── 5. App macOS ───────────────────────────────────────────────────────
if [[ "$(uname -s)" == "Darwin" ]] && command -v xcrun >/dev/null; then
  step "App barre de menu (macOS)"
  if ./scripts/build_app.sh >/tmp/mailbrief-build.log 2>&1; then
    ok "app compilée, installée et démarrée"
  else
    echo "  ⚠️  compilation échouée — voir /tmp/mailbrief-build.log" >&2
  fi
fi

# ── 6. Fini ? ──────────────────────────────────────────────────────────
hr
if $FINISH; then
  echo "Diagnostic :"
  .venv/bin/python -m mailbrief --check || true
  hr
  echo "Tout est en place. Pour planifier le brief quotidien :"
  echo "    ./install_schedule.sh 8 0"
else
  echo "Il reste à remplir .env (identifiants mail + Telegram),"
  echo "puis relancer :  ./install.sh --finish"
fi
