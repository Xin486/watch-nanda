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
import re
import smtplib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formataddr

import paramiko
from apscheduler.schedulers.background import BackgroundScheduler

from models.database import SessionLocal
from models.models import AlertConfig, EmailConfig, Server, ServerStats

# 内存级并发锁：记录正在采集的 Server ID，防止同一节点重复采集
running_tasks = set()

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

            # 功耗读数（可能为 N/A，解析失败按 0 处理）
            power_draw = 0.0
            power_limit = 0.0
            power_node = gpu.find("power_readings")
            if power_node is not None:
                power_draw = _parse_watts(power_node.findtext("power_draw", "") or "N/A")
                power_limit = _parse_watts(power_node.findtext("power_limit", "") or "N/A")

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
    email_config = db.query(EmailConfig).first()
    alert_config = db.query(AlertConfig).first()

    if not email_config or not email_config.smtp_server:
        return False, "SMTP未配置"

    target_recipient = recipient or (alert_config.alert_recipient if alert_config else None)
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
    """扫描离线节点，超过阈值发告警邮件（同一节点 24 小时内不重复发送）"""
    db = SessionLocal()
    try:
        alert_config = db.query(AlertConfig).first()
        if not alert_config or not alert_config.enable_email_alert:
            return  # 未开启邮件告警，直接跳过

        threshold = alert_config.offline_threshold_minutes or 5
        now = datetime.utcnow()

        offline_servers = db.query(Server).filter(
            Server.is_active == True,  # noqa: E712
            Server.status == "offline",
            Server.offline_since != None,  # noqa: E711
        ).all()

        for server in offline_servers:
            # 已离线时长（分钟）
            duration_mins = (now - server.offline_since).total_seconds() / 60

            if duration_mins < threshold:
                continue

            # 未发过告警，或距上次告警已超过 24 小时（防止邮件轰炸）
            if server.last_alert_time and (now - server.last_alert_time).total_seconds() <= 86400:
                continue

            content = (
                f"⚠️ 节点宕机告警\n\n"
                f"主机名: {server.hostname}\n"
                f"IP地址: {server.ip_address}\n"
                f"分组: {server.group_name}\n\n"
                f"已失联时间超过设定的安全阈值 ({threshold} 分钟)。\n"
                f"请及时登录机房终端进行排查！"
            )
            success, _ = send_email_wrapper(
                db, subject=f"【告警】服务器 {server.hostname} 已离线", content=content
            )

            if success:
                server.last_alert_time = now  # 记录告警时间，防止重复发送
                db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 核心采集逻辑
# ---------------------------------------------------------------------------
def _do_monitor_server(server_id):
    """实际执行 SSH 采集的工作线程（由线程池调度）"""
    if server_id in running_tasks:
        return  # 内存锁：该节点正在采集中
    running_tasks.add(server_id)

    db = SessionLocal()
    client = None
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server or not server.is_active:
            return

        # 采集结果默认值（失败时同样回写，前端据此展示离线原因）
        data = {
            "uptime_seconds": 0, "cpu_percent": 0, "cpu_cores": 1,
            "ram_total_mb": 0, "ram_used_mb": 0, "ram_percent": 0,
            "gpu_data": [], "top_process": "暂无",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        try:
            # 远端执行一条组合命令，一次 SSH 会话拿齐所有数据（段间用 <<<XXX>>> 标记）
            cmd = (
                "export PATH=$PATH:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin; "
                "echo '<<<UPTIME>>>'; cat /proc/uptime; "
                "echo '<<<RAM>>>'; free -m; "
                "echo '<<<CPU>>>'; top -bn1 | head -n 5; "
                "echo '<<<CORES>>>'; nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo; "
                "echo '<<<GPU>>>'; timeout 15 nvidia-smi -q -x 2>/dev/null || echo 'NO_GPU'; "
                "echo '<<<TOP_PROC>>>'; ps -eo user,%cpu,%mem,comm --sort=-%cpu | head -n 2 | tail -n 1; "
                "echo '<<<END>>>'"
            )

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            target_host = server.ip_address if server.ip_address else server.hostname

            client.connect(
                hostname=target_host,
                port=server.ssh_port,
                username=server.ssh_user or "root",
                timeout=15,  # 超时设短，防止假死拖垮线程池
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

            # 当前 CPU 占用最高的进程
            top_proc_part = get_section(output, "TOP_PROC")
            if top_proc_part:
                parts = top_proc_part.split()
                if len(parts) >= 4:
                    data["top_process"] = f"{parts[0]} 运行 {parts[3]} (CPU:{parts[1]}% 内存:{parts[2]}%)"

            # 成功采集：节点从离线恢复时补发恢复邮件
            if server.status == "offline" and server.last_alert_time:
                recovery_content = (
                    f"✅ 节点已恢复\n\n"
                    f"主机名: {server.hostname}\n"
                    f"IP地址: {server.ip_address}\n"
                    f"现已重新建立 SSH 监控连接。"
                )
                send_email_wrapper(db, subject=f"【恢复】服务器 {server.hostname} 已恢复上线", content=recovery_content)
                server.last_alert_time = None  # 清空告警记录

            server.status = "online"
            server.last_online = datetime.utcnow()
            server.offline_since = None
            server.latest_status_data = data

        except Exception as e:
            # 采集失败：标记离线并记录首次离线时间
            server.status = "offline"
            if not server.offline_since:
                server.offline_since = datetime.utcnow()
            data["error_message"] = str(e)
            server.latest_status_data = data

        finally:
            db.commit()
            if client:
                client.close()

    finally:
        db.close()
        running_tasks.discard(server_id)  # 释放内存锁


def monitor_all_servers():
    """触发器（每 60 秒）：把所有活跃节点丢进线程池并发采集"""
    db = SessionLocal()
    try:
        servers = db.query(Server).filter(Server.is_active == True).all()  # noqa: E712
        for server in servers:
            executor.submit(_do_monitor_server, server.id)
    finally:
        db.close()


def flush_stats_to_db():
    """触发器（每 10 分钟）：把每台机器的实时缓存快照写入历史表"""
    db = SessionLocal()
    try:
        servers = db.query(Server).filter(Server.is_active == True).all()  # noqa: E712
        now = datetime.utcnow()
        for server in servers:
            data = server.latest_status_data or {}
            db.add(ServerStats(
                server_id=server.id,
                timestamp=now,
                status=server.status,
                uptime_seconds=data.get("uptime_seconds", 0),
                cpu_percent=data.get("cpu_percent", 0),
                cpu_cores=data.get("cpu_cores", 1),
                ram_total_mb=data.get("ram_total_mb", 0),
                ram_used_mb=data.get("ram_used_mb", 0),
                ram_percent=data.get("ram_percent", 0),
                gpu_data=data.get("gpu_data", []),
            ))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 调度器初始化（由 FastAPI startup 事件调用）
# ---------------------------------------------------------------------------
def start_scheduler():
    """注册并启动后台定时任务"""
    scheduler = BackgroundScheduler()
    scheduler.add_job(monitor_all_servers, "interval", seconds=60)      # 每分钟并发采集
    scheduler.add_job(flush_stats_to_db, "interval", minutes=10)        # 每 10 分钟历史落库
    scheduler.add_job(check_server_alerts, "interval", minutes=1)       # 每分钟告警巡检
    scheduler.start()
    print("🚀 APScheduler 已启动：包含 1 分钟采集任务、1 分钟告警巡视与 10 分钟落库任务。")
