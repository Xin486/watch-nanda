"""Staged root installer, invoked over password-authenticated SSH by install-agent.sh.

Only the installer executes OS commands. monitor_agent.py remains metrics-only.
"""
import argparse
from contextlib import contextmanager
import fcntl
import grp
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import shutil
import shlex
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
    env['LC_ALL'] = 'C'
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


def firewall_state():
    managers = []
    if shutil.which('ufw'):
        result = command(['ufw', 'status'], check=False, timeout=15)
        if result.returncode:
            raise RuntimeError('无法读取 UFW 状态，请先核对目标机防火墙')
        if 'Status: active' in result.stdout:
            managers.append('ufw')
    if shutil.which('firewall-cmd'):
        result = command(['firewall-cmd', '--state'], check=False, timeout=15)
        if result.returncode == 0:
            managers.append('firewalld')
        elif result.returncode not in (252, 1):
            raise RuntimeError('无法读取 firewalld 状态，请先核对目标机防火墙')
    return managers


def rollback_network(stage):
    journal = stage/'network-changes.json'
    regular(journal)
    if not journal.exists():
        return {}
    remaining = json.loads(journal.read_text())
    while remaining:
        remove = remaining[-1]
        if not remove or remove[0] not in ('ufw', 'firewall-cmd'):
            raise RuntimeError('防火墙恢复记录不正确，请人工核对')
        result = command(remove, check=False, timeout=30)
        if result.returncode:
            raise RuntimeError('新增出站规则未能恢复，请核对节点安装目录中的 network-changes.json')
        remaining.pop()
        write_file(journal, json.dumps(remaining).encode())
    journal.unlink()
    return {}


def allow_report_egress(stage, managers, addresses, port):
    """Only add endpoint-specific OUTPUT rules, preserving unrelated firewall policy."""
    if len(managers) > 1:
        raise RuntimeError('同时启用多个防火墙管理器，请先核对；不会自动覆盖现有策略')
    added = []
    journal = stage/'network-changes.json'
    regular(journal)
    if journal.exists():
        raise RuntimeError('已有未完成的防火墙变更，请先恢复')

    def add(create, remove):
        command(create, timeout=30)
        added.append(remove)
        write_file(journal, json.dumps(added).encode())

    try:
        for address in addresses:
            tag = 'node-monitor-agent-' + hashlib.sha256(f'{address}:{port}'.encode()).hexdigest()[:12]
            if managers == ['ufw']:
                # Do not update comments or ownership of a matching pre-existing rule.
                rules = command(['ufw', 'show', 'added'], timeout=15).stdout.splitlines()
                matching = False
                for line in rules:
                    tokens = shlex.split(line)
                    if tokens[:3] != ['ufw', 'allow', 'out'] or 'to' not in tokens:
                        continue
                    destination = tokens.index('to')
                    target = tokens[destination + 1:]
                    try:
                        network = ipaddress.ip_network(target[0], strict=False)
                        same_host = network.prefixlen == network.max_prefixlen and str(network.network_address) == address
                        source = tokens[tokens.index('from') + 1] if 'from' in tokens else 'any'
                        any_source = source == 'any' or ipaddress.ip_network(source, strict=False).prefixlen == 0
                        same_port = len(target) >= 3 and target[1:3] == ['port', str(port)]
                    except (ValueError, IndexError):
                        continue
                    if (same_host and same_port and any_source and 'proto' in tokens
                            and tokens[tokens.index('proto') + 1] == 'tcp'
                            and 'on' not in tokens):
                        matching = True
                if not matching:
                    rule = ['allow', 'out', 'proto', 'tcp', 'to', address, 'port', str(port), 'comment', tag]
                    add(['ufw', 'insert', '1', *rule], ['ufw', '--force', 'delete', *rule])
            elif managers == ['firewalld']:
                family = 'ipv4' if ipaddress.ip_address(address).version == 4 else 'ipv6'
                rule = [family, 'filter', 'OUTPUT', '0', '-d', address, '-p', 'tcp', '--dport', str(port),
                        '-m', 'comment', '--comment', tag, '-j', 'ACCEPT']
                # Configure runtime and persistence separately; never reload the host firewall.
                for scope in ([], ['--permanent']):
                    prefix = ['firewall-cmd', *scope, '--direct']
                    result = command([*prefix, '--query-rule', *rule], check=False, timeout=15)
                    if result.returncode == 1:
                        add([*prefix, '--add-rule', *rule], [*prefix, '--remove-rule', *rule])
                    elif result.returncode:
                        raise RuntimeError('无法核对 firewalld 出站规则，不会覆盖自定义策略')
        return len(added)
    except BaseException:
        if added:
            rollback_network(stage)
        raise


def check_network(stage, url, configure=False):
    parsed = urlparse(url)
    try:
        port = 443 if parsed.port is None else parsed.port
    except ValueError:
        raise RuntimeError('HTTPS 上报端口不合法') from None
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError('需要有效 HTTPS 上报地址')
    if not 1 <= port <= 65535:
        raise RuntimeError('HTTPS 上报端口不合法')
    managers = firewall_state()
    try:
        addresses = sorted({str(ipaddress.ip_address(row[4][0])) for row in
                            socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)})
    except OSError:
        raise RuntimeError('上报入口 DNS 解析失败；请先检查节点 DNS 和网络') from None
    if not addresses or len(addresses) > 8:
        raise RuntimeError('上报入口解析结果异常，请核对地址')
    added = allow_report_egress(stage, managers, addresses, port) if configure else 0
    try:
        context = ssl.create_default_context(cafile=str(stage/'ca.crt') if (stage/'ca.crt').exists() else None)
        with socket.create_connection((parsed.hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=parsed.hostname):
                pass
    except (ssl.SSLError, OSError, ValueError) as error:
        if configure:
            rollback_network(stage)
        if isinstance(error, (ssl.SSLError, ValueError)):
            raise RuntimeError('HTTPS 证书校验失败，请检查 CA、证书地址和节点时钟；未关闭证书校验') from None
        raise RuntimeError(f'节点无法出站连接 {parsed.hostname}:{port}/TCP；请检查本机防火墙、回包、路由与上游 ACL，未开放入站端口') from None
    return {'direction': 'outbound', 'host': parsed.hostname, 'port': port, 'tls_verified': True,
            'firewalls': managers, 'rules_added': added, 'service_account_verified': False}


def check(stage, url, configure_firewall=False):
    check_owned_service()
    if sys.version_info < (3,10):
        raise RuntimeError('节点需要 Python 3.10+；请先升级系统 Python')
    network = check_network(stage, url, configure_firewall)
    result = {'hostname':socket.gethostname(), 'python_version':'.'.join(map(str,sys.version_info[:3])),
              'active':active(), 'clock':time.time(), 'venv_available':False, 'account_safe':False, 'config_safe':False,
              'network':network}
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
    if (stage/'network-changes.json').exists():
        write_file(backup/'network-changes.json',(stage/'network-changes.json').read_bytes())
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
        reason = ('上报凭据被拒绝，请重新部署以同步 Token' if 'HTTP 401' in result.stdout else
                  '上报入口拒绝访问，请核对中心 WAF/访问策略' if 'HTTP 403' in result.stdout else
                  '采样或时钟校验失败，请核对节点时间' if 'HTTP 422' in result.stdout else
                  '服务账户无法完成 HTTPS 上报，请检查出站防火墙、回包、证书和服务权限')
        raise RuntimeError(reason + '；部署未完成，将恢复原服务')
    command(['systemctl','enable','--now',NAME])
    time.sleep(2)
    if not active():
        raise RuntimeError('Agent 服务未持续运行，将恢复原服务')
    return {'active':True,'network':{'service_account_verified':True,'report_accepted':True}}


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
    parser.add_argument('action',choices=['check','prepare','activate','rollback','finish','network-rollback'])
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--url')
    parser.add_argument('--release')
    parser.add_argument('--firewall-mode',choices=['inspect','ensure'],default='inspect')
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise RuntimeError('需要 root 或 sudo 权限')
    os.umask(0o022)  # venv files must remain readable by the service account.
    stage = args.stage
    if stage.parent != Path('/tmp') or not stage.name.startswith('watch-nanda-agent.') or stage.is_symlink() or stage.stat().st_mode & 0o077:
        raise RuntimeError('临时目录权限或路径不正确')
    with operation_lock():
        if args.action == 'check':
            return check(stage,args.url,args.firewall_mode == 'ensure')
        if args.action == 'network-rollback':
            return rollback_network(stage)
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
