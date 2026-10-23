"""Non-secret per-installation settings, kept outside Git and existing .env."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYS = {'WATCH_NANDA_HTTPS_HOST', 'WATCH_NANDA_TLS_CA', 'WATCH_NANDA_TLS_CERT',
        'WATCH_NANDA_TLS_KEY', 'WATCH_NANDA_NODE', 'AGENT_REPORT_URL', 'AGENT_CA_FILE',
        'SSH_CREDENTIAL_KEY_FILE', 'OPS_PASSWORD_FILE', 'SSH_PROFILES_FILE'}


def load_site(root=ROOT):
    path = root / '.runtime/site.json'
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or any(k not in KEYS or not isinstance(v, str) or '\n' in v or '\x00' in v for k, v in value.items()):
        raise RuntimeError('站点配置格式无效，请核对 .runtime/site.json')
    return value


def apply_site(root=ROOT):
    try:
        from dotenv import load_dotenv
        load_dotenv(root/'backend/.env')
    except ImportError:
        pass  # stdlib-only bootstrap runs before application dependencies exist
    for key, value in load_site(root).items():
        os.environ.setdefault(key, value)
