"""Execute inside the extracted portable Python, without host site-packages or model cache."""
import argparse,ctypes,importlib.metadata,json,os,subprocess,sys,time,urllib.request,wave
from pathlib import Path

parser=argparse.ArgumentParser();parser.add_argument('--app',required=True);parser.add_argument('--work',required=True)
args=parser.parse_args();root=Path(args.app).resolve();work=Path(args.work).resolve();work.mkdir(parents=True,exist_ok=True)
assert Path(sys.executable).is_relative_to(root)
assert sys.flags.isolated and Path(sys.base_prefix).is_relative_to(root)
import faster_whisper,imageio_ffmpeg,pypdf,ctranslate2,onnxruntime
from app.core import Project
from app.server import models_available
models=models_available();assert Path(models[0]).is_relative_to(root)
assert not (root/'.runtime/recent.json').exists()
ffmpeg=imageio_ffmpeg.get_ffmpeg_exe();assert Path(ffmpeg).is_relative_to(root)
for dll in ('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll','vcomp140.dll'):
    ctypes.WinDLL(str(root/'.runtime/python'/dll))
versions={d.metadata['Name']:d.version for d in importlib.metadata.distributions()}
(work/'组件版本.json').write_text(json.dumps(versions,ensure_ascii=False,indent=2),encoding='utf-8')
# Synthetic local audio exercises extraction and real model inference without business material.
source=work/'test.wav'
with wave.open(str(source),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*16000*3)
mono=work/'mono.wav'
subprocess.run([ffmpeg,'-y','-i',str(source),'-ar','16000','-ac','1',str(mono)],check=True,capture_output=True)
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',PYTHONNOUSERSITE='1')
subprocess.run([sys.executable,'-m','app.worker','--audio',str(mono),'--output',str(work/'transcript.json'),'--model',models[0],'--cache',str(work/'empty-cache')],env=env,check=True,timeout=180,capture_output=True)
assert (work/'transcript.json').exists()
srt=work/'01.srt';srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nHello.\n',encoding='utf-8')
folder=work/'project';folder.mkdir();project=Project.create(folder,'便携验收')
project.import_pairs([{'episode':'01','srt':str(srt)}]);project.close()
project=Project.open(folder);assert project.data['cues'][0]['accepted']['text']=='Hello.';project.close()
log=(work/'server.log').open('w',encoding='utf-8')
server=subprocess.Popen([sys.executable,'-m','app.server','--no-browser'],cwd=root,env=env,stdout=log,stderr=log)
try:
    for _ in range(300):
        time.sleep(.1)
        if (root/'.runtime/server.json').exists():break
        if server.poll() is not None:raise RuntimeError((work/'server.log').read_text(encoding='utf-8',errors='replace'))
    info=json.loads((root/'.runtime/server.json').read_text(encoding='utf-8'))
    page=urllib.request.urlopen(info['url'],timeout=10).read().decode('utf-8')
    assert 'LOCAL REVIEW STUDIO' in page
finally:
    if 'info' in locals():
        import signal
        os.kill(info['pid'],signal.SIGTERM)
    server.wait(timeout=20);log.close()
(work/'便携验收结果.json').write_text(json.dumps({'passed':True,'python':sys.version,'executable':str(sys.executable),'model':str(models[0]),'checks':['独立Python与依赖','内置FFmpeg','应用本地VC运行库','离线模型加载并真实执行3秒静音推理','字幕项目新建导入保存重开','本地网页服务启动'],'limits':['本机隔离目录验收，未在另一台干净电脑或Windows虚拟机验收','静音推理验证模型执行，不作为对白识别准确率测试']},ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS portable smoke',flush=True)
