"""Local-only media work. Model inference runs in a killable child process."""
import array
import copy
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave
import uuid
from pathlib import Path

from .core import APP_ROOT, UserError, atomic_json, read_json, same_file, digest, now


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        path = shutil.which('ffmpeg')
        if path:
            return path
        raise UserError('音频组件未准备，请双击“准备环境.cmd”。')


def run_media(args, stop=None, process_callback=None):
    p = subprocess.Popen([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', *map(str, args)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if process_callback:
        process_callback(p)
    while True:
        try:
            out, err = p.communicate(timeout=.25)
            break
        except subprocess.TimeoutExpired:
            if stop and stop.is_set():
                p.kill()
                p.communicate()
                raise InterruptedError('任务已暂停')
    if p.returncode:
        raise UserError('音视频处理失败：' + err.decode('utf-8', 'replace')[-1500:])
    return out


def waveform(path, out):
    with wave.open(str(path), 'rb') as w:
        if w.getsampwidth() != 2:
            raise UserError('波形需要16位PCM音频')
        peaks, n = [], w.getnframes()
        step = max(1, round(w.getframerate() * .01))
        for _ in range(math.ceil(n / step)):
            samples = array.array('h', w.readframes(step))
            if sys.byteorder != 'little':
                samples.byteswap()
            peaks.append(round(max((abs(x) for x in samples), default=0) / 32768, 4))
    atomic_json(out, {'peaks': peaks, 'resolution_ms': 10, 'version': 2})
    return peaks


def normalized(text):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', ' ', text.lower()).strip()


def aligned_words(transcript, start_ms, end_ms):
    """Use word midpoints, not an intersecting ASR segment's entire sentence."""
    words, seen = [], set()
    for seg in transcript['segments']:
        for w in seg.get('words', []):
            if w.get('start') is None or w.get('end') is None:
                continue
            middle = (w['start'] + w['end']) * 500
            key = (w['start'], w['end'], w['word'])
            if start_ms - 60 <= middle <= end_ms + 60 and key not in seen:
                words.append(w)
                seen.add(key)
    return sorted(words, key=lambda w: (w['start'], w['end']))


def rule_check(project, ep, transcript):
    rows = [c for c in project.data['cues'] if c['episode'] == ep]
    issues = []
    duration = project.data['episodes'][ep].get('duration_ms')

    def issue(kind, cue, start, end, message, evidence=''):
        key = digest([ep, kind, cue['id'] if cue else None, round(start), message])[:20]
        issues.append({'id': key, 'episode': ep, 'kind': kind, 'cue_id': cue['id'] if cue else None,
                       'start_ms': round(start), 'end_ms': round(end), 'message': message,
                       'evidence_text': evidence, 'resolved': False})

    previous = None
    for c in sorted(rows, key=lambda c: c['accepted']['start_ms']):
        r = c['accepted']
        if r['deleted']:
            continue
        s, e = r['start_ms'], r['end_ms']
        if s < 0 or e <= s:
            issue('时间格式', c, s, e, '时间倒置或负时间')
        if duration and e > duration + 20:
            issue('超出片长', c, s, e, '字幕结束超出片长')
        if previous and s < previous:
            issue('重叠', c, s, e, '与前一字幕重叠')
        previous = max(previous or 0, e)
        if e - s > 7000:
            issue('挂屏', c, s, e, '字幕显示超过7秒，请核对出点')
        segments = [x for x in transcript['segments'] if x['end'] * 1000 > s and x['start'] * 1000 < e]
        words = aligned_words(transcript, s, e)
        recognized = ' '.join(w['word'].strip() for w in words) if words else ' '.join(x['text'] for x in segments)
        comparable = bool(words) or all(sum(not other['accepted']['deleted'] and other['accepted']['start_ms'] < seg['end']*1000 and other['accepted']['end_ms'] > seg['start']*1000 for other in rows) <= 1 for seg in segments)
        if not segments:
            issue('识别不确定', c, s, e, '机器未识别到对应对白；不可据此删除字幕')
        elif comparable and difflib.SequenceMatcher(None, normalized(r['text']), normalized(recognized)).ratio() < .52:
            issue('文字差异', c, s, e, '本地识别与字幕差异较大，待AI/人工核对', recognized)
        if words and difflib.SequenceMatcher(None, normalized(r['text']), normalized(recognized)).ratio() > .6 and (abs(min(w['start'] for w in words) * 1000 - s) > 800 or abs(max(w['end'] for w in words) * 1000 - e) > 1200):
            issue('入出点疑点', c, s, e, '识别词边界与字幕边界不一致；只作试听提示', recognized)
    for seg in transcript['segments']:
        s, e = seg['start'] * 1000, seg['end'] * 1000
        matched = [c for c in rows if not c['accepted']['deleted'] and c['accepted']['start_ms'] < e and c['accepted']['end_ms'] > s]
        cue = matched[0] if matched else None
        if re.search(r'[\u4e00-\u9fff]', seg['text']) or seg.get('language') in ('zh', 'yue'):
            issue('中文或疑似中文', cue, s, e, '检测到中文/疑似中文，请按原片时间人工试听，不自动翻译或删除', seg['text'])
        if not matched and seg['text'].strip():
            issue('可疑漏句', None, s, e, '识别片段没有对应字幕，不自动补写喊叫、咒语或歌词', seg['text'])
        if seg.get('avg_logprob', 0) < -1 or seg.get('no_speech_prob', 0) > .55:
            issue('识别不稳定', cue, s, e, '低置信识别，需人工复核', seg['text'])
    # Supplied name/term lists provide candidate spellings, never invented canonical names.
    for ref in project.data['references']:
        if ref['kind'] not in ('names', 'terms'):
            continue
        lines = (project.root / ref['text_file']).read_text(encoding='utf-8').splitlines()
        for term in (x.strip().lstrip('-* ').strip() for x in lines):
            if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{1,38}", term):
                continue
            for c in rows:
                text = c['accepted']['text']
                candidates = re.findall(r"[A-Za-z][A-Za-z']+", text)
                if term.lower() not in text.lower() and any(.77 <= difflib.SequenceMatcher(None, term.lower(), w.lower()).ratio() < 1 for w in candidates):
                    issue('人名术语疑点', c, c['accepted']['start_ms'], c['accepted']['end_ms'], f'与资料中的 {term} 拼写相近，仅供人工核对')
    return issues


class Processor:
    def __init__(self, mutex):
        self.mutex = mutex
        self.stop = threading.Event()
        self.thread = None
        self.child = None

    def active(self):
        return bool(self.thread and self.thread.is_alive())

    def pause(self):
        self.stop.set()
        if self.child and self.child.poll() is None:
            self.child.kill()
        if self.thread:
            self.thread.join(timeout=15)
        if self.active():
            raise UserError('任务尚未停止，请稍后重试；当前项目未切换。')

    def start(self, project, episodes, model_path='', download_model='small'):
        if self.active():
            raise UserError('已有本地任务正在运行。')
        if not episodes or not set(episodes) <= set(project.data['episodes']):
            raise UserError('请选择要处理的集数。')
        generating = project.data.get('mode') == 'generation'
        if generating:
            from .generation import has_generated
            episodes = [ep for ep in episodes if not has_generated(project, ep)]
            if not episodes:
                raise UserError('所选集数已生成，不会覆盖听改进度。')
        if not model_path:
            bundled = sorted((APP_ROOT / '.runtime/models').glob('*/model.bin'))
            if bundled:
                model_path = str(bundled[0].parent)
        if (APP_ROOT / 'portable.json').exists() and not model_path:
            raise UserError('便携包内的识别模型缺失，请重新完整解压便携包；未自动联网下载。')
        self.stop.clear()
        if generating:
            from .generation_jobs import run_generation
            self.thread = threading.Thread(target=run_generation, args=(self, project, episodes, model_path, download_model), daemon=True)
        else:
            self.thread = threading.Thread(target=self._run, args=(project, episodes, model_path, download_model), daemon=True)
        self.thread.start()

    def update(self, project, ep, **values):
        with self.mutex:
            job = project.data['jobs'].setdefault(ep, {})
            if all(job.get(k) == v for k, v in values.items()):
                return
            job.update(values)
            project.save()

    def execute_child(self,command,project,key,status_path=None,timeout=None):
        self.child=subprocess.Popen(command,cwd=APP_ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        began=time.monotonic()
        try:
            while self.child.poll() is None:
                if self.stop.wait(.5):
                    self.child.kill();self.child.communicate();raise InterruptedError('任务已暂停；已完成分段保留')
                if timeout and time.monotonic()-began>timeout:
                    self.child.kill();self.child.communicate();raise UserError('设备测速超时')
                if status_path and status_path.exists():
                    try:self.update(project,key,**read_json(status_path))
                    except (ValueError,OSError):pass
            _,error=self.child.communicate()
            if self.stop.is_set():raise InterruptedError('任务已暂停')
            if self.child.returncode:raise UserError('识别进程未完成：'+error.decode('utf-8','replace')[-1800:])
        finally:self.child=None

    def automatic_backend(self,project,key,model_path,download_model,audio):
        from .acceleration import choose_backend
        rows=[c['accepted']['start_ms']/1000 for c in project.data['cues'] if c['episode']==key and not c['accepted']['deleted']]
        start=max(0,min(rows,default=0)-.5)
        def probe(device):
            result=project.root/'cache/tmp'/('speed-'+uuid.uuid4().hex+'.json')
            command=[sys.executable,'-m','app.acceleration','--device',device,'--model',model_path,'--download-model',download_model,'--audio',str(audio),'--start',str(start),'--cache',str(project.root/'cache/models'),'--result',str(result)]
            try:
                self.execute_child(command,project,key,timeout=180)
                return read_json(result)
            finally:result.unlink(missing_ok=True)
        return choose_backend(model_path,download_model,probe,lambda message:self.update(project,key,status='preparing',message=message))

    def _run(self, project, episodes, model_path, download_model):
        for key in episodes:
            if self.stop.is_set():
                break
            started = time.monotonic()
            ep = project.data['episodes'][key]
            try:
                self.update(project, key, status='running', progress=0, message='核对素材并提取音频', started_at=now())
                evidence = project.root / 'evidence' / key
                evidence.mkdir(exist_ok=True, parents=True)
                audio = evidence / 'audio.wav'
                if not audio.exists():
                    source = Path(ep['media'])
                    if not source.is_file():
                        raise UserError('素材不可访问，请重新定位；已有证据保留。')
                    if ep.get('media_fingerprint') and not same_file(source, ep['media_fingerprint']):
                        raise UserError('源素材指纹变化，请新建版本，未复用旧审核结果。')
                    temp = project.root / 'cache/tmp' / (key + '.wav')
                    run_media(['-y', '-i', source, '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', temp], self.stop)
                    os.replace(temp, audio)
                with wave.open(str(audio), 'rb') as w:
                    duration = w.getnframes() / w.getframerate()
                with self.mutex:
                    ep.update(audio=str(audio.relative_to(project.root)), duration_ms=round(duration * 1000))
                    project.save()
                wavefile = project.root / 'cache/waveforms' / (key + '.json')
                if not wavefile.exists():
                    waveform(audio, wavefile)
                transcript_path = evidence / 'transcript.json'
                if not transcript_path.exists():
                    backend=self.automatic_backend(project,key,model_path,download_model,audio)
                    self.update(project,key,device=backend['device'],acceleration_message=backend['message'])
                    command = [sys.executable, '-m', 'app.worker', '--audio', str(audio), '--output', str(transcript_path),
                               '--model', model_path, '--download-model', download_model,
                               '--cache', str(project.root / 'cache/models'),'--device',backend['device']]
                    status_path = evidence / 'worker-status.json'
                    try:self.execute_child(command,project,key,status_path=status_path)
                    except UserError as error:
                        if backend['device']!='cuda':raise
                        from .acceleration import record_fallback
                        backend=record_fallback(backend,error)
                        self.update(project,key,device='cpu',message=backend['message'],acceleration_message=backend['message'])
                        command[-1]='cpu'
                        self.execute_child(command,project,key,status_path=status_path)
                if self.stop.is_set():
                    raise InterruptedError('任务已暂停')
                self.update(project, key, status='running', progress=90, message='规则初查与疑点证据截取')
                transcript = read_json(transcript_path)
                with self.mutex:
                    issues = rule_check(project, key, transcript)
                for n, issue in enumerate(issues):
                    if self.stop.is_set():
                        raise InterruptedError('任务已暂停')
                    s = max(0, issue['start_ms'] / 1000 - 1.2)
                    end = min(duration, max(s + .1, issue['end_ms'] / 1000 + 1.2))
                    clip = evidence / 'clips' / (issue['id'] + '.wav')
                    clip.parent.mkdir(exist_ok=True)
                    if not clip.exists():
                        temp = project.root / 'cache/tmp' / (issue['id'] + '.wav')
                        run_media(['-y', '-ss', s, '-i', audio, '-t', end - s, '-c:a', 'pcm_s16le', temp], self.stop)
                        os.replace(temp, clip)
                    issue.update(clip=str(clip.relative_to(project.root)), clip_start_ms=round(s * 1000))
                with self.mutex:
                    old = {i['id']: i for i in project.data['issues'] if i['episode'] == key}
                    for item in issues:
                        if item['id'] in old:
                            for field in ('resolved','triage','resolution','resolved_at','excluded_by_user','scope_decision'):
                                if field in old[item['id']]:item[field]=copy.deepcopy(old[item['id']][field])
                    project.data['issues'] = [i for i in project.data['issues'] if i['episode'] != key] + issues
                    # Preserve human confirmations, surface new findings as separate evidence.
                    ep.update(transcript=str(transcript_path.relative_to(project.root)), local_status='本地初查完成 / 等待外部AI意见')
                    elapsed = round(time.monotonic() - started, 2)
                    project.data['jobs'][key].update(status='done', progress=100, message=f'本地初查完成，{len(issues)}项疑点；未完成AI语义审查',
                                                     seconds=elapsed, audio_seconds=round(duration, 3), realtime_factor=round(elapsed / duration, 3))
                    project.commit('本地处理完成', extra={'episode': key, 'issues': len(issues)})
            except InterruptedError as exc:
                self.update(project, key, status='paused', message=str(exc))
                break
            except Exception as exc:
                from .job_errors import explain_error
                error = explain_error(exc)
                self.update(project, key, status='failed', message=error['title'], error=error,
                            seconds=round(time.monotonic() - started, 2))
            finally:
                self.child = None
