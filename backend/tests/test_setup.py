"""Deployment regressions using isolated files, sockets and schema fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from core import schema, site_config
import setup_project as setup
import setup_security as security
import bootstrap


class SchemaTests(unittest.TestCase):
    def test_shipped_schema_covers_all_model_columns_and_compound_index(self):
        from models.models import Server, ServerStats, AgentCredential, EmailConfig, AlertConfig
        tables, indexes = schema.definitions(schema.SCHEMA.read_text(encoding='utf-8'))
        for model in (Server, ServerStats, AgentCredential, EmailConfig, AlertConfig):
            self.assertLessEqual({c.name for c in model.__table__.columns}, set(tables[model.__tablename__][1]))
        self.assertIn(('ix_server_stats_sid_ts','server_stats',('server_id','timestamp')), [i[:3] for i in indexes])

    def test_additive_migration_preserves_rows_and_is_idempotent(self):
        from sqlalchemy import create_engine
        engine = create_engine('sqlite://')
        sql = "CREATE TABLE servers (id INTEGER primary key, hostname varchar(100) not null, collection_mode varchar(10) default 'ssh' not null, ssh_password_encrypted TEXT null); CREATE INDEX ix_servers_hostname ON servers (hostname);"
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE servers (id INTEGER primary key, hostname varchar(100) not null)')
            connection.exec_driver_sql("INSERT INTO servers VALUES (73,'existing')")
            self.assertFalse(schema.reconcile(connection, sql, check_only=True))
            self.assertEqual(len(connection.exec_driver_sql('PRAGMA table_info(servers)').all()),2)
            schema.reconcile(connection, sql)
            self.assertEqual(connection.exec_driver_sql('SELECT * FROM servers').one(), (73,'existing','ssh',None))
            self.assertTrue(schema.reconcile(connection, sql))
        engine.dispose()

    def test_missing_identity_fails_before_any_ddl_and_does_not_invent_data(self):
        from sqlalchemy import create_engine
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE servers (hostname TEXT)')
            connection.exec_driver_sql("INSERT INTO servers VALUES ('retained')")
            with self.assertRaisesRegex(RuntimeError,'不能自动伪造'):
                schema.reconcile(connection, 'CREATE TABLE unrelated (id int); CREATE TABLE servers (id int primary key, hostname TEXT);')
            self.assertFalse(__import__('sqlalchemy').inspect(connection).has_table('unrelated'))
        engine.dispose()

    def test_missing_history_metrics_do_not_backfill_fake_values(self):
        metadata = Mock()
        metadata.has_table.return_value=True
        metadata.get_columns.return_value=[{'name':'id'}, {'name':'server_id'}]
        with patch('sqlalchemy.inspect', return_value=metadata):
            changes = schema.plan(Mock(), 'CREATE TABLE server_stats (id int primary key, server_id int not null, cpu_percent float default 0 null, timestamp datetime default CURRENT_TIMESTAMP null);')
        self.assertIn('ADD COLUMN `cpu_percent` float  null', changes[0][1])
        self.assertIn('SET DEFAULT 0', changes[1][1])
        self.assertNotIn('CURRENT_TIMESTAMP', changes[2][1])


class SetupTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Requires Linux advisory locks')
    def test_lifecycle_cannot_start_during_dependency_replacement(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with (root/'setup.lock').open('w') as stream,patch.object(setup.life,'RUNTIME',root),patch.dict(os.environ,{'WATCH_NANDA_SETUP_PID':''}):
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError,'初始化/升级正在'):
                    setup.life.check_setup_lock()
                fcntl.flock(stream,fcntl.LOCK_UN)
                setup.life.check_setup_lock()

    def test_failed_upgrade_restores_dependency_bytes_and_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.runtime').mkdir()
            for relative in ('backend/.venv','frontend/node_modules','frontend/dist'):
                path=root/relative;path.mkdir(parents=True)
                (path/'retained').write_bytes(('before '+relative).encode())
            unrelated=root/'unrelated';unrelated.write_text('do not touch')
            with patch.object(setup,'ROOT',root),patch.object(setup.life,'managed_services',return_value=None),patch.object(setup.life,'read_state',return_value=[]),patch.object(setup,'legacy_listener',return_value=None),patch.object(setup.life,'standalone_stop'),patch.object(setup.os,'geteuid',return_value=1000,create=True):
                upgrade=setup.Upgrade();upgrade.stopped=True
                for relative in ('backend/.venv','frontend/node_modules','frontend/dist'):
                    upgrade.move(relative)
                    (root/relative).mkdir()
                    (root/relative/'broken').write_text('failed replacement')
                (root/'.runtime/site.json').write_text('{}')
                upgrade.rollback()
                for relative in ('backend/.venv','frontend/node_modules','frontend/dist'):
                    self.assertEqual((root/relative/'retained').read_bytes(),('before '+relative).encode())
                    self.assertFalse((root/relative/'broken').exists())
                self.assertFalse((root/'.runtime/site.json').exists())
                self.assertEqual(unrelated.read_text(),'do not touch')

    def test_rollback_reloads_the_previous_private_node_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.runtime').mkdir()
            (root/'.runtime/site.json').write_text(json.dumps({'WATCH_NANDA_NODE':'/private/previous/node'}))
            with patch.object(setup,'ROOT',root),patch.object(setup.life,'managed_services',return_value=None),patch.object(setup.life,'read_state',return_value=[]),patch.object(setup,'legacy_listener',return_value=None),patch.object(setup.life,'standalone_stop'),patch.object(setup.os,'geteuid',return_value=1000,create=True),patch.dict(os.environ,{},clear=True):
                upgrade=setup.Upgrade()
                os.environ['WATCH_NANDA_NODE']='/new/node'
                (root/'.runtime/site.json').write_text(json.dumps({'WATCH_NANDA_NODE':'/new/node'}))
                upgrade.rollback()
                self.assertEqual(os.environ['WATCH_NANDA_NODE'],'/private/previous/node')

    @unittest.skipUnless(sys.platform=='linux','Requires Linux signal semantics')
    def test_legacy_stop_never_signals_a_reused_pid(self):
        record={'pid':12345,'ticks':'1','cwd':'/project/backend','command':'old','role':'backend','args':['python','main.py']}
        with patch.object(setup.life,'process_identity',return_value={**record,'ticks':'2'}),patch.object(setup.os,'kill') as kill:
            setup.stop_legacy(record)
            kill.assert_not_called()

    def test_existing_site_and_env_survive_site_initialization_and_define_agent_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.runtime').mkdir();(root/'backend').mkdir()
            (root/'backend/.env').write_text('DB_PASSWORD=retained\n')
            original=(root/'backend/.env').read_bytes()
            with patch.object(setup,'ROOT',root),patch.object(setup.life,'nginx_owned',return_value=False),patch.object(setup.os,'geteuid',return_value=1000,create=True),patch.dict(os.environ,{},clear=True):
                site=setup.configure_site('10.168.100.150','/existing/node')
                self.assertEqual(site['AGENT_REPORT_URL'],'https://10.168.100.150:3000/api/agent/report')
                again=setup.configure_site(None,'/new/node')
                self.assertEqual(again['WATCH_NANDA_HTTPS_HOST'],'10.168.100.150')
                with self.assertRaises(RuntimeError):setup.configure_site('10.0.0.2','/node')
            self.assertEqual((root/'backend/.env').read_bytes(),original)

    def test_existing_database_outage_cannot_create_replacement(self):
        with patch.object(setup.subprocess,'run',return_value=Mock(returncode=42)),patch.object(bootstrap,'ensure_docker') as docker:
            with self.assertRaisesRegex(RuntimeError,'不创建替代数据库'):
                setup.database_needs_docker('/python',True,existing_install=True)
            docker.assert_not_called()

    def test_site_configuration_cannot_inject_arbitrary_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.runtime').mkdir()
            (root/'.runtime/site.json').write_text(json.dumps({'LD_PRELOAD':'/evil.so'}))
            with self.assertRaises(RuntimeError):site_config.load_site(root)

    @unittest.skipUnless(os.name=='posix','Linux file ownership and certificate APIs')
    def test_fresh_security_is_usable_and_second_install_preserves_keys_and_password(self):
        import ssl
        from core import ops_auth
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);tls=root/'tls'
            env={'WATCH_NANDA_TLS_CA':str(tls/'ca.crt'),'WATCH_NANDA_TLS_CERT':str(tls/'server.crt'),
                 'WATCH_NANDA_TLS_KEY':str(tls/'server.key'),'OPS_PASSWORD_FILE':str(root/'ops.json')}
            input_file=root/'password';input_file.write_text('a-test-only-password');input_file.chmod(0o600)
            with patch.dict(os.environ,env):
                security.tls('10.1.2.3');security.operator(input_file)
                before={p:p.read_bytes() for p in list(tls.iterdir())+[root/'ops.json']}
                self.assertTrue(ops_auth.verify_login('a-test-only-password','test'))
                security.tls('10.1.2.3');security.operator(input_file)
                self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))
                self.assertTrue(all((p.stat().st_mode&0o777)==0o600 for p in before))
                with self.assertRaises(ssl.CertificateError):security.tls('10.1.2.4')


if __name__=='__main__':unittest.main()
