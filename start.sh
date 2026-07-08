#!/bin/bash

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "===================================================="
echo "🚀 开始启动无 Redis 版服务器监控平台..."
echo "===================================================="

# 1. 启动 FastAPI 后端 (它会自动带起 APScheduler 采集任务)
echo "📦 正在启动核心服务 (API + 调度器)..."
cd $PROJECT_ROOT/backend
uvicorn main:app --host 0.0.0.0 --port 7980 > backend.log 2>&1 &
BACKEND_PID=$!

# 2. 启动 Vue 3 前端
echo "🎨 正在启动 Vue 3 前端大屏..."
cd $PROJECT_ROOT/frontend
npm run dev > frontend.log 2>&1 &
FRONTEND_PID=$!

echo "===================================================="
echo "🎉 监控平台极速启动完毕！"
echo "🖥️  访问地址: http://你的服务器IP:3000"
echo "===================================================="
echo "🛑 当前终端保持运行，按 [Ctrl + C] 一键安全关闭"

trap "echo -e '\n👋 安全关闭...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM EXIT

wait
