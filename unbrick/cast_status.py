import warnings; warnings.filterwarnings("ignore")
import pychromecast, time
import os; IP=os.environ.get("SPEAKER_IP","192.168.1.x")
cc = pychromecast.get_chromecast_from_host((IP, 8009, None, "HK Citation 200", "Office speaker"))
cc.wait(timeout=15)
s = cc.status
print("name:", cc.name, "| model:", cc.cast_info.model_name, "| type:", cc.cast_type)
print("volume:", round(s.volume_level,2), "| muted:", s.volume_muted)
print("app:", s.display_name, s.app_id, "| standby:", s.is_stand_by, "| active_input:", s.is_active_input)
mc = cc.media_controller
time.sleep(1)
print("media state:", mc.status.player_state, "| content:", mc.status.content_id)
cc.disconnect()
