import paramiko
import re
import xml.etree.ElementTree as ET
import os
import sys
import smtplib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from email.utils import formataddr
from models.models import EmailConfig, AlertConfig # 引入刚才的配置表模型
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.database import SessionLocal
from models.models import Server, ServerStats
from core.config import settings

from apscheduler.schedulers.background import BackgroundScheduler

# 内存级并发锁：记录正在采集的 Server ID，防重复采集
running_tasks = set()

# SSH 线程池 (允许同时并发采集 50 台服务器)
executor = ThreadPoolExecutor(max_workers=50)

# --- SSH 数据解析辅助函数 (保持不变) ---
def parse_uptime(uptime_output):
    try:
        match = re.search(r'(\d+\.\d+)', uptime_output)
        if match: return int(float(match.group(1)))
    except: pass
    return 0

def parse_cpu(top_output):
    try:
        match = re.search(r'Cpu\(s\):\s*([\d\.]+)\s*us,\s*([\d\.]+)\s*sy', top_output)
        if match: return round(float(match.group(1)) + float(match.group(2)), 1)
    except: pass
    return 0.0

def parse_ram(free_output):
    try:
        for line in free_output.strip().split('\n'):
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 4:
                    total, used = int(parts[1]), int(parts[2])
                    return {"total_mb": total, "used_mb": used, "percent": round((used / total) * 100, 1) if total > 0 else 0}
    except: pass
    return {"total_mb": 0, "used_mb": 0, "percent": 0}

def parse_ps(ps_output):
    process_map = {}
    try:
        for line in ps_output.strip().split('\n'):
            parts = line.strip().split(None, 2)
            if len(parts) >= 2 and parts[0].isdigit():
                process_map[int(parts[0])] = {"user": parts[1], "cmd": parts[2] if len(parts) == 3 else ""}
    except: pass
    return process_map

def parse_gpu(xml_output, process_map):
    gpus = []
    try:
        root = ET.fromstring(xml_output)
        for gpu in root.findall('gpu'):
            product_name = gpu.find('product_name').text
            temp = gpu.find('temperature').find('gpu_temp').text if gpu.find('temperature') is not None else "N/A"
            mem_total = gpu.find('fb_memory_usage').find('total').text
            mem_used = gpu.find('fb_memory_usage').find('used').text
            
            try:
                mem_percent = round((float(mem_used.split()[0]) / float(mem_total.split()[0])) * 100, 1)
            except:
                mem_percent = 0
            
            gpus.append({
                "model": product_name, "temp": temp,
                "memory_used": mem_used, "memory_total": mem_total, "memory_percent": mem_percent
            })
    except: pass
    return gpus

def get_section(text, tag):
    try:
        marker = f"<<<{tag}>>>"
        parts = text.split(marker)
        if len(parts) > 1:
            content = parts[1]
            next_marker_idx = content.find("<<<")
            if next_marker_idx != -1: return content[:next_marker_idx].strip()
            return content.strip()
    except: pass
    return ""
# --- 邮件发送核心 ---
def send_email_wrapper(db, subject, content, recipient=None, is_test=False):
    """底层邮件发送逻辑"""
    email_config = db.query(EmailConfig).first()
    alert_config = db.query(AlertConfig).first()
    
    if not email_config or not email_config.smtp_server:
        return False, "SMTP未配置"
        
    target_recipient = recipient or (alert_config.alert_recipient if alert_config else None)
    if not target_recipient:
        return False, "未设置接收人邮箱"

    try:
        msg = MIMEText(content, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = formataddr((email_config.from_name or 'Node Monitor', email_config.from_address))
        msg['To'] = target_recipient

        # 处理多个收件人
        recipients_list = [r.strip() for r in target_recipient.split(',')]

        if email_config.smtp_port == 465:
            server_cnn = smtplib.SMTP_SSL(email_config.smtp_server, email_config.smtp_port, timeout=10)
        else:
            server_cnn = smtplib.SMTP(email_config.smtp_server, email_config.smtp_port, timeout=10)
            if email_config.use_tls:
                server_cnn.starttls()
                
        server_cnn.login(email_config.smtp_user, email_config.smtp_password)
        server_cnn.sendmail(email_config.from_address, recipients_list, msg.as_string())
        server_cnn.quit()
        return True, "发送成功"
    except Exception as e:
        return False, str(e)

# --- 阈值告警轮询任务 ---
def check_server_alerts():
    """每分钟运行一次：检查是否有服务器超过安全阈值需要发邮件"""
    db = SessionLocal()
    try:
        alert_config = db.query(AlertConfig).first()
        if not alert_config or not alert_config.enable_email_alert:
            return # 未开启邮件告警，直接跳过
            
        threshold = alert_config.offline_threshold_minutes or 5
        now = datetime.utcnow()
        
        # 找出所有离线的服务器
        offline_servers = db.query(Server).filter(
            Server.is_active == True,
            Server.status == 'offline',
            Server.offline_since != None
        ).all()
        
        for server in offline_servers:
            # 计算已经离线了多少分钟
            duration_mins = (now - server.offline_since).total_seconds() / 60
            
            # 如果突破了安全阈值
            if duration_mins >= threshold:
                # 检查是否还没有发过告警，或者上一次发告警已经是 24 小时前了 (防止邮件轰炸)
                if not server.last_alert_time or (now - server.last_alert_time).total_seconds() > 86400:
                    content = f"⚠️ 节点宕机告警\n\n主机名: {server.hostname}\nIP地址: {server.ip_address}\n分组: {server.group_name}\n\n已失联时间超过设定的安全阈值 ({threshold} 分钟)。\n请及时登录机房终端进行排查！"
                    success, msg = send_email_wrapper(db, subject=f"【告警】服务器 {server.hostname} 已离线", content=content)
                    
                    if success:
                        server.last_alert_time = now # 记录告警时间，防止重复发送
                        db.commit()
    finally:
        db.close()
# --- 核心采集逻辑 ---
def _do_monitor_server(server_id):
    """实际执行 SSH 采集的工作线程"""
    # 获取内存锁，防并发重入
    if server_id in running_tasks:
        return
    running_tasks.add(server_id)
    
    db = SessionLocal()
    client = None
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server or not server.is_active:
            return
            
        data = {
            "uptime_seconds": 0, "cpu_percent": 0, "cpu_cores": 1,
            "ram_total_mb": 0, "ram_used_mb": 0, "ram_percent": 0,
            "gpu_data": [], "top_process": "暂无",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        try:
            cmd = (
                "export PATH=$PATH:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin; "
                "echo '<<<UPTIME>>>'; cat /proc/uptime; "
                "echo '<<<RAM>>>'; free -m; "
                "echo '<<<CPU>>>'; top -bn1 | head -n 5; "
                "echo '<<<CORES>>>'; nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo; "
                "echo '<<<GPU>>>'; timeout 15 nvidia-smi -q -x 2>/dev/null || echo 'NO_GPU'; "
                "echo '<<<PS>>>'; ps -eo pid,user,args --no-headers 2>/dev/null | grep -v '<<<' | tail -n +2; "
                "echo '<<<TOP_PROC>>>'; ps -eo user,%cpu,%mem,comm --sort=-%cpu | head -n 2 | tail -n 1; "
                "echo '<<<END>>>'"
            )
            
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            target_host = server.ip_address if server.ip_address else server.hostname
            
            client.connect(
                hostname=target_host,
                port=server.ssh_port,
                username=server.ssh_user or 'root',
                timeout=15  # 超时时间设短，防止假死
            )
            
            stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
            output = stdout.read().decode('utf-8', errors='ignore')
            
            data['uptime_seconds'] = parse_uptime(get_section(output, "UPTIME"))
            ram_info = parse_ram(get_section(output, "RAM"))
            data['ram_total_mb'] = ram_info['total_mb']
            data['ram_used_mb'] = ram_info['used_mb']
            data['ram_percent'] = ram_info['percent']
            data['cpu_percent'] = parse_cpu(get_section(output, "CPU"))
            
            try:
                data['cpu_cores'] = int(re.search(r'(\d+)', get_section(output, "CORES")).group(1))
            except: pass
            
            gpu_part = get_section(output, "GPU")
            if 'NO_GPU' not in gpu_part and gpu_part.strip():
                start = gpu_part.find('<')
                if start != -1:
                    data['gpu_data'] = parse_gpu(gpu_part[start:], parse_ps(get_section(output, "PS")))

            top_proc_part = get_section(output, "TOP_PROC")
            if top_proc_part:
                parts = top_proc_part.split()
                if len(parts) >= 4:
                    data['top_process'] = f"{parts[0]} 运行 {parts[3]} (CPU:{parts[1]}% 内存:{parts[2]}%)"

            # 成功采集：仅做 UPDATE，极低 MySQL 压力
            # 成功采集：仅做 UPDATE
            if server.status == 'offline' and server.last_alert_time:
                # 如果它之前掉线并触发过告警，现在活过来了，发一封恢复邮件
                recovery_content = f"✅ 节点已恢复\n\n主机名: {server.hostname}\nIP地址: {server.ip_address}\n现已重新建立 SSH 监控连接。"
                send_email_wrapper(db, subject=f"【恢复】服务器 {server.hostname} 已恢复上线", content=recovery_content)
                server.last_alert_time = None # 清空告警记录
                
            server.status = 'online'
            server.last_online = datetime.utcnow()
            server.offline_since = None
            server.latest_status_data = data
            
        except Exception as e:
            # 采集失败处理
            server.status = 'offline'
            if not server.offline_since:
                server.offline_since = datetime.utcnow()
            data['error_message'] = str(e)
            server.latest_status_data = data
            
        finally:
            db.commit()
            if client: client.close()
            
    finally:
        db.close()
        running_tasks.discard(server_id) # 释放内存锁


def monitor_all_servers():
    """触发器：每分钟将所有机器丢进线程池"""
    db = SessionLocal()
    try:
        servers = db.query(Server).filter(Server.is_active == True).all()
        for server in servers:
            executor.submit(_do_monitor_server, server.id)
    finally:
        db.close()

def flush_stats_to_db():
    """触发器：每 10 分钟将最新缓存状态落库一次"""
    db = SessionLocal()
    try:
        servers = db.query(Server).filter(Server.is_active == True).all()
        now = datetime.utcnow()
        for server in servers:
            data = server.latest_status_data or {}
            stat = ServerStats(
                server_id=server.id,
                timestamp=now,
                status=server.status,
                uptime_seconds=data.get('uptime_seconds', 0),
                cpu_percent=data.get('cpu_percent', 0),
                cpu_cores=data.get('cpu_cores', 1),
                ram_total_mb=data.get('ram_total_mb', 0),
                ram_used_mb=data.get('ram_used_mb', 0),
                ram_percent=data.get('ram_percent', 0),
                gpu_data=data.get('gpu_data', [])
            )
            db.add(stat)
        db.commit()
    finally:
        db.close()


# --- 初始化全局调度器 ---
def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(monitor_all_servers, 'interval', seconds=60)
    scheduler.add_job(flush_stats_to_db, 'interval', minutes=10)
    
    # 👇 新增：每 1 分钟去巡视一遍是否要触发邮件告警
    scheduler.add_job(check_server_alerts, 'interval', minutes=1)
    
    scheduler.start()
    print("🚀 APScheduler 已启动：包含 1 分钟采集任务、1 分钟告警巡视与 10 分钟落库任务。")
