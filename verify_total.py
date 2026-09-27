"""Does audio arrive when the virtual device tells apps it will? Plays a soft chirp INTO
'Citation Split' as an app would, notes the device's promised play time, hears it on the
mic. Gap near 0 = video apps that honor device latency will be in lip sync.
    ./venv/bin/python verify_total.py      (service must be running and active)
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, sounddevice as sd, time, json, os
from scipy.signal import butter, sosfiltfilt
HERE=os.path.dirname(os.path.abspath(__file__)); CFG=json.load(open(os.path.join(HERE,"config.json"))); SR=48000; devs=sd.query_devices()
OUT=[i for i,d in enumerate(devs) if d["name"]==CFG["input_device"] and d["max_output_channels"]>0][0]
MIC=[i for i,d in enumerate(devs) if d["name"]==CFG.get("mic_device","MacBook Pro Microphone")][0]
def chirp(f0,f1,L=0.08):
    t=np.arange(int(L*SR))/SR; k=np.log(f1/f0); ph=2*np.pi*f0*L/k*(np.exp(k*t/L)-1); return (np.sin(ph)*np.hanning(len(t))).astype(np.float32)
C_LO,C_HI=chirp(300,1500),chirp(4000,9000)
def heard(x,ref,band):
    xb=sosfiltfilt(butter(4,band,"bandpass",fs=SR,output="sos"),x); N=len(xb)+len(ref)
    e=np.abs(np.fft.irfft(np.fft.rfft(xb,N)*np.conj(np.fft.rfft(ref,N)),N)[:len(xb)]); i=int(np.argmax(e)); return (i/SR) if e[i]>6*np.median(e) else None
lvl=0.12; sig=np.zeros((int(1.2*SR),2),np.float32); at=int(0.4*SR)
sig[at:at+len(C_LO),0]+=lvl*C_LO; sig[at:at+len(C_HI),0]+=lvl*C_HI; sig[:,1]=sig[:,0]
rows=[]
for trial in range(3):
    pos={"i":0,"claim":None}
    def cb(out,frames,ti,status):
        i=pos["i"]; blk=sig[i:i+frames]
        if i<=at<i+frames: pos["claim"]=time.time()+(at-i)/SR+(ti.outputBufferDacTime-ti.currentTime)
        out[:len(blk)]=blk; out[len(blk):]=0; pos["i"]=i+frames
        if pos["i"]>=len(sig): raise sd.CallbackStop
    rec=sd.rec(int(2.5*SR),samplerate=SR,channels=1,device=MIC,dtype="float32"); t0=time.time(); time.sleep(0.25)
    with sd.OutputStream(device=OUT,samplerate=SR,channels=2,blocksize=512,callback=cb,dtype="float32") as s:
        while s.active: time.sleep(0.05)
    sd.wait(); x=rec[:,0]; tl,th=heard(x,C_LO,[250,1800]),heard(x,C_HI,[3500,10000])
    if tl is None or th is None or pos["claim"] is None: print(f"trial {trial+1}: chirp not heard (is the service active? volumes up?)"); continue
    gb,gm=(t0+tl-pos["claim"])*1000,(t0+th-pos["claim"])*1000; rows.append((gb,gm))
    print(f"trial {trial+1}: vs the device's promise, bluetooth {gb:+5.0f} ms, macbook {gm:+5.0f} ms   (+ = late, - = early)")
    time.sleep(0.4)
if rows:
    gb,gm=np.median([r[0] for r in rows]),np.median([r[1] for r in rows])
    print(f"RESULT: bluetooth {gb:+.0f} ms, macbook {gm:+.0f} ms, speakers {abs(gb-gm):.0f} ms apart ->",
          "lip sync OK" if abs(gb)<25 and abs(gm)<25 and abs(gb-gm)<15 else "off; ./resync.sh, or raise total_latency_ms + rebuild if consistently late")
