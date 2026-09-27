import warnings; warnings.filterwarnings("ignore")
import pychromecast, time, sys, subprocess, wave, threading
import numpy as np, sounddevice as sd
from scipy.io import wavfile
from scipy.signal import stft
IP="192.168.1.x"; MAC="192.168.1.y"; PORT=8765; SR=48000; DEV=5; F="06-sweep-20hz-20khz.wav"
OUT="/private/tmp/claude-501/-Users-abhushan-experiments/5d917b04-c5ca-4e19-b4cf-4fea4866979e/scratchpad"
srv=subprocess.Popen([sys.executable,"-m","http.server",str(PORT),"--bind",MAC,"-d","/Users/abhushan/experiments/speaker-tests"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(1)
with wave.open(f"/Users/abhushan/experiments/speaker-tests/{F}") as w: dur=w.getnframes()/w.getframerate()
cc=pychromecast.get_chromecast_from_host((IP,8009,None,"HK Citation 200","Office speaker")); cc.wait(timeout=15)
orig=cc.status.volume_level
if cc.status.app_id!="CC1AD845": cc.quit_app(); time.sleep(3)
cc.set_volume(0.45); mc=cc.media_controller; mc.launch()
for _ in range(12):
    time.sleep(1)
    if cc.status.app_id=="CC1AD845": break
buf={}
th=threading.Thread(target=lambda: buf.__setitem__('x', sd.rec(int((dur+3)*SR),samplerate=SR,channels=1,device=DEV,dtype='float32'))); th.start()
mc.play_media(f"http://{MAC}:{PORT}/{F}","audio/wav",title="sweep"); th.join(); sd.wait()
x=buf['x'][:,0]; wavfile.write(f"{OUT}/rec-sweep.wav",SR,x)
try: mc.stop()
except Exception: pass
cc.set_volume(orig); cc.disconnect(); srv.terminate()
fr,t,Z=stft(x,SR,nperseg=4096,noverlap=2048); P=np.abs(Z)**2
env=P.sum(0); noise=env[:int(1/(t[1]-t[0]))].mean()
on=np.argmax(env>noise*10); t0=t[on]
print(f"sweep {dur:.0f}s log 20Hz-20kHz; onset at {t0:.2f}s in recording. Expected freq = 20*1000^(t/30).")
print(f"noise floor per bin ~ {10*np.log10(noise/len(fr)+1e-20):.1f} dB")
for tt in np.arange(t0, t0+dur, 1.0):
    i=np.argmin(abs(t-tt)); k=P[:,i].argmax(); exp=20*1000**((tt-t0)/dur)
    print(f"  t+{tt-t0:4.1f}s expect {exp:7.0f} Hz | measured peak {fr[k]:7.0f} Hz at {10*np.log10(P[k,i]+1e-20):6.1f} dB")
