#!/usr/bin/env bash
# Installe la planification quotidienne sous macOS (launchd).
# Usage : ./install_schedule.sh [heure] [minute]   (défaut : 08:00)
set -euo pipefail

HOUR="${1:-8}"
MINUTE="${2:-0}"
LABEL="com.mailbrief.daily"
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if ! [[ "$HOUR" =~ ^[0-9]+$ && "$MINUTE" =~ ^[0-9]+$ ]]; then
  echo "Usage : $0 [heure 0-23] [minute 0-59]" >&2
  exit 1
fi

chmod +x "$PROJECT/scripts/run.sh"
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data"

# Recharge proprement un éventuel service déjà installé.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT/scripts/run.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$PROJECT/data/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT/data/launchd.err.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
EOF

launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null \
  || launchctl load "$PLIST"

echo "✅ Planifié : $LABEL → tous les jours à $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
echo "   plist  : $PLIST"
echo "   logs   : $PROJECT/data/launchd.log"
echo
echo "Teste maintenant avec : $PROJECT/scripts/run.sh --dry-run"
