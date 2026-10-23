"""Reusable SSH secret storage and batch recovery without contacting any nodes."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import paramiko
import test_operations as existing
from core import agent_batch as batch, operations as ops, ssh_profiles
from models.models import Server, AgentCredential


class AgentBatchTests(unittest.TestCase):
    client = existing.OperationsTests.client
    login = existing.OperationsTests.login

    def setUp(self):
        existing.OperationsTests.setUp(self)
        self.profile_path = Path(self.temp.name)/'ssh-profiles.json'
        self.profile_env = patch.dict(os.environ, {'SSH_PROFILES_FILE': str(self.profile_path)})
        self.profile_env.start()
        self.batch_db = patch.object(batch, 'SessionLocal', self.Session)
        self.batch_db.start()
        batch.batches.clear(); batch.manual_inputs.clear()

    def tearDown(self):
        self.batch_db.stop(); self.profile_env.stop()
        batch.batches.clear(); batch.manual_inputs.clear()
        existing.OperationsTests.tearDown(self)

    def agents(self, count=1):
        with self.Session() as db:
            db.get(Server, 1).collection_mode = 'agent'
            db.add(AgentCredential(server_id=1, token_hash='original-token'))
            for i in range(2, count+1):
                db.add(Server(id=i, hostname=f'node-{i}', ip_address=f'192.0.2.{i}', ssh_port=22,
                              ssh_user='root', collection_mode='agent', is_active=True))
            db.commit()

    def create(self, client, ids=None):
        with patch.object(batch, 'kick'):
            response = client.post('/api/ops/agent-batches', json={'server_ids': ids or [1]})
        self.assertEqual(response.status_code, 200, response.text)
        return batch.batches[response.json()['id']]

    def run_one(self, entry, index=0):
        item = entry['items'][index]
        job = ops.reserve_deployment(item['server_id'], reinstall=True)
        item.update(status='running', job_id=job['id'])
        batch.run_item(entry, item, job)
        return item

    def test_profiles_require_operator_https_and_never_return_passwords(self):
        with self.client() as client:
            self.assertEqual(client.get('/api/ops/ssh-profiles').status_code, 401)
            self.login(client)
            body = {'name':'cluster', 'username':'root', 'password':'profile-secret', 'priority':3}
            self.assertEqual(client.post('/api/ops/ssh-profiles', json=body,
                                        headers={'origin':'https://evil.example'}).status_code, 403)
            response = client.post('/api/ops/ssh-profiles', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            profile = response.json(); self.assertTrue(profile['has_password'])
            self.assertNotIn('profile-secret', response.text)
            self.assertNotIn('encrypted', response.text)
            self.assertNotIn('profile-secret', self.profile_path.read_text())
            if os.name == 'posix': self.assertEqual(self.profile_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(ssh_profiles.candidates(), [{'username':'root','password':'profile-secret'}])
            self.assertNotIn('profile-secret', client.get('/api/ops/ssh-profiles').text)
            update = {'name':'renamed', 'username':'root', 'priority':2, 'enabled':False}
            self.assertEqual(client.put('/api/ops/ssh-profiles/'+profile['id'], json=update).status_code, 200)
            self.assertFalse(ssh_profiles.candidates())
            update['enabled'] = True
            client.put('/api/ops/ssh-profiles/'+profile['id'], json=update)
            self.assertEqual(ssh_profiles.candidates()[0]['password'], 'profile-secret')
            update['username'] = 'another-user'
            self.assertEqual(client.put('/api/ops/ssh-profiles/'+profile['id'], json=update).status_code, 422)
            self.assertEqual(client.delete('/api/ops/ssh-profiles/'+profile['id']).status_code, 200)
            self.assertEqual(client.get('/api/ops/ssh-profiles').json(), [])

    def test_batch_is_agent_only_explicit_deduplicated_and_single_active_batch(self):
        with self.client() as client, patch.object(ops, 'connect_ssh') as connect:
            self.assertEqual(client.post('/api/ops/agent-batches', json={'server_ids':[1]}).status_code, 401)
            self.login(client)
            self.assertEqual(client.post('/api/ops/agent-batches', json={'server_ids':[1]}).status_code, 409)
            self.agents()
            entry = self.create(client, [1,1])
            self.assertEqual(len(entry['items']), 1)
            connect.assert_not_called()
            with self.Session() as db: self.assertEqual(db.get(AgentCredential,1).token_hash, 'original-token')
            self.assertEqual(client.post('/api/ops/agent-batches', json={'server_ids':[1]}).status_code, 409)
            result = client.get('/api/ops/agent-batches/'+entry['id']).json()
            self.assertNotIn('_identity', str(result))

    def test_priority_fallback_and_manual_input_do_not_expose_or_persist_password(self):
        self.agents()
        with self.client() as client:
            self.login(client)
            client.post('/api/ops/ssh-profiles', json={'name':'first','username':'bad','password':'first-secret','priority':1})
            client.post('/api/ops/ssh-profiles', json={'name':'second','username':'good','password':'second-secret','priority':2})
            entry = self.create(client)
            attempts = []
            def connect(config):
                attempts.append(config['username'])
                if config['username'] == 'bad': raise paramiko.AuthenticationException()
                return MagicMock()
            def deploy(job, config):
                self.assertTrue(config['reinstall']); self.assertEqual(config['password'],'second-secret')
                job.update(status='success', message='verified')
            with patch.object(ops,'connect_ssh', side_effect=connect), patch.object(ops,'run_deployment', side_effect=deploy):
                item = self.run_one(entry)
            self.assertEqual(attempts, ['bad','good']); self.assertEqual(item['status'],'success')
            self.assertFalse(ops.active_jobs)
            self.assertNotIn('second-secret', client.get('/api/ops/agent-batches/'+entry['id']).text)
            with self.Session() as db: self.assertFalse(db.get(Server,1).ssh_password_encrypted)

    def test_missing_credentials_pause_then_manual_retry_and_stop_clear_secrets(self):
        self.agents(2)
        with self.client() as client:
            self.login(client); entry = self.create(client,[1,2])
            with patch.object(ops, 'connect_ssh') as connect:
                item = self.run_one(entry)
                self.assertEqual(item['status'], 'needs_credentials'); connect.assert_not_called()
            with patch.object(batch, 'kick'):
                response = client.post(f"/api/ops/agent-batches/{entry['id']}/nodes/1/retry",
                                       json={'username':'manual','password':'manual-secret'})
            self.assertEqual(response.status_code,200); self.assertNotIn('manual-secret',response.text)
            self.assertIn((entry['id'],1), batch.manual_inputs)
            calls=[]
            def deploy(job, config): calls.append(config['username']); job.update(status='success',message='ok')
            with patch.object(ops,'connect_ssh', return_value=MagicMock()), patch.object(ops,'run_deployment',side_effect=deploy):
                self.assertEqual(self.run_one(entry)['status'],'success')
            self.assertEqual(calls,['manual']); self.assertFalse(batch.manual_inputs)
            with patch.object(batch,'kick'):
                entry['items'][0]['status']='needs_credentials'
                client.post(f"/api/ops/agent-batches/{entry['id']}/nodes/1/retry",json={'username':'root','password':'cancel-me'})
            response=client.post(f"/api/ops/agent-batches/{entry['id']}/stop")
            self.assertEqual(response.status_code,200);self.assertFalse(batch.manual_inputs)
            self.assertTrue(all(row['status']=='cancelled' for row in entry['items']))

    def test_queue_continues_after_missing_credentials_and_respects_slot_limit(self):
        self.agents(2)
        with self.client() as client:
            self.login(client);entry=self.create(client,[1,2])
            def choose(item,manual):
                if item['server_id']==1:return None
                return {'server_id':2,'password':'ephemeral','reinstall':True}
            def deploy(job,config):job.update(status='success',message='verified')
            def immediate(fn,*args):fn(*args);return SimpleNamespace(result=lambda:None)
            with patch.object(batch,'choose_config',side_effect=choose),patch.object(ops,'run_deployment',side_effect=deploy),patch.object(ops.workers,'submit',side_effect=immediate):
                entry['coordinator']=True;batch.coordinate(entry)
            self.assertEqual([r['status'] for r in entry['items']],['needs_credentials','success'])
            self.assertEqual(batch.payload(entry)['status'],'waiting_input');self.assertFalse(ops.active_jobs)
            ops.active_jobs.update({90:'one',91:'two'})
            with self.assertRaises(existing.HTTPException) as error:ops.reserve_deployment(2,reinstall=True)
            self.assertEqual(error.exception.status_code,429)

    def test_unknown_host_pauses_and_node_mode_change_prevents_any_login(self):
        self.agents()
        with self.client() as client:
            self.login(client);entry=self.create(client)
            key=MagicMock();key.asbytes.return_value=b'new-host-key'
            choices=lambda:[{'username':'root','password':'ephemeral'}]
            with patch.object(ssh_profiles,'candidates',side_effect=choices),patch.object(ops,'connect_ssh',side_effect=ops.UnknownHost(key)):
                item=self.run_one(entry)
            self.assertEqual(item['status'],'needs_trust');self.assertTrue(item['fingerprint'].startswith('SHA256:'))
            with self.Session() as db:db.get(Server,1).collection_mode='ssh';db.commit()
            with patch.object(ops,'connect_ssh') as connect:self.assertEqual(self.run_one(entry)['status'],'failed');connect.assert_not_called()

    def test_network_failure_does_not_cycle_passwords_and_auth_attempts_are_bounded(self):
        self.agents()
        with self.client() as client:
            self.login(client);entry=self.create(client)
            choices=lambda:[{'username':'root','password':f'secret-{i}'} for i in range(5)]
            with patch.object(ssh_profiles,'candidates',side_effect=choices),patch.object(ops,'connect_ssh',side_effect=OSError()) as connect:
                self.assertEqual(self.run_one(entry)['status'],'failed');self.assertEqual(connect.call_count,1)
            with patch.object(ssh_profiles,'candidates',side_effect=choices),patch.object(ops,'connect_ssh',side_effect=paramiko.AuthenticationException()) as connect:
                self.assertEqual(self.run_one(entry)['status'],'needs_credentials');self.assertEqual(connect.call_count,3)

    def test_logs_are_requested_per_node_and_bulk_retry_never_trusts_hosts(self):
        self.agents(2)
        with self.client() as client:
            self.login(client);entry=self.create(client,[1,2])
            entry['items'][0].update(status='needs_credentials',logs=['safe deployment line'])
            entry['items'][1].update(status='needs_trust',fingerprint='SHA256:needs-confirmation')
            summary=client.get('/api/ops/agent-batches/'+entry['id']).json()
            self.assertNotIn('safe deployment line',str(summary))
            node=client.get(f"/api/ops/agent-batches/{entry['id']}/nodes/1").json()
            self.assertEqual(node['logs'],['safe deployment line']);self.assertNotIn('_identity',node)
            with patch.object(batch,'kick'):
                response=client.post(f"/api/ops/agent-batches/{entry['id']}/retry-waiting")
            self.assertEqual(response.status_code,200)
            self.assertEqual([row['status'] for row in response.json()['items']],['queued','needs_trust'])

    def test_retrying_an_old_failed_batch_cannot_start_a_second_active_queue(self):
        self.agents()
        with self.client() as client:
            self.login(client);old=self.create(client);old['items'][0]['status']='failed'
            current=self.create(client)
            self.assertNotEqual(old['id'],current['id'])
            self.assertEqual(client.post(f"/api/ops/agent-batches/{old['id']}/nodes/1/retry",json={}).status_code,409)
            self.assertEqual(client.post(f"/api/ops/agent-batches/{old['id']}/retry-waiting").status_code,409)
