"""Finalize a clean portable folder and write its redistribution ZIP and hashes."""
import argparse,hashlib,importlib.metadata,json,zipfile
from pathlib import Path

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--zip',required=True)
    args=parser.parse_args();root=Path(args.root).resolve();archive=Path(args.zip).resolve()
    assert json.loads((root/'portable.json').read_text(encoding='utf-8'))['offline']
    assert not archive.is_relative_to(root),'ZIP must be outside the portable folder'
    files=[p for p in root.rglob('*') if p.is_file()]
    forbidden={'project.json','recent.json','server.json','acceleration.json','.subtitle-review.lock','.env'}
    assert not [str(p) for p in files if p.name in forbidden or p.is_symlink() or 'desktop-user-data' in p.parts or '__pycache__' in p.parts or p.suffix=='.pyc'],'Portable folder contains runtime state, cached bytecode, or links'
    versions={d.metadata['Name']:d.version for d in importlib.metadata.distributions(path=[str(root/'.runtime/deps')])}
    (root/'第三方组件版本.json').write_text(json.dumps(versions,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'requirements.lock.txt').write_text('\n'.join(f'{k}=={v}' for k,v in sorted(versions.items()))+'\n',encoding='utf-8')
    manifest=[{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(root.rglob('*')) if p.is_file() and p.name!='文件清单_SHA256.json']
    (root/'文件清单_SHA256.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('打包文件 '+str(len(manifest)+1),flush=True)
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for p in sorted(root.rglob('*')):
            if p.is_file():z.write(p,root.name+'/'+p.relative_to(root).as_posix())
    checksum=sha(archive)
    archive.with_suffix('.zip.sha256.txt').write_text(checksum+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps({'zip':str(archive),'bytes':archive.stat().st_size,'sha256':checksum},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
