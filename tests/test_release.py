import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from marketintel.core import Store
from marketintel.settings import Connections
from tools.prepare_release import build, release_files


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {}, clear=True); self.environment.start()

    def tearDown(self):
        self.environment.stop(); self.temp.cleanup()

    def test_persists_but_status_never_contains_secrets(self):
        c = Connections(self.root)
        status = c.update({'APIFY_TOKEN': 'fixture-apify-private-value', 'OPENROUTER_API_KEY': 'fixture-model-private-value', 'OPENROUTER_MODEL': 'example/model'})
        self.assertTrue(status['connections']['apify'])
        self.assertTrue(status['connections']['ai'])
        self.assertNotIn('fixture-', json.dumps(status))
        self.assertEqual(Connections(self.root).get('APIFY_TOKEN'), 'fixture-apify-private-value')

    def test_priority_and_explicit_clear_override_environment_after_restart(self):
        (self.root / '.env').write_text('APIFY_TOKEN=fixture-file-value\n')
        os.environ['APIFY_TOKEN'] = 'fixture-environment-value'
        c = Connections(self.root)
        self.assertEqual(c.get('APIFY_TOKEN'), 'fixture-environment-value')
        c.update({'APIFY_TOKEN': 'fixture-interface-value'})
        self.assertEqual(c.get('APIFY_TOKEN'), 'fixture-interface-value')
        c.update({'APIFY_TOKEN': ''})
        self.assertFalse(Connections(self.root).status()['connections']['apify'])
        self.assertEqual(os.environ['APIFY_TOKEN'], 'fixture-environment-value')

    def test_partial_update_preserves_others_and_installations_are_isolated(self):
        c = Connections(self.root); c.update({'APIFY_TOKEN': 'fixture-secret'})
        c.update({'OPENROUTER_MODEL': 'example/model'})
        self.assertEqual(c.get('APIFY_TOKEN'), 'fixture-secret')
        self.assertEqual(Connections(self.root / 'another-install').get('APIFY_TOKEN'), '')

    def test_invalid_update_does_not_change_existing_file(self):
        c = Connections(self.root); c.update({'APIFY_TOKEN': 'fixture-secret'})
        original = c.path.read_bytes()
        for values in [{'unknown': 'bad'}, {'APIFY_TOKEN': 123}, {'APIFY_TOKEN': 'a\nb'}, {'APIFY_TOKEN': 'x' * 4097}]:
            with self.subTest(values=list(values)), self.assertRaises(ValueError): c.update(values)
            self.assertEqual(c.path.read_bytes(), original)

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission check')
    def test_owner_only_permissions_and_symlink_rejection(self):
        c = Connections(self.root); c.update({'APIFY_TOKEN': 'fixture-secret'})
        self.assertEqual(stat.S_IMODE(c.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(c.path.parent.stat().st_mode), 0o700)
        other = self.root / 'outside.json'; other.write_text('{}')
        c.path.unlink(); c.path.symlink_to(other)
        with self.assertRaises(ValueError): Connections(self.root)

    def test_credentials_are_not_in_database_notes_export_or_backup(self):
        c = Connections(self.root); secret = 'fixture-secret-not-for-export'; c.update({'APIFY_TOKEN': secret})
        store = Store(self.root); store.save('project', '测试研究')
        self.assertNotIn(secret, json.dumps(store.export()))
        self.assertNotIn(secret, store.report())
        backup = Path(store.backup())
        self.assertFalse((backup / 'config').exists())
        self.assertTrue((backup / 'intelligence.sqlite').exists())

    def test_redaction_happens_before_response_truncation(self):
        c = Connections(self.root); secret = 'fixture-sensitive-long-value'; c.update({'APIFY_TOKEN': secret})
        value = c.redact('x' * 490 + secret)[:500]
        self.assertNotIn('fixture', value)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        project = Path(__file__).resolve().parent.parent
        for original in release_files(project):
            target = self.root / original.relative_to(project)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(original, target)

    def tearDown(self): self.temp.cleanup()

    def test_archive_excludes_all_runtime_files_even_when_present(self):
        private_data = 'private-fixture-' + uuid.uuid4().hex
        for name in ['config/connections.json', 'vault/private.md', 'data/raw/page.txt', 'backups/private.db', '.obsidian/workspace.json', '.env', '.env.backup', 'docs/live-results.json']:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(private_data)
        archive = self.root / 'release.zip'; result = build(self.root, archive)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(len(z.namelist()), result['files'])
            self.assertIn('MarketIntel/LICENSE', z.namelist())
            self.assertFalse(any(private_data in z.read(n).decode() for n in z.namelist()))
            self.assertFalse(any(n.startswith('MarketIntel/vault/') or n.startswith('MarketIntel/config/') for n in z.namelist()))

    def test_known_secret_pattern_stops_packaging_without_printing_value(self):
        value = 'sk-' + 'or-v1-' + 'a' * 40
        (self.root / 'app.py').write_text('secret = ' + repr(value))
        with self.assertRaises(ValueError) as error: release_files(self.root)
        self.assertNotIn(value, str(error.exception))
        self.assertIn('app.py', str(error.exception))

    @unittest.skipUnless(shutil.which('git'), 'Git optional for ignore verification')
    def test_gitignore_excludes_private_locations_and_keeps_empty_example(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        names = ['vault/private.md', 'config/connections.json', '.env', '.env.backup', '.obsidian/workspace.json', 'data/raw/a.txt', 'backups/a.db', 'docs/live-results.json']
        result = subprocess.run(['git', '-C', str(self.root), 'check-ignore', '--no-index', *names], capture_output=True, text=True, check=True)
        self.assertEqual(set(result.stdout.splitlines()), set(names))
        example = subprocess.run(['git', '-C', str(self.root), 'check-ignore', '--no-index', '.env.example'], capture_output=True)
        self.assertEqual(example.returncode, 1)


if __name__ == '__main__': unittest.main()
