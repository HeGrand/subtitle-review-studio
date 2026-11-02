import hashlib,importlib.util,json,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from test_core import ROOT as TEST_ROOT

ROOT=Path(__file__).resolve().parents[1]

class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='subtitle-release-',dir=TEST_ROOT);self.root=Path(self.temp.name)/'Portable';self.root.mkdir()
        (self.root/'portable.json').write_text('{"offline":true}',encoding='utf-8')
        (self.root/'start.cmd').write_text('example',encoding='utf-8')
        gpu=self.root/'.runtime/gpu';gpu.mkdir(parents=True);(gpu/'example.dll').write_bytes(b'synthetic library')
        self.out=Path(self.temp.name)/'out'
    def tearDown(self):self.temp.cleanup()
    def run_packager(self,*extra):
        return subprocess.run([sys.executable,str(ROOT/'scripts/package_release.py'),'--root',str(self.root),'--output',str(self.out),*extra],capture_output=True,text=True,encoding='utf-8')
    def test_core_and_gpu_overlay_are_separate_and_have_valid_hashes(self):
        result=self.run_packager();self.assertEqual(result.returncode,0,result.stderr)
        archives=list(self.out.glob('*.zip'));self.assertEqual(len(archives),2)
        core=next(p for p in archives if 'windows-x64' in p.name);gpu=next(p for p in archives if 'gpu-addon' in p.name)
        with zipfile.ZipFile(core) as z:
            self.assertFalse(any('/gpu/' in p for p in z.namelist()))
            manifest=json.loads(z.read('Portable/文件清单_SHA256.json'))
            for entry in manifest:self.assertEqual(hashlib.sha256(z.read('Portable/'+entry['path'])).hexdigest(),entry['sha256'])
        with zipfile.ZipFile(gpu) as z:
            self.assertIn('.runtime/gpu/example.dll',z.namelist());self.assertNotIn('Portable/start.cmd',z.namelist())
        for line in (self.out/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
            expected,name=line.split('  ',1);self.assertEqual(hashlib.sha256((self.out/name).read_bytes()).hexdigest(),expected)
    def test_private_state_and_unsafe_version_cannot_be_packaged(self):
        (self.root/'recent.json').write_text('{"private":true}')
        self.assertNotEqual(self.run_packager().returncode,0);self.assertFalse(list(self.out.glob('*.zip')) if self.out.exists() else [])
        (self.root/'recent.json').unlink()
        profile=self.root/'.runtime/desktop-user-data';profile.mkdir();(profile/'Cookies').write_text('synthetic private profile')
        self.assertNotEqual(self.run_packager().returncode,0);(profile/'Cookies').unlink();profile.rmdir()
        self.assertNotEqual(self.run_packager('--tag','../../outside').returncode,0)
    def test_asset_limit_fails_before_publication(self):
        spec=importlib.util.spec_from_file_location('release_packager',ROOT/'scripts/package_release.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.LIMIT=1
        with self.assertRaises(ValueError):module.make_zip(Path(self.temp.name)/'large.zip',[],{'example':'too large'})
