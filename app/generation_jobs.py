"""Restartable generation queue sharing the existing worker and pause controls."""
import copy
import os
import sys
import time
import wave

from .core import UserError, atomic_json, now, read_json, same_file
from .generation import build_cues, install_cues
from .processing import run_media, waveform


def retry_windows(rows, duration_ms):
    windows = []
    for i, row in enumerate(rows):
        previous_end = rows[i - 1]['end_ms'] if i else 0
        next_start = rows[i + 1]['start_ms'] if i + 1 < len(rows) else duration_ms
        start, end = row['start_ms'], row['end_ms']
        source = row['generation_source'] or {}
        short_fragment = (end - start < 700 and 0 <= next_start - end <= 120
                          and any('未能确定对应剧本' in reason for reason in row['reasons']))
        if source.get('script_status') != 'context_candidate' and not short_fragment:
            continue
        lo, hi = max(previous_end, start - 450), min(next_start, end + 200)
        if hi - lo >= 300:
            windows.append({'index': i, 'start_ms': lo, 'end_ms': hi})
    return windows


def run_whisperx(processor, project, key, audio, evidence, lines, settings, model_path, download_model):
    backend = processor.automatic_backend(project, key, model_path, download_model, audio)
    plan = project.root / 'cache/tmp' / (key + '-whisperx-plan.json')
    result = project.root / 'cache/tmp' / (key + '-whisperx-result.json')
    transcript_path = evidence / 'transcript-whisperx.json'
    atomic_json(plan, {'lines': lines, 'settings': settings})
    try:
        # The optional system-Python stack has its own dependencies.
        command = [sys.executable, '-E', '-X', 'utf8', '-m', 'app.whisperx_match', '--audio', str(audio), '--model', model_path,
                   '--download-model', download_model, '--cache', str(project.root / 'cache/models'),
                   '--plan', str(plan), '--output', str(result), '--device', backend['device']]
        processor.update(project, key, status='running', progress=20, device=backend['device'],
                         message='WhisperX 听写与文稿词级对齐')
        try:
            processor.execute_child(command, project, key)
        except UserError as exc:
            from .acceleration import record_fallback
            if backend['device'] != 'cuda' or not any(x in str(exc).lower() for x in ('cuda', 'cublas', 'cudnn', 'out of memory')):
                raise
            record_fallback(backend, exc)
            command[-1] = 'cpu'
            processor.update(project, key, device='cpu', message='WhisperX GPU 未完成，使用 CPU 继续')
            processor.execute_child(command, project, key)
        data = read_json(result)
        with processor.mutex:
            if processor.stop.is_set():
                raise InterruptedError('已暂停')
            before = copy.deepcopy(project.data)
            try:
                atomic_json(transcript_path, data['transcript'])
                project.data['episodes'][key]['transcript'] = str(transcript_path.relative_to(project.root))
                return install_cues(project, key, data['transcript'], prepared_rows=data['rows'],
                                    unmatched=data['unmatched'], unmatched_script=data['issues'], local_alignment=True)
            except Exception:
                project.data = before
                project.save()
                raise
    finally:
        plan.unlink(missing_ok=True)
        result.unlink(missing_ok=True)


def run_generation(processor, project, episodes, model_path, download_model):
    for key in episodes:
        if processor.stop.is_set():
            break
        began = time.monotonic()
        ep = project.data['episodes'][key]
        try:
            processor.update(project, key, status='running', progress=0, message='提取音轨，准备生成字幕', started_at=now())
            evidence = project.root / 'evidence' / key
            evidence.mkdir(parents=True, exist_ok=True)
            if not same_file(ep['media'], ep['media_fingerprint']):
                raise UserError('视频指纹已变化，请新建项目处理新版本。')
            audio = evidence / 'audio.wav'
            if not audio.exists():
                temp = project.root / 'cache/tmp' / (key + '.wav')
                run_media(['-y', '-i', ep['media'], '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', temp], processor.stop)
                os.replace(temp, audio)
            with wave.open(str(audio)) as handle:
                duration = handle.getnframes() / handle.getframerate()
            with processor.mutex:
                ep.update(audio=str(audio.relative_to(project.root)), duration_ms=round(duration * 1000))
                project.save()
            wavefile = project.root / 'cache/waveforms' / (key + '.json')
            if not wavefile.exists():
                waveform(audio, wavefile)
            generation = project.data.get('generation', {})
            lines = [line for line in generation.get('lines', [])
                     if line['episode'] == key and line['language'] == ep['generation_language']]
            enhanced = ep['generation_language'] == 'en' and bool(lines)
            if enhanced:
                from .whisperx_match import available
                if available(model_path):
                    result = run_whisperx(processor, project, key, audio, evidence, lines,
                                          generation.get('settings', {}), model_path, download_model)
                    processor.update(project, key, status='done', progress=100,
                                     seconds=round(time.monotonic() - began, 2), audio_seconds=round(duration, 3),
                                     message=f"WhisperX 已生成 {result['count']} 条，{result['unmatched_count']} 段待试听")
                    continue
            transcript_path = evidence / 'transcript.json'
            if not transcript_path.exists():
                backend = processor.automatic_backend(project, key, model_path, download_model, audio)
                processor.update(project, key, device=backend['device'], status='running', message='识别实说台词')
                command = [sys.executable, '-m', 'app.worker', '--audio', str(audio), '--output', str(transcript_path),
                           '--model', model_path, '--download-model', download_model, '--cache', str(project.root / 'cache/models'),
                           '--language', ep['generation_language'], '--device', backend['device']]
                try:
                    processor.execute_child(command, project, key, status_path=evidence / 'worker-status.json')
                except UserError as exc:
                    if backend['device'] != 'cuda':
                        raise
                    from .acceleration import record_fallback
                    record_fallback(backend, exc)
                    command[-1] = 'cpu'
                    processor.update(project, key, device='cpu', message='GPU未完成，使用CPU继续已保存分段')
                    processor.execute_child(command, project, key, status_path=evidence / 'worker-status.json')
            if processor.stop.is_set():
                raise InterruptedError('已暂停，识别分段保留')
            transcript = read_json(transcript_path)
            unmatched = []
            rows = build_cues(transcript, lines, ep['generation_language'],
                              {**generation.get('settings', {}), 'script_only': True}, unmatched)
            windows = retry_windows(rows, round(duration * 1000))
            retry_failed = False
            if windows:
                processor.update(project, key, progress=88, message='本地二次识别疑点片段')
                plan = evidence / 'retry-windows.json'
                output = evidence / 'retry-result.json'
                atomic_json(plan, windows)
                device = transcript.get('execution', {}).get('device', 'cpu')
                command = [sys.executable, '-m', 'app.worker', '--audio', str(audio), '--output', str(output),
                           '--model', model_path, '--download-model', download_model, '--cache', str(project.root / 'cache/models'),
                           '--language', ep['generation_language'], '--device', device, '--review-windows', str(plan)]
                try:
                    processor.execute_child(command, project, key, timeout=180)
                    for index, result in read_json(output).items():
                        rows[int(index)]['local_retry'] = result
                except (UserError, OSError, ValueError):
                    retry_failed = True
            processor.update(project, key, progress=92, message='匹配剧本、断句并校准入点')
            from .onset import speech_regions
            try:
                regions = speech_regions(audio)
            except Exception:
                regions = None
            with processor.mutex:
                if processor.stop.is_set():
                    raise InterruptedError('已暂停')
                ep['transcript'] = str(transcript_path.relative_to(project.root))
                before = copy.deepcopy(project.data)
                try:
                    result = install_cues(project, key, transcript, regions or (), prepared_rows=rows, unmatched=unmatched)
                except Exception:
                    project.data = before
                    project.save()
                    raise
                elapsed = round(time.monotonic() - began, 2)
                project.data['jobs'][key].update(status='done', progress=100, seconds=elapsed,
                    audio_seconds=round(duration, 3), message=f"已生成 {result['count']} 条，{result['flagged']} 条建议优先听改"
                    + (' · WhisperX 环境不可用，使用原匹配' if enhanced else '')
                    + (' · 人声检测未完成，请试听入点' if regions is None else '')
                    + (' · 二次识别未完成，请听改' if retry_failed else ''))
                project.save()
        except InterruptedError as exc:
            processor.update(project, key, status='paused', message=str(exc))
            break
        except Exception as exc:
            from .job_errors import explain_error
            error = explain_error(exc)
            processor.update(project, key, status='failed', message=error['title'], error=error)
        finally:
            processor.child = None
