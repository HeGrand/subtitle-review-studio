"""Publish-size CPU core and optional overlays from a clean portable folder."""
import argparse,hashlib,json,re,zipfile
from pathlib import Path

LIMIT=2*1024**3
FORBIDDEN={'project.json','recent.json','server.json','acceleration.json','ai-config.json','.subtitle-review.lock','.env'}

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def make_zip(path,files,extra):
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for source,name in files:z.write(source,name)
        for name,text in extra.items():z.writestr(name,text)
    if path.stat().st_size>=LIMIT:raise ValueError(f'{path.name} exceeds the GitHub 2GiB asset limit')
    return sha(path)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--output',required=True);parser.add_argument('--tag',default='v0.2.0')
    args=parser.parse_args();root=Path(args.root).resolve();out=Path(args.output).resolve()
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:[-.][A-Za-z0-9.]+)?',args.tag):raise ValueError('Use a version tag such as v0.1.0')
    if out.is_relative_to(root):raise ValueError('Output must be outside the portable folder')
    if not json.loads((root/'portable.json').read_text(encoding='utf-8')).get('offline'):raise ValueError('Expected a portable distribution')
    out.mkdir(parents=True,exist_ok=True)
    core=[];gpu=[];whisperx=[]
    for p in sorted(root.rglob('*')):
        if not p.is_file():continue
        relative=p.relative_to(root).as_posix()
        if p.is_symlink() or p.name in FORBIDDEN or 'desktop-user-data' in p.parts or '__pycache__' in p.parts or p.suffix=='.pyc':raise ValueError('Private state or nonportable file: '+relative)
        if relative=='文件清单_SHA256.json':continue
        item={'path':relative,'bytes':p.stat().st_size,'sha256':sha(p)}
        group = gpu if relative.startswith('.runtime/gpu/') else whisperx if relative.startswith('.runtime/whisperx/') else core
        group.append((p,item))
    print(f'Core: {len(core)} files; GPU: {len(gpu)} files; WhisperX: {len(whisperx)} files',flush=True)
    prefix=f'SubtitleReviewStudio-{args.tag}';checks={}
    manifest=json.dumps([entry for _,entry in core],ensure_ascii=False,indent=2)
    path=out/(prefix+'-windows-x64.zip')
    checks[path.name]=make_zip(path,[(p,root.name+'/'+entry['path']) for p,entry in core],{root.name+'/文件清单_SHA256.json':manifest})
    print('Core archive ready',flush=True)
    if gpu:
        path=out/(prefix+'-nvidia-gpu-addon.zip')
        instructions='将本压缩包解压到已经包含“字幕工作室.exe”的程序目录，合并.runtime文件夹。首次识别自动比较GPU和CPU速度，按实测选择；无需安装系统CUDA。显卡驱动由电脑原环境提供。\n'
        checks[path.name]=make_zip(path,[(p,entry['path']) for p,entry in gpu],{'GPU组件安装说明.txt':instructions,'GPU组件文件清单_SHA256.json':json.dumps([entry for _,entry in gpu],ensure_ascii=False,indent=2)})
        print('GPU archive ready',flush=True)
    if whisperx:
        path=out/(prefix+'-whisperx-en-addon.zip')
        instructions='关闭字幕工作室，将本包解压到含“字幕工作室.exe”的主程序目录，合并.runtime 文件夹。重新启动后，有英文文稿的集数可以离线使用 WhisperX 对齐。已有字幕可从生成页重新匹配。\n'
        checks[path.name]=make_zip(path,[(p,entry['path']) for p,entry in whisperx],
                                   {'WhisperX安装说明.txt':instructions,
                                    'WhisperX组件文件清单_SHA256.json':json.dumps([entry for _,entry in whisperx],ensure_ascii=False,indent=2)})
        print('WhisperX archive ready',flush=True)
    instructions='字幕工作室 '+args.tag+'\n\n先完整解压 windows-x64 主程序包，再双击其中的“字幕工作室.exe”。CPU可直接使用，无需安装Python或下载模型。\nNVIDIA用户可选下载 nvidia-gpu-addon，将其解压到含启动入口的程序目录，合并.runtime。\n首次测速需额外等待几十秒，此后复用结果。项目目录请选择程序文件夹之外；结束时保存并关闭项目。\n项目源码、使用说明及许可证见对应GitHub仓库README。各第三方组件保留原许可证。\n'
    if whisperx:
        instructions+='英文文稿匹配请同时下载 whisperx-en-addon，将它解压到含启动入口的目录，合并.runtime。英文对齐模型随组件提供，可离线使用。\n'
    (out/'INSTALL.txt').write_text(instructions,encoding='utf-8')
    checks['INSTALL.txt']=sha(out/'INSTALL.txt')
    (out/'SHA256SUMS.txt').write_text('\n'.join(f'{value}  {name}' for name,value in checks.items())+'\n',encoding='utf-8')
    print(json.dumps({'assets':[{'name':name,'bytes':(out/name).stat().st_size,'sha256':value} for name,value in checks.items()]},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
