#!/bin/bash
# STEP 2 (read-only): ask the bootloader about itself. Writes nothing.
set -u
LOG="$(dirname "$0")/logs/getvar-$(date +%Y%m%d-%H%M%S).txt"
echo "== fastboot devices =="
fastboot devices -l
echo
if [ -z "$(fastboot devices)" ]; then
  echo "fastboot sees nothing. Either the speaker is not in fastboot mode,"
  echo "or its interface class does not match FF/42/03 (run 01-identify.py)."
  echo "If 01 found the device but this does not, use 03-set-slot-a.py instead."
  exit 1
fi
echo "== fastboot getvar all  (logging to $LOG) =="
fastboot getvar all 2>&1 | tee "$LOG"
echo
echo "Saved: $LOG"
echo "Look for: current-slot, slot-count, slot-successful:a/b, slot-unbootable:a/b."
echo "If current-slot is ALREADY 'a', slot corruption is NOT your problem - stop here."
