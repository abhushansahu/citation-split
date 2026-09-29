"""Two-way active crossover across two output devices, with in-session sync.

Reads system audio from a virtual input device (BlackHole), splits it at
the crossover with a 4th-order Linkwitz-Riley filter, and sends:

  low band  (mono)   -> the Bluetooth speaker   (whose tweeter is dead)
  high band (stereo) -> the MacBook speakers, delayed to match Bluetooth latency

Bluetooth latency differs every time the stream is opened (tens of ms) and
and then drifts slowly, so the delay is measured *inside* this process, from
the music itself: it keeps a rolling copy of what each output was sent,
records the Mac mic, and correlates the two in a band only that output
reaches, which gives both paths' lags from one recording. Their difference
is the offset, and it shifts its delay buffer until that is near zero --
adding no sound of its own. Quiet or narrow material falls back to a pair
of quiet chirps. It re-measures while playing: first after two minutes,
then backing off as long as nothing has moved. SIGUSR1 (./resync.sh) forces
one.

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
def load_config():
    """config.json (tracked) with config.local.json (git-ignored: your own device addresses) laid over it."""
    c = json.load(open(os.path.join(HERE, "config.json")))
    lp = os.path.join(HERE, "config.local.json")
    if os.path.exists(lp): c.update(json.load(open(lp)))
    return c
CFG = load_config()
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

class Limiter:
    """Per-output peak limiter: instant attack, ~150 ms release. Lets gain_db go above 0 without hard clipping."""
    def __init__(self, ceiling=0.97):
        self.ceil = ceiling; self.g = 1.0; self.rel = np.exp(-BLOCK / (0.15 * SR))
    def __call__(self, y):
        peak = float(np.max(np.abs(y))) if y.size else 0.0
        need = self.ceil / peak if peak > self.ceil else 1.0
        if need < self.g: self.g = need                      # attack now
        else: self.g = need + (self.g - need) * self.rel     # release toward what this block allows
        if self.g < 0.999: y = y * self.g
        return np.clip(y, -1.0, 1.0)

class Chain:
    def __init__(self, spec, ch):
        parts = []
        if spec.get("lowpass_hz"):  parts.append(butter(spec.get("lowpass_order", 2), spec["lowpass_hz"], "lowpass", fs=SR, output="sos"))
        if spec.get("highpass_hz"): parts.append(butter(spec.get("highpass_order", 2), spec["highpass_hz"], "highpass", fs=SR, output="sos"))
        for e in spec.get("eq", []): parts.append(peaking(e["f"], e["gain_db"], e.get("q", 1.0)))
        self.sos = np.vstack(parts) if parts else np.array([[1, 0, 0, 1, 0, 0]], float)
        self.z = np.stack([sosfilt_zi(self.sos)] * ch, axis=-1) * 0
        self.g = 10 ** (spec.get("gain_db", 0) / 20); self.lim = Limiter(spec.get("ceiling", 0.97))
    def __call__(self, x):
        y, self.z = sosfilt(self.sos, x, axis=0, zi=self.z)
        return self.lim(y * self.g)

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
        CFG = load_config()
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
    def reset(self, target=None):
        """Fresh start: fill to a base target (drops any pad / sync shift from a previous activation)."""
        with self.lock:
            if target is not None: self.target = int(target)
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
    tap_push(lo, hi)
    ring_bt.push(lo); ring_mac.push(hi)

# --- tap: a rolling copy of exactly what each output was sent ------------
# This is the probe. The program audio already goes out both paths, so the mic
# can be correlated against what we sent instead of against a chirp we add.
# Both taps advance on one counter, so whatever error there is in lining the
# mic recording up with them is identical for the two paths and cancels when
# their lags are subtracted -- and the difference is the only thing sync needs.
TAP_S = 10.0
tap_n = int(TAP_S * SR)
tap_lo = np.zeros(tap_n, np.float32); tap_hi = np.zeros(tap_n, np.float32); tap_w = 0
tap_lock = threading.Lock()
def tap_push(lo, hi):
    global tap_w
    with tap_lock:
        k = len(lo); i = np.arange(tap_w, tap_w + k) % tap_n
        tap_lo[i] = lo[:, 0]; tap_hi[i] = hi.mean(axis=1); tap_w += k
def tap_read(n):
    """The last n samples sent to each output, and the absolute index they end at."""
    with tap_lock:
        if tap_w < n or n > tap_n: return None, None, 0
        i = np.arange(tap_w - n, tap_w) % tap_n
        return np.array(tap_lo[i]), np.array(tap_hi[i]), tap_w

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
    bt_index()
    for sr in (SR, bt_sr):
        try:
            s = sd.OutputStream(device=bt_index(), samplerate=sr, channels=2, dtype="float32", blocksize=BLOCK, callback=bt_cb)
            bt_sr = sr; return s
        except Exception as e: last = e
    raise RuntimeError(f"cannot open Bluetooth output: {last}")

# --- in-session sync -------------------------------------------------------
# Soft chirps (not loud pure tones), found by matched filtering. Low chirp ->
# Bluetooth path, high chirp -> MacBook path, injected at the same instant.
#
# Both bands sit at the top of their path -- the low one just under the
# citation's lowpass, the high one near the top of hearing -- because that is
# where a chirp is hardest to notice. Narrow bands blunt the matched filter's
# peak, so CH_LEN pays it back: detection energy is level^2 * length, and 250 ms
# buys back more than the narrower band and the lower level cost. Each
# measurement logs its margin (correlation peak over background); if that runs
# near MARGIN, widen chirp_band_lo or raise beep_level in config.json.
CH_LEN = float(CFG.get("chirp_len_s", 0.25))
BAND_LO = tuple(CFG.get("chirp_band_lo", [2600, 3400]))
BAND_HI = tuple(CFG.get("chirp_band_hi", [9000, 14000]))
MARGIN = 6.0
def _chirp(f0, f1):
    t = np.arange(int(CH_LEN * SR)) / SR; k = np.log(f1 / f0)
    ph = 2 * np.pi * f0 * CH_LEN / k * (np.exp(k * t / CH_LEN) - 1)
    return (np.sin(ph) * np.hanning(len(t))).astype(np.float32)
CHIRP_LO, CHIRP_HI = _chirp(*BAND_LO), _chirp(*BAND_HI)
def _search_band(b):
    """Detection bandpass: just wider than the chirp, inside Nyquist. Every Hz of
    slack past the chirp's own band only adds noise to the correlation."""
    return [max(20.0, b[0] * 0.92), min(SR / 2 - 500.0, b[1] * 1.08)]
SEARCH_LO, SEARCH_HI = _search_band(BAND_LO), _search_band(BAND_HI)

def _matched_onsets(x, ref, band, n_expect):
    """Onsets of up to n_expect copies of ref in x, each with its margin over the
    background. A margin near MARGIN means the chirp was only just heard."""
    sos = butter(4, band, "bandpass", fs=SR, output="sos"); xb = sosfiltfilt(sos, x)
    n = len(xb) + len(ref); c = np.fft.irfft(np.fft.rfft(xb, n) * np.conj(np.fft.rfft(ref, n)), n)[: len(xb)]
    # Smooth over about one peak width, not a fixed 2 ms: a matched filter's peak is
    # ~1/bandwidth wide, so a window wider than that flattens the peak and not the
    # background -- it cost the wideband high chirp ~10x of its margin.
    k = int(max(1, min(0.002 * SR, SR / (band[1] - band[0]))))
    e = np.abs(c); e = np.convolve(e, np.ones(k) / k, "same")
    bg = float(np.median(e)) + 1e-20        # taken before any peak is blanked out
    out = []
    for _ in range(n_expect):
        i = int(np.argmax(e))
        if e[i] < MARGIN * bg: break
        out.append((i / SR, float(e[i]) / bg)); lo, hi = max(0, i - int(0.25 * SR)), i + int(0.25 * SR); e[lo:hi] = 0
    return sorted(out)

def measure_offset(nb=2, gap=0.6):
    """Inject nb chirp pairs, record the mic, return median (bt - mac) seconds."""
    mic_i = find(CFG.get("mic_device", "MacBook Pro Microphone"), "input")
    lvl = float(CFG.get("beep_level", a.beep_level))   # live, so ./tune.sh can change it
    dur = 0.5 + nb * gap + current_delay_ms() / 1000 + 0.6
    rec = sd.rec(int(dur * SR), samplerate=SR, channels=1, device=mic_i, dtype="float32")
    time.sleep(0.4)
    for k in range(nb):
        with inject_lock: inject.append([(lvl * CHIRP_LO)[:, None], np.column_stack([lvl * CHIRP_HI] * 2), 0])
        time.sleep(gap)
    sd.wait(); x = rec[:, 0]
    p_lo = _matched_onsets(x, CHIRP_LO, SEARCH_LO, nb)
    p_hi = _matched_onsets(x, CHIRP_HI, SEARCH_HI, nb)
    o_lo = [t for t, _ in p_lo]; o_hi = [t for t, _ in p_hi]
    mg = min([v for _, v in p_lo + p_hi], default=0.0)   # weakest of the two paths
    d = []
    for m in o_hi:
        c = [b for b in o_lo if abs(b - m) < 0.5]
        if c: d.append(min(c, key=lambda b: abs(b - m)) - m)
    d = [v for v in d if np.isfinite(v)]
    if not d: return None, 0, mg
    med = float(np.median(d)); inl = [v for v in d if abs(v - med) < 0.02]
    if not inl or not np.isfinite(np.median(inl)): return None, 0, mg
    return float(np.median(inl)), len(inl), mg

# --- passive sync: correlate the mic against the program audio -------------
# Each path owns a band the other barely reaches: below the MacBook's highpass
# only the citation plays, above the citation's lowpass only the MacBook does.
# Correlating the mic against the tap inside each of those bands gives each
# path's acoustic lag, and their difference is the offset -- with no added sound.
def _ana_bands():
    o = CFG.get("outputs", {})
    hp = o.get("macbook", {}).get("highpass_hz", a.crossover) or a.crossover
    lp = o.get("citation", {}).get("lowpass_hz", a.crossover) or a.crossover
    lo = CFG.get("passive_band_lo") or [120.0, max(250.0, hp * 0.6)]
    hi = CFG.get("passive_band_hi") or [min(lp * 1.6, 9000.0), 12000.0]
    return [float(lo[0]), float(lo[1])], [float(hi[0]), float(hi[1])]

def _band_lag(mic_w, ref, band, maxlag, nfft):
    """Lag in samples by which mic_w trails ref, from the cross-spectrum inside
    band, phase-whitened so the peak is sharp regardless of program spectrum.
    Returns (lag, confidence = peak over background)."""
    f = np.fft.rfftfreq(nfft, 1 / SR)
    keep = (f >= band[0]) & (f <= band[1])
    if keep.sum() < 8: return None, 0.0
    C = np.fft.rfft(mic_w, nfft) * np.conj(np.fft.rfft(ref, nfft))
    C = np.where(keep, C / (np.abs(C) + 1e-12), 0)
    c = np.abs(np.fft.irfft(C, nfft))[: maxlag + 1]
    k = int(max(1, min(0.002 * SR, SR / (band[1] - band[0]))))
    c = np.convolve(c, np.ones(k) / k, "same")
    i = int(np.argmax(c)); bg = float(np.median(c)) + 1e-20
    return i, float(c[i]) / bg

PASSIVE_MIN_CONF = 8.0
def measure_offset_passive(secs=None, maxlag_s=1.5):
    """Listen to the program audio on the mic and return (bt - mac) seconds."""
    secs = float(secs or CFG.get("passive_secs", 4.0))
    band_lo, band_hi = _ana_bands()
    mic_i = find(CFG.get("mic_device", "MacBook Pro Microphone"), "input")
    nm = int(secs * SR); L = int(maxlag_s * SR)
    rec = sd.rec(nm, samplerate=SR, channels=1, device=mic_i, dtype="float32")
    sd.wait()
    ref_lo, ref_hi, _ = tap_read(nm + L)      # taken after the recording, so it covers it
    if ref_lo is None: return None, 0.0, 0.0
    m = rec[:, 0]
    if float(np.sqrt(np.mean(m * m))) < 1e-4: return None, 0.0, 0.0
    mic_w = np.zeros(nm + L, np.float32); mic_w[L:] = m    # same window as the tap
    nfft = 1 << int(np.ceil(np.log2(2 * (nm + L))))
    d_lo, c_lo = _band_lag(mic_w, ref_lo, band_lo, L, nfft)
    d_hi, c_hi = _band_lag(mic_w, ref_hi, band_hi, L, nfft)
    if d_lo is None or d_hi is None: return None, 0.0, 0.0
    conf = min(c_lo, c_hi)
    if conf < PASSIVE_MIN_CONF: return None, conf, 0.0
    return (d_lo - d_hi) / SR, conf, min(d_lo, d_hi) / SR

def measure_claim_gap(n=3):
    """Play a chirp INTO the virtual device (as an app would), note when the device claims it will
    play (DAC time), and hear it on the mic. Returns median (claim - heard) seconds: >0 means we
    play EARLY versus what apps are told, so both paths need that much more delay."""
    out_i = find(in_name, "output"); mic_i = find(CFG.get("mic_device", "MacBook Pro Microphone"), "input")
    lvl = float(CFG.get("beep_level", a.beep_level)) * 1.5
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
        o = [t for t, _ in _matched_onsets(x, CHIRP_HI, SEARCH_HI, 1)]
        if o and pos["claim"]: gaps.append(pos["claim"] - (t_rec + o[0]))
        time.sleep(0.3)
    if len(gaps) < 2: return None
    med = float(np.median(gaps)); inl = [g for g in gaps if abs(g - med) < 0.02]
    return float(np.median(inl)) if len(inl) >= 2 else None

applied_pad = 0.0
def enforce_total(tag):
    """Keep app-to-ear equal to the device's promise. The mic measurement gives the pad needed at one
    sync delay; if a later sync moves the delay by dD (both paths shift together), the pad needed is
    smaller by dD. So: measure once, then derive; re-measure on resync."""
    global applied_pad
    if not CFG.get("total_latency_ms"): return
    # The pad is derived from how far the delay has moved, so re-measuring it is a
    # refinement -- and the only thing left that has to make a sound. Opt in.
    stale = "total_pad_ms" not in LAT or "pad_at_delay_ms" not in LAT
    if stale or (tag == "resync" and CFG.get("pad_remeasure_on_resync", False)):
        g = measure_claim_gap()
        if g is None:
            print(f"[{tag}] could not measure app-to-ear gap (chirp not heard)", flush=True)
            if "total_pad_ms" not in LAT: return
        else:
            LAT["total_pad_ms"] = round(g * 1000 + applied_pad, 1); LAT["pad_at_delay_ms"] = round(current_delay_ms(), 1)
            json.dump(LAT, open(LATF, "w"), indent=2)
            print(f"[{tag}] measured: pad {LAT['total_pad_ms']:.0f} ms needed at delay {LAT['pad_at_delay_ms']:.0f} ms", flush=True)
    want = LAT["total_pad_ms"] - (current_delay_ms() - LAT["pad_at_delay_ms"])
    if want < 0:
        print(f"[{tag}] WARNING: Bluetooth is {-want:.0f} ms slower than total_latency_ms allows; audio will be late vs video. Raise total_latency_ms in config.json, ./build-driver.sh, sudo ./install-driver.sh", flush=True)
        want = 0.0
    delta = want - applied_pad
    if abs(delta) >= 2:
        ring_bt.shift(delta / 1000 * SR); ring_mac.shift(delta / 1000 * SR); applied_pad = want
        print(f"[{tag}] pad now {want:.0f} ms (both paths) so audio lands when apps expect it", flush=True)

def settled(timeout=45):
    """Wait until both buffer servos are near target, so a measurement is not taken on a transient."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if getattr(ring_bt, "pulls", 0) > 300 and abs(ring_bt.err_s) < 0.003 * SR and abs(ring_mac.err_s) < 0.003 * SR: return True
        time.sleep(0.5)
    return False

# Only one measurement at a time. Two overlapping syncs inject chirps into the
# same recording, so each one pairs the other's onsets and "measures" nonsense;
# the log has a session where a manual resync landed on top of the startup sync
# and drove the delay 262 -> 326 -> 161 ms chasing phantom offsets.
sync_lock = threading.Lock()
last_sync = {"shift_ms": 0.0, "rounds": 0, "margin": 0.0, "detected": False, "ok": False}

def sync(tag="sync"):
    if not active: print(f"[{tag}] skipped: outputs idle", flush=True); return False
    if not sync_lock.acquire(blocking=False):
        print(f"[{tag}] skipped: a measurement is already running", flush=True); return False
    try: return _sync(tag)
    finally: sync_lock.release()

def _sync(tag):
    global muted
    if not settled(): print(f"[{tag}] buffers not settled after 45 s; measuring anyway", flush=True)
    # "passive" reads the program audio and adds nothing audible; "chirp" always
    # injects; "auto" (default) is passive, dropping to chirps only when the
    # material gives no usable fix -- silence, or nothing in one path's band.
    method = CFG.get("sync_method", "auto")
    use_chirp = (method == "chirp")
    mute_mode = CFG.get("sync_mute", "auto")
    ok = False; rounds = 0; shifted = 0.0; margin = 0.0; detected = False
    try:
        for rnd in range(5):
            rounds = rnd + 1
            if use_chirp:
                # chirps are the only case that needs the programme out of the way
                muted = (mute_mode is True) or (mute_mode == "auto" and tag != "auto")
                time.sleep(0.2)
                try: off, n, mg = measure_offset()
                finally: muted = False
                how = f"{n} beeps, margin {mg:.1f}x"
                if off is None or not np.isfinite(off):
                    print(f"[{tag}] round {rounds}: chirps not detected (best margin {mg:.1f}x, need {MARGIN:.0f}x); "
                          f"Mac within ~1 m of the speaker? volumes up? (beep_level in config.json)", flush=True); continue
            else:
                off, mg, _ = measure_offset_passive()
                how = f"program audio, confidence {mg:.1f}x"
                if off is None or not np.isfinite(off):
                    tail = "; falling back to chirps" if method == "auto" else ""
                    print(f"[{tag}] round {rounds}: no usable fix from the programme "
                          f"(confidence {mg:.1f}x, need {PASSIVE_MIN_CONF:.0f}x){tail}", flush=True)
                    if method == "auto": use_chirp = True
                    continue
            margin = mg
            # the servos will still remove their current smoothed fill errors, so correct for where the paths will REST
            off = off - (ring_bt.err_s - ring_mac.err_s) / SR
            if not np.isfinite(off): continue
            detected = True
            print(f"[{tag}] round {rounds}: bluetooth {off*1000:+.0f} ms vs MacBook (at rest) at delay {current_delay_ms():.0f} ms ({how})", flush=True)
            if abs(off) <= 0.008: ok = True; break
            ring_mac.shift(off * SR)      # BT late -> more Mac delay; BT early -> less
            shifted += off * 1000
    except Exception as e:
        print(f"[{tag}] error during measurement: {e!r}", flush=True)
    finally:
        muted = False    # never leave program audio muted, whatever happened
    last_sync.update(shift_ms=shifted, rounds=rounds, margin=margin, detected=detected, ok=ok)
    if ok:
        LAT["delay_ms"] = round(current_delay_ms(), 1); LAT["measured"] = time.strftime("%Y-%m-%d %H:%M"); json.dump(LAT, open(LATF, "w"), indent=2)
        print(f"[{tag}] locked: MacBook delayed {current_delay_ms():.0f} ms (saved as next start value)", flush=True)
        enforce_total(tag)
    else:
        print(f"[{tag}] could not lock; running with delay {current_delay_ms():.0f} ms", flush=True)
    return ok

# --- run -------------------------------------------------------------------
out_streams = []; mac_lat = 0.0; in_lat = 0.0; in_name = a.input or CFG["input_device"]; T_START = time.time()
import subprocess
def bt_connected():
    """Is the speaker present as a CoreAudio output? (A2DP connected <=> device exists). Needs no Bluetooth permission."""
    r = subprocess.run(["/opt/homebrew/bin/SwitchAudioSource", "-a", "-t", "output"], capture_output=True, text=True)
    return CFG["bt_device"] in r.stdout.splitlines()

def bt_connect():
    addr = CFG.get("bt_address")
    if not addr: return False
    try:
        r = subprocess.run(["/opt/homebrew/bin/blueutil", "--connect", addr], capture_output=True, text=True, timeout=20)
        if r.returncode != 0: print(f"[bt] connect attempt failed: {r.stderr.strip()[:160]}", flush=True)
    except subprocess.TimeoutExpired:
        print("[bt] connect attempt timed out", flush=True)
    for _ in range(10):
        time.sleep(0.5)
        if bt_connected(): return True
    return False

mac_only = False
def open_outputs():
    global out_streams, mac_lat, active, applied_pad
    applied_pad = 0.0
    ring_bt.reset(safety); ring_mac.reset(int(LAT.get("delay_ms", a.delay_ms) / 1000 * SR) + safety)
    bt = open_bt(); mac = sd.OutputStream(device=mac_i, samplerate=SR, channels=2, dtype="float32", blocksize=BLOCK, callback=mac_cb)
    mac_lat = mac.latency
    print(f"[active] reported stream latency: bluetooth {bt.latency*1000:.0f} ms, macbook {mac.latency*1000:.0f} ms", flush=True)
    v = CFG.get("volumes")
    if v:
        for dev, key in ((CFG["bt_device"], "bt"), (CFG["mac_device"], "mac")):
            subprocess.run([os.path.join(HERE, "app", "setvol"), dev, str(v[key])], capture_output=True)
    bt.start(); mac.start(); out_streams = [bt, mac]; active = True
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
in_name = a.input or CFG["input_device"]
in_stream = None
def open_input():
    global in_stream, in_lat
    while in_stream is None:
        try:
            in_stream = sd.InputStream(device=find(in_name, "input"), samplerate=SR, channels=2, dtype="float32", blocksize=BLOCK, callback=in_cb)
        except LookupError as e:
            print(f"waiting: {e} (is the driver installed?)", flush=True); time.sleep(10)
    in_stream.start(); in_lat = in_stream.latency
def reenumerate():
    """PortAudio's device table is frozen at init; a reconnected Bluetooth device has a new identity. Rebuild it (idle only)."""
    global in_stream, mac_i
    try: in_stream.stop(); in_stream.close()
    except Exception: pass
    in_stream = None; sd._terminate(); sd._initialize(); mac_i = find(CFG["mac_device"], "output"); open_input()
def current_output():
    return subprocess.run(["/opt/homebrew/bin/SwitchAudioSource", "-c", "-t", "output"], capture_output=True, text=True).stdout.strip()
def set_output(name):
    subprocess.run(["/opt/homebrew/bin/SwitchAudioSource", "-s", name, "-t", "output"], capture_output=True)
open_input()
signal.signal(signal.SIGUSR2, reload_config)
signal.signal(signal.SIGUSR1, lambda *_: active and threading.Thread(target=sync, args=("resync",), daemon=True).start())
print(f"split service: input '{in_name}' -> {CFG['bt_device']} {spec_bt} | {CFG['mac_device']} {spec_mac}. Idle until audio arrives.", flush=True)
# The Bluetooth stack's own delay drifts mid-session -- tens of ms over minutes --
# and nothing we can see locally reveals it: through a +41 ms drift the bt ring
# sat at 373-384 ms fill with ratio 1.00000 and no underruns, because the servo
# locks the ring's fill and the drift happens downstream of it. A mic measurement
# is the only ground truth, so the check is acoustic, and it backs off while
# things are stable to keep it cheap.
AUTO_FIRST = float(CFG.get("sync_check_s", 120))          # 0 disables the automatic check
AUTO_MAX = float(CFG.get("sync_check_max_s", 900))
AUTO_LOUD_RMS = float(CFG.get("sync_defer_rms", 0.10))
auto_interval = AUTO_FIRST; next_auto = 0.0; auto_due = 0.0

def auto_check():
    """Measure; tighten the cadence if it had to correct, relax it if it did not."""
    global auto_interval, next_auto
    sync("auto")
    if not last_sync["detected"]:
        next_auto = time.time() + 60          # never heard it: retry soon rather than back off
        return
    if abs(last_sync["shift_ms"]) >= 8: auto_interval = AUTO_FIRST
    else: auto_interval = min(auto_interval * 2, AUTO_MAX)
    next_auto = time.time() + auto_interval
    print(f"[auto] next drift check in {auto_interval/60:.0f} min", flush=True)

warned = 0.0; t_active = 0.0; last_stat = 0.0; recheck_done = True
speaker_present = bt_connected(); auto_switched = False; last_present_check = 0.0; last_connect_try = 0.0
try:
    while True:
        time.sleep(0.25); now = time.time()
        # --- speaker presence: follow it with the system output ---------------------------------
        if now - last_present_check > 2:
            last_present_check = now; present = bt_connected()
            if present != speaker_present:
                speaker_present = present
                if not present:
                    if active: close_outputs()
                    if current_output() == in_name:
                        set_output(CFG["mac_device"]); auto_switched = True
                        print(f"[bt] speaker gone; system output -> {CFG['mac_device']}", flush=True)
                    else: print("[bt] speaker gone", flush=True)
                else:
                    if not active: reenumerate()
                    if auto_switched:
                        set_output(in_name); auto_switched = False
                        print(f"[bt] speaker back; system output -> {in_name}", flush=True)
                    else: print("[bt] speaker back", flush=True)
            elif not present:
                if current_output() == in_name:      # user picked the split while the speaker is away
                    set_output(CFG["mac_device"]); auto_switched = True
                    print(f"[bt] speaker not connected; system output -> {CFG['mac_device']} until it is", flush=True)
                if auto_switched and now - last_connect_try > 60:
                    last_connect_try = now; bt_connect()   # harmless if the speaker is off
        playing = now - level["last_audio"] < IDLE_AFTER
        if not active and playing and speaker_present:
            try:
                open_outputs()
            except Exception as e:
                if now - warned > 30: print(f"audio present but cannot open outputs: {e}. Retrying.", flush=True); warned = now
                time.sleep(5); continue
            print(f"[active] audio detected; outputs open, delay {current_delay_ms():.0f} ms", flush=True)
            time.sleep(1.0)
            mode = CFG.get("sync_on_activate", True); age_h = None
            try: age_h = (time.time() - time.mktime(time.strptime(LAT.get("measured", ""), "%Y-%m-%d %H:%M"))) / 3600
            except Exception: pass
            want = (mode is True) or (mode == "auto" and (age_h is None or age_h > CFG.get("sync_max_age_h", 6) or "total_pad_ms" not in LAT))
            if a.no_sync: want = False
            if want:
                sync("startup")
                # a startup that locked on the first round needs no 40 s recheck;
                # the adaptive check below covers it, with fewer chirps
                recheck_done = last_sync["ok"] and last_sync["rounds"] <= 1
            else:
                enforce_total("stored"); recheck_done = True
                print(f"[active] using stored sync (delay {current_delay_ms():.0f} ms); ./resync.sh re-measures.", flush=True)
            t_active = now
            auto_interval = AUTO_FIRST; next_auto = now + AUTO_FIRST; auto_due = 0.0
        elif not active and now - last_stat > 60:
            last_stat = now; print(f"[idle] input rms {level['rms']:.5f} ({'silence' if level['rms'] < 1e-4 else 'audio present'})", flush=True)
        elif active and not playing:
            close_outputs(); print(f"[idle] no audio for {IDLE_AFTER} s; outputs released", flush=True)
        elif active:
            if not recheck_done and now - t_active > 40:
                recheck_done = True; threading.Thread(target=sync, args=("recheck",), daemon=True).start()
            elif AUTO_FIRST > 0 and now >= next_auto and not sync_lock.locked():
                if auto_due == 0.0: auto_due = now
                if (CFG.get("sync_method", "auto") == "chirp" and level["rms"] > AUTO_LOUD_RMS
                        and now - auto_due < 120):
                    next_auto = now + 15          # loud passage: wait for a quieter one, but not forever
                else:
                    auto_due = 0.0; next_auto = now + auto_interval
                    threading.Thread(target=auto_check, daemon=True).start()
            if now - last_stat > 30:
                last_stat = now
                print(f"[{int(now-t_active):5d}s] bt fill {ring_bt.fill/SR*1000:4.0f} ms mac fill {ring_mac.fill/SR*1000:4.0f} ms under bt={ring_bt.under} mac={ring_mac.under} "
                      f"ratio bt={getattr(ring_bt,'ratio',1):.5f} mac={getattr(ring_mac,'ratio',1):.5f} pad {applied_pad:.0f} ms", flush=True)
            if any(not st.active for st in out_streams):
                close_outputs(); print("[idle] an output stream stopped; will reopen when audio continues", flush=True)
except KeyboardInterrupt:
    pass
finally:
    close_outputs()
    try: in_stream.stop(); in_stream.close()
    except Exception: pass
