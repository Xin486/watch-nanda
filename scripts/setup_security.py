"""Prepare HTTPS and operator authentication without replacing existing secrets."""
import argparse
from datetime import datetime, timedelta
import getpass
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from core.config import settings


def write_private(path, data):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def validate_tls(host, ca, cert, key):
    """Verify hostname, lifetime, chain and key using an in-memory TLS handshake."""
    client_context = ssl.create_default_context(cafile=str(ca))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert), str(key))
    client_in, client_out, server_in, server_out = [ssl.MemoryBIO() for _ in range(4)]
    client = client_context.wrap_bio(client_in, client_out, server_hostname=host)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    for _ in range(20):
        try:
            client.do_handshake()
            return
        except ssl.SSLWantReadError:
            pass
        server_in.write(client_out.read())
        try:
            server.do_handshake()
        except ssl.SSLWantReadError:
            pass
        client_in.write(server_out.read())
    raise RuntimeError('HTTPS 证书握手校验未完成')


def tls(host):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    paths = [Path(os.environ[k]) for k in ('WATCH_NANDA_TLS_CA', 'WATCH_NANDA_TLS_CERT', 'WATCH_NANDA_TLS_KEY')]
    if all(p.is_file() for p in paths):
        validate_tls(host, *paths)
        print('保留并验证已有 HTTPS 证书、私钥和 CA。', flush=True)
        return
    if any(p.exists() for p in paths):
        raise RuntimeError('HTTPS 文件不完整，请恢复原 CA/证书/私钥；不会覆盖或混用新旧证书')
    now = datetime.utcnow()
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Watch Nanda '+host+' CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
          .not_valid_after(now+timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    names = [x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
    try:
        names.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        names.append(x509.DNSName(host))
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectAlternativeName(names), False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(ca_key, hashes.SHA256()))
    write_private(paths[0].with_name('ca.key'), ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    write_private(paths[0], ca.public_bytes(serialization.Encoding.PEM))
    write_private(paths[1], cert.public_bytes(serialization.Encoding.PEM))
    write_private(paths[2], key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    print('已生成本站独立 CA 与 HTTPS 证书。浏览器及 Agent 节点需信任：'+str(paths[0]), flush=True)


def operator(password_file=None):
    from core import ops_auth
    path = ops_auth.password_path()
    if path.exists():
        record = json.loads(path.read_text())
        info = path.stat()
        if path.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077 or not all(key in record for key in ('salt', 'digest', 'iterations')):
            raise RuntimeError('已有运维摘要权限或格式无效，请从备份恢复；不会覆盖原口令')
        print('保留已有运维口令摘要。', flush=True)
        return
    if password_file:
        password_file = Path(password_file)
        info = password_file.lstat()
        if password_file.is_symlink() or not password_file.is_file() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RuntimeError('运维口令输入文件必须由当前账户拥有、为普通文件且权限 600')
        password = password_file.read_text().rstrip('\r\n')
    elif sys.stdin.isatty():
        password = getpass.getpass('首次网页运维口令（至少12位，不显示）：')
        if getpass.getpass('再次输入：') != password:
            raise RuntimeError('两次口令不一致')
    else:
        password = secrets.token_urlsafe(24)
        initial = ROOT/'.runtime/initial-ops-password'
        write_private(initial, (password+'\n').encode())
        print('首次运维口令已生成，仅保存于权限600文件：'+str(initial), flush=True)
    ops_auth.set_password(password)
    password = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ops-password-file')
    args = parser.parse_args()
    tls(os.environ['WATCH_NANDA_HTTPS_HOST'])
    from manage_ssh import initialize_key
    initialize_key()
    operator(args.ops_password_file)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('安全配置失败：'+(str(error) if isinstance(error, RuntimeError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
