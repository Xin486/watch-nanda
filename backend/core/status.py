"""Shared freshness rules for REST, SSE, alerts and history snapshots."""
from datetime import datetime

from core.config import settings


def collection_time(data):
    try:
        return datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def freshness(server, now=None):
    now = now or datetime.utcnow()
    checked_at = collection_time(server.latest_status_data or {})
    age = max(0, (now - checked_at).total_seconds()) if checked_at else None
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
        "data_age_seconds": age, "stale_after_seconds": settings.STALE_AFTER_SECONDS,
        "last_online": server.last_online.isoformat() + "Z" if server.last_online else None,
    }
