"""Isolated agent installer regressions; never connect SSH or a production DB."""
import argparse
from contextlib import ExitStack
from datetime import datetime
import hashlib
import importlib.util
import io
import json
import os
import tarfile
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

    def bundle(self,directory,unsafe=False):
        path=Path(directory)/'agent.tar.gz'
        manifest={'format':1,'architecture':'x86_64','glibc_min':'2.17',
                  'source_sha256':hashlib.sha256((ROOT/'agent/monitor_agent.py').read_bytes()).hexdigest(),
                  'installer_sha256':hashlib.sha256((ROOT/'agent/install_node.py').read_bytes()).hexdigest()}
        data=json.dumps(manifest).encode()
        with tarfile.open(path,'w:gz') as archive:
            item=tarfile.TarInfo('node-monitor-agent/manifest.json');item.size=len(data)
            archive.addfile(item,io.BytesIO(data))
            if unsafe:
                item=tarfile.TarInfo('../../etc/unsafe');item.size=1
                archive.addfile(item,io.BytesIO(b'x'))
        return path

    def test_internal_and_public_addresses_are_selected_by_target_subnet(self):
        with patch.dict(os.environ,{'AGENT_REPORT_URL':''}):
            self.assertEqual(installer.default_report_url('192.168.1.8'),installer.DEFAULT_URL)
            for address in ('192.168.2.8','114.212.173.212','172.16.66.177','::1'):
                self.assertEqual(installer.default_report_url(address),installer.PUBLIC_URL)
        args=['install_agent.py','--ip','114.212.173.212','--user','root','--port','22','--url','https://custom.invalid/report']
        with patch.object(sys,'argv',args):
            self.assertEqual(installer.parse_args().url,'https://custom.invalid/report')

    def test_configured_site_report_url_overrides_nanjing_subnet_defaults(self):
        url='https://10.168.100.150:3000/api/agent/report'
        with patch.dict(os.environ,{'AGENT_REPORT_URL':url}):
            for address in ('192.168.1.8','10.168.100.8','114.212.173.212'):
                self.assertEqual(installer.default_report_url(address),url)

    def test_bundle_validation_rejects_path_traversal_and_stale_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path=self.bundle(directory)
            manifest,digest=installer.bundle_details(path)
            self.assertEqual(manifest['architecture'],'x86_64')
            self.assertEqual(digest,hashlib.sha256(path.read_bytes()).hexdigest())
            path=self.bundle(directory,unsafe=True)
            with self.assertRaises(installer.InstallError): installer.bundle_details(path)

    def test_standalone_install_never_invokes_target_python_or_pip(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle=self.bundle(directory)
            remote,_,_=self.fake()
            remote.run.side_effect=lambda command,**kwargs: 'x86_64' if command=='uname -m' else ('glibc 2.17' if command=='getconf GNU_LIBC_VERSION' else ('0' if command=='id -u' else '/tmp/watch-nanda-agent.TEST1234'))
            stack,_,_=self.invoke(remote)
            with stack:
                options=installer.parse_args();options.bundle=bundle
                result=installer.install(options,'test-only-ssh-secret')
            self.assertEqual(result['status'],'installed')
            commands=[c.args[0] for c in remote.run.call_args_list]
            self.assertFalse(any('python3' in c or 'pip install' in c for c in commands))
            self.assertTrue(any(' --self-test' in c for c in commands))
            self.assertTrue(any(c.args[1]=='agent-bundle.tar.gz' for c in remote.put.call_args_list))

    def fake(self):
        remote = Mock(spec=installer.Remote)
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
        self.assertIn('network-rollback',[c.args[1] for c in remote.helper.call_args_list])
        with self.Session() as db:
            self.assertEqual(db.get(Server,1).collection_mode,'ssh')
            self.assertEqual(db.get(Server,1).latest_status_data['cpu_percent'],42)
            self.assertIsNone(db.get(AgentCredential,1))

    def test_batch_uses_password_only_and_rejects_unknown_hosts(self):
        import paramiko
        remote,_,_=self.fake()
        stack,_,client=self.invoke(remote)
        with stack:
            result=installer.install(installer.parse_args(),'test-only-ssh-secret',known_hosts_only=True)
        self.assertEqual(result['status'],'installed')
        self.assertFalse(client.connect.call_args.kwargs['look_for_keys'])
        self.assertFalse(client.connect.call_args.kwargs['allow_agent'])
        self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0],paramiko.RejectPolicy)

    def test_reinstall_preserves_existing_node_and_history_and_does_not_skip_healthy_agent(self):
        with self.Session() as db:
            node=db.get(Server,1);node.collection_mode='agent'
            db.add(AgentCredential(server_id=1,token_hash='old-token-digest'))
            db.add(ServerStats(server_id=1,cpu_percent=42));db.commit()
        remote,_,_=self.fake();stack,_,_=self.invoke(remote)
        with stack:
            args=installer.parse_args();args.server_id=1;args.reinstall=True
            result=installer.install(args,'test-only-ssh-secret')
        self.assertEqual(result['status'],'installed')
        with self.Session() as db:
            self.assertEqual(db.query(Server).count(),1)
            self.assertEqual(db.query(ServerStats).count(),1)
            self.assertEqual(db.get(Server,1).collection_mode,'agent')
            self.assertNotEqual(db.get(AgentCredential,1).token_hash,'old-token-digest')

    def test_reinstall_refuses_ssh_mode_without_connecting_or_changing_credentials(self):
        remote,_,_=self.fake();stack,_,client=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError):
            args=installer.parse_args();args.server_id=1;args.reinstall=True
            installer.install(args,'test-only-ssh-secret')
        client.connect.assert_not_called()
        with self.Session() as db:self.assertIsNone(db.get(AgentCredential,1))

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

    def test_only_check_only_skips_installation_even_when_old_agent_is_healthy(self):
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
            actions=[c.args[1] for c in remote.helper.call_args_list]
            if check_only:
                self.assertEqual(actions,['check'])
                remote.start_session.assert_not_called()
                self.assertFalse(any(c.args[1]=='config.json' for c in remote.put.call_args_list))
            else:
                self.assertEqual(actions,['check','prepare','activate','finish'])
                remote.start_session.assert_called_once()
        with self.Session() as db: self.assertNotEqual(db.get(AgentCredential,1).token_hash,hashlib.sha256(token.encode()).hexdigest())

    def test_old_process_stop_failure_happens_before_any_new_token_is_issued(self):
        remote,_,helper=self.fake()
        def fail(stage,action,**kwargs):
            if action=='prepare':raise installer.InstallError('旧 Agent 进程无法终止：5365(D)')
            return helper(stage,action,**kwargs)
        remote.helper.side_effect=fail
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaisesRegex(installer.InstallError,'5365'):
            installer.main()
        self.assertFalse(any(c.args[1]=='config.json' for c in remote.put.call_args_list))
        with self.Session() as db:
            self.assertEqual(db.get(Server,1).collection_mode,'ssh')
            self.assertIsNone(db.get(AgentCredential,1))

    def test_live_session_conflict_does_not_attempt_firewall_changes_or_provisioning(self):
        remote,_,_=self.fake();remote.start_session.side_effect=installer.InstallError('已有正在执行的部署')
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError):installer.main()
        remote.helper.assert_not_called()
        remote.close_session.assert_called_once()
        with self.Session() as db:self.assertIsNone(db.get(AgentCredential,1))

    def test_lost_session_before_provisioning_restores_preparation_without_changing_database(self):
        remote,_,_=self.fake();remote.assert_session.side_effect=installer.InstallError('会话中断')
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError):installer.main()
        self.assertIn('rollback',[c.args[1] for c in remote.helper.call_args_list])
        with self.Session() as db:self.assertIsNone(db.get(AgentCredential,1))

    def test_uncertain_preparation_recovery_preserves_stage_and_network_journal(self):
        remote,_,helper=self.fake()
        def fail(stage,action,**kwargs):
            if action=='prepare':
                error=installer.InstallError('恢复未确认');error.recovery_required=True;raise error
            return helper(stage,action,**kwargs)
        remote.helper.side_effect=fail
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError):installer.main()
        self.assertNotIn('network-rollback',[c.args[1] for c in remote.helper.call_args_list])
        self.assertFalse(any(c.args[0].startswith('rm -rf') for c in remote.run.call_args_list))

    def test_clock_error_stops_before_provisioning(self):
        remote,check,_=self.fake();check['clock']-=1000
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError): installer.main()
        self.assertEqual([c.args[1] for c in remote.helper.call_args_list],['check','network-rollback'])

    def test_selinux_label_failure_rolls_back_preparation_before_issuing_token(self):
        remote,_,_=self.fake()
        original=remote.run.side_effect
        def run(command,**kwargs):
            if 'restorecon -R --' in command:
                raise installer.InstallError('test SELinux labeling failure')
            return original(command,**kwargs)
        remote.run.side_effect=run
        stack,_,_=self.invoke(remote)
        with stack,self.assertRaises(installer.InstallError):
            installer.main()
        self.assertEqual([c.args[1] for c in remote.helper.call_args_list],['check','prepare','rollback','network-rollback'])
        remote.run.assert_any_call('if command -v restorecon >/dev/null 2>&1; then restorecon -R -- /opt/node-monitor-agent/releases/'+'a'*32+'; fi',root=True)
        with self.Session() as db:
            self.assertEqual(db.get(Server,1).collection_mode,'ssh')
            self.assertIsNone(db.get(AgentCredential,1))

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


class DeploymentSessionTests(unittest.TestCase):
    def remote(self, result=None, root=False):
        client=Mock();stdin=Mock();stdout=Mock();stderr=Mock()
        channel=Mock();channel.exit_status_ready.return_value=False
        stdin.channel=stdout.channel=stderr.channel=channel
        stdout.readline.return_value=json.dumps(result or {'ok':True,'session':True})+'\n'
        client.exec_command.return_value=(stdin,stdout,stderr)
        remote=installer.Remote(client,'test-only-password');remote.root=root
        remote.installer='/tmp/watch-nanda-agent.TEST/node-monitor-agent/node-monitor-agent'
        return remote,client,stdin,stdout

    def test_sudo_password_is_only_sent_through_stdin_and_session_closes_cleanly(self):
        remote,client,stdin,_=self.remote()
        try:
            remote.start_session('/tmp/watch-nanda-agent.TEST');remote.assert_session()
            self.assertNotIn('test-only-password',client.exec_command.call_args.args[0])
            stdin.write.assert_called_once_with('test-only-password\n')
        finally:remote.close_session()
        stdin.channel.shutdown_write.assert_called_once()
        self.assertFalse(remote.heartbeat.is_alive())

    def test_a_real_conflicting_session_is_rejected_without_starting_a_heartbeat(self):
        remote,_,_,_=self.remote({'ok':False,'error':'目标机已有正在执行的 Agent 部署'})
        try:
            with self.assertRaisesRegex(installer.InstallError,'正在执行'):remote.start_session('/tmp/watch-nanda-agent.TEST')
            self.assertFalse(hasattr(remote,'heartbeat'))
        finally:remote.close_session()

    def test_dead_remote_guard_prevents_the_next_helper_call_and_close_does_not_mask_errors(self):
        remote,_,stdin,stdout=self.remote(root=True)
        try:
            remote.start_session('/tmp/watch-nanda-agent.TEST')
            stdout.channel.exit_status_ready.return_value=True
            with self.assertRaisesRegex(installer.InstallError,'中断'):remote.assert_session()
            stdin.channel.shutdown_write.side_effect=OSError('connection lost')
        finally:remote.close_session()

    def test_helper_preserves_recovery_required_for_the_coordinator(self):
        remote,_,_,_=self.remote(root=True)
        remote.run=Mock(return_value=json.dumps({'ok':False,'error':'恢复未确认','recovery_required':True}))
        with self.assertRaises(installer.InstallError) as caught:remote.helper('/tmp/watch-nanda-agent.TEST','prepare')
        self.assertTrue(caught.exception.recovery_required)


@unittest.skipUnless(sys.platform=='linux','Root installer uses Linux account/systemd APIs')
class NodeInstallerTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('install_node',ROOT/'agent/install_node.py')
        self.node=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.node)
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.base=root/'opt';self.etc=root/'etc';self.unit=root/'service'
        self.base.mkdir();self.etc.mkdir()
        self.stack=ExitStack()
        proc=root/'proc';proc.mkdir()
        for name,value in [('BASE',self.base),('ETC',self.etc),('UNIT',self.unit),('PENDING',self.base/'pending.json'),('PROC',proc),('SESSION_LOCK',root/'session.lock')]:
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

    def test_frozen_installer_does_not_pass_bundled_library_path_to_system_commands(self):
        process=Mock(returncode=0);process.communicate.return_value=('systemd 249',None)
        with patch.object(self.node.sys,'frozen',True,create=True),patch.dict(self.node.os.environ,{'LD_LIBRARY_PATH':'/bundle','LD_LIBRARY_PATH_ORIG':'/system'}),patch.object(self.node.subprocess,'Popen',return_value=process) as run:
            self.node.command(['systemctl','--version'])
        env=run.call_args.kwargs['env']
        self.assertEqual(env['LD_LIBRARY_PATH'],'/system')
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'],'1')

    def test_bundle_prepare_checks_files_and_never_installs_dependencies(self):
        stage=Path(self.temp.name)/'stage';bundle=stage/'node-monitor-agent';bundle.mkdir(parents=True)
        (bundle/'node-monitor-agent').write_bytes(b'test executable')
        manifest={'format':1,'architecture':__import__('platform').machine(),'files':{'node-monitor-agent':hashlib.sha256(b'test executable').hexdigest()}}
        (bundle/'manifest.json').write_text(json.dumps(manifest))
        (stage/'bundle.sha256').write_text('test hash')
        with patch.object(self.node,'check_owned_service'),patch.object(self.node,'account',return_value=100),patch.object(self.node,'command') as command:
            result=self.node.prepare(stage)
        executable=Path(result['release'])/'node-monitor-agent'
        self.assertEqual(executable.read_bytes(),b'test executable')
        self.assertEqual(executable.stat().st_mode&0o777,0o755)
        command.assert_any_call([str(executable),'--self-test'])
        self.assertFalse(any('pip' in str(c.args) for c in command.call_args_list))
        self.assertTrue(result['old_agent_stopped'])

    def bundle_stage(self):
        stage=Path(self.temp.name)/'stage';bundle=stage/'node-monitor-agent';bundle.mkdir(parents=True)
        (bundle/'node-monitor-agent').write_bytes(b'test executable')
        (bundle/'manifest.json').write_text(json.dumps({'format':1,'architecture':__import__('platform').machine(),
            'files':{'node-monitor-agent':hashlib.sha256(b'test executable').hexdigest()}}))
        (stage/'bundle.sha256').write_text('test hash')
        return stage

    def test_stale_install_record_is_archived_and_old_service_removed_during_preparation(self):
        stage=self.bundle_stage()
        old=self.base/'releases'/('b'*32);old.mkdir(parents=True)
        (self.base/'current').symlink_to(old)
        self.unit.write_text('old unit');(self.etc/'config.json').write_text('old config')
        self.node.PENDING.write_text('{invalid interrupted installation')
        with patch.object(self.node,'check_owned_service',side_effect=lambda:self.unit.exists()),patch.object(self.node,'account',return_value=100),patch.object(self.node,'stop_agent') as stop,patch.object(self.node,'command',return_value=Mock(returncode=0,stdout='')):
            result=self.node.prepare(stage)
        self.assertTrue(result['recovered_previous'])
        stop.assert_called_once_with(True)
        self.assertFalse(self.unit.exists());self.assertFalse((self.etc/'config.json').exists())
        self.assertFalse((self.base/'current').is_symlink())
        release=Path(result['release'])
        self.assertEqual((release/'.rollback/config.json').read_text(),'old config')
        self.assertEqual((release/'.rollback').stat().st_mode&0o777,0o700)
        self.assertEqual(len(list((self.base/'.install-history').glob('*.json'))),1)

    def test_blocked_old_process_prevents_removing_old_config_and_clears_our_pending_marker(self):
        stage=self.bundle_stage();self.unit.write_text('old unit');(self.etc/'config.json').write_text('old config')
        with patch.object(self.node,'check_owned_service',return_value=True),patch.object(self.node,'account',return_value=100),patch.object(self.node,'stop_agent',side_effect=RuntimeError('进程 5365(D) 无法终止')),patch.object(self.node,'command',return_value=Mock(returncode=0,stdout='')):
            with self.assertRaisesRegex(RuntimeError,'5365'):self.node.prepare(stage)
        self.assertEqual((self.etc/'config.json').read_text(),'old config')
        self.assertEqual(self.unit.read_text(),'old unit')
        self.assertFalse(self.node.PENDING.exists())

    def test_service_removal_failure_restores_original_config_before_preparation_returns(self):
        stage=self.bundle_stage();self.unit.write_text('old unit');(self.etc/'config.json').write_text('old config')
        failed=False
        def execute(args,**kwargs):
            nonlocal failed
            if args[:2]==['systemctl','disable'] and not failed:
                failed=True;raise RuntimeError('test failure')
            return Mock(returncode=0,stdout='')
        with patch.object(self.node,'check_owned_service',return_value=True),patch.object(self.node,'account',return_value=100),patch.object(self.node,'stop_agent'),patch.object(self.node,'command',side_effect=execute):
            with self.assertRaisesRegex(RuntimeError,'test failure'):self.node.prepare(stage)
        self.assertEqual((self.etc/'config.json').read_text(),'old config')
        self.assertEqual(self.unit.read_text(),'old unit')
        self.assertFalse(self.node.PENDING.exists())

    def test_stop_escalates_but_refuses_to_cover_an_uninterruptible_process(self):
        processes=[{'pid':5365,'state':'D','started':'1'}]
        with patch.object(self.node,'agent_processes',return_value=processes),patch.object(self.node,'signal_agents') as signal_agents,patch.object(self.node,'command'),patch.object(self.node.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'5365.*D'):self.node.stop_agent(True)
        self.assertEqual([c.args[0] for c in signal_agents.call_args_list],[self.node.signal.SIGTERM,self.node.signal.SIGKILL])

    def test_process_matching_covers_old_manual_agents_but_not_user_gpu_or_python_jobs(self):
        def process(pid,argv,cgroup='/user.slice/session.scope',state='S'):
            path=self.node.PROC/str(pid);path.mkdir()
            (path/'stat').write_text(f'{pid} (test) '+ ' '.join([state]+['0']*18+['123']))
            (path/'cmdline').write_bytes(('\0'.join(argv)+'\0').encode())
            (path/'cgroup').write_text('0::'+cgroup)
        executable=str(self.base/'releases'/('a'*32)/'node-monitor-agent')
        process(900001,[executable,'--config',str(self.etc/'config.json'),'--once'])
        process(900002,['/usr/bin/nvidia-smi'])
        process(900003,['/usr/bin/python3','/home/user/training.py'])
        process(900004,['/usr/bin/worker'],'/system.slice/'+self.node.NAME)
        process(900005,[executable,'--installer','prepare'])
        self.assertEqual(sorted(p['pid'] for p in self.node.agent_processes()),[900001,900004])

    def test_kernel_blocked_subprocess_timeout_never_waits_forever(self):
        process=Mock(pid=900001)
        process.communicate.side_effect=self.node.subprocess.TimeoutExpired('test',1)
        with patch.object(self.node.subprocess,'Popen',return_value=process),patch.object(self.node.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'无法终止'):self.node.command(['test-command'],timeout=1)
        self.assertEqual(process.communicate.call_count,2)
        kill.assert_called_once_with(900001,self.node.signal.SIGKILL)
        process.stdout.close.assert_called_once()

    def test_finish_removes_old_installed_versions_but_never_follows_external_links(self):
        release=self.base/'releases'/('a'*32);release.mkdir(parents=True)
        (release/'node-monitor-agent').write_text('new')
        (self.base/'current').symlink_to(release)
        old=release.parent/('b'*32);old.mkdir();(old/'node-monitor-agent').write_text('old')
        external=Path(self.temp.name)/'unrelated';external.mkdir();(external/'keep').write_text('keep')
        (release.parent/('c'*32)).symlink_to(external)
        (self.base/'venv').mkdir();(self.base/'monitor_agent.py').write_text('legacy')
        self.node.PENDING.write_text(json.dumps({'release':str(release)}))
        with patch.object(self.node,'active',return_value=True):result=self.node.finish(release)
        self.assertFalse(old.exists());self.assertFalse((self.base/'venv').exists())
        self.assertFalse((self.base/'monitor_agent.py').exists());self.assertTrue((release/'node-monitor-agent').exists())
        self.assertTrue((external/'keep').exists());self.assertTrue(result['cleanup_warning'])
        self.assertFalse(self.node.PENDING.exists())

    def test_live_node_session_blocks_other_installers_and_eof_releases_the_lease(self):
        import subprocess
        stage=Path(self.temp.name)/'stage';stage.mkdir()
        code=('import importlib.util,pathlib; '
              's=importlib.util.spec_from_file_location("node",'+repr(str(ROOT/'agent/install_node.py'))+'); '
              'n=importlib.util.module_from_spec(s);s.loader.exec_module(n); '
              'n.SESSION_LOCK=pathlib.Path('+repr(str(self.node.SESSION_LOCK))+'); '
              'n.deployment_session(pathlib.Path('+repr(str(stage))+'))')
        process=subprocess.Popen([sys.executable,'-u','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            self.assertTrue(json.loads(process.stdout.readline())['ok'])
            self.node.require_session(stage)
            with self.assertRaisesRegex(RuntimeError,'会话已失效'):self.node.require_session(stage/'other')
            with self.assertRaisesRegex(RuntimeError,'正在执行'):self.node.deployment_session(stage/'other')
            process.stdin.close();process.wait(timeout=5)
            with self.assertRaisesRegex(RuntimeError,'会话已失效'):self.node.require_session(stage)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close();process.stderr.close()


if __name__=='__main__': unittest.main()
