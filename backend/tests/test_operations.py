"""Operator authentication, SSH secrets, terminal cleanup and notification controls."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_monitor
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import main
from core import operations as ops, ops_auth
from core.ssh_credentials import encrypt_password
from models.models import Server, AlertConfig
from worker import tasks


class OperationsTests(unittest.TestCase):
    def setUp(self):
        test_monitor.MonitorTests.setUp(self)
        self.key = Path(self.temp.name)/'key'
        self.key.write_bytes(Fernet.generate_key()); self.key.chmod(0o600)
        self.env = patch.dict(os.environ, {'SSH_CREDENTIAL_KEY_FILE':str(self.key), 'OPS_PASSWORD_FILE':str(Path(self.temp.name)/'ops.json')})
        self.env.start()
        self.ops_db = patch.object(ops,'SessionLocal',self.Session);self.ops_db.start()
        self.startup_patches=[patch.object(main,'start_scheduler'),patch.object(main,'stop_scheduler'),patch.object(main,'monitor_once')]
        for mocked in self.startup_patches:mocked.start()
        ops_auth.set_password('test-operator-password')
        ops_auth.sessions.clear();ops_auth.attempts.clear()
        ops.jobs.clear();ops.active_jobs.clear();ops.terminals.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.ops_db.stop();self.env.stop()
        for mocked in reversed(self.startup_patches):mocked.stop()
        ops_auth.sessions.clear();ops_auth.attempts.clear()
        ops.jobs.clear();ops.active_jobs.clear();ops.terminals.clear()
        test_monitor.MonitorTests.tearDown(self)

    def login(self,client):
        response=client.post('/api/ops/login',json={'password':'test-operator-password'},headers={'origin':'https://testserver'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('httponly',response.headers['set-cookie'].lower())
        self.assertIn('secure',response.headers['set-cookie'].lower())
        self.assertNotIn('test-operator-password',response.text)
        return response

    def client(self):
        def db():
            with self.Session() as session:yield session
        main.app.dependency_overrides[main.get_db]=db
        return TestClient(main.app,base_url='https://testserver')

    def test_operator_password_hash_session_expiry_and_login_throttle(self):
        self.assertNotIn('test-operator-password',ops_auth.password_path().read_text())
        token=ops_auth.verify_login('test-operator-password','client')
        request=Request({'type':'http','scheme':'https','path':'/api/ops/session','headers':[(b'host',b'testserver'),(b'cookie',f'{ops_auth.COOKIE}={token}'.encode())]})
        self.assertTrue(ops_auth.authorized(request))
        ops_auth.logout(request)
        with self.assertRaises(HTTPException):ops_auth.authorized(request)
        for _ in range(5):
            with self.assertRaises(HTTPException) as error:ops_auth.verify_login('wrong','bad-client')
            self.assertEqual(error.exception.status_code,401)
        with self.assertRaises(HTTPException) as error:ops_auth.verify_login('wrong','bad-client')
        self.assertEqual(error.exception.status_code,429)

    def test_https_authentication_and_origin_checks_before_ssh(self):
        with self.client() as client,patch.object(ops,'connect_ssh') as connect:
            self.assertEqual(client.post('/api/ops/nodes/1/ssh-test',json={}).status_code,401)
            self.assertEqual(client.post('http://testserver/api/ops/login',json={'password':'test-operator-password'}).status_code,403)
            self.assertEqual(client.post('/api/ops/login',json={'password':'test-operator-password'},headers={'origin':'https://evil.example'}).status_code,403)
            self.login(client)
            self.assertEqual(client.post('/api/ops/nodes/1/ssh-test',json={},headers={'origin':'https://evil.example'}).status_code,403)
            connect.assert_not_called()

    def test_saved_password_never_returned_and_manual_password_not_saved_by_default(self):
        with self.Session() as db:
            node=db.get(Server,1);node.ssh_password_encrypted=encrypt_password('node-secret',node);db.commit()
        with self.client() as client,patch.object(ops,'connect_ssh') as connect:
            self.login(client)
            connect.return_value.get_transport.return_value.get_remote_server_key.return_value.asbytes.return_value=b'host'
            result=client.post('/api/ops/nodes/1/ssh-test',json={'use_saved':True})
            self.assertTrue(result.json()['ok'])
            self.assertEqual(connect.call_args.args[0]['password'],'node-secret')
            self.assertNotIn('node-secret',result.text)
            connect.return_value.close.assert_called()
            client.post('/api/ops/nodes/1/ssh-test',json={'use_saved':False,'username':'other','password':'manual-secret'})
            with self.Session() as db:self.assertEqual(db.get(Server,1).ssh_user,'root')

    def test_password_only_connection_strict_known_host_no_key_fallback(self):
        import paramiko
        with patch.object(paramiko,'SSHClient') as factory:
            ops.connect_ssh({'host':'127.0.0.1','port':22,'username':'test','password':'secret'})
            factory.return_value.load_system_host_keys.assert_called_once()
            self.assertIsInstance(factory.return_value.set_missing_host_key_policy.call_args.args[0],ops.IdentifyUnknown)
            kwargs=factory.return_value.connect.call_args.kwargs
            self.assertFalse(kwargs['allow_agent']);self.assertFalse(kwargs['look_for_keys'])
        self.assertEqual(ops.safe_error(paramiko.AuthenticationException('node-secret'))['code'],'authentication_failed')
        self.assertNotIn('node-secret',str(ops.safe_error(paramiko.AuthenticationException('node-secret'))))

    def test_deployment_secrets_not_in_job_payload_duplicate_job_and_config_edit_blocked(self):
        with self.client() as client,patch.object(ops.workers,'submit') as submit:
            self.login(client)
            payload={'use_saved':False,'username':'root','password':'manual-secret'}
            response=client.post('/api/ops/nodes/1/deploy',json=payload)
            self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn('manual-secret',response.text)
            self.assertEqual(submit.call_args.args[2]['password'],'manual-secret')
            self.assertEqual(client.post('/api/ops/nodes/1/deploy',json=payload).status_code,409)
            with self.Session() as db:
                with self.assertRaises(HTTPException):main.update_server(1,main.ServerUpdate(hostname='test',ip_address='127.0.0.1',group_name='test'),db)
                with self.assertRaises(HTTPException):main.delete_server_node(1,db)
            job=response.json();ops.append_log(ops.jobs[job['id']],'password=manual-secret','manual-secret')
            self.assertNotIn('manual-secret',client.get('/api/ops/jobs/'+job['id']).text)

    def test_node_bell_mutes_offline_and_recovery_without_disabling_collection(self):
        from datetime import timedelta
        with self.Session() as db:
            db.add(AlertConfig(enable_email_alert=True,offline_threshold_minutes=1))
            node=db.get(Server,1);node.status='offline';node.offline_since=self.now-timedelta(hours=1);node.last_alert_time=self.now-timedelta(days=2);db.commit()
            main.update_node_notifications(1,main.NodeNotificationUpdate(enabled=False),db)
            self.assertIsNone(node.last_alert_time)
            self.assertTrue(node.is_active)
        with patch.object(tasks,'send_email_wrapper') as send:
            tasks.check_server_alerts();send.assert_not_called()
            self.assertEqual(tasks.send_node_email(1,'recovery','body'),(False,'muted'))
        with self.Session() as db:
            main.update_node_notifications(1,main.NodeNotificationUpdate(enabled=True),db)
        with patch.object(tasks,'send_email_wrapper',return_value=(True,'')) as send:
            tasks.check_server_alerts();send.assert_called_once()

    def test_notification_migration_can_upgrade_an_existing_database(self):
        from sqlalchemy import text, inspect
        import migrate_agent
        with self.engine.begin() as db:db.execute(text('ALTER TABLE servers DROP COLUMN email_alert_enabled'))
        with patch.object(migrate_agent,'engine',self.engine):
            self.assertFalse(migrate_agent.migrate(check_only=True))
            migrate_agent.migrate();self.assertTrue(migrate_agent.migrate(check_only=True))
        with self.Session() as db:
            node=db.get(Server,1);self.assertEqual(node.hostname,'test');self.assertTrue(node.email_alert_enabled)

    def test_terminal_requires_wss_origin_and_operator_session(self):
        from starlette.websockets import WebSocketDisconnect
        with self.client() as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('wss://testserver/api/ops/nodes/1/terminal',headers={'origin':'https://testserver'}):pass
            response=self.login(client)
            cookie=response.headers['set-cookie'].split(';')[0]
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('wss://testserver/api/ops/nodes/1/terminal',headers={'origin':'https://evil.example','cookie':cookie}):pass

    def test_terminal_disconnect_closes_remote_channel_and_client(self):
        with self.client() as client,patch.object(ops,'connect_ssh') as connect:
            response=self.login(client);cookie=response.headers['set-cookie'].split(';')[0]
            remote=connect.return_value;channel=remote.invoke_shell.return_value
            channel.recv_ready.return_value=False;channel.closed=False;channel.exit_status_ready.return_value=False
            with client.websocket_connect('wss://testserver/api/ops/nodes/1/terminal',headers={'origin':'https://testserver','cookie':cookie}) as ws:
                ws.send_json({'use_saved':False,'username':'root','password':'node-secret'})
                self.assertEqual(ws.receive_json()['type'],'connected')
            remote.close.assert_called();channel.close.assert_called()
            self.assertFalse(ops.terminals)
