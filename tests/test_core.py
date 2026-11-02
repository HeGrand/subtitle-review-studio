import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app.core import Project, UserError, parse_srt, atomic_json, digest, scan_pairs
from app.exchange import export_srt, export_ai, import_opinions, backup
from app.processing import rule_check

ROOT = Path(os.environ.get('REVIEW_TEST_ROOT', str(Path(tempfile.gettempdir()) / 'subtitle-review-tests')))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='case-', dir=ROOT))
        (self.tmp / 'project').mkdir()
        self.p = Project.create(self.tmp / 'project', '验证项目')
        self.inputs = self.tmp / 'inputs'
        self.inputs.mkdir()
        self.out = self.tmp / 'exports'
        self.out.mkdir()
        self.srt = self.inputs / '01.srt'
        self.srt.write_text('1\n00:00:00,100 --> 00:00:01,200\nHello.\n\n2\n00:00:01,300 --> 00:00:02,000\nI am here.\n', encoding='utf-8')
        self.p.import_pairs([{'episode': '01', 'srt': str(self.srt)}])

    def tearDown(self):
        if not self.p.lock.closed:
            self.p.close()
        # Test artifacts remain outside the application; keep for inspection.

    def test_draft_approval_delete_undo(self):
        c = self.p.data['cues'][0]
        self.p.edit([c['id']], 'draft', {'text': 'Good morning.', 'start_ms': 200})
        text, mapping = self.p.srt_text('01')
        self.assertIn('Hello.', text)
        self.assertNotIn('Good morning.', text)
        with self.assertRaises(UserError):
            export_srt(self.p, self.out, delivery=True)
        self.p.edit([c['id']], 'approve')
        self.assertIn('Good morning.', self.p.srt_text('01')[0])
        second_time = copy.deepcopy(self.p.data['cues'][1]['accepted'])
        self.p.edit([c['id']], 'delete')
        text, mapping = self.p.srt_text('01')
        self.assertNotIn('Good morning.', text)
        self.assertEqual(mapping[1]['exported_number'], 1)
        self.assertEqual(second_time, self.p.data['cues'][1]['accepted'])
        self.p.undo()
        self.assertIn('Good morning.', self.p.srt_text('01')[0])

    def test_single_writer_reopen_isolation_and_cleanup(self):
        c = self.p.data['cues'][0]
        self.p.edit([c['id']], 'draft', {'text': 'A draft'})
        self.p.edit([c['id']], 'note', {'note': '保留备注'})
        with self.assertRaises(UserError):
            Project.open(self.p.root)
        temp = self.p.root / 'cache/tmp/a.wav'
        temp.write_bytes(b'temp')
        evidence = self.p.root / 'evidence/keep.wav'
        evidence.write_bytes(b'evidence')
        plan = self.p.cleanup_plan()
        self.assertEqual([x['path'] for x in plan['files']], [str(temp.relative_to(self.p.root))])
        self.p.cleanup(plan['token'])
        self.assertTrue(evidence.exists())
        self.assertTrue(self.srt.exists())
        self.p.close()
        (self.tmp / 'B').mkdir()
        b = Project.create(self.tmp / 'B', 'B')
        self.assertEqual(b.data['cues'], [])
        b.close()
        self.p = Project.open(self.p.root)
        c = self.p.by_id()[c['id']]
        self.assertEqual(c['draft']['text'], 'A draft')
        self.assertEqual(c['note'], '保留备注')
        self.assertEqual(c['status'], 'pending')

    def test_ai_roundtrip_dedupe_snapshot_and_evidence(self):
        result = export_ai(self.p, self.out)
        root = Path(result['directory'])
        data = json.loads((root / 'AI审查意见_填写模板.json').read_text(encoding='utf-8'))
        example = json.loads((root / '输出示例_不可直接当真实意见.json').read_text(encoding='utf-8'))
        data['opinions'] = example['opinions']
        data['opinions'][0]['evidence'].append({'path':'项目与字幕索引.json','detail':'从稳定ID索引核对快照'})
        self.assertEqual(import_opinions(self.p, data)['accepted'], 1)
        self.assertTrue(import_opinions(self.p, data)['duplicate'])
        data['opinions'][0]['opinion_id'] = 'missing-evidence'
        data['opinions'][0]['evidence'] = []
        self.assertEqual(import_opinions(self.p, data)['conflicts'], 1)
        data['project_version'] += 1
        with self.assertRaises(UserError):
            import_opinions(self.p, data)
        readme = (root / 'README_先读我.md').read_text(encoding='utf-8')
        self.assertIn('你要执行本项目的字幕审查', readme)
        self.assertIn('不能宣称整集', readme)
        for textfile in root.rglob('*.json'):
            self.assertNotIn(str(self.inputs), textfile.read_text(encoding='utf-8'))

    def test_ai_contradiction_never_changes_baseline(self):
        result = export_ai(self.p, self.out)
        root = Path(result['directory'])
        d = json.loads((root / '输出示例_不可直接当真实意见.json').read_text(encoding='utf-8'))
        o = d['opinions'][0]
        o.update(opinion_id='one',decision='suggest',suggestion={'text':'One','start_ms':100,'end_ms':1200,'deleted':False})
        two = copy.deepcopy(o)
        two.update(opinion_id='two',suggestion={'text':'Two','start_ms':100,'end_ms':1200,'deleted':False})
        d['opinions'].append(two)
        r=import_opinions(self.p,d)
        self.assertEqual(r['accepted'],0)
        self.assertIn('Hello.',self.p.srt_text('01')[0])

    def test_55_episodes_missing_duplicate_partial_failure(self):
        batch = self.tmp / 'batch'
        media = self.tmp / 'media'
        batch.mkdir();media.mkdir()
        for i in range(2,57):
            shutil.copy2(self.srt,batch/f'{i:02}.srt')
        (batch/'ep02-v2.srt').write_text('invalid',encoding='utf-8')
        (batch/'57.srt').write_text('',encoding='utf-8')
        scan=scan_pairs(str(media),str(batch))
        self.assertEqual(len(scan['pairs']),56)
        self.assertEqual(len(scan['pairs'][0]['srt']),2)
        self.assertTrue(scan['pairs'][0]['warnings'])
        r=self.p.import_pairs([{'episode':f'{i:02}','srt':str(batch/f'{i:02}.srt')} for i in range(2,58)])
        self.assertEqual(sum('count' in x for x in r),55)
        self.assertEqual(sum('error' in x for x in r),1)
        self.assertEqual(len(self.p.data['episodes']),56)

    def test_chinese_empty_recognition_and_timing(self):
        self.p.data['episodes']['01']['duration_ms']=2000
        t={'segments':[{'start':.2,'end':.8,'text':'你好','language':'zh'}]}
        issues=rule_check(self.p,'01',t)
        self.assertTrue(any(i['kind']=='中文或疑似中文' for i in issues))
        self.assertTrue(any(i['kind']=='识别不确定' for i in issues))
        self.assertEqual(self.p.data['cues'][0]['accepted']['deleted'],False)

    def test_invalid_timing_draft_is_preserved_then_can_be_fixed(self):
        c=self.p.data['cues'][0]
        self.p.edit([c['id']],'editor',{'text':'Saved draft','start_input':'bad','end_input':'00:00:01,200','note':'note'})
        self.assertTrue(c['draft_time_error'])
        with self.assertRaises(UserError):
            self.p.edit([c['id']],'approve')
        self.p.edit([c['id']],'editor',{'text':'Saved draft','start_input':'00:00:00,120','end_input':'00:00:01,200','note':'note'})
        self.assertNotIn('draft_time_error',c)
        self.p.edit([c['id']],'approve')
        self.assertEqual(c['accepted']['text'],'Saved draft')

    def test_transfer_portable_without_original_source(self):
        c=self.p.data['cues'][0]
        self.p.edit([c['id']],'note',{'note':'handoff note'})
        result=backup(self.p,self.out)
        transferred=Project.open(result['directory'])
        try:
            self.assertEqual(transferred.data['cues'][0]['note'],'handoff note')
            self.assertTrue((transferred.root/transferred.data['episodes']['01']['original_srt']).exists())
            self.assertEqual(transferred.data['episodes']['01']['media'],'')
        finally:
            transferred.close()

    def test_relink_same_fingerprint_and_reject_new_version(self):
        media=self.inputs/'01.wav'
        media.write_bytes(b'original-media')
        self.p.relink('01',str(media))
        moved=self.inputs/'moved.wav'
        shutil.copy2(media,moved)
        self.p.relink('01',str(moved))
        media.write_bytes(b'new-version')
        with self.assertRaises(UserError):
            self.p.relink('01',str(media))
        self.assertEqual(self.p.data['episodes']['01']['media'],str(moved.resolve()))

    def test_pdf_text_and_scan_detection(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        scan=self.inputs/'scan.pdf'
        writer=PdfWriter();writer.add_blank_page(width=612,height=792)
        with scan.open('wb') as f:writer.write(f)
        with self.assertRaises(UserError):self.p.add_reference(scan,'script')
        page=writer.pages[0]
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content=DecodedStreamObject();content.set_data(b'BT /F1 12 Tf 50 700 Td (Gareth: Tell him.) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(content)
        text=self.inputs/'script.pdf'
        with text.open('wb') as f:writer.write(f)
        self.p.add_reference(text,'script')
        ref=self.p.data['references'][0]
        self.assertIn('Gareth: Tell him.',(self.p.root/ref['text_file']).read_text(encoding='utf-8'))

    def test_docx_script_preserves_paragraphs_and_tables_in_ai_package(self):
        import zipfile
        script=self.inputs/'script.docx'
        xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Gareth: </w:t></w:r><w:r><w:t>Tell him.</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table dialogue.</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
        with zipfile.ZipFile(script,'w') as z:z.writestr('word/document.xml',xml)
        self.p.add_reference(script,'script')
        ref=self.p.data['references'][0]
        self.assertEqual((self.p.root/ref['text_file']).read_text(encoding='utf-8'),'Gareth: Tell him.\nTable dialogue.')
        result=export_ai(self.p,self.out)
        self.assertTrue((Path(result['directory'])/ref['file']).exists())
        self.assertIn('Table dialogue.',(Path(result['directory'])/ref['text_file']).read_text(encoding='utf-8'))
        broken=self.inputs/'broken.docx';broken.write_bytes(b'not a docx')
        with self.assertRaises(UserError):self.p.add_reference(broken,'script')

    def test_only_safe_certain_text_can_be_batch_adopted(self):
        a,b=self.p.data['cues']
        for c,text in ((a,'Hello!'),(b,'I am not here.')):
            c['opinions']=[{'decision':'suggest','confidence':'high','needs_human':False,'chinese_audio':False,'audio_checked':True,
                            'change_kind':'punctuation','meaning_unchanged':True,
                            'suggestion':dict(c['accepted'],text=text)}]
        r=self.p.apply_certain_opinions([a['id'],b['id']])
        self.assertEqual(r['applied'],1)
        self.assertEqual(len(r['skipped']),1)
        self.assertEqual(a['draft']['text'],'Hello!')
        self.assertIsNone(b['draft'])

    def test_source_loss_can_use_cached_evidence_and_other_episode_continues(self):
        import wave
        import threading
        import time
        from app.processing import Processor
        audio=self.inputs/'02.wav'
        with wave.open(str(audio),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*40000)
        self.p.import_pairs([{'episode':'02','srt':str(self.srt),'media':str(audio)}])
        epdir=self.p.root/'evidence/02'
        epdir.mkdir(parents=True)
        shutil.copy2(audio,epdir/'audio.wav')
        atomic_json(epdir/'transcript.json',{'duration':2.5,'segments':[]})
        audio.rename(audio.with_name('moved-offline.wav'))
        worker=Processor(threading.RLock())
        worker.start(self.p,['01','02'])
        deadline=time.monotonic()+20
        while worker.active() and time.monotonic()<deadline:time.sleep(.1)
        if worker.active():worker.pause()
        self.assertEqual(self.p.data['jobs']['01']['status'],'failed')
        self.assertEqual(self.p.data['jobs']['02']['status'],'done')
        self.assertTrue((epdir/'audio.wav').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
