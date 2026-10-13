import json
import os
from pathlib import Path

DEFAULTS = dict(method='auto', quality=85, codec='H.264', engine='gpu',
                audio='AAC', audio_kbps=192, preset='fast', resolution=0, profile='maximum',
                compare='wipe', fit=True, recommend=True, output_dir='',
                target_mb=500.0, bitrate=2000, last_dir='', last_output='', container='mp4', min_vmaf=95.0, schema=3)

def config_path():
    override = os.environ.get('SJVID_CONFIG')
    return Path(override) if override else Path(os.environ.get('APPDATA', str(Path.home()))) / 'sjvid2' / 'settings.json'

def load():
    cfg = DEFAULTS.copy()
    try:
        data = json.loads(config_path().read_text(encoding='utf-8'))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in cfg and type(v) is type(cfg[k])})
    except (OSError, ValueError):
        pass
    return cfg

def save(cfg):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, p)
