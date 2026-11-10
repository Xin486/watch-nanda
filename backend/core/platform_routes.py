"""Site settings UI. Only non-secret settings are returned to browsers."""
import ipaddress
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from core import platform_config as config
from core.operations import require_operator

public = APIRouter()
router = APIRouter(prefix='/api/ops/platform', dependencies=[Depends(require_operator)])


class Route(BaseModel):
    network: str = Field(max_length=64)
    url: str = Field(max_length=2048)


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    report_url: str = Field(max_length=2048)
    routes: list[Route] = Field(default_factory=list, max_length=20)
    ca_mode: str = Field(pattern='^(site|system|custom)$')
    ca_certificate: str | None = Field(default=None, max_length=65536)


class HermesSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool
    api_base: str = Field(max_length=2048)
    model: str = Field(max_length=200)
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False


class Branding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    company_name: str = Field(max_length=80)
    page_title: str = Field(max_length=100)


def notify_bridge(request):
    if not config.public_hermes()['runtime_ready']:
        return
    import http.client, os
    connection = http.client.HTTPConnection('127.0.0.1', int(os.getenv('HERMES_BRIDGE_PORT', '7981')), timeout=60)
    try:
        connection.request('POST', '/api/ops/hermes/reload', headers={
            'Host': request.headers.get('host', ''), 'X-Forwarded-Proto': 'https',
            'Cookie': request.headers.get('cookie', '')})
        response = connection.getresponse()
        response.read(8192)
        if response.status != 200:
            raise OSError('bridge cleanup not confirmed')
    except (OSError, http.client.HTTPException):
        raise HTTPException(503, '助手设置已保存，但任务停止尚未确认，请检查助手运行服务后重试') from None
    finally:
        connection.close()


@public.get('/api/platform')
def get_public():
    try:
        hermes = config.public_hermes()
        return {'branding': config.branding(), 'hermes': {k: hermes[k] for k in ('enabled', 'model')}}
    except (OSError, ValueError):
        raise HTTPException(503, '站点配置暂不可用') from None


@router.get('/agent')
def get_agent():
    return config.agent_settings()


@router.get('/agent/report')
def get_report(host: str = ''):
    try:
        return {'url': config.report_url(host)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.put('/agent')
def save_agent(data: AgentSettings):
    try:
        with config.lock:
            routes = [{'network': str(ipaddress.ip_network(r.network, strict=False)),
                       'url': config.https_url(r.url, report=True)} for r in data.routes]
            networks = [r['network'] for r in routes]
            if len(networks) != len(set(networks)):
                raise ValueError('网络范围不能重复')
            url = config.https_url(data.report_url, report=True)
            previous = config.agent_settings()
            ca_digest = previous.get('ca_digest', '')
            if data.ca_certificate:
                from cryptography import x509
                if 'PRIVATE KEY' in data.ca_certificate:
                    raise ValueError('请上传 CA 公钥证书，不要上传私钥')
                if not x509.load_pem_x509_certificates(data.ca_certificate.encode()):
                    raise ValueError('请上传有效的 CA 公钥证书')
                import hashlib
                ca_digest = hashlib.sha256(data.ca_certificate.encode()).hexdigest()
                path = config.platform_path().with_name('agent-ca-' + ca_digest + '.crt')
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                import os, tempfile
                fd, temporary = tempfile.mkstemp(dir=path.parent)
                try:
                    with os.fdopen(fd, 'w') as stream:
                        stream.write(data.ca_certificate)
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary): os.unlink(temporary)
            has_ca = bool(ca_digest and config.platform_path().with_name('agent-ca-' + ca_digest + '.crt').is_file())
            if data.ca_mode == 'custom' and not has_ca:
                raise ValueError('请选择或上传自定义 CA 证书')
            saved = config.read_json(config.platform_path())
            saved['agent'] = {'report_url': url, 'routes': routes, 'ca_mode': data.ca_mode, 'has_custom_ca': has_ca, 'ca_digest': ca_digest}
            config.write_json(config.platform_path(), saved)
        return get_agent()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except OSError:
        raise HTTPException(503, '配置未能保存，请检查配置目录权限') from None


@router.get('/hermes')
def get_hermes():
    return config.public_hermes()


@router.put('/hermes')
def save_hermes(data: HermesSettings, request: Request):
    try:
        with config.lock:
            previous = config.hermes_settings()
            base = config.https_url(data.api_base) if data.api_base.strip() else ''
            key = '' if data.clear_api_key or base != previous['api_base'] else previous['api_key']
            if data.api_key and data.api_key.get_secret_value():
                key = data.api_key.get_secret_value()
            model = data.model.strip()
            if any(c in model + key for c in '\r\n\x00'):
                raise ValueError('模型或 API Key 格式无效')
            if data.enabled:
                if not base or not model or not key:
                    raise ValueError('开启助手前请填写 API 地址、模型和 API Key')
                if not config.public_hermes()['runtime_ready']:
                    raise ValueError('助手运行环境未安装，请先运行 setup.sh --with-hermes')
            value = {'enabled': data.enabled, 'api_base': base, 'model': model, 'api_key': key}
            changed = any(value[k] != previous[k] for k in value)
            if changed:
                config.write_json(config.hermes_path(), value | {'revision': previous['revision'] + 1})
        notify_bridge(request)
        return get_hermes()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    except OSError:
        raise HTTPException(503, '助手配置未能保存，请检查配置目录权限') from None


@router.put('/branding')
def save_branding(data: Branding):
    with config.lock:
        saved = config.read_json(config.platform_path())
        saved['branding'] = data.model_dump()
        config.write_json(config.platform_path(), saved)
    return config.branding()
