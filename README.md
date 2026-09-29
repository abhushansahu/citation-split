# citation-split

Keeping a Harman Kardon Citation 200 with a dead tweeter in daily use: the
Mac plays the highs, the speaker plays the lows, and a forked virtual audio
device keeps video in lip sync. The story, including the false "bricked"
diagnosis, is in [ARTICLE.md](ARTICLE.md). The earlier unbrick attempt and
the Cast-based acoustic measurements are in [unbrick/](unbrick/README.md).


Uses a Harman Kardon Citation 200 with a dead tweeter as a woofer for the
Mac. All system audio is split at 3 kHz: the band below goes to the speaker
over Bluetooth, the band above plays on the MacBook speakers, delayed so the
two arrive together.

```
Mac apps -> BlackHole (virtual output) -> split.py
                                            |-- <3 kHz, mono  -> Bluetooth -> Citation 200
                                            '-- >3 kHz, stereo, +delay -> MacBook speakers
```

## Setup (once)

```
cd ~/experiments/citation-split
./setup.sh
```

It installs BlackHole (asks for your password once, it's an audio driver),
two small CLI tools and a Python venv, then does a first delay measurement.
CoreAudio only picks up new drivers when it restarts, so setup restarts it
(`sudo killall coreaudiod`) if BlackHole isn't visible. If it still isn't,
reboot and re-run.

## Use (background service, recommended)

```
./build-driver.sh          # once: builds the "Citation Split" virtual device (needs only Command Line Tools)
sudo ./install-driver.sh   # once: installs it and restarts CoreAudio
./build-app.sh             # once: builds app/CitationSplit.app, a launcher the service runs through
./service.sh install       # once: launchd agent; macOS asks to allow "Citation Split" to use the microphone: Allow
./on.sh                    # route Mac audio to the speaker setup (or pick "Citation Split" in the sound menu)
./off.sh                   # back to the MacBook speakers (or pick them in the sound menu)
./vol.sh 85 100            # speaker 85 %, MacBook 100 %; saved, re-applied every time the service activates
./tune.sh                  # apply config.json edits live
./service.sh log           # watch it work; status / restart / uninstall also available
```

The service keeps the input open and does nothing until audio arrives, then
opens the speaker and MacBook outputs, syncs, and releases them again after
`idle_after_s` (120 s) of silence. So you can switch outputs freely from the
sound menu.

It also follows the speaker: if the Bluetooth speaker disconnects (switched
off, out of range, battery flat) while "Citation Split" is the system output,
the service switches the output to the MacBook speakers within about two
seconds, and switches back when the speaker reappears. If you pick "Citation
Split" while the speaker is away, it is switched back to the MacBook speakers
immediately. "Citation Split" itself cannot be hidden from the sound menu:
it is a driver-published device and stays listed; the auto-switch makes that
harmless.

### When the microphone is actually used

Only during a sync measurement: about 2 s of the Mac mic while the quiet
chirps play. That happens on every activation by default, because the
Bluetooth latency changes each time its stream is opened (157 vs 223 ms were
seen minutes apart), so a stored value can be tens of ms off. `"auto"` uses
the stored value when the last measurement is younger than `sync_max_age_h`;


`false` = only on `./resync.sh`. The always-open "Citation Split" input is a
virtual device, not the microphone; macOS just files it under the same
permission. For video calls, pick the MacBook speakers (or a headset) as the
call app's output; the service then stays idle and touches nothing.

### Bluetooth permission

The app bundle also declares a Bluetooth usage string, so the service can
ask the speaker to reconnect (`blueutil --connect`) when it is away. Allow it
if asked; presence detection itself needs no permission (it reads the audio
device list).

### Microphone permission

macOS treats reading *any* audio input, virtual devices included, as
microphone use. A bare python started by launchd is never asked and just
receives silence, which looks like "no audio output" with the service
sitting idle. The service therefore runs through `app/CitationSplit.app`, a
tiny launcher with a microphone usage description, so the permission prompt
appears once and sticks. If it was ever denied: System Settings > Privacy &
Security > Microphone > enable Citation Split, then `./service.sh restart`.

### Why "Citation Split" instead of plain BlackHole

The device is BlackHole with one line changed: it *reports* a presentation
latency of `total_latency_ms` (600 ms) to CoreAudio while adding none itself.
Video apps that honor device latency (Safari, Chrome, TV, QuickTime, IINA)
hold the picture back by that much. On first activation the service plays a
soft chirp *into* the device the way an app would, compares when the device
promised to play it with when the mic heard it, and pads both audio paths by
the gap (`total_pad_ms` in `latency.json`, re-measured on `./resync.sh`).
So app-to-ear lands exactly on the promise, whatever Bluetooth measures.
Result: lip sync in browsers with no per-app setting. Audio-only playback is
unaffected. Change `total_latency_ms` -> rebuild -> reinstall.

## Use (foreground, without the service)

```
./start.sh          # Ctrl-C stops and restores normal audio
./stop.sh           # from another terminal
```

On every start, and again 40 s later, the split plays two quiet chirp pairs
(a low one from the speaker, a high one from the MacBook), listens on the
Mac mic, and shifts its delay buffer until both arrive within about 8 ms.
Program audio is muted for the ~2 s this takes. Keep the Mac within
a metre or so of the speaker and the room reasonably quiet for those few
seconds. This happens inside the running process because Bluetooth latency
can change each time the audio stream is opened.

The keyboard volume keys do nothing while the split runs, because the
system output is the virtual device. Use `vol.sh`.

## Checks

```
./resync.sh                      # re-measure inside the running split (if it starts to sound smeared)
./venv/bin/python calibrate.py   # measure in a fresh session and save the start value
./venv/bin/python verify_live.py # with the split RUNNING: 3 soft chirps + a 6 s quiet sweep, checks sync and response
./venv/bin/python verify_total.py # with the service ACTIVE: is audio landing when the device tells apps it will?
```

## Volume

The system volume slider drives the virtual device and should stay at 100.
Loudness comes from the two physical outputs: `vol.sh N M` sets them now
and stores them under `volumes` in `config.json`, which the service
re-applies every time it activates (via `app/setvol`, a small CoreAudio tool
that sets a device's volume without switching the default output). Each
output's `gain_db` adds gain before that, and may go above 0: a per-output
peak limiter (ceiling 0.97) prevents clipping, so +3 to +6 dB is safe; beyond
that loud passages get audibly squashed. Note each device only carries part
of the spectrum, so neither will match its own full-range loudness. Raising
`gain_db` above 0 adds gain before the devices; go up in 2 dB steps and
back off if either output distorts.

## Tuning the bands

Each output has its own band in `config.json` under `outputs`, and they may
overlap. Edit, then `./tune.sh` applies it live.

| key | meaning |
|---|---|
| `lowpass_hz`, `lowpass_order` | citation's upper edge and slope (order 1 = 6 dB/oct, 2 = 12, 4 = 24) |
| `highpass_hz`, `highpass_order` | macbook's lower edge and slope |
| `gain_db` | level trim for that output, above 0 allowed (limited, not clipped) |
| `ceiling` | limiter ceiling, default 0.97 |
| `eq` | list of peaking bands `{"f", "gain_db", "q"}` for that output |

`./venv/bin/python autoeq.py` plays 8 s of quiet pink noise through the
split, measures third-octave levels at the laptop mic (close to where you
sit), and writes corrective peaking bands toward a gentle target (small bass
lift, slight downward tilt). `--dry-run` shows the table, `--reset` clears
all EQ. The laptop mic is not a measurement mic, so treat the result as a
starting point and trim by ear.

Starting point: citation low-pass 3.5 kHz order 2, macbook high-pass 1.2 kHz
order 2, macbook -2 dB. Two presets worth trying by ear (edit, `./tune.sh`):

- *More from the laptop*: macbook `highpass_hz` 700, `gain_db` -1. Less of
  the mid-range goes through Bluetooth compression; the laptop carries more.
- *Cleaner seam*: macbook `highpass_hz` 2000, citation `lowpass_hz` 3000,
  orders 4. Narrow overlap, less comb/phasiness between the two positions. The 1.2-3.5 kHz region comes from both; if vocals
sound doubled or phasey, narrow the overlap (raise `highpass_hz` or lower
`lowpass_hz`). If the laptop sounds harsh, add an eq band like
`{"f": 5000, "gain_db": -3, "q": 1.5}` to macbook.

Other keys:

| key | meaning |
| `bt_device`, `mac_device`, `input_device` | device names as macOS shows them |
| `bt_address` | Bluetooth MAC of the speaker, used to auto-connect |

`latency.json` holds the last locked delay and is only the starting guess
for the next session's sync. `beep_level` in `config.json` sets the sync
chirp loudness (0 to 1, default 0.08).

## How it holds sync

Each output device has its own clock, and each pulls audio in bursts, so
the raw buffer depth swings by tens of ms. `split.py` keeps a ring buffer
per output, smooths its depth over about a second, and bends the read rate
by at most 0.1 % (under 2 cents of pitch) to hold it at target. Earlier
versions dropped or repeated whole samples, which warbled audibly on music;
if it ever sounds like that again, check the "rate corr" numbers in the
15-second status lines are not growing without bound. Measured on
2026-09-28: within 2 ms after two minutes live, no underruns after startup.

## Files

```
setup.sh      one-time install of tools + first calibration
build-driver.sh / install-driver.sh   the Citation Split virtual device (BlackHole fork)
build-app.sh  builds app/CitationSplit.app (launcher, for the microphone permission)
service.sh    launchd agent: install | uninstall | status | log | restart
on.sh / off.sh  switch system output to the split / back to MacBook speakers
start.sh      switch output to BlackHole, set volumes, run split.py (Ctrl-C restores)
stop.sh       kill split.py and restore the MacBook speakers
vol.sh        set the two physical output volumes
tune.sh       reload config.json into the running split
resync.sh     ask the running split to re-measure its delay
split.py      the crossover / delay engine, with in-session sync
calibrate.py  runs split.py --calibrate-only
verify_live.py end-to-end check of the running split via the system player
verify_total.py app-to-ear timing vs the device's promise (lip-sync check)
autoeq.py     measured room/seat EQ correction via the laptop mic
config.json   devices and tuning
latency.json  measured delay
```

## If it sounds bad

- **Warbling / wobbly pitch:** was a bug in the buffer servo, fixed 2026-09-28.
  Status lines every 15 s show each buffer's `ratio`; it should sit within
  0.0005 of 1.0 once settled.
- **Smeared / doubled transients:** paths out of sync. `./resync.sh`.
- **Harsh or thin:** the laptop is now the tweeter and sits closer to you
  than the speaker. Lower it: `./vol.sh 65 40`.
- **Choppy on the speaker only:** Bluetooth link. Move the Mac closer, or off
  a crowded 2.4 GHz channel.

## Sizing `total_latency_ms`

The promise must exceed the slowest Bluetooth session plus the split's own
buffers (about 200 ms of overhead). Bluetooth on this speaker has measured
130 to 240 ms, so 400 ran out; 600 leaves headroom. If the log says
`WARNING: Bluetooth is N ms slower than total_latency_ms allows`, raise it,
`./build-driver.sh`, `sudo ./install-driver.sh`. Bigger only means video apps
hold the picture back longer before starting; playback itself is unaffected.

## Known limits

- Bluetooth delay measured 130 to 250 ms on this speaker depending on the
  session, so the Mac's own audio is delayed by that much. Fine for music and video, noticeable
  for games or instruments.
- Highs come from the laptop, lows from the speaker. The image follows the
  laptop.
- Stopgap only. The real fix is a replacement tweeter.
