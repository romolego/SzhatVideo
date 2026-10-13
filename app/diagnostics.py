"""Проверка автономной сборки без изменения пользовательских настроек."""
import json
import os
from pathlib import Path
import tempfile
import time
from .media import binary,run,probe,detect_gpu,Job,command

def self_test(destination):
    result={'ffmpeg':False,'ffprobe':False,'gpu':[],'encodes':[]}
    with tempfile.TemporaryDirectory(prefix='sjvid-selftest-') as folder:
        source=Path(folder)/'input.mkv'
        result['ffmpeg']=bool(run([binary('ffmpeg'),'-version']).stdout)
        result['ffprobe']=bool(run([binary('ffprobe'),'-version']).stdout)
        run([binary('ffmpeg'),'-v','error','-y','-f','lavfi','-i','testsrc2=size=320x240:rate=24',
             '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3','-c:v','libx264','-c:a','aac',str(source)])
        media=probe(str(source));result['gpu']=detect_gpu()
        for engine in ['cpu']+(['gpu'] if 'h264' in result['gpu'] else []):
            output=Path(folder)/(engine+'.mp4')
            run(command(Job(media,'H.264',engine,'medium','auto',85,0,'AAC',192),output))
            meta=probe(str(output))
            assert meta.duration>2.5 and meta.has_audio
            result['encodes'].append({'engine':engine,'duration':meta.duration,'size':meta.size})
    result['passed']=True
    Path(destination).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return 0
