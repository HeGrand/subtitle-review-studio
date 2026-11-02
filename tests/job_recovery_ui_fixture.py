import sys
from pathlib import Path
from app.core import Project
from app.server import Handler, STATE, ThreadingHTTPServer

root = Path(sys.argv[1])
(root/'project').mkdir()
srt = root/'01.srt'
srt.write_text('1\n00:00:00,100 --> 00:00:01,000\nHello.\n', encoding='utf-8')
p = Project.create(root/'project', '失败恢复验收')
p.import_pairs([{'episode':ep, 'srt':str(srt)} for ep in ('01','02')])
p.edit([p.data['cues'][0]['id']], 'approve')
p.data['cues'][0]['note'] = '保留人工进度'
for ep in ('01','02'):
    p.data['jobs'][ep] = {'status':'failed', 'message':'Legacy error: disk full 123'}
p.close()
STATE.recent_path = root/'recent.json'
server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
STATE.port = server.server_address[1]
print('http://127.0.0.1:'+str(STATE.port)+'/', flush=True)
server.serve_forever()
