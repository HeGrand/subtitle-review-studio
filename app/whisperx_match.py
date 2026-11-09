"""Optional local English script alignment using the tested WhisperX backend."""
import argparse
import importlib.metadata
import math
import os
import re
import statistics
import sys
import wave
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from .core import APP_ROOT, UserError, atomic_json, read_json
from .generation import cue_layout, strip_opening_ellipsis

WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
CHECKPOINT = 'wav2vec2_fairseq_base_ls960_asr_ls960.pth'
PORTABLE = APP_ROOT / '.runtime' / 'whisperx'


def checkpoint_dir():
    if (PORTABLE / 'checkpoints' / CHECKPOINT).is_file():
        return PORTABLE / 'checkpoints'
    torch_home = Path(os.environ.get('TORCH_HOME') or Path.home() / '.cache' / 'torch')
    return torch_home / 'hub' / 'checkpoints'


def available(model_path):
    if model_path and not (Path(model_path) / 'model.bin').is_file():
        return False
    try:
        if (PORTABLE / 'deps').is_dir():
            version = next((d.version for d in importlib.metadata.distributions(name='whisperx', path=[str(PORTABLE / 'deps')])), '')
        else:
            version = importlib.metadata.version('whisperx')
        return version == '3.2.0' and (checkpoint_dir() / CHECKPOINT).is_file()
    except importlib.metadata.PackageNotFoundError:
        return False


def lexical(text):
    return tuple(WORD.findall(text.casefold().replace('’', "'")))


def keys(text):
    return ''.join(c for c in text.casefold() if c.isalnum())


def linked_words(lines, words):
    script = [(i, c) for i, text in enumerate(lines) for c in keys(text)]
    heard = [(i, c) for i, word in enumerate(words) for c in keys(word['word'])]
    hits = defaultdict(list)
    matcher = SequenceMatcher(None, [c for _, c in heard], [c for _, c in script], autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for offset in range(size):
            hits[script[b + offset][0]].append(heard[a + offset][0])
    return hits


def speech_chunks(regions, chunk_size, **_):
    chunks = []
    for region in regions:
        start, end = region['start'] / 16000, region['end'] / 16000
        if chunks and end - chunks[-1]['start'] <= chunk_size:
            chunks[-1]['end'] = end
        else:
            chunks.append(dict(start=start, end=end))
    return chunks


def timed_words(words):
    return [w for w in words if keys(w['word']) and 'start' in w and 'end' in w
            and math.isfinite(w['start']) and math.isfinite(w['end']) and w['end'] > w['start']]


def select_alignment(text, rough, align, duration, previous_start, primary):
    if not rough or any(w['end'] - w['start'] > 1.2 for w in rough):
        return []
    start, end = rough[0]['start'], rough[-1]['end']
    candidates = []
    for pad in (.32, .16):
        lo, hi = max(0, start - pad), min(duration, end + .42)
        if hi <= lo:
            continue
        candidate = timed_words(align(text, lo, hi))
        if not candidate or lexical(' '.join(w['word'] for w in candidate)) != lexical(text):
            continue
        if any('score' not in w or w['end'] - w['start'] > 1.2 for w in candidate):
            continue
        first, last = candidate[0], candidate[-1]
        if (first['start'] < max(lo + .025, start - .30) or round(first['start'] * 1000) <= previous_start
                or min(first['score'], last['score']) < .25 or statistics.median(w['score'] for w in candidate) < .4):
            continue
        candidates.append(candidate)
    if candidates and (len(candidates) == 1 or abs(candidates[0][0]['start'] - candidates[1][0]['start']) <= .09):
        return candidates[0]
    if (primary and lexical(' '.join(w['word'] for w in rough)) == lexical(text)
            and round(start * 1000) > previous_start
            and all(w['end'] - w['start'] >= .04 and w.get('score', 0) >= .35 for w in rough)):
        return rough
    return []


def make_rows(text, aligned, line, evidence, width, max_seconds, duration_ms):
    # Slice the original spelling and punctuation at acoustically aligned word boundaries.
    matches = list(WORD.finditer(text))
    starts = [0]
    token = 0
    for word in aligned[:-1]:
        token += len(lexical(word['word']))
        starts.append(matches[token].start())
    starts.append(len(text))
    spans = []
    first = 0
    quote_open = False
    for i, word in enumerate(aligned):
        proposed = text[starts[first]:starts[i + 1]]
        full, wrapped, _ = cue_layout(proposed, quote_open, width, 'en')
        if i > first and (round(word['start'] * 1000) - round(aligned[i - 1]['end'] * 1000) >= 500
                          or len(full) > 44 or len(wrapped) > 2
                          or word['end'] - aligned[first]['start'] > max_seconds):
            spans.append((first, i))
            _, _, quote_open = cue_layout(text[starts[first]:starts[i]], quote_open, width, 'en')
            first = i
    spans.append((first, len(aligned)))
    result = []
    quote_open = False
    source = line.get('source') or {}
    for first, stop in spans:
        excerpt, wrapped, quote_open = cue_layout(text[starts[first]:starts[stop]], quote_open, width, 'en')
        start = round(aligned[first]['start'] * 1000)
        end = min(duration_ms, round(aligned[stop - 1]['end'] * 1000) + 120)
        if end <= start or len(excerpt) > 44 or len(wrapped) > 2 or start < 0:
            return []
        provenance = {'script_status': 'matched', 'script_text': source.get('text', line['text']),
                      'script_excerpt': excerpt, 'asr_text': evidence}
        if source.get('path'):
            provenance['script_source'] = {'path': source['path'], 'line': source['line'],
                                           'end_line': source.get('end_line', source['line']),
                                           'reference_name': Path(source['path']).name, 'episode': line['episode']}
        result.append(dict(text='\n'.join(wrapped), start_ms=start, end_ms=end, deleted=False,
                           reasons=[], generation_source=provenance))
    return result


def match_lines(lines, primary, alternate, align, duration, settings):
    texts = [strip_opening_ellipsis(line['text']) for line in lines]
    width = int(settings.get('max_line_chars_en', 42))
    max_seconds = float(settings.get('max_cue_seconds', 6))
    if not 8 <= width <= 80 or not 1 <= max_seconds <= 12:
        raise UserError('字幕长度或时长设置超出范围。')
    sources = [(primary, linked_words(texts, primary), True), (alternate, linked_words(texts, alternate), False)]
    rows, missing = [], []
    duration_ms = round(duration * 1000)
    for i, (text, line) in enumerate(zip(texts, lines)):
        best_evidence = (0, [])
        for words, links, is_primary in sources:
            ids = links[i]
            rough = words[ids[0]:ids[-1] + 1] if ids else []
            if len(ids) > best_evidence[0]:
                best_evidence = (len(ids), rough)
            if len(ids) < .8 * max(1, len(keys(text))):
                continue
            aligned = select_alignment(text, rough, align, duration, rows[-1]['end_ms'] - 121 if rows else -1, is_primary)
            if not aligned:
                continue
            formatted = make_rows(text, aligned, line, ' '.join(w['word'].strip() for w in rough),
                                  width, max_seconds, duration_ms)
            if formatted:
                rows.extend(formatted)
                break
        else:
            _, rough = best_evidence
            missing.append((text, rough, len(rows)))

    for a, b in zip(rows, rows[1:]):
        a['end_ms'] = min(a['end_ms'], b['start_ms'])
    issues = []
    for text, rough, index in missing:
        left = rows[index - 1]['end_ms'] if index else 0
        right = rows[index]['start_ms'] if index < len(rows) else duration_ms
        if rough:
            start = max(0, round(rough[0]['start'] * 1000))
            end = min(duration_ms, round(rough[-1]['end'] * 1000))
            estimated = False
        else:
            start, end = min(left, right), max(left, right)
            estimated = True
        if end <= start:
            start, end = max(0, start - 500), min(duration_ms, start + 500)
        if end > start:
            issues.append(dict(start_ms=start, end_ms=end, script_text=text,
                               evidence_text=' '.join(w['word'].strip() for w in rough), estimated=estimated))
    return rows, issues


def run(args):
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    if (PORTABLE / 'deps').is_dir():
        sys.path.insert(0, str(PORTABLE / 'deps'))
    from .acceleration import prepare_gpu_libraries
    prepare_gpu_libraries()
    import numpy as np
    import torch
    import whisperx
    from faster_whisper.utils import download_model
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    from whisperx import asr as whisperx_asr

    def vad(item):
        options = VadOptions(threshold=.25, min_speech_duration_ms=80, min_silence_duration_ms=150,
                             speech_pad_ms=180, max_speech_duration_s=28)
        return get_speech_timestamps(item['waveform'].squeeze(0).numpy(), options)

    torch.set_num_threads(4)
    with wave.open(args.audio, 'rb') as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
            raise UserError('WhisperX 需要应用提取的 16 kHz 单声道音频。')
        audio = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').astype(np.float32) / 32768
    duration = len(audio) / 16000
    path = args.model or download_model(args.download_model, cache_dir=args.cache)
    # WhisperX 3.2 expects a Pyannote frontend; adapt the existing bundled Silero VAD instead.
    whisperx_asr.merge_chunks = speech_chunks
    asr = whisperx.load_model(path, args.device, compute_type='float16' if args.device == 'cuda' else 'int8',
                              language='en', vad_model=vad, asr_options={'multilingual': False, 'hotwords': None})
    heard = asr.transcribe(audio, batch_size=4, language='en')
    model, metadata = whisperx.load_align_model('en', 'cpu', model_dir=str(checkpoint_dir()))
    primary = timed_words(whisperx.align(heard['segments'], model, metadata, audio, 'cpu')['word_segments'])

    # A second decode without VAD preserves cues that the batched pass skipped.
    segments, _ = asr.model.transcribe(args.audio, language='en', beam_size=5, word_timestamps=True,
                                      condition_on_previous_text=False, vad_filter=False)
    alternate = [dict(word=w.word, start=w.start, end=w.end, score=w.probability)
                 for segment in segments for w in segment.words or ()]
    del asr

    def align(text, start, end):
        return whisperx.align([dict(text=text, start=start, end=end)], model, metadata, audio, 'cpu')['word_segments']

    plan = read_json(args.plan)
    rows, issues = match_lines(plan['lines'], primary, timed_words(alternate), align, duration, plan['settings'])
    if not rows:
        raise UserError('WhisperX 没有可靠对齐本集文稿，请检查集数、语言或素材。')
    # Retain the independent recognition for review. Raise unclaimed, confident
    # phrases outside the aligned and pending spans as separate listening items.
    transcript = {'duration': duration, 'execution': {'device': args.device}, 'segments': [
        {'words': [dict(word=w['word'], start=w['start'], end=w['end'], probability=w.get('score', 0))
                   for w in primary]}]}
    spans = [(r['start_ms'] - 150, r['end_ms'] + 150) for r in rows + issues]
    extras, fragment = [], []
    for word in primary:
        center = (word['start'] + word['end']) * 500
        claimed = any(start <= center <= end for start, end in spans)
        if claimed or (fragment and word['start'] - fragment[-1]['end'] >= .5):
            if len(fragment) >= 2 or any(w.get('score', 0) >= .85 for w in fragment):
                extras.append(fragment)
            fragment = []
        if not claimed and word.get('score', 0) >= .65:
            fragment.append(dict(text=word['word'].strip(), start=word['start'], end=word['end'], score=word['score']))
    if len(fragment) >= 2 or any(w['score'] >= .85 for w in fragment):
        extras.append(fragment)
    atomic_json(Path(args.output), dict(rows=rows, issues=issues, transcript=transcript, unmatched=extras))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('audio', 'model', 'download-model', 'cache', 'device', 'plan', 'output'):
        parser.add_argument('--' + name, required=True)
    run(parser.parse_args())
