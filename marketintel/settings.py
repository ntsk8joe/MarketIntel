"""Per-installation credentials. Never return secret values to the browser."""
import base64
import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote


FIELDS = ('APIFY_TOKEN', 'DATAFORSEO_LOGIN', 'DATAFORSEO_PASSWORD', 'OPENROUTER_API_KEY', 'OPENROUTER_MODEL')


def read_environment_file(path):
    values = {}
    if not path.is_file(): return values
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if key in FIELDS:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ['"', "'"]: value = value[1:-1]
            values[key] = value
    return values


class Connections:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'config' / 'connections.json'
        self.lock = threading.RLock()
        self.legacy = read_environment_file(self.root / '.env')
        self.values = {}
        self.check_path()
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.validate(data)
            self.values = data
            if os.name == 'posix': os.chmod(self.path, 0o600)

    def check_path(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('连接配置不能使用符号链接')

    @staticmethod
    def validate(values):
        if not isinstance(values, dict) or any(k not in FIELDS for k in values):
            raise ValueError('连接配置字段无效')
        if any(not isinstance(v, str) or len(v) > 4096 or any(c in v for c in ['\n', '\r', '\x00']) for v in values.values()):
            raise ValueError('连接值必须为单行文字，长度不超过 4096 字符')

    def get(self, key):
        with self.lock:
            # Explicit empty values disable a connection even when .env exists.
            return self.values.get(key, os.environ.get(key, self.legacy.get(key, '')))

    def status(self):
        fields = {key: bool(self.get(key)) for key in FIELDS}
        return {'configured': fields, 'model': self.get('OPENROUTER_MODEL'),
                'connections': {'apify': fields['APIFY_TOKEN'],
                 'dataforseo': fields['DATAFORSEO_LOGIN'] and fields['DATAFORSEO_PASSWORD'],
                 'ai': fields['OPENROUTER_API_KEY'] and fields['OPENROUTER_MODEL']}}

    def update(self, values):
        self.validate(values)
        with self.lock:
            self.check_path()
            merged = {**self.values, **{k: v.strip() for k, v in values.items()}}
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name == 'posix': os.chmod(self.path.parent, 0o700)
            handle, temporary = tempfile.mkstemp(prefix='.connections-', dir=str(self.path.parent))
            try:
                with os.fdopen(handle, 'w', encoding='utf-8') as file:
                    json.dump(merged, file, ensure_ascii=False, indent=2)
                    file.flush(); os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary): os.unlink(temporary)
            self.values = merged
            return self.status()

    def redact(self, message):
        secrets = [self.get(key) for key in FIELDS if key != 'OPENROUTER_MODEL']
        login, password = self.get('DATAFORSEO_LOGIN'), self.get('DATAFORSEO_PASSWORD')
        if login and password: secrets.append(base64.b64encode((login + ':' + password).encode()).decode())
        for value in sorted(set(secrets), key=len, reverse=True):
            if value:
                message = message.replace(value, '[redacted]').replace(quote(value, safe=''), '[redacted]')
        return message
