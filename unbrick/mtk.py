"""Shared helpers: pinned libusb backend + fastboot-over-bulk protocol."""
import sys, time, usb.core, usb.util, usb.backend.libusb1

LIBUSB = "/opt/homebrew/lib/libusb-1.0.dylib"
MTK_VID = 0x0E8D
FASTBOOT_PID = 0x201C

# Commands that write to flash. Never send these: there is no public firmware
# dump for HM_Citation200_Main_Board_MP1, so an erase is unrecoverable.
FORBIDDEN = ("flash", "erase", "format", "oem", "wipe", "update", "boot")

def backend():
    be = usb.backend.libusb1.get_backend(find_library=lambda x: LIBUSB)
    if be is None:
        sys.exit("FATAL: libusb backend unavailable. Run: brew install libusb")
    return be

def find(vid=MTK_VID, pid=None):
    kw = {"idVendor": vid, "backend": backend()}
    if pid is not None:
        kw["idProduct"] = pid
    return usb.core.find(**kw)

def find_all(**kw):
    return list(usb.core.find(find_all=True, backend=backend(), **kw))

def bulk_endpoints(dev):
    """Return (ep_out, ep_in, interface) for the first vendor-specific iface."""
    try:
        dev.set_configuration()
    except usb.core.USBError:
        pass  # already configured
    cfg = dev.get_active_configuration()
    for intf in cfg:
        eps = list(intf)
        outs = [e for e in eps
                if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK]
        ins = [e for e in eps
               if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
               and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK]
        if outs and ins:
            return outs[0], ins[0], intf
    raise RuntimeError("no bulk IN/OUT endpoint pair found")

def command(dev, cmd, timeout=5000, quiet=False):
    """Send one fastboot command and READ the reply (the posted script skipped
    this and ran blind). Returns (status, payload, info_lines)."""
    head = cmd.split(":")[0].lower()
    if any(head.startswith(f) for f in FORBIDDEN):
        raise RuntimeError(f"REFUSED: '{cmd}' can write flash. Not sending.")
    ep_out, ep_in, _ = bulk_endpoints(dev)
    ep_out.write(cmd.encode("ascii"), timeout=timeout)
    infos = []
    while True:
        try:
            raw = bytes(ep_in.read(256, timeout=timeout))
        except usb.core.USBError as e:
            return "TIMEOUT", str(e), infos
        status, payload = raw[:4].decode("ascii", "replace"), raw[4:].decode("ascii", "replace")
        if status == "INFO":
            infos.append(payload)
            if not quiet:
                print(f"    (info) {payload}")
            continue
        return status, payload, infos
