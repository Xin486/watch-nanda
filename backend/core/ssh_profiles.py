"""Operator-managed reusable SSH credentials; only encrypted secrets reach disk."""
import json
import os
from pathlib import Path
import stat
from threading import Lock
from uuid import uuid4

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from core.operations import require_operator
from core.ssh_credentials import cipher

router = APIRouter(prefix='/api/ops/ssh-profiles', dependencies=[Depends(require_operator)])
lock = Lock()


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=1, max_length=50, pattern=r'^[\w.@-]+$')
    password: SecretStr | None = None
    priority: int = Field(default=100, ge=1, le=999)
    enabled: bool = True

    @field_validator('password')
    @classmethod
    def valid_password(cls, value):
        if value is not None:
            text = value.get_secret_value()
            if not 1 <= len(text) <= 1024 or any(c in text for c in '\r\n\x00'):
                raise ValueError('请输入有效的 SSH 密码')
        return value


def path():
    return Path(os.getenv('SSH_PROFILES_FILE', '/etc/node-monitor/ssh-profiles.json'))


def read():
    try:
        descriptor = os.open(path(), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return []
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid != os.geteuid())):
            raise ValueError('Unsafe SSH profiles file')
        rows = json.load(stream)
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError('Invalid SSH profiles file')
    return rows


def write(rows):
    destination = path()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.' + uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(rows, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def public(row):
    return {key: row[key] for key in ('id', 'name', 'username', 'priority', 'enabled')} | {'has_password': bool(row.get('encrypted'))}


def candidates():
    """Return an ephemeral decrypted snapshot, never an API response."""
    with lock:
        rows = sorted(read(), key=lambda row: (row['priority'], row['id']))
    result = []
    for row in rows:
        if not row['enabled']:
            continue
        try:
            payload = json.loads(cipher().decrypt(row['encrypted'].encode()))
            if (payload['id'], payload['username']) != (row['id'], row['username']):
                raise ValueError('Profile identity changed')
            result.append({'username': row['username'], 'password': payload['password']})
        except (InvalidToken, ValueError, KeyError, OSError):
            # A broken stored key is reported as unavailable, not as a successful login.
            continue
    return result


@router.get('')
def list_profiles():
    try:
        with lock:
            return [public(row) for row in sorted(read(), key=lambda row: (row['priority'], row['id']))]
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, '常用 SSH 凭据暂不可读取，请核对存储与密钥权限') from None


def save(data, profile_id=None):
    try:
        with lock:
            rows = read()
            old = next((row for row in rows if row['id'] == profile_id), None)
            if profile_id and old is None:
                raise HTTPException(404, '常用凭据不存在')
            if old is None and len(rows) >= 20:
                raise HTTPException(409, '最多保存 20 组常用凭据')
            if data.password is None and (old is None or old['username'] != data.username):
                raise HTTPException(422, '新增凭据或修改用户名时必须填写密码')
            row = {'id': profile_id or uuid4().hex, 'name': data.name.strip(), 'username': data.username,
                   'priority': data.priority, 'enabled': data.enabled}
            if not row['name']:
                raise HTTPException(422, '请填写凭据名称')
            if data.password is not None:
                payload = {'id': row['id'], 'username': row['username'], 'password': data.password.get_secret_value()}
                row['encrypted'] = cipher().encrypt(json.dumps(payload).encode()).decode()
            else:
                row['encrypted'] = old['encrypted']
            rows = [row if entry['id'] == profile_id else entry for entry in rows] if old else rows + [row]
            write(rows)
            return public(row)
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, '常用 SSH 凭据暂不可保存，请核对存储与加密密钥') from None


@router.post('')
def create_profile(data: ProfileInput):
    return save(data)


@router.put('/{profile_id}')
def update_profile(profile_id: str, data: ProfileInput):
    return save(data, profile_id)


@router.delete('/{profile_id}')
def delete_profile(profile_id: str):
    try:
        with lock:
            rows = read()
            kept = [row for row in rows if row['id'] != profile_id]
            if len(kept) == len(rows):
                raise HTTPException(404, '常用凭据不存在')
            write(kept)
        return {'success': True}
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, '常用 SSH 凭据暂不可删除') from None
