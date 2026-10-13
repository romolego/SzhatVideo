from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

def binary(name):
    for root in [Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)), Path(sys.executable).parent]:
        p = root / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))
        if p.is_file():
            return str(p)
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f'Не найден {name}. Положите его в папку bin рядом с приложением.')
    return p

def run(args, timeout=60):
    p = subprocess.run(args, capture_output=True, encoding='utf-8', errors='replace',
                       creationflags=NO_WINDOW, timeout=timeout)
    if p.returncode:
        raise RuntimeError('\n'.join(p.stderr.strip().splitlines()[-12:]) or 'Ошибка FFmpeg')
    return p

@dataclass
class Media:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    size: int
    video_codec: str
    audio_codec: str
    audio_kbps: int
    has_audio: bool
    ten_bit: bool = False
    hdr: bool = False
    rotation: int = 0

    @property
    def mb(self):
        return self.size / 1024**2

def probe(path):
    data = json.loads(run([binary('ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]).stdout)
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise RuntimeError('В файле нет видеодорожки.')
    v = videos[0]
    a = next((s for s in data['streams'] if s.get('codec_type') == 'audio'), {})
    fmt = data.get('format', {})
    dur = float(fmt.get('duration') or v.get('duration') or 0)
    if not math.isfinite(dur) or dur <= 0:
        raise RuntimeError('Не удалось определить длительность видео.')
    rate = v.get('avg_frame_rate', '25/1')
    try:
        num, den = map(float, rate.split('/'))
        fps = num / den if den else 25
    except (ValueError, ZeroDivisionError):
        fps = 25
    rotation = next((int(s.get('rotation', 0)) for s in v.get('side_data_list', []) if 'rotation' in s), 0)
    w, h = v['width'], v['height']
    if abs(rotation) % 180 == 90:
        w, h = h, w
    return Media(str(path), dur, w, h, fps, Path(path).stat().st_size, v.get('codec_name', '?'),
                 a.get('codec_name', ''), int(a.get('bit_rate') or 0)//1000, bool(a),
                 '10' in v.get('pix_fmt', '') or '12' in v.get('pix_fmt', ''),
                 v.get('color_transfer') in ('smpte2084', 'arib-std-b67'), rotation)

def clock(seconds):
    n = max(0, round(seconds))
    return f'{n//3600}:{n//60%60:02}:{n%60:02}' if n >= 3600 else f'{n//60}:{n%60:02}'

def auto_params(quality, height):
    """Вправо: меньше QP/CRF; исходное разрешение начиная с 60."""
    q = max(0, min(100, int(quality)))
    if q >= 60:
        return round(28 - (q-60) * 16/40), 0
    fraction = 0.3 + q / 60 * 0.7
    h = max(2, min(height // 2 * 2, round(height * fraction / 2) * 2))
    return round(38 - q / 60 * 10), h if h < height else 0

def quality_for_crf(crf):
    return max(60, min(100, round(60 + (28-crf)*2.5)))

@dataclass
class Job:
    media: Media
    codec: str
    engine: str
    preset: str
    method: str
    quality: int
    resolution: int
    audio: str
    audio_kbps: int
    target_mb: float = 500
    bitrate: int = 2000
    min_vmaf: float = 95.0
    auto_height: int = 0

    @property
    def params(self):
        if self.method == 'auto':
            base = min(self.auto_height, self.media.height) if self.auto_height else self.media.height
            q,h = auto_params(self.quality, base)
            return q, h or (base if base < self.media.height else 0)
        return round(38 - self.quality * 0.26), min(self.resolution,self.media.height) if self.resolution else 0

    @property
    def encoder(self):
        prefix = 'hevc' if self.codec == 'H.265 / HEVC' else 'h264'
        return prefix+'_nvenc' if self.engine == 'gpu' else ('libx265' if prefix=='hevc' else 'libx264')

    @property
    def audio_rate(self):
        if not self.media.has_audio or self.audio == 'Без звука': return 0
        if self.audio == 'Копировать': return self.media.audio_kbps or 192
        return self.audio_kbps

    @property
    def video_rate(self):
        if self.method == 'size':
            return max(32, int(self.target_mb*1024**2*8/self.media.duration/1000/1.025 - self.audio_rate))
        return self.bitrate

def video_args(job, crf=None, height=None):
    q, h = job.params
    q = q if crf is None else crf
    h = h if height is None else height
    args = []
    if h:
        args += ['-vf', f'scale=-2:{min(h, job.media.height)//2*2}:flags=lanczos']
    # Постоянный формат для одинаковой оценки, превью и финального кодирования.
    args += ['-pix_fmt', 'yuv420p', '-c:v', job.encoder]
    if job.engine == 'gpu':
        preset = {'fast':'p1', 'medium':'p5', 'slow':'p6'}[job.preset]
        args += ['-preset', preset, '-tune', 'hq', '-rc', 'vbr', '-spatial-aq',
                 '0' if job.preset == 'fast' else '1', '-rc-lookahead', '0' if job.preset == 'fast' else '20']
        if job.method in ('auto', 'quality') or crf is not None:
            args += ['-cq', str(q), '-b:v', '0']
        else:
            args += ['-b:v', f'{job.video_rate}k']
    else:
        args += ['-preset', {'fast':'veryfast','medium':'medium','slow':'slow'}[job.preset], '-threads', str(max(1, (os.cpu_count() or 4)//2))]
        if job.encoder == 'libx265':
            args += ['-x265-params', f'pools={max(1,(os.cpu_count() or 4)//2)}:log-level=error']
        if job.method in ('auto','quality') or crf is not None:
            args += ['-crf', str(q)]
        else:
            args += ['-b:v', f'{job.video_rate}k']
    if job.codec == 'H.265 / HEVC': args += ['-tag:v', 'hvc1']
    return args

def input_args(job):
    # CUDA декодирование с выгрузкой кадров для обычного scale/превью.
    return ['-hwaccel', 'cuda'] if job.engine == 'gpu' and job.media.video_codec in ('h264','hevc','mpeg2video','vp9','av1') else []

def command(job, output, start=None, duration=None, sample=False):
    args = [binary('ffmpeg'), '-hide_banner', '-nostdin', '-y']
    if start is not None: args += ['-ss', f'{start:.6f}']
    args += input_args(job) + ['-i', job.media.path]
    if duration is not None: args += ['-t', f'{duration:.6f}']
    args += ['-map', '0:V:0'] + video_args(job)
    if sample or job.audio == 'Без звука' or not job.media.has_audio:
        args += ['-an']
    else:
        args += ['-map', '0:a:0?']
        args += ['-c:a', 'copy'] if job.audio == 'Копировать' else ['-c:a','aac','-b:a',f'{job.audio_kbps}k']
    args += ['-sn', '-map_metadata', '0']
    if str(output).lower().endswith('.mp4'): args += ['-movflags', '+faststart']
    return args + ['-progress', 'pipe:1', '-nostats', str(output)]

def detect_gpu():
    """Проверка реального кодирования, а не только наличия имени encoder."""
    supported = []
    for codec in ('h264', 'hevc'):
        try:
            run([binary('ffmpeg'), '-v','error','-f','lavfi','-i','color=size=320x240:rate=24',
                 '-frames:v','2','-c:v',codec+'_nvenc','-preset','p5','-f','null','-'], timeout=20)
            supported.append(codec)
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
    return supported
