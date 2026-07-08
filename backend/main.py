from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from models.database import engine, Base, get_db
from pydantic import BaseModel
from typing import Optional
from collections import defaultdict
from pydantic import BaseModel
from models.models import Server
from worker.tasks import start_scheduler
from models.models import EmailConfig, AlertConfig # 确保导入了这两个模型
import uvicorn

# 初始化 FastAPI
app = FastAPI(title="Node Monitor API")

# 启用 CORS 跨域支持
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 核心：在 FastAPI 启动时，拉起后台的采集调度器！
@app.on_event("startup")
def startup_event():
    start_scheduler()

@app.get("/api/servers")
def get_servers(group: str = None, db: Session = Depends(get_db)):
    """获取服务器列表及实时缓存状态 (纯 MySQL 实现，无 Redis)"""
    query = db.query(Server).filter(Server.is_active == True)
    if group:
        query = query.filter(Server.group_name == group)
    servers = query.all()
    
    result = []
    for server in servers:
        # 直接使用 MySQL JSON 字段里的缓存数据
        data = server.latest_status_data or {}
        
        # 兜底默认值
        if not data:
            data = {
                'status': server.status or 'unknown',
                'uptime_seconds': 0, 'cpu_percent': 0, 'cpu_cores': 0,
                'ram_percent': 0, 'ram_used_mb': 0, 'ram_total_mb': 0,
                'gpu_data': [], 'top_process': '暂无',
                'timestamp': server.last_online.isoformat() + 'Z' if server.last_online else None
            }
        elif 'top_process' not in data:
            data['top_process'] = '暂无'
            
        result.append({
            "id": server.id,
            "hostname": server.hostname,
            "ip_address": server.ip_address,
            "group_name": server.group_name,
            "status": server.status,
            **data
        })
    return result

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=7980)
# --- 增加一个 Pydantic 数据模型用来接收前端传来的表单 ---
class ServerUpdate(BaseModel):
    hostname: str
    ip_address: str
    group_name: str
    ssh_user: str
    ssh_port: int

@app.post("/api/server/{server_id}/update")
def update_server(server_id: int, data: ServerUpdate, db: Session = Depends(get_db)):
    """更新节点基本信息"""
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        return {"error": "Server not found"}
        
    server.hostname = data.hostname
    server.ip_address = data.ip_address
    server.group_name = data.group_name
    server.ssh_user = data.ssh_user
    server.ssh_port = data.ssh_port
    db.commit()
    return {"success": True}

from models.models import ServerStats # 别忘了从 models 导入历史表

@app.get("/api/server/{server_id}/stats_count")
def get_stats_count(server_id: int, db: Session = Depends(get_db)):
    """查询某个节点的历史数据总量"""
    count = db.query(ServerStats).filter(ServerStats.server_id == server_id).count()
    return {"count": count}

@app.post("/api/server/{server_id}/clear_stats")
def clear_server_stats(server_id: int, db: Session = Depends(get_db)):
    """清空某个节点的所有历史数据"""
    db.query(ServerStats).filter(ServerStats.server_id == server_id).delete()
    db.commit()
    return {"success": True}
# --- 增加服务器接收数据的 Pydantic 模型 ---
class ServerCreate(BaseModel):
    hostname: str
    ip_address: str
    group_name: str
    ssh_user: str
    ssh_port: int

@app.post("/api/servers/add")
def add_server(data: ServerCreate, db: Session = Depends(get_db)):
    """新增一台服务器"""
    new_server = Server(
        hostname=data.hostname,
        ip_address=data.ip_address,
        group_name=data.group_name,
        ssh_user=data.ssh_user,
        ssh_port=data.ssh_port,
        is_active=True,
        status="offline" # 刚添加时默认为离线，等待后台调度器抓取
    )
    db.add(new_server)
    db.commit()
    return {"success": True, "message": "服务器添加成功"}


from datetime import datetime, timedelta
from models.models import ServerStats

@app.get("/api/server/{server_id}/history")
@app.get("/api/server/{server_id}/history")
def get_server_history(server_id: int, days: int = 1, db: Session = Depends(get_db)):
    """获取历史负载数据（支持按天跨度并自动降采样聚合）"""
    cutoff_time = datetime.utcnow() - timedelta(days=days)
    stats = db.query(ServerStats).filter(
        ServerStats.server_id == server_id,
        ServerStats.timestamp >= cutoff_time
    ).order_by(ServerStats.timestamp.asc()).all()
    
    data_points = []
    
    if days <= 7:
        # 1周以内：返回原始明细数据
        for s in stats:
            gpu_avg = 0
            if s.gpu_data and isinstance(s.gpu_data, list) and len(s.gpu_data) > 0:
                try: gpu_avg = sum([g.get('memory_percent', 0) for g in s.gpu_data]) / len(s.gpu_data)
                except: pass
            data_points.append({
                "time": s.timestamp.isoformat() + "Z",
                "cpu": s.cpu_percent,
                "ram": s.ram_percent,
                "gpu": round(gpu_avg, 1)
            })
    else:
        # 大于1周：按天分组求平均值，防止数据量过大卡死前端图表
        grouped = defaultdict(list)
        for s in stats:
            day_str = s.timestamp.strftime('%Y-%m-%d')
            gpu_avg = 0
            if s.gpu_data and isinstance(s.gpu_data, list) and len(s.gpu_data) > 0:
                try: gpu_avg = sum([g.get('memory_percent', 0) for g in s.gpu_data]) / len(s.gpu_data)
                except: pass
            grouped[day_str].append((s.cpu_percent or 0, s.ram_percent or 0, gpu_avg))
            
        for day_str, values in grouped.items():
            avg_cpu = sum(v[0] for v in values) / len(values)
            avg_ram = sum(v[1] for v in values) / len(values)
            avg_gpu = sum(v[2] for v in values) / len(values)
            data_points.append({
                "time": day_str + "T00:00:00Z", # 格式化时间确保前端统一解析
                "cpu": round(avg_cpu, 1),
                "ram": round(avg_ram, 1),
                "gpu": round(avg_gpu, 1)
            })
            
    return {"server_id": server_id, "data": data_points}
# ----- Pydantic 接收模型 (放宽限制，允许空值) -----
class AlertUpdate(BaseModel):
    enable_email_alert: bool
    offline_threshold_minutes: int
    alert_recipient: Optional[str] = ""

class EmailUpdate(BaseModel):
    smtp_server: Optional[str] = ""
    smtp_port: Optional[int] = 465
    smtp_user: Optional[str] = ""
    smtp_password: Optional[str] = ""
    from_name: Optional[str] = "Node Monitor"
    from_address: Optional[str] = ""
    use_tls: Optional[bool] = True

# ----- 告警设置 API -----
@app.get("/api/settings/alert")
def get_alert_settings(db: Session = Depends(get_db)):
    config = db.query(AlertConfig).first()
    if not config:
        return {"enable_email_alert": False, "offline_threshold_minutes": 5, "alert_recipient": ""}
    return {
        "enable_email_alert": config.enable_email_alert,
        "offline_threshold_minutes": config.offline_threshold_minutes,
        "alert_recipient": config.alert_recipient or ""
    }

@app.post("/api/settings/alert")
def update_alert_settings(data: AlertUpdate, db: Session = Depends(get_db)):
    config = db.query(AlertConfig).first()
    if not config:
        config = AlertConfig()
        db.add(config)
    config.enable_email_alert = data.enable_email_alert
    config.offline_threshold_minutes = data.offline_threshold_minutes
    config.alert_recipient = data.alert_recipient
    db.commit()
    return {"success": True}

# ----- 邮件设置 API -----
@app.get("/api/settings/email")
def get_email_settings(db: Session = Depends(get_db)):
    config = db.query(EmailConfig).first()
    if not config:
        return {}
    return {
        "smtp_server": config.smtp_server, "smtp_port": config.smtp_port,
        "smtp_user": config.smtp_user, "smtp_password": config.smtp_password,
        "from_name": config.from_name, "from_address": config.from_address,
        "use_tls": config.use_tls
    }

@app.post("/api/settings/email")
def update_email_settings(data: EmailUpdate, db: Session = Depends(get_db)):
    config = db.query(EmailConfig).first()
    if not config:
        config = EmailConfig()
        db.add(config)
    config.smtp_server = data.smtp_server
    config.smtp_port = data.smtp_port
    config.smtp_user = data.smtp_user
    config.smtp_password = data.smtp_password
    config.from_name = data.from_name
    config.from_address = data.from_address
    config.use_tls = data.use_tls
    db.commit()
    return {"success": True}

# ----- 发送测试邮件 API -----
from worker.tasks import send_email_wrapper # 等下会在 tasks.py 里写这个封装
@app.post("/api/settings/email/test")
def test_email(db: Session = Depends(get_db)):
    try:
        success, msg = send_email_wrapper(
            db, 
            subject="【Node Monitor】系统邮件测试", 
            content="这是一封来自 Node Monitor 的测试邮件，说明您的 SMTP 服务器配置完全正确！", 
            is_test=True
        )
        return {"success": success, "message": msg}
    except Exception as e:
        return {"success": False, "message": str(e)}