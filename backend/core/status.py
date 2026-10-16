"""Shared freshness rules for REST, SSE, alerts and history snapshots."""
from datetime import datetime, timezone

from core.config import settings


def collection_time(data):
    try:
        stamp = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp.tzinfo else stamp
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def freshness(server, now=None):
    now = now or datetime.utcnow()
    data = server.latest_status_data or {}
    agent = getattr(server, 'collection_mode', 'ssh') == 'agent'
    checked_at = collection_time({'timestamp': data.get('received_at')}) if agent else collection_time(data)
    age = max(0, (now - checked_at).total_seconds()) if checked_at else None
    if agent:
        return ('pending' if age is None else 'offline' if age > settings.AGENT_OFFLINE_SECONDS
                else 'stale' if age > settings.AGENT_STALE_SECONDS else server.status), age
    status = ("pending" if age is None else
              "stale" if age > settings.STALE_AFTER_SECONDS else server.status)
    return status, age


def server_payload(server, now=None):
    status, age = freshness(server, now)
    data = {
        "uptime_seconds": 0, "cpu_percent": 0, "cpu_cores": 0,
        "ram_percent": 0, "ram_used_mb": 0, "ram_total_mb": 0,
        "gpu_data": [], "top_process": "暂无", "timestamp": None,
        **(server.latest_status_data or {}),
    }
    return {
        **data, "id": server.id, "hostname": server.hostname,
        "ip_address": server.ip_address, "group_name": server.group_name,
        "ssh_user": server.ssh_user, "ssh_port": server.ssh_port,
        "status": status, "collection_status": server.status,
        "collection_mode": getattr(server, 'collection_mode', 'ssh'),
        "data_age_seconds": age,
        "stale_after_seconds": settings.AGENT_STALE_SECONDS if getattr(server, 'collection_mode', 'ssh') == 'agent' else settings.STALE_AFTER_SECONDS,
        "offline_after_seconds": settings.AGENT_OFFLINE_SECONDS if getattr(server, 'collection_mode', 'ssh') == 'agent' else None,
        "last_online": server.last_online.isoformat() + "Z" if server.last_online else None,
    }
