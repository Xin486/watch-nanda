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
from datetime import datetime
from typing import Optional, Literal

import asyncio

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError, SecretStr
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from models.database import SessionLocal, get_db
from models.models import AlertConfig, EmailConfig, Server, ServerStats, AgentCredential
from core.agent import Report, accept_report
from worker.tasks import send_email_wrapper, start_scheduler, stop_scheduler, monitor_once
from core.status import server_payload
from core.history import history_payload
from core.ssh_credentials import encrypt_password
from core.operations import router as operations_router, assert_idle

# ---------------------------------------------------------------------------
# 应用初始化
# ---------------------------------------------------------------------------
app = FastAPI(title="Node Monitor API")
app.include_router(operations_router)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exc):
    # Pydantic errors can embed the submitted input, including passwords.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': list(error['loc']), 'msg': error['msg'], 'type': error['type']}
        for error in exc.errors()]})


def password_ciphertext(secret, server, request):
    if secret is None or not secret.get_secret_value():
        return None
    if request is None or request.scope.get('scheme') != 'https':
        raise HTTPException(400, 'SSH 密码只能通过 HTTPS 提交，或使用服务器本地 manage_ssh.py')
    try:
        return encrypt_password(secret.get_secret_value(), server)
    except ValueError:
        raise HTTPException(422, 'SSH 密码或加密密钥配置无效') from None
    except OSError:
        raise HTTPException(503, '请先在服务器初始化 SSH 凭据加密密钥') from None

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
    start_scheduler()
    monitor_once()  # 同一任务队列，后台采集，API 不等待 SSH


@app.on_event("shutdown")
def shutdown_event():
    stop_scheduler()


# ---------------------------------------------------------------------------
# 请求体模型 (Pydantic)
# ---------------------------------------------------------------------------
class ServerCreate(BaseModel):
    """新增服务器节点"""
    hostname: str
    ip_address: str
    group_name: str
    ssh_user: str = 'root'
    ssh_port: int = 22
    collection_mode: Literal['ssh', 'agent'] = 'agent'
    ssh_password: Optional[SecretStr] = None


class ServerUpdate(BaseModel):
    """更新节点基本信息"""
    hostname: str
    ip_address: str
    group_name: str
    ssh_user: str = 'root'
    ssh_port: int = 22
    collection_mode: Optional[Literal['ssh', 'agent']] = None
    ssh_password: Optional[SecretStr] = None


class AlertUpdate(BaseModel):
    """告警规则配置"""
    enable_email_alert: bool
    offline_threshold_minutes: int
    alert_recipient: Optional[str] = ""


class NodeNotificationUpdate(BaseModel):
    enabled: bool


class EmailUpdate(BaseModel):
    """SMTP 发件配置"""
    smtp_server: Optional[str] = ""
    smtp_port: Optional[int] = 465
    smtp_user: Optional[str] = ""
    smtp_password: Optional[str] = None
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

    return [server_payload(server) for server in servers]


@app.post("/api/servers/add")
def add_server(data: ServerCreate, db: Session = Depends(get_db), request: Request = None):
    """新增一台服务器（默认离线，等待后台调度器首次采集）"""
    new_server = Server(
        hostname=data.hostname,
        ip_address=data.ip_address,
        group_name=data.group_name,
        ssh_user=data.ssh_user,
        ssh_port=data.ssh_port,
        collection_mode=data.collection_mode,
        is_active=True,
        status="offline",
    )
    if data.collection_mode == 'ssh':
        new_server.ssh_password_encrypted = password_ciphertext(data.ssh_password, new_server, request)
        if not new_server.ssh_password_encrypted:
            raise HTTPException(422, 'SSH 采集需要输入用户名密码')
    db.add(new_server)
    db.commit()
    return {"success": True, "message": "服务器添加成功", "server_id": new_server.id}


@app.post("/api/server/{server_id}/update")
def update_server(server_id: int, data: ServerUpdate, db: Session = Depends(get_db), request: Request = None):
    """更新节点基本信息"""
    assert_idle(server_id)
    server = db.query(Server).filter(Server.id == server_id).with_for_update().first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    mode = data.collection_mode or server.collection_mode
    identity_changed = any(getattr(server, key) != getattr(data, key)
                           for key in ('ip_address', 'ssh_user', 'ssh_port'))
    target = Server(hostname=data.hostname, ip_address=data.ip_address, ssh_user=data.ssh_user, ssh_port=data.ssh_port)
    encrypted = password_ciphertext(data.ssh_password, target, request) if mode == 'ssh' else None
    connection_changed = mode != server.collection_mode or any(getattr(server, key) != getattr(data, key)
                             for key in ("hostname", "ip_address", "ssh_user", "ssh_port"))
    if connection_changed or encrypted:
        server.latest_status_data = None
        server.status = "pending"
        server.offline_since = None
        server.last_alert_time = None
    server.hostname = data.hostname
    server.ip_address = data.ip_address
    server.group_name = data.group_name
    server.ssh_user = data.ssh_user
    server.ssh_port = data.ssh_port
    if identity_changed:
        server.ssh_password_encrypted = None
    if encrypted:
        server.ssh_password_encrypted = encrypted
    if mode != server.collection_mode:
        credential = db.get(AgentCredential, server_id)
        if credential:
            db.delete(credential)
    server.collection_mode = mode
    db.commit()
    return {"success": True}


@app.delete("/api/server/{server_id}")
def delete_server_node(server_id: int, db: Session = Depends(get_db)):
    """彻底删除一台服务器节点及其所有历史数据"""
    assert_idle(server_id)
    server = db.query(Server).filter(Server.id == server_id).with_for_update().first()
    if not server:
        return {"success": False, "message": "找不到该服务器"}

    # 先删除名下的历史数据，防止触发外键约束错误
    db.query(ServerStats).filter(ServerStats.server_id == server_id).delete()
    db.query(AgentCredential).filter(AgentCredential.server_id == server_id).delete()
    db.delete(server)
    db.commit()
    return {"success": True, "message": "服务器已彻底删除"}


@app.post('/api/server/{server_id}/notifications')
def update_node_notifications(server_id: int, data: NodeNotificationUpdate, db: Session = Depends(get_db)):
    server = db.query(Server).filter(Server.id == server_id, Server.is_active == True).with_for_update().first()
    if server is None:
        raise HTTPException(404, 'Server not found')
    server.email_alert_enabled = data.enabled
    if not data.enabled:
        server.last_alert_time = None
    db.commit()
    return {'success': True, 'email_alert_enabled': server.email_alert_enabled}


@app.post('/api/agent/report')
async def agent_report(request: Request, db: Session = Depends(get_db)):
    """Bound the body before parsing it, including chunked requests."""
    authorization = request.headers.get('authorization', '')
    if not authorization.startswith('Bearer ') or not 32 <= len(authorization[7:]) <= 256:
        raise HTTPException(401, 'A node Bearer token is required')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            raise HTTPException(413, 'Report exceeds 64 KiB')
        body.extend(chunk)
    try:
        report = Report.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(422, 'Invalid metrics report') from None
    # Sync database work must not block the SSE event loop.
    return await asyncio.to_thread(accept_report, db, report, authorization[7:])


# ---------------------------------------------------------------------------
# SSE 实时数据推送（替代前端 60 秒轮询）
# ---------------------------------------------------------------------------
@app.get("/api/stream/servers")
async def stream_servers():
    """Server-Sent Events 端点：每 10 秒推送一次最新数据（只读 DB 缓存，不触发 SSH 采集）"""

    def _build_payload():
        with SessionLocal() as db:
            now = datetime.utcnow()
            return [server_payload(s, now) for s in db.query(Server).filter(Server.is_active == True).all()]

    async def event_generator():
        while True:
            # 同步 SQL 放到线程中，不阻塞所有 SSE 客户端的事件循环。
            payload = await asyncio.to_thread(_build_payload)
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(10)

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
    """短期快照、长期北京时间日均值；缺失指标不作为零参与平均。"""
    if not 1 <= days <= 365:
        raise HTTPException(status_code=422, detail="days must be between 1 and 365")
    if not db.query(Server.id).filter(Server.id == server_id).first():
        raise HTTPException(status_code=404, detail="Server not found")
    return history_payload(db, server_id, days)


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
        "has_password": bool(config.smtp_password),
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
    if data.smtp_password is not None:
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
