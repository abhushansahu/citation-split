#!/usr/bin/env python3
"""STEP 3: the actual fix, over raw pyusb -- for when the `fastboot` binary
cannot see the device because of an interface-class mismatch.

Unlike the script from the Reddit thread, this READS the reply to every
command, so you find out whether the bootloader accepted set_active or
silently ignored it. Flash-writing commands are hard-blocked in mtk.py.

  --dry-run   (default) probe read-only vars, change nothing
  --commit    send set_active:a, then reboot
"""
import sys, time
from mtk import find, command, FASTBOOT_PID

PROBE = ["getvar:current-slot", "getvar:slot-count", "getvar:has-slot:boot",
         "getvar:slot-successful:a", "getvar:slot-successful:b",
         "getvar:slot-unbootable:a", "getvar:slot-unbootable:b",
         "getvar:product", "getvar:version-bootloader", "getvar:serialno"]

def main():
    commit = "--commit" in sys.argv
    dev = find(pid=FASTBOOT_PID)
    if dev is None:
        print("Device 0e8d:201c not found. Run 01-identify.py --watch first.")
        return 1
    print(f"Found 0e8d:{FASTBOOT_PID:04x}\n")

    print("--- read-only probe ---")
    results = {}
    for cmd in PROBE:
        status, payload, _ = command(dev, cmd, quiet=True)
        results[cmd] = (status, payload)
        flag = {"OKAY": "ok", "FAIL": "unsupported", "TIMEOUT": "no reply"}.get(status, status)
        print(f"  {cmd:<34} {flag:<12} {payload.strip()}")

    slot = results.get("getvar:current-slot", ("", ""))[1].strip()
    if slot == "a":
        print("\n  NOTE: bootloader already reports current-slot = a.")
        print("  Switching slots will not help; your fault is probably not a bad OTA.")

    if not commit:
        print("\n--- dry run. Nothing was changed. ---")
        print("Re-run with --commit to send set_active:a and reboot.")
        return 0

    print("\n--- committing ---")
    for cmd in ("set_active:a", "reboot"):
        print(f"  -> {cmd}")
        try:
            status, payload, _ = command(dev, cmd)
            print(f"     {status} {payload.strip()}")
        except Exception as e:
            # 'reboot' commonly kills the link before the reply lands
            print(f"     (link dropped: {e})")
        time.sleep(2)
    print("\nDone. Watch the speaker: chime + LEDs = booted into slot A.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
