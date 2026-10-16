#!/bin/bash
set -e

APP="Token Tracker"
BUNDLE="TokenTracker.app"

echo "Building $APP (pure Swift, no Python)..."

swiftc main.swift \
    -framework Cocoa \
    -framework WebKit \
    -framework Network \
    -o "$APP"

# Bundle into .app
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS"
mkdir -p "$BUNDLE/Contents/Resources"

cp "$APP" "$BUNDLE/Contents/MacOS/$APP"
cp token-dashboard.html "$BUNDLE/Contents/Resources/token-dashboard.html" 2>/dev/null || true

cat > "$BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Token Tracker</string>
    <key>CFBundleDisplayName</key><string>Token Tracker</string>
    <key>CFBundleIdentifier</key><string>com.tokentracker.app</string>
    <key>CFBundleVersion</key><string>2.0</string>
    <key>CFBundleExecutable</key><string>Token Tracker</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

echo "Done — open $BUNDLE to launch"
