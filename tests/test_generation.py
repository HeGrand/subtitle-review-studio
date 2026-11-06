import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import Project, UserError, parse_srt
from app import ai_config
from app.generation import add_video, ai_wrap_lines, build_cues, confirm_lines, import_script, install_cues, parse_script
from app.processing import Processor
from app.exchange import export_ai, export_srt


def transcript(text):
    words = text.split()
    return {'duration': len(words) * .3 + 1, 'segments': [{'text': text, 'words': [
        {'word': ' ' + w, 'start': i * .3, 'end': (i + 1) * .3, 'probability': .99} for i, w in enumerate(words)]}]}


def script_line(text):
    return {'episode': '32', 'language': 'en', 'speaker': 'ROSE', 'text': text,
            'source': {'path': 'script.txt', 'line': 1, 'text': text}}


class GenerationTests(unittest.TestCase):
    def test_partial_script_is_flagged_but_a_complete_line_split_by_pause_is_not(self):
        line = script_line('Nobody asked you.')
        settings = {'script_only': True}
        partial = build_cues(transcript('asked you.'), [line], 'en', settings)[0]
        self.assertEqual(partial['script_coverage']['missing'], [0])
        self.assertTrue(any('部分词' in reason for reason in partial['reasons']))
        heard = transcript('Nobody asked you.')
        for word in heard['segments'][0]['words'][1:]:
            word['start'] += .6
            word['end'] += .6
        rows = build_cues(heard, [line], 'en', settings)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all('script_coverage' not in row for row in rows))

    def test_numeric_forms_and_insertions_stay_on_script(self):
        settings = {'script_only': True}
        for spoken, written in [('100.', 'One hundred.'), ('one hundred and twenty one.', '121.')]:
            with self.subTest(spoken=spoken):
                extras = []
                rows = build_cues(transcript(spoken), [script_line(written)], 'en', settings, extras)
                self.assertEqual(rows[0]['text'], written)
                self.assertFalse(extras)
        extras = []
        rows = build_cues(transcript('Nobody really asked you.'), [script_line('Nobody asked you.')],
                          'en', settings, extras)
        self.assertEqual(rows[0]['text'], 'Nobody asked you.')
        self.assertEqual([[word['norm'] for word in part] for part in extras], [['really']])

    def test_repeated_short_script_line_remains_a_reviewable_cue(self):
        extras = []
        rows = build_cues(transcript('Rose.'), [script_line('Rose.'), script_line('Rose.')],
                          'en', {'script_only': True}, extras)
        self.assertEqual([row['text'] for row in rows], ['Rose.'])
        self.assertFalse(extras)
        self.assertIsNone(rows[0]['generation_source'])
        self.assertTrue(any('重复候选' in reason for reason in rows[0]['reasons']))

    def test_unmatched_audio_blocks_only_delivery_and_can_be_resolved_by_approval(self):
        add_video(self.p, 32, self.media, 'en')
        confirm_lines(self.p, 32, 'en', ['Yes.', 'Go.'])
        heard = transcript('Yes. No! Go.')
        install_cues(self.p, '32', heard)
        cues = self.p.data['cues']
        self.assertEqual(cues[0]['accepted']['end_ms'], 300)
        self.p.edit([c['id'] for c in cues], 'approve')
        issue = self.p.data['issues'][0]
        issue['triage'] = {'result': 'dismissed', 'reason': 'AI 建议排除'}
        self.assertEqual(self.p.validate_export(), [])
        self.assertTrue(any('No!' in error for error in self.p.validate_export(delivery=True)))
        cid = self.p.add_cue('32', {'text': 'No!', 'start_ms': 300, 'end_ms': 600}, issue['id'])
        self.p.edit([cid], 'approve')
        self.assertEqual(self.p.validate_export(delivery=True), [])
        self.p.undo()
        self.assertFalse(self.p.data['issues'][0]['resolved'])

    def test_approved_cue_keeps_new_evidence_pending(self):
        from app.review import add_post_approval_evidence, reconcile_human_tasks
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript('Hello.'))
        cue = self.p.data['cues'][0]
        self.p.edit([cue['id']], 'approve')
        add_post_approval_evidence(self.p.data, cue, {
            'start_ms': 0, 'end_ms': 800, 'reason': '新证据待试听', 'evidence': []})
        reconcile_human_tasks(self.p.data)
        task = self.p.data['review_tasks'][0]
        self.assertFalse(task['resolved'])
        self.assertEqual(self.p.snapshot()['cues'][0]['category'], 'human')
        self.assertEqual(cue['status'], 'approved')
        self.assertEqual(self.p.validate_export(), [])
        self.assertTrue(self.p.validate_export(delivery=True))
        task['resolved'] = True
        self.assertEqual(self.p.validate_export(delivery=True), [])

    def test_approve_uses_neighbor_draft_start_and_preserves_its_unconfirmed_text(self):
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript('Hello. Goodbye.'))
        first, second = self.p.data['cues']
        edge = second['accepted']['start_ms']
        self.p.edit([first['id']], 'draft', {'end_ms': edge + 30})
        self.p.edit([second['id']], 'draft', {'start_ms': edge + 50, 'text': 'Draft text.'})
        outcome = self.p.edit([first['id']], 'approve')
        self.assertEqual(outcome['shifted'][0]['start_ms'], edge + 50)
        self.assertEqual(second['accepted']['text'], 'Goodbye.')
        self.assertEqual(second['draft']['text'], 'Draft text.')
        self.assertEqual(second['status'], 'pending')
        self.p.undo()
        self.assertEqual(second['accepted']['start_ms'], edge)

    def test_approve_shifts_next_start_and_can_accept_an_onset_suggestion(self):
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript('Hello. Goodbye.'))
        first, second = self.p.data['cues']
        edge = second['accepted']['start_ms']
        first['time_adjustments'] = [{'field': 'start_ms', 'from': 0, 'to': 80, 'applied': False}]
        self.p.edit([first['id']], 'draft', {'end_ms': edge + 30})
        with self.assertRaisesRegex(UserError, '已变化'):
            self.p.edit([first['id']], 'approve', {'onset_target': 90})
        self.p.edit([first['id']], 'approve', {'onset_target': 80})
        self.assertEqual(first['accepted']['start_ms'], 80)
        self.assertEqual(second['accepted']['start_ms'], edge + 30)
        self.assertTrue(first['time_adjustments'][0]['applied'])
        self.assertEqual(first['status'], 'approved')
        self.assertEqual(second['status'], 'pending')
        self.p.undo()
        self.assertFalse(first['time_adjustments'][0]['applied'])
        self.assertEqual(second['accepted']['start_ms'], edge)

    def test_approval_rejects_overlap_and_delivery_checks_layout(self):
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript('Hello.'))
        first = self.p.data['cues'][0]
        cid = self.p.add_cue('32', {'text': 'Overlap.', 'start_ms': 100, 'end_ms': 400})
        with self.assertRaisesRegex(UserError, '重叠'):
            self.p.edit([cid], 'approve')
        self.p.edit([cid], 'delete')
        self.p.edit([first['id']], 'draft', {'text': 'Hello，\nthere.\n' + 'a' * 45})
        self.p.edit([first['id']], 'approve')
        self.assertEqual(self.p.validate_export(), [])
        errors = self.p.validate_export(delivery=True)
        self.assertTrue(any('44 字符' in error for error in errors))
        self.assertTrue(any('半角' in error for error in errors))

    def test_partial_api_failure_keeps_successful_batch(self):
        from app.server import Handler, STATE
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript(' '.join(['Hello.'] * 13)))
        calls = []

        def chat(messages, **kwargs):
            rows = json.loads(messages[-1]['content'])
            calls.append(len(rows))
            if len(calls) == 2:
                raise UserError('连接中断')
            return json.dumps({'findings': [{'id': row['id'], 'reason': '核对台词'} for row in rows]})

        handler = object.__new__(Handler)
        with patch.object(STATE, 'project', self.p), patch('app.ai_config.chat', side_effect=chat):
            def run(**values):
                return handler.ai_review_episode({'project_id': self.p.data['project_id'], 'episode': '32',
                                                  'revision': self.p.data['revision'], **values})

            first = run()
            self.assertEqual((first['reviewed'], first['unreviewed']), (12, 1))
            self.assertEqual(first['failures'], ['连接中断'])
            self.assertEqual(sum(bool(c.get('api_review')) for c in self.p.data['cues']), 12)
            self.assertEqual(self.p.data['api_review_runs']['32'], first)
            self.assertEqual(run(resume=True)['reviewed'], 1)
        self.assertEqual(calls, [12, 1, 1])

    def test_script_only_filters_extras_and_keeps_located_repeated_and_uncertain_lines(self):
        lines = [script_line(text) for text in ('Yes.', 'Yes?', 'Yes.', 'That easy?',
                 'Why?', 'Gauntlet business.', "I'll be back tomorrow.", 'Unspoken ending.')]
        heard = transcript("Yes. Yes? Yes. That easy? Oopsie! Why? Call it business. I'll be back tomorrow.")
        settings = {'script_only': True}
        rows = build_cues(heard, lines, 'en', settings)
        self.assertEqual([row['text'] for row in rows], [line['text'] for line in lines[:-1]])
        self.assertTrue(rows[5]['reasons'])
        self.assertIn('Oopsie!', [row['text'] for row in build_cues(heard, [], 'en', settings)])
        with self.assertRaisesRegex(UserError, '没有定位'):
            build_cues(transcript('Something else.'), lines, 'en', settings)

    def test_matching_number_spellings_do_not_break_dialogue(self):
        lines = [script_line('everyone shifts by twenty. I’m twenty-one.')]
        for text in ("everyone shifts by 20. I'm 21.", "everyone shifts by twenty. I'm twenty one."):
            self.assertEqual([r['text'] for r in build_cues(transcript(text), lines, 'en')],
                             ["everyone shifts by twenty.", "I'm twenty-one."])
        rows = build_cues(transcript('everyone shifts by 21.'), [script_line('everyone shifts by twenty.')], 'en')
        self.assertIn('21.', ' '.join(row['text'] for row in rows))
        self.assertTrue(any(row['reasons'] for row in rows))
        rows = build_cues(transcript("If you go, i'll go."), [script_line('If you go,'), script_line("i'll go.")], 'en')
        self.assertEqual(rows[-1]['text'], "I'll go.")

    def test_partial_suffix_keeps_spoken_prefix_with_full_script_candidate(self):
        line = script_line('Nobody asked you.')
        rows = build_cues(transcript('But he asked you.'), [line], 'en')
        self.assertEqual([row['text'] for row in rows], ['But he asked you.'])
        self.assertEqual(rows[0]['generation_source']['script_excerpt'], line['text'])
        self.assertEqual(rows[0]['generation_source']['script_status'], 'context_candidate')

    def test_deleting_short_prefix_reopens_suffix_until_reviewed(self):
        add_video(self.p, 32, self.media, 'en')
        heard = transcript('But he asked you.')
        rows = build_cues(heard, [], 'en')
        template = rows[0]
        rows = [dict(template, text='But he', end_ms=200),
                dict(template, text='asked you.', start_ms=200, generation_source={
                    'script_status': 'matched', 'script_text': 'Nobody asked you.',
                    'script_excerpt': 'asked you.', 'script_source': None})]
        install_cues(self.p, '32', heard, prepared_rows=rows)
        first, second = [c['id'] for c in self.p.data['cues']]
        self.p.edit([second], 'approve')
        self.p.edit([first], 'delete')
        snapshot = self.p.snapshot()['cues'][1]
        self.assertEqual(snapshot['category'], 'human')
        self.assertEqual(snapshot['script_context']['script_excerpt'], 'Nobody asked you.')
        self.p.edit([second], 'approve')
        self.assertEqual(self.p.snapshot()['cues'][1]['category'], 'approved')

    def test_generation_exports_only_srt_for_current_and_delivery(self):
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript('Hello there.'))
        current = export_srt(self.p, self.root)
        self.p.edit([self.p.data['cues'][0]['id']], 'approve')
        delivery = export_srt(self.p, self.root, delivery=True)
        for result in (current, delivery):
            folder = Path(result['directory'])
            self.assertEqual(list(folder.iterdir()), [folder / '32.srt'])
            self.assertEqual(result['files'], [str(folder / '32.srt')])
            self.assertFalse(folder.with_suffix('.zip').exists())
        self.assertEqual(self.p.data['exports'][-1]['type'], 'delivery')

    def test_generation_calibrates_small_onset_shift_and_flags_large_shift(self):
        add_video(self.p, 32, self.media, 'en')
        heard = transcript('Hello there. Goodbye for now.')
        for word in heard['segments'][0]['words'][2:]:
            word['start'] += 1
            word['end'] += 1
        heard['duration'] += 1
        install_cues(self.p, '32', heard, [(240, 600), (2300, 2500)])
        first, second = self.p.data['cues']
        self.assertEqual(first['accepted']['start_ms'], 120)
        self.assertEqual(second['accepted']['start_ms'], 1600)
        self.assertIn('建议校准入点', second['note'])
        self.assertTrue(first['time_adjustments'][0]['applied'])
        self.assertFalse(second['time_adjustments'][0]['applied'])

    def test_ai_wrap_repairs_sentence_and_article_breaks(self):
        bad = ['It owns the police, the', 'power, every screen in every home.']
        with patch('app.ai_config.chat', return_value=json.dumps([bad])):
            self.assertEqual(ai_wrap_lines('Meridian. ' + ' '.join(bad), 'en', 42),
                             ['Meridian.', 'It owns the police, the power,', 'every screen in every home.'])

    def test_pdf_line_wrapping_is_repaired_before_hard_sentence_cuts(self):
        from app.generation import ai_wrap_groups, join_extracted_dialogue
        source = [
            {'line': 14, 'speaker': 'Dex', 'text': 'Meridian. It owns the police, the'},
            {'line': 15, 'speaker': 'DEX', 'text': 'power, every screen in every home.'},
            {'line': 32, 'speaker': 'Prophet', 'text': 'Six hostiles. Left gunner fires in'},
            {'line': 33, 'speaker': 'Prophet', 'text': 'two seconds.'},
            {'line': 38, 'speaker': 'Runner', 'text': "That's Zero. Light him up!"},
            {'line': 39, 'speaker': 'Runner', 'text': 'Dr. Chen has 2.5 seconds. Ready? Go!'},
        ]
        joined = join_extracted_dialogue(source, 'en')
        self.assertEqual([(r['line'], r['end_line']) for r in joined], [(14, 15), (32, 33), (38, 38), (39, 39)])
        with patch('app.ai_config.chat', return_value=json.dumps([['It owns the police, the', 'power, every screen in every home.']])):
            groups, _ = ai_wrap_groups([r['text'] for r in joined], 'en', 42)
        self.assertEqual(groups, [
            ['Meridian.', 'It owns the police, the power,', 'every screen in every home.'],
            ['Six hostiles.', 'Left gunner fires in two seconds.'],
            ["That's Zero.", 'Light him up!'],
            ['Dr. Chen has 2.5 seconds.', 'Ready?', 'Go!'],
        ])

    def test_ai_wrap_preserves_words_and_punctuation_and_uses_timed_boundaries(self):
        long_line = "You always have an answer after you've already hurt me."
        short_line = "...Then why do I still want you to stay?"
        expected = ["You always have an answer", "after you've already hurt me.", short_line[3:]]
        with patch('app.ai_config.chat', return_value=json.dumps([expected[:2]])):
            lines = ai_wrap_lines(long_line + '\n' + short_line, 'en', 42)
        self.assertEqual(lines, expected)
        heard = transcript(long_line + ' ' + short_line[3:])
        for source in ([long_line, short_line], lines):
            rows = build_cues(heard, [script_line(line) for line in source], 'en')
            self.assertEqual([r['text'] for r in rows], expected)
            self.assertEqual(rows[1]['start_ms'], 1500)
        with patch('app.ai_config.chat', return_value=json.dumps([["You always have an answer", "after you've hurt me."]])):
            with self.assertRaises(UserError):
                ai_wrap_lines(long_line, 'en', 42)
        self.assertEqual(build_cues(transcript('Wait...'), [script_line('Wait...')], 'en')[0]['text'], 'Wait...')

    def setUp(self):
        base = Path(os.environ.get('REVIEW_TEST_ROOT', tempfile.gettempdir())).resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.tmp.name)
        folder = self.root / 'project'; folder.mkdir()
        self.p = Project.create(folder, 'generation test')
        self.p.data.update(mode='generation', review_scope='generated_subtitles')
        self.script = self.root / '剧本.txt'
        self.script.write_text("EPISODE 32\n第三十二集\nROSE\n萝丝\nYou're here!\n你来了！\nRose leaves.\n萝丝离开。\nEPISODE 33\nROSE\nGoodbye.\n再见。\n", encoding='utf-8')
        self.media = self.root / '32.wav'; self.media.write_bytes(b'test media')

    def tearDown(self):
        if not self.p.lock.closed:
            self.p.close()
        assert self.root.resolve().is_relative_to(self.base)
        self.tmp.cleanup()

    def test_bilingual_parser_excludes_role_names_and_stage_directions(self):
        data = parse_script(self.script.read_text(encoding='utf-8'), 'script.txt')
        self.assertEqual([x['text'] for x in data['lines']], ["You're here!", '你来了！', 'Goodbye.', '再见。'])
        self.assertEqual(set(data['episode_source']), {'32', '33'})

    def test_actual_words_win_over_changed_script(self):
        result = build_cues(transcript('I was trying to save you.'), [script_line('I was trying to protect you.')], 'en')
        text = ' '.join(x['text'].replace('\n', ' ') for x in result)
        self.assertIn('save', text)
        self.assertNotIn('protect', text)
        self.assertTrue(any(x['reasons'] for x in result))
        self.assertTrue(all(not x['generation_source'] or x['generation_source']['script_status'] == 'context_candidate'
                            for x in result if 'save' in x['text']))

    def test_wrong_word_does_not_split_a_matched_script_line(self):
        line = script_line('What I know is Dawnclaws accused her.')
        heard = transcript('What I know is Dawnclothes accused her,')
        rows = build_cues(heard, [line], 'en')
        self.assertEqual([r['text'] for r in rows], ['What I know is Dawnclothes accused her.'])
        self.assertEqual(rows[0]['generation_source']['script_excerpt'], line['text'])
        self.assertEqual(rows[0]['generation_source']['script_status'], 'context_candidate')

    def test_short_display_gap_closes_only_with_continuous_speech(self):
        add_video(self.p, 32, self.media, 'en')
        heard = transcript('One two three. Four five six. Seven eight.')
        for i, word in enumerate(heard['segments'][0]['words']):
            shift = (i // 3) * .24
            word['start'] += shift
            word['end'] += shift
        install_cues(self.p, '32', heard, [(0, 2100), (2400, 3000)])
        first, second, third = [c['accepted'] for c in self.p.data['cues']]
        self.assertEqual(first['end_ms'], second['start_ms'])
        self.assertLess(second['end_ms'], third['start_ms'])

    def test_review_can_close_an_edited_gap_and_undo(self):
        from app.onset import apply_gaps, gap_preview
        add_video(self.p, 32, self.media, 'en')
        heard = transcript('First sentence. Second sentence.')
        for word in heard['segments'][0]['words'][2:]:
            word['start'] += .4
            word['end'] += .4
        install_cues(self.p, '32', heard)
        first, second = self.p.data['cues']
        self.p.edit([first['id'], second['id']], 'approve')
        audio = self.p.root / 'evidence' / 'audio.wav'
        audio.touch()
        self.p.data['episodes']['32']['audio'] = 'evidence/audio.wav'
        previous_end = first['accepted']['end_ms']
        with patch('app.onset.speech_regions', return_value=[(0, 1600)]):
            self.assertEqual([row['id'] for row in gap_preview(self.p, '32')], [first['id']])
            self.assertEqual(apply_gaps(self.p, '32', [first['id']]), {'applied': 1})
        self.assertEqual(first['accepted']['end_ms'], second['accepted']['start_ms'])
        self.assertEqual(first['status'], 'pending')
        self.p.undo()
        self.assertEqual(first['accepted']['end_ms'], previous_end)
        self.assertEqual(first['status'], 'approved')

    def test_missing_negation_is_not_inserted_and_difference_is_flagged(self):
        result = build_cues(transcript('We do want to leave.'), [script_line('We do not want to leave.')], 'en')
        self.assertNotIn('not', ' '.join(x['text'] for x in result))
        self.assertTrue(any(x['reasons'] for x in result))

    def test_reorder_and_repeated_dialogue_keep_audio_order(self):
        script = [script_line('You are here?'), script_line('Please go away!')]
        result = build_cues(transcript('Please go away. You are here. Please go away.'), script, 'en')
        self.assertEqual(' '.join(x['text'] for x in result), 'Please go away! You are here? Please go away!')

    def test_english_apostrophes_normalized_without_losing_end_symbols(self):
        result = build_cues(transcript("No, that's not what I..."), [script_line('No, that’s not what I—')], 'en')
        self.assertEqual(result[0]['text'], "No, that's not what I--")

    def test_chinese_preserves_symbols_and_flags_estimated_character_times(self):
        t = {'duration': 3, 'segments': [{'words': [{'word': '你回来了吗', 'start': .5, 'end': 2, 'probability': .99}]}]}
        line = script_line('你回来了吗？！'); line['language'] = 'zh'
        result = build_cues(t, [line], 'zh')
        self.assertEqual(result[0]['text'], '你回来了吗?!')
        self.assertTrue(result[0]['reasons'])

    def test_long_lines_never_gain_unspoken_words_or_exceed_two_lines(self):
        words = 'one two three four five six seven eight nine ten eleven twelve'
        result = build_cues(transcript(words), [script_line(words + '.')], 'en', {'max_line_chars_en': 15})
        self.assertTrue(all(len(x['text'].splitlines()) <= 2 for x in result))
        output = ' '.join(x['text'].replace('\n', ' ') for x in result).rstrip('.')
        self.assertEqual(output, words)

    def test_burn_in_caption_rules(self):
        heard = transcript('I... I... Go, Away! now.')
        for word in heard['segments'][0]['words'][1:]:
            word['start'] += .5
            word['end'] += .5
        rows = build_cues(heard, [], 'en')
        self.assertEqual(rows[0]['text'], 'I...')
        self.assertGreaterEqual(rows[1]['start_ms'], 800)
        words = transcript('Wait, Please! now.')
        words['segments'][0]['words'][1].update(start=.8, end=1.1)
        words['segments'][0]['words'][2].update(start=1.1, end=1.4)
        self.assertEqual([r['text'] for r in build_cues(words, [], 'en')], ['Wait,', 'please!', 'Now.'])
        quote = '“one two three four five six seven eight nine ten eleven twelve”'
        rows = build_cues(transcript(quote), [], 'en')
        self.assertGreater(len(rows), 1)
        for row in rows:
            self.assertLess(len(row['text']), 45)
            self.assertLessEqual(len(row['text'].splitlines()), 2)
            self.assertTrue(row['text'].startswith('"') and row['text'].endswith('"'))

    def test_long_caption_prefers_punctuation_then_audio_then_clause(self):
        text = 'By the time the truth mattered, she was dead.'
        rows = build_cues(transcript(text), [], 'en')
        self.assertEqual([r['text'] for r in rows], ['By the time the truth mattered,', 'she was dead.'])

        timed = transcript('This morning I waited quietly outside your house.')
        words = timed['segments'][0]['words']
        for word in words[5:]:
            word['start'] += .18
            word['end'] += .18
        rows = build_cues(timed, [], 'en')
        self.assertEqual([r['text'] for r in rows], ['This morning I waited quietly', 'outside your house.'])

        rows = build_cues(transcript('We can walk along the river but we should go home.'), [], 'en')
        self.assertEqual([r['text'] for r in rows], ['We can walk along the river', 'but we should go home.'])

    def test_review_mode_cannot_use_generation_and_pending_export_is_blocked(self):
        self.p.data['mode'] = 'review'
        with self.assertRaises(UserError):
            import_script(self.p, self.script)
        self.p.data['mode'] = 'generation'
        import_script(self.p, self.script)
        add_video(self.p, 32, self.media, 'en')
        self.assertTrue(self.p.validate_export())
        with self.assertRaises(UserError):
            export_ai(self.p, self.root)

    def test_reset_episode_archives_old_subtitles_and_reuses_media(self):
        from app.generation import episode_versions, reset_episode, restore_episode_version
        add_video(self.p, 32, self.media, 'en')
        add_video(self.p, 33, self.media, 'en')
        heard = transcript('Hello.')
        for ep in ('32', '33'):
            install_cues(self.p, ep, heard)
        old, other = self.p.data['cues']
        self.p.edit([old['id']], 'delete')
        original = self.p.root / self.p.data['episodes']['32']['original_srt']
        self.p.data['jobs']['32'] = {'status': 'done'}
        self.p.data['episodes']['32']['transcript'] = 'evidence/32/transcript.json'
        archive = Path(reset_episode(self.p, '32')['archive'])
        self.assertEqual(self.p.data['cues'], [other])
        self.assertNotIn('32', self.p.data['jobs'])
        self.assertEqual(self.p.data['episodes']['32']['transcript'], 'evidence/32/transcript.json')
        self.assertTrue(json.loads(archive.read_text(encoding='utf-8'))['cues'][0]['accepted']['deleted'])
        confirm_lines(self.p, 32, 'en', ['Hello.'])
        install_cues(self.p, '32', heard)
        replacement = self.p.data['cues'][-1]
        self.assertNotEqual(replacement['id'], old['id'])
        self.assertNotEqual(self.p.root / self.p.data['episodes']['32']['original_srt'], original)
        self.assertTrue(original.is_file())
        version = episode_versions(self.p, '32')['versions'][0]
        restore_episode_version(self.p, '32', version['id'])
        restored = self.p.by_id()[old['id']]
        self.assertTrue(restored['accepted']['deleted'])
        self.assertEqual(self.p.root / self.p.data['episodes']['32']['original_srt'], original)
        self.assertEqual(self.p.by_id()[other['id']], other)
        current = episode_versions(self.p, '32')['versions'][0]
        restore_episode_version(self.p, '32', current['id'])
        self.assertIn(replacement['id'], self.p.by_id())

    def test_shared_boundary_updates_both_drafts_and_undoes_together(self):
        from app.subtitle_layout import layout
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript('Hello. Goodbye.'))
        first, second = self.p.data['cues']
        before = [copy.deepcopy(c['accepted']) for c in (first, second)]
        point = second['accepted']['start_ms'] + 20
        layout(self.p, first['id'], 'shared-boundary',
               {'next_id': second['id'], 'before': before, 'boundary_ms': point})
        self.assertEqual(first['draft']['end_ms'], point)
        self.assertEqual(second['draft']['start_ms'], point)
        self.assertEqual([c['accepted'] for c in (first, second)], before)
        self.p.undo()
        self.assertIsNone(first['draft'])
        self.assertIsNone(second['draft'])

    def test_generation_and_human_progress_protection(self):
        import_script(self.p, self.script)
        add_video(self.p, 32, self.media, 'en')
        processor = Processor(threading.RLock())
        self.assertEqual(install_cues(self.p, '32', transcript("You're here."))['count'], 1)
        rows, warnings = parse_srt(self.p.root / 'originals/32.srt')
        self.assertEqual(rows[0]['text'], "You're here!")
        cue = self.p.data['cues'][0]
        self.p.edit([cue['id']], 'approve')
        before = copy.deepcopy(cue)
        with self.assertRaises(UserError):
            install_cues(self.p, '32', transcript("You're here."))
        with self.assertRaises(UserError):
            confirm_lines(self.p, 32, 'en', ['Something else.'])
        with self.assertRaises(UserError):
            processor.start(self.p, ['32'])
        self.assertEqual(cue, before)

    def test_generation_without_script_and_with_pasted_text(self):
        add_video(self.p, 32, self.media, 'en')
        add_video(self.p, 33, self.media, 'en')
        confirm_lines(self.p, 33, 'en', ['Are you here?'])
        heard = transcript('Are you here.')
        install_cues(self.p, '32', heard)
        install_cues(self.p, '33', heard)
        self.assertEqual(self.p.data['cues'][0]['accepted']['text'], 'Are you here.')
        self.assertNotIn('未能确定对应剧本', self.p.data['cues'][0]['note'])
        self.assertEqual(self.p.data['cues'][1]['accepted']['text'], 'Are you here?')

    def test_api_review_retries_malformed_and_keeps_valid_findings(self):
        from app.api_review import review_rows
        rows = [{'id': 'left'}, {'id': 'right'}]
        reply = ('说明文字\n```json\n'
                 '{"findings":[{"id":"left","reason":"应在逗号断开","text":null,"boundary_after":"6"},'
                 '{"id":"foreign","reason":"invalid"}]}\n```')
        with patch.object(ai_config, 'chat', side_effect=['截断的返回 {"findings":[', reply]) as chat:
            findings, reviewed, ignored = review_rows(rows)
        self.assertEqual(findings, {'left': ('应在逗号断开', '', 6)})
        self.assertEqual(reviewed, {'left'})
        self.assertEqual(ignored, 1)
        self.assertEqual(chat.call_count, 2)
        with patch.object(ai_config, 'chat', return_value=[{'type': 'text', 'text': '{"findings":[]}'}]):
            self.assertEqual(review_rows(rows), ({}, {'left', 'right'}, 0))

    def test_ai_extraction_rejects_rewritten_quotes_and_keeps_symbols(self):
        source = [{'line': 7, 'text': 'No, that’s not what I—'}]
        with patch.object(ai_config, 'chat', return_value=json.dumps({'lines': [{'line': 7, 'text': 'No, that is not what I meant.'}]})):
            with self.assertRaises(UserError):
                ai_config.extract_lines(source, 'en')
        with patch.object(ai_config, 'chat', return_value=json.dumps({'lines': [{'line': 7, 'text': source[0]['text'], 'speaker': 'EDWARD'}]})):
            self.assertEqual(ai_config.extract_lines(source, 'en')[0]['text'], source[0]['text'])

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_api_key_is_encrypted_and_not_exposed_and_new_provider_drops_old_key(self):
        cfg = self.root / 'private-config.json'
        with patch.object(ai_config, 'CONFIG', cfg):
            result = ai_config.save_config({'base_url': 'https://example.test/v1', 'model': 'test', 'api_key': 'test-secret-no-real-key'})
            self.assertTrue(result['has_key'])
            self.assertNotIn('test-secret', cfg.read_text())
            encrypted = json.loads(cfg.read_text())['encrypted_key']
            import base64
            self.assertEqual(ai_config.protect(base64.b64decode(encrypted), decrypt=True), b'test-secret-no-real-key')
            self.assertNotIn('api_key', result)
            ai_config.save_config({'base_url': 'https://another.test/v1', 'model': 'test'})
            self.assertFalse(ai_config.public_config()['has_key'])

    def test_remote_plain_http_is_rejected_and_local_endpoint_supported(self):
        with self.assertRaises(UserError):
            ai_config.endpoint('http://example.test/v1')
        self.assertEqual(ai_config.endpoint('http://localhost:1234/v1'), 'http://localhost:1234/v1/chat/completions')

    def test_split_merge_are_pending_undoable_and_leave_other_timing_untouched(self):
        from app.subtitle_layout import layout
        import_script(self.p, self.script)
        add_video(self.p, 32, self.media, 'en')
        install_cues(self.p, '32', transcript("You're here."))
        c = self.p.data['cues'][0]
        original = copy.deepcopy(c)
        start, end = c['accepted']['start_ms'], c['accepted']['end_ms']
        layout(self.p, c['id'], 'split', {'boundary_ms': (start + end) // 2, 'left': "You're", 'right': 'here!'})
        self.assertEqual(len(self.p.data['cues']), 2)
        self.assertTrue(all(x['status'] == 'pending' for x in self.p.data['cues']))
        layout(self.p, c['id'], 'merge', {'text': "You're here!"})
        self.assertEqual(c['accepted']['start_ms'], start)
        self.assertEqual(c['accepted']['end_ms'], end)
        self.p.undo()
        self.assertFalse(self.p.data['cues'][1]['accepted']['deleted'])
        self.p.undo()
        self.assertEqual(self.p.data['cues'], [original])

    def test_queue_continues_after_one_episode_fails_using_saved_transcript(self):
        import wave
        from app.core import fingerprint
        import_script(self.p, self.script)
        for ep, words in [('32', "You're here."), ('33', 'Goodbye.')]:
            confirm_lines(self.p, ep, 'en', [words.replace('.', '!') if ep == '32' else words])
            add_video(self.p, ep, self.media, 'en')
            evidence = self.p.root / 'evidence' / ep; evidence.mkdir()
            with wave.open(str(evidence / 'audio.wav'), 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * 32000)
            (evidence / 'transcript.json').write_text(json.dumps(transcript(words)), encoding='utf-8')
        self.p.data['episodes']['32']['media_fingerprint']['hash'] = 'changed-media'
        processor = Processor(threading.RLock())
        with (patch.object(processor, 'automatic_backend', side_effect=AssertionError('saved transcript must be reused')),
              patch('app.onset.speech_regions', return_value=[])):
            processor.start(self.p, ['32', '33'], 'no-model-needed')
            processor.thread.join(timeout=5)
        self.assertFalse(processor.active())
        self.assertEqual(self.p.data['jobs']['32']['status'], 'failed')
        self.assertEqual(self.p.data['jobs']['33']['status'], 'done')
        self.assertEqual({c['episode'] for c in self.p.data['cues']}, {'33'})

    def test_adapter_uses_configured_model_and_returns_text(self):
        import io
        cfg = self.root / 'private-config.json'
        with patch.object(ai_config, 'CONFIG', cfg):
            ai_config.save_config({'base_url': 'http://localhost:1234/v1', 'model': 'local-test'})
            response = io.BytesIO(json.dumps({'choices': [{'message': {'content': 'OK'}}]}).encode())
            with patch.object(ai_config.urllib.request, 'urlopen', return_value=response) as transport:
                self.assertEqual(ai_config.test_connection(), {'connected': True})
                req = transport.call_args.args[0]
                self.assertEqual(req.full_url, 'http://localhost:1234/v1/chat/completions')
                payload = json.loads(req.data)
                self.assertEqual(payload['model'], 'local-test')
                self.assertEqual(payload['messages'], [{'role': 'user', 'content': 'Reply OK.'}])
