"""Isolated agent installer regressions; never connect SSH or a production DB."""
import argparse
from contextlib import ExitStack
from datetime import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import test_monitor
from models.models import AgentCredential, Server, ServerStats

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('install_agent',ROOT/'scripts/install_agent.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    setUp = test_monitor.MonitorTests.setUp
    tearDown = test_monitor.MonitorTests.tearDown

    def fake(self):
        remote = Mock()
        remote.run.side_effect = lambda command,**kwargs: '0' if command=='id -u' else '/tmp/watch-nanda-agent.TEST1234'
        check = {'hostname':'test','python_version':'3.10.12','clock':time.time(),'active':False}
        def helper(stage,action,**kwargs):
            if action=='check': return check
            if action=='prepare': return {'release':'/opt/node-monitor-agent/releases/'+'a'*32}
            return {}
        remote.helper.side_effect = helper
        return remote,check,helper

    def invoke(self, remote, *, check_only=False):
        import paramiko
        import models.database
        args = argparse.Namespace(ip='127.0.0.1',port=22,user='test',server_id=None,url='https://monitor.invalid/api/agent/report',ca_file=None,check_only=check_only)
        output = io.StringIO()
        stack = ExitStack()
        stack.enter_context(patch.object(installer,'parse_args',return_value=args))
        stack.enter_context(patch.object(installer.getpass,'getpass',return_value='test-only-ssh-secret'))
        stack.enter_context(patch.object(installer,'Remote',return_value=remote))
        client = stack.enter_context(patch.object(paramiko,'SSHClient')).return_value
        stack.enter_context(patch.object(models.database,'SessionLocal',self.Session))
        stack.enter_context(patch('sys.stdout',output))
        stack.enter_context(patch('sys.stderr',output))
        return stack,output,client

    def test_success_reuses_node_preserves_history_and_uses_password_only(self):
        with self.Session() as db:
            db.add(ServerStats(server_id=1,cpu_percent=42));db.commit()
        remote,_,_=self.fake()
        stack,output,client=self.invoke(remote)
        with stack: installer.main()
        config=next(c for c in remote.put.call_args_list if c.args[1]=='config.json')
        data=json.loads(config.args[2])
        self.assertEqual(config.args[3],0o600)
        self.assertNotIn(data['token'],output.getvalue())
        self.assertNotIn('test-only-ssh-secret',output.getvalue())
        self.assertFalse(client.connect.call_args.kwargs['look_for_keys'])
        self.assertFalse(client.connect.call_args.kwargs['allow_agent'])
        with self.Session() as db:
            self.assertEqual(db.query(Server).count(),1)
            self.assertEqual(db.query(ServerStats).one().cpu_percent,42)
            self.assertEqual(db.get(Server,1).collection_mode,'agent')
            self.assertEqual(db.get(AgentCredential,1).token_hash,hashlib.sha256(data['token'].encode()).hexdigest())

    def test_failed_first_report_restores_ssh_and_revokes_new_token(self):
        remote,_,helper=self.fake()
        def fail(stage,action,**kwargs):
            if action=='activate': raise installer.InstallError('test report failed')
            return helper(stage,action,**kwargs)
        remote.helper.side_effect=fail
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError): installer.main()
        self.assertIn('rollback',[c.args[1] for c in remote.helper.call_args_list])
        with self.Session() as db:
            self.assertEqual(db.get(Server,1).collection_mode,'ssh')
            self.assertEqual(db.get(Server,1).latest_status_data['cpu_percent'],42)
            self.assertIsNone(db.get(AgentCredential,1))

    def test_batch_can_reuse_existing_keys_but_rejects_unknown_hosts(self):
        import paramiko
        remote,_,_=self.fake()
        stack,_,client=self.invoke(remote)
        with stack:
            result=installer.install(installer.parse_args(),'test-only-ssh-secret',use_existing_keys=True,known_hosts_only=True)
        self.assertEqual(result['status'],'installed')
        self.assertTrue(client.connect.call_args.kwargs['look_for_keys'])
        self.assertFalse(client.connect.call_args.kwargs['allow_agent'])
        self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0],paramiko.RejectPolicy)

    def test_uncertain_remote_rollback_preserves_new_credentials(self):
        remote,_,helper=self.fake()
        def fail(stage,action,**kwargs):
            if action in ('activate','rollback'): raise installer.InstallError('network lost')
            return helper(stage,action,**kwargs)
        remote.helper.side_effect=fail
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError): installer.main()
        with self.Session() as db:
            self.assertEqual(db.get(Server,1).collection_mode,'agent')
            self.assertIsNotNone(db.get(AgentCredential,1))

    def test_dependency_failure_does_not_switch_credentials(self):
        remote,_,helper=self.fake()
        def fail(stage,action,**kwargs):
            if action=='prepare': raise installer.InstallError('pip unavailable')
            return helper(stage,action,**kwargs)
        remote.helper.side_effect=fail
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError): installer.main()
        with self.Session() as db:
            self.assertEqual(db.get(Server,1).collection_mode,'ssh')
            self.assertIsNone(db.get(AgentCredential,1))

    def test_healthy_repeat_and_check_only_never_rotate_token(self):
        token='a'*43
        with self.Session() as db:
            server=db.get(Server,1);server.collection_mode='agent'
            server.latest_status_data={'received_at':datetime.utcnow().isoformat()+'Z'}
            db.add(AgentCredential(server_id=1,token_hash=hashlib.sha256(token.encode()).hexdigest()));db.commit()
        for check_only in (False,True):
            remote,check,_=self.fake()
            check.update(active=True,config_safe=True,account_safe=True,server_id=1,url='https://monitor.invalid/api/agent/report',token_hash=hashlib.sha256(token.encode()).hexdigest(),program_hash=hashlib.sha256((ROOT/'agent/monitor_agent.py').read_bytes()).hexdigest(),ca_hash=None,interval_seconds=300)
            stack,_,_=self.invoke(remote,check_only=check_only)
            with stack: installer.main()
            self.assertEqual([c.args[1] for c in remote.helper.call_args_list],['check'])
            self.assertFalse(any(c.args[1]=='config.json' for c in remote.put.call_args_list))
        with self.Session() as db: self.assertEqual(db.get(AgentCredential,1).token_hash,hashlib.sha256(token.encode()).hexdigest())

    def test_clock_error_stops_before_provisioning(self):
        remote,check,_=self.fake();check['clock']-=1000
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError): installer.main()
        self.assertEqual([c.args[1] for c in remote.helper.call_args_list],['check'])

    def test_recovery_never_overwrites_credentials_changed_by_another_admin(self):
        with self.Session() as db:
            snapshot,_,digest=installer.provision(db,'127.0.0.1',22,'root','test')
            db.get(AgentCredential,1).token_hash='another-admin';db.commit()
            with self.assertRaises(installer.InstallError): installer.restore_credentials(db,snapshot,digest)
            self.assertEqual(db.get(AgentCredential,1).token_hash,'another-admin')

    def test_new_node_failure_keeps_id_and_history_for_retry(self):
        with self.Session() as db:
            snapshot,_,digest=installer.provision(db,'192.0.2.10',22,'root','new-node')
            db.add(ServerStats(server_id=snapshot['id'],cpu_percent=9));db.commit()
            installer.restore_credentials(db,snapshot,digest)
            server=installer.find_server(db,'192.0.2.10',22)
            self.assertEqual(server.id,snapshot['id'])
            self.assertEqual(server.status,'pending')
            self.assertEqual(db.query(ServerStats).one().cpu_percent,9)
            self.assertIsNone(db.get(AgentCredential,server.id))

    def test_ambiguous_ip_requires_explicit_id(self):
        with self.Session() as db:
            db.add(Server(hostname='duplicate',ip_address='127.0.0.1',ssh_port=22));db.commit()
            with self.assertRaises(installer.InstallError): installer.find_server(db,'127.0.0.1',22)
            self.assertEqual(installer.find_server(db,'127.0.0.1',22,1).id,1)

    def test_private_key_is_rejected_as_ca(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.pem';path.write_text('-----BEGIN PRIVATE KEY-----')
            args=['install_agent.py','--ip','127.0.0.1','--user','root','--port','22','--ca-file',str(path)]
            with patch.object(sys,'argv',args),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit): installer.parse_args()

    def test_unknown_ssh_host_is_not_automatically_trusted(self):
        key=Mock();key.asbytes.return_value=b'test key';key.get_name.return_value='ssh-ed25519'
        client=Mock()
        with patch('builtins.input',return_value='no'),patch('sys.stdout',io.StringIO()),self.assertRaises(installer.InstallError):
            installer.VerifyHost().missing_host_key(client,'192.0.2.10',key)
        client.save_host_keys.assert_not_called()


@unittest.skipUnless(sys.platform=='linux','Root installer uses Linux account/systemd APIs')
class NodeInstallerTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('install_node',ROOT/'agent/install_node.py')
        self.node=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.node)
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.base=root/'opt';self.etc=root/'etc';self.unit=root/'service'
        self.base.mkdir();self.etc.mkdir()
        self.stack=ExitStack()
        for name,value in [('BASE',self.base),('ETC',self.etc),('UNIT',self.unit),('PENDING',self.base/'pending.json')]:
            self.stack.enter_context(patch.object(self.node,name,value))
        self.stack.enter_context(patch.object(self.node.os,'chown'))
        self.stack.enter_context(patch.object(self.node.grp,'getgrnam',return_value=Mock(gr_gid=100)))

    def tearDown(self):
        self.stack.close();self.temp.cleanup()

    def test_rollback_restores_previous_config_and_service_and_keeps_backup_private(self):
        release=self.base/'releases'/('a'*32);release.mkdir(parents=True)
        backup=release/'.rollback';backup.mkdir(mode=0o700)
        for name,value in [('config.json',b'old-token-config'),('ca.crt',b'old-ca'),('service',b'old-unit')]:
            self.node.write_file(backup/name,value)
        state={'active':True,'enabled':True,'files':['config.json','ca.crt','service'],'current':None}
        self.node.write_file(backup/'state.json',json.dumps(state).encode())
        self.node.write_file(self.node.PENDING,json.dumps({'release':str(release)}).encode())
        (self.base/'current').symlink_to(release)
        with patch.object(self.node,'active',return_value=True),patch.object(self.node,'command') as command:
            self.node.rollback(release)
        self.assertEqual((self.etc/'config.json').read_bytes(),b'old-token-config')
        self.assertEqual((self.etc/'config.json').stat().st_mode&0o777,0o640)
        self.assertEqual((backup/'config.json').stat().st_mode&0o777,0o600)
        self.assertEqual(self.unit.read_bytes(),b'old-unit')
        self.assertFalse((self.base/'current').exists())
        self.assertFalse(self.node.PENDING.exists())
        command.assert_any_call(['systemctl','start',self.node.NAME])

    def test_rollback_does_not_touch_service_before_activation(self):
        release=self.base/'releases'/('a'*32);release.mkdir(parents=True)
        self.node.write_file(self.node.PENDING,json.dumps({'release':str(release)}).encode())
        with patch.object(self.node,'command') as command: self.node.rollback(release)
        command.assert_not_called()
        self.assertFalse(self.node.PENDING.exists())

    def test_activation_verifies_report_before_enabling_service_and_can_restore(self):
        release=self.base/'releases'/('a'*32);release.mkdir(parents=True)
        stage=Path(self.temp.name)/'stage';stage.mkdir()
        (stage/'config.json').write_text(json.dumps({'server_id':1,'token':'a'*43,'url':'https://monitor.invalid/api/agent/report'}))
        (stage/'node-monitor-agent.service').write_text('Description=Node Monitor metrics reporting agent\nExecStart='+str(self.base)+'/venv/bin/python '+str(self.base)+'/monitor_agent.py --config '+str(self.etc)+'/config.json\n')
        (self.etc/'config.json').write_bytes(b'previous config')
        self.unit.write_bytes(b'previous unit')
        self.node.write_file(self.node.PENDING,json.dumps({'release':str(release)}).encode())
        result=Mock(returncode=0,stdout='Report completed: accepted=True')
        with patch.object(self.node,'command',return_value=result) as command,patch.object(self.node,'account',return_value=100),patch.object(self.node,'check_owned_service',return_value=True),patch.object(self.node,'active',return_value=True),patch.object(self.node.time,'sleep'):
            self.node.activate(stage,release)
            calls=[c.args[0] for c in command.call_args_list]
            report=next(i for i,c in enumerate(calls) if c[0]=='runuser')
            enabled=calls.index(['systemctl','enable','--now',self.node.NAME])
            self.assertLess(report,enabled)
            self.assertEqual((self.base/'current').resolve(),release)
            self.assertIn('/current/venv/bin/python',self.unit.read_text())
            self.assertEqual((self.etc/'config.json').stat().st_mode&0o777,0o640)
            self.node.rollback(release)
        self.assertEqual((self.etc/'config.json').read_bytes(),b'previous config')
        self.assertEqual(self.unit.read_bytes(),b'previous unit')

    def test_failed_report_does_not_enable_new_service(self):
        release=self.base/'releases'/('a'*32);release.mkdir(parents=True)
        stage=Path(self.temp.name)/'stage';stage.mkdir()
        (stage/'config.json').write_text('{}')
        (stage/'node-monitor-agent.service').write_text('test unit')
        self.node.write_file(self.node.PENDING,json.dumps({'release':str(release)}).encode())
        def execute(args,**kwargs): return Mock(returncode=1 if args[0]=='runuser' else 0,stdout='HTTP 401')
        with patch.object(self.node,'command',side_effect=execute) as command,patch.object(self.node,'account',return_value=100),patch.object(self.node,'check_owned_service',return_value=False),patch.object(self.node,'active',return_value=False),self.assertRaises(RuntimeError):
            self.node.activate(stage,release)
        self.assertNotIn(['systemctl','enable','--now',self.node.NAME],[c.args[0] for c in command.call_args_list])

    def test_foreign_service_and_release_paths_are_rejected(self):
        self.unit.write_text('Description=unrelated service\nUser=root\n')
        result=Mock(stdout=f'LoadState=loaded\nFragmentPath={self.unit}\n')
        with patch.object(self.node,'command',return_value=result),self.assertRaises(RuntimeError): self.node.check_owned_service()
        with self.assertRaises(RuntimeError): self.node.release_path('/opt/other-project')


if __name__=='__main__': unittest.main()
