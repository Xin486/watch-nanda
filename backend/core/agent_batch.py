"""Serial Agent recovery queue sharing the existing deployment worker limits."""
from collections import Counter
from threading import Lock, Thread
import time
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
import paramiko
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from core import operations as ops, ssh_profiles
from models.database import SessionLocal
from models.models import Server

router = APIRouter(prefix='/api/ops/agent-batches', dependencies=[Depends(ops.require_operator)])
lock = Lock()
batches = {}
manual_inputs = {}
WAITING = {'needs_credentials', 'needs_trust'}


class BatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    server_ids: list[int] = Field(min_length=1, max_length=500)


class RetryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str | None = Field(default=None, min_length=1, max_length=50, pattern=r'^[\w.@-]+$')
    password: SecretStr | None = None

    @model_validator(mode='after')
    def credentials(self):
        if (self.username is None) != (self.password is None):
            raise ValueError('请同时填写 SSH 用户名和密码')
        if self.password is not None:
            text = self.password.get_secret_value()
            if not 1 <= len(text) <= 1024 or any(c in text for c in '\r\n\x00'):
                raise ValueError('请输入有效的 SSH 密码')
        return self


def payload(batch, *, items=True):
    result = {key: batch[key] for key in ('id', 'created_at', 'cancelled')}
    counts = Counter(item['status'] for item in batch['items'])
    result['counts'] = dict(counts)
    result['total'] = len(batch['items'])
    result['status'] = ('stopping' if batch['cancelled'] and counts['running'] else
                        'cancelled' if batch['cancelled'] else
                        'running' if batch['coordinator'] or counts['queued'] or counts['running'] else
                        'waiting_input' if any(counts[state] for state in WAITING) else 'finished')
    if items:
        result['items'] = [{key: value for key, value in item.items() if not key.startswith('_') and key != 'logs'}
                           | {'has_logs': bool(item['logs'])} for item in batch['items']]
    return result


def find(batch_id):
    batch = batches.get(batch_id)
    if batch is None:
        raise HTTPException(404, '批次不存在或部署服务已重启，请先核验节点上报再重新选择')
    return batch


def assert_available(batch_id=None):
    # Caller holds the batch lock. Old failed batches cannot start a second queue.
    if any(row['id'] != batch_id and not row['cancelled'] and
           (row['coordinator'] or any(item['status'] in WAITING | {'queued', 'running'} for item in row['items']))
           for row in batches.values()):
        raise HTTPException(409, '已有未结束的批次，请进入该批次继续或停止队列')


def validate_node(item):
    with SessionLocal() as db:
        node = db.get(Server, item['server_id'])
        if node is None or not node.is_active or node.collection_mode != 'agent':
            raise HTTPException(409, '节点已禁用、删除或不再使用 Agent，未执行部署')
        if (node.ip_address or node.hostname, node.ssh_port or 22) != item['_identity']:
            raise HTTPException(409, '节点连接地址已变化，请重新选择，未执行部署')


def choose_config(item, manual):
    validate_node(item)
    if manual:
        choices = [manual]
    else:
        try:
            choices = ssh_profiles.candidates()
        except (OSError, ValueError, KeyError):
            choices = []
        try:
            saved = ops.configuration(item['server_id'], ops.Connection())
            choices.append({'username': saved['username'], 'password': saved['password']})
            saved.clear()
        except HTTPException as error:
            if error.status_code != 422:
                raise
    seen, per_user = set(), Counter()
    try:
        for choice in choices:
            identity = (choice['username'], choice['password'])
            if identity in seen or per_user[identity[0]] >= 3:
                continue
            seen.add(identity)
            per_user[identity[0]] += 1
            config = ops.configuration(item['server_id'], ops.Connection(use_saved=False, **choice))
            try:
                if (config['host'], config['port']) != item['_identity']:
                    raise HTTPException(409, '节点连接地址已变化，请重新选择，未执行部署')
                validate_node(item)
                client = ops.connect_ssh(config)
                client.close()
                validate_node(item)
                config['reinstall'] = True
                return config
            except paramiko.AuthenticationException:
                config.clear()
                continue
            except BaseException:
                config.clear()
                raise
        return None
    finally:
        for choice in choices:
            choice.clear()
        seen.clear()


def run_item(batch, item, job):
    config = None
    with lock:
        manual = manual_inputs.pop((batch['id'], item['server_id']), None)
    try:
        config = choose_config(item, manual)
        if config is None:
            with lock:
                item.update(status='needs_credentials', message='没有可用的 SSH 凭据或认证失败，请输入此节点用户名和密码')
            return
        with lock:
            item.update(message='SSH 认证通过，正在重部署并验证真实 HTTPS 上报')
        ops.run_deployment(job, config)
        with lock:
            item.update(status=job['status'], message=job['message'], logs=list(job['logs']))
    except Exception as error:
        if isinstance(error, (ops.UnknownHost, paramiko.BadHostKeyException)):
            safe = ops.safe_error(error)
            result = {**safe, 'status': 'needs_trust', 'previous_fingerprint': safe.get('previous_fingerprint'),
                      'host': item['_identity'][0], 'port': item['_identity'][1],
                      'message': '主机指纹已变化，请核对新旧指纹后确认更换；队列已继续处理其他节点' if isinstance(error, paramiko.BadHostKeyException) else '请核对主机指纹并确认信任，队列已继续处理其他节点'}
        elif isinstance(error, HTTPException):
            result = {'status': 'failed', 'message': str(error.detail)}
        else:
            safe = ops.safe_error(error)
            result = {'status': 'failed', 'message': safe['message']}
        with lock:
            item.update(result)
    finally:
        if config:
            config.clear()
        if manual:
            manual.clear()
        with lock:
            item['logs'] = [line[:1000] for line in job['logs'][-80:]]
            message = item['message']
        with ops.guard:
            if ops.active_jobs.get(item['server_id']) == job['id']:
                ops.active_jobs.pop(item['server_id'], None)
            if job['status'] == 'running':
                job.update(status='failed', message=message, finished_at=time.time())


def coordinate(batch):
    try:
        while True:
            with lock:
                item = next((row for row in batch['items'] if row['status'] == 'queued'), None)
                if batch['cancelled'] or item is None:
                    return
            try:
                job = ops.reserve_deployment(item['server_id'], reinstall=True)
            except HTTPException as error:
                if error.status_code in (409, 429):
                    time.sleep(1)
                    continue
                with lock:
                    item.update(status='failed', message=str(error.detail))
                continue
            except Exception:
                with lock:
                    item.update(status='failed', message='任务名额暂不可用，请重试')
                continue
            with lock:
                if batch['cancelled']:
                    with ops.guard:
                        ops.active_jobs.pop(item['server_id'], None)
                        job.update(status='failed', message='批次已停止', finished_at=time.time())
                    return
                item.update(status='running', message='正在按优先级验证常用/已存 SSH 凭据', job_id=job['id'])
            try:
                ops.workers.submit(run_item, batch, item, job).result()
            except Exception:
                with ops.guard:
                    ops.active_jobs.pop(item['server_id'], None)
                    job.update(status='failed', message='部署工作线程不可用', finished_at=time.time())
                with lock:
                    item.update(status='failed', message='部署工作线程不可用，可稍后重试')
    finally:
        with lock:
            batch['coordinator'] = False
        kick(batch)


def kick(batch):
    with lock:
        if batch['coordinator'] or batch['cancelled'] or not any(row['status'] == 'queued' for row in batch['items']):
            return
        batch['coordinator'] = True
    try:
        Thread(target=coordinate, args=(batch,), daemon=True, name='agent-batch-queue').start()
    except Exception:
        with lock:
            batch['coordinator'] = False
            for row in batch['items']:
                if row['status'] == 'queued':
                    row.update(status='failed', message='队列暂不可启动，请重试')
        raise HTTPException(503, '队列暂不可启动') from None


@router.post('')
def create_batch(data: BatchInput):
    ids = list(dict.fromkeys(data.server_ids))
    if any(server_id <= 0 for server_id in ids):
        raise HTTPException(422, '节点编号不合法')
    with SessionLocal() as db:
        nodes = {node.id: node for node in db.query(Server).filter(Server.id.in_(ids)).all()}
        if any(i not in nodes or not nodes[i].is_active or nodes[i].collection_mode != 'agent' for i in ids):
            raise HTTPException(409, '所选节点包含已禁用、删除或非 Agent 节点，请刷新后重新选择')
        items = [{'server_id': i, 'hostname': nodes[i].hostname, 'username': nodes[i].ssh_user or 'root',
                  'address': f'{nodes[i].ip_address or nodes[i].hostname}:{nodes[i].ssh_port or 22}',
                  '_identity': (nodes[i].ip_address or nodes[i].hostname, nodes[i].ssh_port or 22),
                  'status': 'queued', 'message': '等待队列', 'logs': [], 'job_id': None} for i in ids]
    with lock:
        assert_available()
        while len(batches) >= 5:
            oldest = next(iter(batches))
            if batches[oldest]['coordinator']:
                raise HTTPException(409, '请等待当前批次结束')
            batches.pop(oldest)
        batch = {'id': uuid4().hex, 'created_at': time.time(), 'cancelled': False, 'coordinator': False, 'items': items}
        batches[batch['id']] = batch
    kick(batch)
    with lock:
        return payload(batch)


@router.get('')
def list_batches():
    with lock:
        return [payload(batch, items=False) for batch in reversed(list(batches.values()))]


@router.get('/{batch_id}')
def get_batch(batch_id: str):
    with lock:
        batch = find(batch_id)
        result = payload(batch)
    for item in result['items']:
        if item['status'] == 'running' and item['job_id']:
            with ops.guard:
                job = ops.jobs.get(item['job_id'])
                if job:
                    item['has_logs'] = bool(job['logs'])
    return result


@router.get('/{batch_id}/nodes/{server_id}')
def get_item(batch_id: str, server_id: int):
    with lock:
        batch = find(batch_id)
        item = next((row for row in batch['items'] if row['server_id'] == server_id), None)
        if item is None:
            raise HTTPException(404, '节点不在此批次中')
        result = {key: list(value) if key == 'logs' else value for key, value in item.items() if not key.startswith('_')}
    if result['status'] == 'running' and result['job_id']:
        with ops.guard:
            job = ops.jobs.get(result['job_id'])
            if job:
                result['logs'] = list(job['logs'])
    return result


@router.post('/{batch_id}/nodes/{server_id}/retry')
def retry_item(batch_id: str, server_id: int, data: RetryInput):
    with lock:
        batch = find(batch_id)
        if batch['cancelled']:
            raise HTTPException(409, '批次已停止，请重新选择节点')
        assert_available(batch_id)
        item = next((row for row in batch['items'] if row['server_id'] == server_id), None)
        if not item or item['status'] not in WAITING | {'failed'}:
            raise HTTPException(409, '此节点不处于可重试状态')
        if data.password is not None:
            manual_inputs[(batch_id, server_id)] = {'username': data.username, 'password': data.password.get_secret_value()}
        item.update(status='queued', message='已加入重试队列', logs=[], job_id=None)
        item.pop('fingerprint', None)
    kick(batch)
    with lock:
        return payload(batch)


@router.post('/{batch_id}/stop')
def stop_batch(batch_id: str):
    with lock:
        batch = find(batch_id)
        batch['cancelled'] = True
        for item in batch['items']:
            if item['status'] in WAITING | {'queued'}:
                item.update(status='cancelled', message='队列已停止，未执行部署')
            secret = manual_inputs.pop((batch_id, item['server_id']), None)
            if secret:
                secret.clear()
        return payload(batch)


@router.post('/{batch_id}/retry-waiting')
def retry_waiting(batch_id: str):
    with lock:
        batch = find(batch_id)
        if batch['cancelled']:
            raise HTTPException(409, '批次已停止，请重新选择节点')
        assert_available(batch_id)
        for item in batch['items']:
            if item['status'] == 'needs_credentials':
                item.update(status='queued', message='重新尝试常用及已存凭据', logs=[], job_id=None)
    kick(batch)
    with lock:
        return payload(batch)
