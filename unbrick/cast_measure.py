import warnings; warnings.filterwarnings("ignore")
import pychromecast, time, sys, subprocess, wave, threading
import numpy as np, sounddevice as sd
from scipy.signal import welch
from scipy.io import wavfile
IP="192.168.1.x"; MAC="192.168.1.y"; PORT=8765; SR=48000; DEV=5
TESTS=[("03-woofer-150hz.wav",150),("04-midrange-1khz.wav",1000),("05-tweeter-10khz.wav",10000),("08-pink-noise.wav",None)]
OUT="/private/tmp/claude-501/-Users-abhushan-experiments/5d917b04-c5ca-4e19-b4cf-4fea4866979e/scratchpad"

def record(sec):
    x = sd.rec(int(sec*SR), samplerate=SR, channels=1, device=DEV, dtype='float32'); sd.wait(); return x[:,0]
def band_db(x, f, bw=0.1):
    fr,P = welch(x, SR, nperseg=8192)
    m=(fr>f*(1-bw))&(fr<f*(1+bw)); return 10*np.log10(P[m].mean()+1e-20)
def octaves(x):
    fr,P = welch(x, SR, nperseg=16384); out=[]
    for c in [63,125,250,500,1000,2000,4000,8000,16000]:
        m=(fr>c/1.414)&(fr<c*1.414); out.append((c,10*np.log10(P[m].sum()+1e-20)))
    return out

srv = subprocess.Popen([sys.executable,"-m","http.server",str(PORT),"--bind",MAC,"-d","/Users/abhushan/experiments/speaker-tests"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(1)
print("mic check: recording 3s room silence...", flush=True)
base = record(3); wavfile.write(f"{OUT}/rec-silence.wav", SR, base)
print(f"   rms {20*np.log10(np.sqrt((base**2).mean())+1e-12):.1f} dBFS", flush=True)

cc = pychromecast.get_chromecast_from_host((IP, 8009, None, "HK Citation 200", "Office speaker")); cc.wait(timeout=15)
orig = cc.status.volume_level
if cc.status.app_id and cc.status.app_id!="CC1AD845": cc.quit_app(); time.sleep(3)
cc.set_volume(0.45); time.sleep(0.3)
mc = cc.media_controller; mc.launch()
for _ in range(12):
    time.sleep(1)
    if cc.status.app_id=="CC1AD845": break
print("receiver:", cc.status.app_id, cc.status.display_name, flush=True)
res={}
try:
    for f,freq in TESTS:
        with wave.open(f"/Users/abhushan/experiments/speaker-tests/{f}") as w: dur=w.getnframes()/w.getframerate()
        print(f"PLAY+REC {f}", flush=True)
        buf={}
        th=threading.Thread(target=lambda: buf.__setitem__('x', record(dur+2))); th.start()
        mc.play_media(f"http://{MAC}:{PORT}/{f}", "audio/wav", title=f)
        th.join(); print("   state:", mc.status.player_state, mc.status.idle_reason, flush=True); x=buf['x'][int(2.5*SR):]   # skip lead-in
        wavfile.write(f"{OUT}/rec-{f}", SR, x); res[f]=x
        try: mc.stop()
        except Exception: pass
        time.sleep(1.5)
finally:
    cc.set_volume(orig); cc.disconnect(); srv.terminate()

print("\n=== RESULTS (mic on MacBook, dB relative, higher = louder) ===")
for f,freq in TESTS:
    if freq:
        print(f"{freq:>6} Hz tone : signal {band_db(res[f],freq):6.1f} dB   room-noise at same freq {band_db(base,freq):6.1f} dB   -> SNR {band_db(res[f],freq)-band_db(base,freq):5.1f} dB")
print("\nPink noise octave bands (flat speaker + flat mic would be roughly flat; 63 Hz rolls off on a small speaker):")
pn=octaves(res["08-pink-noise.wav"]); bn=octaves(base)
ref=[v for c,v in pn if c==1000][0]
for (c,v),(_,n) in zip(pn,bn):
    print(f"  {c:>5} Hz : {v-ref:+6.1f} dB rel 1k   (noise floor {n-ref:+6.1f})")
