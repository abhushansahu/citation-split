"""Quiet end-to-end check while the split is RUNNING (./start.sh elsewhere).
About 12 s total: three soft chirp pairs for alignment, then a 6 s sweep at
low level, both played with afplay so the audio goes app -> BlackHole ->
split -> both devices, recorded on the Mac mic.
    ./venv/bin/python verify_live.py
"""
import warnings; warnings.filterwarnings("ignore")
import os, json, subprocess, time, numpy as np, sounddevice as sd
from scipy.io import wavfile
from scipy.signal import butter, sosfiltfilt, stft
HERE=os.path.dirname(os.path.abspath(__file__)); SR=48000
CFG=json.load(open(os.path.join(HERE,"config.json")))
MIC=[i for i,d in enumerate(sd.query_devices()) if d["name"]==CFG.get("mic_device","MacBook Pro Microphone")][0]
TMP=os.path.join(HERE,"tmp"); os.makedirs(TMP,exist_ok=True)
LVL=0.08; SWEEP_LVL=0.06; SWEEP_S=6
def chirp(f0,f1,L=0.08):
    t=np.arange(int(L*SR))/SR; k=np.log(f1/f0); ph=2*np.pi*f0*L/k*(np.exp(k*t/L)-1); return (np.sin(ph)*np.hanning(len(t))).astype(np.float32)
C_LO,C_HI=chirp(300,1500),chirp(4000,9000)
def matched(x,ref,band,n):
    xb=sosfiltfilt(butter(4,band,"bandpass",fs=SR,output="sos"),x); N=len(xb)+len(ref)
    e=np.abs(np.fft.irfft(np.fft.rfft(xb,N)*np.conj(np.fft.rfft(ref,N)),N)[:len(xb)]); k=int(0.002*SR); e=np.convolve(e,np.ones(k)/k,"same")
    out=[]
    for _ in range(n):
        i=int(np.argmax(e))
        if e[i]<6*np.median(e): break
        out.append(i/SR); e[max(0,i-int(0.25*SR)):i+int(0.25*SR)]=0
    return sorted(out)
def play_rec(wav,secs):
    rec=sd.rec(int(secs*SR),samplerate=SR,channels=1,device=MIC,dtype="float32"); time.sleep(0.3); subprocess.run(["afplay",wav]); sd.wait(); return rec[:,0]
# 1. alignment: 3 chirp pairs, 0.6 s apart
N=3; sig=np.zeros(int(0.8+N*0.6)*SR+SR,np.float32)
for k in range(N): i=int((0.5+k*0.6)*SR); sig[i:i+len(C_LO)]+=LVL*C_LO; sig[i:i+len(C_HI)]+=LVL*C_HI
wavfile.write(f"{TMP}/live-chirps.wav",SR,(np.column_stack([sig,sig])*32767).astype(np.int16))
x=play_rec(f"{TMP}/live-chirps.wav",len(sig)/SR+1.5)
lo,hi=matched(x,C_LO,[250,1800],N),matched(x,C_HI,[3500,10000],N); d=[]
for m in hi:
    c=[v for v in lo if abs(v-m)<0.5]
    if c: d.append((min(c,key=lambda v:abs(v-m))-m)*1000)
print(f"ALIGNMENT: {len(d)} chirp pairs; bluetooth relative to MacBook: "+", ".join(f"{v:+.0f}" for v in d)+" ms")
print("   ->","in sync" if d and abs(np.median(d))<=15 else "OUT OF SYNC, run ./resync.sh" if d else "chirps not detected (volumes up? Mac near the speaker?)")
# 2. sweep, short and quiet
n=SWEEP_S*SR; k=np.log(20000/40); tt=np.arange(n)/SR; ph=2*np.pi*40*SWEEP_S/k*(np.exp(k*tt/SWEEP_S)-1)
s=(SWEEP_LVL*np.sin(ph)).astype(np.float32); f=int(0.05*SR); r=np.linspace(0,1,f); s[:f]*=r; s[-f:]*=r[::-1]
wavfile.write(f"{TMP}/live-sweep.wav",SR,(np.column_stack([s,s])*32767).astype(np.int16))
y=play_rec(f"{TMP}/live-sweep.wav",SWEEP_S+2)
# locate the sweep by matched filter, then measure each octave in the window where the sweep passes through it
N2=len(y)+len(s); c=np.abs(np.fft.irfft(np.fft.rfft(y,N2)*np.conj(np.fft.rfft(s,N2)),N2)[:len(y)]); t0=int(np.argmax(c))
floor_seg=y[:max(int(0.3*SR),t0-int(0.1*SR))] if t0>int(0.4*SR) else y[:int(0.3*SR)]
def band_db(seg,f1,f2):
    F=np.abs(np.fft.rfft(seg*np.hanning(len(seg))))**2; fr=np.fft.rfftfreq(len(seg),1/SR); m=(fr>=f1)&(fr<f2); return 10*np.log10(F[m].sum()/len(seg)+1e-20)
heard=[]; names={250:"250",500:"500",1000:"1k",2000:"2k",4000:"4k",8000:"8k",16000:"16k"}
for fc in names:
    f1,f2=fc/1.414,fc*1.414; ta=t0+int(np.log(f1/40)/k*SWEEP_S*SR); tb=t0+int(np.log(min(f2,19000)/40)/k*SWEEP_S*SR)
    if tb<=ta or tb>len(y): continue
    snr=band_db(y[ta:tb],f1,f2)-band_db(floor_seg,f1,f2)
    if snr>8: heard.append(fc)
    print(f"   {names[fc]:>4s} Hz band: {snr:5.1f} dB above room noise")
heard=set(int(np.floor(np.log2(f/31.25))) for f in heard)
print("SWEEP: octave bands heard:"," ".join(names[f] for f in names if int(np.floor(np.log2(f/31.25))) in heard))
print("   ->","response continuous through the crossover" if {5,6,7,8}<=heard else "gap in response")
