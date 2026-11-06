"""Isolated UI fixture; uses deterministic ASR evidence, never paid APIs."""
import array, json, math, sys, wave
from pathlib import Path
from app import ai_config
from app.core import Project
from app.generation import install_cues
from app.processing import Processor, waveform
from app.server import Handler, STATE, ThreadingHTTPServer

root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
(root / 'project').mkdir()
(root / 'script.txt').write_text('EPISODE 32\nROSE\nNo, that’s not what I—\nEPISODE 33\nROSE\nGo away.\nEDWARD\n'
                                 "You always have an answer after you've already hurt me.\n", encoding='utf-8')
audio = root / '32.wav'
with wave.open(str(audio), 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(array.array('h', (int(1000 * math.sin(i * .1)) for i in range(4 * 16000))).tobytes())


class FixtureProcessor(Processor):
    def start(self, p, episodes, *args):
        for ep in episodes:
            lang = p.data['episodes'][ep]['generation_language']
            p.data['episodes'][ep].update(audio=str(audio), duration_ms=4000)
            heard = ("No, that's not what I... Noise." if ep == '32'
                     else "You always have an answer after you've already hurt me.").split()
            transcript = {'duration': 4, 'segments': [{'words': [{'word': ' ' + t, 'start': i * .3, 'end': (i + 1) * .3, 'probability': .99} for i, t in enumerate(heard)]}]}
            evidence = p.root / 'evidence' / ep; evidence.mkdir(exist_ok=True)
            (evidence / 'transcript.json').write_text(json.dumps(transcript), encoding='utf-8')
            p.data['episodes'][ep]['transcript'] = f'evidence/{ep}/transcript.json'
            install_cues(p, ep, transcript)
            if ep == '33' and p.data['episodes'][ep].get('generation_id'):
                first = next(c for c in p.data['cues'] if c['episode'] == ep)
                first['time_adjustments'] = [{'field': 'start_ms', 'from': 0, 'to': 80, 'applied': False}]
                first['note'] = '检测到人声起点，建议校准入点'
            waveform(audio, p.root / 'cache/waveforms' / (ep + '.json'))
            p.data['jobs'][ep] = {'status': 'done', 'progress': 100, 'message': '验收字幕已生成'}
            p.save()


STATE.processor = FixtureProcessor(STATE.mutex)
STATE.recent_path = root / 'recent.json'
ai_config.CONFIG = root / 'ai-config.json'
def fake_chat(messages, **kwargs):
    if 'findings' in messages[0]['content']:
        return json.dumps({'findings': []})
    if '仅提取' in messages[0]['content']:
        source = json.loads(messages[1]['content'])
        line = next(row for row in source if row['text'].startswith('You always'))
        return json.dumps({'lines': [{**line, 'speaker': 'EDWARD'}]})
    return json.dumps([["You always have an answer", "after you've already hurt me."]])


ai_config.chat = fake_chat
http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
STATE.port = http.server_address[1]
print(f'http://127.0.0.1:{STATE.port}/', flush=True)
http.serve_forever()
