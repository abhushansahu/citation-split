#!/bin/zsh
# Build app/CitationSplit.app, the launcher the service runs through (so macOS grants it Microphone access).
set -e; cd "$(dirname "$0")"
B=app/CitationSplit.app/Contents; rm -rf app/CitationSplit.app; mkdir -p "$B/MacOS"
clang -O2 -o "$B/MacOS/CitationSplit" app/launcher.c
clang -O2 -o app/setvol app/setvol.c -framework CoreAudio -framework CoreFoundation -framework AudioToolbox
cp app/Info.plist "$B/Info.plist"
codesign --force --sign - app/CitationSplit.app
echo "built app/CitationSplit.app"
