from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, Float
from sqlalchemy.sql import func
from models.database import Base

class Server(Base):
    __tablename__ = 'servers'

    id = Column(Integer, primary_key=True, index=True)
    hostname = Column(String(100), nullable=False, index=True)
    ip_address = Column(String(50), nullable=True)
    ssh_port = Column(Integer, default=22)
    ssh_user = Column(String(50), default='root')
    group_name = Column(String(50), default='未分组')
    is_active = Column(Boolean, default=True)
    
    # --- 替代 Redis 的核心字段 ---
    status = Column(String(20), default='offline')
    last_online = Column(DateTime, nullable=True)
    offline_since = Column(DateTime, nullable=True)
    
    # 实时监控缓存数据 (JSON)
    latest_status_data = Column(JSON, nullable=True)
    
    # 任务防并发锁 (时间戳)
    locked_until = Column(DateTime, nullable=True)
    last_alert_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())

class ServerStats(Base):
    __tablename__ = 'server_stats'

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, default=func.now(), index=True)
    status = Column(String(20), default='offline')
    uptime_seconds = Column(Integer, default=0)
    cpu_percent = Column(Float, default=0.0)
    cpu_cores = Column(Integer, default=1)
    ram_total_mb = Column(Integer, default=0)
    ram_used_mb = Column(Integer, default=0)
    ram_percent = Column(Float, default=0.0)
    
    # 存放解析好的 GPU 数组 (型号、温度、显存、进程)
    gpu_data = Column(JSON, nullable=True)
# ... 你原本的 Server 和 ServerStats 类代码 ...

class EmailConfig(Base):
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
    __tablename__ = "alert_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    enable_email_alert = Column(Boolean, default=False)
    offline_threshold_minutes = Column(Integer, default=5)
    alert_recipient = Column(String(255))