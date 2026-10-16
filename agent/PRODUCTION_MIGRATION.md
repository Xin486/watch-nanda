# 192.168.1.24 生产迁移

核对日期：2026-10-16。仓库路径 `/home/test/Downloads/watch-nanda/monitor_platform`，数据库 `monitor_db`，MySQL 9.7.1。
节点 104 条；历史表为 InnoDB，元数据估计约 117 万条、数据约 13.4 GB，实际行数以数据库为准。
当前无 `servers.collection_mode` 列、无 `agent_credentials` 表。

## 变更范围

只需两个增量结构变更，迁移脚本会先检查是否存在，允许重跑：

```sql
ALTER TABLE servers
  ADD COLUMN collection_mode VARCHAR(10) NOT NULL DEFAULT 'ssh';

CREATE TABLE agent_credentials (
  server_id INT NOT NULL PRIMARY KEY,
  token_hash VARCHAR(64) NOT NULL,
  last_history_report_id VARCHAR(36) NULL
);
```

104 个已有节点保持 SSH，不改 ID、SSH 配置和历史记录。不会对 `server_stats` 做 ALTER、复制、清空或重建。
不要把用于全新数据库初始化的 `monitor_db.sql` 重新导入生产库。
脚本使用后端 `.env` / DATABASE_URL，并将 MySQL 元数据锁等待上限设置为 10 秒。
MySQL DDL 会隐式提交；若第二步失败，第一步可能已经生效，可排查后重跑，不依赖事务整体回滚。

## 执行顺序

1. 将合并后的 `feat/sse-realtime-push` 推送远程。生产机先确认工作区无业务修改，此时先不更新工作文件。
2. 暂停监控后端和前端，再使用 `git pull --ff-only origin feat/sse-realtime-push` 更新对应分支，避免 Vite 在更新过程中热加载半成品。当前发现两份 start.sh/Vite，监听 3000 和 3001，需确认并分别正常退出；不要误停 MySQL 容器。
3. 使用已有备份流程备份数据库结构及 `servers`、`alert_configs`、`email_configs`。这些备份包含配置和凭据，限制访问。历史数据无需为本次 DDL 单独导出 13.4 GB，但原有历史备份策略应保留。
4. 在新代码的 backend 目录执行检查与迁移，使用运行后端的 Python 环境：

```bash
cd /home/test/Downloads/watch-nanda/monitor_platform/backend
python3 migrate_agent.py --check
python3 migrate_agent.py
python3 migrate_agent.py --check
```

第一次应显示两个 missing；迁移完成后两个均为 present。只运行 --check 不会更改数据库。

5. 在数据库管理工具中核对：

```sql
SELECT collection_mode, COUNT(*) FROM servers GROUP BY collection_mode;
SHOW CREATE TABLE agent_credentials;
```

此时预计 `ssh = 104`，凭据表为空；除非期间管理员新增了节点或主动签发了 Token。
6. 启动一份新版监控服务，确认 `/api/servers` 正常、已有节点继续采集、历史图可读。
7. 配置节点可访问的 HTTPS 入口。当前 192.168.1.24 未监听 443，不能仅签发 Token 就开始节点上报。
8. 选一台节点按 [Agent 接入说明](README.md) 签发 Token、安装普通用户采集服务，确认上报、失联/恢复、历史周期后再逐台迁移。

## 回退

仅结构变更完成、尚未迁移节点时，可运行旧版服务，新增列和表保留。
已有节点切为 agent 后，若运行旧版 SSH 后端，它不会识别 agent 模式，可能重新开始 SSH 采集。
因此回退旧版本前应明确决定哪些节点回 SSH，并先停用对应 agent；不希望恢复 SSH 的节点暂时禁用监控。
一般优先在新版页面逐节点改回 SSH，而不是回退整个后端。
不要删除历史表或回灌旧库来回退这两个增量结构变更。

本文为已核实环境的执行方案；本次检查未运行生产 DDL、没有切换节点或重启服务。

## 生产运行配置

`deploy/production/` 提供此主机的 Nginx 和后端 systemd 配置。
部署后后端由 `watch-nanda-backend.service` 管理，前端构建结果复制到 `/var/www/watch-nanda`，由 `nginx.service` 在 3000 提供。
新版 `start.sh`/`stop.sh` 会识别本项目 systemd 服务，不再重复启动开发服务器。运维可使用 `bash start.sh --restart` 或 `systemctl restart watch-nanda-backend nginx`。
443 仅代理 `/api/agent/report`，不公开管理 API；管理页面仍为 `http://192.168.1.24:3000`，API 保持 7980。
内部 TLS 的公共 CA 为 `/etc/node-monitor/tls/ca.crt`，节点配置 `ca_file` 指向其可信副本。
CA 私钥应留在 root 的受限备份目录；节点无需任何 SSH 私钥或 TLS 客户端私钥。
