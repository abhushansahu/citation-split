# Citation 200 unbrick — working notes

Harman Kardon Citation 200, board `HM_Citation200_Main_Board_MP1` (2020.06.03).
Symptom: powers on → white LEDs blink → **plays startup tone** → dies. Hard
reset (Vol− + ⏻) does nothing.

Goal: test the Reddit A/B-slot theory — that a half-written Cast OTA corrupted
slot B while slot A is intact — by setting the active slot back to A over
fastboot. Source thread: r/hardwarehacking `1p5pfm3`.

## Status

- [x] Toolchain installed and verified on macOS 26.6.2 / arm64
- [ ] Speaker put into fastboot mode
- [ ] USB descriptor captured (`01`)
- [ ] Slot state read (`02` or `03 --dry-run`)
- [ ] `set_active:a` sent (`03 --commit`)

## Environment (already done, 2026-09-17)

| Piece | Version / path |
|---|---|
| fastboot | 37.0.1, universal binary, arm64 native — `/opt/homebrew/bin/fastboot` |
| libusb | 1.0.30 arm64 — `/opt/homebrew/lib/libusb-1.0.dylib` |
| pyusb | in `./venv`, backend pinned to the brew dylib |

**macOS gotcha, already worked around:** the Reddit recipe's `pip install libusb`
ships an **x86_64-only** dylib. On Apple Silicon pyusb imports fine but
`get_backend()` returns `None` and the script dies at `usb.core.find()`.
Fix was `brew install libusb`; `mtk.py` pins that path explicitly.

**Not needed on macOS:** Zadig / WinUSB. macOS does not bind a kernel driver to
an unclaimed vendor-specific interface, so libusb claims it directly.

## Getting into fastboot mode

Hold **Volume+ and the Multifunction button** together for 10–15 s while
connected by USB. Release around the moment the USB re-enumerate chime plays.

- All front lights on, buttons dead → **correct** (Yocto/fastboot gadget)
- Four **yellow** LEDs → wrong mode, that is BROM/download. Unplug, retry.

PID reference: `0x2000` BROM · `0x2001` preloader · `0x201c` Yocto/fastboot.

## Run order

```bash
cd ~/experiments/citation200-unbrick

./run 01-identify.py --watch     # poll while you work the button combo
./run 01-identify.py             # one-shot descriptor dump
```

`01` answers the branch point — whether the interface is class `FF`/sub `42`/
proto `03`, the signature the `fastboot` binary matches on:

- **MATCH** → `./02-fastboot-probe.sh` (read-only `getvar all`, logs to `logs/`),
  then `fastboot --set-active=a && fastboot reboot`
- **NO MATCH** → fastboot's binary will not see it; use raw pyusb:
  `./run 03-set-slot-a.py` (dry run), then `./run 03-set-slot-a.py --commit`

## Safety rules

- `mtk.py` **hard-blocks** `flash`, `erase`, `format`, `oem`, `wipe`, `update`.
  There is no public firmware dump for this board — an erase is unrecoverable.
  Verified: those are refused before anything reaches USB.
- `03` **reads the reply** to every command. The posted Reddit script only wrote
  to the OUT endpoint and never read the IN endpoint, so it could not tell
  `OKAY` from `FAIL` — it reported success unconditionally.
- If `getvar:current-slot` already returns **`a`**, stop. Slot corruption is not
  the fault and switching slots will not help.

## Expectation setting

The two Reddit reports describe a speaker that set up but would not *connect*,
and shut itself down. Ours reaches the **startup chime**, which means bootloader
plus enough OS to drive the DSP and amp — that points more at a power-rail,
battery, or amplifier fault than at a bad slot. This is cheap and non-destructive
so it is worth ruling out first, but UART on the J4 header is still the real
diagnostic if it does not take.

## Do not

Download `storage.to/wTPMco4un` from that thread. Anonymous file-host link
attached to an "AI fixed my device" post — classic malware pattern, and there is
nothing in it we need.

## Files

```
mtk.py              shared: pinned libusb backend, fastboot protocol, command guard
01-identify.py      read-only USB descriptor dump (--watch to poll)
02-fastboot-probe.sh read-only `fastboot getvar all` → logs/
03-set-slot-a.py    the fix over pyusb (--dry-run default, --commit to send)
run                 wrapper that uses ./venv/bin/python
logs/  dumps/       captured output
venv/               python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

## Session log 2026-09-27

Speaker direct to Mac on an Apple USB-C cable. Watcher (`01 --watch`,
unbuffered, log in `logs/identify-watch-20260927-171959.txt`) ran the whole time.

- Normal plug-in: boots, chime, dies. No USB enumeration.
- Vol+ + Multifunction 10–15 s (warm): restarts, chime, then **four steady
  yellow/orange front LEDs**. No USB enumeration at any point. So this yellow
  state is NOT BROM/preloader (those enumerate within ~1 s) — probably a
  battery/charging indication after the OS shut itself down.
- Cold start (buttons held before applying USB power, 15–20 s), twice: still
  no MediaTek VID on the bus.
- Red herring: `2d79:0003 "LDR2001"` (Shenzhen Ledrui PD controller,
  `/dev/cu.usbmodem2020_12_222`) is another accessory on the Mac, present since
  2026-09-24; it did not go away when the speaker was unplugged. Not the speaker.

Conclusion so far: the speaker produces no USB gadget in any state reached.
Either the fastboot combo is different on this board revision, or the USB-C
port is charge-only from the SoC's point of view. UART on J4 is the next step.

## Session log 2026-09-28

Speaker came up on its own after being plugged in. **The A/B-slot theory is dead:
the OS boots fine.** No USB gadget appears, as before — the USB-C port is
charge-only in normal operation.

Read over the LAN (Cast setup API, no auth token needed for `eureka_info`):

- `HK-Citation-200-…` on `_googlecast._tcp`, IP on the LAN (set `SPEAKER_IP` / `MAC_IP` for the cast scripts), name "Office speaker"
- firmware `1.52.272222` stable-channel, `has_update: false`, `setup_state: 60`,
  `wpa_state: 10` (associated), uptime ~43 min at time of reading
- current source: **Bluetooth Audio** (app id `89EA58C1`), volume 0.69
- `user_eq`: low_shelf 150 Hz **0 dB**, high_shelf 4.5 kHz **0 dB** → the bass
  boost is NOT the Google Home equaliser
- `scan_wifi` / RSSI needs `cast-local-authorization-token` (401) — not read

Manual (`HK_Citation_200_Owner_s_Manual_EN.pdf`, saved in scratchpad):

- Front amber, constant = **microphone muted** (slider on the back). This is
  what the "four yellow LEDs" on 2026-09-27 were, not BROM.
- Wi-Fi indicator on the back is a strength bar (excellent / fair / weak or
  disconnected). No green state exists in the manual.
- Battery LED: red blinking = low, white blinking = charging, white steady = full.
- Factory reset: hold ⏻ >10 s.

Cast sender: `cast_status.py` (read-only), `cast_play.py <wav…>` (serves
`~/experiments/speaker-tests` on :8765, plays at 0.35, restores volume).
Played 10 kHz / 1 kHz / 150 Hz tones — awaiting listening report.

### Acoustic measurement 2026-09-28 (MacBook mic, `cast_measure.py` / `cast_sweep.py`)

**Gotcha found first:** `play_media` while the Bluetooth Audio app (`89EA58C1`)
is in front reports PLAYING but never fetches the file and never makes sound.
Must `quit_app()`, then `media_controller.launch()` (Default Media Receiver
`CC1AD845`), then play. Both scripts now do this.

Tones, all -15 dBFS, speaker at 0.45 volume, room floor -51 dBFS:

| Tone | SNR at mic |
|---|---|
| 150 Hz | +31 dB |
| 1 kHz | +46 dB |
| 10 kHz | +3 dB (noise floor) |

Log sweep 20 Hz-20 kHz: tracks cleanly ~40 Hz to ~3 kHz, then nothing above
~3.2 kHz. Pink-noise octave bands fall into the floor above 2 kHz.

**Conclusion: the tweeter path is dead** (driver, its amp channel, or the DSP
tweeter output). The woofer is fine. This is the "bass boosted" sound.
Google Home EQ is flat, so no software knob will fix it. Unbrick tooling is
now irrelevant; next step is opening the unit and checking the tweeter's
voice coil (DC resistance, typically 4-8 Ω) and its connector.
