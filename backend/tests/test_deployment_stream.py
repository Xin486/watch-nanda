"""Deployment subscription authentication, snapshots and disconnects without SSH."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
import test_operations as existing
from core import deployment_stream as feed, operations as ops, agent_batch as batch


class DeploymentStreamTests(unittest.TestCase):
    setUp = existing.OperationsTests.setUp
    tearDown = existing.OperationsTests.tearDown
    client = existing.OperationsTests.client
    login = existing.OperationsTests.login

    def test_job_feed_is_authenticated_read_only_and_returns_final_snapshot(self):
        job = ops.reserve_deployment(1)
        ops.append_log(job, 'safe log', 'not-in-log')
        job.update(status='success', message='ready')
        with self.client() as client, patch.object(ops, 'connect_ssh') as connect, patch.object(ops.workers, 'submit') as submit:
            url = f"/api/ops/jobs/{job['id']}/stream"
            self.assertEqual(client.get(url).status_code, 401)
            self.login(client)
            self.assertEqual(client.get(url, headers={'origin': 'https://evil.example'}).status_code, 403)
            result = client.get(url)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers['x-accel-buffering'], 'no')
            self.assertEqual(result.headers['cache-control'], 'no-store')
            self.assertIn('text/event-stream', result.headers['content-type'])
            frames = result.text.splitlines()
            self.assertEqual(frames[0], 'event: snapshot')
            self.assertEqual(json.loads(frames[1][6:])['logs'], ['safe log'])
            self.assertEqual(client.get('/api/ops/jobs/missing/stream').status_code, 404)
            connect.assert_not_called(); submit.assert_not_called()

    def test_batch_snapshot_sends_only_selected_or_running_logs_and_preserves_waiting(self):
        entry = {'id': 'feed-batch', 'created_at': 1, 'cancelled': False, 'coordinator': False,
                 'items': [{'server_id': 1, 'hostname': 'first', 'status': 'needs_trust', 'message': 'confirm',
                            '_identity': ('private-host', 22), 'logs': ['first-log'], 'job_id': 'old'},
                           {'server_id': 2, 'hostname': 'second', 'status': 'running', 'message': 'install',
                            '_identity': ('private-host-2', 22), 'logs': [], 'job_id': 'active'}]}
        batch.batches[entry['id']] = entry
        ops.jobs['active'] = {'logs': ['live-log']}
        try:
            with self.client() as client, patch.object(batch, 'kick') as kick:
                root = '/api/ops/agent-batches/feed-batch'
                self.assertEqual(client.get(root+'/snapshot').status_code, 401)
                self.login(client)
                live = client.get(root+'/snapshot').json()
                self.assertEqual(live['live_log']['logs'], ['live-log'])
                self.assertNotIn('first-log', str(live)); self.assertNotIn('_identity', str(live))
                selected = client.get(root+'/snapshot?log_node=1').json()
                self.assertEqual(selected['live_log']['logs'], ['first-log'])
                self.assertNotIn('live-log', str(selected))
                self.assertEqual(client.get(root+'/stream?log_node=999').status_code, 404)
                entry['items'][1].update(status='success', logs=['final-log'])
                self.assertEqual(batch.live_snapshot(entry['id'])['batch']['status'], 'waiting_input')
                entry['items'][0]['status'] = 'cancelled'
                result = client.get(root+'/stream')
                self.assertEqual(result.status_code, 200); self.assertIn('final-log', result.text)
                # A retry of an earlier row is the latest activity, not the last
                # row in the original selection order.
                ops.jobs['old'] = {'created_at': 50}
                ops.jobs['active']['created_at'] = 10
                self.assertEqual(batch.live_snapshot(entry['id'])['live_log']['server_id'], 1)
                kick.assert_not_called()
        finally:
            batch.batches.clear()

    def test_changes_are_pushed_heartbeats_are_sent_and_auth_is_rechecked(self):
        async def check():
            request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
            snapshot = {'status': 'running', 'logs': []}
            with patch.object(feed.ops_auth, 'authorized') as auth, patch.object(feed, 'INTERVAL', 0), patch.object(feed, 'HEARTBEAT', 0):
                stream = feed.events(request, lambda: snapshot.copy(), lambda data: data['status'] == 'success')
                self.assertIn('event: snapshot', await anext(stream))
                self.assertIn('event: heartbeat', await anext(stream))
                snapshot['logs'] = ['new output']
                self.assertIn('new output', await anext(stream))
                auth.side_effect = HTTPException(401, 'session expired')
                self.assertIn('event: feed-error', await anext(stream))
                with self.assertRaises(StopAsyncIteration): await anext(stream)
        asyncio.run(check())

    def test_client_disconnect_does_not_read_snapshot_or_touch_task(self):
        async def check():
            request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
            with patch.object(feed.ops_auth, 'authorized') as auth:
                stream = feed.events(request, lambda: self.fail('disconnected client read a snapshot'), lambda _: False)
                with self.assertRaises(StopAsyncIteration): await anext(stream)
                auth.assert_not_called()
        asyncio.run(check())
