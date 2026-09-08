"""
Node Monitor 后端 API 入口
============================
职责：
1. 提供服务器节点管理、历史负载查询、告警/邮件配置等 REST 接口
2. 启动时自动拉起 APScheduler 后台采集调度器（采集逻辑见 worker/tasks.py）

启动方式（在 backend 目录下执行）：
    uvicorn main:app --host 0.0.0.0 --port 7980
"""
import json
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import asyncio

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import SessionLocal, get_db
from models.models import AlertConfig, EmailConfig, Server, ServerStats
from worker.tasks import send_email_wrapper, start_scheduler, monitor_once, collect_server_data

# ---------------------------------------------------------------------------
# 应用初始化
# ---------------------------------------------------------------------------
app = FastAPI(title="Node Monitor API")

# 允许前端 (Vite 3000 端口) 跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    """FastAPI 启动时立即执行一次全量采集，再拉起后台调度器"""
    monitor_once()       # 阻塞采集，确保首次打开页面就有数据
    start_scheduler()    # 拉起后台定时任务


# ---------------------------------------------------------------------------
# 请求体模型 (Pydantic)
# ---------------------------------------------------------------------------
class ServerCreate(BaseModel):
    """新增服务器节点"""
    hostname: str
    ip_address: str
    group_name: str
    ssh_user: str
    ssh_port: int


class ServerUpdate(BaseModel):
    """更新节点基本信息"""
    hostname: str
    ip_address: str
    group_name: str
    ssh_user: str
    ssh_port: int


class AlertUpdate(BaseModel):
    """告警规则配置"""
    enable_email_alert: bool
    offline_threshold_minutes: int
    alert_recipient: Optional[str] = ""


class EmailUpdate(BaseModel):
    """SMTP 发件配置"""
    smtp_server: Optional[str] = ""
    smtp_port: Optional[int] = 465
    smtp_user: Optional[str] = ""
    smtp_password: Optional[str] = ""
    from_name: Optional[str] = "Node Monitor"
    from_address: Optional[str] = ""
    use_tls: Optional[bool] = True


# ---------------------------------------------------------------------------
# 服务器节点管理
# ---------------------------------------------------------------------------
@app.get("/api/servers")
def get_servers(group: str = None, db: Session = Depends(get_db)):
    """获取服务器列表及实时缓存状态（纯 MySQL 实现，无 Redis）

    实时数据存放在 servers.latest_status_data (JSON 字段)，
    由后台采集任务每分钟刷新一次。
    """
    query = db.query(Server).filter(Server.is_active == True)  # noqa: E712
    if group:
        query = query.filter(Server.group_name == group)
    servers = query.all()

    result = []
    for server in servers:
        data = server.latest_status_data or {}

        # 兜底默认值：尚未完成首次采集的节点
        if not data:
            data = {
                "status": server.status or "unknown",
                "uptime_seconds": 0,
                "cpu_percent": 0,
                "cpu_cores": 0,
                "ram_percent": 0,
                "ram_used_mb": 0,
                "ram_total_mb": 0,
                "gpu_data": [],
                "top_process": "暂无",
                "timestamp": server.last_online.isoformat() + "Z" if server.last_online else None,
            }
        elif "top_process" not in data:
            data["top_process"] = "暂无"

        result.append({
            "id": server.id,
            "hostname": server.hostname,
            "ip_address": server.ip_address,
            "group_name": server.group_name,
            "status": server.status,
            # 前端编辑节点时需要回显 SSH 凭据
            "ssh_user": server.ssh_user,
            "ssh_port": server.ssh_port,
            **data,
        })
    return result


@app.post("/api/servers/add")
def add_server(data: ServerCreate, db: Session = Depends(get_db)):
    """新增一台服务器（默认离线，等待后台调度器首次采集）"""
    new_server = Server(
        hostname=data.hostname,
        ip_address=data.ip_address,
        group_name=data.group_name,
        ssh_user=data.ssh_user,
        ssh_port=data.ssh_port,
        is_active=True,
        status="offline",
    )
    db.add(new_server)
    db.commit()
    return {"success": True, "message": "服务器添加成功"}


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


@app.delete("/api/server/{server_id}")
def delete_server_node(server_id: int, db: Session = Depends(get_db)):
    """彻底删除一台服务器节点及其所有历史数据"""
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        return {"success": False, "message": "找不到该服务器"}

    # 先删除名下的历史数据，防止触发外键约束错误
    db.query(ServerStats).filter(ServerStats.server_id == server_id).delete()
    db.delete(server)
    db.commit()
    return {"success": True, "message": "服务器已彻底删除"}


# ---------------------------------------------------------------------------
# SSE 实时数据推送（替代前端 60 秒轮询）
# ---------------------------------------------------------------------------
@app.get("/api/stream/servers")
async def stream_servers():
    """Server-Sent Events 端点：每 10 秒推送一次最新数据（只读 DB 缓存，不触发 SSH 采集）"""

    def _build_payload(db):
        """读取所有活跃节点，拼装与 /api/servers 相同结构的 JSON"""
        servers = db.query(Server).filter(Server.is_active == True).all()  # noqa: E712
        result = []
        for server in servers:
            data = server.latest_status_data or {}
            if not data:
                data = {
                    "status": server.status or "unknown",
                    "uptime_seconds": 0, "cpu_percent": 0, "cpu_cores": 0,
                    "ram_percent": 0, "ram_used_mb": 0, "ram_total_mb": 0,
                    "gpu_data": [], "top_process": "暂无",
                    "timestamp": server.last_online.isoformat() + "Z" if server.last_online else None,
                }
            elif "top_process" not in data:
                data["top_process"] = "暂无"
            result.append({
                "id": server.id,
                "hostname": server.hostname,
                "ip_address": server.ip_address,
                "group_name": server.group_name,
                "status": server.status,
                "ssh_user": server.ssh_user,
                "ssh_port": server.ssh_port,
                **data,
            })
        return result

    async def event_generator():
        while True:
            db = SessionLocal()
            try:
                payload = _build_payload(db)
            finally:
                db.close()
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(10)  # 每 10 秒推送一次（只读 DB，不触发 SSH）

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 历史负载数据
# ---------------------------------------------------------------------------
@app.get("/api/server/{server_id}/history")
def get_server_history(server_id: int, days: int = 1, db: Session = Depends(get_db)):
    """获取历史负载数据（≤7 天返回明细，>7 天按天聚合降采样，防止图表卡死）"""
    cutoff_time = datetime.utcnow() - timedelta(days=days)
    stats = db.query(ServerStats).filter(
        ServerStats.server_id == server_id,
        ServerStats.timestamp >= cutoff_time,
    ).order_by(ServerStats.timestamp.asc()).all()

    data_points = []

    if days <= 7:
        # 一周以内：返回原始明细数据
        for s in stats:
            data_points.append({
                "time": s.timestamp.isoformat() + "Z",
                "cpu": s.cpu_percent,
                "ram": s.ram_percent,
                "gpu": _avg_gpu_percent(s.gpu_data),
            })
    else:
        # 超过一周：按天分组求平均值，减少前端渲染压力
        grouped = defaultdict(list)
        for s in stats:
            day_str = s.timestamp.strftime("%Y-%m-%d")
            grouped[day_str].append((s.cpu_percent or 0, s.ram_percent or 0, _avg_gpu_percent(s.gpu_data)))

        for day_str, values in grouped.items():
            data_points.append({
                "time": day_str + "T00:00:00Z",
                "cpu": round(sum(v[0] for v in values) / len(values), 1),
                "ram": round(sum(v[1] for v in values) / len(values), 1),
                "gpu": round(sum(v[2] for v in values) / len(values), 1),
            })

    return {"server_id": server_id, "data": data_points}


def _avg_gpu_percent(gpu_data):
    """计算一组 GPU 的平均显存占用百分比（供历史数据聚合使用）"""
    if not gpu_data or not isinstance(gpu_data, list) or len(gpu_data) == 0:
        return 0
    try:
        return round(sum(g.get("memory_percent", 0) for g in gpu_data) / len(gpu_data), 1)
    except Exception:
        return 0


@app.delete("/api/server/{server_id}/history")
def delete_server_history(server_id: int, db: Session = Depends(get_db)):
    """仅清空单台机器的所有历史监控数据（保留节点本身）"""
    db.query(ServerStats).filter(ServerStats.server_id == server_id).delete()
    db.commit()
    return {"success": True, "message": "历史数据清理成功"}


# ---------------------------------------------------------------------------
# 告警与邮件设置
# ---------------------------------------------------------------------------
@app.get("/api/settings/alert")
def get_alert_settings(db: Session = Depends(get_db)):
    """读取告警规则配置"""
    config = db.query(AlertConfig).first()
    if not config:
        return {"enable_email_alert": False, "offline_threshold_minutes": 5, "alert_recipient": ""}
    return {
        "enable_email_alert": config.enable_email_alert,
        "offline_threshold_minutes": config.offline_threshold_minutes,
        "alert_recipient": config.alert_recipient or "",
    }


@app.post("/api/settings/alert")
def update_alert_settings(data: AlertUpdate, db: Session = Depends(get_db)):
    """保存告警规则配置"""
    config = db.query(AlertConfig).first()
    if not config:
        config = AlertConfig()
        db.add(config)
    config.enable_email_alert = data.enable_email_alert
    config.offline_threshold_minutes = data.offline_threshold_minutes
    config.alert_recipient = data.alert_recipient
    db.commit()
    return {"success": True}


@app.get("/api/settings/email")
def get_email_settings(db: Session = Depends(get_db)):
    """读取 SMTP 发件配置"""
    config = db.query(EmailConfig).first()
    if not config:
        return {}
    return {
        "smtp_server": config.smtp_server,
        "smtp_port": config.smtp_port,
        "smtp_user": config.smtp_user,
        "smtp_password": config.smtp_password,
        "from_name": config.from_name,
        "from_address": config.from_address,
        "use_tls": config.use_tls,
    }


@app.post("/api/settings/email")
def update_email_settings(data: EmailUpdate, db: Session = Depends(get_db)):
    """保存 SMTP 发件配置"""
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


@app.post("/api/settings/email/test")
def test_email(db: Session = Depends(get_db)):
    """发送一封测试邮件，验证 SMTP 配置是否可用"""
    try:
        success, msg = send_email_wrapper(
            db,
            subject="【Node Monitor】系统邮件测试",
            content="这是一封来自 Node Monitor 的测试邮件，说明您的 SMTP 服务器配置完全正确！",
            is_test=True,
        )
        return {"success": success, "message": msg}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# 行情数据代理（大屏 K 线）
# ---------------------------------------------------------------------------
# 内存缓存：避免前端每 60 秒轮询时频繁请求上游接口 {cache_key: (过期时间戳, 数据)}
_kline_cache = {}


@app.get("/api/market/kline")
def get_market_kline(symbol: str = "90.BK1036", klt: int = 101, lmt: int = 120):
    """代理东方财富 K 线接口（后端转发以避开浏览器跨域限制），带 60 秒内存缓存

    参数说明：
    - symbol: 东方财富 secid。默认 90.BK1036 = 「半导体」行业板块指数；
              个股示例：1.688981（沪市·中芯国际）、0.002371（深市·北方华创）
    - klt:    K 线周期。101=日K、102=周K、103=月K；1/5/15/30/60=分钟K
    - lmt:    返回条数（默认 120 根）
    """
    cache_key = f"{symbol}:{klt}:{lmt}"
    cached = _kline_cache.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]

    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={symbol}&klt={klt}&fqt=1&lmt={lmt}&end=20500101"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        stock = (raw or {}).get("data") or {}

        # 每条 K 线格式：日期,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
        klines = []
        for line in stock.get("klines", []):
            p = line.split(",")
            if len(p) < 6:
                continue
            klines.append({
                "time": p[0],
                "open": float(p[1]),
                "close": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "volume": float(p[5]),
            })

        if not klines:
            return {"name": "", "code": symbol, "klines": [], "latest": None}

        last = klines[-1]
        prev = klines[-2]["close"] if len(klines) > 1 else last["open"]
        payload = {
            "name": stock.get("name") or "行情",
            "code": stock.get("code") or symbol,
            "klines": klines,
            "latest": {
                "price": last["close"],
                "pct": round((last["close"] - prev) / prev * 100, 2) if prev else 0,
            },
        }
        _kline_cache[cache_key] = (time.time() + 60, payload)
        return payload
    except Exception as e:
        return {"error": str(e), "name": "", "code": symbol, "klines": [], "latest": None}


# ---------------------------------------------------------------------------
# 本地调试入口（生产环境请使用 uvicorn 命令启动）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7980)
