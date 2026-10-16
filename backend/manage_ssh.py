"""Local administrator CLI: no password in arguments, logs or HTTP."""
import argparse
import getpass
import os

import paramiko
from cryptography.fernet import Fernet
from core.ssh_credentials import encrypt_password, key_path
from models.database import SessionLocal
from models.models import Server, AgentCredential


def initialize_key():
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print('Encryption key already exists; preserved.')
        return
    with os.fdopen(descriptor, 'wb') as target:
        target.write(Fernet.generate_key() + b'\n')
    print('Created SSH credential encryption key (keep an offline protected backup).')


def main():
    parser = argparse.ArgumentParser(description='Initialize SSH secret storage or set a verified password locally')
    parser.add_argument('--init-key', action='store_true')
    parser.add_argument('--server-id', type=int)
    parser.add_argument('--user')
    args = parser.parse_args()
    if args.init_key:
        initialize_key()
        return
    if not args.server_id:
        parser.error('--server-id is required')
    password = getpass.getpass('SSH password (hidden): ')
    with SessionLocal() as db:
        server = db.get(Server, args.server_id)
        if not server:
            parser.error('Unknown server ID')
        if args.user:
            server.ssh_user = args.user
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(server.ip_address or server.hostname, port=server.ssh_port or 22,
                           username=server.ssh_user or 'root', password=password,
                           look_for_keys=False, allow_agent=False, timeout=10, banner_timeout=15, auth_timeout=15)
        finally:
            client.close()
        server.ssh_password_encrypted = encrypt_password(password, server)
        server.collection_mode = 'ssh'
        server.latest_status_data = None
        server.status = 'pending'
        server.offline_since = None
        credential = db.get(AgentCredential, server.id)
        if credential:
            db.delete(credential)
        db.commit()
    print('Verified password stored encrypted; password-only SSH collection enabled.')


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('SSH configuration failed: ' + type(error).__name__)
        raise SystemExit(1)
