"""Installer/lifecycle regressions. No production services, database or Docker calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/(name+'.py'))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


init = module('initialize')
life = module('lifecycle')


class InstallerTests(unittest.TestCase):
    def test_bootstrap_connects_without_selecting_the_not_yet_created_database(self):
        from sqlalchemy.engine import make_url
        url=make_url('mysql+pymysql://test:secret@127.0.0.1:3307/new_database?charset=utf8mb4')
        engine=init.connect_server(url)
        try:
            self.assertIsNone(engine.url.database)
            self.assertEqual(engine.url.username,url.username)
            self.assertEqual(engine.url.password,url.password)
            self.assertEqual(engine.url.port,url.port)
            self.assertEqual(engine.url.query,url.query)
        finally:
            engine.dispose()

    def test_existing_env_is_never_overwritten_and_passwords_are_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'backend').mkdir()
            (root/'backend'/'.env.example').write_text('DB_HOST=127.0.0.1\nDB_PASSWORD=\n')
            init.prepare_env(root)
            first=(root/'backend'/'.env').read_bytes()
            self.assertGreater(len(first.split(b'DB_PASSWORD=')[1].strip()),32)
            init.prepare_env(root)
            self.assertEqual(first,(root/'backend'/'.env').read_bytes())
            if sys.platform=='linux': self.assertEqual((root/'backend'/'.env').stat().st_mode&0o777,0o600)
            (root/'backend'/'.env').unlink()
            init.prepare_env(root)
            self.assertNotEqual(first,(root/'backend'/'.env').read_bytes())

    def test_schema_applies_missing_objects_only_and_does_not_swallow_errors(self):
        connection=Mock()
        metadata=Mock()
        metadata.has_table.side_effect=lambda name: name=='servers'
        metadata.get_indexes.return_value=[{'name':'already_present'}]
        schema='CREATE TABLE servers (id int); CREATE TABLE agent_credentials (server_id int); CREATE INDEX already_present ON servers (id); CREATE INDEX new_index ON servers (id);'
        with patch('sqlalchemy.inspect',return_value=metadata):
            init.apply_schema(connection,schema)
        self.assertEqual(connection.exec_driver_sql.call_count,2)
        self.assertIn('agent_credentials',connection.exec_driver_sql.call_args_list[0].args[0])
        connection.exec_driver_sql.side_effect=RuntimeError('DDL failed')
        with patch('sqlalchemy.inspect',return_value=metadata),self.assertRaises(RuntimeError):
            init.apply_schema(connection,'CREATE TABLE agent_credentials (server_id int);')

    def test_schema_rejects_non_schema_commands_and_quotes_names(self):
        with self.assertRaises(RuntimeError): init.apply_schema(Mock(),'DROP TABLE servers;')
        self.assertEqual(init.quoted_identifier('monitor`test'),'`monitor``test`')

    def test_remote_database_failure_never_creates_local_docker(self):
        from sqlalchemy.engine import make_url
        with patch.object(init.subprocess,'run') as run,self.assertRaises(RuntimeError):
            init.docker_database(make_url('mysql+pymysql://test:secret@192.0.2.1/monitor'))
        run.assert_not_called()

    def test_busy_database_port_never_creates_replacement_container(self):
        from sqlalchemy.engine import make_url
        socket_context=Mock();socket_context.__enter__=Mock();socket_context.__exit__=Mock(return_value=False)
        with patch.object(init.socket,'create_connection',return_value=socket_context),patch.object(init.subprocess,'run') as run,self.assertRaises(RuntimeError):
            init.docker_database(make_url('mysql+pymysql://test:secret@127.0.0.1/monitor'))
        run.assert_not_called()

    def test_docker_creation_does_not_put_passwords_on_command_line(self):
        from sqlalchemy.engine import make_url
        url=make_url('mysql+pymysql://root:private-database-secret@127.0.0.1/monitor')
        result=Mock(returncode=1,stderr='Error: No such object: watch-nanda-mysql')
        with patch.object(init.socket,'create_connection',side_effect=ConnectionRefusedError),patch.object(init.shutil,'which',return_value='/usr/bin/docker'),patch.object(init.subprocess,'run',return_value=result) as run,patch.object(init,'connect_server'):
            init.docker_database(url)
        create=next(c for c in run.call_args_list if c.args[0][:2]==['docker','run'])
        self.assertNotIn(url.password,' '.join(create.args[0]))
        self.assertIn('127.0.0.1:3306:3306',create.args[0])
        self.assertEqual(create.kwargs['env']['MYSQL_ROOT_PASSWORD'],url.password)

    def test_docker_inspection_error_does_not_create_container(self):
        from sqlalchemy.engine import make_url
        with patch.object(init.socket,'create_connection',side_effect=ConnectionRefusedError),patch.object(init.shutil,'which',return_value='/usr/bin/docker'),patch.object(init.subprocess,'run',return_value=Mock(returncode=1,stderr='Docker daemon error')) as run,self.assertRaises(RuntimeError):
            init.docker_database(make_url('mysql+pymysql://root:secret@127.0.0.1/monitor'))
        self.assertFalse(any(c.args[0][:2]==['docker','run'] for c in run.call_args_list))

    def test_shell_syntax_and_lf(self):
        bash=shutil.which('bash')
        if not bash and Path('C:/Program Files/Git/bin/bash.exe').exists():
            bash='C:/Program Files/Git/bin/bash.exe'
        if not bash: self.skipTest('Bash not available')
        for name in ['start.sh','setup.sh','stop.sh','install-agent.sh','agent/build.sh']:
            self.assertNotIn(b'\r\n',(ROOT/name).read_bytes())
            subprocess.run([bash,'-n',str(ROOT/name)],check=True,capture_output=True)
            help_result=subprocess.run([bash,str(ROOT/name),'--help'],check=True,capture_output=True,
                                       env=dict(os.environ,PYTHON_BIN=sys.executable.replace('\\','/')))
            self.assertIn(b'--',help_result.stdout) if name!='stop.sh' else self.assertTrue(help_result.stdout)


class LifecycleTests(unittest.TestCase):
    def test_systemd_directory_is_a_path_value_and_escapes_specifiers(self):
        self.assertEqual(life.systemd_directory('/srv/watch nanda%/backend'),'/srv/watch nanda%%/backend')
        for value in ('relative/backend','/srv/bad\npath','/srv/bad\rpath','/srv/bad\x00path'):
            with self.assertRaises(RuntimeError):life.systemd_directory(value)

    @unittest.skipUnless(sys.platform=='linux','Requires the Linux systemd parser')
    def test_generated_systemd_units_pass_the_real_parser_with_spaces_and_percent_paths(self):
        if not shutil.which('systemd-analyze'):self.skipTest('systemd-analyze not available')
        with tempfile.TemporaryDirectory(prefix='watch nanda% ') as directory:
            root=Path(directory);units=root/'units';units.mkdir();systemd=root/'systemd';systemd.mkdir()
            python=root/'backend/.venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
            (root/'frontend').mkdir()
            def path(value):
                return units if str(value)=='/etc/systemd/system' else systemd if str(value)=='/run/systemd/system' else Path(value)
            with patch.object(life,'ROOT',root),patch.object(life,'Path',side_effect=path),patch.object(life.os,'geteuid',return_value=0),patch.object(life,'unit_directory',return_value=None),patch.object(life,'read_state',return_value=[]),patch.object(life,'nginx_owned',return_value=False),patch.object(life.shutil,'which',return_value=sys.executable),patch.object(life,'run'):
                life.install_services()
            for name,role in ((life.BACKEND_UNIT,'backend'),(life.FRONTEND_UNIT,'frontend')):
                self.assertIn('WorkingDirectory='+str(root/role).replace('%','%%')+'\n',(units/name).read_text())
            checked=subprocess.run(['systemd-analyze','verify',str(units/life.BACKEND_UNIT),str(units/life.FRONTEND_UNIT)],capture_output=True,text=True,timeout=20)
            self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)

    @unittest.skipUnless(sys.platform=='linux','Requires Linux TCP reuse semantics')
    def test_port_probe_distinguishes_a_listener_from_its_closed_time_wait_connections(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
            self.assertFalse(life.port_free(port))
            with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
                accepted,_=listener.accept();accepted.close()
                self.assertEqual(client.recv(1),b'')
        self.assertTrue(life.port_free(port))

    def test_registered_waf_nginx_is_owned_only_by_its_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'nginx.conf'
            config.write_text('# watch-nanda-managed-root: '+str(life.ROOT.resolve())+'\nroot /var/www/watch-nanda;\n')
            self.assertTrue(life.nginx_owned(config))
            config.write_text('# watch-nanda-managed-root: /another/checkout\nroot /var/www/watch-nanda;\n')
            self.assertFalse(life.nginx_owned(config))

    def test_https_health_check_verifies_ca_and_uses_https_origin(self):
        response=Mock();response.__enter__=Mock(return_value=Mock(status=200));response.__exit__=Mock(return_value=False)
        with patch.dict(os.environ,{'WATCH_NANDA_HTTPS_HOST':'monitor.example.com'}),patch.object(life.ssl,'create_default_context') as context,patch.object(life,'urlopen',return_value=response) as request:
            life.wait_https()
        self.assertEqual(request.call_args.args[0],'https://monitor.example.com:443/')
        self.assertIs(request.call_args.kwargs['context'],context.return_value)

    def test_systemd_from_another_checkout_is_never_touched(self):
        with patch.object(life,'unit_directory',return_value='/unrelated/backend'),self.assertRaises(RuntimeError):
            life.managed_services()

    def test_unrecognized_nginx_is_never_stopped(self):
        directories={life.BACKEND_UNIT:str(life.ROOT/'backend'),life.FRONTEND_UNIT:None}
        with patch.object(life,'unit_directory',side_effect=lambda unit: directories[unit]),patch.object(life,'nginx_owned',return_value=False),self.assertRaises(RuntimeError):
            life.managed_services()

    def test_known_production_nginx_uses_systemd_without_duplicate_processes(self):
        with patch.object(life,'unit_directory',return_value=str(life.ROOT/'backend')),patch.object(life,'nginx_owned',return_value=True):
            self.assertEqual(life.managed_services(),[life.BACKEND_UNIT,'nginx.service'])

    def test_reused_pid_is_not_owned(self):
        record={'pid':123,'ticks':'1','cwd':str(life.ROOT/'backend'),'command':'digest','role':'backend'}
        with patch.object(life,'process_identity',return_value={**{k:record[k] for k in ['pid','ticks','cwd','command']},'ticks':'2'}):
            self.assertFalse(life.owned(record))

    def test_occupied_port_is_not_silently_reassigned(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ['backend/.venv/bin/python','frontend/node_modules/vite/bin/vite.js','frontend/dist/index.html']:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('test')
            with patch.object(life,'ROOT',root),patch.object(life,'read_state',return_value=[]),patch.object(life.shutil,'which',return_value='node'),patch.object(life,'run'),patch.object(life,'port_free',return_value=False),patch.object(life.subprocess,'Popen') as spawn,self.assertRaises(RuntimeError):
                life.standalone_start()
            spawn.assert_not_called()

    @unittest.skipUnless(sys.platform=='linux','Requires Linux /proc and process groups')
    def test_real_standalone_start_twice_stop_and_preserve_unrelated_process(self):
        if not shutil.which('openssl'): self.skipTest('OpenSSL needed for HTTPS preview fixture')
        with tempfile.TemporaryDirectory(prefix='watch nanda ') as directory:
            root=Path(directory)
            for child in ['backend/.venv/bin','frontend/node_modules/vite/bin','frontend/dist']:
                (root/child).mkdir(parents=True)
            (root/'backend/.venv/bin/python').symlink_to(sys.executable)
            (root/'backend/migrate_agent.py').write_text('print("test schema ready")\n')
            server='import sys\nfrom http.server import HTTPServer,BaseHTTPRequestHandler\nclass Handler(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200);self.end_headers();self.wfile.write(b"ok")\nHTTPServer(("127.0.0.1",int(sys.argv[sys.argv.index("--port")+1])),Handler).serve_forever()\n'
            (root/'backend/uvicorn.py').write_text(server)
            tls_server=server.replace('HTTPServer(("127.0.0.1",int(sys.argv[sys.argv.index("--port")+1])),Handler).serve_forever()',
                'import ssl,os\nsrv=HTTPServer(("127.0.0.1",int(sys.argv[sys.argv.index("--port")+1])),Handler)\nctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)\nctx.load_cert_chain(os.environ["WATCH_NANDA_TLS_CERT"],os.environ["WATCH_NANDA_TLS_KEY"])\nsrv.socket=ctx.wrap_socket(srv.socket,server_side=True)\nsrv.serve_forever()')
            (root/'frontend/node_modules/vite/bin/vite.js').write_text(tls_server)
            cert=root/'test.crt';key=root/'test.key'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=127.0.0.1','-addext','subjectAltName=IP:127.0.0.1'],check=True,capture_output=True)
            (root/'frontend/dist/index.html').write_text('test')
            def unused_port():
                with socket.socket() as s:
                    s.bind(('127.0.0.1',0));return s.getsockname()[1]
            unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
            records=[]
            try:
                with patch.dict(os.environ,{'WATCH_NANDA_HTTPS_HOST':'127.0.0.1','WATCH_NANDA_TLS_CERT':str(cert),'WATCH_NANDA_TLS_KEY':str(key),'WATCH_NANDA_TLS_CA':str(cert)}),patch.object(life,'ROOT',root),patch.object(life,'RUNTIME',root/'.runtime'),patch.object(life,'BACKEND_PORT',unused_port()),patch.object(life,'FRONTEND_PORT',unused_port()),patch.object(life.shutil,'which',return_value=sys.executable):
                    life.ensure_runtime()
                    life.standalone_start();records=life.read_state()
                    self.assertEqual(len(records),2)
                    life.standalone_start()
                    self.assertEqual(records,life.read_state())
                    life.standalone_stop(life.read_state())
                    self.assertFalse(life.read_state())
                    self.assertIsNone(unrelated.poll())
                    for record in records:
                        try: os.waitpid(record['pid'],0)
                        except ChildProcessError: pass
            finally:
                unrelated.terminate();unrelated.wait()
                for record in records:
                    try: os.killpg(record['pid'],15)
                    except ProcessLookupError: pass


if __name__=='__main__': unittest.main()
