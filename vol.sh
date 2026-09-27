#!/bin/zsh
# ./vol.sh            -> show current volumes
# ./vol.sh 85 100     -> speaker 85 %, MacBook 100 % (also saved to config.json "volumes", applied on every activation)
cd "$(dirname "$0")"
BT=$(python3 -c 'import json;print(json.load(open("config.json"))["bt_device"])')
MAC=$(python3 -c 'import json;print(json.load(open("config.json"))["mac_device"])')
if [ -n "$1" ]; then
  ./app/setvol "$BT" "$1"; ./app/setvol "$MAC" "${2:-$1}"
  python3 -c "import json;c=json.load(open('config.json'));c['volumes']={'bt':$1,'mac':${2:-$1}};json.dump(c,open('config.json','w'),indent=2)"
  echo "(saved: the service will set these every time it activates)"
else
  ./app/setvol "$BT"; ./app/setvol "$MAC"
fi
