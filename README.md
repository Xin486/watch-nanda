# Node Monitor 服务器监控平台

集中监控 Linux 服务器的 CPU、内存和 GPU，支持 **Agent 主动上报**与 **SSH 密码采集**，提供实时看板、历史曲线、邮件告警和网页运维。

技术栈：Vue 3 / Vite / ECharts / FastAPI / MySQL。主线分支：`feat/sse-realtime-push`。

## 主要功能

| 功能 | 使用入口 |
| --- | --- |
| 实时监控、分组和筛选 | 节点总览；点击卡片查看详情 |
| 算力、显存和功耗展示 | 算力大屏 |
| CPU、内存和显存趋势 | 历史负载 |
| 持续失联告警与恢复通知 | 设置中配置邮件；节点铃铛控制提醒 |
| SSH 连接测试和交互终端 | 运维命令行 |
| Agent 安装与重部署 | 增加服务器、节点配置；数据清理页可选择批量重部署 |
| 节点管理与重名筛选 | 数据清理；按节点 ID 区分记录，删除同时清除对应历史 |
| Hermes 助手（可选） | 右下角悬浮窗；卡片图标可填入节点名，收起后任务继续 |

网页运维无需平台口令登录，访问范围由内网/WAF 策略控制；节点 SSH 仍需用户名、密码及主机指纹确认。站点名称、Agent 上报入口与网段规则、可选 Hermes 的开关/API/模型均可在“设置”配置。

## 快速开始

监控中心使用 Linux，运行环境为 Python 3.10–3.12、Node.js 18+、MySQL 8+。在仓库根目录执行，先将地址替换为本站 IP 或域名：

```bash
sudo bash setup.sh --host <本站IP或域名>
# 自选 HTTPS 端口并准备可选助手（首次安装默认关闭）
sudo bash setup.sh --host <本站IP或域名> --port 8443 --with-hermes
```

初始化会准备运行时、依赖、数据库、HTTPS 和本机架构 Agent 包，然后启动服务。默认在 root/systemd 环境安装自启服务；其他环境使用后台模式。

完成后访问脚本输出的 **HTTPS 地址**，并在浏览器信任本站 CA。在“设置”核对节点可访问的上报地址；如需助手，填写兼容 API 的地址、模型 ID 与密钥后开启。跨网络部署见 [模板部署](docs/PORTABLE_DEPLOYMENT.md)；已有数据库只做增量迁移，不重新导入 `monitor_db.sql`。

## 日常操作

在仓库根目录、使用安装时的账户执行；systemd 模式需相应管理权限：

```bash
bash start.sh            # 启动
bash start.sh --restart  # 重启，加载新配置
bash stop.sh             # 停止平台，保留数据库
sudo bash install-agent.sh  # 交互安装节点 Agent
```

数据库等配置在 `backend/.env`，模板见 [backend/.env.example](backend/.env.example)。环境变量优先；真实配置、密码和密钥不提交到 Git。

## 文档导航

| 要做什么 | 查看文档 |
| --- | --- |
| 安装、升级、备份或故障恢复 | [部署与升级](docs/DEPLOYMENT.md) |
| 接入节点、构建 Agent、签发 Token 或安装回滚 | [Agent 接入说明](agent/README.md) |
| 配置 Hermes 悬浮聊天与后台任务 | [Hermes 接入说明](docs/HERMES_CHAT.md) |
| 查找代码、理解采集/历史/SSE 及数据口径 | [项目架构](docs/PROJECT_CONTEXT.md) |

## 目录结构

```text
backend/            REST、SSE、采集调度和网页运维
frontend/           控制台、大屏与管理页面
agent/              节点采样、HTTPS 上报及安装器
scripts/            初始化、升级与启停编排
deploy/production/  Nginx 与雷池配置参考
docs/               架构、部署和 Hermes 说明
```

## 回归测试

在 Linux 仓库根目录、完成依赖准备后执行：

```bash
backend/.venv/bin/python -m pip install -r backend/requirements-test.txt
backend/.venv/bin/python -m unittest discover -s backend/tests -v
(cd frontend && node --test tests/*.test.mjs && npm run build)
```

后端测试使用临时 SQLite，SSH/SMTP 均模拟；systemd、权限和安装回滚需在 Linux 验证。MySQL 历史专项检查可在 `backend/` 中运行 `.venv/bin/python tests/check_history_mysql.py`，仅操作连接内临时表。
