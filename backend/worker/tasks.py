"""
后台采集与告警调度模块
========================
职责：
1. 通过 SSH 采集每台被监控节点的 CPU / 内存 / GPU / 进程数据
2. 定时将实时数据落库到历史表 server_stats
3. 巡检离线节点，按阈值发送告警 / 恢复邮件

调度任务由 start_scheduler() 统一注册（FastAPI 启动时调用）：
- monitor_all_servers : 每 60 秒   并发采集所有活跃节点
- flush_stats_to_db   : 每 10 分钟 把实时缓存快照写入历史表
- check_server_alerts : 每 60 秒   离线告警巡检
"""
import logging
import time
from threading import Lock
from types import SimpleNamespace

import re
import smtplib
import xml.etree.ElementTree as ET
from sqlalchemy import null
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formataddr

import paramiko
from apscheduler.schedulers.background import BackgroundScheduler

from core.status import freshness, collection_time
from core.config import settings
from core.ssh_credentials import decrypt_password
from models.database import SessionLocal
from models.models import AlertConfig, EmailConfig, Server, ServerStats, AgentCredential

# 内存级并发锁：记录正在采集的 Server ID，防止同一节点重复采集
running_tasks = set()
tasks_lock = Lock()
logger = logging.getLogger(__name__)
scheduler = None
# A rejected password is tried once per configuration, avoiding account lockout.
rejected_credentials = {}

# SSH 采集线程池（最多同时并发采集 50 台服务器）
executor = ThreadPoolExecutor(max_workers=50)


# ---------------------------------------------------------------------------
# 采集输出解析
# 远端执行一条组合命令，各段输出用 <<<XXX>>> 标记分隔，逐段解析
# ---------------------------------------------------------------------------
def get_section(text, tag):
    """取出远端输出中 <<<tag>>> 与下一个 <<< 标记之间的内容"""
    try:
        marker = f"<<<{tag}>>>"
        parts = text.split(marker)
        if len(parts) > 1:
            content = parts[1]
            next_marker_idx = content.find("<<<")
            if next_marker_idx != -1:
                return content[:next_marker_idx].strip()
            return content.strip()
    except Exception:
        pass
    return ""


def parse_uptime(uptime_output):
    """从 /proc/uptime 输出解析运行秒数"""
    try:
        match = re.search(r"(\d+\.\d+)", uptime_output)
        if match:
            return int(float(match.group(1)))
    except Exception:
        pass
    return 0


def parse_cpu(top_output):
    """从 top 输出解析 CPU 使用率（us + sy）"""
    try:
        match = re.search(r"Cpu\(s\):\s*([\d\.]+)\s*us,\s*([\d\.]+)\s*sy", top_output)
        if match:
            return round(float(match.group(1)) + float(match.group(2)), 1)
    except Exception:
        pass
    return 0.0


def parse_ram(free_output):
    """从 free -m 输出解析内存总量 / 已用量 / 使用率"""
    try:
        for line in free_output.strip().split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 4:
                    total, used = int(parts[1]), int(parts[2])
                    percent = round((used / total) * 100, 1) if total > 0 else 0
                    return {"total_mb": total, "used_mb": used, "percent": percent}
    except Exception:
        pass
    return {"total_mb": 0, "used_mb": 0, "percent": 0}


def _parse_watts(text):
    """解析 nvidia-smi 的功耗文本（形如 "35.12 W"，失败返回 0）"""
    try:
        return float(str(text).replace("W", "").strip())
    except Exception:
        return 0.0


# 常见 NVIDIA 显卡参考功耗上限（TDP，单位 W）：驱动拿不到真实 power_limit 时按型号匹配
# 注意：匹配按子串查找，长型号 key 需排在可能误匹配的短 key 之前
GPU_TDP_REFERENCE = {
    "H200": 700, "H100": 700, "H20": 400, "A100": 400, "A800": 300, "A40": 300,
    "A30": 165, "A16": 250, "A10": 150, "L40S": 350, "L40": 300, "L20": 275, "L4": 72,
    "V100": 300, "P100": 250, "P40": 250, "T4": 70, "K80": 300,
    "RTX 6000 Ada": 300, "RTX A6000": 300, "RTX A5500": 230, "RTX A5000": 230,
    "RTX A4000": 140, "RTX 5000": 265, "RTX 4000": 160, "RTX 4090": 450,
    "RTX 4080": 320, "RTX 4070 Ti": 285, "RTX 4070": 200, "RTX 4060 Ti": 160, "RTX 4060": 115,
    "RTX 3090 Ti": 450, "RTX 3090": 350, "RTX 3080 Ti": 350, "RTX 3080": 320,
    "RTX 3070 Ti": 290, "RTX 3070": 220, "RTX 3060 Ti": 200, "RTX 3060": 170, "RTX 3050": 130,
    "RTX 2080 Ti": 250, "RTX 2080": 215, "RTX 2070": 175, "RTX 2060": 160,
    "GTX 1080 Ti": 250, "GTX 1080": 180, "GTX 1070 Ti": 180, "GTX 1070": 150, "GTX 1060": 120,
}
# 型号未收录时的默认参考功耗
DEFAULT_GPU_TDP = 350


def reference_power_limit(model, current):
    """返回功耗上限：优先真实读数，其次按型号匹配参考 TDP，最后用默认值"""
    if current and current > 0:
        return current
    if model:
        model_lower = model.lower()
        for key, tdp in GPU_TDP_REFERENCE.items():
            if key.lower() in model_lower:
                return tdp
    return DEFAULT_GPU_TDP


def parse_gpu_power_csv(csv_output):
    """解析 nvidia-smi --query-gpu=power.draw,power.max_limit 的 CSV 输出

    返回 [(draw, limit), ...]，顺序与 XML 中的 GPU 顺序一致；N/A 解析为 0。
    """
    rows = []
    for line in csv_output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            rows.append((_parse_watts(parts[0]), _parse_watts(parts[1])))
    return rows


def parse_gpu(xml_output):
    """解析 nvidia-smi -q -x 的 XML 输出，提取每张卡的型号、温度、显存、功耗"""
    gpus = []
    try:
        root = ET.fromstring(xml_output)
        for gpu in root.findall("gpu"):
            product_name = gpu.find("product_name").text
            temp_node = gpu.find("temperature")
            temp = temp_node.find("gpu_temp").text if temp_node is not None else "N/A"

            mem_usage = gpu.find("fb_memory_usage")
            mem_total = mem_usage.find("total").text
            mem_used = mem_usage.find("used").text

            try:
                mem_percent = round((float(mem_used.split()[0]) / float(mem_total.split()[0])) * 100, 1)
            except Exception:
                mem_percent = 0

            # 功耗读数（兼容新旧两种 nvidia-smi XML 格式，解析失败按 0 处理）
            # 新格式: instant_power_draw / gpu_ceiling_power_limit->current_power_limit
            # 旧格式: power_draw / power_limit
            power_draw = 0.0
            power_limit = 0.0
            power_node = gpu.find("gpu_power_readings") or gpu.find("power_readings")
            if power_node is not None:
                power_draw = _parse_watts(power_node.findtext("instant_power_draw", "") or "N/A")
                if power_draw <= 0:
                    power_draw = _parse_watts(power_node.findtext("average_power_draw", "") or "N/A")
                if power_draw <= 0:
                    power_draw = _parse_watts(power_node.findtext("power_draw", "") or "N/A")

                ceiling = power_node.find("gpu_ceiling_power_limit")
                if ceiling is not None:
                    power_limit = _parse_watts(ceiling.findtext("current_power_limit", "") or "N/A")
                if power_limit <= 0:
                    power_limit = _parse_watts(power_node.findtext("power_limit", "") or "N/A")

            # XML 拿不到上限时，按显卡型号匹配内置参考 TDP（不同卡型上限差异大）
            power_limit = reference_power_limit(product_name, power_limit)

            gpus.append({
                "model": product_name,
                "temp": temp,
                "memory_used": mem_used,
                "memory_total": mem_total,
                "memory_percent": mem_percent,
                "power_draw": round(power_draw, 1),    # 当前功耗 (W)
                "power_limit": round(power_limit, 1),  # 功耗上限 (W)
            })
    except Exception:
        pass
    return gpus


# ---------------------------------------------------------------------------
# 邮件发送
# ---------------------------------------------------------------------------
def send_email_wrapper(db, subject, content, recipient=None, is_test=False):
    """底层邮件发送逻辑

    返回 (是否成功, 提示信息)。465 端口走 SSL，其余端口可开启 STARTTLS。
    """
    # 凭据读完即归还连接，SMTP 网络等待不占数据库连接。
    with SessionLocal() as config_db:
        row = config_db.query(EmailConfig).first()
        email_config = SimpleNamespace(**{c.name: getattr(row, c.name) for c in EmailConfig.__table__.columns}) if row else None
        alert = config_db.query(AlertConfig).first()
        default_recipient = alert.alert_recipient if alert else None

    if not email_config or not email_config.smtp_server:
        return False, "SMTP未配置"

    target_recipient = recipient or default_recipient
    if not target_recipient:
        return False, "未设置接收人邮箱"

    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr((email_config.from_name or "Node Monitor", email_config.from_address))
        msg["To"] = target_recipient

        # 支持多个收件人（逗号分隔）
        recipients_list = [r.strip() for r in target_recipient.split(",")]

        if email_config.smtp_port == 465:
            server_conn = smtplib.SMTP_SSL(email_config.smtp_server, email_config.smtp_port, timeout=10)
        else:
            server_conn = smtplib.SMTP(email_config.smtp_server, email_config.smtp_port, timeout=10)
            if email_config.use_tls:
                server_conn.starttls()

        server_conn.login(email_config.smtp_user, email_config.smtp_password)
        server_conn.sendmail(email_config.from_address, recipients_list, msg.as_string())
        server_conn.quit()
        return True, "发送成功"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# 离线告警巡检（每分钟执行一次）
# ---------------------------------------------------------------------------
def check_server_alerts():
    """SSH 明确失败及 Agent 超时告警；使用服务端时间判断失联。"""
    now = datetime.utcnow()
    # Agent silence is inferred from server receipt time, never the node clock.
    recoveries = []
    with SessionLocal() as db:
        alert_config = db.query(AlertConfig).first()
        alerts_enabled = bool(alert_config and alert_config.enable_email_alert)
        for server in db.query(Server).filter(Server.is_active == True, Server.collection_mode == 'agent').order_by(Server.id).with_for_update().all():
            effective, age = freshness(server, now)
            if effective == 'offline':
                server.status = 'offline'
                server.offline_since = server.offline_since or server.last_online
            elif effective == 'online' and server.last_alert_time and server.email_alert_enabled:
                recoveries.append((server.id, server.hostname, server.last_alert_time))
        db.commit()
    if not alerts_enabled:
        return
    for sid, hostname, previous_alert in recoveries:
        success, _ = send_node_email(sid, f'【恢复】服务器 {hostname} 已恢复上报', f'节点 {hostname} 已恢复指标上报。')
        if success:
            with SessionLocal() as db:
                db.query(Server).filter(Server.id == sid, Server.last_alert_time == previous_alert).update({Server.last_alert_time: None})
                db.commit()
    with SessionLocal() as db:
        config = db.query(AlertConfig).first()
        if not config or not config.enable_email_alert:
            return
        threshold = config.offline_threshold_minutes or 5
        candidates = []
        for server in db.query(Server).filter(Server.is_active == True, Server.status == "offline", Server.email_alert_enabled == True).all():
            if freshness(server, now)[0] != "offline" or not server.offline_since:
                continue
            wait = max(settings.AGENT_OFFLINE_SECONDS, threshold * 60) if server.collection_mode == 'agent' else threshold * 60
            if (now - server.offline_since).total_seconds() < wait:
                continue
            if server.last_alert_time and (now - server.last_alert_time).total_seconds() <= 86400:
                continue
            candidates.append((server.id, server.hostname, server.ip_address, server.offline_since))
    for sid, hostname, address, offline_since in candidates:
        success, message = send_node_email(sid, f"【告警】服务器 {hostname} 已离线",
            f"节点 {hostname} ({address}) 已失联超过 {threshold} 分钟，请检查。")
        if success:
            with SessionLocal() as db:
                db.query(Server).filter(Server.id == sid, Server.status == "offline",
                    Server.offline_since == offline_since).update({Server.last_alert_time: now})
                db.commit()
        elif message != 'muted':
            logger.warning("Alert delivery failed for node %s: %s", sid, message)


def send_node_email(server_id, subject, content):
    # Recheck immediately before sending, including when the toggle changed mid-cycle.
    with SessionLocal() as db:
        node = db.get(Server, server_id)
        config = db.query(AlertConfig).first()
        if not node or not node.is_active or not node.email_alert_enabled or not config or not config.enable_email_alert:
            return False, 'muted'
    return send_email_wrapper(None, subject, content)


# ---------------------------------------------------------------------------
# 核心采集逻辑
# ---------------------------------------------------------------------------
def collect_server_data(server):
    """通过 SSH 采集单台服务器的全部指标，返回 data 字典。

    纯采集逻辑，不涉及数据库写入，供 _do_monitor_server 和 SSE 端点共用。
    """
    data = {
        "uptime_seconds": 0, "cpu_percent": 0, "cpu_cores": 1,
        "ram_total_mb": 0, "ram_used_mb": 0, "ram_percent": 0,
        "gpu_data": [], "top_process": "暂无",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    client = None
    try:
        password = decrypt_password(server)
        credential_version = getattr(server, 'ssh_password_encrypted', None)
        if rejected_credentials.get(getattr(server, 'id', None)) == credential_version:
            raise ValueError('SSH 密码认证失败，请更新密码后重试')
        cmd = (
            "export PATH=$PATH:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin; "
            "echo '<<<UPTIME>>>'; cat /proc/uptime; "
            "echo '<<<RAM>>>'; free -m; "
            "echo '<<<CPU>>>'; top -bn1 | head -n 5; "
            "echo '<<<CORES>>>'; nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo; "
            "echo '<<<GPU>>>'; timeout 15 nvidia-smi -q -x 2>/dev/null || echo 'NO_GPU'; "
            "echo '<<<GPUPW>>>'; timeout 15 nvidia-smi --query-gpu=power.draw,power.max_limit --format=csv,noheader 2>/dev/null || echo 'NO_GPU'; "
            "echo '<<<TOP_PROC>>>'; ps -eo user,%cpu,%mem,comm --sort=-%cpu | head -n 2 | tail -n 1; "
            "echo '<<<END>>>'"
        )

        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        target_host = server.ip_address if server.ip_address else server.hostname

        client.connect(
            hostname=target_host,
            port=server.ssh_port,
            username=server.ssh_user or "root",
            password=password, look_for_keys=False, allow_agent=False,
            timeout=15, banner_timeout=15, auth_timeout=15,
        )

        _, stdout, _ = client.exec_command(cmd, timeout=15)
        output = stdout.read().decode("utf-8", errors="ignore")

        # 逐段解析
        data["uptime_seconds"] = parse_uptime(get_section(output, "UPTIME"))
        ram_info = parse_ram(get_section(output, "RAM"))
        data["ram_total_mb"] = ram_info["total_mb"]
        data["ram_used_mb"] = ram_info["used_mb"]
        data["ram_percent"] = ram_info["percent"]
        data["cpu_percent"] = parse_cpu(get_section(output, "CPU"))

        try:
            data["cpu_cores"] = int(re.search(r"(\d+)", get_section(output, "CORES")).group(1))
        except Exception:
            pass

        # GPU 段：无卡时命令输出 NO_GPU，跳过解析
        gpu_part = get_section(output, "GPU")
        if "NO_GPU" not in gpu_part and gpu_part.strip():
            start = gpu_part.find("<")
            if start != -1:
                data["gpu_data"] = parse_gpu(gpu_part[start:])

        # GPU 功率：优先用 CSV 查询结果修正
        gpu_pw_part = get_section(output, "GPUPW")
        if "NO_GPU" not in gpu_pw_part and gpu_pw_part.strip():
            pw_rows = parse_gpu_power_csv(gpu_pw_part)
            for i, gpu in enumerate(data["gpu_data"]):
                if i < len(pw_rows):
                    draw, limit = pw_rows[i]
                    if draw and draw > 0:
                        gpu["power_draw"] = draw
                    if limit and limit > 0:
                        gpu["power_limit"] = limit

        # 当前 CPU 占用最高的进程
        top_proc_part = get_section(output, "TOP_PROC")
        if top_proc_part:
            parts = top_proc_part.split()
            if len(parts) >= 4:
                data["top_process"] = f"{parts[0]} 运行 {parts[3]} (CPU:{parts[1]}% 内存:{parts[2]}%)"

        return data, True, ""

    except Exception as e:
        if isinstance(e, paramiko.AuthenticationException):
            rejected_credentials[getattr(server, 'id', None)] = getattr(server, 'ssh_password_encrypted', None)
            error = 'SSH 密码认证失败，请更新密码后重试'
        elif isinstance(e, paramiko.BadHostKeyException):
            error = 'SSH 主机指纹不匹配，请由管理员核验'
        elif isinstance(e, ValueError):
            error = 'SSH 密码未配置、已被拒绝或加密配置不可用，请检查凭据'
        else:
            error = 'SSH 采集失败：' + type(e).__name__
        data["error_message"] = error
        return data, False, error
    finally:
        data["timestamp"] = datetime.utcnow().isoformat() + "Z"
        if client:
            client.close()


CONFIG_FIELDS = ("hostname", "ip_address", "ssh_port", "ssh_user", "group_name", "collection_mode", "ssh_password_encrypted")


def _do_monitor_server(server_id):
    """读配置 / 无连接 SSH / 短事务写回；同一节点排队及执行期间均去重。"""
    started = time.monotonic()
    try:
        with SessionLocal() as db:
            row = db.query(Server).filter(Server.id == server_id, Server.is_active == True, Server.collection_mode == 'ssh').first()
            if row is None:
                return
            config = {key: getattr(row, key) for key in CONFIG_FIELDS}
        data, ok, error = collect_server_data(SimpleNamespace(id=server_id, **config))
        data["timestamp"] = datetime.utcnow().isoformat() + "Z"
        data["collection_duration_seconds"] = round(time.monotonic() - started, 3)
        recovery = False
        with SessionLocal() as db:
            # 锁定写入行，并检查配置，丢弃编辑前或删除前启动的旧任务结果。
            row = db.query(Server).filter(Server.id == server_id).with_for_update().first()
            if row is None or not row.is_active or any(getattr(row, key) != val for key, val in config.items()):
                logger.info("Discarded obsolete collection for node %s", server_id)
                return
            now = datetime.utcnow()
            if ok:
                recovery = row.status == "offline" and bool(row.last_alert_time)
                row.status, row.last_online, row.offline_since = "online", now, None
                row.last_alert_time = None
            else:
                row.status = "offline"
                row.offline_since = row.offline_since or now
            row.latest_status_data = data
            db.commit()
        if recovery:
            success, message = send_node_email(server_id, f"【恢复】服务器 {config['hostname']} 已恢复上线",
                f"节点 {config['hostname']} ({config['ip_address']}) 已恢复 SSH 连接。")
            if not success and message != 'muted':
                logger.warning("Recovery delivery failed for node %s: %s", server_id, message)
        if not ok:
            logger.warning("Collection failed for node %s in %.2fs: %s", server_id, time.monotonic() - started, error)
    except Exception:
        logger.exception("Collection/persistence failed for node %s", server_id)
    finally:
        with tasks_lock:
            running_tasks.discard(server_id)


def submit_server(server_id):
    with tasks_lock:
        if server_id in running_tasks:
            return None
        running_tasks.add(server_id)
    try:
        return executor.submit(_do_monitor_server, server_id)
    except Exception:
        with tasks_lock:
            running_tasks.discard(server_id)
        raise


def monitor_all_servers():
    with SessionLocal() as db:
        ids = [sid for (sid,) in db.query(Server.id).filter(Server.is_active == True, Server.collection_mode == 'ssh').all()]
    for sid in ids:
        submit_server(sid)


def monitor_once():
    """启动与周期采集共用任务队列，启动无需等待全部 SSH 完成。"""
    monitor_all_servers()


def flush_stats_to_db():
    """触发器（每 10 分钟）：把每台机器的实时缓存快照写入历史表"""
    db = SessionLocal()
    try:
        servers = db.query(Server).filter(Server.is_active == True).order_by(Server.id).all()  # noqa: E712
        now = datetime.utcnow()
        for server in servers:
            # Serialize against reports so history dedup survives concurrent requests.
            db.refresh(server, with_for_update=True)
            data = server.latest_status_data or {}
            effective_status, _ = freshness(server, now)
            valid = effective_status == "online"
            credential = db.get(AgentCredential, server.id) if server.collection_mode == 'agent' else None
            if credential and valid and credential.last_history_report_id == data.get('report_id'):
                continue
            if credential and valid:
                credential.last_history_report_id = data.get('report_id')
            db.add(ServerStats(
                server_id=server.id,
                timestamp=(collection_time(data) if server.collection_mode == 'agent' and valid else now),
                status=effective_status,
                uptime_seconds=data.get("uptime_seconds", 0) if valid else null(),
                cpu_percent=data.get("cpu_percent", 0) if valid else null(),
                cpu_cores=data.get("cpu_cores", 1) if valid else null(),
                ram_total_mb=data.get("ram_total_mb", 0) if valid else null(),
                ram_used_mb=data.get("ram_used_mb", 0) if valid else null(),
                ram_percent=data.get("ram_percent", 0) if valid else null(),
                gpu_data=data.get("gpu_data", []) if valid else None,
            ))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 调度器初始化（由 FastAPI startup 事件调用）
# ---------------------------------------------------------------------------
def start_scheduler():
    """注册并启动后台定时任务"""
    global scheduler
    if scheduler and scheduler.running:
        return
    scheduler = BackgroundScheduler()
    scheduler.add_job(monitor_all_servers, "interval", seconds=60)      # 每分钟并发采集
    scheduler.add_job(flush_stats_to_db, "interval", minutes=10)        # 每 10 分钟历史落库
    scheduler.add_job(check_server_alerts, "interval", minutes=1)       # 每分钟告警巡检
    scheduler.start()
    print("🚀 APScheduler 已启动：包含 1 分钟采集任务、1 分钟告警巡视与 10 分钟落库任务。")


def stop_scheduler():
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    executor.shutdown(wait=True, cancel_futures=True)
    with tasks_lock:
        running_tasks.clear()
