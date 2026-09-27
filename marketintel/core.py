import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import threading
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path


KINDS = {'project', 'source', 'evidence', 'competitor', 'opportunity', 'experiment', 'decision', 'research'}
STAGES = ['needs-evidence', 'interview', 'pilot', 'paid', 'repeat', 'parked']
LABELS = {'project': '研究方向', 'source': '追踪来源', 'evidence': '证据', 'competitor': '竞品',
          'opportunity': '机会', 'experiment': '验证', 'decision': '决策', 'research': '研究记录'}
FOLDERS = {'project': '10-Markets', 'source': '15-Sources', 'competitor': '20-Competitors',
           'evidence': '30-Evidence', 'research': '40-ResearchRuns', 'experiment': '50-Experiments',
           'opportunity': '55-Opportunities', 'decision': '60-Decisions'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def uid(prefix):
    return prefix + '-' + uuid.uuid4().hex[:16]


def number(value, name, nullable=True):
    if value is None or value == '':
        if nullable:
            return None
        raise ValueError(name + '不能为空')
    if isinstance(value, bool):
        raise ValueError(name + '必须是数字')
    try:
        n = float(value)
    except (ValueError, TypeError):
        raise ValueError(name + '必须是数字')
    if not math.isfinite(n) or n < 0:
        raise ValueError(name + '必须是有限的非负数')
    return n


def economics(inputs):
    if not isinstance(inputs, dict):
        raise ValueError('经济情景必须是对象')
    names = ['price', 'variable', 'fixed', 'acquisition', 'customers']
    values = {k: number(inputs.get(k), k) for k in names}
    if values['customers'] is not None and not values['customers'].is_integer():
        raise ValueError('客户数必须为整数')
    missing = [k for k, v in values.items() if v is None]
    if missing:
        return {'status': 'unknown', 'missing': missing, 'inputs': values, 'profit': None, 'break_even': None}
    margin = values['price'] - values['variable']
    profit = values['customers'] * margin - values['fixed'] - values['acquisition']
    break_even = (values['fixed'] + values['acquisition']) / margin if margin > 0 else None
    if not math.isfinite(profit) or (break_even is not None and not math.isfinite(break_even)):
        raise ValueError('经济情景计算溢出，请缩小输入数值')
    return {'status': 'scenario', 'inputs': values, 'margin': margin,
            'profit': profit, 'break_even': math.ceil(break_even) if break_even is not None else None}


def checked_path(path, boundary):
    """Reject symlinks before reading/writing app-managed local paths."""
    path, boundary = Path(path), Path(boundary)
    if not path.is_relative_to(boundary) or not path.resolve().is_relative_to(boundary.resolve()):
        raise ValueError('文件路径超出允许目录')
    for item in [path] + list(path.parents):
        if item.is_symlink(): raise ValueError('系统管理的文件路径不能包含符号链接')
        if item == boundary: break
    return path


def write_local_text(path, text, boundary):
    path = checked_path(path, boundary)
    descriptor, temporary = tempfile.mkstemp(prefix='.marketintel-', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        checked_path(path, boundary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.data_dir = self.root / 'data'
        self.vault = self.root / 'vault'
        checked_path(self.data_dir, self.root); checked_path(self.vault, self.root)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.vault.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / 'intelligence.sqlite'
        checked_path(self.db_path, self.root)
        if os.name != 'nt':
            self.data_dir.chmod(0o700); self.vault.chmod(0o700)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                  project_id TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
                  created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS records_kind ON records(kind, project_id);
                CREATE TABLE IF NOT EXISTS snapshots (
                  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, hash TEXT NOT NULL,
                  checked_at TEXT NOT NULL, url TEXT NOT NULL, text TEXT NOT NULL,
                  raw_path TEXT NOT NULL, content_type TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS snapshots_source ON snapshots(source_id, checked_at);
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, status TEXT NOT NULL, source_id TEXT NOT NULL,
                  created TEXT NOT NULL, updated TEXT NOT NULL, result TEXT NOT NULL,
                  remote_id TEXT, error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS changes (
                  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, old_id TEXT NOT NULL,
                  new_id TEXT NOT NULL, created TEXT NOT NULL, diff TEXT NOT NULL,
                  reviewed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            # A local process cannot finish a running task after it exits. Keep the
            # remote ID so an Apify run can be resumed without starting another run.
            db.execute("UPDATE jobs SET status='interrupted', error='上次运行中断；可恢复已有远端任务或重新检查网页' WHERE status IN ('running','queued')")
        self.ensure_vault()
        if os.name != 'nt': self.db_path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(str(self.db_path), timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def meta(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))
                return value
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else None

    def list(self, kind=None, project_id=None):
        query, args = 'SELECT * FROM records WHERE 1=1', []
        if kind:
            query += ' AND kind=?'; args.append(kind)
        if project_id:
            query += ' AND project_id=?'; args.append(project_id)
        query += ' ORDER BY updated DESC, id'
        with self.connect() as db:
            return [self.unpack(r) for r in db.execute(query, args)]

    @staticmethod
    def unpack(row):
        if row is None:
            return None
        r = dict(row); r['data'] = json.loads(r.pop('payload'))
        return r

    def get(self, identifier):
        with self.connect() as db:
            return self.unpack(db.execute('SELECT * FROM records WHERE id=?', (identifier,)).fetchone())

    def validate(self, kind, name, project_id, data):
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError('未知记录类型')
        if not isinstance(name, str) or not name.strip() or len(name) > 180:
            raise ValueError('标题需要 1—180 个字符')
        if not isinstance(data, dict):
            raise ValueError('数据必须是对象')
        if not isinstance(project_id, str):
            raise ValueError('关联方向 ID 必须是文字')
        if len(json.dumps(data, allow_nan=False)) > 1_000_000:
            raise ValueError('记录过大，请导入摘要或分开保存')
        for key in ['url', 'summary', 'quote', 'thread_key', 'role', 'reason', 'date', 'currency',
                    'analysis', 'audience', 'task', 'keywords', 'region', 'language', 'notes', 'type',
                    'actor_id', 'policy', 'claim_type', 'direction', 'review_status', 'resolved',
                    'observed_at', 'source_id', 'snapshot_id', 'opportunity_id', 'billing', 'checked_at',
                    'price_status', 'features', 'limitations', 'stage', 'payer', 'deliverable',
                    'alternatives', 'channel', 'hypothesis', 'unknowns', 'outcome', 'choice',
                    'conditions', 'next_action', 'counter', 'last_checked']:
            if key in data and data[key] is not None and not isinstance(data[key], str):
                raise ValueError(key + '必须是文字')
        if 'evidence_ids' in data and (not isinstance(data['evidence_ids'], list) or any(not isinstance(x, str) for x in data['evidence_ids'])):
            raise ValueError('关联证据必须是 ID 列表')
        for key in ['tracking', 'capture_allowed', 'save_raw', 'allow_apify', 'repeat']:
            if key in data and not isinstance(data[key], bool):
                raise ValueError(key + '必须是布尔值')
        if 'evidence_ids' in data:
            data['evidence_ids'] = list(dict.fromkeys(data['evidence_ids']))
            for eid in data['evidence_ids']:
                e = self.get(eid)
                if not e or e['kind'] != 'evidence' or e['project_id'] != project_id:
                    raise ValueError('关联证据必须属于同一个研究方向')
        if project_id:
            project = self.get(project_id)
            if not project or project['kind'] != 'project':
                raise ValueError('关联研究方向不存在')
        if kind == 'source':
            from .collectors import validate_public_url
            typ = data.get('type', 'web')
            if typ not in ['web', 'rss', 'apify']:
                raise ValueError('来源仅支持网页、RSS 或 Apify')
            if typ != 'apify':
                validate_public_url(data.get('url', ''), resolve=False)
            else:
                if not re.fullmatch(r'[\w-]+/[\w-]+', data.get('actor_id', '')):
                    raise ValueError('Actor ID 应为 owner/name')
                if not isinstance(data.get('actor_input', {}), dict):
                    raise ValueError('Actor 输入必须为 JSON 对象')
                budget = number(data.get('max_charge'), 'Apify 单次费用上限', False)
                if budget <= 0 or budget > 20:
                    raise ValueError('单次 Apify 费用上限需要大于 0 且不超过 20 美元')
            interval = number(data.get('interval_hours', 336), '复核间隔', False)
            if not 1 <= interval <= 87600:
                raise ValueError('复核间隔需要在 1—87600 小时之间')
            data['interval_hours'] = interval
            data['tracking'] = bool(data.get('tracking', False))
            if typ == 'apify' and data['tracking']:
                raise ValueError('收费 Actor 仅支持手动运行；自动追踪可使用公开网页或 RSS')
        if kind == 'evidence':
            if data.get('claim_type') == 'fact' and not data.get('url'):
                raise ValueError('有来源事实需要原始来源 URL')
            if data.get('review_status') == 'verified' and (not data.get('url') or not data.get('summary')):
                raise ValueError('已审核证据需要原始来源及具体陈述')
            if data.get('review_status', 'pending') not in ['pending', 'verified', 'rejected']:
                raise ValueError('审核状态无效')
            if data.get('claim_type', 'self-report') not in ['fact', 'self-report', 'inference']:
                raise ValueError('证据类型无效')
            if data.get('direction', 'neutral') not in ['support', 'counter', 'neutral']:
                raise ValueError('证据方向无效')
            if data.get('source_id'):
                source = self.get(data['source_id'])
                if not source or source['kind'] != 'source' or source['project_id'] != project_id:
                    raise ValueError('来源必须属于同一个研究方向')
            if data.get('snapshot_id'):
                snapshot = self.snapshot(data['snapshot_id'])
                if not snapshot or snapshot['source_id'] != data.get('source_id'):
                    raise ValueError('快照需要关联其原始来源')
        if kind == 'competitor':
            data['price'] = number(data.get('price'), '价格')
        if kind == 'opportunity':
            data['economics'] = economics(data.get('economics', {}))['inputs']
            if data.get('stage', 'needs-evidence') not in STAGES:
                raise ValueError('机会阶段无效')
        if kind == 'experiment':
            opportunity = self.get(data.get('opportunity_id', ''))
            if not opportunity or opportunity['kind'] != 'opportunity' or opportunity['project_id'] != project_id:
                raise ValueError('验证需要关联同一研究方向的机会')
            data['payment'] = number(data.get('payment'), '实际收款')
            data['cost'] = number(data.get('cost'), '实际成本')
            if 'repeat' in data and not isinstance(data['repeat'], bool):
                raise ValueError('复用标记必须是布尔值')
            if data.get('payment') and not re.fullmatch(r'[A-Z]{3}', data.get('currency', '')):
                raise ValueError('收款币种需要三个大写字母，例如 USD')
            if data.get('payment') and not data.get('date'):
                raise ValueError('收款记录需要日期')
            if data.get('date'):
                try:
                    datetime.strptime(data['date'], '%Y-%m-%d')
                except ValueError:
                    raise ValueError('日期应为 YYYY-MM-DD')
        if kind == 'decision':
            target = self.get(data.get('opportunity_id', ''))
            if not target or target['kind'] != 'opportunity' or target['project_id'] != project_id:
                raise ValueError('决策需要关联同一研究方向的机会')
            if not data.get('reason', '').strip():
                raise ValueError('请写明决策理由')

    def save(self, kind, name, project_id='', data=None, identifier=None):
        if data is not None and not isinstance(data, dict): raise ValueError('数据必须是对象')
        data = dict(data) if data is not None else {}
        self.validate(kind, name, project_id, data)
        identifier = identifier or uid(kind)
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', identifier):
            raise ValueError('记录 ID 无效')
        with self.lock:
            old = self.get(identifier)
            if old and (old['kind'] != kind or old['project_id'] != project_id):
                raise ValueError('记录的类型和关联方向不能更改')
            candidate = {'id': identifier, 'kind': kind}
            checked_path(self.note_path(candidate), self.vault)
            checked_path(self.vault / '00-Index' / (identifier + '-待合并.md'), self.vault)
            if kind == 'experiment' and old:
                target = self.get(old['data']['opportunity_id'])
                if target['data'].get('stage') in ['paid', 'repeat']:
                    trials = [x['data'] for x in self.list('experiment', project_id) if x['id'] != identifier and x['data'].get('opportunity_id') == target['id']]
                    if data.get('opportunity_id') == target['id']: trials.append(data)
                    paid = [x for x in trials if (x.get('payment') or 0) > 0]
                    if not paid or (target['data']['stage'] == 'repeat' and not any(x.get('repeat') for x in paid)):
                        raise ValueError('更正此收款会使机会阶段失去依据；请先将关联机会改回验证阶段')
            if kind == 'opportunity' and data.get('stage') in ['paid', 'repeat']:
                paid = [x for x in self.list('experiment', project_id)
                        if x['data'].get('opportunity_id') == identifier and (x['data'].get('payment') or 0) > 0]
                if not paid:
                    raise ValueError('升级为已成交需要先记录实际收款验证')
                if data['stage'] == 'repeat' and not any(x['data'].get('repeat') for x in paid):
                    raise ValueError('升级为复用阶段需要实际再次购买/使用记录')
            stamp = now()
            with self.connect() as db:
                db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload=excluded.payload,updated=excluded.updated',
                           (identifier, kind, name.strip(), project_id, json.dumps(data, ensure_ascii=False), old['created'] if old else stamp, stamp))
            record = self.get(identifier)
            record['vault_status'] = self.write_note(record)
            return record

    def job(self, source_id, status='queued', identifier=None, result=None, error='', remote_id=None):
        identifier = identifier or uid('run')
        with self.connect() as db:
            old = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
            db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                       (identifier, status, source_id, old['created'] if old else now(), now(),
                        json.dumps(result if result is not None else (json.loads(old['result']) if old else {}), ensure_ascii=False),
                        remote_id or (old['remote_id'] if old else None), error))
        return identifier

    def jobs(self):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC,rowid DESC LIMIT 100')]
        for r in rows:
            r['result'] = json.loads(r['result'])
        return rows

    def get_job(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
        if not row: return None
        row = dict(row); row['result'] = json.loads(row['result'])
        return row

    def latest_job(self, source_id):
        with self.connect() as db:
            row = db.execute('SELECT id FROM jobs WHERE source_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (source_id,)).fetchone()
        return self.get_job(row['id']) if row else None

    def snapshots(self, source_id=None, limit=200):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT id,source_id,hash,checked_at,url,raw_path,content_type,substr(text,1,600) AS preview FROM snapshots ' +
                    ('WHERE source_id=? ' if source_id else '') + 'ORDER BY checked_at DESC, rowid DESC' + (' LIMIT 200' if limit is not None else ''),
                    (source_id,) if source_id else ())]
        return rows

    def snapshot(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM snapshots WHERE id=?', (identifier,)).fetchone()
            return dict(row) if row else None

    def add_snapshot(self, source, text, raw, url, content_type):
        import difflib
        with self.lock:
            source = self.get(source['id']) or source
            text = text[:250_000]
            digest = hashlib.sha256(text.encode()).hexdigest()
            with self.connect() as db:
                old = db.execute('SELECT * FROM snapshots WHERE source_id=? ORDER BY checked_at DESC,rowid DESC LIMIT 1', (source['id'],)).fetchone()
            stamp = now(); changed = bool(old and old['hash'] != digest)
            sid = old['id'] if old and old['hash'] == digest else uid('snapshot')
            if not old or old['hash'] != digest:
                raw_path = ''
                if source['data'].get('save_raw', True):
                    path = self.data_dir / 'raw' / source['id'] / (sid + '.txt')
                    checked_path(path, self.data_dir)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    write_local_text(path, raw, self.data_dir); raw_path = str(path.relative_to(self.root))
                with self.connect() as db:
                    db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)', (sid, source['id'], digest, stamp, url, text, raw_path, content_type))
                    if changed:
                        diff = '\n'.join(difflib.unified_diff(old['text'].splitlines(), text.splitlines(), fromfile=old['checked_at'], tofile=stamp, n=2))[:30_000]
                        db.execute('INSERT INTO changes VALUES (?,?,?,?,?,?,0)', (uid('change'), source['id'], old['id'], sid, stamp, diff))
            data = dict(source['data'])
            data.update(last_checked=stamp, latest_snapshot=sid, last_error='', last_changed=stamp if changed else data.get('last_changed', ''))
            self.save('source', source['name'], source['project_id'], data, source['id'])
            return {'snapshot_id': sid, 'changed': changed, 'unchanged': bool(old and old['hash'] == digest), 'characters': len(text)}

    def changes(self, limit=200):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM changes ORDER BY created DESC, rowid DESC' + (' LIMIT 200' if limit is not None else ''))]

    def acknowledge(self, identifier):
        with self.connect() as db:
            db.execute('UPDATE changes SET reviewed=1 WHERE id=?', (identifier,))

    def due(self, source):
        checked = source['data'].get('last_checked')
        if not checked:
            return True
        try:
            interval = number(source['data'].get('interval_hours', 336), '复核间隔', False)
            if not 1 <= interval <= 87600: return False
            return datetime.fromisoformat(checked) + timedelta(hours=interval) <= datetime.now(timezone.utc)
        except OverflowError:
            return False
        except (ValueError, TypeError):
            return True

    def assessment(self, opportunity):
        data = opportunity['data']
        evidence = [self.get(i) for i in dict.fromkeys(data.get('evidence_ids', []))]
        evidence = [x for x in evidence if x and x['kind'] == 'evidence' and x['project_id'] == opportunity['project_id']]
        approved = [x for x in evidence if x['data'].get('review_status') == 'verified']
        independent = {x['data'].get('thread_key') or x['data'].get('url') or x['id'] for x in approved}
        trials = [x for x in self.list('experiment', opportunity['project_id']) if x['data'].get('opportunity_id') == opportunity['id']]
        paid = [x for x in trials if (x['data'].get('payment') or 0) > 0]
        missing = []
        for key, label in [('payer', '买单人'), ('deliverable', '明确交付'), ('channel', '获客路径'), ('alternatives', '已有替代')]:
            if not data.get(key): missing.append(label)
        if not approved: missing.append('已审核证据')
        if not paid: missing.append('实际付款')
        if not any(x['data'].get('repeat') for x in paid): missing.append('重复使用/购买')
        margin_trials = [x for x in paid if x['data'].get('cost') is not None]
        margin_by_currency = {}
        for trial in margin_trials:
            currency = trial['data'].get('currency', '')
            margin_by_currency[currency] = margin_by_currency.get(currency, 0) + trial['data']['payment'] - trial['data']['cost']
        return {'approved': len(approved), 'independent_threads': len(independent),
                'counter': len([x for x in approved if x['data'].get('direction') == 'counter']),
                'counter_pending': len([x for x in evidence if x['data'].get('direction') == 'counter' and x['data'].get('review_status') == 'pending']),
                'paid_trials': len(paid), 'repeat_trials': len([x for x in paid if x['data'].get('repeat')]),
                'trial_margin_by_currency': margin_by_currency,
                'missing': missing, 'economics': economics(data.get('economics', {})),
                'label': '已有成交证据' if paid else ('值得进一步核查' if approved else '待补证')}

    def ensure_vault(self):
        for folder in FOLDERS.values(): checked_path(self.vault / folder, self.vault).mkdir(exist_ok=True)
        index = self.vault / '00-Index'
        checked_path(index, self.vault).mkdir(exist_ok=True)
        if not (index / '首页.md').exists():
            write_local_text(index / '首页.md', '# MarketIntel 情报知识库\n\n这里保存研究方向、证据、竞品、机会、验证和决策。\n\n系统生成区域由程序维护，人工分析写在区域以外。每个实体使用稳定 ID。\n\n新研究先检索旧结论与反证，再补查事实。初始资料是历史调研，不等于当前已验证的商机。\n', self.vault)

    def note_path(self, record):
        # IDs determine identity. Also find a note the user renamed in Obsidian.
        default = self.vault / FOLDERS[record['kind']] / (record['id'] + '.md')
        if default.exists(): return default
        marker = re.compile(r'^id:\s*([\x22\x27]?)' + re.escape(record['id']) + r'\1\s*$', re.MULTILINE)
        for path in self.vault.rglob('*.md'):
            if path.is_symlink(): continue
            try:
                checked_path(path, self.vault)
                with path.open(encoding='utf-8') as stream:
                    if marker.search(stream.read(2000)): return path
            except (OSError, UnicodeError, ValueError): pass
        return default

    def write_note(self, record):
        begin, end = '<!-- MARKETINTEL:BEGIN -->', '<!-- MARKETINTEL:END -->'
        with self.lock:
            path = self.note_path(record)
            checked_path(path, self.vault)
            old = path.read_text(encoding='utf-8') if path.exists() else ''
            metadata = {'id': record['id'], 'type': record['kind'], 'title': record['name'], 'updated': record['updated'], 'project_id': record['project_id']}
            front = '---\n' + '\n'.join(k + ': ' + json.dumps(v, ensure_ascii=False) for k, v in metadata.items()) + '\n---\n'
            lines = ['# ' + record['name'], '', '类型：' + LABELS[record['kind']], '']
            for k, v in record['data'].items():
                if k in ['actor_input']: continue
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False, indent=2)
                lines.append('## ' + k + '\n\n' + (str(v) if v is not None and v != '' else '未知 / 待补证') + '\n')
            links = [record['project_id']] + record['data'].get('evidence_ids', [])
            if record['data'].get('opportunity_id'): links.append(record['data']['opportunity_id'])
            if any(links):
                lines.append('## 关联档案\n\n' + '\n'.join('- [[' + x + ']]' for x in links if x))
            machine = begin + '\n\n' + '\n'.join(lines) + '\n' + end
            if old and (begin not in old or end not in old):
                conflict = self.vault / '00-Index' / (record['id'] + '-待合并.md')
                write_local_text(conflict, front + machine, self.vault)
                return 'conflict'
            if old:
                pre, rest = old.split(begin, 1); _, post = rest.split(end, 1)
                # Preserve user prose outside the generated region and metadata.
                if pre.startswith('---\n') and '\n---\n' in pre:
                    yaml, pre = pre[4:].split('\n---\n', 1)
                    extra = [line for line in yaml.splitlines() if not any(line.startswith(k + ':') for k in metadata)]
                    if extra:
                        front = front[:-4] + '\n'.join(extra) + '\n---\n'
                content = front + pre + machine + post
            else:
                content = front + '\n' + machine + '\n\n## 人工分析\n\n在这里记录你的判断、补充与疑问。此区域不会被系统摘要覆盖。\n'
            if path.exists() and path.read_text(encoding='utf-8') != old:
                return 'conflict'
            write_local_text(path, content, self.vault)
            return 'synced'

    def sync_vault(self):
        result = {'synced': 0, 'conflict': 0}
        for record in self.list(): result[self.write_note(record)] += 1
        return result

    def search(self, query, project_id=None):
        query = query.strip()[:200]
        if not query: return []
        terms = [x.lower() for x in re.findall(r'[a-zA-Z0-9_-]+|[\u4e00-\u9fff]+', query)]
        if not terms: return []
        hits = []
        for path in self.vault.rglob('*.md'):
            if path.is_symlink(): continue
            try:
                checked_path(path, self.vault)
                with path.open(encoding='utf-8') as stream: text = stream.read(1_000_001)
                if len(text) > 1_000_000: continue
            except (OSError, UnicodeError, ValueError): continue
            if project_id and not re.search(r'^(?:project_id|id):\s*([\x22\x27]?)' + re.escape(project_id) + r'\1\s*$', text, re.MULTILINE): continue
            if not all(term in text.lower() for term in terms): continue
            score = sum(text.lower().count(term) for term in terms)
            if score:
                position = min([text.lower().find(t) for t in terms if t in text.lower()])
                hits.append({'path': str(path.relative_to(self.vault)), 'score': score,
                             'excerpt': text[max(0, position - 100):position + 600], 'text': text[:30_000]})
        return sorted(hits, key=lambda x: -x['score'])[:40]

    def export(self):
        return {'format': 'marketintel-1', 'exported_at': now(), 'records': self.list(), 'snapshots': self.snapshots(limit=None), 'changes': self.changes(limit=None)}

    def report(self, project_id=None):
        records = self.list(project_id=project_id)
        if project_id:
            project = self.get(project_id)
            if project: records.insert(0, project)
        lines = ['# MarketIntel 研究报告', '', '导出时间：' + now(), '', '公开证据与实际成交分开；未知数据不作为零。', '']
        for kind in ['project', 'opportunity', 'competitor', 'evidence', 'experiment', 'decision', 'research']:
            subset = [r for r in records if r['kind'] == kind]
            if not subset: continue
            lines += ['## ' + LABELS[kind], '']
            for r in subset:
                lines += ['### ' + r['name'], '', 'ID：' + r['id'], '']
                for key, value in r['data'].items():
                    if key in ['actor_input']: continue
                    lines += ['**' + key + '**：' + (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value) if value is not None else '未知'), '']
                if kind == 'opportunity': lines += ['评估：' + json.dumps(self.assessment(r), ensure_ascii=False), '']
        return '\n'.join(lines)

    def backup(self):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6]
        dest = checked_path(self.root / 'backups' / stamp, self.root); dest.mkdir(parents=True, mode=0o700)
        if os.name != 'nt': dest.parent.chmod(0o700)
        with self.lock:
            with self.connect() as source, sqlite3.connect(str(dest / 'intelligence.sqlite')) as target:
                source.backup(target)
            shutil.copytree(self.vault, dest / 'vault', symlinks=True)
            if (self.data_dir / 'raw').exists(): shutil.copytree(self.data_dir / 'raw', dest / 'raw', symlinks=True)
        return str(dest)

    def import_evidence(self, rows, project_id):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise ValueError('每次导入 1—1000 条证据')
        prepared = []
        for row in rows:
            if not isinstance(row, dict): raise ValueError('每条证据必须是对象')
            name = row.get('name') or row.get('title') or row.get('summary', '')[:100]
            data = {k: v for k, v in row.items() if k not in ['name', 'title', 'id', 'project_id']}
            data['review_status'] = 'pending'
            self.validate('evidence', name, project_id, data)
            prepared.append((name, data))
        with self.lock:
            existing = {(r['data'].get('url'), r['data'].get('summary')) for r in self.list('evidence', project_id)}
            count = skipped = 0
            for name, data in prepared:
                key = (data.get('url'), data.get('summary'))
                if key in existing: skipped += 1; continue
                self.save('evidence', name, project_id, data); existing.add(key); count += 1
        return {'imported': count, 'skipped': skipped}
