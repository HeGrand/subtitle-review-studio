import copy,json,tempfile,threading,unittest,wave
from pathlib import Path
from unittest.mock import patch,Mock
from app import acceleration as a
from app.core import UserError,atomic_json
from test_core import WorkflowTests,ROOT

class AccelerationTests(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True,exist_ok=True);self.root=Path(tempfile.mkdtemp(prefix='speed-',dir=ROOT))
        self.location=patch.object(a,'APP_ROOT',self.root);self.location.start()
        self.hw=patch.object(a,'hardware',return_value={'cpu':'test','threads':8,'gpus':[{'name':'test GPU','driver':'test'}]});self.hw.start()
    def tearDown(self):self.hw.stop();self.location.stop()
    def choose(self,probe,clock=lambda:100):return a.choose_backend('','small',probe,clock=clock)
    def test_gpu_faster_and_same_profile_is_reused(self):
        probe=Mock(side_effect=lambda device:{'seconds':1 if device=='cuda' else 6})
        p=self.choose(probe);self.assertEqual(p['device'],'cuda');self.assertEqual(probe.call_count,2)
        self.assertEqual(self.choose(probe)['device'],'cuda');self.assertEqual(probe.call_count,2)
    def test_cpu_only_when_measured_faster_and_gpu_wins_tie(self):
        probe=lambda device:{'seconds':5 if device=='cuda' else 2}
        self.assertEqual(self.choose(probe)['device'],'cpu')
        atomic_json(a.profile_path(),{'profiles':{}})
        self.assertEqual(self.choose(lambda device:{'seconds':2})['device'],'cuda')
    def test_missing_gpu_or_gpu_native_error_uses_cpu(self):
        with patch.object(a,'hardware',return_value={'cpu':'test','gpus':[]}):
            probe=Mock();self.assertEqual(self.choose(probe)['device'],'cpu');probe.assert_not_called()
        p=self.choose(Mock(side_effect=UserError('missing CUDA DLL')))
        self.assertEqual(p['device'],'cpu');self.assertIn('error',p['measurements']['cuda']);self.assertEqual(p['expires'],700)
    def test_cpu_probe_failure_keeps_working_gpu(self):
        def probe(device):
            if device=='cpu':raise UserError('CPU probe failed')
            return {'seconds':1}
        self.assertEqual(self.choose(probe)['device'],'cuda')
    def test_pause_does_not_cache_failed_gpu_or_fall_back(self):
        with self.assertRaises(InterruptedError):self.choose(Mock(side_effect=InterruptedError('pause')))
        self.assertFalse(a.profile_path().exists())
    def test_profile_invalidates_when_driver_changes(self):
        probe=Mock(return_value={'seconds':1});self.choose(probe)
        with patch.object(a,'hardware',return_value={'cpu':'test','gpus':[{'driver':'new'}]}):self.choose(probe)
        self.assertEqual(probe.call_count,4)

class FallbackTests(unittest.TestCase):
    setUp=WorkflowTests.setUp
    tearDown=WorkflowTests.tearDown
    def test_refresh_preserves_existing_issue_triage_and_human_resolution(self):
        from app.processing import Processor
        p=self.p;audio=p.root/'evidence/01/audio.wav';audio.parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(audio),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*16000*3)
        atomic_json(audio.parent/'transcript.json',{'segments':[]})
        clip=audio.parent/'clips/saved.wav';clip.parent.mkdir();clip.write_bytes(b'kept')
        issue={'id':'saved','episode':'01','cue_id':p.data['cues'][0]['id'],'start_ms':100,'end_ms':1000,'kind':'文字差异','resolved':False}
        p.data['issues']=[{**issue,'triage':{'result':'dismissed','reason':'已复核'},'resolved':True,'resolution':'保留现有字幕'}]
        with patch('app.processing.rule_check',return_value=[copy.deepcopy(issue)]):Processor(threading.RLock())._run(p,['01'],'test-model','small')
        self.assertEqual(p.data['issues'][0]['triage']['result'],'dismissed');self.assertTrue(p.data['issues'][0]['resolved']);self.assertEqual(p.data['issues'][0]['resolution'],'保留现有字幕')
    def test_gpu_failure_resumes_cpu_and_preserves_confirmed_caption_and_chunks(self):
        from app.processing import Processor
        p=self.p;cue=p.data['cues'][0];p.edit([cue['id']],'approve');before=copy.deepcopy(cue)
        audio=p.root/'evidence/01/audio.wav';audio.parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(audio),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*16000*3)
        processor=Processor(threading.RLock());devices=[];checkpoint=audio.parent/'chunks/00000.json'
        def run(command,*args,**kw):
            devices.append(command[-1])
            if command[-1]=='cuda':atomic_json(checkpoint,{'done':'keep'});raise UserError('CUDA allocation error')
            self.assertEqual(json.loads(checkpoint.read_text()),{'done':'keep'})
            atomic_json(audio.parent/'transcript.json',{'segments':[],'execution':{'device':'cpu'}})
        profile={'device':'cuda','key':'test','message':'GPU faster'}
        with patch.object(a,'APP_ROOT',self.tmp),patch.object(processor,'automatic_backend',return_value=profile),patch.object(processor,'execute_child',side_effect=run),patch('app.processing.rule_check',return_value=[]):
            processor._run(p,['01'],'test-model','small')
        self.assertEqual(devices,['cuda','cpu']);self.assertEqual(p.data['jobs']['01']['status'],'done');self.assertEqual(p.data['jobs']['01']['device'],'cpu');self.assertEqual(cue,before)

del WorkflowTests
