#!/bin/zsh
# Apply config.json to the running split without restarting (filters rebuild, sync is kept).
cd "$(dirname "$0")"
python3 -c 'import json;json.load(open("config.json"))' || { echo "config.json is not valid JSON"; exit 1; }
pkill -USR2 -f "split.py" && echo "reloaded; watch the split terminal for the [reload] line" || echo "split.py is not running"
