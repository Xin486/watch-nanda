"""ORM 模型定义（对应 monitor_db.sql 中的 4 张表）"""
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.sql import func

from models.database import Base


class Server(Base):
    """被监控服务器节点"""
    __tablename__ = "servers"

    # ---- 基本信息 ----
    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String(100), nullable=False, index=True)
    ip_address = Column(String(50), nullable=True)
    ssh_port = Column(Integer, default=22)
    ssh_user = Column(String(50), default="root")
    group_name = Column(String(50), default="未分组")
    is_active = Column(Boolean, default=True)

    # ---- 在线状态（替代 Redis 的核心字段）----
    status = Column(String(20), default="offline")          # online / offline
    last_online = Column(DateTime, nullable=True)           # 最近一次采集成功时间
    offline_since = Column(DateTime, nullable=True)         # 首次离线时间（告警计算用）

    # 实时监控缓存数据：每次采集成功后整体覆盖（JSON）
    latest_status_data = Column(JSON, nullable=True)

    # ---- 任务辅助字段 ----
    locked_until = Column(DateTime, nullable=True)          # 预留：任务防并发锁（时间戳）
    last_alert_time = Column(DateTime, nullable=True)       # 上次发送告警邮件的时间
    created_at = Column(DateTime, default=func.now())


class ServerStats(Base):
    """历史负载快照（每 10 分钟一条，由 flush_stats_to_db 落库）"""
    __tablename__ = "server_stats"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, default=func.now(), index=True)
    status = Column(String(20), default="offline")
    uptime_seconds = Column(Integer, default=0)
    cpu_percent = Column(Float, default=0.0)
    cpu_cores = Column(Integer, default=1)
    ram_total_mb = Column(Integer, default=0)
    ram_used_mb = Column(Integer, default=0)
    ram_percent = Column(Float, default=0.0)

    # 解析好的 GPU 数组（型号、温度、显存）
    gpu_data = Column(JSON, nullable=True)


class EmailConfig(Base):
    """SMTP 发件配置"""
    __tablename__ = "email_configs"

    id = Column(Integer, primary_key=True, index=True)
    smtp_server = Column(String(255))
    smtp_port = Column(Integer, default=465)
    smtp_user = Column(String(255))
    smtp_password = Column(String(255))
    from_name = Column(String(255), default="Node Monitor")
    from_address = Column(String(255))
    use_tls = Column(Boolean, default=True)


class AlertConfig(Base):
    """告警规则配置"""
    __tablename__ = "alert_configs"

    id = Column(Integer, primary_key=True, index=True)
    enable_email_alert = Column(Boolean, default=False)
    offline_threshold_minutes = Column(Integer, default=5)
    alert_recipient = Column(String(255))
