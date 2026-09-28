"""Measure the split at the laptop mic (close to where you sit) and write corrective EQ.

Plays ~8 s of quiet pink noise through the running split, measures third-octave
levels, compares them with a gentle target (a little bass lift, a slight
downward tilt, which most listeners prefer over ruler-flat), and writes
peaking bands into config.json: below the crossover region to the citation,
above it to the macbook. Corrections are capped at +-6 dB and smoothed.

    ./venv/bin/python autoeq.py            # measure, write, apply (./tune.sh)
    ./venv/bin/python autoeq.py --dry-run  # just show what it would do
    ./venv/bin/python autoeq.py --reset    # remove all EQ bands
The laptop mic is not a measurement mic; treat the result as a strong
starting point and adjust by ear.
"""
import warnings; warnings.filterwarnings("ignore")
import os, sys, json, subprocess, time, argparse, numpy as np, sounddevice as sd
from scipy.io import wavfile
HERE=os.path.dirname(os.path.abspath(__file__)); CFGF=os.path.join(HERE,"config.json"); CFG=json.load(open(CFGF)); SR=48000
ap=argparse.ArgumentParser(); ap.add_argument("--dry-run",action="store_true"); ap.add_argument("--reset",action="store_true")
ap.add_argument("--level",type=float,default=0.08); ap.add_argument("--seconds",type=float,default=8); a=ap.parse_args()
def save(cfg):
    json.dump(cfg,open(CFGF,"w"),indent=2); subprocess.run([os.path.join(HERE,"tune.sh")],capture_output=True)
if a.reset:
    for o in CFG["outputs"].values(): o["eq"]=[]
    save(CFG); print("EQ cleared on both outputs and applied."); sys.exit(0)
MIC=[i for i,d in enumerate(sd.query_devices()) if d["name"]==CFG.get("mic_device","MacBook Pro Microphone")][0]
TMP=os.path.join(HERE,"tmp"); os.makedirs(TMP,exist_ok=True)
# pink noise (Voss-McCartney-ish via 1/f spectrum shaping)
n=int(a.seconds*SR); w=np.random.default_rng(1).standard_normal(n); F=np.fft.rfft(w); f=np.fft.rfftfreq(n,1/SR); F[1:]/=np.sqrt(f[1:]); F[0]=0
p=np.fft.irfft(F,n); p=(a.level*p/np.abs(p).max()*3).astype(np.float32); p=np.clip(p,-a.level*4,a.level*4)
r=np.linspace(0,1,int(0.2*SR)); p[:len(r)]*=r; p[-len(r):]*=r[::-1]
wavfile.write(f"{TMP}/pink.wav",SR,(np.column_stack([p,p])*32767).astype(np.int16))
print(f"playing {a.seconds:.0f} s of quiet pink noise through the split; stay where you normally sit and keep quiet...", flush=True)
rec=sd.rec(int((a.seconds+3)*SR),samplerate=SR,channels=1,device=MIC,dtype="float32"); time.sleep(0.3)
subprocess.run(["afplay",f"{TMP}/pink.wav"]); sd.wait(); y=rec[:,0]
noise=y[:int(0.25*SR)]; sig=y[int(1.5*SR):int((a.seconds+0.5)*SR)]
def bands(x):
    X=np.abs(np.fft.rfft(x*np.hanning(len(x))))**2; fr=np.fft.rfftfreq(len(x),1/SR); out=[]
    for c in CENTERS:
        m=(fr>=c/2**(1/6))&(fr<c*2**(1/6)); out.append(10*np.log10(X[m].mean()+1e-20))
    return np.array(out)
CENTERS=np.array([63,80,100,125,160,200,250,315,400,500,630,800,1000,1250,1600,2000,2500,3150,4000,5000,6300,8000,10000,12500])
S=bands(sig); N=bands(noise); snr=S-N
# target: +3 dB below 150 Hz easing to 0 at 300, then -0.8 dB/octave tilt above 1 kHz (relative). Pink noise is flat per band.
target=np.array([3 if c<=125 else (3*(1-np.log2(c/125)/np.log2(300/125)) if c<300 else 0) for c in CENTERS]) + np.array([-0.8*np.log2(c/1000) if c>1000 else 0 for c in CENTERS])
ref=np.mean(S[(CENTERS>=400)&(CENTERS<=2000)]); dev=(S-ref)-target
valid=snr>10
corr=np.where(valid,-dev,0.0); corr=np.clip(corr,-6,6)
corr=np.convolve(corr,[0.25,0.5,0.25],"same")   # smooth across neighbours
print("\n  band   measured  target  correction  (dB, rel. 400-2k mean)")
for c,s_,t,k,v in zip(CENTERS,S-ref,target,corr,valid): print(f"  {c:6.0f} {s_:8.1f} {t:7.1f} {k:10.1f} {'' if v else '  (low SNR, skipped)'}")
# turn per-band corrections into a handful of peaking filters per output (merge runs of same sign)
def to_eq(cs,ks):
    eq=[]; i=0
    while i<len(cs):
        if abs(ks[i])<1.0: i+=1; continue
        j=i
        while j+1<len(cs) and np.sign(ks[j+1])==np.sign(ks[i]) and abs(ks[j+1])>=1.0: j+=1
        fc=float(np.sqrt(cs[i]*cs[j])); g=float(np.mean(ks[i:j+1])); bw_oct=(j-i+1)/3; q=float(np.clip(1.41/max(bw_oct,0.34),0.5,4))
        eq.append({"f":round(fc),"gain_db":round(g,1),"q":round(q,2)}); i=j+1
    return eq
split_hz=float(np.sqrt(CFG["outputs"]["macbook"].get("highpass_hz",1200)*CFG["outputs"]["citation"].get("lowpass_hz",3500)))
lo=CENTERS<split_hz; hi=~lo
eq_c=to_eq(CENTERS[lo],corr[lo]); eq_m=to_eq(CENTERS[hi],corr[hi])
print(f"\ncitation EQ (< {split_hz:.0f} Hz): {eq_c}\nmacbook  EQ (> {split_hz:.0f} Hz): {eq_m}")
if a.dry_run: print("(dry run, nothing written)"); sys.exit(0)
CFG["outputs"]["citation"]["eq"]=eq_c; CFG["outputs"]["macbook"]["eq"]=eq_m; save(CFG)
print("written to config.json and applied live. Undo: ./venv/bin/python autoeq.py --reset")
