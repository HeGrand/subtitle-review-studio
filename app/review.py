"""Review scope is independent of human approval and acoustic verification."""
import copy
import re
import difflib
from .core import digest, now, UserError

LABELS = {'human': '具体疑点待人工', 'term': '术语集中确认', 'draft': '人工草稿待确认', 'candidate': '机器初查候选',
          'incomplete': '文字审查未完成', 'text_clear': '文字审查未见具体问题',
          'ai_corrected': 'AI确定文字修正', 'correction': '确定修正待采用', 'approved': '已人工通过 / 删除', 'merged':'已合并映射记录'}


def valid_review(c):
    r = c.get('text_review', {})
    return r if r.get('snapshot_hash') == digest(c['accepted']) else {}


def issue_pending(i):
    return not i.get('resolved') and i.get('triage', {}).get('result') != 'dismissed'


def issue_in_scope(data,issue):
    return bool(issue.get('cue_id')) or data.get('review_scope','existing_subtitles')!='existing_subtitles'


def task_in_scope(data,task):
    if data.get('review_scope','existing_subtitles')!='existing_subtitles':return True
    if task.get('related_stable_ids'):return True
    text=str(task.get('remaining_id',''))+' '+task.get('reason','')
    if re.search('PAIRING|TIMING|配对|时间错位',text):return True
    return not bool(re.search('漏句|无对应字幕|无字幕',text))


def human_reasons(c,data):
    reasons=[];related=set(c.get('merged_ids') or [c['id']])
    if c.get('draft'):reasons.append('本条有尚未确认的人工草稿')
    if c.get('manual_hold'):reasons.append('本条被明确恢复为待人工审核')
    if c.get('note'):reasons.append('本条有人工备注：'+c['note'])
    reasons.extend(t.get('reason','具体任务待处理') for t in data.get('review_tasks',[]) if not t.get('resolved') and related & set(t.get('related_stable_ids',[])))
    r=valid_review(c)
    if r.get('result')=='human' and c['opinions']:reasons.append(c['opinions'][-1].get('reason','具体文字疑点'))
    reasons.extend(i.get('triage',{}).get('reason') or i.get('message','具体初查疑点') for i in data['issues'] if i.get('cue_id') in related and issue_pending(i) and i.get('triage',{}).get('result')=='retained')
    return list(dict.fromkeys(reasons))


def category(c, data):
    if c['status'] == 'approved':
        return 'approved'
    if c.get('draft'):
        return 'draft'
    if c.get('manual_hold') or c.get('note'):
        return 'human'
    if c.get('merged_into'):
        return 'merged'  # Layout tombstone; its live parent still requires review.
    r = valid_review(c)
    related = set(c.get('merged_ids') or [c['id']])
    tasks = [t for t in data.get('review_tasks', []) if not t.get('resolved') and related & set(t.get('related_stable_ids', []))]
    if tasks or r.get('result') == 'human':
        return 'term' if c['id'] in data.get('_term_routes',{}) and not tasks else 'human'
    if any(i.get('cue_id') in related and issue_pending(i) and i.get('triage',{}).get('result') == 'retained' for i in data['issues']):
        return 'human'
    if r.get('result') == 'correction':
        return 'correction'
    if r.get('result') in ('clear', 'correction_applied'):
        if any(i.get('cue_id') in related and issue_pending(i) for i in data['issues']):
            return 'candidate'
        return 'ai_corrected' if r['result'] == 'correction_applied' else 'text_clear'
    return 'incomplete'


def classify_remaining(data, item, bundle_id=''):
    item = copy.deepcopy(item)
    key = item.get('remaining_id', '')
    issue_id = item.get('issue_id') or (key[6:] if key.startswith('ISSUE-') else '')
    ids = {i['id'] for i in data['issues']}
    if issue_id not in ids:
        found = re.findall(r'\b[a-f0-9]{20}\b', str(item.get('evidence', [])))
        issue_id = next((x for x in found if x in ids), '')
    if issue_id:
        return 'issue_reference', dict(item, issue_id=issue_id)
    if item.get('kind') == 'scope' or key.endswith('FULL-AUDIO'):
        return 'scope', item
    links = item.get('related_stable_ids', [])
    kind = item.get('kind', 'human' if links or key.startswith(('TEXT-', 'EP44-45', 'EP48-')) else 'incomplete')
    identity = [item.get('episode'), sorted(links), item.get('start_ms'), item.get('end_ms'), key or item.get('reason')]
    item.update(id=digest(identity)[:24], kind=kind, bundle_id=bundle_id, related_stable_ids=links)
    return 'task', item


def add_remaining(data, item, bundle_id=''):
    group, record = classify_remaining(data, item, bundle_id)
    field = {'scope': 'review_limits', 'issue_reference': 'issue_references', 'task': 'review_tasks'}[group]
    records = data.setdefault(field, [])
    record.setdefault('bundle_id',bundle_id)
    identity = record.get('issue_id') or record.get('id') or digest([record.get('episode'), record.get('kind'), record.get('remaining_id'), record.get('reason')])
    if not any(r.get('_identity') == identity for r in records):
        if group == 'scope' and bundle_id:
            for older in records:
                if older.get('episode') == record.get('episode') and older.get('bundle_id') != bundle_id:
                    older['superseded'] = True
        record['_identity'] = identity
        record.setdefault('resolved', False)
        records.append(record)


def migrate(data):
    if data.get('review_migration_version') == 1:
        return False
    # Old unable opinions remain untouched, and are NOT promoted to completed review.
    real = []
    for conflict in data['opinion_conflicts']:
        if conflict['reason'] == 'AI剩余未决项':
            add_remaining(data, dict(conflict['opinion'], resolved=conflict.get('resolved', False)))
            data.setdefault('legacy_import_notices', []).append(conflict)
        elif conflict['reason'].startswith('已有人工确认'):
            data.setdefault('legacy_import_notices', []).append(conflict)
        else:
            real.append(conflict)
    data['opinion_conflicts'] = real
    data['review_migration_version'] = 1
    data.setdefault('review_tasks', [])
    data.setdefault('review_limits', [])
    data.setdefault('issue_references', [])
    reconcile_human_tasks(data)
    return True


def accept_assessment(project, c, opinion):
    assessment = opinion.get('text_review')
    if not assessment:
        return  # Old files cannot acquire a new review result by relabelling unable.
    if c['status'] == 'approved':
        return
    c['text_review'] = {**copy.deepcopy(assessment), 'snapshot_hash': digest(c['accepted']),
                        'opinion_id': opinion['opinion_id'], 'bundle_id': opinion['bundle_id'], 'at': now()}
    # Supersede old generic/observation tasks only with an explicit, evidence-backed new assessment.
    for task in project.data.get('review_tasks', []):
        if c['id'] in task.get('related_stable_ids', []) and task.get('kind') != 'incomplete':
            task.update(superseded=True, resolved=True, resolution='由新的逐条文字审查结果取代，原记录保留')


def reconcile_human_tasks(data):
    index = {c['id']:c for c in data['cues']}
    for task in data.get('review_tasks', []):
        links=task.get('related_stable_ids', [])
        if links and not task.get('new_evidence_after_human') and all(index.get(cid,{}).get('status')=='approved' for cid in links):
            task.update(resolved=True, resolution='已有人工确认覆盖该项，保留原任务与确认记录，不重复要求处理')


def safe_correction(c, o):
    r = o.get('suggestion')
    if not r or o.get('decision') != 'suggest':
        return '没有确定修改建议'
    if c['status'] == 'approved' or c.get('draft') or c.get('note') or c.get('manual_hold'):
        return '已有人工确认、草稿或备注，不覆盖'
    if all(r.get(k)==c['accepted'].get(k) for k in ('text','start_ms','end_ms','deleted')):
        return '建议已采用，无需重复处理'
    if o.get('confidence') != 'high' or o.get('needs_human') or o.get('chinese_audio'):
        return '确定程度不足或涉及具体人工疑点'
    if o.get('change_kind') not in ('spelling', 'punctuation', 'minor_wording', 'formal_name') or o.get('meaning_unchanged') is not True:
        return '不是语义不变的文字小修'
    if r.get('deleted') or c['accepted']['deleted'] or r.get('start_ms') != c['accepted']['start_ms'] or r.get('end_ms') != c['accepted']['end_ms']:
        return '增删对白或时间修改须人工确认'
    pattern = r"\d+|\b(?:not|no|never|mustn't|can't|cannot|don't|doesn't|didn't|won't|isn't|aren't|wasn't|weren't)\b"
    if re.findall(pattern, r['text'].lower()) != re.findall(pattern, c['accepted']['text'].lower()):
        return '数字或否定表达发生变化'
    protected = {'i','you','he','she','they','we','him','her','them','king','kingdom','kill','save','die','live','love','hate',
                 'wife','wives','husband','husbands','boy','girl','boys','girls','man','woman','men','women','mother','father'}
    old_words = re.findall(r'[a-z]+', c['accepted']['text'].lower())
    new_words = re.findall(r'[a-z]+', r['text'].lower())
    verbs={'killed':'kill','kills':'kill','killing':'kill','saved':'save','saves':'save','saving':'save',
           'loved':'love','loves':'love','loving':'love','hated':'hate','hates':'hate','hating':'hate',
           'died':'die','dies':'die','dying':'die','lived':'live','lives':'live','living':'live'}
    old_words=[verbs.get(w,w) for w in old_words]
    new_words=[verbs.get(w,w) for w in new_words]
    if any(old_words.count(w) != new_words.count(w) for w in protected):
        return '人物指向或事件含义词变化，需人工决定'
    if o.get('change_kind')=='formal_name':
        delta=difflib.SequenceMatcher(None,old_words,new_words).get_opcodes()
        for kind,a,b,x,y in delta:
            if kind!='equal' and difflib.SequenceMatcher(None,' '.join(old_words[a:b]),' '.join(new_words[x:y])).ratio()<.75:
                return '姓名差异较大，不能作为轻微读音偏差批量处理'
    # Direct acoustic input is optional, but textual corroboration is mandatory when absent.
    if not o.get('audio_checked'):
        support = o.get('text_review', {}).get('support', [])
        kinds = {s.get('type') for s in support if isinstance(s, dict) and s.get('quote') and s.get('path')}
        if not {'script', 'asr', 'context'} <= kinds:
            return '缺少剧本、识别与上下文的具体支持'
        if o.get('change_kind') == 'formal_name' and not o.get('text_review', {}).get('minor_pronunciation'):
            return '姓名读音差异程度未确认'
        if o.get('text_review', {}).get('result') != 'correction':
            return '未完成确定修正的实际文字审查'
    return ''


def merge_plan(project):
    refs = '\n'.join((project.root / r['text_file']).read_text(encoding='utf-8') for r in project.data['references'])
    known = set(re.findall(r'\b(?:Mr|Mrs|Ms|Dr)\.\s+([A-Z][a-zA-Z\'-]+)', refs))
    rows = sorted(project.data['cues'], key=lambda c: (int(c['episode']), c['accepted']['start_ms'], c.get('source_number') or 0))
    candidates = []
    for a, b in zip(rows, rows[1:]):
        ra, rb = a['accepted'], b['accepted']
        if not re.fullmatch(r'(?:Mr|Mrs|Ms|Dr)\.', ra['text'].strip()) or ra['deleted'] or rb['deleted']:
            continue
        surname = re.match(r"([A-Z][a-zA-Z'-]+)\b", rb['text'])
        reasons = []
        if a['episode'] != b['episode']: reasons.append('跨集')
        if not surname or surname[1] not in known: reasons.append('后条姓名缺少正式资料支持')
        if not -20 <= rb['start_ms'] - ra['end_ms'] <= 500: reasons.append('间隔过大或重叠')
        if a.get('speaker') and b.get('speaker') and a['speaker'] != b['speaker']: reasons.append('跨说话人')
        for c in (a,b):
            if c['status'] == 'approved' or c.get('draft') or c.get('note') or any(c['accepted'][k] != c['original'][k] for k in ('text','start_ms','end_ms','deleted')):
                reasons.append('已有人工作业，不自动合并')
        candidates.append({'ids': [a['id'], b['id']], 'episode': a['episode'], 'numbers': [a['source_number'],b['source_number']],
                           'text': ra['text'].strip()+' '+rb['text'].strip(), 'start_ms': ra['start_ms'], 'end_ms': rb['end_ms'],
                           'eligible': not reasons, 'reasons': sorted(set(reasons))})
    return {'candidates': candidates, 'token': digest([project.data['revision'], candidates])}


def merge_apply(project, ids, token):
    plan = merge_plan(project)
    if token != plan['token']: raise UserError('字幕已变化，请重新预览合并范围。')
    chosen = [x for x in plan['candidates'] if x['ids'][0] in ids]
    if len(chosen) != len(set(ids)) or any(not x['eligible'] for x in chosen): raise UserError('所选合并项不可安全处理。')
    index, before = project.by_id(), []
    for item in chosen:
        a,b = [index[cid] for cid in item['ids']]
        before.extend([copy.deepcopy(a),copy.deepcopy(b)])
        text_checked = all(category(c,project.data) in ('text_clear','ai_corrected') for c in (a,b))
        a['accepted'].update(text=item['text'],end_ms=item['end_ms'])
        a['merged_ids'] = item['ids']
        b['accepted']['deleted'] = True
        b['merged_into'] = a['id']
        if text_checked:
            a['text_review'] = {'performed':True,'result':'clear','snapshot_hash':digest(a['accepted']),
                                'method':'仅合并已完成文字审查的两条；文字顺序不变，保留两条原审查依据，不表示声音通过',
                                'merge_source_reviews':[copy.deepcopy(before[-2].get('text_review')),copy.deepcopy(before[-1].get('text_review'))]}
    if before: project.commit('称谓与姓名合并（排版，不代表人工通过）',before)
    return {'merged':len(chosen)}
