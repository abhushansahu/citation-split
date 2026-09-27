"""Two-way active crossover across two output devices, with in-session sync.

Reads system audio from a virtual input device (BlackHole), splits it at
the crossover with a 4th-order Linkwitz-Riley filter, and sends:

  low band  (mono)   -> the Bluetooth speaker   (whose tweeter is dead)
  high band (stereo) -> the MacBook speakers, delayed to match Bluetooth latency

Bluetooth latency differs every time the stream is opened (tens of ms) and
is then constant, so the delay is measured *inside* this process: on start
it plays a short beep pair (1 kHz via Bluetooth, 8 kHz via the MacBook),
listens on the Mac mic, and shifts its own delay buffer until both arrive
together. Send SIGUSR1 to re-sync while running.

Each output device runs on its own clock; a ring buffer per output plus a
slow servo (drop / repeat one sample per block when >10 ms off target)
keeps them aligned indefinitely.

    ./venv/bin/python split.py                    # live from BlackHole, auto-sync at start
    ./venv/bin/python split.py --calibrate-only   # measure, save latency.json, exit
    ./venv/bin/python split.py --file x.wav       # play a wav through the split (tests)
"""
import warnings; warnings.filterwarnings("ignore")
import argparse, json, os, sys, signal, threading, time
import numpy as np, sounddevice as sd
from scipy.signal import butter, sosfilt, sosfilt_zi, sosfiltfilt, resample_poly
from scipy.io import wavfile

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json")))
LATF = os.path.join(HERE, "latency.json")
LAT = json.load(open(LATF)) if os.path.exists(LATF) else {"delay_ms": 200.0}

p = argparse.ArgumentParser()
p.add_argument("--input", help="override input device name (default from config.json)")
p.add_argument("--file", help="wav file to play through the split instead of live input")
p.add_argument("--calibrate-only", action="store_true", help="measure the delay, save it, exit")
p.add_argument("--no-sync", action="store_true", help="skip the startup beep/measure")
p.add_argument("--delay-ms", type=float, default=LAT["delay_ms"])
p.add_argument("--crossover", type=float, default=CFG.get("crossover_hz", 3000))
p.add_argument("--low-gain-db", type=float, default=CFG.get("low_gain_db", 0))
p.add_argument("--high-gain-db", type=float, default=CFG.get("high_gain_db", 0))
p.add_argument("--block", type=int, default=CFG.get("block", 512))
p.add_argument("--beep-level", type=float, default=CFG.get("beep_level", 0.08))
a = p.parse_args()

SR = 48000
BLOCK = a.block

def find(name, kind):
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d[f"max_{kind}_channels"] > 0:
            return i
    raise LookupError(f"{kind} device not found: {name!r}")

def bt_index():
    global bt_i, bt_sr
    bt_i = find(CFG["bt_device"], "output"); bt_sr = int(sd.query_devices(bt_i)["default_samplerate"]); return bt_i
bt_i = None; bt_sr = SR
mac_i = find(CFG["mac_device"], "output")

# --- per-output filter chains, from config ---------------------------------
# config.json "outputs": {"citation": {...}, "macbook": {...}} each with:
#   lowpass_hz / lowpass_order, highpass_hz / highpass_order (0 = none),
#   gain_db, eq: [{"f": Hz, "gain_db": dB, "q": Q}, ...]  (peaking bands)
def peaking(f, g, q):
    A = 10 ** (g / 40); w = 2 * np.pi * f / SR; al = np.sin(w) / (2 * q)
    b = [1 + al * A, -2 * np.cos(w), 1 - al * A]; a_ = [1 + al / A, -2 * np.cos(w), 1 - al / A]
    return np.array([[b[0] / a_[0], b[1] / a_[0], b[2] / a_[0], 1, a_[1] / a_[0], a_[2] / a_[0]]])

class Chain:
    def __init__(self, spec, ch):
        parts = []
        if spec.get("lowpass_hz"):  parts.append(butter(spec.get("lowpass_order", 2), spec["lowpass_hz"], "lowpass", fs=SR, output="sos"))
        if spec.get("highpass_hz"): parts.append(butter(spec.get("highpass_order", 2), spec["highpass_hz"], "highpass", fs=SR, output="sos"))
        for e in spec.get("eq", []): parts.append(peaking(e["f"], e["gain_db"], e.get("q", 1.0)))
        self.sos = np.vstack(parts) if parts else np.array([[1, 0, 0, 1, 0, 0]], float)
        self.z = np.stack([sosfilt_zi(self.sos)] * ch, axis=-1) * 0
        self.g = 10 ** (spec.get("gain_db", 0) / 20)
    def __call__(self, x):
        y, self.z = sosfilt(self.sos, x, axis=0, zi=self.z)
        return y * self.g

def build_chains():
    o = CFG.get("outputs", {})
    cit = o.get("citation", {"lowpass_hz": a.crossover, "lowpass_order": 4})
    mac = o.get("macbook", {"highpass_hz": a.crossover, "highpass_order": 4})
    return Chain(cit, 2), Chain(mac, 2), cit, mac
chain_bt, chain_mac, spec_bt, spec_mac = build_chains()
g_lo, g_hi = 10 ** (a.low_gain_db / 20), 10 ** (a.high_gain_db / 20)

def reload_config(*_):
    global CFG, chain_bt, chain_mac, spec_bt, spec_mac
    try:
        CFG = json.load(open(os.path.join(HERE, "config.json")))
        nb, nm, sb, sm = build_chains()
        chain_bt, chain_mac, spec_bt, spec_mac = nb, nm, sb, sm
        print(f"[reload] citation {sb} | macbook {sm}", flush=True)
    except Exception as e:
        print(f"[reload] config error, keeping old filters: {e}", flush=True)

# --- ring buffers ---------------------------------------------------------
class Ring:
    """Ring buffer with a fractional-rate reader. Output devices pull in bursts,
    so the raw fill swings tens of ms; the servo acts on a ~1 s smoothed fill
    and bends the read rate by at most 0.1 % (1.7 cents), which is inaudible."""
    MAX_RATE = 0.003    # 0.3 % = 5 cents, only reached for errors over ~30 ms
    def __init__(self, ch, seconds, target):
        self.n = int(seconds * SR); self.buf = np.zeros((self.n, ch), np.float32); self.ch = ch
        self.w = 0; self.r = 0.0; self.target = target; self.lock = threading.Lock()
        self.err_s = 0.0; self.under = 0; self.corr = 0.0
    @property
    def fill(self): return self.w - self.r
    def push(self, x):
        with self.lock:
            k = len(x); i = np.arange(self.w, self.w + k) % self.n
            self.buf[i] = x; self.w += k
    def pull(self, k):
        with self.lock:
            err = self.fill - self.target
            self.err_s += 0.01 * (err - self.err_s)
            ratio = 1.0 + max(-self.MAX_RATE, min(self.MAX_RATE, self.err_s / (SR * 2.0)))
            self.ratio = ratio; self.pulls = getattr(self, "pulls", 0) + 1
            need = int(np.ceil(k * ratio)) + 2
            if self.fill < need:
                self.under += 1; return np.zeros((k, self.ch), np.float32)
            base = int(np.floor(self.r)); src = self.buf[np.arange(base, base + need) % self.n]
            pos = (self.r - base) + np.arange(k) * ratio
            out = np.stack([np.interp(pos, np.arange(need), src[:, c]) for c in range(self.ch)], axis=1).astype(np.float32)
            self.r += k * ratio; self.corr += k * (ratio - 1.0)
            return out
    def reset(self):
        with self.lock:
            self.buf[:] = 0; self.w = self.target; self.r = 0.0; self.err_s = 0.0
    def shift(self, delta):
        """Change this output's delay by delta samples: +delta = later (re-read), -delta = skip ahead."""
        with self.lock:
            delta = int(delta)
            self.r = max(self.w - self.n + 1, self.r - delta) if delta > 0 else min(self.w, self.r - delta)
            self.target += delta; self.err_s = 0.0

delay_samples = int(a.delay_ms / 1000 * SR)
safety = 8 * BLOCK    # ~85 ms; absorbs Bluetooth scheduling jitter (adds equally to both paths)
ring_mac = Ring(2, 6, target=delay_samples + safety)
ring_bt = Ring(1, 6, target=safety)
ring_mac.push(np.zeros((delay_samples + safety, 2), np.float32))
ring_bt.push(np.zeros((safety, 1), np.float32))
current_delay_ms = lambda: (ring_mac.target - ring_bt.target) / SR * 1000

# --- processing ----------------------------------------------------------
muted = False   # set during sync so program audio doesn't pollute the measurement
inject = []     # pending sync beeps: [lo(n,1), hi(n,2), pos]; mixed in, never added as extra samples
inject_lock = threading.Lock()
def process(x):
    lo = chain_bt(x); hi = chain_mac(x)
    if muted: lo[:] = 0; hi[:] = 0
    lo = (lo.mean(axis=1, keepdims=True) * g_lo).astype(np.float32); hi = (hi * g_hi).astype(np.float32)
    with inject_lock:
        if inject:
            b1, b8, pos = inject[0]; k = min(len(x), len(b1) - pos)
            lo[:k] += b1[pos:pos + k]; hi[:k] += b8[pos:pos + k]
            inject[0][2] += k
            if inject[0][2] >= len(b1): inject.pop(0)
    ring_bt.push(lo); ring_mac.push(hi)

stat = {"in": 0, "mac": 0, "bt": 0}
level = {"rms": 0.0, "last_audio": 0.0}
active = False
def in_cb(indata, frames, t, status):
    if status: stat["in"] += 1
    x = indata.astype(np.float32)
    r = float(np.sqrt(np.mean(x * x))); level["rms"] = r
    if r > 1e-4: level["last_audio"] = time.time()
    if active: process(x)
def mac_cb(outdata, frames, t, status):
    if status: stat["mac"] += 1
    outdata[:] = ring_mac.pull(frames)
def bt_cb(outdata, frames, t, status):
    if status: stat["bt"] += 1
    if bt_sr == SR: outdata[:] = ring_bt.pull(frames)
    else:
        need = int(np.ceil(frames * SR / bt_sr)) + 1
        y = resample_poly(ring_bt.pull(need)[:, 0], bt_sr, SR)[:frames]
        outdata[:, 0] = y; outdata[:, 1] = y

def open_bt():
    global bt_sr
    for sr in (SR, bt_sr):
        try:
            s = sd.OutputStream(device=bt_index(), samplerate=sr, channels=2, dtype="float32", blocksize=BLOCK, callback=bt_cb)
            bt_sr = sr; return s
        except Exception as e: last = e
    raise RuntimeError(f"cannot open Bluetooth output: {last}")

# --- in-session sync -------------------------------------------------------
# Soft chirps (not loud pure tones), found by matched filtering, which gives
# ~20 dB of processing gain, so they can be quiet. Low chirp -> Bluetooth path,
# high chirp -> MacBook path, injected at the same instant.
CH_LEN = 0.08
def _chirp(f0, f1):
    t = np.arange(int(CH_LEN * SR)) / SR; k = np.log(f1 / f0)
    ph = 2 * np.pi * f0 * CH_LEN / k * (np.exp(k * t / CH_LEN) - 1)
    return (np.sin(ph) * np.hanning(len(t))).astype(np.float32)
CHIRP_LO, CHIRP_HI = _chirp(300, 1500), _chirp(4000, 9000)

def _matched_onsets(x, ref, band, n_expect):
    sos = butter(4, band, "bandpass", fs=SR, output="sos"); xb = sosfiltfilt(sos, x)
    n = len(xb) + len(ref); c = np.fft.irfft(np.fft.rfft(xb, n) * np.conj(np.fft.rfft(ref, n)), n)[: len(xb)]
    e = np.abs(c); e = np.convolve(e, np.ones(int(0.002 * SR)) / int(0.002 * SR), "same")
    out = []
    for _ in range(n_expect):
        i = int(np.argmax(e))
        if e[i] < 6 * np.median(e): break
        out.append(i / SR); lo, hi = max(0, i - int(0.25 * SR)), i + int(0.25 * SR); e[lo:hi] = 0
    return sorted(out)

def measure_offset(nb=2, gap=0.6):
    """Inject nb chirp pairs, record the mic, return median (bt - mac) seconds."""
    mic_i = find(CFG.get("mic_device", "MacBook Pro Microphone"), "input")
    lvl = a.beep_level
    dur = 0.5 + nb * gap + current_delay_ms() / 1000 + 0.6
    rec = sd.rec(int(dur * SR), samplerate=SR, channels=1, device=mic_i, dtype="float32")
    time.sleep(0.4)
    for k in range(nb):
        with inject_lock: inject.append([(lvl * CHIRP_LO)[:, None], np.column_stack([lvl * CHIRP_HI] * 2), 0])
        time.sleep(gap)
    sd.wait(); x = rec[:, 0]
    o_lo = _matched_onsets(x, CHIRP_LO, [250, 1800], nb)
    o_hi = _matched_onsets(x, CHIRP_HI, [3500, 10000], nb)
    d = []
    for m in o_hi:
        c = [b for b in o_lo if abs(b - m) < 0.5]
        if c: d.append(min(c, key=lambda b: abs(b - m)) - m)
    if not d: return None, 0
    med = float(np.median(d)); inl = [v for v in d if abs(v - med) < 0.02]
    return float(np.median(inl)), len(inl)

def measure_claim_gap(n=3):
    """Play a chirp INTO the virtual device (as an app would), note when the device claims it will
    play (DAC time), and hear it on the mic. Returns median (claim - heard) seconds: >0 means we
    play EARLY versus what apps are told, so both paths need that much more delay."""
    out_i = find(in_name, "output"); mic_i = find(CFG.get("mic_device", "MacBook Pro Microphone"), "input")
    lvl = a.beep_level * 1.5
    sig = np.zeros((int(1.2 * SR), 2), np.float32); at = int(0.4 * SR)
    sig[at:at + len(CHIRP_HI), 0] += lvl * CHIRP_HI; sig[at:at + len(CHIRP_LO), 0] += lvl * CHIRP_LO; sig[:, 1] = sig[:, 0]
    gaps = []
    for _ in range(n):
        pos = {"i": 0, "claim": None}
        def cb(out, frames, ti, status):
            i = pos["i"]; blk = sig[i:i + frames]
            if i <= at < i + frames: pos["claim"] = time.time() + (at - i) / SR + (ti.outputBufferDacTime - ti.currentTime)
            out[:len(blk)] = blk; out[len(blk):] = 0; pos["i"] = i + frames
            if pos["i"] >= len(sig): raise sd.CallbackStop
        rec = sd.rec(int(2.5 * SR), samplerate=SR, channels=1, device=mic_i, dtype="float32"); t_rec = time.time(); time.sleep(0.25)
        with sd.OutputStream(device=out_i, samplerate=SR, channels=2, blocksize=BLOCK, dtype="float32", callback=cb) as st:
            while st.active: time.sleep(0.05)
        sd.wait(); x = rec[:, 0]
        o = _matched_onsets(x, CHIRP_HI, [3500, 10000], 1)
        if o and pos["claim"]: gaps.append(pos["claim"] - (t_rec + o[0]))
        time.sleep(0.3)
    if len(gaps) < 2: return None
    med = float(np.median(gaps)); inl = [g for g in gaps if abs(g - med) < 0.02]
    return float(np.median(inl)) if len(inl) >= 2 else None

padded = False
def enforce_total(tag):
    """Once per activation: pad both paths so audio lands exactly when the virtual device tells apps it will."""
    global padded
    if padded or not CFG.get("total_latency_ms"): return
    pad = LAT.get("total_pad_ms")
    if pad is None or tag == "resync":
        g = measure_claim_gap()
        if g is None:
            print(f"[{tag}] could not measure app-to-ear gap (chirp not heard); {'using stored pad' if pad is not None else 'no padding applied'}", flush=True)
        else:
            pad = g * 1000; LAT["total_pad_ms"] = round(pad, 1)
            json.dump(LAT, open(LATF, "w"), indent=2)
            print(f"[{tag}] measured: audio plays {pad:+.0f} ms early vs the device's promise; saved as total_pad_ms", flush=True)
    if pad is None: return
    if pad < 0:
        print(f"[{tag}] WARNING: audio already {-pad:.0f} ms LATE vs the device's promise; raise total_latency_ms, rebuild and reinstall the driver", flush=True); return
    ring_bt.shift(pad / 1000 * SR); ring_mac.shift(pad / 1000 * SR); padded = True
    print(f"[{tag}] padded both paths by {pad:.0f} ms so audio lands when apps expect it", flush=True)

def settled(timeout=45):
    """Wait until both buffer servos are near target, so a measurement is not taken on a transient."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if getattr(ring_bt, "pulls", 0) > 300 and abs(ring_bt.err_s) < 0.003 * SR and abs(ring_mac.err_s) < 0.003 * SR: return True
        time.sleep(0.5)
    return False

def sync(tag="sync"):
    global muted
    if not active: print(f"[{tag}] skipped: outputs idle", flush=True); return False
    if not settled(): print(f"[{tag}] buffers not settled after 45 s; measuring anyway", flush=True)
    muted = True; time.sleep(0.2)
    ok = False
    for rnd in range(5):
        off, n = measure_offset()
        if off is not None and abs(off) > 0.008 and rnd == 4: pass
        if off is None:
            print(f"[{tag}] round {rnd+1}: chirps not detected; Mac within ~1 m of the speaker? volumes up? (beep_level in config.json raises them)", flush=True); continue
        # the servos will still remove their current smoothed fill errors, so correct for where the paths will REST
        off = off - (ring_bt.err_s - ring_mac.err_s) / SR
        print(f"[{tag}] round {rnd+1}: bluetooth {off*1000:+.0f} ms vs MacBook (at rest) at delay {current_delay_ms():.0f} ms ({n} beeps)", flush=True)
        if abs(off) <= 0.008: ok = True; break
        ring_mac.shift(off * SR)      # BT late -> more Mac delay; BT early -> less
    muted = False
    if ok:
        LAT["delay_ms"] = round(current_delay_ms(), 1); LAT["measured"] = time.strftime("%Y-%m-%d %H:%M"); json.dump(LAT, open(LATF, "w"), indent=2)
        print(f"[{tag}] locked: MacBook delayed {current_delay_ms():.0f} ms (saved as next start value)", flush=True)
        enforce_total(tag)
    else:
        print(f"[{tag}] could not lock; running with delay {current_delay_ms():.0f} ms", flush=True)
    return ok

# --- run -------------------------------------------------------------------
out_streams = []; mac_lat = 0.0; in_lat = 0.0; in_name = a.input or CFG["input_device"]
def open_outputs():
    global out_streams, mac_lat, active
    global padded
    padded = False; ring_bt.reset(); ring_mac.reset()
    bt = open_bt(); mac = sd.OutputStream(device=mac_i, samplerate=SR, channels=2, dtype="float32", blocksize=BLOCK, callback=mac_cb)
    mac_lat = mac.latency; bt.start(); mac.start(); out_streams = [bt, mac]; active = True
    print(f"[active] reported stream latency: bluetooth {bt.latency*1000:.0f} ms, macbook {mac.latency*1000:.0f} ms (difference {(bt.latency-mac.latency)*1000:.0f} ms)", flush=True)
    v = CFG.get("volumes")
    if v:
        import subprocess
        for dev, key in ((CFG["bt_device"], "bt"), (CFG["mac_device"], "mac")):
            subprocess.run([os.path.join(HERE, "app", "setvol"), dev, str(v[key])], capture_output=True)
        print(f"[active] volumes set: {CFG['bt_device']} {v['bt']} %, {CFG['mac_device']} {v['mac']} %", flush=True)
def close_outputs():
    global out_streams, active
    active = False
    for st in out_streams:
        try: st.stop(); st.close()
        except Exception: pass
    out_streams = []

if a.file or a.calibrate_only:
    open_outputs()
    if a.file:
        fsr, x = wavfile.read(a.file); x = x.astype(np.float32) / 32768
        if x.ndim == 1: x = np.column_stack([x, x])
        if fsr != SR: x = resample_poly(x, SR, fsr, axis=0).astype(np.float32)
    feed = True
    def feeder():
        z = np.zeros((BLOCK, 2), np.float32)
        while feed:
            if ring_bt.fill < ring_bt.target + 2 * BLOCK: process(z)
            else: time.sleep(BLOCK / SR / 2)
    threading.Thread(target=feeder, daemon=True).start()
    print(f"split: {CFG['bt_device']} {spec_bt} | {CFG['mac_device']} {spec_mac}, delayed {current_delay_ms():.0f} ms", flush=True)
    time.sleep(1.0)
    try:
        if a.calibrate_only: sys.exit(0 if sync("calibrate") else 2)
        if not a.no_sync: sync("startup")
        feed = False; time.sleep(0.05)
        for i in range(0, len(x), BLOCK):
            process(x[i:i + BLOCK])
            while ring_bt.fill > ring_bt.target + 0.3 * SR: time.sleep(0.005)
        ub, um = ring_bt.under, ring_mac.under
        time.sleep(current_delay_ms() / 1000 + 1.0)
        print(f"stats: underruns during playback bt={ub} mac={um}", flush=True)
    finally:
        feed = False; close_outputs()
    sys.exit(0)

in_name = a.input or CFG["input_device"]
# ---- live / service mode: input always open; outputs only while audio plays ----
IDLE_AFTER = CFG.get("idle_after_s", 20)
in_stream = None
while in_stream is None:
    try:
        in_stream = sd.InputStream(device=find(in_name, "input"), samplerate=SR, channels=2, dtype="float32", blocksize=BLOCK, callback=in_cb)
    except LookupError as e:
        print(f"waiting: {e} (is the driver installed?)", flush=True); time.sleep(10)
in_stream.start(); in_lat = in_stream.latency
time.sleep(2.0); print(f"input check: rms {level['rms']:.5f} after 2 s (0.00000 under launchd usually means no Microphone permission for the service)", flush=True)
signal.signal(signal.SIGUSR2, reload_config)
signal.signal(signal.SIGUSR1, lambda *_: active and threading.Thread(target=sync, args=("resync",), daemon=True).start())
print(f"split service: input '{in_name}' -> {CFG['bt_device']} {spec_bt} | {CFG['mac_device']} {spec_mac}. Idle until audio arrives.", flush=True)
warned = 0.0; t_active = 0.0; last_stat = 0.0; recheck_done = True
try:
    while True:
        time.sleep(0.25); now = time.time()
        playing = now - level["last_audio"] < IDLE_AFTER
        if not active and playing:
            try:
                open_outputs()
            except Exception as e:
                if now - warned > 30: print(f"audio present but cannot open outputs: {e}. Retrying.", flush=True); warned = now
                time.sleep(5); continue
            print(f"[active] audio detected; outputs open, delay {current_delay_ms():.0f} ms", flush=True)
            time.sleep(1.0)
            mode = CFG.get("sync_on_activate", "auto"); age_h = None
            try: age_h = (time.time() - time.mktime(time.strptime(LAT.get("measured", ""), "%Y-%m-%d %H:%M"))) / 3600
            except Exception: pass
            want = (mode is True) or (mode == "auto" and (age_h is None or age_h > CFG.get("sync_max_age_h", 6) or "total_pad_ms" not in LAT))
            if a.no_sync: want = False
            if want:
                sync("startup"); recheck_done = False
            else:
                enforce_total("stored"); recheck_done = True
                print(f"[active] using stored sync (measured {age_h:.1f} h ago, delay {current_delay_ms():.0f} ms); no microphone used. ./resync.sh re-measures.", flush=True)
            t_active = now
        elif not active and now - last_stat > 60:
            last_stat = now; print(f"[idle] input rms {level['rms']:.5f} ({'silence' if level['rms'] < 1e-4 else 'audio present'})", flush=True)
        elif active and not playing:
            close_outputs(); print(f"[idle] no audio for {IDLE_AFTER} s; outputs released", flush=True)
        elif active:
            if not recheck_done and now - t_active > 40:
                recheck_done = True; threading.Thread(target=sync, args=("recheck",), daemon=True).start()
            if now - last_stat > 30:
                last_stat = now
                print(f"[{int(now-t_active):5d}s] bt fill {ring_bt.fill/SR*1000:4.0f} ms mac fill {ring_mac.fill/SR*1000:4.0f} ms under bt={ring_bt.under} mac={ring_mac.under} "
                      f"ratio bt={getattr(ring_bt,'ratio',1):.5f} mac={getattr(ring_mac,'ratio',1):.5f} pad {LAT.get('total_pad_ms','-')} ms", flush=True)
            if any(not st.active for st in out_streams):
                close_outputs(); print("[idle] an output stream stopped (device gone?); will reopen when audio continues", flush=True)
except KeyboardInterrupt:
    pass
finally:
    close_outputs()
    try: in_stream.stop(); in_stream.close()
    except Exception: pass
