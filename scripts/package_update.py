"""Small code + desktop-runtime update; intentionally excludes models and project state."""
import argparse,hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args();output=Path(args.output).resolve()
    files=[]
    for folder in ('app','web','desktop'):
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc':files.append((p,p.relative_to(ROOT).as_posix()))
    for name in ('scripts/launch.ps1','scripts/restart.ps1','启动字幕审查.cmd','重新启动字幕审查.cmd','LICENSE','THIRD_PARTY_NOTICES.md'):
        files.append((ROOT/name,name))
    engine=ROOT/'node_modules/electron/dist'
    if not (engine/'electron.exe').exists():raise ValueError('Prepare the official desktop runtime first')
    for p in engine.rglob('*'):
        if p.is_file() and p.suffix!='.log':files.append((p,'.runtime/desktop/'+p.relative_to(engine).as_posix()))
    forbidden={'project.json','recent.json','server.json','acceleration.json','.subtitle-review.lock','Cookies'}
    if any(p.name in forbidden or 'desktop-user-data' in p.parts for p,_ in files):raise ValueError('Update contains private state')
    instructions='字幕工作室 v0.2 更新包\n\n1. 先在旧页面保存并关闭项目，再关闭旧启动窗口/应用。\n2. 将本更新包解压到含“启动字幕审查.cmd”的原程序目录，允许覆盖程序代码。不要解压到业务项目目录。\n3. 双击“重新启动字幕审查.cmd”，界面页脚应显示v0.2并默认打开桌面窗口。\n4. 桌面版支持直接拖入本机及有访问权限的共享盘文件夹；JSON和剧本拖到对应区域可导入。所有浏览入口统一为页面内文件浏览器。\n\n本包不含模型、GPU组件、项目、审核进度或最近记录，不需要重新下载模型/GPU。原项目仍从原目录打开，不必重新导入旧意见。\n'
    manifest=[]
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for p,name in files:
            with p.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
            manifest.append({'path':name,'bytes':p.stat().st_size,'sha256':digest});z.write(p,name)
        z.writestr('更新说明_v0.2.txt',instructions);z.writestr('更新文件_SHA256.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with output.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    output.with_suffix('.zip.sha256.txt').write_text(digest+'  '+output.name+'\n',encoding='utf-8')
    print(json.dumps({'zip':str(output),'bytes':output.stat().st_size,'sha256':digest},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
