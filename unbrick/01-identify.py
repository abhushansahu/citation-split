#!/usr/bin/env python3
"""STEP 1 (read-only): watch for the speaker and dump its USB descriptors.

The key question this answers: does the device's interface match fastboot's
expected class/subclass/protocol (0xFF/0x42/0x03)? If yes -> use the real
`fastboot` binary (02-fastboot-probe.sh). If no -> use the pyusb fallback.
"""
import sys, time, usb.util
from mtk import find_all, MTK_VID

CLASS_NAMES = {0x00: "per-interface", 0xFF: "vendor-specific", 0x08: "mass-storage",
               0x02: "CDC-comm", 0x0A: "CDC-data", 0x03: "HID"}

def describe(dev):
    print(f"\n{'='*64}")
    print(f"  {dev.idVendor:04x}:{dev.idProduct:04x}  bus {dev.bus} addr {dev.address}")
    for field in ("iManufacturer", "iProduct", "iSerialNumber"):
        try:
            print(f"  {field[1:]:<14}: {usb.util.get_string(dev, getattr(dev, field))}")
        except Exception:
            pass
    print(f"{'='*64}")
    try:
        cfg = dev.get_active_configuration()
    except Exception as e:
        print(f"  ! cannot read config descriptor: {e}")
        return
    for intf in cfg:
        c, s, p = intf.bInterfaceClass, intf.bInterfaceSubClass, intf.bInterfaceProtocol
        match = (c, s, p) == (0xFF, 0x42, 0x03)
        print(f"\n  Interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting}")
        print(f"    class    0x{c:02x}  ({CLASS_NAMES.get(c, 'other')})")
        print(f"    subclass 0x{s:02x}")
        print(f"    protocol 0x{p:02x}")
        print(f"    >>> fastboot signature (FF/42/03): "
              f"{'MATCH - use the fastboot binary' if match else 'NO MATCH - use pyusb fallback'}")
        for ep in intf:
            d = "OUT" if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT else "IN "
            t = {0: "control", 1: "iso", 2: "bulk", 3: "interrupt"}[usb.util.endpoint_type(ep.bmAttributes)]
            print(f"    ep 0x{ep.bEndpointAddress:02x} {d} {t:<9} maxpkt {ep.wMaxPacketSize}")

def main():
    watch = "--watch" in sys.argv
    print("Looking for MediaTek devices (VID 0x0E8D)..."
          + ("  [watching - Ctrl-C to stop]" if watch else ""))
    seen = set()
    while True:
        devs = find_all(idVendor=MTK_VID)
        for d in devs:
            key = (d.bus, d.address, d.idProduct)
            if key not in seen:
                seen.add(key)
                describe(d)
                print("\n  PID reference: 0x2000=BROM  0x2001=preloader  "
                      "0x201c=Yocto/fastboot gadget")
        if not watch:
            if not devs:
                print("\n  No MediaTek device found.")
                print("  Put the speaker in fastboot mode first:")
                print("    hold Volume+ AND the Multifunction button together, 10-15s,")
                print("    while connected by USB; release around the re-enumerate chime.")
                print("    All front lights on + buttons dead = right mode.")
                print("    Four YELLOW LEDs = wrong mode (that is BROM/download).")
                print("\n  Re-run with --watch to poll continuously while you try the combo.")
            return 0 if devs else 1
        time.sleep(1)

if __name__ == "__main__":
    sys.exit(main())
