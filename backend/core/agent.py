"""Strict, metrics-only ingestion. No command or configuration delivery channel."""
import hashlib
import hmac
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.status import collection_time
from models.models import AgentCredential, Server


class MetricModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class GPU(MetricModel):
    uuid: str = Field(max_length=100)
    model: str = Field(max_length=150)
    temp: str = Field(default='N/A', max_length=30)
    memory_used: str = Field(max_length=40)
    memory_total: str = Field(max_length=40)
    memory_percent: float = Field(ge=0, le=100)
    gpu_util_percent: float | None = Field(default=None, ge=0, le=100)
    power_draw: float | None = Field(default=None, ge=0, le=10000)
    power_limit: float | None = Field(default=None, ge=0, le=10000)


class Process(MetricModel):
    pid: int = Field(gt=0, le=2**31-1)
    user: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=150)
    cpu_percent: float = Field(ge=0, le=10**7)
    memory_percent: float = Field(ge=0, le=100)


class Report(MetricModel):
    server_id: int = Field(gt=0)
    report_id: UUID
    sampled_at: datetime
    uptime_seconds: int = Field(ge=0, le=10**12)
    cpu_percent: float = Field(ge=0, le=100)
    cpu_cores: int = Field(ge=1, le=100000)
    ram_total_mb: int = Field(ge=0, le=10**12)
    ram_used_mb: int = Field(ge=0, le=10**12)
    ram_percent: float = Field(ge=0, le=100)
    gpu_data: list[GPU] = Field(default_factory=list, max_length=64)
    gpu_error: str | None = Field(default=None, max_length=200)
    top_process_detail: Process | None = None

    @field_validator('sampled_at')
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError('sampled_at requires a timezone')
        return value.astimezone(timezone.utc).replace(tzinfo=None)


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def accept_report(db, report, token, now=None):
    now = now or datetime.utcnow()
    # Lock in the same order as the SSH write path and history flush.
    server = db.query(Server).filter(Server.id == report.server_id).with_for_update().first()
    credential = db.get(AgentCredential, report.server_id)
    if (not server or not server.is_active or server.collection_mode != 'agent'
            or not credential or not hmac.compare_digest(credential.token_hash, token_digest(token))):
        raise HTTPException(401, 'Invalid node credentials')
    old = server.latest_status_data or {}
    sampled = collection_time(old)
    if old.get('report_id') == str(report.report_id):
        return {'accepted': False, 'reason': 'duplicate'}
    if sampled and report.sampled_at <= sampled:
        return {'accepted': False, 'reason': 'out_of_order'}
    age = (now - report.sampled_at).total_seconds()
    if age < -60 or age > 600:
        raise HTTPException(422, 'Sample clock is outside the allowed window')
    received = collection_time({'timestamp': old.get('received_at')})
    if received and (now - received).total_seconds() < 30:
        raise HTTPException(429, 'Report interval must be at least 30 seconds')
    if report.ram_used_mb > report.ram_total_mb:
        raise HTTPException(422, 'Used RAM exceeds total RAM')
    data = report.model_dump(mode='json', exclude={'server_id', 'sampled_at'})
    process = report.top_process_detail
    label = (f'{process.user} 运行 {process.name} (PID:{process.pid} CPU:{process.cpu_percent:g}% 内存:{process.memory_percent:g}%)'
             if process else '未采集（请更新 Agent 或检查进程访问权限）')
    data.update(timestamp=report.sampled_at.isoformat()+'Z', received_at=now.isoformat()+'Z', top_process=label)
    server.latest_status_data = data
    server.status = 'online'
    server.last_online = now
    server.offline_since = None
    # Keep last_alert_time until the alert scheduler sends recovery notification.
    db.commit()
    return {'accepted': True}
