"""Local speech-onset suggestions for existing generated captions."""
import copy
import wave
from bisect import bisect_left

from .core import UserError, proposed
from .script_context import load_transcript, safe_path, signature, tokens


def early_onset_reasons(project):
    if project.data.get('mode') != 'generation':
        return {}
    by_episode = {}
    for cue in project.data['cues']:
        if not cue['accepted']['deleted']:
            by_episode.setdefault(cue['episode'], []).append(cue)
    reasons = {}
    for episode, cues in by_episode.items():
        relative = project.data['episodes'][episode].get('transcript')
        path = safe_path(project.root, relative) if relative else None
        if not path:
            continue
        words = sorted((round(w['start'] * 1000), round(w['end'] * 1000), w['word'])
                       for segment in load_transcript(signature(path)).get('segments', [])
                       for w in segment.get('words', []) if w['end'] - w['start'] >= 1.0)
        starts = [word[0] for word in words]
        ordered = sorted(cues, key=lambda cue: cue['accepted']['start_ms'])
        for previous, cue in zip(ordered, ordered[1:]):
            row = cue['accepted']
            start = row['start_ms']
            if cue['status'] == 'approved' or abs(start - previous['accepted']['end_ms']) > 150:
                continue
            first = tokens(row['text'])
            lo, hi = bisect_left(starts, start - 80), bisect_left(starts, start + 81)
            for word_start, word_end, text in words[lo:hi]:
                heard = tokens(text)
                if first and heard and first[0][0] == heard[0][0] and word_end <= row['end_ms']:
                    duration = (word_end - word_start) / 1000
                    reasons[cue['id']] = f'疑似入点过早：首词 {text.strip()} 的识别时间跨度为 {duration:.2f} 秒，入点贴近上一条出点。请连同上一条试听，核对两句之间的切换。'
                    break
    return reasons


def speech_regions(path):
    import numpy as np
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    with wave.open(str(path), 'rb') as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise UserError('入点校准需要应用提取的 16 kHz 单声道音频。')
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    options = VadOptions(threshold=.3, min_silence_duration_ms=100, speech_pad_ms=0)
    return [(r['start'] // 16, r['end'] // 16) for r in get_speech_timestamps(samples, options)]


def preview(project, episode):
    media = project.data['episodes'].get(episode, {})
    path = safe_path(project.root, media['audio']) if media.get('audio') else None
    if not path or not path.is_file():
        raise UserError('本集没有可用的提取音频，请先生成字幕。')
    regions = speech_regions(path)
    cues = sorted((c for c in project.data['cues'] if c['episode'] == episode and not proposed(c)['deleted']),
                  key=lambda c: (proposed(c)['start_ms'], c['id']))
    candidates = []
    for cue, target, needs_listen in onset_candidates(cues, regions, proposed):
        if cue.get('draft') or cue.get('draft_time_error'):
            continue
        row = proposed(cue)
        candidates.append({'id': cue['id'], 'text': row['text'], 'number': cue.get('source_number'),
                           'start': row['start_ms'], 'target': target, 'needs_listen': needs_listen})
    return candidates


def continuous_gaps(rows, regions, protected_ranges=()):
    for row, following in zip(rows, rows[1:]):
        end, start = row['end_ms'], following['start_ms']
        if (0 < start - end <= 250 and any(a <= end and start <= b for a, b in regions)
                and not any(a < start and b > end for a, b in protected_ranges)):
            yield row, start


def gap_preview(project, episode):
    media = project.data['episodes'].get(episode, {})
    audio = safe_path(project.root, media['audio']) if media.get('audio') else None
    if not audio or not audio.is_file():
        raise UserError('本集没有可用的提取音频。')
    cues = sorted((c for c in project.data['cues'] if c['episode'] == episode and not c['accepted']['deleted']),
                  key=lambda c: (c['accepted']['start_ms'], c['id']))
    rows = [dict(c['accepted'], id=c['id']) for c in cues]
    protected = {c['id'] for c in cues if c.get('draft') or c.get('draft_time_error')}
    protected.update(a['id'] for a, b in zip(cues, cues[1:]) if b['id'] in protected)
    extra_ranges = [(i['start_ms'], i['end_ms']) for i in project.data['issues']
                    if i.get('episode') == episode and i.get('script_only_extra') and not i.get('resolved')]
    return [dict(row, target=target) for row, target in continuous_gaps(rows, speech_regions(audio), extra_ranges)
            if row['id'] not in protected]


def apply_gaps(project, episode, ids):
    if not isinstance(ids, list) or not ids or any(not isinstance(cid, str) for cid in ids):
        raise UserError('请勾选要接上的短空档。')
    selected = set(ids)
    rows = [row for row in gap_preview(project, episode) if row['id'] in selected]
    if len(rows) != len(selected):
        raise UserError('字幕边界已变化，请重新预览。')
    index = project.by_id()
    before = [copy.deepcopy(index[row['id']]) for row in rows]
    for row in rows:
        cue = index[row['id']]
        cue.setdefault('time_adjustments', []).append({
            'field': 'end_ms', 'from': row['end_ms'], 'to': row['target'],
            'reason': '人工采用连续人声短空档校准', 'applied': True})
        cue['accepted']['end_ms'] = row['target']
        cue.update(status='pending', manual_hold=True, confirmed_at=None)
    project.commit('接上连续人声的短空档（待听改）', before)
    return {'applied': len(rows)}


def onset_candidates(items, regions, row_for):
    region = iter(regions)
    current = next(region, None)
    for item in items:
        row = row_for(item)
        start, end = row['start_ms'], row['end_ms']
        while current and current[1] <= start:
            current = next(region, None)
        if not current:
            continue
        onset = current[0]
        target = onset - 120
        if target - start < 80 or onset >= end or end - target < 300:
            continue
        yield item, target, onset - start > 650


def apply(project, episode, changes):
    if not isinstance(changes, dict) or not changes:
        raise UserError('请勾选要采用的入点。')
    by_id = project.by_id()
    updates = []
    for cid, target in changes.items():
        cue = by_id.get(cid)
        if (not cue or cue['episode'] != episode or cue.get('draft') or cue.get('draft_time_error')
                or cue['accepted']['deleted']):
            raise UserError('字幕已变化或有未确认草稿，请重新校准。')
        row = cue['accepted']
        if type(target) is not int or not row['start_ms'] <= target < row['end_ms']:
            raise UserError('校准入点须在当前入点与出点之间。')
        if target != row['start_ms']:
            updates.append((cue, target))
    before = [copy.deepcopy(cue) for cue, _ in updates]
    for cue, target in updates:
        cue.setdefault('time_adjustments', []).append({
            'field': 'start_ms', 'from': cue['accepted']['start_ms'], 'to': target,
            'reason': '人工采用入点校准', 'applied': True})
        cue['accepted']['start_ms'] = target
        cue.update(status='pending', manual_hold=True, confirmed_at=None)
    if updates:
        project.commit('校准字幕入点（待听改）', before)
    return {'applied': len(updates)}
