import copy,json,unittest
from pathlib import Path
from test_core import WorkflowTests
from app.core import digest,UserError,read_json,atomic_json
from app.exchange import export_ai,import_opinions,export_srt
from app.review import migrate,category,merge_plan,merge_apply,safe_correction
from app.processing import rule_check


class ReviewWorkflowTests(WorkflowTests):
    # Reuse the original safety fixture, without inheriting its test methods twice.
    pass


def fresh(self):
    script=self.inputs/'script.txt'
    script.write_text('Hello. I am here.',encoding='utf-8')
    self.p.add_reference(script,'script')
    dest=Path(export_ai(self.p,self.out)['directory'])
    idx=read_json(dest/'项目与字幕索引.json')
    d=read_json(dest/'AI审查意见_填写模板.json')
    for e in idx['entries']:
        d['opinions'].append({'opinion_id':'NEW-'+e['stable_id'],**{k:e[k] for k in ('stable_id','episode','original','start_ms','end_ms','snapshot_hash')},
                             'decision':'keep','suggestion':None,'reason':'已将本条和相邻字幕逐项对照，未发现具体文字问题。',
                             'confidence':'medium','needs_human':False,'chinese_audio':False,'audio_checked':False,
                             'evidence':[{'path':'current_srt/01.srt','detail':e['original']}],
                             'text_review':{'performed':True,'result':'clear','support':[{'type':'context','path':'current_srt/01.srt','quote':e['original']},
                              {'type':'script','path':idx['references'][0]['text_file'],'quote':e['original']}]}})
    return d


class ReviewTests(unittest.TestCase):
    setUp=WorkflowTests.setUp
    tearDown=WorkflowTests.tearDown

    def test_scope_does_not_create_human_tasks_but_unexecuted_review_stays_incomplete(self):
        c=self.p.data['cues'][0]
        self.p.data['issues']=[{'id':'local1','episode':'01','cue_id':c['id'],'resolved':False}]
        self.p.data['opinion_conflicts']=[{'id':'scope','reason':'AI剩余未决项','resolved':False,'opinion':{'remaining_id':'EP01-FULL-AUDIO','episode':'01','reason':'没有直接试听能力'}},
                                        {'id':'ref','reason':'AI剩余未决项','resolved':False,'opinion':{'remaining_id':'ISSUE-local1','episode':'01','reason':'原问题引用'}},
                                        {'id':'real','reason':'快照不匹配','resolved':False,'opinion':{}}]
        before=copy.deepcopy(self.p.data['cues'])
        migrate(self.p.data)
        self.assertEqual(self.p.data['cues'],before)
        self.assertEqual(len(self.p.data['opinion_conflicts']),1)
        self.assertEqual(len(self.p.data['review_limits']),1)
        self.assertEqual(len(self.p.data['issue_references']),1)
        self.assertEqual(category(c,self.p.data),'incomplete')
        self.assertFalse(migrate(self.p.data))

    def test_actual_clear_review_can_deliver_without_human_clicks(self):
        d=fresh(self);d['remaining']=[{'kind':'scope','episode':'01','reason':'直接试听未执行，不代表文字未核对'}]
        r=import_opinions(self.p,d)
        self.assertEqual(r['accepted'],2)
        self.assertTrue(all(c['status']=='pending' for c in self.p.data['cues']))
        self.assertTrue(all(category(c,self.p.data)=='text_clear' for c in self.p.data['cues']))
        self.assertEqual(self.p.validate_export(delivery=True),[])
        result=export_srt(self.p,self.out,delivery=True)
        self.assertTrue((Path(result['directory'])/'审查范围与结果.json').exists())
        self.assertTrue(import_opinions(self.p,d)['duplicate'])
        self.p.edit([self.p.data['cues'][0]['id']],'draft',{'text':'unconfirmed'})
        self.assertTrue(self.p.validate_export(delivery=True))

    def test_legacy_unable_with_claimed_coverage_is_not_promoted_to_clear(self):
        d=fresh(self)
        for o in d['opinions']:
            o.pop('text_review');o.update(decision='unable',needs_human=True)
        d['coverage']=[{'episode':'01','text_reviewed_stable_ids':[c['id'] for c in self.p.data['cues']]}]
        import_opinions(self.p,d)
        self.assertTrue(all(category(c,self.p.data)=='incomplete' for c in self.p.data['cues']))
        self.assertTrue(self.p.validate_export(delivery=True))

    def test_issue_reference_dedup_and_explicit_disposition(self):
        c=self.p.data['cues'][0]
        self.p.data['issues']=[{'id':'one','episode':'01','cue_id':c['id'],'resolved':False}]
        d=fresh(self);d['remaining']=[{'issue_id':'one','episode':'01','reason':'原问题'},{'issue_id':'one','episode':'01','reason':'同一问题重复引用'}]
        d['issue_reviews']=[{'issue_id':'one','result':'dismissed','reason':'词级和相邻字幕一致，旧提示跨条','evidence':[{'path':'current_srt/01.srt','detail':'Hello.'}]}]
        import_opinions(self.p,d)
        self.assertEqual(len(self.p.data['issue_references']),1)
        self.assertEqual(self.p.data['opinion_conflicts'],[])
        self.assertEqual(category(c,self.p.data),'text_clear')
        self.assertTrue(import_opinions(self.p,d)['duplicate'])

    def test_safe_correction_without_audio_and_risk_guards(self):
        c=self.p.data['cues'][0]
        o={'decision':'suggest','confidence':'high','needs_human':False,'chinese_audio':False,'audio_checked':False,
           'change_kind':'punctuation','meaning_unchanged':True,'suggestion':dict(c['accepted'],text='Hello!'),
           'text_review':{'result':'correction','support':[{'type':t,'path':t,'quote':'Hello'} for t in ('script','asr','context')]}}
        c['opinions']=[o];c['text_review']={**o['text_review'],'snapshot_hash':digest(c['accepted'])}
        self.assertEqual(safe_correction(c,o),'')
        self.p.apply_certain_opinions([c['id']])
        self.assertEqual(c['accepted']['text'],'Hello!')
        self.assertEqual(category(c,self.p.data),'ai_corrected')
        self.assertEqual(c['status'],'pending')
        self.assertEqual(self.p.apply_certain_opinions([c['id']])['applied'],0)
        self.assertIsNone(c['draft'])
        self.p.undo();c=self.p.by_id()[c['id']]
        self.assertEqual(c['accepted']['text'],'Hello.')
        for text in ['Hello 2!','Hello not!','Hello he!','Hello kingdom!']:
            unsafe=copy.deepcopy(o);unsafe['suggestion']['text']=text
            self.assertTrue(safe_correction(c,unsafe))
        missing=copy.deepcopy(o);missing['text_review']['support'].pop()
        self.assertTrue(safe_correction(c,missing))

    def test_new_handoff_has_ordered_workflow_and_self_check(self):
        root=Path(export_ai(self.p,self.out)['directory'])
        for name in ('AI审查执行流程.md','回传字段与自检.md','任务执行清单.json'):
            self.assertTrue((root/name).is_file())
        text=(root/'README_先读我.md').read_text(encoding='utf-8')
        self.assertIn('六步顺序',text)
        self.assertIn('不能只检查文件可读性或整包统一unable',text)
        task=read_json(root/'任务执行清单.json')
        self.assertEqual(task['workflow_version'],2)
        self.assertEqual(len(task['episodes'][0]['stable_ids']),2)

    def test_current_srt_only_cannot_claim_completed_text_comparison(self):
        d=fresh(self)
        for opinion in d['opinions']:
            opinion['text_review']['support']=opinion['text_review']['support'][:1]
        result=import_opinions(self.p,d)
        self.assertEqual(result['accepted'],0)
        self.assertEqual(result['conflicts'],2)

    def test_explicit_manual_hold_survives_prior_text_clear(self):
        import_opinions(self.p,fresh(self))
        c=self.p.data['cues'][0]
        self.p.edit([c['id']],'pending')
        self.assertEqual(category(c,self.p.data),'human')
        self.assertTrue(self.p.validate_export(delivery=True))

    def title_fixture(self):
        a,b=self.p.data['cues']
        for c,text,s,e in ((a,'Mr.',100,300),(b,'Payne,',300,900)):
            c['original'].update(text=text,start_ms=s,end_ms=e)
            c['accepted']=copy.deepcopy(c['original'])
        ref=self.inputs/'names.txt';ref.write_text('Mr. Payne',encoding='utf-8')
        self.p.add_reference(ref,'script')
        return a,b

    def test_merge_preview_apply_undo_reopen_mapping(self):
        a,b=self.title_fixture();plan=merge_plan(self.p)
        self.assertTrue(plan['candidates'][0]['eligible'])
        merge_apply(self.p,[a['id']],plan['token'])
        self.assertEqual(a['accepted']['text'],'Mr. Payne,')
        self.assertEqual(a['accepted']['start_ms'],100)
        self.assertEqual(a['accepted']['end_ms'],900)
        self.assertEqual(b['merged_into'],a['id'])
        self.assertNotEqual(a['status'],'approved')
        text,mapping=self.p.srt_text('01')
        self.assertEqual(mapping[1]['merged_into'],a['id'])
        self.assertNotIn('2\n',text)
        self.p.close();self.p=__import__('app.core',fromlist=['Project']).Project.open(self.p.root)
        self.p.undo()
        self.assertEqual(self.p.by_id()[a['id']]['accepted']['text'],'Mr.')
        self.assertFalse(self.p.by_id()[b['id']]['accepted']['deleted'])
        self.assertNotIn('merged_into',self.p.by_id()[b['id']])

    def test_merge_protects_gap_cross_episode_speaker_notes_and_human_edits(self):
        a,b=self.title_fixture()
        for key,value in [('note','keep'),('status','approved'),('draft',dict(a['accepted']))]:
            old=copy.deepcopy(a);a[key]=value
            self.assertFalse(merge_plan(self.p)['candidates'][0]['eligible'])
            a.clear();a.update(old)
        b['accepted']['start_ms']=2000
        self.assertFalse(merge_plan(self.p)['candidates'][0]['eligible'])
        b['accepted']['start_ms']=300;b['episode']='02'
        self.assertFalse(merge_plan(self.p)['candidates'][0]['eligible'])
        b['episode']='01';a['speaker']='A';b['speaker']='B'
        self.assertFalse(merge_plan(self.p)['candidates'][0]['eligible'])

    def term_fixture(self):
        from app.terminology import plan
        script=self.inputs/'terms.txt';script.write_text('Starlight King. Starlight Kingdom.',encoding='utf-8')
        self.p.add_reference(script,'script')
        for c in self.p.data['cues']:
            c['accepted']['text']='the Starlite King.'
            c['text_review']={'performed':True,'result':'human','snapshot_hash':digest(c['accepted'])}
            c['opinions']=[{'reason':'术语正式拼写差异','term_review':{'canonical':'Starlight King'},'review_summary':{'script_text':'Starlight King'}}]
        return plan(self.p)

    def test_term_policy_applies_once_and_undo_preserves_review(self):
        from app.terminology import apply,context,plan
        preview=self.term_fixture();before=copy.deepcopy(self.p.data['cues'])
        self.assertEqual(len(preview['groups']),1)
        self.assertEqual(category(self.p.data['cues'][0],context(self.p)[0]),'term')
        apply(self.p,preview['groups'][0]['id'],'script','',preview['token'])
        self.assertTrue(all(c['accepted']['text']=='the Starlight King.' for c in self.p.data['cues']))
        self.assertTrue(all(c['status']!='approved' for c in self.p.data['cues']))
        self.assertEqual(plan(self.p)['groups'][0]['pending'],0)
        self.assertFalse(self.p.validate_export(delivery=True))
        self.p.undo();self.assertEqual(self.p.data['cues'],before)

    def test_term_policy_preserves_human_work_and_other_timing_problem(self):
        from app.terminology import apply,context
        self.term_fixture();a,b=self.p.data['cues'];a['note']='保留人工备注'
        self.p.data['issues']=[{'id':'timing','episode':'01','cue_id':b['id'],'kind':'入出点','resolved':False,'triage':{'result':'retained','reason':'边界需试听'}}]
        preview=context(self.p)[1];old=copy.deepcopy(a)
        result=apply(self.p,preview['groups'][0]['id'],'current','',preview['token'])
        self.assertEqual(result['skipped_protected'],1);self.assertEqual(a,old)
        self.assertEqual(category(b,context(self.p)[0]),'human')
        self.assertTrue(self.p.validate_export(delivery=True))
        self.assertEqual(b['accepted']['text'],'the Starlite King.')

    def test_term_king_and_kingdom_are_separate_groups(self):
        from app.terminology import plan,apply
        self.term_fixture();extra=[]
        for c in self.p.data['cues']:
            other=copy.deepcopy(c);other['id']+='-kingdom';other['accepted']['text']='Starlite Kingdom.'
            other['opinions'][0]['term_review']['canonical']='Starlight Kingdom';other['text_review']['snapshot_hash']=digest(other['accepted']);extra.append(other)
        self.p.data['cues'].extend(extra);preview=plan(self.p)
        self.assertEqual({g['canonical'] for g in preview['groups']},{'Starlight King','Starlight Kingdom'})
        king=next(g for g in preview['groups'] if g['canonical']=='Starlight King')
        apply(self.p,king['id'],'audio','Starborn King',preview['token'])
        self.assertTrue(all(c['accepted']['text']=='Starlite Kingdom.' for c in extra))

    def test_outside_caption_evidence_does_not_block_but_timing_still_does(self):
        from app.review import task_in_scope
        for c in self.p.data['cues']:c['text_review']={'performed':True,'result':'clear','snapshot_hash':digest(c['accepted'])}
        self.p.data['issues']=[{'id':'outside','episode':'01','cue_id':None,'kind':'可疑漏句','resolved':False}]
        self.p.data['review_tasks']=[{'id':'missing','episode':'01','reason':'无对应字幕的可疑漏句','resolved':False}]
        self.assertFalse(task_in_scope(self.p.data,self.p.data['review_tasks'][0]))
        self.assertFalse(self.p.validate_export(delivery=True))
        self.assertFalse(self.p.data['issues'][0]['resolved'])
        self.p.data['review_tasks'].append({'id':'timing','episode':'01','reason':'局部时间错位','resolved':False})
        self.assertTrue(self.p.validate_export(delivery=True))

    def test_portable_processing_defaults_to_local_model_and_never_downloads_missing_model(self):
        from unittest.mock import patch
        from app.processing import Processor
        import threading
        portable=self.tmp/'portable';portable.mkdir();(portable/'portable.json').write_text('{}')
        processor=Processor(threading.RLock())
        with patch('app.processing.APP_ROOT',portable):
            with self.assertRaises(UserError):processor.start(self.p,['01'])
            model=portable/'.runtime/models/test';model.mkdir(parents=True);(model/'model.bin').write_bytes(b'test')
            with patch('app.processing.threading.Thread') as thread:
                processor.start(self.p,['01'])
                self.assertEqual(thread.call_args.kwargs['args'][2],str(model))

    def test_word_alignment_avoids_whole_segment_false_positive(self):
        a,b=self.p.data['cues']
        transcript={'segments':[{'start':.1,'end':2,'text':'Hello I am here','words':[
            {'start':.1,'end':1.2,'word':'Hello'},{'start':1.3,'end':1.5,'word':'I'},
            {'start':1.5,'end':1.7,'word':'am'},{'start':1.7,'end':2,'word':'here'}]}]}
        issues=rule_check(self.p,'01',transcript)
        self.assertFalse(any(i['kind']=='文字差异' for i in issues))


del ReviewWorkflowTests
del WorkflowTests
