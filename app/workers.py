from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import threading
from PySide6.QtCore import QThread, Signal
from .media import binary, command, input_args, video_args, run, NO_WINDOW

class Cancelled(Exception): pass

class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    status = Signal(str)
    progress = Signal(float, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancelled = False
        self.proc = None

    def cancel(self):
        self.cancelled = True
        p = self.proc
        if p and p.poll() is None:
            try: p.terminate()
            except OSError: pass

    def execute(self, args, duration=None):
        if self.cancelled: raise Cancelled()
        with tempfile.TemporaryFile() as errors:
            self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors,
                                         text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
            lines = []
            began = time.monotonic()
            for line in self.proc.stdout:
                if self.cancelled:
                    self.proc.terminate()
                    break
                lines.append(line)
                if duration and line.startswith('out_time_us='):
                    try: seconds = int(line.split('=')[1])/1e6
                    except ValueError: continue
                    elapsed = time.monotonic()-began
                    speed = seconds/max(elapsed, .01)
                    remaining = max(0,duration-seconds)/max(speed,.01)
                    from .media import clock
                    self.progress.emit(min(.999,seconds/duration), f'Осталось ≈ {clock(remaining)} · {speed:.1f}×')
            code = self.proc.wait()
            self.proc.stdout.close()
            errors.seek(0)
            err = errors.read().decode('utf-8',errors='replace')
            self.proc = None
        if self.cancelled: raise Cancelled()
        if code: raise RuntimeError('\n'.join(err.strip().splitlines()[-12:]) or 'Ошибка FFmpeg')
        return ''.join(lines), err

    def run(self):
        try: self.done.emit(self.work())
        except Cancelled: self.done.emit({'cancelled':True})
        except Exception as e: self.failed.emit(str(e))

class HardwareWorker(Worker):
    def work(self):
        from .media import detect_gpu
        return detect_gpu()

class ProbeWorker(Worker):
    def __init__(self, path, parent=None):
        super().__init__(parent); self.path=path
    def work(self):
        from .media import probe
        return probe(self.path)

def timestamps(media):
    return [min(max(0, media.duration-3), media.duration*f) for f in (.18,.48,.82)]

def estimated_mb(job, paths, lengths):
    kbps = sum(Path(p).stat().st_size for p in paths)*8/sum(lengths)/1000
    return (kbps+job.audio_rate)*1000*job.media.duration/8/1024**2*1.025

class SourceCache:
    """Decoded source scenes are shared by analysis and all preview settings."""
    def __init__(self,folder):
        self.folder=Path(folder);self.lock=threading.Lock();self.previews={}

    def acquire(self,worker):
        while not self.lock.acquire(timeout=.1):
            if worker.cancelled:raise Cancelled()
        if worker.cancelled:
            self.lock.release();raise Cancelled()

    def scenes(self,worker,media):
        stat=Path(media.path).stat()
        key=hashlib.sha256(repr((media.path,stat.st_size,stat.st_mtime_ns)).encode()).hexdigest()
        folder=self.folder/key;folder.mkdir(parents=True,exist_ok=True)
        scenes=[]
        for n,t in enumerate(timestamps(media)):
            if worker.cancelled:raise Cancelled()
            length=min(3,media.duration-t);at=min(.8,length/2)
            ref=folder/f'ref{n}.mkv';original=folder/f'orig{n}.png';ready=folder/f'ready{n}'
            if not ready.exists():
                worker.status.emit(f'Подготовка исходных кадров один раз: {n+1}/3…')
                worker.execute([binary('ffmpeg'),'-v','error','-nostdin','-y','-ss',str(t),'-i',media.path,
                    '-map','0:V:0','-t',str(length),'-an','-pix_fmt','yuv420p','-c:v','ffv1',str(ref),
                    '-map','0:V:0','-vf',f'select=gte(t\\,{at})','-frames:v','1','-fps_mode','vfr',str(original)])
                if not ref.is_file() or not original.is_file():raise RuntimeError('Не удалось подготовить исходные кадры.')
                ready.touch()
            scenes.append(dict(ref=ref,original=original,length=length,time=t+at,at=at))
        return key,scenes

class RecommendationWorker(Worker):
    profileReady = Signal(str,object)
    def __init__(self, job, parent=None, profiles=None, source_cache=None):
        super().__init__(parent); self.job=job
        self.source_cache=source_cache
        from .profiles import profiles_for
        self.profiles=profiles or profiles_for(job.media,(1920,1080),job.min_vmaf)

    def work(self):
        from .settings import config_path
        job=self.job
        stat=Path(job.media.path).stat()
        identity=(4,job.media.path,stat.st_size,stat.st_mtime_ns,job.codec,job.engine,job.preset,job.audio,job.audio_kbps,self.profiles)
        key=hashlib.sha256(repr(identity).encode()).hexdigest()
        path=config_path().parent/'recommendations.json'
        try:cache=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):cache={}
        if not isinstance(cache,dict):cache={}
        if key in cache:
            for name,result in cache[key]['profiles'].items():
                if self.cancelled:raise Cancelled()
                self.profileReady.emit(name,result)
            return dict(cache[key],cached=True)
        result=self.analyze()
        cache[key]=result
        cache=dict(list(cache.items())[-64:])
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(cache),encoding='utf-8');os.replace(tmp,path)
        except OSError:pass
        return result

    def analyze(self):
        job = self.job
        with tempfile.TemporaryDirectory(prefix='sjvid-analysis-') as tmp:
            cache=self.source_cache or SourceCache(Path(tmp)/'sources')
            cache.acquire(self)
            try:_,scenes=cache.scenes(self,job.media)
            finally:cache.lock.release()
            refs=[s['ref'] for s in scenes];lengths=[s['length'] for s in scenes]
            # Одинаковые разрешения используют одни и те же измерения.
            measurements={};results={}
            for profile in self.profiles:
                tests=[];chosen=None;h=profile['height'];threshold=profile['threshold']
                for q in (12,14,16,18,20,22,24,26,28):
                    identity=(h,q)
                    if identity not in measurements:
                        scenes=[];samples=[]
                        for n,ref in enumerate(refs):
                            self.status.emit(f'{profile["name"]}: CQ/CRF {q}, фрагмент {n+1}/3…')
                            candidate=replace(job,media=replace(job.media,path=str(ref)),method='auto')
                            from .media import video_args
                            sample=Path(tmp)/f'h{h}-q{q}-{n}.mkv'
                            scale_height=h if h<job.media.height else 0
                            self.execute([binary('ffmpeg'),'-v','error','-nostdin','-y','-i',str(ref),
                                          '-map','0:V:0','-an']+video_args(candidate,crf=q,height=scale_height)+[str(sample)])
                            # Оба изображения оцениваются в одинаковом размере просмотра.
                            transform=f'scale=-2:{h}:flags=lanczos,' if scale_height else ''
                            graph=f'[0:v]{transform}settb=AVTB,setpts=PTS-STARTPTS[d];[1:v]{transform}settb=AVTB,setpts=PTS-STARTPTS[r];[d][r]libvmaf=n_threads=2:n_subsample=2'
                            _,err=self.execute([binary('ffmpeg'),'-hide_banner','-nostdin','-i',str(sample),'-i',str(ref),
                                               '-filter_complex',graph,'-an','-f','null','-'])
                            match=re.search(r'VMAF score:\s*([\d.]+)',err)
                            if not match:raise RuntimeError('FFmpeg не вернул VMAF. Проверьте поддержку libvmaf.')
                            scenes.append(float(match.group(1)));samples.append(sample)
                        measurements[identity]=dict(crf=q,height=h,mb=estimated_mb(job,samples,lengths),
                                                     vmaf=sum(scenes)/len(scenes),worst=min(scenes),scenes=scenes)
                    test=measurements[identity];tests.append(test)
                    good=test['vmaf']>=threshold and test['worst']>=threshold-1
                    if good and test['mb']<=job.media.mb*.97:
                        # Максимум качества: первый подходящий. Экран/телефон: наименьший размер.
                        if chosen is None or test['mb']<chosen['mb']:chosen=test
                        if profile['first']:break
                    if not good:break
                results[profile['key']]=dict(profile,found=bool(chosen),tests=tests,
                    **(chosen or {}),reason='' if chosen else f'При VMAF ≥ {threshold:g} не найдено уменьшение размера хотя бы на 3%.')
                if self.cancelled:raise Cancelled()
                self.profileReady.emit(profile['key'],results[profile['key']])
            return dict(profiles=results)

class PreviewWorker(Worker):
    def __init__(self,job,folder,parent=None,source_cache=None):
        super().__init__(parent); self.job=job;self.folder=Path(folder)
        self.source_cache=source_cache or SourceCache(self.folder/'sources')
    def work(self):
        cache=self.source_cache;cache.acquire(self)
        try:return self.cached_work(cache)
        finally:cache.lock.release()

    def cached_work(self,cache):
        job=self.job;items=[];paths=[];lengths=[]
        source_key,scenes=cache.scenes(self,job.media)
        identity=(source_key,job.encoder,job.preset,job.params,
                  job.video_rate if job.method in ('size','bitrate') else None)
        if identity in cache.previews:
            result=cache.previews.pop(identity);cache.previews[identity]=result
            return dict(items=result['items'],mb=estimated_mb(job,result['paths'],result['lengths']),cached=True)
        args=[binary('ffmpeg'),'-v','error','-nostdin','-y']
        for scene in scenes:args+=['-i',str(scene['ref'])]
        for n,scene in enumerate(scenes):
            seg=self.folder/f'seg{n}.mkv'
            args+=['-map',f'{n}:V:0','-t',str(scene['length'])]+video_args(job)+['-an','-sn','-map_metadata',str(n),str(seg)]
            paths.append(seg);lengths.append(scene['length'])
            items.append(dict(time=scene['time'],original=str(scene['original']),compressed=str(self.folder/f'comp{n}.png')))
        self.status.emit('Превью: кодирование трёх фрагментов…')
        try:self.execute(args)
        except RuntimeError:
            # Drivers with a low simultaneous NVENC session limit can encode serially.
            for seg,scene in zip(paths,scenes):
                local=replace(job,media=replace(job.media,path=str(scene['ref']),video_codec='ffv1',rotation=0))
                self.execute(command(local,seg,duration=scene['length'],sample=True))
        # Extract three decoded compressed frames in one process.
        args=[binary('ffmpeg'),'-v','error','-nostdin','-y']
        for seg,scene in zip(paths,scenes):args+=['-ss',str(scene['at']),'-i',str(seg)]
        for n,item in enumerate(items):args+=['-map',f'{n}:V:0','-frames:v','1',item['compressed']]
        self.execute(args)
        cache.previews[identity]=dict(items=items,paths=paths,lengths=lengths)
        while len(cache.previews)>16:
            cache.previews.pop(next(iter(cache.previews)))
        return dict(items=items,mb=estimated_mb(job,paths,lengths),cached=False)

class EncodeWorker(Worker):
    def __init__(self,job,output,parent=None):
        super().__init__(parent);self.job=job;self.output=Path(output)
    def work(self):
        # Промежуточный файл рядом с результатом; готовый появляется только после успеха.
        tmp=self.output.with_name(self.output.stem+'.partial'+self.output.suffix)
        if tmp.exists(): raise RuntimeError('Уже существует промежуточный файл: '+str(tmp))
        try:
            self.execute(command(self.job,tmp),duration=self.job.media.duration)
            if self.cancelled: raise Cancelled()
            if not tmp.is_file() or tmp.stat().st_size==0: raise RuntimeError('FFmpeg не создал результат.')
            if self.output.exists(): raise RuntimeError('Файл результата уже существует. Выберите новое имя.')
            os.replace(tmp,self.output)
            self.progress.emit(1,'Готово')
            return dict(path=str(self.output),mb=self.output.stat().st_size/1024**2)
        finally:
            if tmp.exists(): tmp.unlink()
