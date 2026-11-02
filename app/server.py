import argparse
import base64
import copy
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import APP_ROOT, Project, UserError, atomic_json, read_json, scan_pairs, inside
from .processing import Processor, waveform
from .exchange import export_srt, export_ai, import_opinions, backup
from .acceleration import public_status

PICKER_LOCK = threading.Lock()


def system_picker(kind='folder'):
    if os.name != 'nt':
        raise UserError('系统选择器仅支持 Windows；也可以在路径框填入完整路径。')
    if kind == 'folder':
        body = """
$d=New-Object System.Windows.Forms.Form; $d.Text='选择文件夹（可直接粘贴路径）';
$d.Size=New-Object System.Drawing.Size(760,205); $d.StartPosition='CenterScreen'; $d.TopMost=$true;
$label=New-Object System.Windows.Forms.Label; $label.Text='文件夹完整路径（支持共享盘路径）：'; $label.SetBounds(18,15,700,24);
$pathBox=New-Object System.Windows.Forms.TextBox; $pathBox.Name='FolderPath'; $pathBox.SetBounds(18,44,590,30);
$browse=New-Object System.Windows.Forms.Button; $browse.Text='浏览…'; $browse.SetBounds(620,42,105,32);
$errorLabel=New-Object System.Windows.Forms.Label; $errorLabel.ForeColor=[System.Drawing.Color]::Firebrick; $errorLabel.SetBounds(18,80,700,28);
$ok=New-Object System.Windows.Forms.Button; $ok.Text='确定'; $ok.SetBounds(500,116,105,32);
$cancel=New-Object System.Windows.Forms.Button; $cancel.Text='取消'; $cancel.SetBounds(620,116,105,32); $cancel.DialogResult='Cancel';
$browse.Add_Click({$browser=New-Object System.Windows.Forms.FolderBrowserDialog; try{if(Test-Path -LiteralPath $pathBox.Text -PathType Container){$browser.SelectedPath=$pathBox.Text}; if($browser.ShowDialog($d) -eq 'OK'){$pathBox.Text=$browser.SelectedPath}}finally{$browser.Dispose()}});
$ok.Add_Click({$candidate=$pathBox.Text.Trim().Trim([char]34); if([System.IO.Path]::IsPathRooted($candidate) -and (Test-Path -LiteralPath $candidate -PathType Container)){$d.Tag=[System.IO.Path]::GetFullPath($candidate);$d.DialogResult='OK'}else{$errorLabel.Text='请输入存在的文件夹完整路径，或点击浏览。';$pathBox.Focus()}});
$d.Controls.AddRange(@($label,$pathBox,$browse,$errorLabel,$ok,$cancel)); $d.AcceptButton=$ok; $d.CancelButton=$cancel;
$d.Add_Shown({$pathBox.Focus()});
"""
        output = '$d.Tag'
    else:
        filters = {'media': '音视频|*.mp4;*.mkv;*.mov;*.wav;*.mp3;*.m4a;*.flac;*.avi;*.webm|所有文件|*.*',
                   'srt': '字幕|*.srt', 'json': 'JSON意见或进度|*.json', 'reference': '剧本与资料|*.docx;*.txt;*.md;*.pdf'}
        body = "$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Filter='" + filters.get(kind, '所有文件|*.*') + "'"
        output = '$d.FileName'
    # A real topmost owner keeps the native modal dialog above the browser.
    # Hidden console processes have no owner and Windows may otherwise open them behind it.
    script = """$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8;
Add-Type -AssemblyName System.Windows.Forms;
$owner=New-Object System.Windows.Forms.Form;
$owner.Text='字幕工作室 · 选择文件'; $owner.TopMost=$true; $owner.ShowInTaskbar=$false;
$owner.StartPosition='CenterScreen'; $owner.Size=New-Object System.Drawing.Size(1,1);
$owner.Opacity=0; $owner.Show(); $owner.Activate();
try { """ + body + "; if($d.ShowDialog($owner) -eq 'OK'){[Console]::Write(" + output + ")} } finally { if($d){$d.Dispose()}; $owner.Close(); $owner.Dispose() }"
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    if not PICKER_LOCK.acquire(blocking=False):
        raise UserError('已有一个选择窗口打开，请先完成或取消该窗口。')
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-EncodedCommand', encoded],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        PICKER_LOCK.release()
    if result.returncode:
        raise UserError('文件选择器无法启动，请使用完整路径输入。' + result.stderr.decode('utf-8', 'replace')[-300:])
    return result.stdout.decode('utf-8-sig').strip()


class State:
    def __init__(self):
        self.mutex = threading.RLock()
        self.processor = Processor(self.mutex)
        self.project = None
        self.token = secrets.token_urlsafe(32)
        self.client = None
        self.last_seen = 0
        self.port = 0
        self.recent_path = APP_ROOT / '.runtime' / 'recent.json'

    def recent(self):
        try:
            return read_json(self.recent_path)
        except (OSError, ValueError):
            return []

    def remember(self):
        p = self.project
        items = [{'name': p.data['name'], 'path': str(p.root)}, *[x for x in self.recent() if x['path'] != str(p.root)]]
        atomic_json(self.recent_path, items[:15])

    def client_check(self, client):
        if not client:
            raise UserError('页面会话无效，请刷新。')
        if self.client and client != self.client and time.monotonic() - self.last_seen < 35:
            raise UserError('另一个页面正在使用本程序。请先关闭原页面，35秒后再刷新；不会覆盖原页面进度。')
        self.client, self.last_seen = client, time.monotonic()

    def switch(self, folder, name=None):
        self.processor.pause()
        if self.project and self.project.root == Path(folder).resolve():
            return
        new = Project.open(folder) if name is None else Project.create(folder, name)
        try:
            if self.project:
                self.project.close()
        except Exception:
            new.lock.close()
            raise
        self.project = new
        self.remember()


STATE = State()


def models_available():
    root = Path.home() / '.cache/huggingface/hub'
    bundled = [str(p.parent) for p in (APP_ROOT / '.runtime/models').glob('*/model.bin')]
    return bundled + [str(p.parent) for p in root.glob('models--*/snapshots/*/model.bin')]


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        # Avoid writing material paths, tokens or source text to the app directory.
        pass

    def send(self, code, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorize(self, api=True):
        if self.headers.get('Host') not in (f'127.0.0.1:{STATE.port}', f'localhost:{STATE.port}'):
            raise UserError('只接受本机地址。')
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{STATE.port}', f'http://localhost:{STATE.port}'):
            raise UserError('禁止其他网页操作本地项目。')
        if api:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            token = self.headers.get('X-App-Token') or query.get('token', [''])[0]
            if not secrets.compare_digest(token, STATE.token):
                raise UserError('请求凭据无效，请从启动入口重新打开。')

    def do_GET(self):
        try:
            self.authorize(False)
            parsed = urllib.parse.urlsplit(self.path)
            path, q = parsed.path, urllib.parse.parse_qs(parsed.query)
            if path in ('/', '/index.html'):
                page = (APP_ROOT / 'web/index.html').read_text(encoding='utf-8').replace('__APP_TOKEN__', STATE.token)
                return self.send(200, page.encode(), 'text/html; charset=utf-8')
            if path in ('/style.css', '/review.css', '/app.js', '/review-helpers.js'):
                return self.send(200, (APP_ROOT / 'web' / path[1:]).read_bytes(), 'text/css' if path.endswith('.css') else 'text/javascript; charset=utf-8')
            self.authorize()
            if path == '/api/state':
                with STATE.mutex:
                    STATE.client_check(self.headers.get('X-Client'))
                    return self.send(200, {'project': STATE.project.snapshot() if STATE.project else None, 'recent': STATE.recent(),
                                           'models': models_available(), 'processing': STATE.processor.active(),'acceleration':public_status()})
            if path == '/api/waveform':
                with STATE.mutex:
                    p = STATE.project
                    ep = q.get('episode', [''])[0]
                    if not p or ep not in p.data['episodes']:
                        raise UserError('集数不存在')
                    e = p.data['episodes'][ep]
                    if not e.get('audio'):
                        return self.send(200, {'peaks': []})
                    output = p.root / 'cache/waveforms' / (ep + '.json')
                    if not output.exists() or read_json(output).get('version') != 2:
                        waveform(p.root / e['audio'], output)
                    return self.send(200, read_json(output))
            if path == '/api/media':
                with STATE.mutex:
                    p = STATE.project
                    if not p:
                        raise UserError('没有打开项目')
                    if q.get('project', [''])[0] != p.data['project_id']:
                        raise UserError('播放器属于已关闭的项目')
                    clip_id = q.get('issue', [''])[0]
                    if clip_id:
                        issue = next((x for x in p.data['issues'] if x['id'] == clip_id), None)
                        if not issue or not issue.get('clip'):
                            raise UserError('疑点音频尚未生成')
                        media = p.root / issue['clip']
                    else:
                        ep = p.data['episodes'].get(q.get('episode', [''])[0])
                        if not ep:
                            raise UserError('集数不存在')
                        is_video = q.get('video') == ['1']
                        media = Path(ep['media']) if is_video or not ep.get('audio') else Path(ep['audio'])
                        if not str(media) or str(media) == '.':
                            raise UserError('没有关联媒体')
                        if not media.is_absolute():
                            media = p.root / media
                    if not media.is_file():
                        raise UserError('素材不可访问；可切换缓存音频或重新定位')
                    # Open while holding project lock; stream releases it, so project switching cannot redirect the file.
                    file = media.open('rb')
                with file:
                    size = os.fstat(file.fileno()).st_size
                    start, end = 0, size - 1
                    range_header = self.headers.get('Range')
                    if range_header:
                        match = re.fullmatch(r'bytes=(\d+)-(\d*)', range_header)
                        if not match:
                            return self.send(416, {'error': '不支持此Range'})
                        start = int(match[1])
                        end = min(size - 1, int(match[2]) if match[2] else size - 1)
                        if start > end:
                            return self.send(416, {'error': 'Range超出文件'})
                    self.send_response(206 if range_header else 200)
                    self.send_header('Content-Type', mimetypes.guess_type(str(media))[0] or 'application/octet-stream')
                    self.send_header('Content-Length', str(end - start + 1))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-store')
                    if range_header:
                        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    file.seek(start)
                    left = end - start + 1
                    while left:
                        b = file.read(min(left, 128 * 1024))
                        if not b:
                            break
                        self.wfile.write(b)
                        left -= len(b)
                return
            self.send(404, {'error': '不存在的地址'})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (OSError, UserError, ValueError, KeyError) as exc:
            self.send(400, {'error': str(exc)})

    def do_POST(self):
        try:
            self.authorize()
            size = int(self.headers.get('Content-Length', 0))
            if size > 20 * 1024 * 1024:
                raise UserError('请求过大；意见请用文件导入。')
            body = json.loads(self.rfile.read(size) or b'{}')
            route = urllib.parse.urlsplit(self.path).path.removeprefix('/api/')
            with STATE.mutex:
                STATE.client_check(self.headers.get('X-Client'))
            if route == 'pick':
                return self.send(200, {'path': system_picker(body.get('kind', 'folder'))})
            if route == 'pause':
                STATE.processor.pause()
                return self.send(200, {'ok': True})
            if route in ('open', 'create', 'close'):
                # Never wait for a worker while holding its save mutex.
                STATE.processor.pause()
            with STATE.mutex:
                if route == 'scan':
                    return self.send(200, scan_pairs(body.get('media_dir'), body.get('srt_dir')))
                if route in ('create', 'open'):
                    STATE.switch(body['folder'], body.get('name', '') if route == 'create' else None)
                    return self.send(200, {'ok': True})
                if route == 'close':
                    if STATE.project:
                        if body.get('finished'):
                            errors = STATE.project.validate_export(delivery=True)
                            if errors:
                                raise UserError('仍有待审或格式问题，不能标记完成：\n' + '\n'.join(errors[:15]))
                            if not any(e['type'] == 'delivery' and e.get('revision') == STATE.project.data['revision'] for e in STATE.project.data['exports']):
                                raise UserError('请先导出本次修订的交付版。')
                            STATE.project.data['finished'] = True
                        STATE.project.close()
                        STATE.project = None
                    return self.send(200, {'ok': True})
                p = STATE.project
                if not p:
                    raise UserError('请先新建或打开项目。')
                readonly = route in ('cleanup-plan', 'save')
                if not readonly and body.get('revision') != p.data['revision']:
                    raise UserError('页面版本已变化，请刷新后重试；修改未套用。')
                if STATE.processor.active() and route not in ('edit', 'add-cue', 'save'):
                    raise UserError('请先暂停本地任务，再执行此操作。')
                old = copy.deepcopy(p.data)
                try:
                    result = self.project_action(route, body, p)
                except Exception:
                    p.data = old
                    # Some multi-step adapters have already saved a valid intermediate state.
                    # Restore the last complete pre-operation state before reporting failure.
                    if route == 'legacy':
                        p.save()
                    raise
                self.send(200, {'ok': True, 'result': result})
        except (OSError, ValueError, KeyError, TypeError, UserError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception as exc:
            traceback.print_exc()
            self.send(500, {'error': '操作失败，进度未切换：' + str(exc)})

    def project_action(self, route, b, p):
        if route == 'pairs':
            return p.import_pairs(b['pairs'])
        if route == 'edit':
            return p.edit(b['ids'], b['action'], b.get('values'))
        if route == 'add-cue':
            return p.add_cue(b['episode'], b['row'])
        if route == 'undo':
            return p.undo()
        if route == 'apply-certain':
            return p.apply_certain_opinions(b['ids'])
        if route == 'merge-preview':
            from .review import merge_plan
            return merge_plan(p)
        if route == 'merge-apply':
            from .review import merge_apply
            return merge_apply(p,b['ids'],b['token'])
        if route == 'term-preview':
            from .terminology import plan
            return plan(p)
        if route == 'term-apply':
            from .terminology import apply
            return apply(p,b['group_id'],b['choice'],b.get('text',''),b['token'])
        if route == 'scope-ignore':
            from .review import issue_in_scope
            ids=set(b['ids']);matches=[i for i in p.data['issues'] if i['id'] in ids]
            if len(matches)!=len(ids) or any(issue_in_scope(p.data,i) for i in matches):
                raise UserError('批量收起仅适用于没有对应字幕的范围外记录。')
            before=copy.deepcopy(p.data['issues'])
            for item in matches:item.update(excluded_by_user=True,scope_decision='用户仅审查已有字幕，不标记为人工通过')
            p.commit('批量收起范围外识别记录',extra={'issues_before':before})
            return {'excluded':len(matches)}
        if route == 'reclassify':
            from .review import migrate
            changed=migrate(p.data)
            if changed:p.save()
            return {'changed':changed}
        if route == 'save':
            return p.save()
        if route == 'reference':
            return p.add_reference(b['path'], b['kind'])
        if route == 'relink':
            return p.relink(b['episode'], b['path'], b.get('kind', 'media'))
        if route == 'process':
            return STATE.processor.start(p, b['episodes'], b.get('model', ''), b.get('download_model', 'small'))
        if route == 'acceleration-reset':
            if STATE.processor.active():raise UserError('请先暂停初查，再重新测速。')
            from .acceleration import profile_path
            atomic_json(profile_path(),{'profiles':{}})
            return {'reset':True}
        if route == 'cleanup-plan':
            return p.cleanup_plan()
        if route == 'cleanup':
            return p.cleanup(b['token'])
        if route == 'export':
            folder = b['folder']
            if inside(folder, p.root):
                raise UserError('请将导出保存到当前项目以外的输出文件夹，避免转交包递归包含自身。')
            if b['kind'] == 'ai':
                return export_ai(p, folder, b.get('full', False))
            if b['kind'] == 'backup':
                return backup(p, folder, b.get('include_video', False))
            return export_srt(p, folder, b.get('episodes'), b['kind'] == 'delivery')
        if route == 'import':
            return import_opinions(p, read_json(b['path']))
        if route == 'legacy':
            return p.import_legacy(b['folder'], b['progress'])
        if route == 'resolve':
            items = p.data['opinion_conflicts'] if b['kind'] == 'conflict' else p.data.get('review_tasks', []) if b['kind'] == 'task' else p.data['issues']
            item = next((x for x in items if x['id'] == b['id']), None)
            if not item:
                raise UserError('事项不存在')
            item.update(resolved=bool(b['resolved']), resolution=str(b.get('note', '人工已查看并决定')), resolved_at=time.time())
            p.commit('人工处理独立疑点/意见冲突')
            return
        raise UserError('未知操作')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    # One app instance per installation, plus independent project OS locks.
    runtime = APP_ROOT / '.runtime'
    runtime.mkdir(exist_ok=True)
    app_lock = Project.acquire(runtime)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    STATE.port = server.server_address[1]
    url = f'http://127.0.0.1:{STATE.port}/'
    atomic_json(runtime / 'server.json', {'url': url, 'pid': os.getpid()})
    print('字幕审查已启动：' + url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:
        pass
    finally:
        STATE.processor.pause()
        if STATE.project:
            STATE.project.close()
        server.server_close()
        app_lock.close()


if __name__ == '__main__':
    main()
