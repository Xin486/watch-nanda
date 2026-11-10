"""Per-site configuration. No deployment address or provider secret lives in Git."""
import ipaddress
import json
import os
from pathlib import Path
import secrets
from threading import RLock
from urllib.parse import urlsplit, urlunsplit

from core.site_config import ROOT, load_site

lock = RLock()


def platform_path():
    return Path(os.getenv('PLATFORM_CONFIG_FILE', str(ROOT / '.runtime/platform.json')))


def hermes_path():
    return Path(os.getenv('HERMES_CONFIG_FILE') or load_site().get('HERMES_CONFIG_FILE') or ROOT / '.runtime/hermes/web-config.json')


def read_json(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('站点配置格式无效')
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('配置文件不能是符号链接')
    previous = path.stat() if path.exists() else None
    temp = path.with_name(path.name + '.' + secrets.token_hex(8))
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        if previous and os.name == 'posix' and os.geteuid() == 0:
            os.chown(temp, previous.st_uid, previous.st_gid)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def https_url(value, *, report=False):
    value = value.strip()
    if not value or any(c.isspace() for c in value) or '\\' in value:
        raise ValueError('请输入有效的 HTTPS 地址')
    if '://' not in value:
        value = 'https://' + value
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('地址必须使用 HTTPS，不含用户名、密码、查询参数或片段')
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('端口必须为 1–65535')
    path = parsed.path.rstrip('/') or ('/api/agent/report' if report else '')
    return urlunsplit(('https', parsed.netloc, path, '', ''))


def defaults():
    site = load_site()
    host = os.getenv('WATCH_NANDA_HTTPS_HOST') or site.get('WATCH_NANDA_HTTPS_HOST')
    port = os.getenv('WATCH_NANDA_FRONTEND_PORT') or site.get('WATCH_NANDA_FRONTEND_PORT', '3000')
    url = os.getenv('AGENT_REPORT_URL') or site.get('AGENT_REPORT_URL')
    if not url and host:
        authority = '[' + host + ']' if ':' in host else host
        url = f'https://{authority}:{port}/api/agent/report'
    return {'report_url': url or '', 'routes': [], 'ca_mode': 'site', 'has_custom_ca': False}


def agent_settings():
    with lock:
        return defaults() | read_json(platform_path()).get('agent', {})


def report_url(host):
    config = agent_settings()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    for route in sorted(config['routes'], key=lambda r: ipaddress.ip_network(r['network']).prefixlen, reverse=True):
        if address is not None and address in ipaddress.ip_network(route['network']):
            return route['url']
    if not config['report_url']:
        raise ValueError('请先在设置中配置 Agent 上报入口，或初始化本站地址')
    return https_url(config['report_url'], report=True)


def report_ca():
    config = agent_settings()
    if config['ca_mode'] == 'custom':
        path = platform_path().with_name('agent-ca-' + config.get('ca_digest', '') + '.crt')
    elif config['ca_mode'] == 'system':
        import ssl
        value = ssl.get_default_verify_paths().cafile
        if not value:
            raise ValueError('系统 CA 不可用，请选择本站或自定义 CA')
        path = Path(value)
    else:
        site = load_site()
        path = Path(os.getenv('AGENT_CA_FILE') or site.get('AGENT_CA_FILE') or
                    os.getenv('WATCH_NANDA_TLS_CA') or site.get('WATCH_NANDA_TLS_CA', '/etc/node-monitor/tls/ca.crt'))
    return path


def hermes_settings():
    return {'enabled': False, 'api_base': '', 'model': '', 'api_key': '', 'revision': 0} | read_json(hermes_path())


def public_hermes():
    config = hermes_settings()
    cli = os.getenv('HERMES_CLI') or load_site().get('HERMES_CLI', '')
    return {k: config[k] for k in ('enabled', 'api_base', 'model', 'revision')} | {
        'has_api_key': bool(config['api_key']), 'runtime_ready': bool(cli and Path(cli).is_file())}


def branding():
    return {'company_name': '', 'page_title': ''} | read_json(platform_path()).get('branding', {})
