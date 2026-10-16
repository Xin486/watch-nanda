"""Staged root installer, invoked over password-authenticated SSH by install-agent.sh.

Only the installer executes OS commands. monitor_agent.py remains metrics-only.
"""
import argparse
from contextlib import contextmanager
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urlparse
from uuid import uuid4

BASE = Path('/opt/node-monitor-agent')
ETC = Path('/etc/node-monitor-agent')
UNIT = Path('/etc/systemd/system/node-monitor-agent.service')
PENDING = BASE / '.install-pending.json'
NAME = 'node-monitor-agent.service'


def command(args, *, check=True, timeout=900):
    env = dict(os.environ)
    if getattr(sys,'frozen',False):
        if env.get('LD_LIBRARY_PATH_ORIG'):
            env['LD_LIBRARY_PATH']=env['LD_LIBRARY_PATH_ORIG']
        else:
            env.pop('LD_LIBRARY_PATH',None)
        env['PYINSTALLER_RESET_ENVIRONMENT']='1'
    if args[0]=='apt-get': env.update(DEBIAN_FRONTEND='noninteractive',NEEDRESTART_MODE='l')
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout,env=env)
    if check and result.returncode:
        # Command output can include pip configuration. Keep errors bounded and generic.
        raise RuntimeError('命令执行失败：'+Path(args[0]).name+'；检查软件源、权限及节点日志')
    return result


def active():
    return command(['systemctl','is-active','--quiet',NAME],check=False).returncode == 0


def regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError('拒绝覆盖符号链接或非普通文件：'+str(path))


def check_owned_service():
    result = command(['systemctl','show',NAME,'-p','LoadState','-p','FragmentPath'],check=False).stdout
    values = dict(line.split('=',1) for line in result.splitlines() if '=' in line)
    if values.get('LoadState') == 'loaded':
        if values.get('FragmentPath') != str(UNIT):
            raise RuntimeError('同名服务位于其他位置，不会覆盖')
        text = UNIT.read_text()
        expected = ('Description=Node Monitor metrics reporting agent', 'User=node-monitor', 'Group=node-monitor')
        starts = [line for line in text.splitlines() if line.startswith('ExecStart=')]
        valid = [f'ExecStart={BASE}/{prefix}venv/bin/python {BASE}/{prefix}monitor_agent.py --config {ETC}/config.json' for prefix in ('','current/')]
        valid.append(f'ExecStart={BASE}/current/node-monitor-agent --config {ETC}/config.json')
        if not all(item in text.splitlines() for item in expected) or starts not in [[line] for line in valid]:
            raise RuntimeError('同名服务不是本项目标准 Agent，不会覆盖；请先人工核对')
        dropins=command(['systemctl','show',NAME,'-p','DropInPaths']).stdout.strip()
        if dropins.partition('=')[2].strip():
            raise RuntimeError('Agent 服务有自定义 drop-in，请先人工核对，避免覆盖定制配置')
    elif UNIT.exists():
        raise RuntimeError('已有未加载的同名服务文件，请先人工核对')
    for path in (UNIT, ETC/'config.json', ETC/'ca.crt'):
        regular(path)
    for path in (BASE, ETC, BASE/'releases'):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError('安装目录不是普通目录：'+str(path))
        if path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
            raise RuntimeError('安装目录需归 root 所有且不可被其他账户写入：'+str(path))
    current = BASE/'current'
    if current.exists() or current.is_symlink():
        if not current.is_symlink() or current.resolve().parent != (BASE/'releases').resolve():
            raise RuntimeError('current 不是本项目版本链接，不会覆盖')
    return values.get('LoadState') == 'loaded'


def check(stage, url):
    check_owned_service()
    if sys.version_info < (3,10):
        raise RuntimeError('节点需要 Python 3.10+；请先升级系统 Python')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError('需要有效 HTTPS 上报地址')
    context = ssl.create_default_context(cafile=str(stage/'ca.crt') if (stage/'ca.crt').exists() else None)
    with socket.create_connection((parsed.hostname, parsed.port or 443),timeout=10) as sock:
        with context.wrap_socket(sock,server_hostname=parsed.hostname):
            pass
    result = {'hostname':socket.gethostname(), 'python_version':'.'.join(map(str,sys.version_info[:3])),
              'active':active(), 'clock':time.time(), 'venv_available':False, 'account_safe':False, 'config_safe':False}
    try:
        user, group = pwd.getpwnam('node-monitor'), grp.getgrnam('node-monitor')
        groups = {grp.getgrgid(gid).gr_name for gid in os.getgrouplist(user.pw_name,user.pw_gid)}
        result['account_safe'] = bool(user.pw_uid != 0 and user.pw_gid == group.gr_gid
                                      and Path(user.pw_shell).name in ('nologin','false')
                                      and not groups & {'sudo','wheel','docker'} and not set(group.gr_mem)-{'node-monitor'})
    except KeyError:
        pass
    try:
        import ensurepip
        result['venv_available'] = True
    except ImportError:
        pass
    config = ETC/'config.json'
    if config.exists():
        mode = config.stat()
        result['config_safe'] = bool(result['account_safe'] and mode.st_uid == 0 and mode.st_gid == group.gr_gid
                                     and not mode.st_mode & 0o027)
        data = json.loads(config.read_text())
        result.update(server_id=data.get('server_id'),url=data.get('url'),interval_seconds=data.get('interval_seconds',300),
                      token_hash=hashlib.sha256(str(data.get('token','')).encode()).hexdigest())
        ca = Path(data['ca_file']) if data.get('ca_file') else None
        result['ca_hash'] = hashlib.sha256(ca.read_bytes()).hexdigest() if ca else None
    program = BASE/'current'/'monitor_agent.py' if (BASE/'current').is_symlink() else BASE/'monitor_agent.py'
    if program.exists():
        result['program_hash'] = hashlib.sha256(program.read_bytes()).hexdigest()
    manifest=BASE/'current'/'manifest.json'
    if manifest.is_file():
        details=json.loads(manifest.read_text())
        for name,digest in details['files'].items():
            path=manifest.parent/name
            if not path.is_file() or path.is_symlink() or path.stat().st_uid!=0 or path.stat().st_mode & 0o022 or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise RuntimeError('已安装独立包文件校验失败：'+name)
        result['program_hash']=details['source_sha256']
        result['runtime_kind']='standalone'
        result['bundle_sha256']=(BASE/'current'/'bundle.sha256').read_text().strip()
    return result


def account():
    try:
        group = grp.getgrnam('node-monitor')
    except KeyError:
        command(['groupadd','--system','node-monitor'])
        group = grp.getgrnam('node-monitor')
    if set(group.gr_mem) - {'node-monitor'}:
        raise RuntimeError('node-monitor 组包含其他用户，请人工核对凭据访问权限')
    try:
        user = pwd.getpwnam('node-monitor')
    except KeyError:
        command(['useradd','--system','--no-create-home','--gid','node-monitor','--shell','/usr/sbin/nologin','node-monitor'])
        user = pwd.getpwnam('node-monitor')
    groups = {grp.getgrgid(gid).gr_name for gid in os.getgrouplist(user.pw_name,user.pw_gid)}
    if user.pw_uid == 0 or user.pw_gid != group.gr_gid or Path(user.pw_shell).name not in ('nologin','false') or groups & {'sudo','wheel','docker'}:
        raise RuntimeError('已有 node-monitor 账户不符合独立低权限账户要求，请人工核对')
    # Add device access only when NVIDIA devices actually use these groups.
    for device in Path('/dev').glob('nvidia*'):
        try:
            name = grp.getgrgid(device.stat().st_gid).gr_name
        except (OSError,KeyError):
            continue
        if name in ('video','render') and name not in groups:
            command(['usermod','-a','-G',name,'node-monitor'])
            groups.add(name)
    return group.gr_gid


def write_file(path, data, mode=0o600, gid=0):
    regular(path)
    temporary = path.with_name('.'+path.name+'.'+uuid4().hex)
    fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as stream:
        stream.write(data)
    os.chown(temporary,0,gid)
    temporary.chmod(mode)
    temporary.replace(path)


def release_path(value):
    path = Path(value)
    if path.parent != BASE/'releases' or len(path.name) != 32 or any(c not in '0123456789abcdef' for c in path.name) or path.is_symlink():
        raise RuntimeError('无效的安装版本目录')
    return path


def pending(release):
    if not PENDING.exists() or json.loads(PENDING.read_text())['release'] != str(release):
        raise RuntimeError('安装锁已改变，不会覆盖其他安装操作')


def prepare(stage):
    check_owned_service()
    BASE.mkdir(mode=0o755,exist_ok=True)
    BASE.chmod(0o755)
    regular(PENDING)
    if PENDING.exists():
        raise RuntimeError('已有 Agent 安装未结束；请先恢复该次安装，避免并发改动凭据')
    release = BASE/'releases'/uuid4().hex
    release.parent.mkdir(mode=0o755,exist_ok=True)
    release.parent.chmod(0o755)
    release.mkdir(mode=0o755)
    write_file(PENDING,json.dumps({'release':str(release)}).encode())
    try:
        bundle=stage/'node-monitor-agent'
        if (bundle/'manifest.json').is_file():
            manifest=json.loads((bundle/'manifest.json').read_text())
            import platform
            if manifest.get('format')!=1 or manifest.get('architecture')!=platform.machine():
                raise RuntimeError('独立包的格式或 CPU 架构不匹配')
            for name,digest in manifest['files'].items():
                file=bundle/name
                if file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
                    raise RuntimeError('独立包文件校验失败：'+name)
            account()
            shutil.copytree(bundle,release,dirs_exist_ok=True,symlinks=True)
            for path in release.rglob('*'):
                if not path.is_symlink():
                    path.chmod(0o755 if path.is_dir() or path.name=='node-monitor-agent' else 0o644)
            write_file(release/'bundle.sha256',(stage/'bundle.sha256').read_bytes(),0o644)
            command([str(release/'node-monitor-agent'),'--self-test'])
            return {'release':str(release),'runtime':'standalone'}
        try:
            import ensurepip
        except ImportError:
            if shutil.which('apt-get'):
                command(['apt-get','update'])
                command(['apt-get','install','-y','python3-venv','ca-certificates'])
            elif shutil.which('dnf'):
                command(['dnf','install','-y','python3-pip','ca-certificates'])
            else:
                raise RuntimeError('节点缺少 venv/ensurepip，请先安装对应版本的 python3-venv')
        account()
        command([sys.executable,'-m','venv',str(release/'venv')])
        command([str(release/'venv/bin/python'),'-m','pip','install','--disable-pip-version-check','-r',str(stage/'requirements.txt')])
        write_file(release/'monitor_agent.py',(stage/'monitor_agent.py').read_bytes(),0o644)
        command([str(release/'venv/bin/python'),'-c','import pynvml;compile(open(__import__("sys").argv[1]).read(),"agent","exec")',str(release/'monitor_agent.py')])
        return {'release':str(release)}
    except BaseException:
        PENDING.unlink(missing_ok=True)
        raise


def activate(stage, release):
    pending(release)
    exists = check_owned_service()
    group_id = account()
    backup = release/'.rollback'
    backup.mkdir(mode=0o700)
    files = [(ETC/'config.json','config.json'),(ETC/'ca.crt','ca.crt'),(UNIT,'service')]
    metadata = {'active':active(), 'enabled':command(['systemctl','is-enabled','--quiet',NAME],check=False).returncode==0,
                'files':[], 'current':os.readlink(BASE/'current') if (BASE/'current').is_symlink() else None}
    for source, name in files:
        if source.exists():
            write_file(backup/name,source.read_bytes())
            metadata['files'].append(name)
    write_file(backup/'state.json',json.dumps(metadata).encode())
    if exists:
        command(['systemctl','stop',NAME])
    ETC.mkdir(mode=0o750,exist_ok=True)
    os.chown(ETC,0,group_id)
    ETC.chmod(0o750)
    config = json.loads((stage/'config.json').read_text())
    if config.get('ca_file'):
        if config['ca_file'] != str(ETC/'ca.crt'):
            raise RuntimeError('CA 安装路径不正确')
        write_file(ETC/'ca.crt',(stage/'ca.crt').read_bytes(),0o644)
    write_file(ETC/'config.json',json.dumps(config).encode(),0o640,group_id)
    link = BASE/('.current-'+release.name)
    link.symlink_to(release)
    link.replace(BASE/'current')
    template = (stage/'node-monitor-agent.service').read_text()
    standalone=(release/'node-monitor-agent').is_file()
    if standalone:
        lines=template.splitlines()
        template='\n'.join(f'ExecStart={BASE}/current/node-monitor-agent --config {ETC}/config.json' if line.startswith('ExecStart=') else line for line in lines)+'\n'
    else:
        template = template.replace(str(BASE)+'/venv',str(BASE)+'/current/venv').replace(str(BASE)+'/monitor_agent.py',str(BASE)+'/current/monitor_agent.py')
    version=command(['systemctl','--version']).stdout.split()
    if len(version)>1 and version[1].isdigit() and int(version[1])<232:
        template=template.replace('ProtectSystem=strict','ProtectSystem=full').replace('MemoryMax=','MemoryLimit=')
    write_file(UNIT,template.encode(),0o644)
    command(['systemctl','daemon-reload'])
    executable=[str(release/'node-monitor-agent')] if standalone else [str(release/'venv/bin/python'),str(release/'monitor_agent.py')]
    once = ['runuser','-u','node-monitor','--',*executable,'--config',str(ETC/'config.json'),'--once']
    result = command(once,check=False,timeout=150)
    if result.returncode and 'HTTP 429' in result.stdout:
        time.sleep(31)
        result = command(once,check=False,timeout=150)
    if result.returncode:
        raise RuntimeError('首次 HTTPS 上报失败（证书/网络/时钟/凭据）；将恢复原服务。请检查节点与中心日志')
    command(['systemctl','enable','--now',NAME])
    time.sleep(2)
    if not active():
        raise RuntimeError('Agent 服务未持续运行，将恢复原服务')
    return {'active':True}


def rollback(release):
    pending(release)
    backup = release/'.rollback'
    if not (backup/'state.json').exists():
        # No remote activation was attempted; the old service was untouched.
        PENDING.unlink()
        return {}
    metadata = json.loads((backup/'state.json').read_text())
    command(['systemctl','stop',NAME],check=active())
    gid = grp.getgrnam('node-monitor').gr_gid
    for target, name, mode in [(ETC/'config.json','config.json',0o640),(ETC/'ca.crt','ca.crt',0o644),(UNIT,'service',0o644)]:
        if name in metadata['files']:
            write_file(target,(backup/name).read_bytes(),mode,gid if name=='config.json' else 0)
        else:
            regular(target)
            target.unlink(missing_ok=True)
    current = BASE/'current'
    if metadata['current']:
        temporary = BASE/('.restore-'+release.name)
        temporary.symlink_to(metadata['current'])
        temporary.replace(current)
    elif current.is_symlink():
        current.unlink()
    command(['systemctl','daemon-reload'])
    if 'service' in metadata['files']:
        command(['systemctl','enable' if metadata['enabled'] else 'disable',NAME])
        if metadata['active']:
            command(['systemctl','start',NAME])
    else:
        # Remove enablement links even when no previous unit existed.
        link = Path('/etc/systemd/system/multi-user.target.wants')/NAME
        if link.is_symlink() and link.resolve() == UNIT:
            link.unlink()
        command(['systemctl','daemon-reload'])
    PENDING.unlink()
    return {}


@contextmanager
def operation_lock():
    # /run is root-controlled and shared by all temporary installer uploads.
    path = Path('/run/watch-nanda-agent-install.lock')
    regular(path)
    fd = os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action',choices=['check','prepare','activate','rollback','finish'])
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--url')
    parser.add_argument('--release')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise RuntimeError('需要 root 或 sudo 权限')
    os.umask(0o022)  # venv files must remain readable by the service account.
    stage = args.stage
    if stage.parent != Path('/tmp') or not stage.name.startswith('watch-nanda-agent.') or stage.is_symlink() or stage.stat().st_mode & 0o077:
        raise RuntimeError('临时目录权限或路径不正确')
    with operation_lock():
        if args.action == 'check':
            return check(stage,args.url)
        if args.action == 'prepare':
            return prepare(stage)
        release = release_path(args.release)
        if args.action == 'activate':
            return activate(stage,release)
        if args.action == 'rollback':
            return rollback(release)
        pending(release)
        PENDING.unlink()
        return {}


def entrypoint(argv=None):
    try:
        print(json.dumps({'ok':True,**main(argv)}))
    except Exception as error:
        message = str(error) if isinstance(error,RuntimeError) else type(error).__name__
        print(json.dumps({'ok':False,'error':message}))


if __name__ == '__main__':
    entrypoint()
