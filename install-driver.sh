#!/bin/zsh
# Install (or update) the Citation Split virtual device. Run with sudo.
set -e
cd "$(dirname "$0")"
[ "$(id -u)" = "0" ] || { echo "run as: sudo ./install-driver.sh"; exit 1; }
[ -d driver/CitationSplit.driver ] || { echo "build first: ./build-driver.sh"; exit 1; }
rm -rf /Library/Audio/Plug-Ins/HAL/CitationSplit.driver
cp -R driver/CitationSplit.driver /Library/Audio/Plug-Ins/HAL/
chown -R root:wheel /Library/Audio/Plug-Ins/HAL/CitationSplit.driver
killall coreaudiod; sleep 3
if system_profiler SPAudioDataType 2>/dev/null | grep -q "Citation Split"; then echo "installed: 'Citation Split' device is live"; else echo "driver copied but not visible yet; log out/in or reboot"; fi
