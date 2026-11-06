"""Read-only source quotations for review; never treat ASR as script evidence."""
import json
import re
from functools import lru_cache
from pathlib import Path

TOKEN = re.compile(r"[a-zA-Z0-9]+(?:['’][a-zA-Z0-9]+)*|[\u3400-\u9fff]")


def tokens(text):
    return [(m.group().casefold().replace('’', "'"), m.start(), m.end()) for m in TOKEN.finditer(text)]


def signature(path):
    try:
        s = path.stat()
        return str(path), s.st_mtime_ns, s.st_size
    except OSError:
        return str(path), 0, 0


def safe_path(root, relative):
    path = (Path(root) / relative).resolve()
    return path if path.is_relative_to(Path(root).resolve()) else None


def episode_number(text):
    match = re.fullmatch(r'(?:EPISODE|EP)\s*(\d+)', text, re.I)
    if not match:
        match = re.fullmatch(r'第\s*(\d+)\s*集', text)
    if match:
        return str(int(match[1]))
    match = re.fullmatch(r'第([零〇一二两三四五六七八九十百]+)集', text)
    if not match:
        return None
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total = number = 0
    for c in match[1]:
        if c in '十百':
            total += (number or 1) * (10 if c == '十' else 100)
            number = 0
        else:
            number = digits[c]
    return str(total + number)


@lru_cache(maxsize=32)
def source_index(file_signature):
    try:
        text = Path(file_signature[0]).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return (), {}
    paragraphs, index, episode = [], {}, None
    for line, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        header = episode_number(raw)
        if header is not None:
            episode = header
            continue
        ts = tokens(raw)
        if not ts:
            continue
        pos = len(paragraphs)
        paragraphs.append((raw, ts, line, episode))
        for offset, token in enumerate(ts):
            index.setdefault(token[0], []).append((pos, offset))
    return paragraphs, index


@lru_cache(maxsize=32)
def load_transcript(file_signature):
    try:
        return json.loads(Path(file_signature[0]).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def script_match(root, references, episode, text, nearby_texts=()):
    refs = [r for r in references if r.get('kind') == 'script']
    empty = {'script_status': 'not_located' if refs else 'not_provided', 'script_text': '',
             'script_excerpt': '', 'script_source': None}
    query = [t[0] for t in tokens(text)]
    if not query:
        return empty
    neighbors = [[t[0] for t in tokens(value)] for value in nearby_texts if value]
    matches = []
    for ref in refs:
        relative = ref.get('text_file')
        path = safe_path(root, relative) if relative else None
        if path is None:
            continue
        paragraphs, index = source_index(signature(path))
        for pos, offset in index.get(query[0], []):
            raw, ts, line, ep = paragraphs[pos]
            if ep is not None and ep != str(int(episode)):
                continue
            if [t[0] for t in ts[offset:offset + len(query)]] != query:
                continue
            start, end = ts[offset][1], ts[offset + len(query) - 1][2]
            # Keep the source's punctuation, including interrupted-speech dashes.
            while end < len(raw) and not raw[end].isspace() and not TOKEN.match(raw, end):
                end += 1
            excerpt = raw[start:end]
            exact_punctuation = excerpt.casefold().replace('’', "'") == text.strip().casefold().replace('’', "'")
            paragraph_tokens = [t[0] for t in ts]
            nearby_matches = sum(bool(n) and any(paragraph_tokens[k:k + len(n)] == n for k in range(len(ts) - len(n) + 1)) for n in neighbors)
            whole_line = offset == 0 and len(query) == len(ts)
            matches.append(((whole_line, nearby_matches, exact_punctuation), raw, excerpt, {'path': str(relative).replace('\\', '/'),
                            'line': line, 'reference_name': ref.get('name', ''), 'episode': ep}))
    if not matches:
        return empty
    best = max(m[0] for m in matches)
    choices = [m for m in matches if m[0] == best]
    if len(choices) != 1:
        return {**empty, 'candidate_count': len(choices)}
    _, raw, excerpt, source = choices[0]
    return {'script_status': 'matched', 'script_text': raw, 'script_excerpt': excerpt, 'script_source': source}


def annotate_entries(root, references, episodes, entries, generation_lines=None):
    grouped = {}
    low_confidence = set()
    for entry in entries:
        grouped.setdefault(entry['episode'], []).append(entry)
    nearby = {}
    for group in grouped.values():
        ordered = sorted(group, key=lambda e: e['start_ms'])
        for i, entry in enumerate(ordered):
            nearby[entry['stable_id']] = [e.get('raw_original', {}).get('text') or e['original']
                                         for j, e in enumerate(ordered[max(0, i - 2):i + 3], max(0, i - 2)) if j != i]
    for entry in entries:
        # Source baseline remains the locator even after a human edits the text.
        locator = entry.get('script_locator') or entry.get('raw_original', {}).get('text') or entry['original']
        entry.update(script_match(root, references, entry['episode'], locator, nearby[entry['stable_id']]))
        saved = entry.get('generation_source') or {}
        origin = saved.get('script_source') or {}
        relative = origin.get('path', '')
        path = safe_path(root, relative) if relative else None
        if (path and (saved.get('script_status') == 'matched'
                      or entry['script_status'] != 'matched'
                      and (generation_lines is None or saved.get('script_status') == 'context_candidate'))
                and any(str(r.get('text_file', '')).replace('\\', '/') == relative for r in references)):
            paragraphs, _ = source_index(signature(path))
            start = origin.get('line')
            end = origin.get('end_line', start)
            quoted = '\\n'.join(raw for raw, _, line, _ in paragraphs if start <= line <= end) if start else ''
            if quoted and quoted == saved.get('script_text'):
                entry.update({k: saved[k] for k in ('script_status', 'script_text', 'script_excerpt', 'script_source')})
                entry['script_source'] = dict(saved['script_source'])
                ref = next(r for r in references if str(r.get('text_file', '')).replace('\\', '/') == relative)
                entry['script_source']['reference_name'] = ref.get('name', entry['script_source'].get('reference_name', ''))
        ep = episodes.get(entry['episode'], {})
        relative = ep.get('transcript')
        path = safe_path(root, relative) if relative else None
        transcript = load_transcript(signature(path)) if path else {}
        words = [w for s in transcript.get('segments', []) for w in s.get('words', [])
                 if w.get('start') is not None and w.get('end') is not None
                 and entry['start_ms'] - 60 <= (w['start'] + w['end']) * 500 <= entry['end_ms'] + 60]
        entry['asr_text'] = ''.join(w.get('word', '') for w in words).strip()
        if len(words) == 1 and 0 < words[0].get('probability', 0) < .75:
            low_confidence.add(entry['stable_id'])
    if generation_lines:
        refs = {str(r.get('text_file', '')).replace('\\', '/'): r.get('name', '') for r in references}
        lines = {}
        for line in generation_lines:
            source = line.get('source')
            if source and source['path'] in refs:
                lines.setdefault((line['episode'], line['language'], source['path']), []).append(line)
        for episode, group in grouped.items():
            ordered = sorted((e for e in group if not e.get('deleted')), key=lambda e: e['start_ms'])
            lang = episodes.get(episode, {}).get('generation_language')
            for before, entry, after in zip(ordered, ordered[1:], ordered[2:]):
                if entry['script_status'] != 'not_located' or any(
                    neighbor['script_status'] != 'matched' for neighbor in (before, after)
                ):
                    continue
                left, right = before['script_source'], after['script_source']
                if left['path'] != right['path'] or left['line'] >= right['line']:
                    continue
                between = [line for line in lines.get((episode, lang, left['path']), [])
                           if left['line'] < line['source']['line'] < right['line']]
                if len(between) != 1:
                    continue
                line = between[0]
                heard = {word for word, _, _ in tokens(entry['asr_text'])}
                candidate = {word for word, _, _ in tokens(line['text'])}
                shared_word = any(len(word) >= 4 for word in heard & candidate)
                weak_single_word = (entry['stable_id'] in low_confidence and len(heard) == len(candidate) == 1
                                    and next(iter(heard))[0] == next(iter(candidate))[0])
                if not (shared_word or weak_single_word):
                    continue
                source = line['source']
                entry.update(script_status='context_candidate', script_text=source['text'], script_excerpt=line['text'],
                             script_source={'path': source['path'], 'line': source['line'],
                                            'reference_name': refs[source['path']], 'episode': episode})
    return entries


def deleted_prefixes(project):
    from .core import digest

    if project.data.get('mode') != 'generation':
        return {}
    episodes = {}
    for cue in project.data['cues']:
        episodes.setdefault(cue['episode'], []).append(cue)
    result = {}
    for group in episodes.values():
        ordered = sorted(group, key=lambda cue: (cue['original']['start_ms'], cue['id']))
        for previous, cue in zip(ordered, ordered[1:]):
            fragment, row = previous['original'], cue['accepted']
            if (not previous['accepted']['deleted'] or previous.get('merged_into') or fragment['deleted']
                    or row['deleted'] or not 0 < fragment['end_ms'] - fragment['start_ms'] <= 700
                    or not 0 <= row['start_ms'] - fragment['end_ms'] <= 350
                    or re.search(r'[.!?]["\']?$', fragment['text'].strip())):
                continue
            source = cue.get('generation_source') or {}
            if source.get('script_status') != 'matched':
                continue
            whole = source['script_text']
            full_words = [w[0] for w in tokens(whole)]
            tail = [w[0] for w in tokens(row['text'])]
            if len(tail) >= 2 and 0 < len(full_words) - len(tail) <= 3 and full_words[-len(tail):] == tail:
                fingerprint = digest([previous['id'], previous.get('confirmed_at'), row])
                if cue.get('prefix_reviewed') != fingerprint:
                    result[cue['id']] = {'text': whole, 'start_ms': fragment['start_ms'], 'fingerprint': fingerprint}
    return result


def comparison_markdown(entries):
    lines = ['# 字幕与原剧本逐条对照', '',
             '原剧本为资料原文引用；机器识别仅作参考，文字匹配不表示实际声音已经核验。',
             '未定位到不等于剧本无台词。完整台词可能跨多条字幕，请结合本条对应片段查看。', '']
    for entry in entries:
        lines += [f"## 第{entry['episode']}集 第{entry['original_number']}条", '',
                  f"原片时间：{entry['start_ms'] / 1000:.3f}–{entry['end_ms'] / 1000:.3f} 秒", '',
                  '当前字幕：', '', entry['original'], '', '原剧本完整台词：', '',
                  entry.get('script_text') or ('未提供剧本。' if entry.get('script_status') == 'not_provided' else '未唯一定位到原台词，需人工核对。'), '',
                  '上下文候选（待试听）：' if entry['script_status'] == 'context_candidate' else '本条对应的原文片段：',
                  '', entry.get('script_excerpt') or '未定位', '',
                  '机器识别（非人工听证）：', '', entry.get('asr_text') or '本时间范围没有可展示的词级识别。', '']
        if coverage := entry.get('script_coverage'):
            missing = '、'.join(str(index + 1) for index in coverage['missing'])
            lines += ['文稿部分匹配，待试听核对：' + coverage['text'], '', '未匹配词序号：' + missing, '']
        source = entry.get('script_source')
        if source:
            first = source['line']
            last = source.get('end_line', first)
            line_label = f'{first}–{last}' if last > first else str(first)
            lines += [f"剧本出处：{source['reference_name']}，提取文本第 {line_label} 行（{source['path']}）", '']
        if entry.get('human_note'):
            lines += ['听改备注：' + entry['human_note'], '']
        lines += ['稳定 ID：' + entry['stable_id'], '']
    return '\n'.join(lines)
