"""Preview and explicit human policy decisions for recurring terminology."""
import copy,re,difflib
from collections import defaultdict
from .core import digest,now,UserError


def phrases(text):
    result=[]
    for m in re.finditer(r"\b[A-Z][A-Za-z'-]+(?:\s+[A-Z][A-Za-z'-]+){1,2}\b",text):
        words=m[0].split()
        while words and words[0] in ('The','In','So','Now','This','As'):words.pop(0)
        while len(words)>2 and words[-1] in ('Transformation','Form','Ready'):words.pop()
        if len(words)>=2:result.append(' '.join(words))
    return result


def protected(c):
    return c['status']=='approved' or bool(c.get('draft') or c.get('note') or c.get('manual_hold'))


def plan(project):
    data=project.data
    texts=[]
    for ref in data['references']:
        try:texts.append((project.root/ref['text_file']).read_text(encoding='utf-8'))
        except OSError:pass
    reference='\n'.join(texts)
    known_terms=set()
    for row in data['cues']:
        if row['opinions']:
            opinion=row['opinions'][-1]
            if re.search('术语|正式.*拼写|名称|人名',opinion.get('reason','')):
                known_terms.update(x for x in phrases(opinion.get('review_summary',{}).get('script_text') or opinion.get('reason','')) if x.lower() in reference.lower())
    seeds={};variants=defaultdict(set)
    for c in data['cues']:
        if not c['opinions']:continue
        o=c['opinions'][-1];explicit=o.get('term_review',{})
        if not isinstance(explicit,dict):explicit={}
        reason=o.get('reason','')
        if not explicit and not re.search('术语|正式.*拼写|名称|人名',reason) and not (o.get('text_review',{}).get('result')=='human' and set(phrases(o.get('review_summary',{}).get('script_text',''))) & known_terms):continue
        raw=explicit.get('canonical') or o.get('review_summary',{}).get('script_text') or reason
        candidates=[x for x in ([explicit['canonical']] if explicit.get('canonical') else phrases(raw)) if x.lower() in reference.lower()]
        if not candidates:continue
        # Script sentence/canonical assignment determines sense. Never mix King with Kingdom.
        canonical=max(candidates,key=lambda x:len(x.split()))
        tokens=list(re.finditer(r"[A-Za-z][A-Za-z'-]*",c['accepted']['text']))
        size=len(canonical.split());best=None
        for i in range(len(tokens)-size+1):
            start,end=tokens[i].start(),tokens[i+size-1].end();observed=c['accepted']['text'][start:end]
            score=difflib.SequenceMatcher(None,canonical.lower(),observed.lower()).ratio()
            if score>=.62 and (best is None or score>best[0]):best=(score,observed)
        if best:
            seeds[c['id']]=canonical
            variants[canonical].add(best[1])
    owners=defaultdict(set)
    for canon,values in variants.items():
        for value in values:owners[value.lower()].add(canon)
    groups=[]
    for canonical,values in variants.items():
        members=[]
        for c in data['cues']:
            if c['accepted']['deleted']:continue
            if c['id'] in seeds and seeds[c['id']]!=canonical:continue
            choices=[canonical,*values]
            hit=next((m for value in sorted(choices,key=len,reverse=True) if (m:=re.search(r'\b'+re.escape(value)+r'\b',c['accepted']['text'],re.I))),None)
            if not hit:continue
            if len(owners.get(hit[0].lower(),set()))>1 and c['id'] not in seeds:continue
            policy=c.get('term_policy',{})
            done=policy.get('canonical')==canonical and policy.get('snapshot_hash')==digest(c['accepted'])
            done=done or (hit[0].lower()==canonical.lower() and c.get('text_review',{}).get('result') in ('clear','correction_applied') and c['text_review'].get('snapshot_hash')==digest(c['accepted']))
            reason=c['opinions'][-1].get('reason','') if c['opinions'] else ''
            extra=bool(re.search('配对|时间错|边界|漏句|中文|否定|数字',reason))
            extra=extra or any(i.get('cue_id')==c['id'] and not i.get('resolved') and i.get('triage',{}).get('result')=='retained' and i['kind']!='文字差异' for i in data['issues'])
            members.append({'id':c['id'],'episode':c['episode'],'number':c['source_number'],'text':c['accepted']['text'],
                            'observed':hit[0],'start':hit.start(),'end':hit.end(),'protected':protected(c),'handled':done,
                            'other_problem':extra,'snapshot_hash':digest(c['accepted'])})
        if len(members)>=2:
            groups.append({'id':digest(canonical.lower())[:20],'canonical':canonical,'members':members,
                           'meaning_warning':'本组按完整术语单独确认；King 与 Kingdom 不跨组替换。',
                           'pending':sum(not m['protected'] and not m['handled'] for m in members)})
    return {'groups':groups,'token':digest([data['revision'],groups])}


def context(project):
    result=plan(project)
    routes={m['id']:g['id'] for g in result['groups'] for m in g['members'] if not m['handled'] and not m['protected'] and not m['other_problem']}
    return {**project.data,'_term_routes':routes},result


def apply(project,group_id,choice,text,token):
    preview=plan(project)
    if preview['token']!=token:raise UserError('项目已变化，请重新预览术语组。')
    group=next((g for g in preview['groups'] if g['id']==group_id),None)
    if not group:raise UserError('术语组不存在。')
    if choice not in ('script','current','audio'):raise UserError('请选择剧本、当前写法或按音频指定写法。')
    target=group['canonical'] if choice=='script' else text.strip()
    if choice=='audio' and (not target or '\n' in target):raise UserError('请填写你确认的音频对应术语写法。')
    index=project.by_id();before=[];issues_before=copy.deepcopy(project.data['issues'])
    for member in group['members']:
        if member['protected'] or member['handled']:continue
        c=index[member['id']];before.append(copy.deepcopy(c))
        if choice!='current':c['accepted']['text']=c['accepted']['text'][:member['start']]+target+c['accepted']['text'][member['end']:]
        c['term_policy']={'canonical':group['canonical'],'choice':choice,'target':target if choice!='current' else member['observed'],
                          'at':now(),'snapshot_hash':digest(c['accepted']),'direct_audio_verified':False}
        # Only the terminology concern is resolved, never an unrelated timing/material concern.
        if not member['other_problem']:
            for issue in project.data['issues']:
                if issue.get('cue_id')==c['id'] and issue['kind']=='文字差异':
                    issue['triage']={'result':'dismissed','reason':'用户集中确认了本组术语政策，保留历史证据','source':'human_term_policy'}
            if c.get('text_review',{}).get('performed'):
                c['text_review'].update(result='clear',snapshot_hash=digest(c['accepted']),policy='human_term_policy')
        else:
            if c.get('text_review'):c['text_review']['snapshot_hash']=digest(c['accepted'])
    if before:
        project.commit('集中确认术语：'+group['canonical'],before,{'issues_before':issues_before,'term_choice':choice})
    return {'applied':len(before),'skipped_protected':sum(m['protected'] for m in group['members'])}
