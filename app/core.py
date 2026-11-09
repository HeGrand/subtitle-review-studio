from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 1
MEDIA_EXT = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.wav', '.mp3', '.m4a', '.flac', '.aac'}
MARKERS = re.compile(r'【(?:人工审核|人工复核)[^】]*】|\[(?:REVIEW|ISSUE)[^\]]*\]', re.I)


class UserError(Exception):
    pass


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fingerprint(path, full=False):
    p = Path(path)
    st = p.stat()
    h = hashlib.sha256()
    with p.open('rb') as f:
        if full or st.st_size < 4 * 1024 * 1024:
            while b := f.read(1024 * 1024):
                h.update(b)
            mode = 'sha256'
        else:
            # Large media: size + three sampled regions, explicitly labelled as sampled.
            for offset in (0, st.st_size // 2, max(0, st.st_size - 1024 * 1024)):
                f.seek(offset)
                h.update(f.read(1024 * 1024))
            mode = 'sampled-sha256'
    return {'size': st.st_size, 'hash': h.hexdigest(), 'mode': mode}


def same_file(path, fp):
    return fingerprint(path, fp['mode'] == 'sha256') == fp


def inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def external_directory(path):
    p = Path(path).expanduser().resolve()
    if inside(p, APP_ROOT):
        raise UserError('项目和导出必须保存在程序目录以外，请另选文件夹。')
    if not p.is_dir():
        raise UserError('文件夹不存在。')
    return p


def stamp(ms):
    ms = round(ms)
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'


def parse_time(value):
    m = re.fullmatch(r'(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})', value.strip())
    if not m or int(m[2]) > 59 or int(m[3]) > 59:
        raise UserError(f'非法时间码：{value}')
    h, mi, s, ms = map(int, m.groups())
    return ((h * 60 + mi) * 60 + s) * 1000 + ms


def parse_srt(path):
    try:
        raw = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeError:
        raise UserError('字幕须为 UTF-8 编码，请转换后导入。')
    if not raw.strip():
        raise UserError('字幕文件为空。')
    rows, warnings = [], []
    seen = set()
    for i, block in enumerate(re.split(r'\n\s*\n', raw.strip()), 1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit() or ' --> ' not in lines[1]:
            raise UserError(f'第 {i} 个字幕块无法解析，请先修复格式。')
        number = int(lines[0].strip())
        start, end = lines[1].split(' --> ', 1)
        start, end = parse_time(start), parse_time(end)
        if number in seen:
            warnings.append(f'重复序号 {number}；使用独立稳定ID，不以序号定位修改。')
        seen.add(number)
        if number != i:
            warnings.append(f'第{i}块原序号为{number}，导出时重新编号。')
        text = '\n'.join(lines[2:]).strip()
        if MARKERS.search(text):
            raise UserError(f'第{number}条含旧审核标记，请选择干净SRT基线。')
        rows.append({'source_number': number, 'text': text, 'start_ms': start, 'end_ms': end, 'deleted': False})
    return rows, warnings


def episode_key(name):
    stem = Path(name).stem
    m = re.search(r'(?:episode|ep|第)\s*0*(\d+)', stem, re.I)
    if not m:
        numbers = re.findall(r'\d+', stem)
        if len(numbers) != 1:
            return None
        number = int(numbers[0])
    else:
        number = int(m[1])
    return f'{number:02}'


def scan_pairs(media_dir='', srt_dir=''):
    groups, unassigned = {}, []
    for folder, kind in ((media_dir, 'media'), (srt_dir, 'srt')):
        if not folder:
            continue
        root = Path(folder)
        if not root.is_dir():
            raise UserError(f'素材文件夹不可访问：{folder}')
        for p in sorted(root.rglob('*')):
            if not p.is_file() or (p.suffix.lower() not in MEDIA_EXT if kind == 'media' else p.suffix.lower() != '.srt'):
                continue
            key = episode_key(p.name)
            if not key:
                unassigned.append({'path': str(p), 'kind': kind, 'reason': '集数不明确，请逐集指定'})
                continue
            g = groups.setdefault(key, {'episode': key, 'media': [], 'srt': [], 'warnings': []})
            g[kind].append(str(p))
            if not p.stat().st_size:
                g['warnings'].append('空文件：' + p.name)
    for g in groups.values():
        for kind, label in [('media', '音视频'), ('srt', '字幕')]:
            if len(g[kind]) != 1:
                g['warnings'].append(('缺少' if not g[kind] else '多个版本，请手动选择') + label)
    return {'pairs': sorted(groups.values(), key=lambda x: int(x['episode'])), 'unassigned': unassigned}


def validate_row(row, duration=None):
    errors = []
    if type(row.get('deleted')) is not bool:
        return ['删除标记必须为布尔值']
    if row.get('deleted'):
        return errors
    text = row.get('text')
    if not isinstance(text, str) or not text.strip() or re.search(r'\n\s*\n', text):
        errors.append('空字幕或字幕内含空白分段')
    if isinstance(text, str) and (MARKERS.search(text) or '人工审核' in text or '人工复核' in text):
        errors.append('字幕正文不能含审核标记')
    for k in ('start_ms', 'end_ms'):
        if type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) or row[k] != round(row[k]):
            errors.append('时间必须为整数毫秒')
            return errors
    if row['start_ms'] < 0 or row['end_ms'] <= row['start_ms']:
        errors.append('时间边界非法')
    if duration and row['end_ms'] > duration + 20:
        errors.append('超出片长')
    return errors


def accepted(cue):
    return cue['accepted']


def proposed(cue):
    return cue.get('draft') or cue['accepted']


class Project:
    def __init__(self, root, data, lock):
        self.root, self.data, self.lock = Path(root), data, lock

    @classmethod
    def acquire(cls, root):
        # OS byte lock survives stale files and is released by Windows after a crash.
        p = Path(root) / '.subtitle-review.lock'
        f = p.open('a+b')
        if p.stat().st_size == 0:
            f.write(b'0')
            f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            f.close()
            raise UserError('项目已被另一个程序打开，请先在原窗口保存并关闭。')
        return f

    @classmethod
    def create(cls, folder, name):
        root = external_directory(folder)
        if any(root.iterdir()):
            raise UserError('为保护已有文件，请选择空文件夹作为新项目目录。')
        lock = cls.acquire(root)
        p = cls(root, {'schema': SCHEMA, 'project_id': uuid.uuid4().hex, 'name': name.strip() or root.name,
                       'version': 1, 'revision': 0, 'created_at': now(), 'saved_at': now(),
                       'episodes': {}, 'cues': [], 'references': [], 'history': [], 'imports': [],
                       'opinion_conflicts': [], 'issues': [], 'jobs': {}, 'ai_rounds': [], 'exports': [],
                       'legacy': None, 'finished': False}, lock)
        for d in ('originals', 'evidence', 'cache/tmp', 'cache/waveforms', 'cache/models', 'history'):
            (root / d).mkdir(parents=True, exist_ok=True)
        p.save()
        return p

    @classmethod
    def open(cls, folder):
        root = external_directory(folder)
        if not (root / 'project.json').is_file():
            raise UserError('所选文件夹没有 project.json 项目文件。')
        lock = cls.acquire(root)
        try:
            data = read_json(root / 'project.json')
            if data.get('schema') != SCHEMA:
                raise UserError('项目格式版本不受支持，请保留原件并使用对应版本程序。')
            p = cls(root, data, lock)
            from .review import migrate, restore_post_approval_evidence
            if data.get('review_migration_version') != 1:
                (root / 'history').mkdir(exist_ok=True)
                backup_path = root / 'history' / 'before-review-classification-v2.json'
                if not backup_path.exists():
                    shutil.copy2(root / 'project.json',backup_path)
            migrate(data)
            restore_post_approval_evidence(data)
            for job in data['jobs'].values():
                if job.get('status') in ('running', 'preparing'):
                    job.update(status='paused', message='上次程序中断，点击继续可复用已完成分段。')
            p.save()
            return p
        except Exception:
            lock.close()
            raise

    def close(self):
        self.save()
        self.lock.close()

    def save(self):
        self.data['saved_at'] = now()
        dest = self.root / 'project.json'
        if dest.exists():
            # Previous complete state is retained independently from the atomic replacement.
            shutil.copy2(dest, self.root / 'history' / 'previous.json')
        atomic_json(dest, self.data)

    def commit(self, label, before=None, extra=None):
        self.data['revision'] += 1
        self.data['finished'] = False
        self.data['history'].append({'id': uuid.uuid4().hex, 'at': now(), 'label': label,
                                     'before': before or [], 'extra': extra or {}, 'undone': False})
        self.save()

    def by_id(self):
        return {c['id']: c for c in self.data['cues']}

    def source_fingerprint(self):
        return digest({k: {'srt': v.get('srt_fingerprint'), 'media': v.get('media_fingerprint')}
                       for k, v in self.data['episodes'].items()})

    def snapshot(self):
        d = copy.deepcopy({k: v for k, v in self.data.items() if k not in ('history', 'ai_rounds')})
        d['ai_rounds'] = copy.deepcopy([{k: v for k, v in r.items() if k != 'index'} for r in self.data['ai_rounds']])
        d['root'] = str(self.root)
        from .job_errors import explain_error
        for job in d['jobs'].values():
            if job.get('status') == 'failed' and not job.get('error'):
                job['error'] = explain_error(job.get('message', '未知错误'))
        d['fingerprint'] = self.source_fingerprint()
        d['history_count'] = len(self.data['history'])
        if d.get('mode') == 'generation':
            d['versioned_episodes'] = sorted({h['extra']['episode'] for h in self.data['history']
                if h.get('extra', {}).get('episode') and h['extra'].get('archive')})
        for c in d['cues']:
            c['current'] = proposed(c)
        from .review import category, LABELS, human_reasons, issue_pending, issue_in_scope, task_in_scope, valid_local_alignment
        from .terminology import context
        review_data,terms = context(self)
        from .script_context import annotate_entries, tokens
        source_entries = annotate_entries(self.root, d['references'], d['episodes'], [
            {'stable_id': c['id'], 'episode': c['episode'], 'original': c['accepted']['text'],
             'raw_original': c['original'], 'generation_source': c.get('generation_source'), 'script_locator': c.get('script_locator'),
             'start_ms': c['accepted']['start_ms'], 'end_ms': c['accepted']['end_ms'], 'deleted': c['accepted']['deleted']}
            for c in d['cues']], d.get('generation', {}).get('lines', []) if d.get('mode') == 'generation' else None)
        source_by_id = {e['stable_id']: e for e in source_entries}
        d['term_groups']=terms['groups']
        d['review_scope']=self.data.get('review_scope','existing_subtitles')
        for task in d.get('review_tasks',[]):task['in_scope']=task_in_scope(self.data,task)
        counts = {k: 0 for k in LABELS}
        for c in d['cues']:
            source = source_by_id[c['id']]
            c['script_context'] = {k: source[k] for k in ('script_status', 'script_text', 'script_excerpt', 'script_source', 'asr_text')}
            if coverage := c.get('script_coverage'):
                c['script_context'].update(script_status='partial', script_coverage=coverage, script_text=coverage['text'])
            if prefix := review_data['_deleted_prefixes'].get(c['id']):
                c['script_context'].update(script_status='context_candidate', script_excerpt=prefix['text'])
                c['deleted_prefix'] = prefix
            if d.get('mode') == 'generation' and source['script_status'] == 'matched':
                asr = [word for word, _, _ in tokens(source['asr_text'])]
                if asr and asr == [word for word, _, _ in tokens(source['script_excerpt'])]:
                    outdated = '实说内容未能确定对应剧本：保留识别文字，请听改'
                    c['note'] = '；'.join(note for note in c.get('note', '').split('；') if note != outdated)
            c['category'] = category(c, review_data)
            c['category_label'] = LABELS[c['category']]
            if c['category'] == 'text_clear' and valid_local_alignment(c):
                c['category_label'] = '本地文稿对齐通过'
            c['human_reasons']=human_reasons(c,review_data)
            counts[c['category']] += 1
        runs = d.get('api_review_runs', {})
        if runs:
            eligible = {c['id'] for c in d['cues'] if c['status'] != 'approved' and not c.get('draft')
                        and not c['accepted']['deleted']}
            for run in runs.values():
                if 'unreviewed_ids' in run:
                    run['unreviewed'] = sum(cid in eligible for cid in run['unreviewed_ids'])
        d['review_counts'] = counts
        counts['standalone_candidates'] = len({i.get('triage',{}).get('group_id') or i['id'] for i in self.data['issues']
                                               if not i.get('cue_id') and issue_in_scope(self.data,i) and issue_pending(i)})
        counts['outside_scope'] = sum(not i.get('cue_id') for i in self.data['issues'])
        counts['extra_tasks'] = sum(not t.get('resolved') and task_in_scope(self.data,t) for t in self.data.get('review_tasks', []))
        return d

    def import_pairs(self, pairs):
        results = []
        for pair in pairs:
            key = str(pair['episode'])
            if not re.fullmatch(r'\d{1,6}', key):
                results.append({'episode': key, 'error': '集数必须为数字'})
                continue
            key = f'{int(key):02}'
            if key in self.data['episodes']:
                results.append({'episode': key, 'error': '此集已存在；请用重新定位或建立新项目处理改版'})
                continue
            try:
                srt = Path(pair['srt'])
                rows, warnings = parse_srt(srt)
                media = Path(pair['media']) if pair.get('media') else None
                if media and (not media.is_file() or media.stat().st_size == 0):
                    raise UserError('音视频不存在或为空')
                fp = fingerprint(srt, True)
                mfp = fingerprint(media) if media else None
                target = self.root / 'originals' / f'{key}.srt'
                shutil.copy2(srt, target)
                ep = {'episode': key, 'source_srt': str(srt.resolve()), 'original_srt': str(target.relative_to(self.root)),
                      'media': str(media.resolve()) if media else '', 'srt_fingerprint': fp, 'media_fingerprint': mfp,
                      'video_version': pair.get('video_version', media.name if media else '未关联'), 'duration_ms': None,
                      'warnings': warnings, 'local_status': '未处理', 'audio': None, 'transcript': None}
                self.data['episodes'][key] = ep
                for index, row in enumerate(rows):
                    cid = uuid.uuid5(uuid.UUID(self.data['project_id']), f'{key}/{fp["hash"]}/{index}').hex
                    self.data['cues'].append({'id': cid, 'episode': key, 'source_number': row['source_number'],
                                              'original': copy.deepcopy(row), 'accepted': copy.deepcopy(row),
                                              'draft': None, 'status': 'pending', 'note': '', 'opinions': [], 'confirmed_at': None})
                results.append({'episode': key, 'count': len(rows), 'warnings': warnings})
            except (OSError, UserError, ValueError) as exc:
                results.append({'episode': key, 'error': str(exc)})
        if any('count' in x for x in results):
            self.data['version'] += 1
            self.commit('导入字幕与素材配对', extra={'results': results})
        return results

    def manage_episode(self, episode, action, reason=''):
        """Reversible exclusion affects this project only; never unlink source files."""
        archived = self.data.setdefault('excluded_episodes', {})
        if action == 'restore':
            if episode not in archived or episode in self.data['episodes']:
                raise UserError('此集无法恢复，可能已重新导入同集素材。')
            record = archived.pop(episode)
            self.data['episodes'][episode] = record['episode']
            if record['job'] is not None:
                self.data['jobs'][episode] = record['job']
            for name, rows in record['rows'].items():
                self.data.setdefault(name, []).extend(rows)
            self.data['version'] += 1
        else:
            if episode not in self.data['episodes']:
                raise UserError('此集不在当前项目。')
            if action not in ('skip', 'exclude') or not reason.strip():
                raise UserError('请选择处理方式并填写原因。')
            if action == 'skip':
                job = self.data['jobs'].setdefault(episode, {})
                if job.get('status') not in ('failed', 'paused'):
                    raise UserError('只有失败或暂停的初查可以跳过。')
                job.update(status='skipped', skip_reason=reason.strip(), message='已跳过初查；保留字幕，缺失证据如实交给AI')
                self.data['episodes'][episode]['local_status'] = '用户跳过本地初查，未视为初查成功：' + reason.strip()
            else:
                record = {'episode': self.data['episodes'].pop(episode), 'job': self.data['jobs'].pop(episode, None),
                          'rows': {}, 'reason': reason.strip(), 'at': now()}
                for name in ('cues', 'issues', 'review_tasks', 'opinion_conflicts', 'review_limits'):
                    rows = self.data.get(name, [])
                    def belongs(row):
                        return str(row.get('episode', row.get('opinion', {}).get('episode', ''))) == episode
                    record['rows'][name] = [row for row in rows if belongs(row)]
                    self.data[name] = [row for row in rows if not belongs(row)]
                archived[episode] = record
                self.data['version'] += 1
        self.commit({'skip': '跳过本集初查', 'exclude': '从本轮移出集数', 'restore': '恢复移出集数'}[action],
                    extra={'episode': episode, 'reason': reason})
        return {'episode': episode, 'action': action}

    def relink(self, episode, path, kind='media'):
        ep = self.data['episodes'][episode]
        p = Path(path)
        expected = ep.get('media_fingerprint' if kind == 'media' else 'srt_fingerprint')
        if expected and not same_file(p, expected):
            raise UserError('文件指纹不同，属于素材改版。请新建独立项目版本；现有审核结果未改动。')
        if kind == 'media':
            ep['media'] = str(p.resolve())
            ep['media_fingerprint'] = expected or fingerprint(p)
        else:
            ep['source_srt'] = str(p.resolve())
        self.commit('重新定位素材', extra={'episode': episode, 'kind': kind})

    def add_reference(self, path, kind):
        if kind not in ('script', 'names', 'terms'):
            raise UserError('未知资料类别')
        p = Path(path)
        if p.suffix.lower() not in ('.txt', '.md', '.pdf', '.docx'):
            raise UserError('资料支持 DOCX、UTF-8 TXT、Markdown、可提取文字的PDF。')
        if p.suffix.lower() == '.docx':
            import zipfile
            from xml.etree import ElementTree as ET
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            try:
                with zipfile.ZipFile(p) as archive:
                    document = ET.fromstring(archive.read('word/document.xml'))
                    paragraphs = []
                    for paragraph in document.iter(ns + 'p'):
                        parts = []
                        for node in paragraph.iter():
                            if node.tag == ns + 't':
                                parts.append(node.text or '')
                            elif node.tag == ns + 'tab':
                                parts.append('\t')
                            elif node.tag in (ns + 'br', ns + 'cr'):
                                parts.append('\n')
                        paragraphs.append(''.join(parts))
                    text = '\n'.join(paragraphs)
            except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
                raise UserError('DOCX无法读取，请检查是否损坏、加密，或另存为标准DOCX。') from exc
            if not text.strip():
                raise UserError('DOCX没有可提取正文，可能只有扫描图片，请提供可编辑文字。')
            warning = '已读取正文和表格文字；图片中的文字不作OCR，批注和修订删除文字不作为剧本正文。'
        elif p.suffix.lower() == '.pdf':
            try:
                from pypdf import PdfReader
            except ImportError:
                raise UserError('PDF读取组件尚未准备，请双击“准备环境.cmd”。')
            reader = PdfReader(str(p))
            pages = [(page.extract_text() or '') for page in reader.pages]
            if not ''.join(pages).strip():
                raise UserError('该PDF没有可读取文字，可能为扫描件，请提供文字版。')
            text = '\n\n'.join(f'--- 第{i + 1}页 ---\n{v}' for i, v in enumerate(pages))
            warning = f'共{len(pages)}页；{sum(not x.strip() for x in pages)}页未提取到文字，请核对。'
        else:
            text = p.read_text(encoding='utf-8-sig')
            warning = ''
        if not text.strip():
            raise UserError('资料为空。')
        rid = uuid.uuid4().hex
        dest = self.root / 'originals' / 'references' / (rid + p.suffix.lower())
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(p, dest)
        extracted = dest.with_suffix('.extracted.txt')
        extracted.write_text(text, encoding='utf-8')
        self.data['references'].append({'id': rid, 'kind': kind, 'name': p.name, 'file': str(dest.relative_to(self.root)),
                                         'text_file': str(extracted.relative_to(self.root)), 'fingerprint': fingerprint(dest, True),
                                         'warning': warning})
        self.commit('添加审查资料')

    def edit(self, ids, action, values=None):
        values = values or {}
        tasks_before = copy.deepcopy(self.data.get('review_tasks', []))
        index = self.by_id()
        if not ids or any(cid not in index for cid in ids):
            raise UserError('没有有效选择，或条目ID不存在。')
        before, updated = [], []
        issues_before = None
        shifted = []
        for cid in dict.fromkeys(ids):
            cue = copy.deepcopy(index[cid])
            row = copy.deepcopy(proposed(cue))
            if action == 'draft':
                row.update({k: values[k] for k in ('text', 'start_ms', 'end_ms', 'deleted') if k in values})
                cue.update(draft=row, status='pending')
                cue.pop('draft_time_error', None)
                cue.pop('draft_time_inputs', None)
            elif action == 'editor':
                row['text'] = str(values.get('text', row['text']))
                cue['note'] = str(values.get('note', cue['note']))
                raw = {'start': str(values['start_input']), 'end': str(values['end_input'])}
                try:
                    row['start_ms'], row['end_ms'] = parse_time(raw['start']), parse_time(raw['end'])
                    cue.pop('draft_time_error', None)
                    cue.pop('draft_time_inputs', None)
                except UserError as exc:
                    cue['draft_time_error'] = str(exc)
                    cue['draft_time_inputs'] = raw
                if row != cue['accepted'] or cue.get('draft_time_error'):
                    cue.update(draft=row, status='pending')
                elif cue.get('draft'):
                    cue['draft'] = None
            elif action == 'approve':
                if cue.get('draft_time_error'):
                    raise UserError('草稿时间尚未修复：' + cue['draft_time_error'])
                if 'onset_target' in values:
                    target = values['onset_target']
                    suggestion = next((item for item in reversed(cue.get('time_adjustments', []))
                                       if item.get('field') == 'start_ms' and item.get('applied') is False
                                       and item.get('from') == row['start_ms'] and item.get('to') == target), None)
                    if len(ids) != 1 or type(target) is not int or not suggestion or not row['start_ms'] < target < row['end_ms']:
                        raise UserError('入点建议已变化，请重新试听。')
                    row['start_ms'] = target
                    suggestion['applied'] = True
                errs = validate_row(row, self.data['episodes'][cue['episode']]['duration_ms'])
                if errs:
                    raise UserError('；'.join(errs))
                if ' '.join(row['text'].split()) != ' '.join(cue['accepted']['text'].split()):
                    cue.pop('script_coverage', None)
                cue.update(accepted=row, draft=None, status='approved', confirmed_at=now())
                cue.pop('manual_hold',None)
                cue.pop('draft_time_inputs', None)
            elif action == 'pending':
                cue['status'] = 'pending'
                cue['manual_hold'] = True
            elif action == 'delete':
                # Explicit human deletion is an accepted operation; drafts remain separate until this action.
                deleted = copy.deepcopy(cue['accepted'])
                deleted['deleted'] = True
                cue.update(accepted=deleted, draft=None, status='approved', confirmed_at=now())
                cue.pop('manual_hold',None)
                cue.pop('draft_time_error', None)
                cue.pop('draft_time_inputs', None)
            elif action == 'restore':
                cue.update(draft=copy.deepcopy(cue['original']), status='pending')
                cue.pop('draft_time_error', None)
                cue.pop('draft_time_inputs', None)
            elif action == 'note':
                cue['note'] = str(values.get('note', ''))
            else:
                raise UserError('不支持的操作')
            before.append(copy.deepcopy(index[cid]))
            updated.append(cue)
        if action == 'approve':
            from .review import overlap_ids
            replacement = {cue['id']: cue for cue in updated}
            approved_ids = set(replacement)
            for episode in {cue['episode'] for cue in updated}:
                ordered = sorted((cue for cue in self.data['cues'] if cue['episode'] == episode
                                  and not accepted(replacement.get(cue['id'], cue))['deleted']),
                                 key=lambda cue: (accepted(cue)['start_ms'], cue['id']))
                for original, following in zip(ordered, ordered[1:]):
                    if original['id'] not in approved_ids:
                        continue
                    end = accepted(replacement[original['id']])['end_ms']
                    next_id = following['id']
                    neighbor = replacement.get(next_id, following)
                    base, draft = accepted(neighbor), neighbor.get('draft')
                    if end <= base['start_ms'] and (not draft or draft['deleted'] or end <= draft['start_ms']):
                        continue
                    if neighbor.get('draft_time_error'):
                        raise UserError('下一条的时间草稿无效，请先修正它的入点或出点。')
                    new_start = end
                    if draft and not draft['deleted'] and end > base['start_ms']:
                        new_start = max(end, draft['start_ms'])
                    if (end > base['start_ms'] and new_start >= base['end_ms']
                            or draft and not draft['deleted'] and end >= draft['end_ms']):
                        raise UserError('本条出点会覆盖下一条的整段时间，请缩短本条或调整下一条。')
                    if next_id not in replacement:
                        neighbor = copy.deepcopy(neighbor)
                        before.append(copy.deepcopy(following))
                        updated.append(neighbor)
                        replacement[next_id] = neighbor
                    if end > base['start_ms']:
                        neighbor['accepted']['start_ms'] = new_start
                    if draft and not draft['deleted'] and end > draft['start_ms']:
                        neighbor['draft']['start_ms'] = end
                    if next_id not in approved_ids:
                        neighbor.update(status='pending', manual_hold=True, confirmed_at=None)
                    shifted.append({'episode': episode, 'number': neighbor.get('source_number'),
                                    'start_ms': proposed(neighbor)['start_ms']})
            overlaps = overlap_ids((replacement.get(cue['id'], cue) for cue in self.data['cues']), accepted)
            if overlaps & approved_ids:
                raise UserError('本条与前一条重叠，请调整本条入点或前一条出点。')
        for cue in updated:
            index[cue['id']].clear()
            index[cue['id']].update(cue)
        if action in ('approve', 'delete'):
            linked = {cue.get('added_for_issue') for cue in updated
                      if cue['id'] in ids and cue.get('added_for_issue')}
            issues = [issue for issue in self.data['issues'] if issue['id'] in linked]
            if issues:
                issues_before = copy.deepcopy(self.data['issues'])
                for issue in issues:
                    issue.update(resolved=action == 'approve', resolution='已补字幕并确认' if action == 'approve' else '')
        if action == 'approve' and self.data.get('mode') == 'generation':
            from .script_context import deleted_prefixes
            prefixes = deleted_prefixes(self)
            for cue in updated:
                if cue['id'] in ids and cue['id'] in prefixes:
                    index[cue['id']]['prefix_reviewed'] = prefixes[cue['id']]['fingerprint']
        from .review import reconcile_human_tasks
        reconcile_human_tasks(self.data)
        extra = {'review_tasks_before': tasks_before} if tasks_before != self.data.get('review_tasks', []) else {}
        if issues_before is not None:
            extra['issues_before'] = issues_before
        self.commit(action, before, extra)
        if action == 'approve':
            return {'shifted': shifted}

    def add_cue(self, episode, row, issue_id=None):
        if episode not in self.data['episodes']:
            raise UserError('请先选择已导入的集数。')
        row = {k: row[k] for k in ('text', 'start_ms', 'end_ms')}
        row['deleted'] = False
        errs = validate_row(row, self.data['episodes'][episode]['duration_ms'])
        if errs:
            raise UserError('；'.join(errs))
        cid = uuid.uuid4().hex
        base = dict(row, deleted=True)
        cue = {'id': cid, 'episode': episode, 'source_number': None, 'original': copy.deepcopy(base),
               'accepted': base, 'draft': row, 'status': 'pending', 'note': '', 'opinions': [], 'confirmed_at': None}
        if issue_id:
            if not any(i['id'] == issue_id and i['episode'] == episode and not i.get('cue_id') for i in self.data['issues']):
                raise UserError('找不到对应的未匹配片段。')
            cue['added_for_issue'] = issue_id
        self.data['cues'].append(cue)
        self.commit('补字幕（待确认）', extra={'added': [cid]})
        return cid

    def undo(self):
        for h in reversed(self.data['history']):
            if h['label'] in ('清空本集字幕并保留旧版本', '恢复本集字幕版本'):
                raise UserError('不能跨越整集重跑撤销，旧版本已保存在项目的 history 文件夹。')
            if h['label'] in ('从本轮移出集数', '恢复移出集数'):
                raise UserError('集数范围已调整；请使用素材页的恢复入口。此前人工记录仍保留，不能跨越范围调整撤销。')
            if not h['undone'] and (h['before'] or h.get('extra', {}).get('added')
                                    or any(key in h.get('extra', {}) for key in
                                           ('issues_before', 'review_tasks_before', 'api_review_run_before'))):
                index = self.by_id()
                for old in h['before']:
                    index[old['id']].clear()
                    index[old['id']].update(copy.deepcopy(old))
                added = h.get('extra', {}).get('added', [])
                self.data['cues'] = [c for c in self.data['cues'] if c['id'] not in added]
                if 'review_tasks_before' in h.get('extra',{}):
                    self.data['review_tasks'] = copy.deepcopy(h['extra']['review_tasks_before'])
                if 'issues_before' in h.get('extra',{}):
                    self.data['issues'] = copy.deepcopy(h['extra']['issues_before'])
                if run := h.get('extra', {}).get('api_review_run_before'):
                    runs = self.data.setdefault('api_review_runs', {})
                    if run['value']:
                        runs[run['episode']] = copy.deepcopy(run['value'])
                    else:
                        runs.pop(run['episode'], None)
                h['undone'] = True
                self.commit('撤销：' + h['label'])
                return
        raise UserError('没有可撤销的人工操作。')

    def apply_certain_opinions(self, ids):
        from .review import safe_correction
        index, chosen, skipped = self.by_id(), [], []
        for cid in dict.fromkeys(ids):
            c = index.get(cid)
            opinion = c['opinions'][-1] if c and c['opinions'] else None
            r = opinion.get('suggestion') if opinion else None
            reason = ''
            if not c or not opinion or not r:
                reason = '没有文字建议'
            elif c['status'] == 'approved' or c.get('draft'):
                reason = '已有人工确认或草稿，不覆盖'
            else:
                reason = safe_correction(c, opinion)
            if reason:
                skipped.append({'id': cid, 'reason': reason})
            else:
                chosen.append((c, copy.deepcopy(r)))
        before = [copy.deepcopy(c) for c, r in chosen]
        for c, r in chosen:
            if c.get('text_review', {}).get('result') == 'correction':
                c.update(accepted=r, draft=None)
                c['text_review'].update(result='correction_applied', snapshot_hash=digest(r), applied_at=now())
            else:
                c.update(draft=r, status='pending')
        if chosen:
            self.commit('批量采用确定文字建议（保留AI修正与人工确认区别）', before)
        return {'applied': len(chosen), 'skipped': skipped}

    def import_legacy(self, folder, progress_path):
        """Read-only adapter for the existing reviewer, using its exact baseline fingerprint."""
        old = Path(folder)
        revision = read_json(old / 'srt_revision.json')
        progress = read_json(progress_path)
        if progress.get('project') != 'dragon-prince-subtitle-review' or progress.get('version') != 1 or progress.get('fingerprint') != revision.get('reviewer_fingerprint'):
            raise UserError('旧进度的项目、版本或指纹与旧基线不匹配。')
        if self.data['episodes']:
            raise UserError('旧项目导入仅可在空项目执行，避免与已有字幕混合。')
        baseline = old / 'source_srt'
        expected = revision['corrected_hashes']
        for p in baseline.glob('*.srt'):
            wanted = expected.get(p.name, expected.get(p.stem))
            if isinstance(wanted, dict):
                wanted = wanted.get('sha256', wanted.get('hash'))
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            if not wanted or actual != wanted:
                raise UserError('旧字幕基线指纹不同：' + p.name)
        results = self.import_pairs([{'episode': p.stem, 'srt': str(p),
                                     'media': str(old / 'audio' / (p.stem + '.wav')) if (old / 'audio' / (p.stem + '.wav')).exists() else ''}
                                    for p in sorted(baseline.glob('*.srt'))])
        if any('error' in x for x in results):
            raise UserError('旧字幕未完整导入，请查看项目配对结果。')
        codes = {}
        for task in revision.get('manual', []):
            hits = task.get('occurrences') if task['episode'] == '全剧' else [{'episode': task['episode'], 'id': i} for i in task['ids']]
            for hit in hits:
                codes.setdefault((hit['episode'], hit['id']), []).append(task['code'])
        mapping = {}
        for c in self.data['cues']:
            key = c['episode'] + ':' + str(c['source_number'])
            mapping[key] = c
        state = progress['state']
        if set(state.get('edits', {})) - set(mapping):
            raise UserError('旧进度含无法定位的条目，未套用。')
        before = copy.deepcopy(self.data['cues'])
        for key, c in mapping.items():
            edit = state.get('edits', {}).get(key)
            pending = list(codes.get((c['episode'], c['source_number']), []))
            if edit:
                row = {'text': edit['text'], 'start_ms': round(edit['start'] * 1000),
                       'end_ms': round(edit['end'] * 1000), 'deleted': edit['deleted']}
                if validate_row(row):
                    raise UserError('旧进度条目非法：' + key)
                pending = [x for x in pending if x not in edit.get('confirmed', [])]
                if edit.get('hold'):
                    c['draft'] = row
                    pending.append('EDIT')
                else:
                    if ' '.join(row['text'].split()) != ' '.join(c['accepted']['text'].split()):
                        c.pop('script_coverage', None)
                    c['accepted'] = row
            c['status'] = 'pending' if pending and not c['accepted']['deleted'] else 'approved'
            c['legacy_key'] = key
            c['note'] = '\n'.join(f'{code}: {state.get("notes", {}).get(code, "")}' for code in codes.get((c['episode'], c['source_number']), []) if state.get('notes', {}).get(code))
        self.data['legacy'] = {'project': progress['project'], 'fingerprint': progress['fingerprint'], 'saved_at': progress.get('savedAt'),
                               'mapping': {key: c['id'] for key, c in mapping.items()}}
        dest = self.root / 'originals' / 'legacy-progress.json'
        shutil.copy2(progress_path, dest)
        # Old transcripts are explicitly labelled imported evidence, never new AI review.
        for ep, e in self.data['episodes'].items():
            t = old / 'transcripts' / (ep + '.json')
            if t.exists():
                dest = self.root / 'evidence' / ep / 'transcript.json'
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(t, dest)
                e['transcript'] = str(dest.relative_to(self.root))
                e['duration_ms'] = round(read_json(t)['duration'] * 1000)
                e['local_status'] = '已导入旧识别证据，尚未运行本版初查'
        self.commit('导入旧项目已确认进度', before)
        return {'episodes': len(results), 'cues': len(mapping), 'pending': sum(c['status'] == 'pending' for c in mapping.values())}

    def update_legacy_progress(self, progress):
        meta = self.data.get('legacy')
        if not meta or progress.get('project') != meta['project'] or progress.get('fingerprint') != meta['fingerprint'] or progress.get('version') != 1:
            raise UserError('旧进度与本项目已建立的映射不匹配。请在空项目使用“导入旧审核项目”。')
        # Replaying arbitrary old progress over newer work is unsafe; retain evidence and require explicit per-cue review.
        raise UserError('该项目已导入旧进度。为保护新修订，请在新的空项目中导入另一份旧进度后核对。')

    def validate_export(self, episodes=None, delivery=False):
        from .review import category, issue_pending, issue_in_scope, task_in_scope
        from .terminology import context
        from .generation import SUBTITLE_PUNCTUATION
        import unicodedata
        review_data,_=context(self)
        wanted = set(episodes or self.data['episodes'])
        errors = []
        if not wanted or not wanted <= set(self.data['episodes']):
            return ['请选择有效集数']
        for ep in sorted(wanted):
            if self.data.get('mode') == 'generation' and not self.data['episodes'][ep].get('original_srt'):
                errors.append(f'第{ep}集尚未生成字幕')
            previous = None
            for c in sorted((c for c in self.data['cues'] if c['episode'] == ep), key=lambda c: (accepted(c)['start_ms'], c['id'])):
                row = accepted(c)
                if delivery and not row['deleted'] and category(c, review_data) not in ('approved', 'text_clear', 'ai_corrected'):
                    errors.append(f'{ep} / {c["source_number"] or c["id"][:8]} 仍有未完成审查/具体疑点/待采用修正')
                if delivery and c.get('draft'):
                    errors.append(f'{ep} / {c["source_number"]} 有未确认草稿')
                if row['deleted']:
                    if delivery and c.get('draft') and not c['draft']['deleted']:
                        errors.append(f'{ep} 有尚未确认的新增/恢复字幕')
                    continue
                errors += [f'{ep} / {c["source_number"] or c["id"][:8]}：{s}' for s in validate_row(row, self.data['episodes'][ep]['duration_ms'])]
                if delivery:
                    label = f'{ep} / {c["source_number"] or c["id"][:8]} · {stamp(row["start_ms"])}'
                    lines = row['text'].splitlines()
                    if len(lines) > 2 or len(''.join(lines)) > 44:
                        errors.append(f'{label}：字幕须在两行以内，且不超过 44 字符')
                    if any(unicodedata.category(ch).startswith('P') and
                           (ord(ch) in SUBTITLE_PUNCTUATION or unicodedata.east_asian_width(ch) in ('F', 'W'))
                           for ch in row['text']):
                        errors.append(f'{label}：请将标点改为英文半角')
                if previous and row['start_ms'] < previous:
                    errors.append(f'{ep} / {c["source_number"] or c["id"][:8]} · {stamp(row["start_ms"])}：与上一条重叠')
                previous = max(previous or 0, row['end_ms'])
        if delivery:
            index = self.by_id()
            for issue in self.data['issues']:
                linked_cue = index.get(issue.get('cue_id'))
                scope = linked_cue['episode'] if linked_cue else str(issue.get('episode') or '')
                retained = issue.get('triage', {}).get('result') == 'retained'
                if ((scope in wanted or scope not in self.data['episodes'])
                        and (not issue.get('cue_id') or retained)
                        and issue_in_scope(self.data, issue) and issue_pending(issue)):
                    excerpt = issue.get('script_text') or issue.get('evidence_text') or issue.get('message') or issue['id']
                    errors.append(f'{scope or "集数未定位"} · {stamp(issue.get("start_ms", 0))}：{issue.get("kind", "独立疑点")}待处理，{excerpt[:100]}')
            if any(not x.get('resolved') for x in self.data['opinion_conflicts']):
                errors.append('有待处理的意见导入冲突')
            for task in self.data.get('review_tasks', []):
                if task.get('resolved') or not task_in_scope(self.data, task):
                    continue
                scope = {str(task.get('episode') or '')}
                scope.update(str(ep) for ep in task.get('related_episodes', []))
                scope.update(index[cid]['episode'] for cid in task.get('related_stable_ids', []) if cid in index)
                scope.intersection_update(self.data['episodes'])
                if not scope or scope & wanted:
                    errors.append('有未处理的具体疑点或尚未完成的审查任务')
                    break
        return errors

    def srt_text(self, episode):
        blocks, mapping = [], []
        rows = sorted((c for c in self.data['cues'] if c['episode'] == episode), key=lambda c: (accepted(c)['start_ms'], c['id']))
        n = 0
        for c in rows:
            row = accepted(c)
            if not row['deleted']:
                n += 1
                blocks.append(f'{n}\n{stamp(row["start_ms"])} --> {stamp(row["end_ms"])}\n{row["text"].strip()}\n')
            mapping.append({'stable_id': c['id'], 'original_number': c['source_number'], 'exported_number': None if row['deleted'] else n,
                            'merged_ids': c.get('merged_ids'), 'merged_into': c.get('merged_into')})
        return '\n'.join(blocks), mapping

    def cleanup_plan(self):
        # Audio/transcripts/chunks are evidence, including evidence shared with external AI.
        # Only these two allowlisted regenerable locations can ever be removed.
        files = []
        for category, relative in [('临时处理文件', 'cache/tmp'), ('波形（可重新生成）', 'cache/waveforms')]:
            root = self.root / relative
            if not root.exists() or root.is_symlink() or getattr(root, 'is_junction', lambda: False)():
                continue
            for p in root.rglob('*'):
                if p.is_file() and not p.is_symlink() and inside(p, root):
                    files.append({'path': str(p.relative_to(self.root)), 'bytes': p.stat().st_size, 'category': category})
        return {'project': self.data['name'], 'root': str(self.root), 'files': files, 'bytes': sum(p['bytes'] for p in files),
                'warning': '波形下次播放时重新生成；完整音频、识别分段、交接证据、模型、原素材及全部审核记录保留。',
                'token': digest(files)}

    def cleanup(self, token):
        plan = self.cleanup_plan()
        if token != plan['token']:
            raise UserError('缓存范围已变化，请重新预览后确认。')
        results = []
        for item in plan['files']:
            p = self.root / item['path']
            if not inside(p, self.root / 'cache') or p.is_symlink():
                results.append({'path': item['path'], 'error': '路径安全检查未通过'})
                continue
            try:
                p.unlink()
                results.append({'path': item['path'], 'deleted': True})
            except OSError as exc:
                results.append({'path': item['path'], 'error': str(exc)})
        self.commit('清理可再生成缓存', extra={'results': results})
        return results
