#!/usr/bin/env bash
# Lance MailBrief. Utilisé par launchd/cron — chemin absolu, pas de dépendance tty.
set -euo pipefail

# Répertoire du projet, déduit de l'emplacement de ce script.
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT"

PYTHON="$PROJECT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  echo "[run] .venv introuvable : lance 'python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'" >&2
  exit 1
fi

mkdir -p "$PROJECT/data"
echo "==== $(date '+%Y-%m-%d %H:%M:%S') ===="
exec "$PYTHON" -m mailbrief "$@"
