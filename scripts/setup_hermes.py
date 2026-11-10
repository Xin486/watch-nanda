"""Install the optional, unprivileged Hermes service for this checkout.

No provider key is required at installation time. Configure it in Settings.
Existing Hermes homes and credentials are never removed or overwritten.
"""
import argparse
from contextlib import contextmanager
import time
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from core.site_config import apply_site, load_site
from core.platform_config import write_json, hermes_path, read_json

PIN = 'bf0203a794a07aac3ff764972ab9087baa4e4f4e'


@contextmanager
def activation(paths, unit, existing):
    """Restore the matching private settings, bridge and unit on activation failure."""
    saved = {}
    for path in paths:
        if path.is_symlink():
            raise RuntimeError('助手接入文件不能是符号链接')
        saved[path] = (path.read_bytes(), path.stat()) if path.exists() else None
    active = subprocess.run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0
    if existing:
        subprocess.run(['systemctl', 'stop', unit], check=True)
    try:
        yield
        time.sleep(1)
        subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=True)
    except BaseException:
        subprocess.run(['systemctl', 'stop', unit], check=False)
        for path, before in saved.items():
            if before is None:
                if path.exists(): path.unlink()
            else:
                content, info = before
                path.write_bytes(content)
                path.chmod(info.st_mode & 0o777)
                os.chown(path, info.st_uid, info.st_gid)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        if active:
            subprocess.run(['systemctl', 'start', unit], check=False)
        elif not existing:
            subprocess.run(['systemctl', 'disable', unit], check=False, capture_output=True)
        raise


def install(source=None):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('可选 Hermes 服务安装需要 Linux、systemd 和管理员权限')
    import pwd, grp
    from lifecycle import systemd_quote, systemd_directory, systemd_environment
    apply_site(ROOT)
    unit = 'watch-nanda-hermes.service'
    existing = subprocess.run(['systemctl', 'show', unit, '-p', 'LoadState', '-p', 'User',
                               '-p', 'WorkingDirectory', '-p', 'Environment'], capture_output=True, text=True, check=True)
    props = dict(line.split('=', 1) for line in existing.stdout.splitlines() if '=' in line)
    if props.get('LoadState') == 'loaded':
        account = pwd.getpwnam(props['User'])
        base = Path(props['WorkingDirectory']).parent
        if account.pw_uid == 0 or Path(props['WorkingDirectory']).name != 'web-bridge':
            raise RuntimeError('已有助手服务归属不明确，不会覆盖；请先核对其用户和工作目录')
        previous_env = dict(item.split('=', 1) for item in shlex.split(props.get('Environment', '')) if '=' in item)
    else:
        ident = hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:8]
        username = 'watch-hermes-' + ident
        base = Path('/var/lib/watch-nanda-hermes') / ident
        base.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        try:
            account = pwd.getpwnam(username)
            if Path(account.pw_dir) != base or account.pw_uid == 0:
                raise RuntimeError('助手服务账户与安装目录冲突')
        except KeyError:
            subprocess.run(['useradd', '--system', '--user-group', '--create-home', '--home-dir', str(base),
                            '--shell', '/usr/sbin/nologin', username], check=True)
            account = pwd.getpwnam(username)
        previous_env = {}
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(base, account.pw_uid, account.pw_gid)
    cli = Path(os.getenv('HERMES_CLI') or previous_env.get('HERMES_CLI') or base / '.local/bin/hermes')
    home = Path(os.getenv('HERMES_HOME') or previous_env.get('HERMES_HOME') or base / 'home')
    config = Path(os.getenv('HERMES_CONFIG_FILE') or previous_env.get('HERMES_CONFIG_FILE') or base / 'web-config.json')
    workspace = Path(os.getenv('HERMES_CHAT_WORKSPACE') or previous_env.get('HERMES_CHAT_WORKSPACE') or base / 'workspace')
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if (proc / 'cwd').resolve() == workspace.resolve() and b'hermes_worker.py' in (proc / 'cmdline').read_bytes():
                raise RuntimeError('仍有助手任务或清理进程运行，请等待结束后重试安装')
        except (OSError, ProcessLookupError):
            pass
    for path in (home, workspace):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chown(path, account.pw_uid, account.pw_gid)
    # Do not inherit database credentials or root's environment into the tool user.
    env = {k: v for k, v in os.environ.items() if k in {
        'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'https_proxy', 'http_proxy', 'no_proxy',
        'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'PIP_INDEX_URL', 'UV_INDEX_URL'}}
    env.update(HOME=str(base), HERMES_HOME=str(home), SHELL='/bin/bash', LANG='C.UTF-8',
               PATH=str(base / '.local/bin') + ':/usr/local/bin:/usr/bin:/bin')
    def user_run(args, capture=False):
        execution_env = dict(env)
        if os.getenv('HERMES_INSTALL_HTTPS_PROXY'):
            execution_env['HTTPS_PROXY'] = os.environ['HERMES_INSTALL_HTTPS_PROXY']
        return subprocess.run(['runuser', '-u', account.pw_name, '--', 'env', '-i',
                               *[k + '=' + v for k, v in execution_env.items()], *map(str, args)],
                              check=True, capture_output=capture, text=True, cwd=base)
    if not cli.is_file():
        if not shutil.which('git'):
            raise RuntimeError('安装 Hermes 需要预先安装 git')
        checkout = base / 'hermes-agent'
        repo = source or 'https://github.com/NousResearch/hermes-agent.git'
        if source and Path(source).is_file():
            heads = subprocess.check_output(['git', 'bundle', 'list-heads', source], text=True)
            if PIN not in heads:
                raise RuntimeError('离线 Git 包不含固定版本的 Hermes')
            local_source = base / 'installer-source.bundle'
            shutil.copyfile(source, local_source)
            local_source.chmod(0o600)
            os.chown(local_source, account.pw_uid, account.pw_gid)
            repo = str(local_source)
        # A release bundle may contain only a detached tag. The official
        # installer fetches a branch even for an exact commit pin.
        mirror = base / 'installer-pinned.git'
        if not mirror.exists():
            if source and Path(source).is_file():
                user_run(['git', 'init', '--bare', mirror])
                # Git versions predating shallow bundle support lose the
                # boundary during clone. Restore it before importing the
                # original commit and verify the complete pinned tree.
                shallow = mirror / 'shallow'
                shallow.write_text(PIN + '\n')
                os.chown(shallow, account.pw_uid, account.pw_gid)
                user_run(['git', '-C', mirror, 'bundle', 'unbundle', repo])
            else:
                user_run(['git', 'clone', '--bare', *(['--depth', '1', '--branch', 'v0.22.5'] if not source else []), repo, mirror])
        user_run(['git', '-C', mirror, 'cat-file', '-e', PIN + '^{commit}'])
        user_run(['git', '-C', mirror, 'update-ref', 'refs/heads/main', PIN])
        user_run(['git', '-C', mirror, 'fsck', '--full', '--no-reflogs'])
        repo = mirror.as_uri()
        env['HERMES_REPO_URL'] = repo
        if not (checkout / '.git').exists():
            user_run(['git', 'clone', '--no-local', '--depth', '1', '--branch', 'main', '--no-checkout', repo, checkout])
        user_run(['git', '-C', checkout, 'checkout', '--detach', PIN])
        # Run only the pinned official installer, as the isolated account.
        user_run(['bash', checkout / 'scripts/install.sh', '--dir', checkout, '--commit', PIN,
                  '--hermes-home', home, '--non-interactive', '--skip-browser', '--skip-computer-use'])
        if not cli.is_file():
            raise RuntimeError('Hermes 安装未生成启动器，请检查安装日志')
    command = json.loads(user_run([cli, '--print-runtime-command', '--module', 'types'], capture=True).stdout)
    if not isinstance(command, list) or len(command) != 4:
        raise RuntimeError('Hermes 官方运行时接口不兼容')
    venv = base / 'web-venv'
    if not (venv / 'bin/python').exists():
        user_run([command[0], '-m', 'venv', venv])
    user_run([venv / 'bin/python', '-m', 'pip', 'install', 'fastapi>=0.115,<1', 'uvicorn>=0.30,<1'])
    paths = [base / 'web-bridge' / name for name in ('hermes_bridge.py', 'hermes_worker.py')]
    paths += [config, ROOT / '.runtime/site.json', home / 'SOUL.md', base / 'bridge.env',
              Path('/etc/systemd/system') / unit]
    with activation(paths, unit, props.get('LoadState') == 'loaded'):
        bridge = base / 'web-bridge'
        bridge.mkdir(mode=0o700, exist_ok=True)
        os.chown(bridge, account.pw_uid, account.pw_gid)
        for name in ('hermes_bridge.py', 'hermes_worker.py'):
            target = bridge / name
            shutil.copy2(ROOT / 'backend' / name, target)
            os.chown(target, account.pw_uid, account.pw_gid)
        if not config.exists():
            saved = read_json(hermes_path())
            write_json(config, {'enabled': False, 'api_base': '', 'model': '', 'api_key': '', 'revision': 0} | saved)
        os.chown(config, account.pw_uid, account.pw_gid)
        config.chmod(0o600)
        values = load_site(ROOT) | {'HERMES_CLI': str(cli), 'HERMES_HOME': str(home),
            'HERMES_CONFIG_FILE': str(config), 'HERMES_CHAT_WORKSPACE': str(workspace)}
        write_json(ROOT / '.runtime/site.json', values)
        service_env = env | {k: values[k] for k in ('HERMES_CLI', 'HERMES_HOME', 'HERMES_CONFIG_FILE', 'HERMES_CHAT_WORKSPACE')}
        service_env = {k: v for k, v in service_env.items() if k not in {'PIP_INDEX_URL', 'UV_INDEX_URL'}}
        service_env['WATCH_NANDA_BACKEND_PORT'] = os.getenv('WATCH_NANDA_BACKEND_PORT', '7980')
        service_env['WATCH_NANDA_MONITOR_URL'] = 'http://127.0.0.1:' + service_env['WATCH_NANDA_BACKEND_PORT'] + '/api/servers'
        soul = home / 'SOUL.md'
        original = soul.read_text() if soul.exists() else ''
        start, end = '<!-- watch-nanda-monitor-role:start -->', '<!-- watch-nanda-monitor-role:end -->'
        role = start + '\n' + (ROOT / 'deploy/hermes-monitor-role.md').read_text() + '\n' + end
        if start in original and end in original:
            begin, finish = original.index(start), original.index(end) + len(end)
            updated = original[:begin] + role + original[finish:]
        else:
            updated = original.rstrip() + '\n\n' + role + '\n'
        if soul.exists():
            previous_role = base / 'SOUL.before-web-setup.md'
            shutil.copy2(soul, previous_role); previous_role.chmod(0o600)
        soul.write_text(updated); soul.chmod(0o600); os.chown(soul, account.pw_uid, account.pw_gid)
        # Proxy credentials, if supplied by the administrator, must not enter a
        # world-readable systemd unit. EnvironmentFile is private too.
        environment = base / 'bridge.env'
        private_env = ''.join(k + '=' + systemd_environment(v).replace('%%', '%') + '\n' for k, v in service_env.items())
        environment.write_text(private_env)
        environment.chmod(0o600)
        os.chown(environment, account.pw_uid, account.pw_gid)
        lines = 'EnvironmentFile=' + systemd_quote(environment) + '\n'
        content = ('[Unit]\nDescription=Watch Nanda Hermes bridge\nAfter=network-online.target watch-nanda-backend.service\n'
            '[Service]\nType=simple\nUser=' + account.pw_name + '\nGroup=' + grp.getgrgid(account.pw_gid).gr_name + '\nWorkingDirectory=' + systemd_directory(bridge) + '\n'
            + lines + 'ExecStart=' + systemd_quote(venv / 'bin/python') + ' -m uvicorn hermes_bridge:app --host 127.0.0.1 --port '
            + os.getenv('HERMES_BRIDGE_PORT', '7981') + ' --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-access-log --no-server-header\n'
            'Restart=on-failure\nRestartSec=5\nTimeoutStopSec=45\nKillMode=control-group\nUMask=0077\n[Install]\nWantedBy=multi-user.target\n')
        unit_path = Path('/etc/systemd/system') / unit
        if unit_path.exists():
            shutil.copy2(unit_path, base / 'bridge-unit.previous')
        unit_path.write_text(content)
        unit_path.chmod(0o644)
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', unit], check=True)
        subprocess.run(['systemctl', 'restart', unit], check=True)
        print('Hermes 独立运行环境已准备；请在设置中配置 API 并开启助手。')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', help='可选：预先准备的官方 Git 仓库或 Git bundle（仍校验固定版本）')
    args = parser.parse_args()
    try:
        install(args.source)
    except Exception as error:
        print('Hermes 安装未完成：' + (str(error) if isinstance(error, RuntimeError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
