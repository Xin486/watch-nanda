"""Authenticated SSH diagnostics, ephemeral web terminals and bounded Agent jobs."""
import asyncio
import base64
import codecs
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Lock, Thread
import time
from uuid import uuid4
from datetime import datetime

import paramiko
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from core import ops_auth
from core.ssh_credentials import decrypt_password, encrypt_password
from core.status import freshness
from models.database import SessionLocal
from models.models import Server
from worker import tasks

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix='/api/ops')
logger = logging.getLogger(__name__)
guard = Lock()
jobs = {}
active_jobs = {}
terminals = set()
workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='agent-deploy')


class Login(BaseModel):
    password: SecretStr


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    use_saved: bool = True
    username: str | None = Field(default=None, min_length=1, max_length=50, pattern=r'^[\w.@-]+$')
    password: SecretStr | None = None
    save_credentials: bool = False


class Trust(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fingerprint: str = Field(pattern=r'^SHA256:[A-Za-z0-9+/]{43}$')
    previous_fingerprint: str | None = Field(default=None, pattern=r'^SHA256:[A-Za-z0-9+/]{43}$')
    expected_host: str | None = Field(default=None, min_length=1, max_length=255)
    expected_port: int | None = Field(default=None, ge=1, le=65535)


class UnknownHost(paramiko.SSHException):
    def __init__(self, key):
        self.fingerprint = fingerprint(key)


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


class IdentifyUnknown(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        raise UnknownHost(key)


def configuration(server_id, data):
    with SessionLocal() as db:
        node = db.get(Server, server_id)
        if node is None or not node.is_active:
            raise HTTPException(404, '节点不存在或已禁用')
        user = node.ssh_user or 'root'
        if data.use_saved:
            if data.username and data.username != user:
                raise HTTPException(422, '使用已存凭据时不能修改用户名')
            try:
                password = decrypt_password(node)
            except (ValueError, OSError):
                raise HTTPException(422, '没有可用的已存密码，请选择手动输入') from None
        else:
            user = data.username or user
            password = data.password.get_secret_value() if data.password else ''
        if not 1 <= len(password) <= 1024 or any(c in password for c in '\r\n\x00'):
            raise HTTPException(422, '请输入有效的 SSH 用户名和密码')
        return {'server_id': node.id, 'host': node.ip_address or node.hostname,
                'port': node.ssh_port or 22, 'username': user, 'password': password,
                'expected_user': node.ssh_user, 'expected_credentials': node.ssh_password_encrypted}


def connect_ssh(config):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(IdentifyUnknown())
    try:
        client.connect(config['host'], port=config['port'], username=config['username'], password=config['password'],
                       look_for_keys=False, allow_agent=False, timeout=8, banner_timeout=10, auth_timeout=10,
                       channel_timeout=10)
        client.get_transport().set_keepalive(20)
        return client
    except BaseException:
        client.close()
        raise


def safe_error(error):
    if isinstance(error, UnknownHost):
        return {'ok': False, 'code': 'unknown_host', 'message': '首次连接，请核对目标主机指纹后确认信任', 'fingerprint': error.fingerprint}
    if isinstance(error, paramiko.BadHostKeyException):
        return {'ok': False, 'code': 'host_key_changed',
                'message': '主机指纹已变化，请核对新旧指纹，确认后可更新并继续连接',
                'fingerprint': fingerprint(error.key), 'previous_fingerprint': fingerprint(error.expected_key)}
    if isinstance(error, paramiko.AuthenticationException):
        return {'ok': False, 'code': 'authentication_failed', 'message': '用户名或密码认证失败；请检查凭据，不会自动尝试其他密码'}
    return {'ok': False, 'code': 'connection_failed', 'message': 'SSH 连接失败：' + type(error).__name__}


def assert_idle(server_id):
    with guard:
        if server_id in active_jobs:
            raise HTTPException(409, '节点正在部署 Agent，请等待任务结束后修改配置')


def require_operator(request: Request):
    return ops_auth.authorized(request)


@router.post('/login')
async def login(data: Login, request: Request, response: Response):
    ops_auth.secure(request)
    address = request.client.host if request.client else 'unknown'
    token = await asyncio.to_thread(ops_auth.verify_login, data.password.get_secret_value(), address)
    response.set_cookie(ops_auth.COOKIE, token, max_age=ops_auth.TTL, secure=True, httponly=True, samesite='strict', path='/api/ops')
    response.headers['Cache-Control'] = 'no-store'
    return {'authenticated': True, 'expires_in': ops_auth.TTL}


@router.get('/session')
def session(request: Request):
    ops_auth.authorized(request)
    return {'authenticated': True}


@router.post('/logout')
def logout(request: Request, response: Response):
    ops_auth.secure(request)
    ops_auth.logout(request)
    response.delete_cookie(ops_auth.COOKIE, path='/api/ops', secure=True, httponly=True, samesite='strict')
    return {'success': True}


@router.post('/nodes/{server_id}/ssh-test', dependencies=[Depends(require_operator)])
def ssh_test(server_id: int, data: Connection):
    config = configuration(server_id, data)
    started = time.monotonic()
    try:
        client = connect_ssh(config)
        try:
            result = {'ok': True, 'message': 'SSH 握手及密码认证成功',
                      'fingerprint': fingerprint(client.get_transport().get_remote_server_key()),
                      'latency_ms': round((time.monotonic()-started)*1000), 'username': config['username']}
        finally:
            client.close()
        if data.save_credentials and not data.use_saved:
            result.update(save_configuration(config))
        return result
    except Exception as error:
        if isinstance(error, HTTPException):
            raise
        return {**safe_error(error), 'host': config['host'], 'port': config['port']}


def save_configuration(config):
    assert_idle(config['server_id'])
    with SessionLocal() as db:
        node = db.query(Server).filter(Server.id == config['server_id']).with_for_update().first()
        if (not node or not node.is_active
                or (node.ip_address or node.hostname, node.ssh_port or 22) != (config['host'], config['port'])
                or ('expected_user' in config and node.ssh_user != config['expected_user'])
                or ('expected_credentials' in config and node.ssh_password_encrypted != config['expected_credentials'])):
            raise HTTPException(409, '节点连接信息已变化，请刷新页面')
        node.ssh_user = config['username']
        node.ssh_password_encrypted = encrypt_password(config['password'], node)
        ssh = node.collection_mode == 'ssh'
        reset = ssh or freshness(node)[0] != 'online'
        if reset:
            node.status = 'pending'
            node.offline_since = None
            node.last_alert_time = None
            if ssh:
                node.latest_status_data = None
            else:
                # Retain Agent report IDs/timestamps so replay protection survives
                # an SSH credential edit; only a new report restores live metrics.
                node.latest_status_data = {**(node.latest_status_data or {}),
                    'collection_reset_at': datetime.utcnow().isoformat()+'Z'}
        patch = {'id': node.id, 'ssh_user': node.ssh_user, 'has_ssh_password': True}
        if reset:
            patch.update(status='pending', collection_status='pending', data_age_seconds=None)
        db.commit()
    queued = False
    if ssh:
        try:
            tasks.request_server_refresh(config['server_id'])
            queued = True
        except Exception:
            # Credentials have committed; the periodic collector will retry.
            logger.exception('Could not queue refreshed SSH collection for node %s', config['server_id'])
    message = ('用户名和密码已加密同步；节点待采集，正在重新采集指标。' if ssh and queued else
               '用户名和密码已加密同步；节点待采集，后台将按周期重试。' if ssh else
               '用户名和密码已加密同步；节点待采集，等待 Agent 主动上报。' if reset else
               '用户名和密码已加密同步；继续使用 Agent 主动上报。')
    return {'credentials_saved': True, 'credentials_message': message, 'server': patch,
            'collection_reset': reset, 'collection_queued': queued}


@router.post('/nodes/{server_id}/trust-host', dependencies=[Depends(require_operator)])
def trust_host(server_id: int, data: Trust):
    assert_idle(server_id)
    with SessionLocal() as db:
        node = db.get(Server, server_id)
        if not node or not node.is_active:
            raise HTTPException(404, '节点不存在')
        host, port = node.ip_address or node.hostname, node.ssh_port or 22
    if ((data.expected_host is not None and data.expected_host != host)
            or (data.expected_port is not None and data.expected_port != port)):
        raise HTTPException(409, '节点连接地址已变化，请重新检测后核对指纹')
    transport = None
    sock = socket.create_connection((host, port), timeout=8)
    try:
        transport = paramiko.Transport(sock)
        lookup = host if port == 22 else f'[{host}]:{port}'
        path = (Path.home()/'.ssh'/'known_hosts').resolve()
        known = paramiko.HostKeys(str(path)) if path.exists() else paramiko.HostKeys()
        existing = known.lookup(lookup)
        if existing:
            # Match SSHClient's preference for the saved key type. A server may
            # advertise several valid keys, each with a different fingerprint.
            preferred = next(iter(existing))
            options = transport.get_security_options()
            if preferred in options.key_types:
                options.key_types = (preferred,) + tuple(name for name in options.key_types if name != preferred)
        transport.start_client(timeout=10)
        key = transport.get_remote_server_key()
        if fingerprint(key) != data.fingerprint:
            raise HTTPException(409, '指纹再次变化，未保存信任')
        path.parent.mkdir(mode=0o700, exist_ok=True)
        with guard, SessionLocal() as db:
            if server_id in active_jobs:
                raise HTTPException(409, '节点正在部署 Agent，请等待任务结束后更新指纹')
            node = db.query(Server).filter(Server.id == server_id).with_for_update().first()
            if (not node or not node.is_active
                    or (node.ip_address or node.hostname, node.ssh_port or 22) != (host, port)):
                raise HTTPException(409, '节点连接地址已变化，未保存信任')
            known = paramiko.HostKeys(str(path)) if path.exists() else paramiko.HostKeys()
            existing = known.lookup(lookup)
            if data.previous_fingerprint:
                if not existing or data.previous_fingerprint not in {fingerprint(old) for old in existing.values()}:
                    raise HTTPException(409, '已保存的指纹已变化，请重新检测后确认')
                replace_known_host(path, lookup, key)
            elif existing and any(old.asbytes() != key.asbytes() for old in existing.values()):
                raise HTTPException(409, '已有指纹不匹配，请重新检测并明确确认更换指纹')
            elif not existing:
                replace_known_host(path, lookup, key, replacing=False)
        return {'success': True}
    finally:
        if transport:
            transport.close()
        else:
            sock.close()


def replace_known_host(path, lookup, key, *, replacing=True):
    """Replace only this endpoint; retain comments, aliases and unrelated hashed entries."""
    original = path.read_bytes().decode('utf-8') if path.exists() else ''
    lines = []
    for line in original.splitlines(keepends=True):
        entry = paramiko.hostkeys.HostKeyEntry.from_line(line.strip()) if line.strip() and not line.lstrip().startswith('#') else None
        if not entry or entry.key is None:
            lines.append(line)
            continue
        def matches(name):
            if name == lookup:
                return True
            return name.startswith('|1|') and paramiko.HostKeys.hash_host(lookup, name) == name
        remaining = [name for name in entry.hostnames if not matches(name)]
        if len(remaining) == len(entry.hostnames):
            lines.append(line)
        elif remaining:
            fields = line.lstrip().split(None, 1)
            lines.append(','.join(remaining)+' '+fields[1])
    if replacing:
        backup = path.with_name('known_hosts.backup-'+uuid4().hex)
        with backup.open('x', encoding='utf-8', newline='') as stream:
            os.chmod(backup, 0o600)
            stream.write(original)
    content = ''.join(lines)
    if content and not content.endswith('\n'):
        content += '\n'
    content += f'{lookup} {key.get_name()} {key.get_base64()}\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent,
                                         prefix='.known_hosts-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_log(job, line, password):
    text = line.replace(password, '[REDACTED]').rstrip()[:2000]
    with guard:
        job['logs'].append(text)
        job['logs'] = job['logs'][-150:]


def run_deployment(job, config):
    process = None
    try:
        process = subprocess.Popen([sys.executable, '-u', str(ROOT/'backend'/'agent_job.py')],
            cwd=str(ROOT/'backend'), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=os.name == 'posix')
        process.stdin.write(json.dumps(config).encode())
        process.stdin.close()
        def drain():
            for raw in iter(process.stdout.readline, b''):
                append_log(job, raw.decode('utf-8', errors='replace'), config['password'])
        reader = Thread(target=drain, daemon=True)
        reader.start()
        try:
            code = process.wait(timeout=900)
        except subprocess.TimeoutExpired:
            process.terminate()  # Worker catches SIGTERM and runs installer rollback.
            try:
                process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            raise TimeoutError()
        reader.join(timeout=5)
        with guard:
            job['status'] = 'success' if code == 0 else 'failed'
            job['message'] = 'Agent 部署完成，已验证 HTTPS 上报' if code == 0 else '部署未完成，请查看检查结果；未确认恢复时需人工核验节点'
    except Exception as error:
        append_log(job, '任务结束：'+type(error).__name__+'；请检查节点安装状态', config['password'])
        with guard:
            job['status'], job['message'] = 'failed', '部署超时或执行失败，请核验远端状态'
    finally:
        if process and process.stdout:
            process.stdout.close()
        with guard:
            active_jobs.pop(job['server_id'], None)
            job['finished_at'] = time.time()
        config.clear()


def job_payload(job):
    return {key: list(value) if key == 'logs' else value for key, value in job.items()}


@router.post('/nodes/{server_id}/deploy', dependencies=[Depends(require_operator)])
def deploy(server_id: int, data: Connection):
    return start_deployment(server_id, data)


@router.post('/nodes/{server_id}/redeploy', dependencies=[Depends(require_operator)])
def redeploy(server_id: int, data: Connection):
    with SessionLocal() as db:
        node = db.get(Server, server_id)
        if node is None or not node.is_active:
            raise HTTPException(404, '节点不存在或已禁用')
        if node.collection_mode != 'agent':
            raise HTTPException(409, '该节点不是 Agent 采集，无法从此页面重新部署')
    return start_deployment(server_id, data, reinstall=True)


def start_deployment(server_id, data, *, reinstall=False):
    config = configuration(server_id, data)
    config['reinstall'] = reinstall
    if data.save_credentials and not data.use_saved:
        # Verify before persisting manually submitted credentials.
        client = connect_ssh(config)
        client.close()
        save_configuration(config)
    job = reserve_deployment(server_id, reinstall=reinstall)
    try:
        workers.submit(run_deployment, job, config)
    except Exception:
        with guard:
            active_jobs.pop(server_id, None)
            jobs.pop(job['id'], None)
        raise HTTPException(503, '部署任务暂不可用') from None
    logger.info('Operator started Agent deployment for node %s, job %s', server_id, job['id'])
    return job_payload(job)


def reserve_deployment(server_id, *, reinstall=False):
    with guard:
        if server_id in active_jobs:
            raise HTTPException(409, '该节点已有部署任务，请勿重复点击')
        if len(active_jobs) >= 2:
            raise HTTPException(429, '已有两个部署任务，请稍后再试')
        for job_id in list(jobs):
            if jobs[job_id]['status'] != 'running' and time.time()-jobs[job_id]['created_at'] > 86400:
                del jobs[job_id]
        if len(jobs) >= 100:
            old = next((key for key, value in jobs.items() if value['status'] != 'running'), None)
            if old:
                del jobs[old]
        job = {'id': str(uuid4()), 'server_id': server_id, 'kind': 'redeploy' if reinstall else 'deploy',
               'status': 'running', 'logs': [],
               'message': '正在检查 SSH、sudo、时钟、出站防火墙、证书及独立安装包', 'created_at': time.time()}
        jobs[job['id']], active_jobs[server_id] = job, job['id']
    return job


@router.get('/jobs', dependencies=[Depends(require_operator)])
def list_jobs(server_id: int):
    with guard:
        return [job_payload(job) for job in jobs.values() if job['server_id'] == server_id][-5:]


@router.get('/jobs/{job_id}', dependencies=[Depends(require_operator)])
def get_job(job_id: str):
    with guard:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, '任务不存在或服务已重新启动，请核验远端状态')
        return job_payload(job)


@router.websocket('/nodes/{server_id}/terminal')
async def terminal(websocket: WebSocket, server_id: int):
    try:
        session_key = ops_auth.authorized(websocket, websocket=True)
    except HTTPException:
        await websocket.close(code=1008)
        return
    lease = (session_key, server_id)
    with guard:
        busy = lease in terminals or len(terminals) >= 6
        if not busy:
            terminals.add(lease)
    if busy:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    client = channel = None
    saved = {}
    config = {}
    pending_connect = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        if len(raw) > 8192:
            raise ValueError('Input too large')
        data = Connection.model_validate_json(raw)
        config = await asyncio.to_thread(configuration, server_id, data)
        pending_connect = asyncio.create_task(asyncio.to_thread(connect_ssh, config))
        client = await asyncio.shield(pending_connect)
        if data.save_credentials and not data.use_saved:
            saved = await asyncio.to_thread(save_configuration, config)
        channel = await asyncio.to_thread(client.invoke_shell, term='xterm-256color', width=100, height=28)
        channel.settimeout(0.1)
        config.clear()
        await websocket.send_json({'type': 'connected', **saved})
        deadline = time.monotonic()+3600
        idle = time.monotonic()
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        while time.monotonic() < deadline and time.monotonic()-idle < 900:
            with ops_auth.lock:
                valid = ops_auth.sessions.get(session_key, 0) > time.monotonic()
            if not valid:
                break
            for _ in range(8):
                if not channel.recv_ready():
                    break
                chunk = channel.recv(8192)
                if not chunk:
                    break
                await websocket.send_json({'type': 'output', 'data': decoder.decode(chunk)})
            if channel.closed or channel.exit_status_ready():
                break
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            if len(message) > 16384:
                raise ValueError('Input too large')
            command = json.loads(message)
            if command.get('type') == 'input':
                text = command.get('data')
                if not isinstance(text, str) or len(text) > 8192:
                    raise ValueError('Invalid input')
                idle = time.monotonic()
                await asyncio.to_thread(channel.sendall, text)
            elif command.get('type') == 'resize':
                columns, rows = command.get('cols'), command.get('rows')
                if type(columns) != int or type(rows) != int or not 20 <= columns <= 300 or not 5 <= rows <= 100:
                    raise ValueError('Invalid terminal dimensions')
                await asyncio.to_thread(channel.resize_pty, width=columns, height=rows)
        await websocket.send_json({'type': 'closed', 'message': '连接已结束（闲置上限 15 分钟，会话上限 1 小时）'})
    except WebSocketDisconnect:
        pass
    except (Exception, asyncio.CancelledError) as error:
        if not isinstance(error, asyncio.CancelledError):
            try:
                await websocket.send_json({'type': 'error', **safe_error(error), **saved,
                                           **({name: config[name] for name in ('host', 'port')} if config else {})})
            except Exception:
                pass
    finally:
        # A shielded connection can finish after client cancellation; close that too.
        if pending_connect and client is None:
            def close_late(task):
                if not task.cancelled() and task.exception() is None:
                    task.result().close()
            pending_connect.add_done_callback(close_late)
        if channel:
            channel.close()
        if client:
            client.close()
        with guard:
            terminals.discard(lease)
        try:
            await websocket.close()
        except Exception:
            pass
