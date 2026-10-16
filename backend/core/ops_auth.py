"""HTTPS-only operator sessions; no node passwords or terminal transcripts are logged."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
from threading import Lock
import time
from urllib.parse import urlsplit

from fastapi import HTTPException

COOKIE = 'watch_nanda_ops'
TTL = 3600
lock = Lock()
sessions = {}
attempts = {}


def password_path():
    return Path(os.getenv('OPS_PASSWORD_FILE', '/etc/node-monitor/ops-password.json'))


def set_password(password):
    if not 12 <= len(password) <= 1024:
        raise ValueError('运维口令至少 12 个字符')
    path = password_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(32)
    payload = {'salt': salt, 'iterations': 600000,
               'digest': hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()}
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as target:
        json.dump(payload, target)
    temporary.replace(path)


def same_origin(connection, *, websocket=False):
    origin = connection.headers.get('origin')
    if not origin:
        return not websocket
    parsed = urlsplit(origin)
    return parsed.scheme == 'https' and parsed.netloc == connection.headers.get('host')


def secure(connection, *, websocket=False):
    if connection.scope.get('scheme') != ('wss' if websocket else 'https') or not same_origin(connection, websocket=websocket):
        raise HTTPException(403, '运维操作仅允许同源 HTTPS 访问')


def verify_login(password, address):
    now = time.monotonic()
    with lock:
        for key in list(attempts):
            attempts[key] = [stamp for stamp in attempts[key] if now - stamp < 600]
            if not attempts[key]:
                del attempts[key]
        if len(attempts.get(address, [])) >= 5 or sum(map(len, attempts.values())) >= 30:
            raise HTTPException(429, '登录尝试过多，请 10 分钟后重试')
        attempts.setdefault(address, []).append(now)
    try:
        descriptor = os.open(password_path(), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid != os.geteuid())):
                raise ValueError('Unsafe operator password file')
            saved = json.load(stream)
    except (OSError, ValueError):
        raise HTTPException(503, '运维登录尚未配置，请运行 backend/manage_ops.py') from None
    if not 1 <= len(password) <= 1024:
        raise HTTPException(401, '运维口令错误')
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(saved['salt']), saved['iterations']).hex()
    if not hmac.compare_digest(saved['digest'], digest):
        raise HTTPException(401, '运维口令错误')
    token = secrets.token_urlsafe(48)
    with lock:
        for key in list(sessions):
            if sessions[key] <= now:
                del sessions[key]
        if len(sessions) >= 100:
            sessions.pop(next(iter(sessions)))
        sessions[hashlib.sha256(token.encode()).hexdigest()] = now + TTL
        attempts.pop(address, None)
    return token


def authorized(connection, *, websocket=False):
    secure(connection, websocket=websocket)
    token = connection.cookies.get(COOKIE, '')
    key = hashlib.sha256(token.encode()).hexdigest()
    with lock:
        expiry = sessions.get(key, 0)
    if not token or time.monotonic() >= expiry:
        raise HTTPException(401, '请先进行运维登录')
    return key


def logout(connection):
    token = connection.cookies.get(COOKIE, '')
    with lock:
        sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
