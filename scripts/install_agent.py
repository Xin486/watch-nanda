"""Interactive, one-time SSH deployment. Run locally on the monitoring server."""
import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import shlex
import sys
import tarfile
import time
from threading import Event, Thread
from pathlib import PurePosixPath
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
DEFAULT_URL = 'https://192.168.1.24/api/agent/report'
PUBLIC_URL = 'https://114.212.165.149:59443/api/agent/report'
CONFIG = '/etc/node-monitor-agent/config.json'


class InstallError(RuntimeError):
    pass


def default_report_url(ip):
    return DEFAULT_URL if ipaddress.ip_address(ip) in ipaddress.ip_network('192.168.1.0/24') else PUBLIC_URL


def bundle_details(path):
    """Validate a trusted local build before invoking tar on the remote host."""
    with tarfile.open(path,'r:gz') as archive:
        members=archive.getmembers()
        if len(members)>5000 or sum(member.size for member in members)>150*1024*1024:
            raise InstallError('Agent 安装包过大或文件数异常')
        for member in members:
            name=PurePosixPath(member.name)
            if not name.parts or name.is_absolute() or '..' in name.parts or name.parts[0]!='node-monitor-agent' or not (member.isfile() or member.isdir() or member.issym()):
                raise InstallError('Agent 安装包包含不安全路径或文件类型')
            if member.issym():
                target=name.parent/PurePosixPath(member.linkname)
                depth=0
                for part in target.parts:
                    depth += -1 if part=='..' else (0 if part=='.' else 1)
                    if depth<1 or PurePosixPath(member.linkname).is_absolute():
                        raise InstallError('Agent 安装包包含越界符号链接')
        stream=archive.extractfile('node-monitor-agent/manifest.json')
        if stream is None: raise InstallError('Agent 安装包缺少 manifest.json')
        manifest=json.load(stream)
    expected=hashlib.sha256((ROOT/'agent/monitor_agent.py').read_bytes()).hexdigest()
    helper=hashlib.sha256((ROOT/'agent/install_node.py').read_bytes()).hexdigest()
    if manifest.get('format')!=1 or manifest.get('source_sha256')!=expected or manifest.get('installer_sha256')!=helper:
        raise InstallError('Agent 安装包与当前源代码不一致，请重新构建')
    return manifest,hashlib.sha256(path.read_bytes()).hexdigest()


class VerifyHost:
    """Require explicit trust for a previously unknown host; reject changed keys."""
    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        print(f'首次连接 {hostname}，{key.get_name()} 指纹：SHA256:{fingerprint}')
        if input('请核对服务器指纹，信任并保存？输入 yes：').strip().lower() != 'yes':
            raise InstallError('未信任目标主机，已停止')
        client.get_host_keys().add(hostname, key.get_name(), key)
        path = Path.home() / '.ssh' / 'known_hosts'
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if not path.exists():
            path.touch(mode=0o600)
        client.save_host_keys(str(path))


class Remote:
    def __init__(self, client, password):
        self.client = client
        self.password = password
        self.root = False
        self.installer = None
        self.session = None
        self.session_stop = Event()
        self.session_lost = Event()

    def start_session(self, stage):
        prefix = shlex.quote(self.installer)+' --installer' if self.installer else 'python3 '+shlex.quote(stage+'/install_node.py')
        script = f'{prefix} session --stage {shlex.quote(stage)}'
        command = 'bash -c '+shlex.quote(script)
        if not self.root:
            command = "sudo -S -p '' -- "+command
        stdin, stdout, stderr = self.client.exec_command(command, timeout=15)
        self.session = (stdin, stdout, stderr)
        if not self.root:
            stdin.write(self.password+'\n'); stdin.flush()
        try:
            result = json.loads(stdout.readline(4096))
        except (OSError, ValueError):
            raise InstallError('无法建立目标机独占部署会话，请检查 sudo/SSH 后重试') from None
        if not result.get('ok') or not result.get('session'):
            raise InstallError(result.get('error', '无法建立目标机独占部署会话'))
        def heartbeat():
            while not self.session_stop.wait(10):
                try:
                    if stdout.channel.exit_status_ready():
                        raise OSError()
                    stdin.write('.\n'); stdin.flush()
                except (OSError, EOFError):
                    self.session_lost.set()
                    stdout.channel.close()
                    return
        self.heartbeat = Thread(target=heartbeat, daemon=True)
        self.heartbeat.start()

    def assert_session(self):
        if self.session is None or self.session_lost.is_set() or self.session[1].channel.exit_status_ready():
            raise InstallError('目标机独占部署会话已中断，请重新发起覆盖安装')

    def close_session(self):
        self.session_stop.set()
        if self.session:
            try:
                self.session[0].channel.shutdown_write()
            except (OSError, EOFError):
                pass
            finally:
                self.session[1].channel.close()
        if hasattr(self, 'heartbeat'):
            self.heartbeat.join(timeout=1)

    def run(self, script, *, root=False, timeout=900):
        command = 'bash -c ' + shlex.quote(script)
        sudo = root and not self.root
        if sudo:
            command = "sudo -S -p '' -- " + command
        stdin, stdout, stderr = self.client.exec_command(command, timeout=15)
        if sudo:
            stdin.write(self.password + '\n')
            stdin.flush()
        stdin.channel.shutdown_write()
        channel = stdout.channel
        output, errors = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        while True:
            while channel.recv_ready():
                output.extend(channel.recv(32768))
                if len(output) > 131072:
                    channel.close()
                    raise InstallError('远端输出过长，已停止')
            while channel.recv_stderr_ready():
                errors.extend(channel.recv_stderr(32768))
                # Keep a bounded tail; package managers can be quite verbose.
                if len(errors) > 65536:
                    del errors[:-65536]
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() > deadline:
                channel.close()
                raise InstallError('远端操作超时，请检查节点服务状态后重试')
            time.sleep(0.05)
        status = channel.recv_exit_status()
        if status:
            # Do not echo arbitrary remote output, which may contain credentials.
            raise InstallError(f'远端步骤失败（退出码 {status}）；检查目标环境、sudo 权限和软件源')
        return output.decode('utf-8').strip()

    def helper(self, stage, action, **kwargs):
        if self.session is not None:
            self.assert_session()
        arguments = ' '.join(shlex.quote(str(x)) for pair in kwargs.items() for x in ('--'+pair[0].replace('_','-'), pair[1]))
        prefix = shlex.quote(self.installer)+' --installer' if self.installer else 'python3 '+shlex.quote(stage+'/install_node.py')
        value = self.run(f'{prefix} {action} --stage {shlex.quote(stage)} {arguments}', root=True)
        result = json.loads(value)
        if not result.get('ok'):
            error = InstallError(result.get('error', '节点安装失败'))
            error.recovery_required = bool(result.get('recovery_required'))
            raise error
        return result

    def put(self, stage, name, content, mode=0o644):
        with self.client.open_sftp() as sftp:
            sftp.get_channel().settimeout(60)
            path = stage + '/' + name
            with sftp.file(path, 'wb') as stream:
                if len(content)>131072:
                    stream.set_pipelined(True)
                stream.write(content)
            sftp.chmod(path, mode)


STATE_FIELDS = ('collection_mode', 'status', 'latest_status_data', 'offline_since', 'last_alert_time', 'last_online')


def find_server(db, ip, port, server_id=None):
    from models.models import Server
    if server_id is not None:
        server = db.get(Server, server_id)
        if not server:
            raise InstallError('指定的节点 ID 不存在')
        if (server.ip_address or server.hostname) != ip or (server.ssh_port or 22) != port:
            raise InstallError('节点连接信息已变化，请刷新页面后重试')
    else:
        matches = db.query(Server).filter(Server.ip_address == ip).all()
        exact = [node for node in matches if node.ssh_port == port]
        if matches and len(exact) != 1:
            raise InstallError('IP 对应多个节点或 SSH 端口不匹配，请明确指定 --server-id，避免重复建节点')
        server = exact[0] if exact else None
    if server is not None and not server.is_active:
        raise InstallError('该节点已被管理员禁用，请先在页面确认并启用')
    return server


def provision(db, ip, port, user, hostname, server_id=None, *, require_agent=False):
    from models.models import AgentCredential, Server
    server = find_server(db, ip, port, server_id)
    created = server is None
    if created:
        server = Server(hostname=hostname[:100] or ip, ip_address=ip, ssh_port=port, ssh_user=user, is_active=True)
        db.add(server)
        db.flush()
    # Use the same lock order as token administration and report ingestion.
    server = db.query(Server).filter(Server.id == server.id).with_for_update().one()
    if require_agent and server.collection_mode != 'agent':
        db.rollback()
        raise InstallError('该节点已不再使用 Agent，未修改采集方式或凭据，请刷新后重试')
    old = db.get(AgentCredential, server.id)
    snapshot = {'id': server.id, 'created': created, 'state': {k:getattr(server,k) for k in STATE_FIELDS},
                'credential': None if old is None else {'token_hash':old.token_hash, 'last_history_report_id':old.last_history_report_id}}
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    if old is None:
        db.add(AgentCredential(server_id=server.id, token_hash=digest))
    else:
        old.token_hash = digest
        old.last_history_report_id = None
    server.collection_mode = 'agent'
    server.latest_status_data = None
    server.status = 'pending'
    server.offline_since = None
    server.last_alert_time = None
    db.commit()
    return snapshot, token, digest


def restore_credentials(db, snapshot, digest):
    from models.models import AgentCredential, Server
    db.rollback()
    server = db.query(Server).filter(Server.id == snapshot['id']).with_for_update().first()
    credential = db.get(AgentCredential, snapshot['id'])
    if server is None or credential is None or credential.token_hash != digest or server.collection_mode != 'agent':
        db.rollback()
        raise InstallError('节点凭据已被其他操作修改，未覆盖其更改，请人工核对')
    if snapshot['created']:
        # Keep the ID and any historical samples; retries reuse this pending node.
        db.delete(credential)
        server.status = 'pending'
        server.latest_status_data = None
    else:
        for name, value in snapshot['state'].items():
            setattr(server, name, value)
        if snapshot['credential'] is None:
            db.delete(credential)
        else:
            for name, value in snapshot['credential'].items():
                setattr(credential, name, value)
    db.commit()


def parse_args():
    parser = argparse.ArgumentParser(description='在监控中心输入节点 IP/密码，一次性 SSH 检查并安装 HTTPS Agent；不创建免密密钥')
    parser.add_argument('--ip', help='目标 SSH IP；省略时交互输入')
    parser.add_argument('--user', help='SSH 用户，默认 root；非 root 需 sudo（使用同一密码）')
    parser.add_argument('--port', type=int, help='SSH 端口，默认 22')
    parser.add_argument('--server-id', type=int, help='明确使用已有节点 ID，适用于 SD-WAN/NAT 或重复 IP')
    parser.add_argument('--url', help='HTTPS 上报地址；192.168.1.* 默认内网入口，其余默认 114.212.165.149:59443')
    parser.add_argument('--ca-file', type=Path, help='内部 CA 公钥证书，默认 /etc/node-monitor/tls/ca.crt；不要提供 CA 私钥')
    parser.add_argument('--bundle', type=Path, help='独立运行包；默认自动使用 agent/dist 中对应架构的安装包')
    parser.add_argument('--source', action='store_true', help='强制使用旧版 Python/venv 安装方式')
    parser.add_argument('--check-only', action='store_true', help='只检查环境、证书和服务，不安装依赖或签发凭据')
    parser.add_argument('--reinstall', action='store_true', help='重部署指定的已有 Agent 节点，保留 ID 和历史')
    args = parser.parse_args()
    args.ip = args.ip or input('目标机器 IP：').strip()
    try:
        args.ip = str(ipaddress.ip_address(args.ip))
    except ValueError:
        parser.error('请输入有效 IPv4/IPv6 地址')
    args.user = args.user or input('SSH 用户 [root]：').strip() or 'root'
    try:
        args.port = args.port if args.port is not None else int(input('SSH 端口 [22]：').strip() or '22')
    except ValueError:
        parser.error('SSH 端口必须为整数')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', args.user) or not 1 <= args.port <= 65535:
        parser.error('SSH 用户名或端口不合法')
    if args.server_id is not None and args.server_id < 1:
        parser.error('--server-id 必须为正整数')
    if args.reinstall and args.server_id is None:
        parser.error('--reinstall 必须指定已有 --server-id')
    if args.source and args.bundle:
        parser.error('--source 与 --bundle 不能同时使用')
    args.url=args.url or default_report_url(args.ip)
    url = urlparse(args.url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        parser.error('--url 需要不含凭据的 HTTPS 地址')
    if args.ca_file is None and args.url in (DEFAULT_URL,PUBLIC_URL):
        args.ca_file = Path('/etc/node-monitor/tls/ca.crt')
    if args.ca_file is not None:
        import ssl
        try:
            certificate = args.ca_file.read_bytes()
            if b'PRIVATE KEY' in certificate:
                parser.error('只允许 CA 公钥证书，禁止传输私钥')
            ssl.create_default_context(cafile=str(args.ca_file))
        except (OSError, ssl.SSLError):
            parser.error('无法读取 CA 证书；通过 --ca-file 指定监控中心的 CA 公钥证书')
    return args


def install(args, password, *, known_hosts_only=False):
    """Reusable batch entry point; passwords stay in memory and are never logged."""
    try:
        import paramiko
        from models.database import SessionLocal
        from models.models import AgentCredential
    except ImportError:
        raise InstallError('缺少后端依赖，请先在监控中心运行 bash setup.sh') from None
    if not password or '\n' in password or '\r' in password:
        raise InstallError('SSH 密码不能为空或包含换行')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known_hosts = Path.home()/'.ssh'/'known_hosts'
    if known_hosts.exists():
        # save_host_keys must retain all previously trusted hosts, not just this one.
        client.load_host_keys(str(known_hosts))
    client.set_missing_host_key_policy(paramiko.RejectPolicy() if known_hosts_only else VerifyHost())
    remote = Remote(client, password)
    stage = None
    bundle_hash = None
    snapshot = digest = release = None
    activated = completed = False
    session_started = False
    preserve_stage = False
    with SessionLocal() as db:
        try:
            server = find_server(db, args.ip, args.port, args.server_id)
            if getattr(args, 'reinstall', False) and (server is None or server.collection_mode != 'agent'):
                raise InstallError('重部署仅适用于已有 Agent 节点，未修改节点配置')
            if server:
                print(f'匹配已有节点 ID={server.id}：{server.hostname}（历史保留）')
            db.rollback()  # Never hold a database transaction during SSH/pip operations.
            print('连接目标并检查 sudo、Linux、systemd 和 Python…', flush=True)
            client.connect(args.ip, port=args.port, username=args.user, password=password,
                           look_for_keys=False, allow_agent=False, timeout=10, auth_timeout=15, banner_timeout=15)
            remote.root = remote.run('id -u') == '0'
            remote.run('test "$(uname -s)" = Linux && test -d /run/systemd/system && command -v systemctl >/dev/null', root=True)
            bundle=getattr(args,'bundle',None)
            if not getattr(args,'source',False):
                architecture=remote.run('uname -m')
                candidate=ROOT/'agent'/'dist'/f'node-monitor-agent-linux-{architecture}.tar.gz'
                if bundle is None and candidate.is_file(): bundle=candidate
            if getattr(args,'require_bundle',False) and bundle is None:
                raise InstallError('中心缺少目标架构的独立 Agent 包，请先构建；不会改用系统 Python 安装')
            if bundle:
                manifest,bundle_hash=bundle_details(bundle)
                if remote.run('uname -m')!=manifest.get('architecture'):
                    raise InstallError('Agent 安装包架构与目标机器不一致')
                try:
                    libc=remote.run('getconf GNU_LIBC_VERSION').split()[-1]
                    required=manifest.get('glibc_min','2.17')
                    if not re.fullmatch(r'\d+\.\d+',libc) or tuple(map(int,libc.split('.')))<tuple(map(int,required.split('.'))):
                        raise InstallError('目标 glibc 版本低于独立包要求；不会修改系统运行库')
                except (IndexError,ValueError):
                    raise InstallError('无法识别 glibc 版本，暂不支持 Alpine/musl 系统') from None
            elif not args.check_only:
                # Use distro repositories only; never fetch shell scripts from the network.
                remote.run("if ! command -v python3 >/dev/null; then if command -v apt-get >/dev/null; then apt-get update >&2 && DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y python3 python3-venv ca-certificates >&2; elif command -v dnf >/dev/null; then dnf install -y python3 ca-certificates >&2; else exit 10; fi; fi", root=True)
            if not bundle:
                remote.run("python3 -c 'import sys;sys.exit(0 if sys.version_info >= (3,10) else 10)'")
            stage = remote.run('umask 077; mktemp -d /tmp/watch-nanda-agent.XXXXXXXX')
            if not re.fullmatch(r'/tmp/watch-nanda-agent\.[A-Za-z0-9]+', stage):
                raise InstallError('无法创建安全的远端临时目录')
            for name in ('install_node.py','monitor_agent.py','requirements.txt','node-monitor-agent.service'):
                remote.put(stage, name, (ROOT/'agent'/name).read_bytes())
            if bundle:
                remote.put(stage,'agent-bundle.tar.gz',bundle.read_bytes())
                remote.run('test "$(sha256sum '+shlex.quote(stage+'/agent-bundle.tar.gz')+' | cut -d " " -f 1)" = '+shlex.quote(bundle_hash))
                remote.run('tar --no-same-owner -xzf '+shlex.quote(stage+'/agent-bundle.tar.gz')+' -C '+shlex.quote(stage))
                remote.run(shlex.quote(stage+'/node-monitor-agent/node-monitor-agent')+' --self-test')
                remote.installer=stage+'/node-monitor-agent/node-monitor-agent'
                remote.put(stage,'bundle.sha256',(bundle_hash+'\n').encode())
            ca = args.ca_file.read_bytes() if args.ca_file else None
            if ca:
                remote.put(stage, 'ca.crt', ca)
            if not args.check_only:
                remote.start_session(stage)
                session_started = True
            print('检查目标机出站 HTTPS 与防火墙；仅放通上报目的地址/端口，不开放入站端口…', flush=True)
            check = remote.helper(stage, 'check', url=args.url, firewall_mode='inspect' if args.check_only else 'ensure')
            if abs(check['clock']-time.time()) > 55:
                raise InstallError('节点与监控中心时钟相差超过 55 秒，请先启用时间同步')
            print(f"检查通过：{check['hostname']}，Python {check['python_version']}，HTTPS 证书有效；Agent={'运行中' if check['active'] else '未运行'}")
            network = check.get('network')
            if network:
                print(f"出站 {network['host']}:{network['port']}/TCP 已通过 TLS 校验；防火墙={','.join(network['firewalls']) or '未启用受支持的管理器'}；新增规则={network['rules_added']}；安装后还将验证服务账户真实上报。", flush=True)
            server = find_server(db, args.ip, args.port, args.server_id)
            db.rollback()
            if args.check_only:
                print('检查完成，未修改节点或凭据。')
                completed = True
                return {'server_id':server.id if server else None,'status':'checked'}
            print('准备新版并彻底停止旧 Agent、移除旧服务和配置；首次部署也统一覆盖安装…', flush=True)
            prepared = remote.helper(stage, 'prepare')
            release = prepared['release']
            if prepared.get('recovered_previous'):
                print('已接管上次中断的安装记录；旧进程已确认终止，继续本次覆盖安装。', flush=True)
            if not re.fullmatch(r'/opt/node-monitor-agent/releases/[0-9a-f]{32}', release):
                raise InstallError('节点返回了无效的 Agent 版本目录')
            # copytree preserves the staging directory's SELinux xattrs. Restore
            # the destination labels before systemd executes the installed files.
            remote.run('if command -v restorecon >/dev/null 2>&1; then restorecon -R -- '+shlex.quote(release)+'; fi', root=True)
            print('签发单节点 Token，部署服务并验证首次 HTTPS 上报…', flush=True)
            remote.assert_session()
            snapshot, token, digest = provision(db, args.ip, args.port, args.user, check['hostname'], args.server_id,
                                               require_agent=getattr(args, 'reinstall', False))
            config = {'url':args.url, 'server_id':snapshot['id'], 'token':token, 'interval_seconds':300, 'jitter_seconds':15}
            if ca:
                config['ca_file'] = '/etc/node-monitor-agent/ca.crt'
            remote.put(stage, 'config.json', json.dumps(config).encode(), 0o600)
            activated = True
            remote.helper(stage, 'activate', release=release)
            finished = remote.helper(stage, 'finish', release=release)
            completed = True
            if finished.get('cleanup_warning'):
                print('新版上报已通过；部分旧版本文件未能清理，请核对 /opt/node-monitor-agent 下的目录权限。', flush=True)
            print(f"安装成功：节点 ID={snapshot['id']}，服务账户真实 HTTPS 上报已通过，每 300 秒上报，systemd 已启用开机启动。")
            print('SSH 密码未保存，未创建 SSH 密钥或修改 authorized_keys/crontab。')
            return {'server_id':snapshot['id'],'status':'installed'}
        except BaseException as error:
            network_restore_safe = not getattr(error, 'recovery_required', False)
            preserve_stage = not network_restore_safe
            if release and not activated:
                try:
                    remote.helper(stage, 'rollback', release=release)
                except Exception:
                    print('安装准备未完成清理，请检查目标安装锁 '+release, file=sys.stderr)
                    network_restore_safe = False
                    preserve_stage = True
            if activated:
                try:
                    remote.helper(stage, 'rollback', release=release)
                    print('已恢复目标机器原有 Agent 服务和配置。', file=sys.stderr)
                except Exception:
                    print('无法确认远端恢复结果；保留当前凭据及受保护的节点备份，请检查目标 node-monitor-agent 服务。', file=sys.stderr)
                    print(f'节点备份：{release}/.rollback；安装目录：{stage}', file=sys.stderr)
                    # An uncertain remote activation may still be using this token.
                    snapshot = None
                    network_restore_safe = False
            if snapshot:
                restore_credentials(db, snapshot, digest)
                print('已撤销本次 Token；已有节点的原采集方式及凭据已恢复。', file=sys.stderr)
            if stage and session_started and network_restore_safe:
                try:
                    remote.helper(stage, 'network-rollback')
                except Exception:
                    # Retain the journal for manual recovery, including pre-activation failures.
                    preserve_stage = True
                    print('出站规则恢复未确认，请核对 '+stage+'/network-changes.json；未关闭防火墙或改动其他规则。', file=sys.stderr)
            raise
        finally:
            if stage and (completed or not activated) and not preserve_stage:
                try:
                    remote.run('rm -rf -- '+shlex.quote(stage), root=True, timeout=15)
                except Exception:
                    print('临时文件未能清理，请在目标机删除 '+stage, file=sys.stderr)
            try:
                remote.close_session()
            finally:
                client.close()


def main():
    args = parse_args()
    password = getpass.getpass('SSH 密码（不显示、不保存）：')
    return install(args,password)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        # SSH/SQL exceptions may embed URLs, usernames or configuration data.
        print('Agent 安装失败：'+(str(error) if isinstance(error,InstallError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
