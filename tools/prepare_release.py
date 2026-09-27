"""Build a source-only archive from an explicit allowlist, never from runtime data."""
import argparse
import json
import re
import zipfile
from pathlib import Path


TOP = ['app.py', 'start.command', 'start.bat', 'README.md', 'LICENSE', '.gitignore', '.env.example', 'SECURITY.md', 'CONTRIBUTING.md']
DOCUMENTS = ['docs/接口与来源.md', 'docs/开源发布指南.md', 'docs/安全回归报告.md']
TESTS = ['tests/test_system.py', 'tests/test_release.py', 'tests/test_security.py', 'tests/browser_smoke.cjs']
PATTERNS = [r'sk-(?:or-v1-)?[A-Za-z0-9_-]{20,}', r'gh[pousr]_[A-Za-z0-9]{20,}', r'apify_api_[A-Za-z0-9]{12,}']


def release_files(root):
    files = [root / name for name in TOP + DOCUMENTS + TESTS]
    files += list((root / 'marketintel').glob('*.py'))
    files += [root / 'web' / name for name in ['index.html', 'app.js', 'style.css', 'tokens.css']]
    files += [root / 'tools' / 'prepare_release.py', root / '.github' / 'workflows' / 'tests.yml']
    for path in files:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('缺失或不安全的发布文件：' + str(path.relative_to(root)))
        content = path.read_text(encoding='utf-8')
        personal_paths = ['/' + 'Users' + '/', '/' + 'home' + '/']
        if any(value in content for value in personal_paths) or any(re.search(pattern, content) for pattern in PATTERNS):
            raise ValueError('发布检查发现个人路径或疑似凭据，需人工核查：' + str(path.relative_to(root)))
    for line in (root / '.env.example').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if value.strip(): raise ValueError('.env.example 必须保持空值')
    return sorted(set(files))


def build(root, output):
    files = release_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files: archive.write(path, 'MarketIntel/' + path.relative_to(root).as_posix())
    return {'files': len(files), 'archive': str(output), 'excluded': ['credentials', 'database', 'vault', 'raw pages', 'backups', 'personal reports', 'browser artifacts']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成只含开源代码的发布包')
    parser.add_argument('--output', default='release/marketintel-source.zip')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    print(json.dumps(build(root, Path(args.output).resolve()), ensure_ascii=False, indent=2))
