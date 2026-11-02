"""Isolated CPU/CUDA speed probes and per-machine, per-model automatic routing."""
import argparse,json,os,platform,statistics,subprocess,time,wave
from pathlib import Path
from .core import APP_ROOT,atomic_json,read_json,digest

DLL_HANDLES=[]
PARAMETERS={'beam_size':5,'word_timestamps':True,'vad_filter':False,'condition_on_previous_text':False,'language':None}

def prepare_gpu_libraries():
    folders=sorted({p.parent for p in (APP_ROOT/'.runtime/gpu').rglob('*.dll')})
    if folders:
        os.environ['PATH']=os.pathsep.join(map(str,folders))+os.pathsep+os.environ.get('PATH','')
        if os.name=='nt':
            for folder in folders:DLL_HANDLES.append(os.add_dll_directory(str(folder)))
    return folders

def hardware():
    data={'cpu':platform.processor(),'threads':os.cpu_count(),'gpus':[]}
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=name,uuid,driver_version,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode==0:
            for line in result.stdout.splitlines():
                values=[x.strip() for x in line.split(',')]
                if len(values)==4:data['gpus'].append(dict(zip(('name','uuid','driver','memory_mb'),values)))
    except (OSError,subprocess.TimeoutExpired):pass
    return data

def profile_path():return APP_ROOT/'.runtime/acceleration.json'

def load_profiles():
    try:
        result=read_json(profile_path())
        return result if isinstance(result,dict) and isinstance(result.get('profiles'),dict) else {'profiles':{}}
    except (OSError,ValueError):return {'profiles':{}}

def fingerprint(model,download_model,hw):
    file=Path(model)/'model.bin' if model else None
    stamp=[file.stat().st_size,file.stat().st_mtime_ns] if file and file.exists() else download_model
    gpu_versions=sorted([str(p.relative_to(APP_ROOT/'.runtime/gpu')),p.stat().st_size,p.stat().st_mtime_ns] for p in (APP_ROOT/'.runtime/gpu').rglob('*.dll'))
    import importlib.metadata
    try:engine=importlib.metadata.version('ctranslate2')
    except importlib.metadata.PackageNotFoundError:engine='unknown'
    return digest([hw,stamp,engine,gpu_versions,PARAMETERS,2])

def choose_backend(model,download_model,run_probe,notify=lambda message:None,clock=time.time):
    hw=hardware();key=fingerprint(model,download_model,hw);database=load_profiles();cached=database['profiles'].get(key)
    if cached and clock()<cached.get('expires',0):
        notify(cached['message']+'（复用本机测速结果）');return cached
    profile={'key':key,'hardware':hw,'model':Path(model).name if model else download_model,'at':clock(),'device':'cpu','compute_type':'int8','measurements':{}}
    if not hw['gpus']:
        profile['message']='未检测到可用 NVIDIA GPU，使用 CPU'
    else:
        for device in ('cuda','cpu'):
            notify('首次自动测速：'+('GPU' if device=='cuda' else 'CPU')+'，使用相同音频和识别参数；之后复用结果')
            try:profile['measurements'][device]=run_probe(device)
            except InterruptedError:raise
            except Exception as exc:profile['measurements'][device]={'error':str(exc)[-700:]}
            if device=='cuda' and 'error' in profile['measurements'][device]:break
        gpu=profile['measurements'].get('cuda',{});cpu=profile['measurements'].get('cpu',{})
        if 'seconds' in gpu and ('seconds' not in cpu or gpu['seconds']<=cpu['seconds']):
            message='GPU 实测更快，自动使用 GPU' if 'seconds' in cpu else 'GPU 推理通过；CPU测速未完成，暂用GPU'
            profile.update(device='cuda',compute_type='float16',message=message)
        elif 'seconds' in gpu:
            profile['message']='CPU 在同段音频实测更快，自动使用 CPU'
        else:profile['message']='GPU 加载或推理未通过，已自动使用 CPU'
    failed=any(m.get('error') for m in profile['measurements'].values())
    profile['expires']=clock()+(600 if failed else 7*86400)
    database['profiles'][key]=profile;database['last_key']=key;atomic_json(profile_path(),database)
    notify(profile['message']);return profile

def record_fallback(profile,error):
    result={**profile,'device':'cpu','compute_type':'int8','message':'GPU 运行中出错，已保留完成分段并改用 CPU','fallback_error':str(error)[-700:],'expires':time.time()+600}
    database=load_profiles();database['profiles'][profile['key']]=result;database['last_key']=profile['key'];atomic_json(profile_path(),database)
    return result

def public_status():
    database=load_profiles();p=database['profiles'].get(database.get('last_key'),{})
    return {k:p[k] for k in ('device','compute_type','message','measurements','model','at') if k in p}

def main():
    parser=argparse.ArgumentParser()
    for name in ('model','audio','result','cache','download-model'):parser.add_argument('--'+name,default='')
    parser.add_argument('--device',choices=('cpu','cuda'),required=True);parser.add_argument('--start',type=float,default=0)
    args=parser.parse_args();prepare_gpu_libraries()
    os.environ.update(HF_HOME=str(Path(args.cache).resolve()),HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1')
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    import numpy as np
    with wave.open(args.audio,'rb') as w:
        rate=w.getframerate();assert rate==16000 and w.getnchannels()==1 and w.getsampwidth()==2
        w.setpos(min(int(args.start*rate),max(0,w.getnframes()-rate)))
        sample=np.frombuffer(w.readframes(12*rate),dtype=np.int16).astype(np.float32)/32768
    began=time.perf_counter()
    model=WhisperModel(args.model or download_model(args.download_model,cache_dir=args.cache),device=args.device,compute_type='float16' if args.device=='cuda' else 'int8',cpu_threads=min(8,os.cpu_count() or 4),num_workers=1,local_files_only=True)
    load=time.perf_counter()-began;times=[];counts=[]
    for trial in range(3):
        began=time.perf_counter();segments,_=model.transcribe(sample,**PARAMETERS);rows=list(segments)
        elapsed=time.perf_counter()-began
        if trial:times.append(elapsed);counts.append(sum(len(s.words or []) for s in rows))
    atomic_json(args.result,{'device':args.device,'seconds':statistics.median(times),'runs':times,'load_seconds':load,'sample_seconds':len(sample)/16000,'words':counts,'parameters':PARAMETERS})

if __name__=='__main__':main()
