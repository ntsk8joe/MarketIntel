import csv
import io
import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .core import Store, now
from .collectors import Collector
from .research import discover, keyword_metrics, ai_brief
from .seed import seed
from .settings import Connections


def csv_cell(value):
    text = '' if value is None else str(value)
    if text and (text[0] in '\t\r\n' or text.lstrip('\ufeff \t\r\n').startswith(('=', '+', '-', '@', '＝', '＋', '－', '＠'))):
        return "'" + text
    return text


def reject_json_constant(value):
    raise ValueError('JSON 不允许 NaN 或 Infinity')


def serve(root, port=8765, start_scheduler=True, load_demo=False):
    store = Store(root)
    if load_demo: seed(store)
    connections = Connections(root)
    collector = Collector(store, connections)
    token = secrets.token_urlsafe(32)
    assets = Path(__file__).resolve().parent.parent / 'web'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if '/api/state' not in self.path:
                # Search queries and export filters may contain private material.
                print(self.command + ' ' + urlsplit(self.path).path + ' ' + str(args[1] if len(args) > 1 else ''), flush=True)

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def trusted(self):
            host = self.headers.get('Host', '')
            return host in ['127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)]

        def send(self, payload, status=200, content_type='application/json; charset=utf-8', filename=None):
            if isinstance(payload, (dict, list)): payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            elif isinstance(payload, str): payload = payload.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'")
            self.send_header('Referrer-Policy', 'no-referrer')
            if filename: self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
            self.end_headers(); self.wfile.write(payload)

        def do_GET(self):
            if not self.trusted(): return self.send({'error': 'Host 不允许'}, 403)
            parts = urlsplit(self.path); path = parts.path; args = parse_qs(parts.query)
            try:
                if path == '/api/state':
                    records = store.list()
                    for r in records:
                        if r['kind'] == 'opportunity': r['assessment'] = store.assessment(r)
                        if r['kind'] == 'source':
                            r['due'] = store.due(r)
                            recent = store.latest_job(r['id'])
                            r['start_uncertain'] = bool(r['data'].get('type') == 'apify' and recent and not recent.get('remote_id') and recent['status'] in ['error', 'interrupted', 'waiting'])
                    return self.send({'records': records, 'changes': store.changes(), 'jobs': store.jobs(), 'snapshots': store.snapshots(),
                                      'csrf': token, 'root': str(store.root), 'vault': str(store.vault),
                                      **connections.status()})
                if path == '/api/search': return self.send(store.search(args.get('q', [''])[0], args.get('project', [None])[0]))
                if path == '/api/snapshot':
                    item = store.snapshot(args.get('id', [''])[0])
                    return self.send(item or {'error': '快照不存在'}, 200 if item else 404)
                if path == '/api/note':
                    note = (store.vault / args.get('path', [''])[0]).resolve()
                    if not note.is_relative_to(store.vault) or note.suffix != '.md' or not note.is_file():
                        return self.send({'error': '笔记不存在'}, 404)
                    return self.send({'path': str(note.relative_to(store.vault)), 'text': note.read_text(encoding='utf-8')})
                if path == '/api/export':
                    typ = args.get('type', ['json'])[0]; project_id = args.get('project', [None])[0]
                    if typ not in ['json', 'csv', 'markdown']: raise ValueError('不支持的导出格式')
                    if typ == 'markdown': return self.send(store.report(project_id), content_type='text/markdown; charset=utf-8', filename='marketintel-report.md')
                    if typ == 'csv':
                        stream = io.StringIO(); writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
                        writer.writerow(['id', 'name', 'project_id', 'url', 'summary', 'claim_type', 'direction', 'review_status', 'observed_at'])
                        for r in store.list('evidence', project_id): writer.writerow([csv_cell(r.get(k) if k in ['id', 'name', 'project_id'] else r['data'].get(k, '')) for k in ['id', 'name', 'project_id', 'url', 'summary', 'claim_type', 'direction', 'review_status', 'observed_at']])
                        return self.send('\ufeff' + stream.getvalue(), content_type='text/csv; charset=utf-8', filename='evidence.csv')
                    return self.send(store.export(), filename='marketintel-data.json')
                if path == '/': path = '/index.html'
                if path not in ['/index.html', '/app.js', '/style.css', '/tokens.css']:
                    return self.send({'error': '页面不存在'}, 404)
                asset = assets / path.lstrip('/')
                return self.send(asset.read_bytes(), content_type=(mimetypes.guess_type(str(asset))[0] or 'text/plain') + '; charset=utf-8')
            except (ValueError, OSError) as exc:
                return self.send({'error': connections.redact(str(exc))[:500]}, 400)

        def do_POST(self):
            if not self.trusted() or self.headers.get('X-MarketIntel-Token') != token:
                return self.send({'error': '请求已过期，请刷新页面'}, 403)
            origin = self.headers.get('Origin')
            if origin and origin not in ['http://127.0.0.1:' + str(self.server.server_port), 'http://localhost:' + str(self.server.server_port)]:
                return self.send({'error': '来源不允许'}, 403)
            try:
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                    raise ValueError('请求需要唯一的 Content-Length，且不支持分块传输')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000: raise ValueError('请求需要正文，且不能超过 2 MB')
                body = json.loads(self.rfile.read(length), parse_constant=reject_json_constant); path = urlsplit(self.path).path
                if not isinstance(body, dict): raise ValueError('请求必须是 JSON 对象')
                if path == '/api/connections': return self.send(connections.update(body.get('values')))
                if path == '/api/records':
                    return self.send(store.save(body['kind'], body['name'], body.get('project_id', ''), body.get('data', {}), body.get('id')))
                if path == '/api/collect': return self.send({'job_id': collector.submit(body['source_id'], body.get('resume_id'), body.get('confirmed_new', False))})
                if path == '/api/changes/review': store.acknowledge(body['id']); return self.send({'ok': True})
                if path == '/api/sync': return self.send(store.sync_vault())
                if path == '/api/backup': return self.send({'path': store.backup()})
                if path == '/api/import':
                    rows = body.get('rows')
                    if body.get('format') == 'csv': rows = list(csv.DictReader(io.StringIO(body.get('text', '').lstrip('\ufeff'))))
                    return self.send(store.import_evidence(rows, body.get('project_id', '')))
                if path == '/api/discover': return self.send(discover(store, body.get('project_id'), body.get('query', ''), body.get('provider')))
                if path == '/api/keywords': return self.send(keyword_metrics(store, body.get('project_id'), body.get('keywords'), connections))
                if path == '/api/ai': return self.send(ai_brief(store, body.get('project_id'), body.get('question', '请分析这个方向，重点寻找反证与下一步'), connections))
                return self.send({'error': '接口不存在'}, 404)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                message = connections.redact(str(exc))[:500]
                return self.send({'error': message}, 400)
            except Exception:
                return self.send({'error': '操作未完成，请检查数据格式或接口连接；原有资料已保留'}, 500)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    if start_scheduler:
        threading.Thread(target=collector.scheduler, daemon=True).start()
    server.store = store; server.collector = collector; server.connections = connections
    return server
