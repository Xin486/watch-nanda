"""Read-only diagnostics of accepted Agent reports, using shared freshness rules."""
from datetime import datetime

from core.config import settings
from core.status import collection_time, freshness


def agent_health(server, credential_configured, now=None):
    now = now or datetime.utcnow()
    data = server.latest_status_data or {}
    received = collection_time({'timestamp': data.get('received_at')})
    sampled = collection_time(data)
    status, _ = freshness(server, now)
    if not credential_configured:
        status = 'unconfigured'
    elif not received:
        status = 'pending'
    messages = {
        'online': '检查时上报正常，平台近期已接收到通过认证的 Agent 数据。',
        'stale': '上报已延迟，请在节点检查 Agent 服务、日志和到上报地址的网络。',
        'offline': '当前未收到可用的新上报，请在节点检查 Agent 服务和 HTTPS 连接。',
        'pending': '正在等待新的 Agent 上报；服务正常时通常每 5 分钟上报一次。',
        'unconfigured': '平台未配置有效的 Agent 上报凭据，请先完成 Agent 部署或重新配置。',
    }
    return {
        'server_id': server.id,
        'status': status,
        'available': status == 'online',
        'credential_configured': bool(credential_configured),
        'message': messages.get(status, '当前上报不可用，请检查节点 Agent 服务。'),
        'checked_at': now.isoformat() + 'Z',
        'last_received_at': received.isoformat() + 'Z' if received else None,
        'last_sampled_at': sampled.isoformat() + 'Z' if sampled else None,
        'report_age_seconds': max(0, (now - received).total_seconds()) if received else None,
        'stale_after_seconds': settings.AGENT_STALE_SECONDS,
        'offline_after_seconds': settings.AGENT_OFFLINE_SECONDS,
    }
