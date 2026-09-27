"""Adversarial regression fixtures; never use real provider credentials."""
import csv
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from marketintel.collectors import Collector, apify_request
from marketintel.core import Store, economics
from marketintel.server import serve
from marketintel.settings import Connections


class StorageSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.project = self.store.save('project', 'Security fixture')

    def tearDown(self):
        self.temp.cleanup()

    def test_record_requires_object_even_when_empty(self):
        for value in [[], False, '', [('notes', 'text')]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.save('research', 'Invalid object', data=value)

    def test_nonfinite_nested_data_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save('research', 'Invalid JSON', data={'metrics': [float('nan')]})

    def test_text_and_permission_fields_require_correct_types(self):
        with self.assertRaises(ValueError):
            self.store.save('research', 'Bad analysis', data={'analysis': {}})
        with self.assertRaises(ValueError):
            self.store.save('source', 'Bad consent', data={'url': 'https://example.com', 'tracking': 'false'})

    def test_large_interval_does_not_crash_due_check(self):
        legacy = {'data': {'last_checked': '2026-01-01T00:00:00+00:00', 'interval_hours': 1e308}}
        self.assertIs(self.store.due(legacy), False)
        with self.assertRaises(ValueError):
            self.store.save('source', 'Huge interval', data={'url': 'https://example.com', 'interval_hours': 1e308})

    def test_economics_does_not_produce_infinity(self):
        with self.assertRaises(ValueError):
            economics(dict(price=1e308, customers=10, variable=0, fixed=0, acquisition=0))

    def test_duplicate_evidence_does_not_inflate_assessment(self):
        e = self.store.save('evidence', 'One statement', self.project['id'],
                            {'url': 'https://example.com', 'summary': 'One person', 'review_status': 'verified'})
        opp = self.store.save('opportunity', 'One opportunity', self.project['id'], {'evidence_ids': [e['id']] * 4})
        self.assertEqual(self.store.assessment(opp)['approved'], 1)

    def test_competitor_cannot_link_other_project_evidence(self):
        other = self.store.save('project', 'Other market')
        e = self.store.save('evidence', 'Unrelated statement', other['id'])
        with self.assertRaises(ValueError):
            self.store.save('competitor', 'Wrong association', self.project['id'], {'evidence_ids': [e['id']]})

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink fixture')
    def test_note_temporary_symlink_never_overwrites_external_file(self):
        target = Path(self.temp.name) / 'outside.txt'; target.write_text('KEEP')
        self.store.note_path(self.project).with_suffix('.md.tmp').symlink_to(target)
        self.store.save('project', 'Updated', identifier=self.project['id'])
        self.assertEqual(target.read_text(), 'KEEP')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink fixture')
    def test_conflict_symlink_never_overwrites_external_file(self):
        self.store.note_path(self.project).write_text('Manual note')
        target = Path(self.temp.name) / 'outside.txt'; target.write_text('KEEP')
        (self.store.vault / '00-Index' / (self.project['id'] + '-待合并.md')).symlink_to(target)
        try: self.store.save('project', 'Updated', identifier=self.project['id'])
        except ValueError: pass
        self.assertEqual(target.read_text(), 'KEEP')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink fixture')
    def test_raw_symlink_cannot_write_outside_data_directory(self):
        outside = Path(self.temp.name) / 'outside'; outside.mkdir()
        (self.store.data_dir / 'raw').symlink_to(outside, target_is_directory=True)
        source = self.store.save('source', 'Source', data={'url': 'https://example.com'})
        with self.assertRaises(ValueError):
            self.store.add_snapshot(source, 'Text', 'Raw', source['data']['url'], 'text/plain')
        self.assertEqual(list(outside.iterdir()), [])

    def test_bad_manual_note_does_not_break_all_search_results(self):
        (self.store.vault / 'bad.md').write_bytes(b'\xff\xfe')
        self.assertTrue(self.store.search('Security fixture'))

    def test_search_requires_all_query_terms(self):
        (self.store.vault / 'related.md').write_text('supplier variants')
        (self.store.vault / 'unrelated.md').write_text('supplier supplier supplier supplier')
        self.assertEqual([r['path'] for r in self.store.search('supplier variants')], ['related.md'])

    def test_ambiguous_paid_start_cannot_resume_as_new_charge(self):
        source = self.store.save('source', 'Actor', data={'type': 'apify', 'actor_id': 'test/actor', 'max_charge': 1, 'allow_apify': True})
        job = self.store.job(source['id'])
        with patch.dict(os.environ, {'APIFY_TOKEN': 'fixture-old-token'}), patch('marketintel.collectors.apify_request', side_effect=TimeoutError('Response lost')):
            collector = Collector(self.store); collector.run(source, job)
            with patch('marketintel.collectors.threading.Thread') as thread, self.assertRaises(ValueError):
                collector.submit(source['id'], job)
            thread.assert_not_called()

    def test_ambiguous_paid_start_requires_explicit_new_run_confirmation(self):
        source = self.store.save('source', 'Actor', data={'type': 'apify', 'actor_id': 'test/actor', 'max_charge': 1, 'allow_apify': True})
        self.store.job(source['id'], 'error', result={'start_uncertain': True})
        with patch.dict(os.environ, {'APIFY_TOKEN': 'fixture-token'}), patch('marketintel.collectors.threading.Thread') as thread:
            collector = Collector(self.store)
            with self.assertRaises(ValueError): collector.submit(source['id'])
            thread.assert_not_called()
            collector.submit(source['id'], confirmed_new=True)
            thread.assert_called_once()

    def test_export_retains_metadata_older_than_ui_limits(self):
        with self.store.connect() as db:
            db.executemany('INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?)',
                           [('snapshot-' + str(i), 'source-fixture', 'hash', '2026-01-01', 'https://example.com', 'Text', '', 'text/plain') for i in range(205)])
            db.executemany('INSERT INTO changes VALUES (?,?,?,?,?,?,0)',
                           [('change-' + str(i), 'source-fixture', 'old', 'new', '2026-01-01', 'Diff') for i in range(205)])
        self.assertEqual(len(self.store.snapshots()), 200)
        self.assertEqual(len(self.store.export()['snapshots']), 205)
        self.assertEqual(len(self.store.export()['changes']), 205)

    def test_resume_finds_run_older_than_latest_100_jobs(self):
        source = self.store.save('source', 'Actor', data={'type': 'apify', 'actor_id': 'test/actor', 'max_charge': 1, 'allow_apify': True})
        old = self.store.job(source['id'], 'error', remote_id='original-run')
        with self.store.connect() as db: db.execute("UPDATE jobs SET created='2020-01-01' WHERE id=?", (old,))
        for _ in range(101): self.store.job(source['id'], 'done')
        with patch.dict(os.environ, {'APIFY_TOKEN': 'fixture-token'}), patch('marketintel.collectors.threading.Thread') as thread:
            Collector(self.store).submit(source['id'], old)
            self.assertEqual(thread.call_args.kwargs['args'][2], 'original-run')

    def test_rotating_token_does_not_leak_inflight_token(self):
        connections = Connections(self.temp.name); connections.update({'APIFY_TOKEN': 'fixture-old-token'})
        source = self.store.save('source', 'Actor', data={'type': 'apify', 'actor_id': 'test/actor', 'max_charge': 1})
        job = self.store.job(source['id'])
        def failure(*args, **kwargs):
            connections.update({'APIFY_TOKEN': 'fixture-new-token'})
            raise ValueError('Remote error fixture-old-token')
        with patch('marketintel.collectors.apify_request', side_effect=failure):
            Collector(self.store, connections).run(source, job)
        self.assertNotIn('fixture-old-token', json.dumps(self.store.export()))
        self.assertNotIn('fixture-old-token', json.dumps(self.store.jobs()))


class HTTPSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = serve(cls.temp.name, 0, start_scheduler=False)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)
        with urlopen(cls.url + '/api/state') as response: cls.token = json.load(response)['csrf']

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    def test_csv_formulas_are_neutralized(self):
        for value in ['=1+1', '+1+1', '-1+1', '@SUM(1)', '\t=1+1', '\r=1+1', '  =1+1', '＝1+1', '＋1', '－1', '＠SUM(1)', '\ufeff=1']:
            self.server.store.save('evidence', value, data={'summary': value})
        with urlopen(self.url + '/api/export?type=csv') as response:
            rows = list(csv.DictReader(io.StringIO(response.read().decode('utf-8-sig'))))
        self.assertTrue(all(r['name'].startswith("'") and r['summary'].startswith("'") for r in rows))

    def test_unsupported_export_type_is_rejected(self):
        with self.assertRaises(HTTPError) as error: urlopen(self.url + '/api/export?type=wrong')
        self.assertEqual(error.exception.code, 400)

    def test_json_api_rejects_nan(self):
        request = Request(self.url + '/api/records', data=b'{"kind":"research","name":"Bad","data":{"metric":NaN}}',
                          headers={'X-MarketIntel-Token': self.token, 'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as error: urlopen(request)
        self.assertEqual(error.exception.code, 400)


class AuthenticatedRedirectTests(unittest.TestCase):
    def test_apify_redirect_never_forwards_authorization(self):
        received = []
        class Destination(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"data":{}}')
        target = ThreadingHTTPServer(('127.0.0.1', 0), Destination)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.send_response(302); self.send_header('Location', 'http://127.0.0.1:' + str(target.server_port)); self.end_headers()
        source = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in [target, source]]
        for thread in threads: thread.start()
        try:
            def local_request(url, **kwargs): return Request('http://127.0.0.1:' + str(source.server_port), **kwargs)
            with patch('marketintel.collectors.Request', side_effect=local_request), self.assertRaises(ValueError):
                apify_request('acts/test/runs', 'fixture-token', {})
            self.assertEqual(received, [])
        finally:
            for server in [source, target]: server.shutdown(); server.server_close()
            for thread in threads: thread.join()
