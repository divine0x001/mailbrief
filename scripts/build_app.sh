#!/usr/bin/env bash
# Compile l'app barre de menu et assemble MailBrief.app
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$PROJECT/swift/MailBriefApp/main.swift"
OUT="$PROJECT/build"
APP="$OUT/MailBrief.app"

command -v swiftc >/dev/null || {
  echo "swiftc introuvable : installe les Command Line Tools (xcode-select --install)" >&2
  exit 1
}

echo "→ compilation de $SRC"
mkdir -p "$OUT"
# On détruit d'abord l'ancien bundle : si la compilation échoue, il ne
# reste aucun binaire périmé à lancer par erreur.
rm -rf "$APP"
xcrun swiftc -O "$SRC" -o "$OUT/MailBrief" \
  -framework AppKit \
  2>&1 | sed 's/^/  /'

echo "→ assemblage de MailBrief.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
mv "$OUT/MailBrief" "$APP/Contents/MacOS/MailBrief"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>MailBrief</string>
  <key>CFBundleDisplayName</key>     <string>MailBrief</string>
  <key>CFBundleIdentifier</key>      <string>com.mailbrief.app</string>
  <key>CFBundleVersion</key>         <string>1.0</string>
  <key>CFBundleShortVersionString</key> <string>1.0</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleExecutable</key>      <string>MailBrief</string>
  <key>LSMinimumSystemVersion</key>  <string>13.0</string>
  <key>LSUIElement</key>             <true/>
  <key>NSHighResolutionCapable</key> <true/>
  <key>MailBriefProjectRoot</key>    <string>$PROJECT</string>
</dict>
</plist>
EOF

echo "→ signature ad-hoc locale"
codesign --force --sign - "$APP" 2>&1 | sed 's/^/  /' || \
  echo "  (signature ignorée — binaire local, ça reste exécutable)"

# Installation : c'est la copie de ~/Applications qui tourne réellement,
# il faut donc la remplacer et redémarrer l'agent, sinon on garde l'ancien.
INSTALLED="$HOME/Applications/MailBrief.app"
echo "→ installation dans $INSTALLED"
mkdir -p "$HOME/Applications"
rm -rf "$INSTALLED"
cp -R "$APP" "$INSTALLED"
chmod +x "$INSTALLED/Contents/MacOS/MailBrief"

if launchctl print "gui/$(id -u)/com.mailbrief.app" >/dev/null 2>&1; then
  echo "→ redémarrage de l'agent com.mailbrief.app"
  launchctl kickstart -k "gui/$(id -u)/com.mailbrief.app" 2>/dev/null || true
else
  echo "→ agent non chargé, démarrage direct"
  "$INSTALLED/Contents/MacOS/MailBrief" >/dev/null 2>&1 &
fi
sleep 2
if pgrep -f "MailBrief.app/Contents/MacOS/MailBrief" >/dev/null; then
  echo "  app en cours ✅"
else
  echo "  ⚠️ l'app ne semble pas avoir démarré" >&2
fi

echo "✅ $APP"
echo "✅ $INSTALLED"
