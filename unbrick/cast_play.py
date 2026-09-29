import warnings; warnings.filterwarnings("ignore")
import pychromecast, time, sys, subprocess, os, wave
import os; IP=os.environ.get("SPEAKER_IP","192.168.1.x"); MAC=os.environ.get("MAC_IP","192.168.1.y"); PORT=8765
files = sys.argv[1:]
srv = subprocess.Popen([sys.executable,"-m","http.server",str(PORT),"--bind",MAC,"-d","/Users/abhushan/experiments/speaker-tests"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(1)
cc = pychromecast.get_chromecast_from_host((IP, 8009, None, "HK Citation 200", "Office speaker"))
cc.wait(timeout=15)
orig = cc.status.volume_level
cc.set_volume(0.35); time.sleep(0.5)
mc = cc.media_controller
try:
    for f in files:
        with wave.open(f"/Users/abhushan/experiments/speaker-tests/{f}") as w:
            dur = w.getnframes()/w.getframerate()
        print(f"PLAYING {f} ({dur:.0f}s)", flush=True)
        mc.play_media(f"http://{MAC}:{PORT}/{f}", "audio/wav", title=f)
        mc.block_until_active(timeout=15)
        t0=time.time()
        while time.time()-t0 < dur+4:
            time.sleep(1)
            st = mc.status.player_state
            if st in ("IDLE",) and time.time()-t0>3: break
        print(f"   ended state={mc.status.player_state} after {time.time()-t0:.0f}s", flush=True)
finally:
    try: mc.stop()
    except Exception: pass
    cc.set_volume(orig); time.sleep(0.5)
    print("volume restored to", round(cc.status.volume_level,2))
    cc.disconnect(); srv.terminate()
