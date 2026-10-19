# 项目架构与现有改动进度

本次梳理日期：2026-10-19（Asia/Shanghai）。依据为本地源码、Git 历史、既有部署记录及生产只读检查。后续开发规则见仓库根目录 [AGENTS.md](../AGENTS.md)。本文中的提交和节点数量是核对快照，不是长期固定值。

## 整体架构

平台同时支持两种采集方式：中心用 SSH 密码采集旧节点，节点 Agent 用 HTTPS 主动上报。两种方式写入相同的实时缓存，统一向页面、状态判断、告警和历史采样提供数据。

```text
SSH 节点 ← 中心 Paramiko / 60 秒任务 ─┐
                                    ├→ servers.latest_status_data
Agent 节点 → HTTPS /api/agent/report ─┘          │
                       ┌──────────────────────┼─────────────────────┐
                       ↓                      ↓                     ↓
                REST / 10 秒 SSE       10 分钟历史快照         1 分钟邮件巡检
                       ↓                      ↓
                useServers 共享状态      server_stats
                       ↓                      ↓
             控制台/节点详情/算力大屏    history.py → 历史图表
```

标准生命周期入口负责启动前的迁移就绪检查；FastAPI startup 自身启动 APScheduler 并提交首轮 SSH 任务，不自动迁移数据库。线程池处理网络采集，数据库只用于读取配置和短事务写回。队列去重、配置比对和行锁防止重复采集及旧配置结果覆盖新配置。

Agent 默认 300 秒采样上报，加随机错峰；校验单节点 Token、时间窗口、报告 ID、报告顺序、请求体大小和指标范围。Token 不能修改节点配置，Agent 不接受远程指令。在线按接收时间判断，历史按有效报告采样时间记录，并防止同报告重复落历史。

REST 和 SSE 都调用 `core/status.py`。前端 `useServers` 处理共享 SSE 连接、首次 REST 加载、断流兜底、乱序保护和每秒计算数据年龄，避免断流后页面一直显示在线。

## 业务入口与代码定位

| 能力 | 前端主要位置 | 后端主要位置 |
| --- | --- | --- |
| 节点卡片、分组、筛选、实时指标 | `Dashboard.vue`、`useServers.js`、`dashboardMetrics.js` | `main.py`、`core/status.py`、`worker/tasks.py` |
| 算力大屏、功耗排行、GPU 分组矩阵、行情 | `BigScreen.vue`、`RingChart.vue` | 服务器列表/SSE、`/api/market/kline` |
| 新增、编辑、节点详情、提醒铃铛 | `AddServerModal.vue`、`NodeDetailModal.vue`、`NodeAlertToggle.vue` | 节点 CRUD、notifications API、SSH 凭据模块 |
| Agent 上报诊断 | `AgentHealthPanel.vue` | `core/agent_health.py`、agent-health API |
| 历史曲线、均值/峰值、数据清理 | `History.vue`、`Cleanup.vue`、`historyChart.js` | `core/history.py`、历史查询/删除 API |
| SMTP 与告警规则 | `Settings.vue` | 配置 API、`worker/tasks.py` |
| 已有节点 SSH 检测和交互终端 | `NodeOperations.vue`、`useNodeAccess.js`、`RemoteTerminal.vue` | `core/operations.py`、`core/ops_auth.py` |
| 新节点 Agent 部署 | `AgentDeploy.vue`、连接/运维布局组件 | 部署任务 → `agent_job.py` → `scripts/install_agent.py` → `agent/install_node.py` |
| 已有 Agent 重部署 | `AgentHealthPanel.vue` → `AgentRedeploy.vue`（复用部署界面） | 受认证 redeploy API、Agent 模式检查、既有安装器和失败回滚 |
| 管理页主题与图表配色 | `useTheme.js`、`utils/theme.js`、`styles/theme.css` | 不改变采集和数据接口 |
| 初始化、启停与部署归属检查 | 根目录 Shell 入口 | `scripts/initialize.py`、`scripts/lifecycle.py`、`deploy/production/` |

前端源码路径均相对 `frontend/src/`，后端路径相对 `backend/`。完整目录图见 [AGENTS.md](../AGENTS.md#项目结构与职责)。

## 数据模型与关键口径

- `servers`：节点身份、分组、启用状态、采集方式、加密 SSH 密码、提醒开关、实时 JSON 缓存与状态/告警时间。
- `server_stats`：每节点历史快照，CPU/内存/GPU 数据与采样时状态；生产有 `(server_id, timestamp)` 复合索引。
- `agent_credentials`：节点 Token 的 SHA-256 摘要和已落历史的报告 ID。
- `email_configs` / `alert_configs`：SMTP 配置和全局提醒规则。SMTP 密码读取只返回是否已设置，保存省略密码表示保留原值。
- 内部时间以 UTC 为基础；历史超过 7 天按北京时间自然日聚合。显存曲线是 GPU 显存百分比的有效值平均，与 GPU 计算利用率、功率和一次性“任意一张卡超过阈值”分析不同。
- 离线、过期、缺失和非法样本不作为 0 参与均值；历史摘要按有效原始样本计数加权，峰值取原始有效样本峰值。
- 节点 ID 是关联历史和凭据的身份；同 IP 可对应不同端口/节点，不按共享 IP 擅自合并。

## Git 已完成的演进

整理开始时的基线分支为 `feat/sse-realtime-push`，本地、远程跟踪引用和生产 HEAD 均为 `45010f3`。这是发布前的历史基线；当前版本以 `git log`、远程分支和生产现场为准。以下按功能阶段归纳，并非待实现清单。

| 阶段 | 代表提交 | 已落地内容 |
| --- | --- | --- |
| 早期基础与 GPU 展示 | `97e9582`、`cc057ae`、`b270ca9`、`ce02f8a` | 前后端结构、环境配置、GPU 功率解析、TDP 兜底与大屏功耗矩阵 |
| 9 月实时链路 | `c9d1c69`、`5acc142`、`7173668` | SSE、首次加载与 REST 兜底、采集并发、去重和数据新鲜度 |
| 9 月历史查询 | `eca1582` | MySQL 端 JSON 提取、北京时间聚合、有效样本统计、缺口和交互保护 |
| 10 月 16 日 Agent 与初始化 | `f73ffff`、`26f1e56`、`01b9489`、`f62a65c` | Agent 协议/迁移、幂等初始化与启停、SSH 安装编排、独立运行包和回滚 |
| 10 月 16 日凭据与运维 | `fbc7c1d`、`ea44b06`、`453ff25`、`9e830e8` | 密码 SSH、进程采集、节点提醒、运维认证、WSS 终端和受限部署任务 |
| 10 月 17 日展示和入口 | `a295890`、`e75841e`、`20086bc`、`1533131`、`cc39dd2` | 控制台重排、移动适配、全站 HTTPS、恢复 SD-WAN 平台/雷池控制台访问 |
| 10 月 18 日入口拆分 | `198ee04` | 已有节点运维与新增节点 Agent 部署分开；旧书签重定向 |
| 10 月 19 日凭据同步 | `158013e`、`c549578` | 成功验证后同步凭据、清理失败状态、重新采集及页面提示 |
| 10 月 19 日上报检查 | `5f25b71` | 节点配置增加 Agent 最近上报的只读健康检查 |
| 10 月 19 日主题 | `dc9e1dc`、`15d9aae`、`45010f3` | 明暗主题、设置图标、管理页/节点弹窗/历史图表统一配色与状态保持 |

历史提交进度以 Git 为准。当前维护说明只保留仍适用的结构、约定与部署流程，不把一次性安装状态作为操作步骤。

## 生产现场核对快照（2026-10-19）

| 项目 | 只读核对结果 |
| --- | --- |
| 主机与仓库 | `192.168.1.24`，`/home/test/Downloads/watch-nanda/monitor_platform` |
| 分支、提交、工作区 | `feat/sse-realtime-push` / `45010f3`，工作区干净 |
| 服务 | `watch-nanda-backend.service`、`nginx.service` 均 active/running |
| 实际后端命令 | `/usr/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 7980 --no-server-header` |
| 后端依赖环境 | 尚无 `backend/.venv/bin/python`；不能直接照新式 venv 部署假定现场 |
| 平台入口 | Nginx 443 → 雷池 8088 → `127.0.0.1:18080` → 静态目录/API |
| 后端与静态文件 | `127.0.0.1:7980`；`/var/www/watch-nanda/index.html` 存在 |
| 雷池控制台 | `192.168.1.24:9443` → `https://127.0.0.1:19443` |
| 端口 | 443、9443 在监听；18080/7980 仅回环；80/3000 无监听 |
| 访问策略 | Nginx 管理入口及雷池控制台允许 `192.168.1.0/24`，未单独拒绝 `.199` |
| 数据库 | MySQL `9.7.1`，五张业务表均存在 |
| 迁移 | collection_mode、agent_credentials、ssh_password_encrypted、email_alert_enabled 四项均 present |
| 节点 | 102 个：72 Agent、30 SSH；API 检查瞬间 101 online、1 offline |
| 历史索引 | server_id 单列、timestamp 单列和 `(server_id, timestamp)` 复合索引存在 |
| Agent 包 | 生产 `agent/dist/` 有 `node-monitor-agent-linux-x86_64.tar.gz` |

生产检查仅执行 Git/服务/监听/代理配置读取、迁移 `--check`、表结构与节点分组计数查询以及本机 `/api/servers` GET。未更新生产工作文件、执行 DDL、重启服务、安装节点或修改凭据；未扫描/计数大型历史表。节点状态会随采集变化，不能用这张快照承诺后续在线数量。源码 HEAD 一致和服务运行也不等于逐功能端到端验收。

## 维护资料的分工

- [README](../README.md)：使用、功能和初始化；[部署说明](DEPLOYMENT.md)：当前生产入口、升级和恢复；[Agent 说明](../agent/README.md)：节点接入、Token 和安装回滚。已完成首次迁移的旧操作方案不再单独保留。
- 后端服务由 `scripts/lifecycle.py` 生成；初始化同时负责索引补齐。基础 Nginx 模板仍用于 HTTPS 配置参考和服务归属识别，不能删除或覆盖现场完整 WAF 配置。
- 已移除未使用的跨域配置、对外调试启动入口及安装器旧密钥占位参数；前端保持同源代理，SSH 保持密码认证和严格指纹校验。
- `outputs/reports/` 保存最终分析报告，`outputs/` 的截图和部署审计只作有日期的历史证据，不是当前操作指南，且不进入业务提交。一次性 Git bundle、重复数据库依赖和已生成报告的临时输入/脚本可清理。
- `--source` Agent 安装、旧书签重定向、Nginx 归属识别和现有数据库兼容字段仍被代码或测试使用，继续保留。不能把“较早引入”直接等同于“无用”。
- Agent 安装的当前源码还检查 UFW/firewalld 并管理仅到上报地址/端口的出站规则，保留原有策略和回滚记录；最终成功仍以服务账户真实上报为准。重部署 API 仅接受已有 Agent 节点，不改变节点 ID 和历史。发布前必须重新构建匹配安装器的独立包；每次发布的提交、包摘要、备份路径和验收结果记录在 `outputs/` 的发布审计中。
- 验证命令见 [AGENTS.md](../AGENTS.md#验证方式)，结果应按每轮实际运行情况报告；有日期的生产快照不能替代本轮代码的回归验证。
