#!/bin/zsh
# Re-measure the Bluetooth delay inside the running split (plays 4 short beep pairs).
pkill -USR1 -f "split.py" && echo "resync requested; watch the split terminal" || echo "split.py is not running"
