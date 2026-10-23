"""Operator authentication, SSH secrets, terminal cleanup and notification controls."""
import os
import paramiko
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
from core.ssh_credentials import decrypt_password
from core.status import freshness, server_payload
from models.models import Server, AlertConfig, AgentCredential
from worker import tasks


class OperationsTests(unittest.TestCase):
    def trust_fixture(self):
        home = Path(self.temp.name)/'home'
        path = home/'.ssh'/'known_hosts'
        path.parent.mkdir(parents=True)
        old, new, other = (paramiko.RSAKey.generate(1024) for _ in range(3))
        with self.Session() as db:
            node=db.get(Server,1);node.ip_address='192.0.2.10';node.ssh_port=10022;db.commit()
        lookup='[192.0.2.10]:10022'
        unrelated=paramiko.HostKeys.hash_host('other.example')
        hashed=paramiko.HostKeys.hash_host(lookup)
        original=(f'# retain this comment\n{lookup},alias.example ssh-rsa {old.get_base64()}\n'
                  f'{hashed} ssh-rsa {old.get_base64()}\n'
                  f'{unrelated} ssh-rsa {other.get_base64()}\n'
                  f'[192.0.2.10]:2200 ssh-rsa {other.get_base64()}\n')
        path.write_bytes(original.encode())
        transport=MagicMock();transport.get_remote_server_key.return_value=new
        body={'fingerprint':ops.fingerprint(new),'previous_fingerprint':ops.fingerprint(old),
              'expected_host':'192.0.2.10','expected_port':10022}
        return home,path,old,new,other,transport,body

    def test_changed_key_error_exposes_old_and_new_fingerprints_without_trusting(self):
        old,new=(paramiko.RSAKey.generate(1024) for _ in range(2))
        result=ops.safe_error(paramiko.BadHostKeyException('192.0.2.10',new,old))
        self.assertEqual(result['code'],'host_key_changed')
        self.assertEqual(result['previous_fingerprint'],ops.fingerprint(old))
        self.assertEqual(result['fingerprint'],ops.fingerprint(new))
        self.assertFalse(result['ok'])

    def test_confirmed_key_replacement_preserves_aliases_hashes_other_ports_and_backup(self):
        home,path,old,new,other,transport,body=self.trust_fixture()
        before=path.read_bytes()
        with self.client() as client, patch.object(ops.Path,'home',return_value=home), patch.object(ops.socket,'create_connection') as dial, patch.object(ops.paramiko,'Transport',return_value=transport):
            self.assertEqual(client.post('/api/ops/nodes/1/trust-host',json=body).status_code,401)
            dial.assert_not_called()
            self.login(client)
            response=client.post('/api/ops/nodes/1/trust-host',json=body)
            self.assertEqual(response.status_code,200,response.text)
            dial.assert_called_once_with(('192.0.2.10',10022),timeout=8)
        known=paramiko.HostKeys(str(path))
        self.assertTrue(known.check('[192.0.2.10]:10022',new))
        self.assertFalse(known.check('[192.0.2.10]:10022',old))
        self.assertTrue(known.check('alias.example',old))
        self.assertTrue(known.check('other.example',other))
        self.assertTrue(known.check('[192.0.2.10]:2200',other))
        self.assertIn('# retain this comment',path.read_text())
        backups=list(path.parent.glob('known_hosts.backup-*'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),before)
        transport.auth_password.assert_not_called();transport.close.assert_called_once()
        if os.name=='posix':
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(backups[0].stat().st_mode & 0o777,0o600)

    def test_replacement_requires_matching_old_key_and_rejects_key_changing_again(self):
        home,path,old,new,other,transport,body=self.trust_fixture()
        before=path.read_bytes()
        variants=[{k:v for k,v in body.items() if k!='previous_fingerprint'},
                  {**body,'previous_fingerprint':ops.fingerprint(other)},
                  {**body,'fingerprint':ops.fingerprint(other)}]
        with self.client() as client, patch.object(ops.Path,'home',return_value=home), patch.object(ops.socket,'create_connection'), patch.object(ops.paramiko,'Transport',return_value=transport):
            self.login(client)
            for variant in variants:
                response=client.post('/api/ops/nodes/1/trust-host',json=variant)
                self.assertEqual(response.status_code,409,response.text)
                self.assertEqual(path.read_bytes(),before)
        self.assertEqual(list(path.parent.glob('known_hosts.backup-*')),[])

    def test_trust_rejects_target_change_before_and_during_handshake(self):
        home,path,old,new,other,transport,body=self.trust_fixture()
        before=path.read_bytes()
        with self.client() as client, patch.object(ops.Path,'home',return_value=home), patch.object(ops.socket,'create_connection') as dial, patch.object(ops.paramiko,'Transport',return_value=transport):
            self.login(client)
            response=client.post('/api/ops/nodes/1/trust-host',json={**body,'expected_port':22})
            self.assertEqual(response.status_code,409);dial.assert_not_called()
            def change_address():
                with self.Session() as db:
                    db.get(Server,1).ip_address='192.0.2.20';db.commit()
                return new
            transport.get_remote_server_key.side_effect=change_address
            response=client.post('/api/ops/nodes/1/trust-host',json=body)
            self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(path.read_bytes(),before)

    def test_first_unknown_key_still_works_and_active_deployment_cannot_change_trust(self):
        home,path,old,new,other,transport,body=self.trust_fixture()
        path.unlink()
        body.pop('previous_fingerprint')
        with self.client() as client, patch.object(ops.Path,'home',return_value=home), patch.object(ops.socket,'create_connection') as dial, patch.object(ops.paramiko,'Transport',return_value=transport):
            self.login(client)
            ops.active_jobs[1]='running-job'
            self.assertEqual(client.post('/api/ops/nodes/1/trust-host',json=body).status_code,409)
            dial.assert_not_called();ops.active_jobs.clear()
            self.assertEqual(client.post('/api/ops/nodes/1/trust-host',json=body).status_code,200)
        self.assertTrue(paramiko.HostKeys(str(path)).check('[192.0.2.10]:10022',new))

    def test_failed_atomic_key_save_keeps_original_file(self):
        home,path,old,new,other,transport,body=self.trust_fixture()
        before=path.read_bytes()
        with patch.object(ops.os,'replace',side_effect=OSError('simulated-write-failure')):
            with self.assertRaises(OSError):ops.replace_known_host(path,'[192.0.2.10]:10022',new)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(list(path.parent.glob('.known_hosts-*')),[])

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
        tasks.refresh_requests.clear();tasks.rejected_credentials.clear()

    def tearDown(self):
        main.app.dependency_overrides.clear()
        self.ops_db.stop();self.env.stop()
        for mocked in reversed(self.startup_patches):mocked.stop()
        ops_auth.sessions.clear();ops_auth.attempts.clear()
        ops.jobs.clear();ops.active_jobs.clear();ops.terminals.clear()
        tasks.refresh_requests.clear();tasks.rejected_credentials.clear()
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

    def test_redeployment_is_authenticated_agent_only_and_queues_the_existing_node(self):
        with self.client() as client,patch.object(ops.workers,'submit') as submit:
            body={'use_saved':False,'username':'root','password':'test-node-secret'}
            self.assertEqual(client.post('/api/ops/nodes/1/redeploy',json=body).status_code,401)
            self.login(client)
            self.assertEqual(client.post('/api/ops/nodes/1/redeploy',json=body).status_code,409)
            with self.Session() as db:
                node=db.get(Server,1);node.collection_mode='agent'
                db.add(AgentCredential(server_id=1,token_hash='old-agent-token-digest'));db.commit()
            response=client.post('/api/ops/nodes/1/redeploy',json=body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['kind'],'redeploy')
            self.assertEqual(response.json()['server_id'],1)
            self.assertNotIn('test-node-secret',response.text)
            self.assertTrue(submit.call_args.args[2]['reinstall'])
            self.assertEqual(client.post('/api/ops/nodes/1/redeploy',json=body).status_code,409)
            with self.Session() as db:
                self.assertEqual(db.get(AgentCredential,1).token_hash,'old-agent-token-digest')
                self.assertEqual(db.query(Server).count(),1)

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

    def test_verified_manual_credentials_sync_offline_ssh_node_and_queue_collection(self):
        with self.Session() as db:
            node=db.get(Server,1);node.status='offline';node.offline_since=self.now;node.last_alert_time=self.now
            node.ssh_password_encrypted=encrypt_password('old-password',node);db.commit()
        with self.client() as client, patch.object(ops,'connect_ssh') as connect, patch.object(tasks,'request_server_refresh') as refresh:
            self.login(client)
            connect.return_value.get_transport.return_value.get_remote_server_key.return_value.asbytes.return_value=b'host'
            response=client.post('/api/ops/nodes/1/ssh-test',json={'use_saved':False,'username':'manager','password':'new-password','save_credentials':True})
            result=response.json()
            self.assertTrue(result['ok']);self.assertTrue(result['credentials_saved'])
            self.assertEqual(result['server']['collection_status'],'pending')
            self.assertNotIn('new-password',response.text);self.assertNotIn('expected_credentials',response.text)
            refresh.assert_called_once_with(1)
        with self.Session() as db:
            node=db.get(Server,1)
            self.assertEqual(node.ssh_user,'manager');self.assertEqual(decrypt_password(node),'new-password')
            self.assertEqual(node.collection_mode,'ssh');self.assertIsNone(node.latest_status_data)
            self.assertIsNone(node.offline_since);self.assertIsNone(node.last_alert_time)
            self.assertEqual(server_payload(node)['status'],'pending')
        with patch.object(tasks,'collect_server_data',return_value=({'cpu_percent':19},True,'')):
            tasks._do_monitor_server(1)
        with self.Session() as db:self.assertEqual(server_payload(db.get(Server,1))['status'],'online')

    def test_agent_credentials_sync_preserves_collection_mode_token_and_fresh_metrics(self):
        with self.Session() as db:
            node=db.get(Server,1);node.collection_mode='agent'
            node.latest_status_data={'timestamp':self.now.isoformat()+'Z','received_at':self.now.isoformat()+'Z','report_id':'existing-report','cpu_percent':42}
            original=dict(node.latest_status_data)
            db.add(AgentCredential(server_id=1,token_hash='a'*64));db.commit()
        with patch.object(tasks,'request_server_refresh') as refresh:
            config=ops.configuration(1,ops.Connection(use_saved=False,username='test',password='verified-password'))
            result=ops.save_configuration(config)
            self.assertFalse(result['collection_reset']);refresh.assert_not_called()
        with self.Session() as db:
            node=db.get(Server,1)
            self.assertEqual(node.collection_mode,'agent');self.assertEqual(decrypt_password(node),'verified-password')
            self.assertEqual(node.latest_status_data,original);self.assertEqual(node.status,'online')
            self.assertEqual(db.get(AgentCredential,1).token_hash,'a'*64)

    def test_offline_agent_waits_for_new_report_and_retains_replay_protection(self):
        from datetime import timedelta
        from uuid import uuid4
        from core.agent import accept_report, token_digest, Report
        from core.config import settings
        old=self.now-timedelta(hours=1);report_id=str(uuid4())
        with self.Session() as db:
            node=db.get(Server,1);node.collection_mode='agent';node.status='offline'
            node.latest_status_data={'timestamp':old.isoformat()+'Z','received_at':old.isoformat()+'Z','report_id':report_id,'cpu_percent':90}
            db.add(AgentCredential(server_id=1,token_hash=token_digest('a'*43)));db.commit()
        with patch.object(tasks,'request_server_refresh') as refresh:
            result=ops.save_configuration(ops.configuration(1,ops.Connection(use_saved=False,username='test',password='verified-password')))
            refresh.assert_not_called();self.assertTrue(result['collection_reset'])
        with self.Session() as db:
            node=db.get(Server,1)
            self.assertEqual(freshness(node)[0],'pending');self.assertIsNone(server_payload(node)['data_age_seconds'])
            self.assertEqual(node.latest_status_data['report_id'],report_id)
            self.assertEqual(freshness(node,self.now+timedelta(seconds=settings.AGENT_OFFLINE_SECONDS+10))[0],'offline')
            report=Report(server_id=1,report_id=report_id,sampled_at=old.isoformat()+'Z',uptime_seconds=1,cpu_percent=1,cpu_cores=1,ram_total_mb=1,ram_used_mb=0,ram_percent=0)
            self.assertEqual(accept_report(db,report,'a'*43)['reason'],'duplicate')
            report=report.model_copy(update={'report_id':uuid4(),'sampled_at':self.now})
            self.assertTrue(accept_report(db,report,'a'*43)['accepted'])
            self.assertEqual(freshness(node)[0],'online');self.assertNotIn('collection_reset_at',node.latest_status_data)

    def test_failed_manual_verification_does_not_overwrite_saved_password_or_state(self):
        import paramiko
        with self.Session() as db:
            node=db.get(Server,1);node.status='offline';node.ssh_password_encrypted=encrypt_password('old-password',node);db.commit()
        with self.client() as client,patch.object(ops,'connect_ssh',side_effect=paramiko.AuthenticationException()),patch.object(tasks,'request_server_refresh') as refresh:
            self.login(client)
            response=client.post('/api/ops/nodes/1/ssh-test',json={'use_saved':False,'username':'new-user','password':'wrong-password','save_credentials':True})
            self.assertFalse(response.json()['ok']);refresh.assert_not_called()
        with self.Session() as db:
            node=db.get(Server,1);self.assertEqual(decrypt_password(node),'old-password');self.assertEqual(node.status,'offline')

    def test_concurrent_credential_change_rejects_stale_save(self):
        config=ops.configuration(1,ops.Connection(use_saved=False,username='first',password='verified-password'))
        with self.Session() as db:
            node=db.get(Server,1);node.ssh_user='newer';node.ssh_password_encrypted=encrypt_password('newer-password',node);db.commit()
        with self.assertRaises(HTTPException) as error:ops.save_configuration(config)
        self.assertEqual(error.exception.status_code,409)
        with self.Session() as db:self.assertEqual(decrypt_password(db.get(Server,1)),'newer-password')

    def test_credential_refresh_during_old_collection_queues_new_config_and_drops_old_failure(self):
        with self.Session() as db:
            node=db.get(Server,1);node.ssh_password_encrypted=encrypt_password('old-password',node);db.commit()
            tasks.rejected_credentials[1]=node.ssh_password_encrypted
        def old_collection(_):
            config=ops.configuration(1,ops.Connection(use_saved=False,username='manager',password='new-password'))
            ops.save_configuration(config)
            self.assertIn(1,tasks.refresh_requests);self.assertNotIn(1,tasks.rejected_credentials)
            return {'error_message':'old failure'},False,'old failure'
        tasks.running_tasks.add(1)
        with patch.object(tasks,'collect_server_data',old_collection),patch.object(tasks.executor,'submit') as submit:
            tasks._do_monitor_server(1)
            submit.assert_called_once_with(tasks._do_monitor_server,1)
        with self.Session() as db:
            node=db.get(Server,1);self.assertEqual(node.status,'pending');self.assertIsNone(node.latest_status_data)
            self.assertEqual(decrypt_password(node),'new-password')
        self.assertNotIn(1,tasks.refresh_requests)

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
        from threading import Event
        with self.client() as client,patch.object(ops,'connect_ssh') as connect:
            response=self.login(client);cookie=response.headers['set-cookie'].split(';')[0]
            remote=connect.return_value;channel=remote.invoke_shell.return_value
            closed=Event();remote.close.side_effect=closed.set
            channel.recv_ready.return_value=False;channel.closed=False;channel.exit_status_ready.return_value=False
            with client.websocket_connect('wss://testserver/api/ops/nodes/1/terminal',headers={'origin':'https://testserver','cookie':cookie}) as ws:
                ws.send_json({'use_saved':False,'username':'root','password':'node-secret'})
                self.assertEqual(ws.receive_json()['type'],'connected')
            self.assertTrue(closed.wait(2),'Remote client was not closed after WebSocket disconnect')
            remote.close.assert_called();channel.close.assert_called()
            self.assertFalse(ops.terminals)

    def test_terminal_manual_verification_syncs_credentials_before_connected_message(self):
        from threading import Event
        with self.client() as client,patch.object(ops,'connect_ssh') as connect,patch.object(tasks,'request_server_refresh') as refresh:
            response=self.login(client);cookie=response.headers['set-cookie'].split(';')[0]
            remote=connect.return_value;channel=remote.invoke_shell.return_value
            closed=Event();remote.close.side_effect=closed.set
            channel.recv_ready.return_value=False;channel.closed=False;channel.exit_status_ready.return_value=False
            with client.websocket_connect('wss://testserver/api/ops/nodes/1/terminal',headers={'origin':'https://testserver','cookie':cookie}) as ws:
                ws.send_json({'use_saved':False,'username':'manager','password':'verified-password','save_credentials':True})
                result=ws.receive_json()
                self.assertEqual(result['type'],'connected');self.assertTrue(result['credentials_saved'])
                self.assertEqual(result['server']['status'],'pending');self.assertNotIn('verified-password',str(result))
            self.assertTrue(closed.wait(2));refresh.assert_called_once_with(1)
        with self.Session() as db:self.assertEqual(decrypt_password(db.get(Server,1)),'verified-password')
