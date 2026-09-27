#!/bin/zsh
# Build "Citation Split", a BlackHole-based virtual output device that REPORTS a
# fixed presentation latency (so video apps hold the picture back to match the
# Bluetooth speaker) without adding any delay itself. Needs only Command Line
# Tools (clang), not Xcode. Output: driver/CitationSplit.driver
set -e
cd "$(dirname "$0")"
TOTAL_MS=$(python3 -c 'import json;print(json.load(open("config.json")).get("total_latency_ms",400))')
FRAMES=$((TOTAL_MS*48))          # reported in frames at 48 kHz (the rate the split opens the device at)
SRC=blackhole-src/BlackHole/BlackHole.c
[ -f "$SRC" ] || git clone -q --depth 1 https://github.com/ExistentialAudio/BlackHole.git blackhole-src
mkdir -p driver/build
# Patch: the device's kAudioDevicePropertyLatency getter returns a hard-coded 0 upstream; make it a build constant.
python3 - "$SRC" driver/build/CitationSplit.c <<'PY'
import sys, re
src = open(sys.argv[1]).read()
i = src.index("case kAudioDevicePropertyLatency:\n\t\t\t//\tThis property returns the presentation latency of the device.")
j = src.index("*((UInt32*)outData) = 0;", i)
assert j - i < 600, "unexpected layout"
out = src[:j] + "*((UInt32*)outData) = kReported_Latency_Frames;" + src[j + len("*((UInt32*)outData) = 0;"):]
open(sys.argv[2], "w").write(out); print("patched device latency getter")
PY
echo "== compiling (reported latency ${TOTAL_MS} ms = ${FRAMES} frames)"
B=driver/CitationSplit.driver/Contents; rm -rf driver/CitationSplit.driver; mkdir -p "$B/MacOS" "$B/Resources"
clang -arch arm64 -O2 -bundle -o "$B/MacOS/CitationSplit" driver/build/CitationSplit.c \
  -framework CoreAudio -framework CoreFoundation -framework Accelerate \
  -DkReported_Latency_Frames=$FRAMES \
  -DkDriver_Name='"CitationSplit"' -DkDevice_Name='"Citation Split"' -DkDevice2_Name='"Citation Split Mirror"' \
  -DkPlugIn_BundleID='"audio.existential.CitationSplit"' -DkManufacturer_Name='"abhushan (BlackHole fork)"' \
  -DkSampleRates=48000 -DkDevice2_IsHidden=true 2>&1 | grep -v "warning:" || true
[ -x "$B/MacOS/CitationSplit" ] || { echo "compile failed"; exit 1; }
cat > "$B/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDevelopmentRegion</key><string>English</string>
  <key>CFBundleExecutable</key><string>CitationSplit</string>
  <key>CFBundleIdentifier</key><string>audio.existential.CitationSplit</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>CitationSplit</string>
  <key>CFBundlePackageType</key><string>BNDL</string>
  <key>CFBundleShortVersionString</key><string>0.7.1-cs${TOTAL_MS}</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFPlugInFactories</key><dict><key>e395c745-4eea-4d94-bb92-46224221047c</key><string>BlackHole_Create</string></dict>
  <key>CFPlugInTypes</key><dict><key>443ABAB8-E7B3-491A-B985-BEB9187030DB</key><array><string>e395c745-4eea-4d94-bb92-46224221047c</string></array></dict>
</dict></plist>
PLIST
codesign --force --sign - "driver/CitationSplit.driver" >/dev/null
codesign -dv driver/CitationSplit.driver 2>&1 | grep -E "Identifier|Signature" 
echo "== built driver/CitationSplit.driver (reports ${TOTAL_MS} ms). Install with: sudo ./install-driver.sh"
