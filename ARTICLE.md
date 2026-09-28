# Salvaging a "dead" Harman Kardon Citation 200

*How a speaker that spent a month in a boot loop came back with one dead part, and how a
laptop, a microphone and a few hundred lines of Python turned it back into
something worth listening to. Everything described here is in this repo.*

## The lost cause

The Citation 200 is a 2020 portable smart speaker: Wi-Fi, Bluetooth, Google
Cast, one woofer, one tweeter. Mine had reached the state every owner of a
smart speaker dreads. Plug it in, the lights blink, it plays its startup chime,
and it dies. Hard reset did nothing. The internet's diagnosis was a corrupted
firmware slot from a half-finished update, with a recipe for forcing the
bootloader into fastboot mode over USB and switching to the other slot.

We built the tooling for that recipe carefully, with a guard that refuses any
command that could erase the flash, because there is no public firmware dump
for this board and an erase is unrecoverable. Then we spent an evening holding
button combinations. The speaker never once showed up on the USB bus. Its USB-C
port, it turns out, is charge-only from the SoC's point of view.

## It got itself out of it

Then, after more than a month of this, the speaker came up on its own: Wi-Fi,
Bluetooth, Cast, all working. We cannot claim credit, and we cannot fully
explain it either. Weeks of factory resets and button combinations had done
nothing. What was different in the end was that the speaker had been left with
its battery disconnected for an extended stretch before being plugged in
again, and the Google Home setup was redone from an Android phone instead of
an iPhone. Whether the long power-down cleared some stuck flag or state, or
whether the boot loop simply gave up, we do not know. If your unit is in the
same loop and nothing else works, a long, complete disconnection is cheap to
try and is what preceded recovery here.

Two things we did get wrong along the way are worth passing on. The four
ominous orange lights we had read as a bootloader mode were nothing of the
sort: the manual's LED table says amber means the microphone mute slider is
on. And "chime then die" is exactly what a deeply flat battery looks like,
which is not something the fastboot theory predicted. Read the manual's LED
table before reading Reddit.

Back in the land of the living, though, it sounded terrible. "Very bass
boosted, nowhere near how it used to."

## Measuring instead of guessing

We had no measurement microphone, so we used the MacBook's. The speaker
advertises Google Cast on the LAN, so a small script cast test tones to it
while the laptop recorded. One thing bit us immediately: with the Bluetooth
app in the foreground, Cast reports "PLAYING" but never fetches the file. You
have to quit that app and launch the media receiver explicitly.

Once sound actually came out, the picture was unambiguous:

| Tone | Level at the mic |
|---|---|
| 150 Hz | 31 dB above room noise |
| 1 kHz | 46 dB above room noise |
| 10 kHz | 3 dB, in the noise |

A sweep confirmed it: output tracks cleanly to about 3 kHz and then nothing.
The tweeter path was dead. The Google Home equaliser was flat, so no software
knob would fix it. "Bass boosted" was simply "no treble".

The correct repair is a replacement tweeter. Until then, was there anything
software could do?

## Using the speaker for what it can still do

The idea: let the Citation play only what it can, everything below about
3 kHz, and let the MacBook's own speakers play everything above. A two-way
active crossover, except the two "drivers" are separate devices connected by
different paths.

The pieces, all in this repo:

- **A virtual output device** so every Mac app's audio lands in our process.
  We started with BlackHole.
- **A Python DSP process** (`split.py`) that filters the stream into two
  bands, sends the low band to the Citation over Bluetooth and the high band
  to the MacBook speakers, and holds them in sync.
- **A measurement of the Bluetooth delay**, made with the laptop mic: a low
  chirp goes to the speaker and a high chirp to the laptop at the same
  instant, and matched filtering finds when each arrives. The difference is
  the delay the MacBook path needs to add.

## Things that went wrong, and what they taught us

**Bluetooth latency is not a number.** On this speaker it measured anywhere
from 130 to 250 ms, and it changes each time the audio stream is opened. A
calibration made in a separate process was useless. The measurement has to
happen inside the running process, on the live stream.

**The drift servo warbled.** Two output devices run on two clocks, so a
buffer per device has to be kept at a target depth. The first version dropped
or repeated single samples to do that. It measured fine on sweeps and clicks
and sounded awful on music. Output devices pull audio in bursts, so the buffer
depth swings by tens of milliseconds, and the servo kept stretching time back
and forth. A steady 6 kHz tone wobbled between 5980 and 6012 Hz. The fix was
a smoothed, fractional-rate reader limited to 0.3 percent, under 2 cents of
pitch. Lesson: coverage and timing tests do not catch modulation. Test a
steady tone for spectral purity.

**A slow servo fools the sync.** Once the servo was gentle, a sync measurement
taken mid-correction was wrong by whatever the servo still had left to do.
The measurement now subtracts the pending buffer error, and the service
re-checks itself 40 seconds after locking.

**Lip sync needs the device to tell the truth.** For music the delay is
invisible. For video, apps hold the picture back by whatever latency the
output device reports, and a virtual device reports zero. So we forked
BlackHole, changed one line so it reports a fixed 400 ms, and made the split
pad both paths so audio really does land 400 ms after the app hands it over.
Chrome, Safari and Apple's players then lip-sync on their own. The build
needs only Command Line Tools, not Xcode. Two gotchas: CoreAudio only loads
new drivers when its daemon restarts, and logging out does not restart it;
and the option BlackHole documents for latency also *adds* that delay inside
the driver, which is not what you want.

**launchd and the microphone.** Running the split as a background service, it
received pure silence from the virtual device. macOS treats reading any audio
input as microphone use, and a bare python process started by launchd is
never asked for permission. It just gets zeros. The service now runs through a
20-line C launcher inside an app bundle that declares a microphone usage
string, so the prompt appears once and sticks.

**Quiet tests.** The first test signals were loud pure tones and a 20-second
sweep, appropriate for diagnosing a dead driver and painful to live with.
Matched filtering gives about 20 dB of processing gain, so the sync chirps
now play at a quarter of the level and the sweep is 6 seconds.

## What it is now

Pick "Citation Split" in the sound menu and play anything. A background
service wakes up, sets the volumes, applies stored timing, and goes idle two
minutes after the music stops. Every two hours or so, or on request, it plays
two soft chirps and re-measures. Verified with the mic: the two speakers land
within a few milliseconds of each other and within a few milliseconds of what
the virtual device promises apps. Each output has its own configurable band,
gain and EQ, applied live.

It is not a repaired speaker. Treble comes from the laptop and bass from
across the room, and Bluetooth compression sits in the middle. But it is a
speaker in daily use instead of a paperweight, for the price of an evening.

## If you are in a similar spot

1. **Measure before believing.** A laptop mic and a sine sweep will tell you
   which driver is dead in ten minutes. Everything in `unbrick/` and
   `verify_live.py` works with any speaker that accepts Cast, AirPlay or
   Bluetooth.
2. **The split is generic.** `config.json` names the devices; nothing in
   `split.py` is Citation-specific. Any Bluetooth speaker with a dead tweeter,
   or any pairing of a good sub with a good top, works the same way.
3. **The BlackHole fork is the reusable trick.** Any time a virtual device
   feeds a path with real latency, build it to report that latency and pad to
   match. Video will follow.
4. **Expect the privacy gate.** Anything that reads an audio input under
   launchd needs an app bundle and a microphone usage string.

The README covers setup in order: tools, driver, app wrapper, service.
