"""Password-only SSH credentials. The encryption key never lives in the database."""
import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def key_path():
    return Path(os.getenv('SSH_CREDENTIAL_KEY_FILE', '/etc/node-monitor/ssh-credentials.key'))


def cipher():
    path = key_path()
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('SSH credential key must be a regular file')
        if os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
            raise ValueError('SSH credential key must be owned by the service user with mode 600')
        return Fernet(source.read(128).strip())


def identity(server):
    return [server.ip_address or server.hostname, server.ssh_port or 22, server.ssh_user or 'root']


def encrypt_password(password, server):
    if not isinstance(password, str) or not 1 <= len(password) <= 1024 or '\x00' in password:
        raise ValueError('Invalid SSH password')
    payload = json.dumps({'identity': identity(server), 'password': password}, ensure_ascii=False).encode()
    return cipher().encrypt(payload).decode('ascii')


def decrypt_password(server):
    encrypted = getattr(server, 'ssh_password_encrypted', None)
    if not encrypted:
        raise ValueError('请先配置 SSH 用户名和密码')
    try:
        payload = json.loads(cipher().decrypt(encrypted.encode('ascii')))
        if payload['identity'] != identity(server):
            raise ValueError('SSH connection changed')
        return payload['password']
    except (InvalidToken, KeyError, UnicodeError, json.JSONDecodeError):
        raise ValueError('SSH 凭据无法解密，请重新配置') from None
