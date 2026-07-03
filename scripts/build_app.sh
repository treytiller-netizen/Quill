#!/bin/bash
# Build Quill.app into /Applications (falls back to ~/Applications).
#
# The bundle is a thin launcher around this project's uv virtualenv. Keeping the
# bundle path and identifier stable means macOS permission grants (Microphone,
# Accessibility, Input Monitoring) survive rebuilds — you approve them once.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUNDLE_ID="com.treytiller.quill"

APP_PARENT="/Applications"
[ -w "$APP_PARENT" ] || APP_PARENT="$HOME/Applications"
mkdir -p "$APP_PARENT"
APP="$APP_PARENT/Quill.app"

echo "→ Syncing Python environment"
uv sync --project "$PROJECT_DIR" --python 3.12
PYTHON="$PROJECT_DIR/.venv/bin/python"

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
    <key>CFBundleShortVersionString</key><string>0.2.0</string>
    <key>CFBundleVersion</key>           <string>0.2.0</string>
    <key>LSMinimumSystemVersion</key>    <string>13.0</string>
    <key>LSUIElement</key>               <true/>
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

cp "$PROJECT_DIR/build/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

# Ad-hoc signature keeps TCC (permission) attribution stable across rebuilds.
codesign --force --sign - "$APP" 2>/dev/null || true
touch "$APP"

echo "✓ Built $APP"
echo "  Open it once, grant Microphone + Accessibility + Input Monitoring,"
echo "  then right-click the Dock icon → Options → Keep in Dock."
