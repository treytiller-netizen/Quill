#!/bin/bash
# Build Quill.app into /Applications (falls back to ~/Applications).
#
# - The Python environment lives at ~/.quill/venv, OUTSIDE iCloud-synced
#   Documents: iCloud's "Optimize Mac Storage" evicts big rarely-read files
#   and corrupts venvs with "name 2.ext" conflict copies.
# - The bundle path and identifier stay stable so macOS permission grants
#   (Microphone, Accessibility, Input Monitoring) survive rebuilds.
# - Installs a watchdog LaunchAgent that relaunches Quill if it dies
#   unexpectedly (sleep, crash) but respects an intentional Quit, and
#   registers Quill as a login item.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE_ID="com.treytiller.quill"
export UV_PROJECT_ENVIRONMENT="$HOME/.quill/venv"
PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"

APP_PARENT="/Applications"
[ -w "$APP_PARENT" ] || APP_PARENT="$HOME/Applications"
mkdir -p "$APP_PARENT" "$HOME/.quill"
APP="$APP_PARENT/Quill.app"

echo "→ Syncing Python environment to $UV_PROJECT_ENVIRONMENT"
uv sync --project "$PROJECT_DIR" --python 3.12

echo "→ Rendering app icon"
uv run --project "$PROJECT_DIR" python "$PROJECT_DIR/scripts/make_icon.py" "$PROJECT_DIR/build"

echo "→ Assembling $APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>Quill</string>
    <key>CFBundleDisplayName</key>       <string>Quill</string>
    <key>CFBundleIdentifier</key>        <string>${BUNDLE_ID}</string>
    <key>CFBundleExecutable</key>        <string>Quill</string>
    <key>CFBundleIconFile</key>          <string>AppIcon</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.3.0</string>
    <key>CFBundleVersion</key>           <string>0.3.0</string>
    <key>LSMinimumSystemVersion</key>    <string>13.0</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>Quill records your voice while you hold the dictation key so it can transcribe what you say.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/Quill" <<LAUNCHER
#!/bin/bash
# Load secrets for GUI launches (Dock apps don't inherit shell env).
set -a
[ -f "\$HOME/.quill/env" ] && source "\$HOME/.quill/env"
set +a
exec "$PYTHON" -m quill
LAUNCHER
chmod +x "$APP/Contents/MacOS/Quill"

cat > "$APP/Contents/Resources/watchdog.sh" <<'WATCHDOG'
#!/bin/bash
# Revive Quill if it should be running but isn't (crash, sleep-kill).
# ~/.quill/should_run exists while Quill runs; an intentional Quit removes it.
[ -f "$HOME/.quill/should_run" ] || exit 0
/usr/bin/pgrep -f "python( .*)? -m quill" >/dev/null && exit 0
exec /usr/bin/open -g /Applications/Quill.app
WATCHDOG
chmod +x "$APP/Contents/Resources/watchdog.sh"

cp "$PROJECT_DIR/build/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

# Ad-hoc signature keeps TCC (permission) attribution stable across rebuilds.
codesign --force --sign - "$APP" 2>/dev/null || true
touch "$APP"

echo "→ Installing watchdog LaunchAgent"
PLIST_PATH="$HOME/Library/LaunchAgents/${BUNDLE_ID}.watchdog.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>               <string>${BUNDLE_ID}.watchdog</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>${APP}/Contents/Resources/watchdog.sh</string></array>
    <key>StartInterval</key>       <integer>60</integer>
    <key>RunAtLoad</key>           <true/>
    <key>AssociatedBundleIdentifiers</key> <string>${BUNDLE_ID}</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/${BUNDLE_ID}.watchdog" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

echo "→ Registering Quill as a login item"
osascript -e 'tell application "System Events" to if not (exists login item "Quill") then make login item at end with properties {path:"/Applications/Quill.app", hidden:false}' >/dev/null

echo "✓ Built $APP (env: ~/.quill/venv, watchdog + login item installed)"
