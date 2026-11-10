"""Bridge isolation, cancellation and protocol tests; no model/network access."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hermes_bridge as module
from fastapi import HTTPException
from starlette.requests import Request


class AuthTests(unittest.IsolatedAsyncioTestCase):
    def request(self, scheme='https', origin='https://monitor.example', token='valid-operator-cookie'):
        headers = [(b'host', b'monitor.example')]
        if origin:
            headers.append((b'origin', origin.encode()))
        if token:
            headers.append((b'cookie', ('watch_nanda_ops=' + token).encode()))
        return Request({'type': 'http', 'scheme': scheme, 'method': 'GET', 'path': '/',
                        'query_string': b'', 'headers': headers, 'server': ('monitor.example', 443)})

    async def test_http_and_cross_origin_rejected_before_authentication(self):
        for request in [self.request(scheme='http'), self.request(origin='https://attacker.example')]:
            with patch.object(module, 'verify_session') as verify:
                with self.assertRaises(HTTPException) as error:
                    await module.authenticate(request)
                self.assertEqual(error.exception.status_code, 403)
                verify.assert_not_called()

    async def test_cookie_required_and_backend_remains_authority(self):
        with self.assertRaises(HTTPException) as error:
            await module.authenticate(self.request(token=''))
        self.assertEqual(error.exception.status_code, 401)
        with patch.object(module, 'verify_session', side_effect=HTTPException(401, 'expired')):
            with self.assertRaises(HTTPException):
                await module.authenticate(self.request())

    async def test_validated_cookie_becomes_owner_digest(self):
        with patch.object(module, 'verify_session') as verify:
            owner, headers = await module.authenticate(self.request())
        self.assertEqual(len(owner), 64)
        self.assertNotIn('valid-operator-cookie', owner)
        verify.assert_called_once_with(headers)
        self.assertEqual(headers['X-Forwarded-Proto'], 'https')
        self.assertEqual(headers['Host'], 'monitor.example')

    async def test_bridge_uses_validation_only_not_automatic_session_creation(self):
        with patch.object(module.http.client, 'HTTPConnection') as factory:
            factory.return_value.getresponse.return_value.status = 200
            module.verify_session({'Cookie': 'watch_nanda_ops=opaque'})
            factory.return_value.request.assert_called_once_with('GET', '/api/ops/session?validate_only=true',
                headers={'Cookie': 'watch_nanda_ops=opaque'})


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = module.Bridge()
        self.item = self.bridge.create('owner-one')
        self.temp = tempfile.TemporaryDirectory()
        self.script = Path(self.temp.name) / 'hermes'
        self.script.write_text('''#!/usr/bin/env python3
import json, sys, time
payload = json.load(sys.stdin)
if payload.get('action') == 'stop':
    sys.exit(0)
prompt = payload['prompt']
if prompt == 'slow':
    print(json.dumps({'type':'text','text':'partial'}), flush=True)
    time.sleep(60)
elif prompt == 'error':
    print(json.dumps({'type':'result','exit_code':1,'error':'secret-private-key'}), flush=True)
else:
    if prompt == 'background':
        time.sleep(.25)
        from pathlib import Path
        Path('background-result.txt').write_text('done')
    print(json.dumps({'type':'tool_use','name':'terminal','input':{'secret':'hidden'}}), flush=True)
    print(json.dumps({'type':'text','text':'OK'}), flush=True)
    print(json.dumps({'type':'result','exit_code':0,'text':'OK','session_id':'local-1234567890'}), flush=True)
''')
        self.script.chmod(0o700)
        self.patches = [patch.object(module, 'CLI', str(self.script)),
                        patch.object(module, 'runtime_settings', lambda: {'enabled': True, 'revision': 1,
                            'model': 'test-model', 'api_base': 'https://api.example/v1', 'api_key': 'test-only'}),
                        patch.object(module, 'resolve_command', lambda: [str(self.script)]),
                        patch.object(module, 'WORKSPACE', self.temp.name),
                        patch.object(module, 'verify_session', lambda _: None)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        await asyncio.gather(*(self.bridge.stop(i) for i in self.bridge.conversations.values()))
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def message(self, text='hello', ident='request-1234567890'):
        return module.Message(text=text, request_id=ident)

    async def test_owner_isolation_and_session_id_not_exposed(self):
        with self.assertRaises(HTTPException) as error:
            self.bridge.get(self.item.id, 'someone-else')
        self.assertEqual(error.exception.status_code, 404)
        self.bridge.start(self.item, self.message(), {})
        await self.item.task
        self.assertEqual(self.item.session, 'local-1234567890')
        self.assertNotIn('session', self.item.snapshot())
        self.assertNotIn('hidden', str(self.item.snapshot()))
        self.assertEqual(self.item.messages[-1]['text'], 'OK')

    async def test_idempotent_send_and_owner_concurrency(self):
        self.bridge.start(self.item, self.message('slow'), {})
        self.bridge.start(self.item, self.message('slow'), {})
        self.assertEqual(len(self.item.messages), 2)
        other = self.bridge.create('owner-one')
        with self.assertRaises(HTTPException) as error:
            self.bridge.start(other, self.message(), {})
        self.assertEqual(error.exception.status_code, 409)

    async def test_stop_before_task_started_is_terminal(self):
        self.bridge.start(self.item, self.message('slow'), {})
        await self.bridge.stop(self.item)
        self.assertEqual(self.item.status, 'stopped')
        self.assertIsNone(self.item.process)

    async def test_stop_terminates_live_process_and_retains_partial(self):
        self.bridge.start(self.item, self.message('slow'), {})
        for _ in range(100):
            if self.item.messages[-1]['text']:
                break
            await asyncio.sleep(.02)
        process = self.item.process
        self.assertIsNotNone(process)
        await self.bridge.stop(self.item)
        self.assertIsNotNone(process.returncode)
        self.assertEqual(self.item.status, 'stopped')
        self.assertEqual(self.item.messages[-1]['text'], 'partial')

    async def test_expired_auth_terminates_running_agent(self):
        with patch.object(module, 'verify_session', side_effect=HTTPException(401, 'expired')):
            self.bridge.start(self.item, self.message('slow'), {})
            await asyncio.wait_for(self.item.task, 8)
        self.assertEqual(self.item.status, 'stopped')
        self.assertIn('失效', self.item.error)
        self.assertIsNone(self.item.process)

    async def test_upstream_errors_are_redacted(self):
        self.bridge.start(self.item, self.message('error'), {})
        await self.item.task
        self.assertEqual(self.item.status, 'error')
        self.assertNotIn('secret-private-key', str(self.item.snapshot()))

    async def test_disabled_or_changed_configuration_stops_background_generation(self):
        for settings in ({'enabled': False, 'revision': 1}, {'enabled': True, 'revision': 2}):
            item = self.bridge.create('config-owner')
            self.bridge.start(item, self.message('slow'), {})
            await asyncio.sleep(.1)
            with patch.object(module, 'runtime_settings', return_value=settings):
                await asyncio.wait_for(item.task, 8)
            self.assertEqual(item.status, 'stopped')
            self.assertIsNone(item.process)
            self.assertNotIn('api_key', item.config)

    async def test_disabled_assistant_rejects_new_work_and_changed_provider_requires_new_conversation(self):
        with patch.object(module, 'runtime_settings', return_value={'enabled': False}):
            with self.assertRaises(HTTPException) as error:
                self.bridge.start(self.item, self.message(), {})
            self.assertEqual(error.exception.status_code, 503)
        self.bridge.start(self.item, self.message(), {})
        await self.item.task
        with patch.object(module, 'runtime_settings', return_value={'enabled': True, 'revision': 2}):
            with self.assertRaises(HTTPException) as error:
                self.bridge.start(self.item, self.message(ident='request-second-123'), {})
            self.assertEqual(error.exception.status_code, 409)

    async def test_stop_retry_does_not_cancel_native_cleanup_in_progress(self):
        entered, release = asyncio.Event(), asyncio.Event()
        terminate = self.bridge.terminate
        async def cleanup(item):
            entered.set()
            await release.wait()
            await terminate(item)
        with patch.object(self.bridge, 'terminate', side_effect=cleanup):
            self.bridge.start(self.item, self.message('slow'), {})
            await asyncio.sleep(.1)
            first = asyncio.create_task(self.bridge.stop(self.item))
            await entered.wait()
            second = asyncio.create_task(self.bridge.stop(self.item))
            await asyncio.sleep(.02)
            self.assertFalse(second.done())
            release.set()
            await asyncio.gather(first, second)

    async def test_capacity_and_input_limits(self):
        self.bridge.start(self.item, self.message('slow'), {})
        second = self.bridge.create('owner-two')
        self.bridge.start(second, self.message('slow'), {})
        third = self.bridge.create('owner-three')
        with self.assertRaises(HTTPException) as error:
            self.bridge.start(third, self.message(), {})
        self.assertEqual(error.exception.status_code, 429)
        with self.assertRaises(ValueError):
            self.message('x' * 8001)

    async def test_background_work_finishes_without_any_snapshot_requests(self):
        # After the submission response, no browser polling or open panel is
        # required for the server-owned task to perform its work and finish.
        self.bridge.start(self.item, self.message('background'), {})
        await asyncio.wait_for(self.item.task, 5)
        self.assertEqual((Path(self.temp.name) / 'background-result.txt').read_text(), 'done')
        self.assertEqual(self.item.status, 'idle')
        self.assertEqual(self.item.messages[-1]['text'], 'OK')
        self.assertIsNone(self.item.process)

    async def test_background_deadline_still_terminates_task(self):
        with patch.object(module, 'TURN_TIMEOUT', .05):
            self.bridge.start(self.item, self.message('slow'), {})
            await asyncio.wait_for(self.item.task, 8)
        self.assertEqual(self.item.status, 'error')
        self.assertIn('超时', self.item.error)
        self.assertIsNone(self.item.process)


if __name__ == '__main__':
    unittest.main()
