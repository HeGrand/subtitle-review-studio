"""Same full episode and model, sequential CPU/GPU runs; no project mutations."""
import argparse,json,subprocess,sys,time
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);parser.add_argument('--audio',required=True);parser.add_argument('--output',required=True)
args=parser.parse_args();root=Path(args.output);root.mkdir(parents=True,exist_ok=False);results={}
for device in ('cpu','cuda'):
    folder=root/device;folder.mkdir();began=time.perf_counter()
    subprocess.run([sys.executable,'-m','app.worker','--device',device,'--model',args.model,'--audio',args.audio,'--output',str(folder/'transcript.json'),'--cache',str(root/'cache')],check=True,timeout=360)
    elapsed=time.perf_counter()-began;transcript=json.loads((folder/'transcript.json').read_text(encoding='utf-8'))
    results[device]={'wall_seconds':elapsed,'inference_seconds':transcript['processing_seconds'],'audio_seconds':transcript['duration'],'segments':len(transcript['segments']),'execution':transcript['execution']}
    print(device+' '+str(round(elapsed,2))+' seconds',flush=True)
results['wall_speedup']=results['cpu']['wall_seconds']/results['cuda']['wall_seconds']
(root/'对照结果.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(results,ensure_ascii=False),flush=True)
