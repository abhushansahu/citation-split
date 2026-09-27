#!/bin/zsh
# Stop the split and put the MacBook speakers back as system output.
cd "$(dirname "$0")"
MAC=$(python3 -c 'import json;print(json.load(open("config.json"))["mac_device"])')
pkill -f "split.py" 2>/dev/null
SwitchAudioSource -s "$MAC" -t output >/dev/null && echo "output restored to $MAC"
