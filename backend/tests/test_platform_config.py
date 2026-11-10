"""Portable site configuration, secret redaction and installer parity."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_operations as existing
from core import platform_config as config
from core import platform_routes


class PlatformTests(unittest.TestCase):
    client = existing.OperationsTests.client
    login = existing.OperationsTests.login

    def setUp(self):
        existing.OperationsTests.setUp(self)
        folder = Path(self.temp.name)
        self.platform = folder / 'platform.json'
        self.hermes = folder / 'hermes.json'
        cli = folder / 'hermes'; cli.touch()
        p = patch.dict(os.environ, {'PLATFORM_CONFIG_FILE': str(self.platform), 'HERMES_CONFIG_FILE': str(self.hermes),
            'HERMES_CLI': str(cli), 'WATCH_NANDA_HTTPS_HOST': 'monitor.example',
            'WATCH_NANDA_FRONTEND_PORT': '3443', 'AGENT_REPORT_URL': ''})
        p.start(); self.addCleanup(p.stop)
        notify = patch.object(platform_routes, 'notify_bridge')
        notify.start(); self.addCleanup(notify.stop)

    def tearDown(self):
        existing.OperationsTests.tearDown(self)

    def test_new_site_defaults_to_itself_and_requires_a_browser_session_for_writes(self):
        self.assertEqual(config.report_url('203.0.113.10'), 'https://monitor.example:3443/api/agent/report')
        with self.client() as client:
            self.assertEqual(client.get('/api/ops/platform/agent').status_code, 401)
            self.assertFalse(client.get('/api/platform').json()['hermes']['enabled'])
            self.login(client)
            self.assertEqual(client.get('/api/ops/platform/agent').status_code, 200)

    def test_custom_endpoint_ports_cidr_priority_and_preview_use_the_same_resolver(self):
        body = {'report_url': 'monitor.example:9443', 'ca_mode': 'system', 'routes': [
            {'network': '10.0.0.0/8', 'url': 'https://inside.example:4443'},
            {'network': '10.2.0.0/16', 'url': 'https://near.example:5443'}]}
        with self.client() as client:
            self.login(client)
            response = client.put('/api/ops/platform/agent', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(config.report_url('10.2.3.4'), 'https://near.example:5443/api/agent/report')
            self.assertEqual(config.report_url('10.3.4.5'), 'https://inside.example:4443/api/agent/report')
            self.assertEqual(config.report_url('203.0.113.5'), 'https://monitor.example:9443/api/agent/report')
            self.assertEqual(client.get('/api/ops/platform/agent/report?host=10.2.3.4').json()['url'], config.report_url('10.2.3.4'))
            self.assertEqual(client.get('/api/ops/platform/agent').json()['ca_mode'], 'system')
        self.assertEqual(self.platform.stat().st_mode & 0o777, 0o600)

    def test_bad_address_or_private_key_does_not_replace_saved_settings(self):
        config.write_json(self.platform, {'agent': {'report_url': 'https://saved.example/api/agent/report',
            'routes': [], 'ca_mode': 'site'}})
        before = self.platform.read_bytes()
        with self.client() as client:
            self.login(client)
            for url in ['http://remote.example', 'https://user:pass@remote.example', 'https://remote.example:70000', 'https://remote.example/?key=private']:
                response = client.put('/api/ops/platform/agent', json={'report_url': url, 'routes': [], 'ca_mode': 'site'})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.platform.read_bytes(), before)
            response = client.put('/api/ops/platform/agent', json={'report_url': 'https://valid.example', 'routes': [],
                'ca_mode': 'custom', 'ca_certificate': '-----BEGIN PRIVATE KEY-----\nnot-a-certificate'})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(self.platform.read_bytes(), before)

    def test_model_key_is_never_returned_and_blank_preserves_until_explicit_clear(self):
        body = {'enabled': True, 'api_base': 'https://provider.example/v1', 'model': 'model-a', 'api_key': 'secret-model-key'}
        with self.client() as client:
            self.login(client)
            response = client.put('/api/ops/platform/hermes', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn('secret-model-key', response.text)
            self.assertTrue(response.json()['has_api_key'])
            self.assertNotIn('secret-model-key', client.get('/api/platform').text)
            self.assertNotIn('api_key', client.get('/api/platform').text)
            body.pop('api_key'); body['model'] = 'model-b'
            self.assertEqual(client.put('/api/ops/platform/hermes', json=body).status_code, 200)
            self.assertEqual(config.hermes_settings()['api_key'], 'secret-model-key')
            body.update(enabled=False, clear_api_key=True)
            self.assertEqual(client.put('/api/ops/platform/hermes', json=body).status_code, 200)
            self.assertFalse(client.get('/api/ops/platform/hermes').json()['has_api_key'])
            self.assertEqual(client.post('/api/ops/hermes/conversations', json={}).status_code, 503)
        self.assertEqual(self.hermes.stat().st_mode & 0o777, 0o600)

    def test_provider_errors_do_not_echo_submitted_credentials(self):
        with self.client() as client:
            self.login(client)
            response = client.put('/api/ops/platform/hermes', json={'enabled': True, 'api_base': 'https://provider.example',
                'model': 'bad\nmodel', 'api_key': 'hidden-provider-key'})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('hidden-provider-key', response.text)

    def test_changed_api_endpoint_cannot_silently_reuse_the_previous_provider_key(self):
        config.write_json(self.hermes, {'enabled': True, 'api_base': 'https://first.example/v1',
            'model': 'model-a', 'api_key': 'first-provider-secret', 'revision': 1})
        with self.client() as client:
            self.login(client)
            response = client.put('/api/ops/platform/hermes', json={'enabled': True,
                'api_base': 'https://second.example/v1', 'model': 'model-a'})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(config.hermes_settings()['api_base'], 'https://first.example/v1')
            self.assertNotIn('first-provider-secret', response.text)

    def test_cleanup_failure_is_explicit_and_retry_does_not_rotate_the_saved_revision(self):
        from fastapi import HTTPException
        body = {'enabled': False, 'api_base': 'https://provider.example/v1', 'model': 'model-a'}
        with self.client() as client:
            self.login(client)
            with patch.object(platform_routes, 'notify_bridge', side_effect=HTTPException(503, '任务停止尚未确认')):
                response = client.put('/api/ops/platform/hermes', json=body)
            self.assertEqual(response.status_code, 503)
            self.assertFalse(config.hermes_settings()['enabled'])
            revision = config.hermes_settings()['revision']
            with patch.object(platform_routes, 'notify_bridge') as notify:
                self.assertEqual(client.put('/api/ops/platform/hermes', json=body).status_code, 200)
                notify.assert_called_once()
            self.assertEqual(config.hermes_settings()['revision'], revision)


if __name__ == '__main__':
    unittest.main()
