"""Render an optional standalone Nginx example; never install/reload it."""
import argparse
import ipaddress
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def render(host, networks, port=443, backend_port=7980, static_dir=None, tls_dir='/etc/node-monitor/tls', console=False):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9.-]+', host) or '..' in host:
            raise ValueError('host 必须为 IP 或域名')
    if not all(1 <= p <= 65535 for p in (port, backend_port)):
        raise ValueError('端口必须为 1–65535')
    allowed = [str(ipaddress.ip_network(n, strict=False)) for n in networks]
    if not allowed:
        raise ValueError('请明确指定管理网段')
    def quote(path):
        path = str(path)
        if not Path(path).is_absolute() or any(c in path for c in '\r\n\x00$'):
            raise ValueError('配置路径必须为绝对路径，不含控制字符或变量')
        return '"' + path.replace('\\', '\\\\').replace('"', '\\"') + '"'
    name = 'safeline-console.conf' if console else 'nginx.conf'
    text = (ROOT / 'deploy/production' / name).read_text()
    values = {'SITE_HOST': host, 'HTTPS_PORT': str(port), 'BACKEND_PORT': str(backend_port),
        'IPV6_LISTEN': f'        listen [::]:{port} ssl;' if ':' in host else '',
        'STATIC_DIR': quote(static_dir or ROOT / 'frontend/dist'), 'TLS_CERT': quote(Path(tls_dir) / 'server.crt'),
        'TLS_KEY': quote(Path(tls_dir) / 'server.key'), 'ADMIN_ALLOW': '\n'.join('            allow ' + n + ';' for n in allowed)}
    for key, value in values.items():
        text = text.replace('__' + key + '__', value)
    if not console:
        text = '# watch-nanda-managed-root: ' + str(ROOT.resolve()) + '\n' + text
    return text


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--allow-network', action='append', required=True)
    parser.add_argument('--port', type=int, default=443)
    parser.add_argument('--backend-port', type=int, default=7980)
    parser.add_argument('--static-dir')
    parser.add_argument('--tls-dir', default='/etc/node-monitor/tls')
    parser.add_argument('--console', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    content = render(args.host, args.allow_network, args.port, args.backend_port, args.static_dir, args.tls_dir, args.console)
    if args.output:
        with args.output.open('x') as out:
            out.write(content)
    else:
        print(content)
