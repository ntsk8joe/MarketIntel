import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request
import xml.etree.ElementTree as ET

from .core import now, uid
from .http_client import authenticated_json


BLOCKED_AUTO = ['painbase.space', 'trustmrr.com', 'reddit.com', 'redditinc.com']
MAX_BYTES = 2_000_000


def validate_public_url(url, resolve=True):
    if not isinstance(url, str) or len(url) > 3000 or any(ord(c) < 32 for c in url):
        raise ValueError('请输入有效的公开网页 URL')
    parts = urlsplit(url)
    if parts.scheme not in ['http', 'https'] or not parts.hostname or parts.username or parts.password:
        raise ValueError('仅支持没有账号密码的 http/https URL')
    try:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        raise ValueError('URL 端口无效')
    if port not in [80, 443]:
        raise ValueError('仅允许公开网站的 80/443 端口')
    host = parts.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('不能采集本机或内部地址')
    try:
        direct = ipaddress.ip_address(host)
        if not direct.is_global:
            raise ValueError('不能采集私网、回环或保留地址')
    except ValueError as exc:
        if '不能采集' in str(exc): raise
    if not resolve:
        return parts
    try:
        addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except socket.gaierror:
        raise ValueError('域名解析失败，请检查地址和网络')
    if not addresses or any(not ipaddress.ip_address(x).is_global for x in addresses):
        raise ValueError('域名解析到非公开地址，已停止采集')
    return parts, addresses


def public_fetch(url):
    current = url
    for _ in range(6):
        parts, addresses = validate_public_url(current)
        host = parts.hostname.rstrip('.').lower()
        if any(host == x or host.endswith('.' + x) for x in BLOCKED_AUTO):
            raise ValueError('此平台默认不自动采集；请使用许可允许的导出或手动添加证据')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        # Pin the connection to the already-validated address; a second DNS lookup
        # must not turn a public URL into a request to the local network.
        sock = socket.create_connection((addresses[0], port), timeout=20)
        if parts.scheme == 'https':
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            except Exception:
                sock.close(); raise
        connection = http.client.HTTPConnection(host, port, timeout=20)
        connection.sock = sock
        try:
            path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
            connection.request('GET', path, headers={'User-Agent': 'MarketIntel/1.0 public-research', 'Accept': 'text/html,application/rss+xml,application/xml,text/plain,application/json', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in [301, 302, 303, 307, 308]:
                location = response.getheader('Location')
                if not location: raise ValueError('重定向缺少目标地址')
                current = urljoin(current, location); continue
            if response.status >= 400: raise ValueError('网页返回 HTTP ' + str(response.status))
            length = response.getheader('Content-Length')
            if length and int(length) > MAX_BYTES: raise ValueError('页面超过 2 MB 限制，请使用导出或更小页面')
            blob = response.read(MAX_BYTES + 1)
            if len(blob) > MAX_BYTES: raise ValueError('页面超过 2 MB 限制')
            content_type = response.getheader('Content-Type', 'text/plain')
            if not any(x in content_type.lower() for x in ['text/', 'html', 'xml', 'json']):
                raise ValueError('目前只采集文本、HTML、XML 与 JSON；其他文件请手动导入摘要')
            charset = re.search(r'charset=([\w-]+)', content_type)
            try: raw = blob.decode(charset.group(1) if charset else 'utf-8', errors='replace')
            except LookupError: raw = blob.decode('utf-8', errors='replace')
            return raw, current, content_type
        finally:
            connection.close()
    raise ValueError('网页重定向次数过多')


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0; self.output = []

    def handle_starttag(self, tag, attrs):
        if tag in ['script', 'style', 'noscript', 'svg']: self.skip += 1
        if not self.skip and tag in ['p', 'h1', 'h2', 'h3', 'h4', 'li', 'div', 'tr', 'br']:
            self.output.append('\n')

    def handle_endtag(self, tag):
        if tag in ['script', 'style', 'noscript', 'svg'] and self.skip: self.skip -= 1
        if not self.skip and tag in ['p', 'li', 'div', 'tr']: self.output.append('\n')

    def handle_data(self, data):
        if not self.skip: self.output.append(data)

    def text(self):
        return '\n'.join(re.sub(r'\s+', ' ', line).strip() for line in ''.join(self.output).splitlines() if line.strip())


def extract(raw, typ, content_type):
    if typ == 'rss':
        root = ET.fromstring(raw)
        lines = []
        for item in list(root.findall('.//item'))[:100] + list(root.findall('{http://www.w3.org/2005/Atom}entry'))[:100]:
            for node in list(item):
                if node.tag.split('}')[-1] in ['title', 'description', 'summary', 'content', 'link', 'pubDate', 'published', 'updated']:
                    text = ''.join(node.itertext()) or node.attrib.get('href', '')
                    parser = TextExtractor(); parser.feed(text)
                    lines.append(parser.text())
        if not lines: raise ValueError('未识别 RSS/Atom 条目，请检查源地址')
        return '\n'.join(lines)
    if 'html' in content_type.lower():
        parser = TextExtractor(); parser.feed(raw); text = parser.text()
        if len(text) < 80:
            raise ValueError('页面可读文字太少，可能需要登录或动态渲染；可使用 Apify 或手动导入')
        return text
    return raw


def apify_request(path, token, payload=None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    body = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'; body = json.dumps(payload).encode()
    request = Request('https://api.apify.com/v2/' + path, data=body, headers=headers)
    # Fixed destination; no source URL is interpolated into the destination host.
    return authenticated_json(request, 4_000_000, timeout=30)


class Collector:
    def __init__(self, store, connections=None):
        self.store = store; self.connections = connections; self.active = set(); self.lock = threading.Lock()

    def submit(self, source_id, resume_id=None, confirmed_new=False):
        source = self.store.get(source_id)
        if not source or source['kind'] != 'source': raise ValueError('采集来源不存在')
        with self.lock:
            if source_id in self.active: raise ValueError('此来源正在运行，请等候完成')
            if len(self.active) >= 2: raise ValueError('已有两个采集任务运行中，请稍后再试')
            if source['data'].get('type') == 'apify' and not self.token(): raise ValueError('请在数据与连接中配置 APIFY_TOKEN')
            if source['data'].get('type') == 'apify' and source['data'].get('allow_apify') is not True:
                raise ValueError('请在来源设置中确认允许手动运行收费 Actor')
            existing = self.store.get_job(resume_id) if resume_id else None
            if resume_id and (not existing or existing['source_id'] != source_id or existing['status'] not in ['interrupted', 'waiting', 'error']):
                raise ValueError('任务无法恢复')
            if existing and source['data'].get('type') == 'apify' and not existing.get('remote_id'):
                raise ValueError('没有远端任务 ID，不能恢复；请先在 Apify 控制台核查是否已启动或收费，再决定是否新建任务')
            if not existing and source['data'].get('type') == 'apify':
                previous = self.store.latest_job(source_id)
                if previous and previous['status'] in ['error', 'interrupted', 'waiting'] and not previous.get('remote_id') and confirmed_new is not True:
                    raise ValueError('上次启动结果未确定，请先核查 Apify 控制台，再明确确认新建收费任务')
            identifier = self.store.job(source_id, identifier=resume_id)
            self.active.add(source_id)
        thread = threading.Thread(target=self.run, args=(source, identifier, existing.get('remote_id') if existing else None), daemon=True)
        thread.start()
        return identifier

    def token(self):
        import os
        return self.connections.get('APIFY_TOKEN') if self.connections else os.environ.get('APIFY_TOKEN', '')

    def run(self, source, identifier, remote_id=None):
        self.store.job(source['id'], 'running', identifier)
        token = ''
        try:
            typ = source['data'].get('type', 'web')
            extra = {}
            if typ == 'apify':
                token = self.token()
                if not remote_id:
                    self.store.job(source['id'], 'running', identifier, result={'start_uncertain': True})
                    actor = quote(source['data']['actor_id'].replace('/', '~'), safe='~')
                    budget = source['data']['max_charge']
                    run = apify_request('acts/' + actor + '/runs?timeout=180&maxTotalChargeUsd=' + str(budget), token, source['data'].get('actor_input', {}))['data']
                    remote_id = run['id']
                    self.store.job(source['id'], 'running', identifier, remote_id=remote_id, result={'start_uncertain': False})
                deadline = time.monotonic() + 210
                while True:
                    run = apify_request('actor-runs/' + quote(remote_id, safe=''), token)['data']
                    if run['status'] in ['SUCCEEDED', 'FAILED', 'TIMED-OUT', 'ABORTED']:
                        self.store.job(source['id'], 'running', identifier, result={'remote_status': run['status'], 'cost_usd': run.get('usageTotalUsd')}, remote_id=remote_id)
                        break
                    if time.monotonic() >= deadline:
                        self.store.job(source['id'], 'waiting', identifier, remote_id=remote_id, error='远端仍在运行；恢复会查询同一任务，不会新建收费任务')
                        return
                    time.sleep(3)
                if run['status'] != 'SUCCEEDED': raise ValueError('Apify 任务状态：' + run['status'])
                dataset = run.get('defaultDatasetId')
                if not dataset: raise ValueError('Actor 没有输出数据集')
                items = apify_request('datasets/' + quote(dataset, safe='') + '/items?format=json&limit=200&clean=true', token)
                raw = json.dumps(items, ensure_ascii=False, indent=2)
                text = '\n'.join(str(x.get('text') or x.get('markdown') or json.dumps(x, ensure_ascii=False)) for x in items if isinstance(x, dict))
                if not text: raise ValueError('数据集没有可读内容')
                url = 'https://console.apify.com/actors/runs/' + remote_id
                content_type = 'application/json'
                extra = {'remote_id': remote_id, 'dataset_id': dataset, 'items_received': len(items), 'truncated_at': 200,
                         'cost_usd': run.get('usageTotalUsd'), 'cost_note': '来源为 Apify 返回值；账单以供应商为准；首版最多取 200 条'}
            else:
                if source['data'].get('capture_allowed') is not True:
                    raise ValueError('请先确认此来源允许本次采集与保存范围')
                raw, url, content_type = public_fetch(source['data']['url'])
                text = extract(raw, typ, content_type)
            result = self.store.add_snapshot(source, text, raw, url, content_type)
            result.update(extra)
            self.store.job(source['id'], 'done', identifier, result=result, remote_id=remote_id)
            self.store.save('research', '采集记录：' + source['name'], source['project_id'],
                            {'source_id': source['id'], 'run_id': identifier, 'observed_at': now(), 'result': result,
                             'summary': '内容有变化，需审核' if result['changed'] else '取得快照；并未自动认定内容为已验证证据'})
        except Exception as exc:
            # Do not dump authenticated request bodies or environment values.
            message = str(exc)
            if token: message = message.replace(token, '[redacted]')
            if self.connections: message = self.connections.redact(message)
            message = message[:500]
            if source['data'].get('type') == 'apify' and not remote_id:
                message = '未取得远端任务 ID；请到 Apify 控制台核查运行和收费记录。' + message
            self.store.job(source['id'], 'error', identifier, error=message, remote_id=remote_id)
            current = self.store.get(source['id'])
            data = dict(current['data']); data['last_error'] = message
            self.store.save('source', current['name'], current['project_id'], data, current['id'])
        finally:
            with self.lock: self.active.discard(source['id'])

    def scheduler(self):
        while True:
            for source in self.store.list('source'):
                data = source['data']
                if not data.get('tracking') or data.get('type') == 'apify' or not data.get('capture_allowed'):
                    continue
                if not self.store.due(source): continue
                # Failed hosts are retried no more than once per hour.
                recent = next((j for j in self.store.jobs() if j['source_id'] == source['id']), None)
                if recent:
                    from datetime import datetime, timezone
                    if (datetime.now(timezone.utc) - datetime.fromisoformat(recent['created'])).total_seconds() < 3600:
                        continue
                try: self.submit(source['id'])
                except ValueError: pass
            time.sleep(60)
