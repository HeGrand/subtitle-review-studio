"""Prepare the official Windows Electron runtime without a system installation."""
import hashlib,json,re,subprocess,zipfile,concurrent.futures,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    package=ROOT/'node_modules/electron'
    version=json.loads((package/'package.json').read_text(encoding='utf-8'))['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Expected a stable Electron version')
    target=package/'dist'
    if (target/'electron.exe').exists():
        if (target/'version').read_text().strip()==version:return
        raise ValueError('Desktop runtime version differs; run npm ci --ignore-scripts before preparing it')
    cache=ROOT/'.runtime/desktop-download';cache.mkdir(parents=True,exist_ok=True)
    name=f'electron-v{version}-win32-x64.zip';base=f'https://github.com/electron/electron/releases/download/v{version}/'
    archive=cache/(name+'.part');sums=cache/'SHASUMS256.txt'
    curl=['curl.exe','--fail','--silent','--show-error','--location','--retry','2','--connect-timeout','15','--max-time','90']
    subprocess.run(curl+['--output',str(sums),base+'SHASUMS256.txt'],check=True)
    expected=next(line.split()[0] for line in sums.read_text().splitlines() if line.split()[-1].lstrip('*')==name)
    def checksum(file):
        with file.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    if not archive.exists() or checksum(archive)!=expected:
        if os.environ.get('GITHUB_ACTIONS')=='true':
            subprocess.run(curl+['--max-time','900','--output',str(archive),base+name],check=True)
        else:
            # Fixed ranges also work on networks which stall large single-response downloads.
            headers=cache/'size.headers'
            subprocess.run(curl+['--range','0-0','--dump-header',str(headers),'--output',str(cache/'size.byte'),base+name],check=True)
            total=int(re.findall(r'content-range:\s*bytes\s+0-0/(\d+)',headers.read_text(),re.I)[-1])
            parts=cache/('parts-'+version);parts.mkdir(exist_ok=True);size=1024*1024;count=(total+size-1)//size
            def download(index):
                start=index*size;end=min(total,start+size)-1;part=parts/str(index)
                if not part.exists() or part.stat().st_size!=end-start+1:
                    subprocess.run(curl+['--range',f'{start}-{end}','--output',str(part),base+name],check=True)
                if part.stat().st_size!=end-start+1:raise ValueError('Incomplete desktop runtime range')
                return index
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                for number,_ in enumerate(pool.map(download,range(count)),1):
                    if number%32==0 or number==count:print(f'Desktop runtime: {number}/{count} ranges',flush=True)
            with archive.open('wb') as output:
                for index in range(count):
                    with (parts/str(index)).open('rb') as stream:
                        for block in iter(lambda:stream.read(size),b''):output.write(block)
    digest=checksum(archive)
    if digest!=expected:raise ValueError('Electron checksum does not match the official release')
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        if any(Path(name).is_absolute() or '..' in Path(name).parts for name in z.namelist()):raise ValueError('Invalid runtime archive')
        z.extractall(target)
    (package/'path.txt').write_text('electron.exe',encoding='utf-8')
    print('Official desktop runtime verified: '+version)
if __name__=='__main__':main()
