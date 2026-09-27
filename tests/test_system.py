import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from marketintel.core import Store, economics, number
from marketintel.collectors import Collector, extract, public_fetch, validate_public_url
from marketintel.research import discover, keyword_metrics, ai_brief
from marketintel.seed import seed
from marketintel.server import serve
from marketintel.settings import Connections, read_environment_file


class LocalCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.project = self.store.save('project', '测试方向', data={'task': '测试任务'})

    def tearDown(self):
        self.temp.cleanup()

    def source(self, **values):
        return self.store.save('source', '测试来源', self.project['id'],
                               dict(url='https://example.com/pricing', capture_allowed=True, **values))

    def opportunity(self, **values):
        return self.store.save('opportunity', '测试机会', self.project['id'], values)

    def trial(self, opportunity, **values):
        return self.store.save('experiment', '测试验证', self.project['id'],
                               dict(opportunity_id=opportunity['id'], **values))


class EconomicsTests(unittest.TestCase):
    def test_missing_is_unknown_not_zero(self):
        self.assertIsNone(economics({'price': 10})['profit'])
        self.assertEqual(economics({k: 0 for k in ['price', 'variable', 'fixed', 'acquisition', 'customers']})['profit'], 0)

    def test_scenario_and_break_even(self):
        result = economics(dict(price=20, variable=5, fixed=100, acquisition=40, customers=10))
        self.assertEqual(result['profit'], 10)
        self.assertEqual(result['break_even'], 10)
        self.assertEqual(result['status'], 'scenario')

    def test_nonfinite_negative_boolean_and_fractional_customers(self):
        for value in ['nan', float('inf'), -1, True, 'x']:
            with self.subTest(value=value), self.assertRaises(ValueError): number(value, '费用')
        with self.assertRaises(ValueError): economics({'customers': 0.5})


class StorageTests(LocalCase):
    def test_snapshots_deduplicate_and_preserve_changes(self):
        source = self.source()
        first = self.store.add_snapshot(source, '价格 10', '<p>价格 10</p>', source['data']['url'], 'text/html')
        second = self.store.add_snapshot(source, '价格 10', 'same', source['data']['url'], 'text/html')
        third = self.store.add_snapshot(source, '价格 20', '<p>价格 20</p>', source['data']['url'], 'text/html')
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertTrue(second['unchanged'])
        self.assertTrue(third['changed'])
        self.assertEqual(len(self.store.snapshots()), 2)
        self.assertEqual(self.store.snapshot(first['snapshot_id'])['text'], '价格 10')
        change = self.store.changes()[0]
        self.assertIn('-价格 10', change['diff'])
        self.store.acknowledge(change['id'])
        self.assertEqual(self.store.changes()[0]['reviewed'], 1)

    def test_collection_does_not_overwrite_changed_source_settings(self):
        source = self.source()
        updated = dict(source['data'], interval_hours=24, tracking=True)
        self.store.save('source', source['name'], source['project_id'], updated, source['id'])
        self.store.add_snapshot(source, '正文', '正文', source['data']['url'], 'text/plain')
        self.assertEqual(self.store.get(source['id'])['data']['interval_hours'], 24)
        self.assertTrue(self.store.get(source['id'])['data']['tracking'])

    def test_vault_preserves_manual_prose_custom_properties_and_renames(self):
        record = self.project
        path = self.store.note_path(record)
        text = path.read_text().replace('id: "' + record['id'] + '"', 'id: ' + record['id']).replace('---\n', '---\ntags:\n  - investigation\npriority: high\n', 1) + '\n人工结论：不要重复研究。\n'
        path.write_text(text)
        renamed = path.with_name('我自己的市场笔记.md'); path.rename(renamed)
        self.store.save('project', '更新标题', data={'task': '另一任务'}, identifier=record['id'])
        new = renamed.read_text()
        self.assertIn('人工结论：不要重复研究。', new)
        self.assertIn('tags:\n  - investigation\npriority: high', new)
        self.assertIn('# 更新标题', new)
        self.assertFalse(path.exists())
        self.assertTrue(self.store.search('重复研究'))
        self.assertTrue(self.store.search('重复研究', record['id']))
        self.store.sync_vault()
        self.assertEqual(renamed.read_text().count('priority: high'), 1)

    def test_missing_markers_preserve_original_and_create_merge_note(self):
        path = self.store.note_path(self.project)
        path.write_text('完全人工维护的笔记')
        saved = self.store.save('project', '新标题', data={}, identifier=self.project['id'])
        self.assertEqual(saved['vault_status'], 'conflict')
        self.assertEqual(path.read_text(), '完全人工维护的笔记')
        self.assertTrue((self.store.vault / '00-Index' / (self.project['id'] + '-待合并.md')).exists())

    def test_import_validates_whole_batch_and_forces_pending(self):
        valid = {'name': '痛点', 'url': 'https://example.com/thread', 'summary': '手工工作太多', 'review_status': 'verified'}
        with self.assertRaises(ValueError): self.store.import_evidence([valid, {'name': '坏数据', 'claim_type': 'invalid'}], self.project['id'])
        self.assertEqual(self.store.list('evidence'), [])
        self.assertEqual(self.store.import_evidence([valid, valid], self.project['id']), {'imported': 1, 'skipped': 1})
        self.assertEqual(self.store.list('evidence')[0]['data']['review_status'], 'pending')

    def test_fact_requires_source_and_snapshot_requires_same_source(self):
        with self.assertRaises(ValueError): self.store.save('evidence', '空来源事实', self.project['id'], {'claim_type': 'fact'})
        source = self.source()
        snap = self.store.add_snapshot(source, '正文', '正文', source['data']['url'], 'text/plain')
        with self.assertRaises(ValueError): self.store.save('evidence', '错误快照', self.project['id'], {'snapshot_id': snap['snapshot_id']})

    def test_paid_and_repeat_stages_require_actual_records(self):
        opp = self.opportunity(stage='pilot')
        with self.assertRaises(ValueError): self.store.save('opportunity', opp['name'], opp['project_id'], {'stage': 'paid'}, opp['id'])
        with self.assertRaises(ValueError): self.trial(opp, payment=30, currency='USD')
        self.trial(opp, payment=30, date='2026-09-27', currency='USD')
        self.store.save('opportunity', opp['name'], opp['project_id'], {'stage': 'paid'}, opp['id'])
        with self.assertRaises(ValueError): self.store.save('opportunity', opp['name'], opp['project_id'], {'stage': 'repeat'}, opp['id'])
        self.trial(opp, payment=20, date='2026-09-28', currency='USD', repeat=True)
        self.store.save('opportunity', opp['name'], opp['project_id'], {'stage': 'repeat'}, opp['id'])

    def test_cost_unknown_and_currencies_never_added_together(self):
        opp = self.opportunity()
        self.trial(opp, payment=30, currency='USD', date='2026-09-27')
        self.assertEqual(self.store.assessment(opp)['trial_margin_by_currency'], {})
        self.trial(opp, payment=40, cost=10, currency='USD', date='2026-09-27')
        self.trial(opp, payment=100, cost=50, currency='EUR', date='2026-09-27')
        self.assertEqual(self.store.assessment(opp)['trial_margin_by_currency'], {'USD': 30, 'EUR': 50})

    def test_cannot_erase_last_payment_basis_of_paid_stage(self):
        opp = self.opportunity(stage='pilot')
        trial = self.trial(opp, payment=30, currency='USD', date='2026-09-27')
        self.store.save('opportunity', opp['name'], opp['project_id'], {'stage': 'paid'}, opp['id'])
        with self.assertRaises(ValueError): self.store.save('experiment', trial['name'], trial['project_id'], dict(trial['data'], payment=0), trial['id'])
        self.store.save('opportunity', opp['name'], opp['project_id'], {'stage': 'pilot'}, opp['id'])
        self.store.save('experiment', trial['name'], trial['project_id'], dict(trial['data'], payment=0), trial['id'])

    def test_assessment_counts_only_approved_and_groups_same_thread(self):
        evidence = []
        for state, direction in [('verified', 'support'), ('verified', 'counter'), ('pending', 'counter'), ('rejected', 'counter')]:
            evidence.append(self.store.save('evidence', '测试陈述', self.project['id'],
                            {'url': 'https://example.com/thread', 'summary': '测试陈述', 'review_status': state, 'direction': direction}))
        opp = self.opportunity(evidence_ids=[x['id'] for x in evidence])
        a = self.store.assessment(opp)
        self.assertEqual((a['approved'], a['independent_threads'], a['counter'], a['counter_pending']), (2, 1, 1, 1))

    def test_cross_project_links_and_invalid_types_rejected(self):
        p = self.store.save('project', '另一方向')
        evidence = self.store.save('evidence', '其他证据', p['id'], {})
        with self.assertRaises(ValueError): self.opportunity(evidence_ids=[evidence['id']])
        with self.assertRaises(ValueError): self.opportunity(evidence_ids='not-a-list')
        with self.assertRaises(ValueError): self.store.import_evidence([{'name': '坏摘要', 'summary': {}}], self.project['id'])

    def test_backup_is_readable_and_contains_manual_notes_and_raw(self):
        source = self.source()
        self.store.add_snapshot(source, '正文', '原始正文', source['data']['url'], 'text/plain')
        (self.store.vault / '自己的笔记.md').write_text('人工判断')
        backup = Path(self.store.backup())
        with sqlite3.connect(backup / 'intelligence.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 1)
        self.assertEqual((backup / 'vault' / '自己的笔记.md').read_text(), '人工判断')
        self.assertEqual(len(list((backup / 'raw').rglob('*.txt'))), 1)

    def test_seed_is_idempotent_pending_and_has_no_sales(self):
        seed(self.store); count = len(self.store.list()); seed(self.store)
        self.assertEqual(len(self.store.list()), count)
        self.assertTrue(all(x['data']['review_status'] == 'pending' for x in self.store.list('evidence')))
        self.assertEqual(self.store.list('experiment'), [])
        self.assertTrue(all(not x['data']['tracking'] for x in self.store.list('source')))

    def test_startup_marks_interrupted_job_and_retains_remote_id(self):
        source = self.source(); job = self.store.job(source['id'], 'running', remote_id='existing-run')
        reopened = Store(self.temp.name)
        row = next(x for x in reopened.jobs() if x['id'] == job)
        self.assertEqual((row['status'], row['remote_id']), ('interrupted', 'existing-run'))


class CollectorTests(LocalCase):
    def test_private_addresses_and_dns_rebinding_are_blocked(self):
        for url in ['http://127.0.0.1', 'http://10.0.0.1', 'http://[::1]', 'http://localhost', 'file:///tmp/a', 'https://example.com:8080', 'https://user:pass@example.com']:
            with self.subTest(url=url), self.assertRaises(ValueError): validate_public_url(url, resolve=False)
        with patch('marketintel.collectors.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(ValueError): validate_public_url('https://public-looking.example')

    def test_html_and_feed_extraction_skip_scripts(self):
        html = '<script>unsafe code</script><h1>价格</h1><p>' + '真实内容 ' * 40 + '</p>'
        self.assertNotIn('unsafe code', extract(html, 'web', 'text/html'))
        self.assertIn('条目', extract('<rss><channel><item><title>条目</title><link>https://example.com/a</link></item></channel></rss>', 'rss', 'text/xml'))
        with self.assertRaises(ValueError): extract('<html><script>dynamic</script></html>', 'web', 'text/html')

    def test_collector_records_failure_and_never_marks_evidence_verified(self):
        source = self.source(); collector = Collector(self.store); job = self.store.job(source['id'])
        with patch('marketintel.collectors.public_fetch', side_effect=ValueError('来源不可用')):
            collector.run(source, job)
        self.assertEqual(self.store.jobs()[0]['status'], 'error')
        self.assertEqual(self.store.list('evidence'), [])

    def test_successful_collection_saves_snapshot_and_run(self):
        source = self.source(); collector = Collector(self.store); job = self.store.job(source['id'])
        with patch('marketintel.collectors.public_fetch', return_value=('测试公开正文', source['data']['url'], 'text/plain')):
            collector.run(source, job)
        self.assertEqual(self.store.jobs()[0]['status'], 'done')
        self.assertEqual(len(self.store.list('research')), 1)
        self.assertEqual(self.store.list('evidence'), [])

    def test_apify_resume_queries_existing_run_without_starting_new_paid_run(self):
        source = self.store.save('source', '测试 Actor', self.project['id'], {'type': 'apify', 'actor_id': 'test/actor', 'actor_input': {}, 'max_charge': 1, 'allow_apify': True})
        job = self.store.job(source['id'], remote_id='original-run')
        def fixture(path, token, payload=None):
            self.assertIsNone(payload)
            if path.startswith('actor-runs/'):
                return {'data': {'id': 'original-run', 'status': 'SUCCEEDED', 'defaultDatasetId': 'dataset', 'usageTotalUsd': 0.1}}
            return [{'text': '模拟接口测试数据'}]
        with patch('marketintel.collectors.apify_request', side_effect=fixture), patch.dict(os.environ, {'APIFY_TOKEN': 'test-only'}):
            Collector(self.store).run(source, job, 'original-run')
        result = self.store.jobs()[0]
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['remote_id'], 'original-run')
        self.assertEqual(result['result']['cost_usd'], 0.1)

    def test_apify_automatic_tracking_is_rejected(self):
        with self.assertRaises(ValueError): self.store.save('source', 'Actor', self.project['id'], {'type': 'apify', 'actor_id': 'a/b', 'max_charge': 1, 'tracking': True})

    def test_failed_apify_run_retains_returned_cost(self):
        source = self.store.save('source', '失败 Actor', self.project['id'], {'type': 'apify', 'actor_id': 'test/actor', 'max_charge': 1})
        job = self.store.job(source['id'], remote_id='original-run')
        with patch('marketintel.collectors.apify_request', return_value={'data': {'status': 'FAILED', 'usageTotalUsd': 0.2}}):
            Collector(self.store).run(source, job, 'original-run')
        self.assertEqual(self.store.jobs()[0]['status'], 'error')
        self.assertEqual(self.store.jobs()[0]['result']['cost_usd'], 0.2)


class ResearchTests(LocalCase):
    def test_invalid_keyword_is_rejected_before_paid_call(self):
        with patch.dict(os.environ, {'DATAFORSEO_LOGIN': 'test', 'DATAFORSEO_PASSWORD': 'secret'}), patch('marketintel.research.authenticated_json') as call:
            with self.assertRaises(ValueError): keyword_metrics(self.store, self.project['id'], ['x' * 81])
            call.assert_not_called()

    def test_discovery_saves_original_links_pending_and_searches_old_notes_first(self):
        self.store.save('research', 'old task', self.project['id'], {'summary': 'csv task history'})
        fixture = {'hits': [{'objectID': '123', 'story_id': '120', 'title': 'csv task', 'comment_text': '模拟测试讨论'}]}
        with patch('marketintel.research.public_fetch', return_value=(json.dumps(fixture), 'https://hn.algolia.com/', 'application/json')):
            result = discover(self.store, self.project['id'], 'csv', 'hackernews')
        e = self.store.list('evidence')[0]
        self.assertEqual(e['data']['url'], 'https://news.ycombinator.com/item?id=123')
        self.assertEqual(e['data']['review_status'], 'pending')
        self.assertEqual(len(result['history_matches']), 1)
        self.assertNotIn(e['id'], result['history_matches'][0])

    def test_optional_paid_interfaces_save_returned_metrics_and_pending_ai(self):
        with patch.dict(os.environ, {'DATAFORSEO_LOGIN': 'test', 'DATAFORSEO_PASSWORD': 'secret'}), patch('marketintel.research.authenticated_json', return_value={'tasks': [{'status_code': 20000, 'cost': 0.01, 'result': [{'keyword': 'test', 'search_volume': 10}]}]}):
            result = keyword_metrics(self.store, self.project['id'], ['test'])
            record = self.store.get(result['research_id'])
            self.assertEqual(record['data']['metrics'][0]['search_volume'], 10)
            self.assertEqual(record['data']['cost_usd'], 0.01)
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test', 'OPENROUTER_MODEL': 'test-model'}), patch('marketintel.research.authenticated_json', return_value={'choices': [{'message': {'content': '模拟模型推断'}}], 'usage': {'total_tokens': 10}}):
            result = ai_brief(self.store, self.project['id'], '下一步是什么')
            self.assertEqual(self.store.get(result['research_id'])['data']['review_status'], 'pending')

    def test_environment_file_only_allows_connection_keys_no_execution(self):
        path = Path(self.temp.name) / '.env'
        path.write_text('APIFY_TOKEN="literal-$(echo unsafe)"\nHOME=wrong\nOPENROUTER_MODEL=test/model\n')
        with patch.dict(os.environ, {}, clear=True):
            values = read_environment_file(path)
            self.assertEqual(values['APIFY_TOKEN'], 'literal-$(echo unsafe)')
            self.assertNotIn('HOME', values)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = serve(cls.temp.name, 0, start_scheduler=False, load_demo=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.state = cls.request('/api/state')[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.temp.cleanup()

    @classmethod
    def request(cls, path, body=None, headers=None):
        request = Request(cls.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers or {})
        try:
            with urlopen(request, timeout=5) as response:
                text = response.read().decode()
                return response.status, json.loads(text) if 'json' in response.headers.get('Content-Type', '') else text
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_mutation_requires_csrf_and_same_origin(self):
        body = {'kind': 'research', 'name': '测试 API', 'data': {}}
        self.assertEqual(self.request('/api/records', body)[0], 403)
        headers = {'X-MarketIntel-Token': self.state['csrf'], 'Origin': 'https://foreign.example'}
        self.assertEqual(self.request('/api/records', body, headers)[0], 403)
        headers['Origin'] = self.url
        status, saved = self.request('/api/records', body, headers)
        self.assertEqual(status, 200)
        self.assertEqual(saved['vault_status'], 'synced')

    def test_host_check_note_traversal_and_unknown_assets(self):
        self.assertEqual(self.request('/api/state', headers={'Host': 'foreign.example'})[0], 403)
        self.assertEqual(self.request('/api/note?path=../../app.py')[0], 404)
        self.assertEqual(self.request('/data/intelligence.sqlite')[0], 404)
        self.assertEqual(self.request('/')[0], 200)

    def test_bom_csv_import_and_report_export(self):
        status, result = self.request('/api/import', {'project_id': 'project-web-agency', 'format': 'csv', 'text': '\ufeffname,url,summary\nAPI 测试证据,https://example.com/test,模拟测试陈述\n'}, {'X-MarketIntel-Token': self.state['csrf']})
        self.assertEqual(status, 200)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(self.request('/api/export?type=markdown')[0], 200)
        self.assertIn('API 测试证据', self.request('/api/export?type=csv')[1])

    def test_connection_api_never_returns_credentials_and_redacts_errors(self):
        secret = 'fixture-private-connection-value'
        headers = {'X-MarketIntel-Token': self.state['csrf']}
        status, body = self.request('/api/connections', {'values': {'DATAFORSEO_LOGIN': 'fixture-login', 'DATAFORSEO_PASSWORD': secret}}, headers)
        self.assertEqual(status, 200)
        self.assertTrue(body['connections']['dataforseo'])
        self.assertNotIn(secret, json.dumps(body))
        self.assertNotIn(secret, json.dumps(self.request('/api/state')[1]))
        self.assertNotIn(secret, json.dumps(self.request('/api/export')[1]))
        with patch('marketintel.server.keyword_metrics', side_effect=ValueError('供应商报错：' + secret)):
            status, body = self.request('/api/keywords', {'project_id': 'project-web-agency', 'keywords': ['test']}, headers)
        self.assertEqual(status, 400)
        self.assertNotIn(secret, json.dumps(body))
        self.assertIn('[redacted]', body['error'])
        self.request('/api/connections', {'values': {'DATAFORSEO_LOGIN': '', 'DATAFORSEO_PASSWORD': ''}}, headers)

    def test_new_installation_has_no_demo_or_external_report_import(self):
        with tempfile.TemporaryDirectory() as root:
            server = serve(root, 0, start_scheduler=False)
            try:
                self.assertEqual(server.store.list(), [])
                self.assertEqual(server.store.snapshots(), [])
            finally: server.server_close()


if __name__ == '__main__': unittest.main()
