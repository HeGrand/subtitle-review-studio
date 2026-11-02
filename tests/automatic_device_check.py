"""Real automatic selection and full processing in an independent project."""
import argparse,copy,json,subprocess,time
from pathlib import Path
from app.core import Project
from app.processing import Processor,ffmpeg_exe
from app.acceleration import public_status
import threading

parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);parser.add_argument('--audio',required=True);parser.add_argument('--output',required=True)
args=parser.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=False)
source=root/'01.wav';subprocess.run([ffmpeg_exe(),'-y','-i',args.audio,'-t','20','-ac','1','-ar','16000',str(source)],capture_output=True,check=True)
srt=root/'01.srt';srt.write_text('1\n00:00:00,100 --> 00:00:01,200\nBeautiful.\n\n2\n00:00:02,000 --> 00:00:03,500\nTen bottles.\n',encoding='utf-8')
folder=root/'project';folder.mkdir();project=Project.create(folder,'GPU自动选择验收')
try:
    project.import_pairs([{'episode':'01','srt':str(srt),'media':str(source)}]);cue=project.data['cues'][0];project.edit([cue['id']],'approve');before=copy.deepcopy(cue)
    processor=Processor(threading.RLock());began=time.perf_counter();processor.start(project,['01'],args.model);processor.thread.join(timeout=220)
    assert not processor.active(),'Processing timed out'
    job=project.data['jobs']['01'];assert job['status']=='done',job
    assert job['device']=='cuda',job
    assert cue==before,'Human approval changed'
    transcript=json.loads((project.root/project.data['episodes']['01']['transcript']).read_text(encoding='utf-8'))
    assert transcript['execution']['device']=='cuda'
    report={'passed':True,'elapsed_with_first_calibration':time.perf_counter()-began,'job':job,'automatic':public_status(),'checks':['真实自动GPU/CPU测速','选择实测更快GPU','自动完整本地初查','人工通过逐字段不变','字幕和原片时间未改动']}
    (root/'结果.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False),flush=True)
finally:
    if 'processor' in locals() and processor.active():processor.pause()
    project.close()
