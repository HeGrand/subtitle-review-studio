"""Restartable multilingual 30-second ASR segments, no VAD deletion assumption."""
import argparse
import os
import threading
import time
import wave
from pathlib import Path

from .core import atomic_json, read_json


def main():
    parser = argparse.ArgumentParser()
    for name in ('audio', 'output', 'model', 'cache', 'download-model'):
        parser.add_argument('--' + name, default='')
    parser.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    parser.add_argument('--language',choices=('en','zh','auto'),default='auto')
    parser.add_argument('--review-windows')
    args = parser.parse_args()
    os.environ['HF_HOME'] = str(Path(args.cache).resolve())
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'
    from .acceleration import prepare_gpu_libraries
    prepare_gpu_libraries()
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    output = Path(args.output)
    status = output.parent / 'worker-status.json'
    downloading = threading.Event()
    downloading.set()

    def report_download():
        while downloading.wait(.5):
            size = sum(p.stat().st_size for p in Path(args.cache).rglob('*') if p.is_file())
            atomic_json(status, {'status': 'preparing', 'progress': 0,
                                 'message': '正在加载已有本地模型，不下载资源' if args.model else f'准备/下载本地模型：已保存 {size / 1048576:.1f} MB；失败原因会显示在任务中'})
            time.sleep(1)

    reporter = threading.Thread(target=report_download, daemon=True)
    reporter.start()
    try:
        model_dir = args.model or download_model(args.download_model, cache_dir=args.cache)
        if args.model and not (Path(model_dir) / 'model.bin').is_file():
            raise ValueError('选定模型文件夹中没有 model.bin')
        model = WhisperModel(model_dir, device=args.device, compute_type='float16' if args.device=='cuda' else 'int8', cpu_threads=min(8, os.cpu_count() or 4),
                             num_workers=1, local_files_only=True)
    finally:
        downloading.clear()
        reporter.join(timeout=2)
    with wave.open(args.audio, 'rb') as w:
        duration = w.getnframes() / w.getframerate()
        rate = w.getframerate()
    import numpy as np
    if args.review_windows:
        results = {}
        with wave.open(args.audio, 'rb') as audio:
            for window in read_json(args.review_windows):
                start, end = window['start_ms'], window['end_ms']
                audio.setpos(round(start * rate / 1000))
                samples = np.frombuffer(audio.readframes(round((end - start) * rate / 1000)),
                                        dtype=np.int16).astype(np.float32) / 32768
                segments, _ = model.transcribe(samples, language=None if args.language == 'auto' else args.language,
                                               beam_size=5, word_timestamps=True, vad_filter=False,
                                               condition_on_previous_text=False)
                words = [word for segment in segments for word in segment.words or []]
                if words:
                    results[str(window['index'])] = {'text': ''.join(w.word for w in words).strip(),
                                                     'start_ms': start + round(words[0].start * 1000),
                                                     'end_ms': min(end, start + round(words[-1].end * 1000)),
                                                     'edge_limited': words[0].start < .06}
        atomic_json(output, results)
        return
    segments, languages = [], []
    chunks = output.parent / 'chunks'
    chunks.mkdir(exist_ok=True)
    began = time.monotonic();new_chunks=0;reused_chunks=0
    for i, start in enumerate(range(0, int(duration) + 1, 30)):
        if start >= duration:
            break
        end = min(start + 30, duration)
        chunk_file = chunks / f'{i:05}.json'
        if chunk_file.exists():
            chunk = read_json(chunk_file)
            reused_chunks+=1
        else:
            with wave.open(args.audio, 'rb') as w:
                w.setpos(start * rate)
                audio = np.frombuffer(w.readframes(round((end - start) * rate)), dtype=np.int16).astype(np.float32) / 32768
            iterator, info = model.transcribe(audio, language=None if args.language=='auto' else args.language, beam_size=5, word_timestamps=True,
                                              vad_filter=False, condition_on_previous_text=False)
            new_chunks+=1
            chunk = {'start': start, 'end': end, 'language': info.language,'device':args.device,
                     'language_probability': info.language_probability, 'segments': []}
            for s in iterator:
                chunk['segments'].append({'start': round(s.start + start, 3), 'end': round(min(end, s.end + start), 3),
                                          'text': s.text, 'language': info.language, 'avg_logprob': s.avg_logprob,
                                          'no_speech_prob': s.no_speech_prob,
                                          'words': [{'start': round(w.start + start, 3), 'end': round(w.end + start, 3),
                                                     'word': w.word, 'probability': w.probability} for w in s.words or []]})
            atomic_json(chunk_file, chunk)
        segments.extend(chunk['segments'])
        languages.append({k: chunk[k] for k in ('start', 'end', 'language', 'language_probability')})
        atomic_json(status, {'status': 'running', 'progress': round(end / duration * 85),
                             'message': f'本地多语言识别 {end:.1f} / {duration:.1f} 秒；完成分段已保存'})
    atomic_json(output, {'duration': duration, 'segments': segments, 'languages': languages,
                         'execution':{'device':args.device,'compute_type':'float16' if args.device=='cuda' else 'int8','new_chunks':new_chunks,'reused_chunks':reused_chunks},
                         'processing_seconds': round(time.monotonic() - began, 3), 'vad_filter': False,
                         'limitations': '30秒块自动识别语言；短中文、混合语言、低声与配乐仍可能漏检。识别为空不代表无对白。'})


if __name__ == '__main__':
    main()
