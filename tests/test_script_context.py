import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.core import Project
from app.exchange import export_ai
from app.script_context import annotate_entries, script_match


class ScriptContextTests(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get('REVIEW_TEST_ROOT', tempfile.gettempdir())).resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.tmp.name)
        self.base = base
        self.script = self.root / 'script.txt'
        self.script.write_text('EPISODE 31\nNot this episode.\nEPISODE 32\nEDWARD\nNo, that’s not what I—\nROSE\nNo. You were trying to keep me in the dark.\nNo touch. No scent. No Sebastian. Mine.\n', encoding='utf-8')
        self.refs = [{'kind': 'script', 'text_file': 'script.txt', 'name': '剧本.txt'}]

    def tearDown(self):
        assert self.root.resolve().is_relative_to(self.base)
        self.tmp.cleanup()

    def test_split_cue_keeps_full_original_line_and_punctuation(self):
        result = script_match(self.root, self.refs, '32', "that's not what I...")
        self.assertEqual(result['script_status'], 'matched')
        self.assertEqual(result['script_text'], 'No, that’s not what I—')
        self.assertEqual(result['script_excerpt'], 'that’s not what I—')
        self.assertEqual(result['script_source']['line'], 5)

    def test_short_word_uses_punctuation_to_disambiguate(self):
        result = script_match(self.root, self.refs, '32', 'No.')
        self.assertEqual(result['script_text'], 'No. You were trying to keep me in the dark.')
        self.assertEqual(result['script_excerpt'], 'No.')

    def test_wrong_episode_and_missing_script_are_not_no_dialogue(self):
        self.assertEqual(script_match(self.root, self.refs, '32', 'Not this episode.')['script_status'], 'not_located')
        self.assertEqual(script_match(self.root, [], '32', 'No.')['script_status'], 'not_provided')

    def test_ambiguous_match_is_not_presented_as_certain(self):
        self.script.write_text('EPISODE 32\nHello.\nHello.\n', encoding='utf-8')
        self.assertEqual(script_match(self.root, self.refs, '32', 'Hello.')['script_status'], 'not_located')

    def test_neighboring_cues_disambiguate_repeated_words(self):
        self.script.write_text('EPISODE 32\nRose trains alone. No Sebastian.\nNo touch. No scent. No Sebastian. Mine.\n', encoding='utf-8')
        self.assertEqual(script_match(self.root, self.refs, '32', 'No Sebastian.')['script_status'], 'not_located')
        result = script_match(self.root, self.refs, '32', 'No Sebastian.', ['No scent.', 'Mine.'])
        self.assertEqual(result['script_text'], 'No touch. No scent. No Sebastian. Mine.')

    def test_chinese_episode_scope_and_quote(self):
        self.script.write_text('第三十一集\n你回来了吗？！\n第三十二集\n你回来了吗？！\n', encoding='utf-8')
        result = script_match(self.root, self.refs, '32', '你回来了吗')
        self.assertEqual(result['script_text'], '你回来了吗？！')
        self.assertEqual(result['script_source']['line'], 4)

    def test_context_candidate_requires_both_anchors_and_shared_word(self):
        self.script.write_text("EPISODE 32\nWhy?\nGauntlet business.\nI'll be back tomorrow.\n", encoding='utf-8')
        lines = [{'episode': '32', 'language': 'en', 'text': 'Gauntlet business.',
                  'source': {'path': 'script.txt', 'line': 3, 'text': 'Gauntlet business.'}}]
        entries = [{'stable_id': str(i), 'episode': '32', 'original': text,
                    'start_ms': i * 1000, 'end_ms': i * 1000 + 800}
                   for i, text in enumerate(('Why?', 'Call it business.', "I'll be back tomorrow."))]
        episodes = {'32': {'generation_language': 'en', 'transcript': 'asr.json'}}
        for word, status in [(' business.', 'context_candidate'), (' anything.', 'not_located')]:
            (self.root / 'asr.json').write_text(json.dumps({'segments': [{'words': [
                {'word': word, 'start': 1.1, 'end': 1.5}]}]}), encoding='utf-8')
            entries[2]['deleted'] = False
            result = annotate_entries(self.root, self.refs, episodes, entries, lines)[1]
            self.assertEqual(result['script_status'], status)
            if status == 'context_candidate':
                self.assertEqual(result['script_excerpt'], 'Gauntlet business.')
                self.assertEqual(result['script_source']['line'], 3)
                entries[2]['deleted'] = True
                self.assertEqual(annotate_entries(self.root, self.refs, episodes, entries, lines)[1]['script_status'],
                                 'not_located')

    def test_export_and_snapshot_use_original_baseline_without_changing_edits(self):
        folder = self.root / 'project'
        folder.mkdir()
        srt = self.root / '32.srt'
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nNo, that's not what I...\n", encoding='utf-8')
        p = Project.create(folder, 'source test')
        try:
            p.import_pairs([{'episode':32, 'srt':str(srt)}])
            p.add_reference(str(self.script), 'script')
            c = p.data['cues'][0]
            p.edit([c['id']], 'draft', {'text':'An intentional human edit.'})
            p.edit([c['id']], 'approve')
            original = copy.deepcopy(c)
            snapshot = p.snapshot()
            self.assertEqual(snapshot['cues'][0]['script_context']['script_text'], 'No, that’s not what I—')
            result = export_ai(p, self.root)
            output = Path(result['directory'])
            index = json.loads((output / '项目与字幕索引.json').read_text(encoding='utf-8'))
            entry = index['entries'][0]
            self.assertEqual(entry['original'], 'An intentional human edit.')
            self.assertEqual(entry['script_text'], 'No, that’s not what I—')
            self.assertEqual(entry['human_status'], 'approved')
            self.assertTrue((output / '逐条台词对照.md').is_file())
            self.assertEqual(c, original)
        finally:
            p.close()
