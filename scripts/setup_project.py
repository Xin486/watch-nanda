"""One entry point for fresh installs and recoverable, project-scoped upgrades."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from core.site_config import load_site, apply_site
import bootstrap
import lifecycle as life


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


@contextmanager
def installation_lock():
    import fcntl
    life.ensure_runtime()
    with (ROOT/'.runtime/setup.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('本项目已有初始化/升级正在进行，请等待完成') from None
        yield


def detect_host():
    if shutil.which('ip'):
        result = subprocess.run(['ip', '-j', 'route', 'get', '1.1.1.1'], capture_output=True, text=True)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)[0]['prefsrc']
            except (KeyError, IndexError, ValueError):
                pass
    addresses = [item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                 if not ipaddress.ip_address(item[4][0]).is_loopback]
    if addresses:
        return addresses[0]
    raise RuntimeError('无法确定 HTTPS 地址，请指定 --host IP或域名')


def configure_site(host, node):
    previous = load_site(ROOT)
    # Read existing .env without importing application dependencies into bootstrap.
    # Actual .env overrides remain authoritative when the application starts.
    from dotenv import dotenv_values
    configured = {k: v for k, v in dotenv_values(ROOT/'backend/.env').items() if v is not None}
    old_host = os.environ.get('WATCH_NANDA_HTTPS_HOST') or configured.get('WATCH_NANDA_HTTPS_HOST') or previous.get('WATCH_NANDA_HTTPS_HOST')
    if host and old_host and host != old_host:
        raise RuntimeError('已有 HTTPS 地址与 --host 不符；升级不自动轮换证书，请单独规划地址迁移')
    host = host or old_host or ('192.168.1.24' if life.nginx_owned() else detect_host())
    # Validate that the argument is a host, never a URL/path or shell expression.
    import re
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host) or '..' in host:
        raise RuntimeError('--host 需要 IPv4 地址或域名，不含端口/协议/路径')
    secure = Path('/etc/node-monitor') if os.geteuid() == 0 else ROOT/'.runtime/secure'
    defaults = {'WATCH_NANDA_HTTPS_HOST': host, 'WATCH_NANDA_NODE': node,
                'WATCH_NANDA_TLS_CA': str(secure/'tls/ca.crt'),
                'WATCH_NANDA_TLS_CERT': str(secure/'tls/server.crt'),
                'WATCH_NANDA_TLS_KEY': str(secure/'tls/server.key'),
                'SSH_CREDENTIAL_KEY_FILE': str(secure/'ssh-credentials.key'),
                'OPS_PASSWORD_FILE': str(secure/'ops-password.json'),
                'SSH_PROFILES_FILE': str(secure/'ssh-profiles.json')}
    values = {key: os.environ.get(key) or configured.get(key) or previous.get(key) or value
              for key, value in defaults.items()}
    values['WATCH_NANDA_NODE'] = node
    # Preserve the existing Nanjing split LAN/WAN report URL policy.
    if not life.nginx_owned():
        values['AGENT_REPORT_URL'] = (os.environ.get('AGENT_REPORT_URL') or configured.get('AGENT_REPORT_URL')
                                      or previous.get('AGENT_REPORT_URL') or f'https://{host}:3000/api/agent/report')
        values['AGENT_CA_FILE'] = (os.environ.get('AGENT_CA_FILE') or configured.get('AGENT_CA_FILE')
                                   or previous.get('AGENT_CA_FILE') or values['WATCH_NANDA_TLS_CA'])
    path = ROOT/'.runtime/site.json'
    temp = path.with_suffix('.new')
    temp.write_text(json.dumps(values, ensure_ascii=False, indent=2)+'\n')
    temp.chmod(0o600)
    temp.replace(path)
    os.environ.update(values)
    return values


def check_agent_jobs():
    """Never interrupt an installer's SSH session or credential rollback."""
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if (proc/'cwd').resolve().is_relative_to(ROOT):
                args = (proc/'cmdline').read_bytes().split(b'\0')
                if any(Path(os.fsdecode(arg)).name == 'agent_job.py' for arg in args if arg):
                    raise RuntimeError('仍有 Agent 部署任务运行；请等待部署/回滚完成后重试升级')
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue


def active_units(services):
    return [unit for unit in services if subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0]


def legacy_listener(port, role):
    """Recognize old nohup launches by listener PID, exact cwd and entry point."""
    if life.port_free(port):
        return None
    if not shutil.which('ss'):
        raise RuntimeError('旧端口被占用且缺少 ss，无法确认进程归属；不会自动停止')
    import re
    output = run(['ss', '-Hlnpt', f'sport = :{port}'], capture_output=True, text=True).stdout
    pids = set(int(value) for value in re.findall(r'pid=(\d+)', output))
    if len(pids) != 1:
        raise RuntimeError(f'{port} 端口进程归属不明确，不会自动停止')
    pid = pids.pop()
    identity = life.process_identity(pid)
    args = [os.fsdecode(item) for item in (Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0') if item]
    directory = ROOT/role
    if role == 'backend':
        recognized = ('uvicorn' in args and 'main:app' in args or
                      any(value in ('main.py', str(directory/'main.py')) for value in args[1:]))
    else:
        recognized = str(directory/'node_modules/vite/bin/vite.js') in args
    if not identity or identity['cwd'] != str(directory.resolve()) or not recognized:
        raise RuntimeError(f'{port} 端口不属于本项目已知入口，不会自动停止')
    return dict(identity, role=role, args=args)


def stop_legacy(record):
    import signal
    expected = {key: record[key] for key in ('pid', 'ticks', 'cwd', 'command')}
    for signum, seconds in ((signal.SIGTERM, 45), (signal.SIGKILL, 10)):
        if life.process_identity(record['pid']) != expected:
            return
        os.kill(record['pid'], signum)
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            if life.process_identity(record['pid']) != expected:
                return
            time.sleep(0.2)
    raise RuntimeError('已确认归属的旧进程仍未终止（可能为 D 状态）；不继续覆盖或重启整机')


class Upgrade:
    def __init__(self):
        self.backup = ROOT/'.runtime/setup-backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.backup.mkdir(parents=True, mode=0o700)
        self.moved = []
        self.units = {}
        self.services = life.managed_services()
        self.active = active_units(self.services) if self.services else []
        self.background = [r for r in life.read_state() if life.owned(r)]
        self.legacy = []
        if not self.services and not self.background:
            for role, port in (('backend',7980),('frontend',3000)):
                record = legacy_listener(port, role)
                if record:
                    self.legacy.append(record)
        self.stopped = False
        self.environment = dict(os.environ)
        for unit in (life.BACKEND_UNIT, life.FRONTEND_UNIT):
            path = Path('/etc/systemd/system')/unit
            self.units[unit] = path.read_bytes() if path.is_file() else None
            if self.units[unit] is not None:
                (self.backup/unit).write_bytes(self.units[unit])
        for source in [ROOT/'backend/.env', ROOT/'AGENTS.md', ROOT/'.runtime/site.json']:
            if source.is_file():
                target = self.backup/source.name
                shutil.copy2(source, target)
                target.chmod(0o600)
        if life.nginx_owned():
            target = Path('/var/www/watch-nanda')
            if (target/'index.html').is_file():
                shutil.copy2(target/'index.html', self.backup/'published-index.html')
        (self.backup/'RECOVERY.md').write_text(
            '# Setup recovery\n\nConfiguration/database backup files are private. Do not commit them.\n'
            'The database is additive only and is never automatically downgraded.\n'
            'On failure the installer restores dependency directories and service units.\n'
            'For manual recovery stop this project, restore directories to their recorded paths,\n'
            'restore the matching Git revision and service unit files, daemon-reload and start.\n'
            'database-before.sql.gz is a consistent logical backup; restore into a separate database first.\n')

    def stop(self):
        check_agent_jobs()
        if self.services:
            # Keep Nginx/WAF running; only the backend and owned preview need stop.
            services = [unit for unit in self.services if unit != 'nginx.service']
            run(['systemctl', 'stop', '--no-block', *services])
            self.stopped = True
            deadline = time.monotonic()+150
            while time.monotonic() < deadline:
                states = run(['systemctl', 'show', *services, '-p', 'ActiveState', '--value'], capture_output=True, text=True).stdout.split()
                if states and all(state in ('inactive', 'failed') for state in states):
                    break
                time.sleep(1)
            else:
                raise RuntimeError('项目服务停止超时；不替换依赖，请检查遗留进程')
        else:
            life.standalone_stop(self.background)
            self.stopped = True
            for record in self.legacy:
                stop_legacy(record)
        self.stopped = True
        life.prepare_setup()
        # Unregistered legacy listeners are not safe to terminate automatically.
        if not life.port_free(7980) or (not life.nginx_owned() and not life.port_free(3000)):
            raise RuntimeError('项目端口仍被未登记进程占用；请确认旧进程归属后停止，不会按名称全局杀进程')

    def move(self, relative):
        source = ROOT/relative
        if source.exists():
            destination = self.backup/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            self.moved.append(relative)
        else:
            self.moved.append(relative)  # remove newly-created directory on failure
        (self.backup/'directories.json').write_text(json.dumps(self.moved))

    def rollback(self):
        print('安装失败，恢复本次替换的运行环境及服务配置…', flush=True)
        try:
            services = life.managed_services()
            if services:
                run(['systemctl', 'stop', *[u for u in services if u != 'nginx.service']], timeout=150)
            else:
                life.standalone_stop(life.read_state())
            for relative in reversed(self.moved):
                current, saved = ROOT/relative, self.backup/relative
                if current.is_dir() and not current.is_symlink():
                    shutil.rmtree(current)
                elif current.exists() or current.is_symlink():
                    current.unlink()
                if saved.exists():
                    saved.rename(current)
            site = ROOT/'.runtime/site.json'
            if (self.backup/'site.json').exists():
                shutil.copy2(self.backup/'site.json', site)
            elif site.exists():
                site.unlink()
            discovered_node = os.environ.get('WATCH_NANDA_NODE')
            os.environ.clear()
            os.environ.update(self.environment)
            apply_site(ROOT)
            if discovered_node:
                os.environ.setdefault('WATCH_NANDA_NODE', discovered_node)
            if self.services or Path('/run/systemd/system').exists() and os.geteuid() == 0:
                for name, value in self.units.items():
                    path = Path('/etc/systemd/system')/name
                    if value is not None:
                        path.write_bytes(value)
                    elif path.exists() and life.unit_directory(name) in (str(ROOT/'backend'), str(ROOT/'frontend')):
                        run(['systemctl', 'disable', name])
                        path.unlink()
                run(['systemctl', 'daemon-reload'])
            published = self.backup/'published-index.html'
            if published.is_file():
                shutil.copy2(published, '/var/www/watch-nanda/index.html')
            if self.active:
                run(['systemctl', 'start', *self.active], timeout=150)
            elif self.background:
                life.standalone_start()
            elif self.legacy:
                for record in self.legacy:
                    expected = {key:record[key] for key in ('pid','ticks','cwd','command')}
                    if life.process_identity(record['pid']) == expected:
                        continue
                    with (ROOT/record['role']/'setup-recovery.log').open('ab') as log:
                        subprocess.Popen(record['args'], cwd=record['cwd'], stdout=log, stderr=log,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
        except BaseException as error:
            print('自动恢复未确认：'+type(error).__name__+'；请根据 '+str(self.backup/'RECOVERY.md')+' 核对。', flush=True)


def database_needs_docker(python, enabled, existing_install=False):
    source = '''from core.config import settings
from sqlalchemy.engine import make_url
import socket,sys
url=make_url(settings.DATABASE_URL)
if url.host not in ('localhost','127.0.0.1','::1'):sys.exit(0)
try:
 socket.create_connection((url.host,url.port or 3306),timeout=3).close()
except ConnectionRefusedError:sys.exit(42)
'''
    result = subprocess.run([str(python), '-c', source], cwd=ROOT/'backend', stderr=subprocess.DEVNULL)
    if result.returncode == 42:
        if existing_install:
            raise RuntimeError('已有部署的本地数据库未监听；请先恢复原容器/数据卷，不创建替代数据库')
        bootstrap.ensure_docker(enabled)
    elif result.returncode:
        raise RuntimeError('数据库地址探测失败，不创建替代数据库')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--systemd', action='store_true')
    mode.add_argument('--standalone', action='store_true')
    parser.add_argument('--host')
    parser.add_argument('--skip-deps', action='store_true')
    parser.add_argument('--skip-agent-build', action='store_true')
    parser.add_argument('--no-bootstrap', action='store_true')
    parser.add_argument('--no-start', action='store_true')
    parser.add_argument('--ops-password-file')
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise RuntimeError('setup 仅支持 Linux')
    with installation_lock():
        os.environ['WATCH_NANDA_SETUP_PID'] = str(os.getpid())
        import ssl
        if not ssl.create_default_context().get_ca_certs():
            bootstrap.packages(['ca-certificates'], ['ca-certificates'], not args.no_bootstrap)
        check_agent_jobs()
        transaction = Upgrade()
        print('本次备份：'+str(transaction.backup), flush=True)
        systemd = not args.standalone and (args.systemd or bool(transaction.services) or
                                           os.geteuid() == 0 and Path('/run/systemd/system').is_dir())
        if args.standalone and transaction.services:
            raise RuntimeError('已有 systemd 服务，升级应保留托管方式，不能同时创建后台实例')
        if systemd and (os.geteuid() != 0 or not Path('/run/systemd/system').is_dir()):
            raise RuntimeError('systemd 安装需要 root 和运行中的 systemd')
        python = bootstrap.python_runtime(not args.no_bootstrap)
        node = bootstrap.node_runtime(not args.no_bootstrap)
        os.environ['PATH'] = str(Path(node).parent)+os.pathsep+os.environ['PATH']
        os.environ.setdefault('PIP_DEFAULT_TIMEOUT', '60')
        os.environ.setdefault('PIP_RETRIES', '5')
        npm = Path(node).parent/'npm'
        try:
            transaction.stop()
            venv = ROOT/'backend/.venv'
            if not args.skip_deps:
                transaction.move('backend/.venv')
                try:
                    run([python, '-m', 'venv', venv])
                except subprocess.CalledProcessError:
                    bootstrap.packages(['python3-venv'], ['python3-pip'], not args.no_bootstrap)
                    run([python, '-m', 'venv', venv])
                run([venv/'bin/python', '-m', 'pip', 'install', '-r', ROOT/'backend/requirements.txt'])
                transaction.move('frontend/node_modules')
                run([npm, 'ci', '--no-audit', '--no-fund'], cwd=ROOT/'frontend')
            elif not bootstrap.compatible_python(venv/'bin/python'):
                raise RuntimeError('--skip-deps 需要已初始化且兼容的 backend/.venv')
            python = venv/'bin/python'
            # Execute the dependency-using steps in the application's new venv.
            run([python, ROOT/'scripts/initialize.py', '--prepare-env'])
            # dotenv is installed in that venv, not necessarily in system Python.
            site_args = [python, __file__, '--configure-site', node, args.host or '']
            run(site_args)
            os.environ.update(load_site(ROOT))
            # Back up existing site secrets before security initialization. New
            # installs leave these absent; repeat installs retain their bytes.
            for key in ('WATCH_NANDA_TLS_CA', 'WATCH_NANDA_TLS_CERT', 'WATCH_NANDA_TLS_KEY',
                        'SSH_CREDENTIAL_KEY_FILE', 'OPS_PASSWORD_FILE', 'SSH_PROFILES_FILE'):
                source = Path(os.environ[key])
                if source.is_file():
                    destination = transaction.backup/'protected'/key
                    destination.parent.mkdir(mode=0o700, exist_ok=True)
                    shutil.copy2(source, destination)
                    destination.chmod(0o600)
            for source, name in ((Path(os.environ['WATCH_NANDA_TLS_CA']).with_name('ca.key'), 'CA_PRIVATE_KEY'),
                                 (Path.home()/'.ssh/known_hosts', 'KNOWN_HOSTS')):
                if source.is_file():
                    destination = transaction.backup/'protected'/name
                    destination.parent.mkdir(mode=0o700, exist_ok=True)
                    shutil.copy2(source, destination)
                    destination.chmod(0o600)
            security = [python, ROOT/'scripts/setup_security.py']
            if args.ops_password_file:
                security += ['--ops-password-file', str(Path(args.ops_password_file).absolute())]
            run(security)
            database_needs_docker(python, not args.no_bootstrap, bool(transaction.services or transaction.background or transaction.legacy))
            initialize = [python, ROOT/'scripts/initialize.py', '--backup-dir', transaction.backup]
            if transaction.services or transaction.background or transaction.legacy:
                initialize.append('--existing-install')
            run(initialize)
            transaction.move('frontend/dist')
            run([npm, 'run', 'build'], cwd=ROOT/'frontend')
            if args.skip_agent_build:
                print('已跳过 Agent 包构建；网页 Agent 部署需要稍后运行 bash agent/build.sh。', flush=True)
            else:
                # Skip only a current, validated bundle for the local architecture.
                import platform
                bundle = ROOT/'agent/dist'/('node-monitor-agent-linux-'+platform.machine()+'.tar.gz')
                checked = subprocess.run([python, '-c', 'import sys;sys.path.insert(0,"scripts");from install_agent import bundle_details;from pathlib import Path;bundle_details(Path(sys.argv[1]))', bundle],
                                         cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if checked.returncode:
                    bootstrap.ensure_docker(not args.no_bootstrap)
                    run(['bash', ROOT/'agent/build.sh'])
            if systemd:
                run([python, ROOT/'scripts/lifecycle.py', 'install'])
            if not args.no_start:
                run([python, ROOT/'scripts/lifecycle.py', 'start'], timeout=180)
            (transaction.backup/'SUCCESS').write_text(datetime.now().isoformat()+'\n')
            print('初始化/覆盖升级完成。已有配置、密钥、节点 ID 和历史数据保留。', flush=True)
            print('页面：https://'+os.environ['WATCH_NANDA_HTTPS_HOST']+('' if life.nginx_owned() else ':3000'), flush=True)
        except BaseException:
            if transaction.stopped:
                transaction.rollback()
            raise


if __name__ == '__main__':
    try:
        if len(sys.argv) == 4 and sys.argv[1] == '--configure-site':
            configure_site(sys.argv[3] or None, sys.argv[2])
        else:
            main()
    except (Exception, KeyboardInterrupt) as error:
        # Dependency/SQL exceptions may contain configuration values; don't dump them.
        print('初始化失败：'+(str(error) if isinstance(error, RuntimeError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
