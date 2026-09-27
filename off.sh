#!/bin/zsh
# Back to the MacBook speakers (the service stays installed and goes idle by itself).
cd "$(dirname "$0")"
MAC=$(python3 -c 'import json;print(json.load(open("config.json"))["mac_device"])')
SwitchAudioSource -s "$MAC" -t output >/dev/null && echo "output -> $MAC"
