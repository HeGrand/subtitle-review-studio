"""Explicit, undoable split/merge actions for generated subtitles."""
import copy
import uuid
from .core import UserError, now, proposed, validate_row
from .generation import require_generation


def layout(project, cue_id, action, values):
    require_generation(project)
    cue = project.by_id().get(cue_id)
    if not cue or proposed(cue)['deleted'] or cue.get('draft_time_error'):
        raise UserError('请选择有效字幕并先修复时间')
    if action == 'shared-boundary':
        neighbor = project.by_id().get(values.get('next_id'))
        rows = sorted((c for c in project.data['cues'] if c['episode'] == cue['episode'] and not proposed(c)['deleted']),
                      key=lambda c: (c['accepted']['start_ms'], c['id']))
        position = rows.index(cue)
        if position + 1 >= len(rows) or rows[position + 1] is not neighbor or neighbor.get('draft_time_error'):
            raise UserError('相邻字幕已变化，或下一条时间草稿无效。')
        point = values.get('boundary_ms')
        left, right = proposed(cue), proposed(neighbor)
        if values.get('before') != [left, right]:
            raise UserError('字幕内容或时间已变化，请重新打开切点。')
        if type(point) is not int or not left['start_ms'] < point < right['end_ms']:
            raise UserError('切点须位于两条字幕的总时间范围内。')
        drafts = [dict(left, end_ms=point), dict(right, start_ms=point)]
        duration = project.data['episodes'][cue['episode']]['duration_ms']
        errors = [message for row in drafts for message in validate_row(row, duration)]
        if errors:
            raise UserError('；'.join(errors))
        if ((position and proposed(rows[position - 1])['end_ms'] > drafts[0]['start_ms'])
                or (position + 2 < len(rows) and proposed(rows[position + 2])['start_ms'] < drafts[1]['end_ms'])):
            raise UserError('两条字幕的外侧边界与其他字幕重叠，请先修正外侧入出点。')
        before = [copy.deepcopy(cue), copy.deepcopy(neighbor)]
        for item, draft in zip((cue, neighbor), drafts):
            item.update(draft=draft, status='pending', confirmed_at=None)
        project.commit('调整相邻字幕共同切点（待确认）', before)
        return {'id': cue_id}
    if action == 'resegment':
        from .api_review import boundary_plan
        index = values.get('opinion')
        if type(index) is not int or not 0 <= index < len(cue['opinions']):
            raise UserError('断句建议已过期，请重新复查。')
        saved = cue['opinions'][index].get('layout_suggestion')
        if not saved or cue['accepted']['deleted']:
            raise UserError('断句建议已过期，请重新复查。')
        ordered = sorted((c for c in project.data['cues'] if c['episode'] == cue['episode'] and not c['accepted']['deleted']),
                         key=lambda c: (c['accepted']['start_ms'], c['id']))
        pos = next(i for i, item in enumerate(ordered) if item['id'] == cue_id)
        following = ordered[pos + 1] if pos + 1 < len(ordered) else None
        plan = boundary_plan(project, cue, following, saved['after'])
        if not plan or plan != saved:
            raise UserError('相邻字幕或识别时间已变化，请重新复查。')
        before = [copy.deepcopy(cue), copy.deepcopy(following)]
        for item, row in zip((cue, following), plan['rows']):
            item.pop('script_coverage', None)
            item.update(accepted=row, status='pending', manual_hold=True,
                        generation_source=None, script_locator=row['text'], confirmed_at=None)
        project.commit('采用 AI 断句建议（待听改）', before)
        return {'id': cue_id}
    row = copy.deepcopy(proposed(cue))
    before = [copy.deepcopy(cue)]
    extra = {}
    if action == 'split':
        boundary = int(values['boundary_ms'])
        left, right = str(values['left']).strip(), str(values['right']).strip()
        if not row['start_ms'] < boundary < row['end_ms'] or not left or not right:
            raise UserError('拆分点须位于字幕时间范围内，两段文字不能为空')
        second = dict(row, text=right, start_ms=boundary)
        row.update(text=left, end_ms=boundary)
        errors = validate_row(row) + validate_row(second)
        if errors:
            raise UserError('；'.join(errors))
        cid = uuid.uuid4().hex
        added = {'id': cid, 'episode': cue['episode'], 'source_number': None,
                 'original': dict(second, deleted=True), 'accepted': second, 'draft': None,
                 'status': 'pending', 'manual_hold': True, 'note': cue.get('note', ''), 'opinions': [],
                 'confirmed_at': None, 'script_locator': right}
        project.data['cues'].append(added)
        extra['added'] = [cid]
    elif action == 'merge':
        ordered = sorted((c for c in project.data['cues'] if c['episode'] == cue['episode'] and not proposed(c)['deleted']),
                         key=lambda c: (proposed(c)['start_ms'], c['id']))
        pos = next(i for i, c in enumerate(ordered) if c['id'] == cue_id)
        if pos + 1 >= len(ordered):
            raise UserError('后面没有可以合并的字幕')
        other = ordered[pos + 1]
        if 'next_id' in values and values['next_id'] != other['id']:
            raise UserError('相邻字幕已变化，请重新选择后合并')
        if other.get('draft_time_error'):
            raise UserError('下一条时间尚未修复')
        before.append(copy.deepcopy(other))
        tail = proposed(other)
        row.update(text=str(values['text']).strip(), end_ms=max(row['end_ms'], tail['end_ms']))
        if not row['text']:
            raise UserError('合并文字不能为空')
        errors = validate_row(row, project.data['episodes'][cue['episode']]['duration_ms'])
        if errors:
            raise UserError('；'.join(errors))
        other['accepted'] = dict(tail, deleted=True)
        other.update(draft=None, merged_into=cue_id, status='approved', confirmed_at=now())
        cue['merged_ids'] = list(dict.fromkeys((cue.get('merged_ids') or [cue_id]) + (other.get('merged_ids') or [other['id']])))
        cue['note'] = '；'.join(x for x in (cue.get('note', ''), other.get('note', '')) if x)
    else:
        raise UserError('未知断句操作')
    errors = validate_row(row, project.data['episodes'][cue['episode']]['duration_ms'])
    if errors:
        raise UserError('；'.join(errors))
    cue.pop('script_coverage', None)
    cue.update(accepted=row, draft=None, status='pending', manual_hold=True,
               generation_source=None, script_locator=row['text'], confirmed_at=None)
    project.commit('手动拆分字幕' if action == 'split' else '手动合并字幕', before, extra)
    return {'id': cue_id}
