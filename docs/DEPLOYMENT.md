# 部署与升级

## 当前生产入口

生产主机为 `192.168.1.24`，仓库位于 `/home/test/Downloads/watch-nanda/monitor_platform`。最近一次只读核对及源码版本见 [项目上下文](PROJECT_CONTEXT.md#生产现场核对快照2026-10-19)，执行前仍需核对现场。

```text
HTTPS 443 → Nginx → 雷池 8088 → 回环 Nginx 18080
                                  ├→ /var/www/watch-nanda
                                  └→ 127.0.0.1:7980 FastAPI
HTTPS 9443 → Nginx → https://127.0.0.1:19443 雷池控制台
```

管理页面、API 和 SSE 使用同源 HTTPS，终端使用 WSS。管理入口及雷池控制台允许 `192.168.1.0/24`，包括 SD-WAN SNAT 网关 `192.168.1.199`。外部只开放 `/api/agent/report`。后端只监听回环地址，不再使用对外 HTTP 3000 入口。

## 配置来源

| 配置 | 当前来源与用途 |
| --- | --- |
| 后端服务 | `scripts/lifecycle.py` 按仓库位置、venv 和运行账户生成 `watch-nanda-backend.service`；不维护另一份静态服务模板 |
| 数据库及阈值 | 环境变量优先于 `backend/.env`，配置模板为 `backend/.env.example` |
| 生产 Nginx/WAF | 保留现场配置和 `# watch-nanda-managed-root: <仓库绝对路径>` 标记；基础模板 `deploy/production/nginx.conf` 不能覆盖现有 WAF 配置 |
| 雷池控制台代理 | `deploy/production/safeline-console.conf`；生产副本 `/etc/nginx/snippets/watch-nanda-safeline-console.conf` |
| TLS | `/etc/node-monitor/tls/`；节点只需受信任的 CA 公钥证书 |
| SSH 密码加密 | `/etc/node-monitor/ssh-credentials.key`，或 `SSH_CREDENTIAL_KEY_FILE` 指定的文件 |
| 网页运维口令摘要 | `/etc/node-monitor/ops-password.json`，或 `OPS_PASSWORD_FILE` 指定的文件 |

2026-10-19 生产后端仍使用 `/usr/bin/python3`；新式 `setup.sh` 会建立 `backend/.venv` 并更新服务。不要把现场旧依赖环境直接写死到新的服务文件。

## 标准升级

先检查当前分支、工作区差异、`systemctl cat watch-nanda-backend` 及 Nginx 的项目归属。备份数据库配置、节点配置、`.env`、独立加密密钥和现场代理配置；备份按受保护的运维流程保存，不提交 Git。保留既有历史数据备份策略。

在维护窗口使用当前部署所需的管理员账户：

1. 在现有版本目录运行 `sudo bash stop.sh`，确认只停止本项目。
2. 更新到已确认的分支/提交，保留现场 `.env` 和本地差异。
3. 执行以下初始化和启动命令：

```bash
sudo bash setup.sh --systemd
sudo bash start.sh
```

`setup.sh` 保留已有配置和数据库，按锁文件安装依赖、幂等补齐表/索引、执行增量迁移、构建前端，再生成服务。识别属于本项目的 Nginx 时更新静态目录并保留现有 WAF 配置；安装过程本身不会启动服务。依赖已满足时可加 `--skip-deps`，但不会跳过数据库检查和前端构建。

若本次更新包含 `agent/monitor_agent.py` 或 `agent/install_node.py`，应在开放网页部署功能前运行 `bash agent/build.sh`，将各目标架构的独立包与源码一起准备好。`setup.sh` 不自动构建 Agent 包，旧包不能通过当前源码哈希校验。节点防火墙出站规则和已有 Agent 重部署流程见 [Agent 说明](../agent/README.md#出站网络与目标机防火墙)。

已有库不能重新导入 `monitor_db.sql`，也不需要执行单独的建索引 SQL；索引由 `scripts/initialize.py` 根据现有结构补齐。首次部署、独立 HTTPS 模式及环境要求见 [README](../README.md)。

## 已有生产的小范围发布

若依赖、数据库结构和服务配置均未改变，可保留实际运行环境进行小范围发布：先确认生产工作区干净、无运行中的部署任务，保存当前提交、静态文件和 Agent 包的受保护备份；只读迁移检查通过后，将源码快进到已推送的提交。

使用锁文件对应的前端构建结果发布，保留旧哈希资源，最后原子替换首页。将经过清单及源码哈希校验、原生自检的 Agent 包和两个摘要侧文件一起替换，再重启 `watch-nanda-backend.service`。不需要改动时保留 Nginx/WAF、systemd 配置和依赖环境，不执行初始化或迁移写入。发布后验证 HTTPS、API、SSE、WSS 路由及 Agent 上报；记录可恢复的备份路径。回退时恢复原提交、首页与 Agent 包并重启后端，不回退或覆盖数据库。

## 迁移检查

使用实际运行后端的 Python 环境，在 `backend/` 中执行：

```bash
python3 migrate_agent.py --require-ready
```

使用 venv 时把 `python3` 换成 `.venv/bin/python`。`--check` 只打印状态，`--require-ready` 还会在缺结构时退出 2；两者都不修改数据库。检查项为：

- `servers.collection_mode`
- `agent_credentials`
- `servers.ssh_password_encrypted`
- `servers.email_alert_enabled`

确实缺失时，在备份及维护窗口内运行 `python3 migrate_agent.py`。该脚本幂等，不重建 `server_stats`，不重置节点 ID、已有采集方式或 Token。MySQL DDL 会隐式提交，部分失败后应排查并重跑，不能假定事务整体回滚。

## 发布验证与恢复

检查后端和 Nginx 服务状态、HTTPS 页面/API/SSE、WSS 运维入口，以及节点最近上报/采集时间；确认 7980 仅回环监听、管理访问策略和雷池链路未改变。前端发布保留旧哈希资源，最后替换首页，兼容已打开的页面。

服务日志使用 `journalctl -u watch-nanda-backend`。生产版本回退时恢复已确认兼容的源码、依赖、前端和服务/代理配置，保留现有新增数据库结构及历史。不能直接切到不识别 Agent 的老后端，否则可能重新对 Agent 节点进行 SSH 采集。

单节点切换与安装回滚见 [Agent 接入说明](../agent/README.md#试点及回退)。Agent 安装失败使用安装器的恢复流程；不要删除 release、`.rollback` 或 `.install-pending.json` 来强行重试。
