import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.core import Project, UserError
from app.exchange import export_ai
from app.job_errors import explain_error
from app.processing import Processor


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'project').mkdir()
        self.p = Project.create(self.root/'project', '错误恢复')
        self.source = self.root/'01.srt'
        self.source.write_text('1\n00:00:00,100 --> 00:00:01,000\nHello.\n', encoding='utf-8')
        self.p.import_pairs([{'episode':ep, 'srt':str(self.source)} for ep in ('01','02')])

    def tearDown(self):
        self.p.close()
        self.tmp.cleanup()

    def test_failure_does_not_stop_other_episodes_and_keeps_detail(self):
        processor = Processor(threading.RLock())
        processor._run(self.p, ['01','02'], '', 'small')
        for ep in ('01','02'):
            job = self.p.data['jobs'][ep]
            self.assertEqual(job['status'], 'failed')
            self.assertIn('素材不可访问', job['error']['detail'])
            self.assertIn('重新定位', job['error']['action'])
        self.p.manage_episode('01', 'skip', '共享盘失联，先做文字审查')
        (self.root/'out').mkdir()
        result = export_ai(self.p, self.root/'out')
        index = json.loads((Path(result['directory'])/'项目与字幕索引.json').read_text(encoding='utf-8'))
        episode = next(e for e in index['episodes'] if e['episode']=='01')
        self.assertEqual(episode['local_job']['status'], 'skipped')
        self.assertIsNone(episode['transcript'])
        self.assertTrue(any('01' in x and '缺少本地识别稿' in x for x in result['missing']))
        self.assertEqual(len(self.p.data['cues']), 2)

    def test_exclude_restore_preserves_human_and_source(self):
        cue = self.p.data['cues'][0]
        self.p.edit([cue['id']], 'approve')
        cue['note'] = '人工备注'
        before = copy.deepcopy(cue)
        self.p.data['issues'] = [{'id':'test', 'episode':'01', 'cue_id':cue['id'], 'resolved':True}]
        self.p.manage_episode('01', 'exclude', '本轮暂不交付此集')
        self.assertNotIn('01', self.p.data['episodes'])
        self.assertFalse(any(c['episode']=='01' for c in self.p.snapshot()['cues']))
        self.assertEqual(self.p.data['issues'], [])
        self.assertTrue(self.source.exists())
        self.assertTrue((self.p.root/'originals/01.srt').exists())
        with self.assertRaises(UserError):
            self.p.undo()
        self.p.close()
        self.p = Project.open(self.root/'project')
        self.p.manage_episode('01', 'restore')
        self.assertEqual(self.p.by_id()[cue['id']], before)
        self.assertTrue(self.p.data['issues'][0]['resolved'])

    def test_cannot_skip_success_or_silently_overwrite_restored_episode(self):
        with self.assertRaises(UserError):
            self.p.manage_episode('01', 'skip', 'test')
        self.p.manage_episode('01', 'exclude', 'test')
        self.p.import_pairs([{'episode':'01', 'srt':str(self.source)}])
        with self.assertRaises(UserError):
            self.p.manage_episode('01', 'restore')

    def test_error_hints_keep_original_evidence(self):
        for message in ('CUDA out of memory', 'No space left on device', 'Permission denied', 'unknown failure 123'):
            explanation = explain_error(message)
            self.assertEqual(explanation['detail'], message)
            self.assertTrue(explanation['title'])
            self.assertTrue(explanation['action'])


if __name__ == '__main__':
    unittest.main()
