"""Build an isolated Windows x64 offline distribution; never copy user runtime state."""
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path

APP=Path(__file__).resolve().parents[1]
def write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
def fetch(url,path):
    if not path.exists():
        print('下载 '+url,flush=True)
        temp=path.with_suffix(path.suffix+'.part')
        subprocess.run(['curl.exe','--fail','--silent','--show-error','--location','--retry','2','--connect-timeout','15','--max-time','900','--continue-at','-','--output',str(temp),url],check=True)
        temp.replace(path)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--model',required=True);parser.add_argument('--vc-redist',required=True)
    args=parser.parse_args();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    root=out/'字幕工作室_Windows便携版';root.mkdir(exist_ok=False)
    runtime=root/'.runtime';runtime.mkdir();cache=out/'构建下载';cache.mkdir(exist_ok=True)
    for folder in ('app','web'):
        shutil.copytree(APP/folder,root/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (root/'scripts').mkdir();shutil.copy2(APP/'scripts/launch.ps1',root/'scripts/launch.ps1')
    shutil.copy2(APP/'启动字幕审查.cmd',root/'启动字幕审查.cmd')
    py=runtime/'python';py.mkdir()
    # Relocate the verified local CPython runtime and matching binary wheels.
    # Explicit _pth isolation prevents registry/site-package/host-Python dependencies.
    base=Path(sys.base_prefix);version=f'{sys.version_info.major}{sys.version_info.minor}'
    for name in ('python.exe','pythonw.exe','python3.dll',f'python{version}.dll','LICENSE.txt'):
        shutil.copy2(base/name,py/name)
    shutil.copytree(base/'Lib',py/'Lib',ignore=shutil.ignore_patterns('site-packages','test','tests','idlelib','tkinter','ensurepip','__pycache__','*.pyc'))
    shutil.copytree(base/'DLLs',py/'DLLs',ignore=shutil.ignore_patterns('_test*','_ctypes_test*','_tkinter*','tcl*','tk*','*.pdb','*.lib'))
    write(py/f'python{version}._pth','Lib\nDLLs\n.\n..\\..\n..\\deps\n')
    shutil.copytree(APP/'.runtime/deps',runtime/'deps',ignore=shutil.ignore_patterns('__pycache__','*.pyc','bin','Scripts','*.lib','*.pdb'))
    if (APP/'.runtime/gpu').exists():
        shutil.copytree(APP/'.runtime/gpu',runtime/'gpu',ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.lib','*.pdb','include'))
    # Application-local redistributable DLLs: no system installation or administrator rights.
    vc=Path(args.vc_redist)
    for folder in vc.iterdir():
        if folder.is_dir() and folder.name.lower().endswith(('.crt','.openmp')):
            for dll in folder.glob('*.dll'):shutil.copy2(dll,py/dll.name)
    if not (py/'msvcp140.dll').exists():raise ValueError('No x64 Visual C++ CRT redistributables found')
    model=runtime/'models/faster-whisper-large-v3-turbo';model.mkdir(parents=True)
    for name in ('config.json','model.bin','preprocessor_config.json','tokenizer.json','vocabulary.json'):
        shutil.copyfile(Path(args.model)/name,model/name)  # Dereference local cache links.
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md'):
        if (APP/name).exists():shutil.copy2(APP/name,root/name)
    notices=root/'第三方组件说明';notices.mkdir()
    for name,url in [('Whisper模型_MIT许可证.txt','https://raw.githubusercontent.com/openai/whisper/main/LICENSE'),('模型来源说明.md','https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo/raw/main/README.md')]:
        fetch(url,cache/name);shutil.copy2(cache/name,notices/name)
    write(notices/'组件说明.txt','CPython运行时由本机已验证安装独立复制，使用_pth隔离，不读取系统Python或用户site-packages；许可证见.runtime/python/LICENSE.txt。\n各Python组件的许可证随.runtime/deps中的dist-info保留。\nFFmpeg许可证和来源说明随imageio_ffmpeg组件保留。\nMicrosoft Visual C++运行库使用Visual Studio Redist/MSVC/x64的应用本地可再发行DLL，不包含开发工具。\n模型：mobiuslabsgmbh/faster-whisper-large-v3-turbo，保留来源及Whisper MIT许可。\n')
    write(root/'portable.json',json.dumps({'name':'字幕工作室','version':'0.1.0','platform':'Windows x64','python':sys.version.split()[0],'offline':True,'acceleration':'auto-benchmark','model':'faster-whisper-large-v3-turbo'},ensure_ascii=False,indent=2))
    write(root/'先读我.txt','字幕工作室 · Windows便携版\n\n1. 完整解压整个文件夹到可写目录，再双击“启动字幕审查.cmd”。不要在压缩包内直接运行。\n2. 无需安装Python、运行组件或下载模型，无需管理员权限。支持Windows 10/11 64位；默认浏览器打开本地页面。\n3. 首次进入是空白工作台。新建项目请选择程序文件夹以外的目录，再导入SRT、音视频和DOCX等资料。\n4. 运行本地初查；全部集数完成后按提示前往AI交接与导出。包交给你使用的外部AI，再导回意见。\n5. 仅审已有字幕：无对应字幕识别片段不阻挡导出。文字无具体问题无需逐条点击通过；具体疑点和草稿仍需处理。\n6. 结束时先在页面“保存并关闭”项目，再关闭启动窗口。项目和进度留在你选择的项目目录。\n7. 外部AI服务需自行使用；便携包不包含AI账号或付费API，也不会自动上传素材。\n8. 不包含他人的项目、审核进度、最近记录或账号。带离程序文件夹后仍需保存自己的项目和素材。\n\n模型为多语言large-v3-turbo。默认先检测电脑，首次同段音频实测GPU和CPU速度，选择更快且可用的一方；GPU故障会保留完成分段并回退CPU。支持NVIDIA CUDA显卡；其他电脑自动使用CPU。首次测速会增加等待，此后复用本机结果，素材页可清除测速结果重新检测。建议预留4GB以上可用内存及足够素材空间。\n')
    manifest=[]
    for path in sorted(root.rglob('*')):
        if path.is_file():
            h=hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
            manifest.append({'path':path.relative_to(root).as_posix(),'bytes':path.stat().st_size,'sha256':h.hexdigest()})
    write(root/'文件清单_SHA256.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    print('构建完成 '+str(root),flush=True)
if __name__=='__main__':main()
