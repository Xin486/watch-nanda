"""Project-scoped Linux lifecycle management, with systemd and standalone modes."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from core.site_config import apply_site
BACKEND_UNIT = 'watch-nanda-backend.service'
FRONTEND_UNIT = 'watch-nanda-frontend.service'
RUNTIME = ROOT / '.runtime'
BACKEND_PORT = 7980
FRONTEND_PORT = 3000


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def unit_directory(unit):
    if not Path('/run/systemd/system').exists() or not shutil.which('systemctl'):
        return None
    result = subprocess.run(['systemctl', 'show', unit, '-p', 'LoadState', '-p', 'WorkingDirectory'],
                            capture_output=True, text=True, timeout=10)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    return values.get('WorkingDirectory', '') if values.get('LoadState') == 'loaded' else None


def nginx_owned(config=None):
    # Existing production nginx belongs exclusively to this application. Never
    # stop a shared nginx merely because it is running or listening on port 3000.
    config = config or Path('/etc/nginx/nginx.conf')
    template = ROOT / 'deploy' / 'production' / 'nginx.conf'
    if not config.is_file():
        return False
    content = config.read_text()
    marker = '# watch-nanda-managed-root: ' + str(ROOT.resolve())
    registered = marker in content.splitlines() and 'root /var/www/watch-nanda;' in content
    return registered or (template.is_file() and content.strip() == template.read_text().strip())


def managed_services():
    directory = unit_directory(BACKEND_UNIT)
    if directory is None:
        return None
    if Path(directory).resolve() != (ROOT / 'backend').resolve():
        raise RuntimeError('同名 systemd 后端属于其他目录，不会操作其他项目的服务')
    if nginx_owned():
        return [BACKEND_UNIT, 'nginx.service']
    frontend = unit_directory(FRONTEND_UNIT)
    if frontend and Path(frontend).resolve() == (ROOT / 'frontend').resolve():
        return [BACKEND_UNIT, FRONTEND_UNIT]
    raise RuntimeError('已发现本项目 systemd 后端，但前端服务未确认；请运行 sudo bash setup.sh --systemd')


def systemctl(action, services):
    command = ['systemctl', action, *services]
    if os.geteuid() != 0:
        command.insert(0, 'sudo')
    run(command)


def ensure_runtime():
    if RUNTIME.is_symlink():
        raise RuntimeError('.runtime 不得为符号链接')
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    if RUNTIME.stat().st_uid != os.geteuid():
        raise RuntimeError('.runtime 属于其他账户；请使用安装时的账户启动/停止项目')
    RUNTIME.chmod(0o700)


@contextmanager
def locked():
    import fcntl
    ensure_runtime()
    with (RUNTIME / 'lifecycle.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有启动/停止操作正在运行，请稍后重试') from None
        yield


def process_identity(pid):
    try:
        proc = Path('/proc') / str(pid)
        fields = (proc / 'stat').read_text().rpartition(') ')[2].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'ticks': fields[19], 'cwd': str((proc / 'cwd').resolve()),
                'command': hashlib.sha256((proc / 'cmdline').read_bytes()).hexdigest()}
    except (OSError, IndexError):
        return None


def owned(record):
    if not isinstance(record, dict) or not isinstance(record.get('pid'), int):
        return False
    identity = process_identity(record['pid'])
    expected_cwd = (ROOT / ('backend' if record.get('role') == 'backend' else 'frontend')).resolve()
    try:
        return bool(identity and all(identity[k] == record.get(k) for k in identity)
                    and Path(identity['cwd']) == expected_cwd and os.getpgid(record['pid']) == record['pid'])
    except ProcessLookupError:
        return False


def read_state():
    file = RUNTIME / 'processes.json'
    if not file.exists():
        return []
    value = json.loads(file.read_text())
    if not isinstance(value, list):
        raise RuntimeError('进程记录格式不正确，已停止操作')
    return value


def save_state(records):
    temp = RUNTIME / 'processes.new'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(records, stream)
    temp.replace(RUNTIME / 'processes.json')


def port_free(port):
    with socket.socket() as sock:
        # Match the server socket so connections in TIME_WAIT do not look like
        # an unrelated listener after a clean shutdown.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', port))
            return True
        except OSError:
            return False


def wait_http(port, path='/', timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f'http://127.0.0.1:{port}{path}', timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f'{port} 端口健康检查失败；请检查本项目日志')


def wait_https(port=443, path='/', timeout=40):
    host = os.environ.get('WATCH_NANDA_HTTPS_HOST', '192.168.1.24')
    ca = Path(os.environ.get('WATCH_NANDA_TLS_CA', '/etc/node-monitor/tls/ca.crt'))
    context = ssl.create_default_context(cafile=str(ca) if ca.is_file() else None)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f'https://{host}:{port}{path}', context=context, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError(f'HTTPS {host}:{port} 健康检查失败；请检查证书信任与服务日志')


def standalone_stop(records):
    active = [r for r in records if owned(r)]
    for record in reversed(active):
        try:
            os.killpg(record['pid'], signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 45
    while active and time.monotonic() < deadline:
        active = [r for r in active if owned(r)]
        if active:
            time.sleep(1)
    save_state(active)
    if active:
        raise RuntimeError('部分项目进程未在45秒内退出；记录已保留，未强制杀死进程')
    print('本项目进程已停止，数据库未停止。')


def standalone_start():
    previous = [r for r in read_state() if owned(r)]
    python = ROOT / 'backend' / '.venv' / 'bin' / 'python'
    vite = ROOT / 'frontend' / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    node = os.environ.get('WATCH_NANDA_NODE') or shutil.which('node')
    if not python.is_file() or not vite.is_file() or not node or not (ROOT / 'frontend' / 'dist' / 'index.html').is_file():
        raise RuntimeError('项目尚未初始化，请先运行 bash setup.sh')
    # Authenticate/inspect schema before spawning a backend or a frontend.
    run([str(python), str(ROOT / 'backend' / 'migrate_agent.py'), '--require-ready'])
    for role, port in [('backend', BACKEND_PORT), ('frontend', FRONTEND_PORT)]:
        if not any(r.get('role') == role for r in previous) and not port_free(port):
            raise RuntimeError(f'{port} 端口被未登记进程占用，拒绝重复启动或自动换端口')
    commands = [
        ('backend', [str(python), '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(BACKEND_PORT)]),
        ('frontend', [node, str(vite), 'preview', '--host', '0.0.0.0', '--port', str(FRONTEND_PORT), '--strictPort']),
    ]
    new = []
    try:
        for role, command in commands:
            if any(r.get('role') == role for r in previous):
                continue
            with (ROOT / role / (role + '.log')).open('ab') as log:
                process = subprocess.Popen(command, cwd=ROOT / role, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log, start_new_session=True)
            # Child must exec/chdir before its immutable identity is recorded.
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(role + ' 启动失败，请检查对应日志')
                record = process_identity(process.pid)
                if record and record['cwd'] == str((ROOT / role).resolve()):
                    break
                time.sleep(0.01)
            if not record or record['cwd'] != str((ROOT / role).resolve()):
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
                raise RuntimeError(role + ' 无法读取启动进程身份')
            record['role'] = role
            new.append(record)
            save_state(previous + new)
        wait_http(BACKEND_PORT, '/api/servers')
        wait_https(FRONTEND_PORT)
    except BaseException:
        try:
            standalone_stop(new)
        finally:
            save_state(previous + [r for r in new if owned(r)])
        raise
    print('项目已在后台运行（重复 start 不会新增实例）。页面：https://<服务器IP>:3000；API 使用同源 HTTPS。')


def systemd_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def systemd_directory(value):
    # WorkingDirectory takes a path value, not the ExecStart argument grammar.
    value = str(value)
    if not value.startswith('/') or any(char in value for char in '\n\r\x00'):
        raise RuntimeError('systemd 工作目录必须是有效的绝对路径')
    return value.replace('%', '%%')


def systemd_environment(value):
    # Environment= does not expand $, unlike ExecStart's argument grammar.
    if any(char in value for char in '\r\n\x00'):
        raise RuntimeError('systemd 环境配置不能包含换行或空字符')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'


def install_services():
    if os.geteuid() != 0 or not Path('/run/systemd/system').exists():
        raise RuntimeError('--systemd 需要 root 及正在运行的 systemd')
    for unit, directory in [(BACKEND_UNIT, ROOT/'backend'), (FRONTEND_UNIT, ROOT/'frontend')]:
        existing = unit_directory(unit)
        if existing is not None and Path(existing).resolve() != directory.resolve():
            raise RuntimeError('同名 systemd 服务属于其他项目，不会覆盖：' + unit)
    runtime = read_state() if RUNTIME.exists() else []
    if any(owned(r) for r in runtime):
        raise RuntimeError('请先运行 stop.sh 停止 standalone 实例，再安装 systemd 服务')
    python = ROOT/'backend'/'.venv'/'bin'/'python'
    command = systemd_quote(python) + ' -m uvicorn main:app --host 127.0.0.1 --port 7980 --no-server-header'
    units = [(BACKEND_UNIT, ROOT/'backend', command)]
    if nginx_owned():
        # Keep old hashed assets for open browser tabs; publish index last.
        target = Path('/var/www/watch-nanda')
        source = ROOT/'frontend'/'dist'
        target.mkdir(parents=True, exist_ok=True, mode=0o755)
        target.chmod(0o755)
        for file in source.rglob('*'):
            if file.is_file() and file.name != 'index.html':
                dest = target/file.relative_to(source)
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                shutil.copyfile(file, dest)
                dest.chmod(0o644)
        for directory in target.rglob('*'):
            if directory.is_dir():
                directory.chmod(0o755)
        index = target/'index.next.html'
        shutil.copyfile(source/'index.html', index)
        index.chmod(0o644)
        index.replace(target/'index.html')
        frontend_service = 'nginx.service'
    else:
        node = os.environ.get('WATCH_NANDA_NODE') or shutil.which('node')
        if not node:
            raise RuntimeError('找不到 Node.js')
        vite = ROOT/'frontend'/'node_modules'/'vite'/'bin'/'vite.js'
        command = systemd_quote(node) + ' ' + systemd_quote(vite) + ' preview --host 0.0.0.0 --port 3000 --strictPort'
        units.append((FRONTEND_UNIT, ROOT/'frontend', command))
        frontend_service = FRONTEND_UNIT
    user = os.environ.get('WATCH_NANDA_SERVICE_USER', 'root')
    import pwd
    pwd.getpwnam(user)
    for name, directory, command in units:
        preflight = ('ExecStartPre='+systemd_quote(python)+' '+systemd_quote(ROOT/'backend'/'migrate_agent.py')+' --require-ready\n'
                     if name == BACKEND_UNIT else '')
        site_environment = ''.join('Environment='+systemd_environment(key+'='+os.environ[key])+'\n'
                                   for key in ('WATCH_NANDA_HTTPS_HOST', 'WATCH_NANDA_TLS_CA', 'WATCH_NANDA_TLS_CERT',
                                               'WATCH_NANDA_TLS_KEY') if key in os.environ)
        content = ('[Unit]\nDescription=Watch Nanda '+name+'\nAfter=network-online.target docker.service\nWants=network-online.target\n'
                   '\n[Service]\nType=simple\nUser='+user+'\nWorkingDirectory='+systemd_directory(directory)+'\n'
                   'Environment=PYTHONUNBUFFERED=1\n'+site_environment+preflight+'ExecStart='+command+'\nRestart=on-failure\nRestartSec=10\nTimeoutStopSec=60\nUMask=0077\n'
                   '\n[Install]\nWantedBy=multi-user.target\n')
        path = Path('/etc/systemd/system')/name
        if path.exists():
            backup = ROOT/'.runtime'/(name+'.previous')
            ensure_runtime()
            shutil.copyfile(path, backup)
            backup.chmod(0o600)
        path.write_text(content)
        path.chmod(0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', BACKEND_UNIT, frontend_service])
    print('systemd 配置已更新。运行 start.sh --restart 应用配置；不会自动重启运行中的服务。')


def prepare_setup():
    """Do not replace virtualenv/node_modules under a running application."""
    units = [BACKEND_UNIT, FRONTEND_UNIT]
    # Nginx/WAF can stay up while dependencies change; assets publish index last.
    for unit in units:
        directory = unit_directory(unit)
        if unit != 'nginx.service' and directory is not None:
            expected = ROOT/('backend' if unit == BACKEND_UNIT else 'frontend')
            if Path(directory).resolve() != expected.resolve():
                raise RuntimeError('同名服务属于其他项目，不能安装此目录')
        if directory is not None:
            active = subprocess.run(['systemctl', 'is-active', '--quiet', unit])
            if active.returncode == 0:
                raise RuntimeError('请先运行 bash stop.sh 停止本项目服务，再运行 setup.sh 安装/升级')
    if any(owned(r) for r in read_state()):
        raise RuntimeError('请先运行 bash stop.sh，再更新运行中的项目依赖')


def check_setup_lock():
    """Prevent start/stop racing an installer that is replacing dependencies."""
    import fcntl
    path = RUNTIME/'setup.lock'
    if not path.exists():
        return
    with path.open() as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            parent = os.getppid()
            if os.environ.get('WATCH_NANDA_SETUP_PID') == str(parent):
                try:
                    args = (Path('/proc')/str(parent)/'cmdline').read_bytes().split(b'\0')
                    if os.fsencode(ROOT/'scripts/setup_project.py') in args:
                        return
                except OSError:
                    pass
            raise RuntimeError('初始化/升级正在替换项目环境，请完成后再启动/停止服务') from None


def main():
    apply_site()
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start','stop','restart','install','mode','prepare'])
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise RuntimeError('启动/停止脚本仅支持 Linux')
    if args.action == 'mode':
        print('systemd' if managed_services() else 'standalone')
        return
    check_setup_lock()
    with locked():
        if args.action == 'prepare':
            prepare_setup()
            return
        if args.action == 'install':
            install_services()
            return
        services = managed_services()
        if services:
            systemctl('stop' if args.action == 'stop' else args.action, services)
            if args.action != 'stop':
                wait_http(7980, '/api/servers')
                wait_https(443 if 'nginx.service' in services else FRONTEND_PORT)
            print('已操作本项目服务：' + ', '.join(services) + '；MySQL 不受影响。')
        elif args.action == 'stop':
            standalone_stop(read_state())
        else:
            if args.action == 'restart':
                standalone_stop(read_state())
            standalone_start()


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('服务操作失败：' + (str(error) if isinstance(error, RuntimeError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
