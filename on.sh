#!/bin/zsh
# Send Mac audio to the speaker setup (service must be installed). Optional volumes: ./on.sh 85 60
cd "$(dirname "$0")"
IN=$(python3 -c 'import json;print(json.load(open("config.json"))["input_device"])')
ADDR=$(python3 -c 'import json,os;c=json.load(open("config.json"));c.update(json.load(open("config.local.json")) if os.path.exists("config.local.json") else {});print(c["bt_address"])')
blueutil --connect "$ADDR" >/dev/null 2>&1 || true
[ -n "$1" ] && ./vol.sh "$1" "${2:-$1}"
SwitchAudioSource -s "$IN" -t output >/dev/null && osascript -e "set volume output volume 100" && echo "output -> $IN"
