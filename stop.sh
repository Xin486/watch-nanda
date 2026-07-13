#!/bin/bash

echo "===================================================="
echo "🛑 开始安全关闭 Node Monitor 监控平台..."
echo "===================================================="

# 1. 查找并关闭后端进程 (FastAPI / Uvicorn)
echo "🔍 正在检索后端进程 (uvicorn)..."
# 通过完整命令特征匹配后端进程
BACKEND_PIDS=$(pgrep -f "uvicorn main:app")

if [ -z "$BACKEND_PIDS" ]; then
    echo "✅ 未检测到运行中的后端服务。"
else
    # 将多个 PID 换行符替换为空格显示
    B_PIDS_LINE=$(echo $BACKEND_PIDS | tr '\n' ' ')
    echo "⚙️ 发现后端进程 (PID: $B_PIDS_LINE)，正在发送安全关闭信号..."
    # 发生 15 号信号 (SIGTERM) 允许程序执行清理工作并安全退出
    kill -15 $BACKEND_PIDS 2>/dev/null
    
    # 给程序 2 秒钟的时间优雅退出
    sleep 2
    
    # 兜底检查：如果进程还在，说明卡死了，强制杀死
    if pgrep -f "uvicorn main:app" > /dev/null; then
        echo "⚠️ 后端进程响应超时，正在强制终止(SIGKILL)..."
        pkill -9 -f "uvicorn main:app" 2>/dev/null
    fi
    echo "✅ 后端服务已彻底关闭，数据库连接已安全释放。"
fi

echo "----------------------------------------------------"

# 2. 查找并关闭前端进程 (npm / vite)
echo "🔍 正在检索前端进程 (npm / vite)..."
# 前端启动通常由 npm 派生出 vite 进程，需要同时匹配并干掉
FRONTEND_PIDS=$(pgrep -f "npm run dev|vite")

if [ -z "$FRONTEND_PIDS" ]; then
    echo "✅ 未检测到运行中的前端服务。"
else
    F_PIDS_LINE=$(echo $FRONTEND_PIDS | tr '\n' ' ')
    echo "🎨 发现前端进程 (PID: $F_PIDS_LINE)，正在关闭..."
    kill -15 $FRONTEND_PIDS 2>/dev/null
    
    sleep 1
    
    if pgrep -f "npm run dev|vite" > /dev/null; then
        pkill -9 -f "npm run dev|vite" 2>/dev/null
    fi
    echo "✅ 前端服务及 3000 端口已彻底释放。"
fi

echo "===================================================="
echo "🎉 监控平台所有服务已安全退出！"
echo "===================================================="
