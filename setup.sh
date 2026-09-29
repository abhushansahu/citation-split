#!/bin/zsh
# One-time setup. Safe to re-run. Needs Homebrew.
set -e
cd "$(dirname "$0")"

echo "== 1/4 Homebrew packages"
brew list --cask blackhole-2ch >/dev/null 2>&1 || brew install --cask blackhole-2ch   # asks for your password (installs an audio driver)
brew list switchaudio-osx >/dev/null 2>&1 || brew install switchaudio-osx
brew list blueutil        >/dev/null 2>&1 || brew install blueutil

echo "== 2/4 Python venv"
[ -x venv/bin/python ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt

echo "== 3/4 Checking devices"
BT=$(python3 -c 'import json;print(json.load(open("config.json"))["bt_device"])')
ADDR=$(python3 -c 'import json,os;c=json.load(open("config.json"));c.update(json.load(open("config.local.json")) if os.path.exists("config.local.json") else {});print(c["bt_address"])')
blueutil --connect "$ADDR" 2>/dev/null || true
sleep 2
if ! SwitchAudioSource -a -t output | grep -q "BlackHole 2ch"; then
  echo "   BlackHole installed but CoreAudio hasn't loaded it yet; restarting CoreAudio (asks for your password)"
  sudo killall coreaudiod; sleep 3
fi
SwitchAudioSource -a -t output | grep -q "BlackHole 2ch" || { echo "BlackHole still not visible. Reboot, then re-run ./setup.sh"; exit 1; }
SwitchAudioSource -a -t output | grep -q "$BT" || { echo "'$BT' is not connected over Bluetooth. Pair it in System Settings > Bluetooth, then re-run."; exit 1; }
echo "   BlackHole 2ch: ok    $BT: connected"

echo "== 4/4 Calibrating Bluetooth delay (plays a few short beeps, listens on the Mac mic)"
echo "   Put the Mac within about 1 m of the speaker and keep the room quiet."
./venv/bin/python calibrate.py

echo
echo "Setup complete. Run ./start.sh to use the speaker."
