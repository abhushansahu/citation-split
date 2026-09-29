#!/bin/zsh
# Route all Mac audio through the split: lows to the Bluetooth speaker,
# highs to the MacBook speakers. Ctrl-C (or ./stop.sh) restores normal output.
cd "$(dirname "$0")"
BT=$(python3 -c 'import json;print(json.load(open("config.json"))["bt_device"])')
ADDR=$(python3 -c 'import json,os;c=json.load(open("config.json"));c.update(json.load(open("config.local.json")) if os.path.exists("config.local.json") else {});print(c["bt_address"])')
MAC=$(python3 -c 'import json;print(json.load(open("config.json"))["mac_device"])')
IN=$(python3 -c 'import json;print(json.load(open("config.json"))["input_device"])')
VOL=${1:-85}   # ./start.sh 60  -> both physical outputs at 60 %

[ -f latency.json ] || { echo "not calibrated yet: run ./setup.sh"; exit 1; }
launchctl print gui/$(id -u)/com.abhushan.citation-split >/dev/null 2>&1 && { echo "the background service is installed; use ./on.sh / ./off.sh instead (or ./service.sh uninstall)"; exit 1; }
blueutil --connect "$ADDR" >/dev/null 2>&1 || true
for i in {1..10}; do SwitchAudioSource -a -t output | grep -q "$BT" && break; sleep 1; done
SwitchAudioSource -a -t output | grep -q "$BT" || { echo "'$BT' not connected over Bluetooth"; exit 1; }

PREV=$(SwitchAudioSource -c -t output)
restore() { echo; echo "restoring output to $MAC"; SwitchAudioSource -s "$MAC" -t output >/dev/null; }
trap restore EXIT INT TERM

# Physical device volumes are set here; the volume keys do nothing while
# BlackHole is the system output. Re-run ./start.sh N or use ./vol.sh N.
SwitchAudioSource -s "$BT"  -t output >/dev/null; osascript -e "set volume output volume $VOL"
SwitchAudioSource -s "$MAC" -t output >/dev/null; osascript -e "set volume output volume $VOL"
SwitchAudioSource -s "$IN" -t output >/dev/null
SwitchAudioSource -s "$IN" -t output >/dev/null
osascript -e "set volume output volume 100"   # BlackHole itself at unity

echo "system output -> BlackHole -> split (was: $PREV). Volumes at $VOL%. Ctrl-C to stop."
./venv/bin/python split.py
