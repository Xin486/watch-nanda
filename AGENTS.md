# watch-nanda 项目前置提示词

本文件适用于整个仓库。开始任务先阅读本文件；详细架构、Git 进度和有日期的生产核对结果见 [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)。以实际代码、Git 差异和现场配置为准，不把历史记录当成实时状态。

## 项目定位与工作方式

- 这是南大服务器监控平台：监控 CPU、内存、NVIDIA GPU、功耗和进程，提供节点控制台、算力大屏、历史曲线、邮件提醒及网页 SSH 运维。
- 技术栈为 Vue 3 / Vite / ECharts / xterm.js，FastAPI / SQLAlchemy / APScheduler / Paramiko，以及 MySQL。当前没有 Redis 或 Celery；`backend/worker/` 是进程内线程池和调度器。
- 本地开发环境可以是 Windows，生产和 Agent 运行环境是 Linux/systemd。Python 支持范围按初始化脚本为 3.10–3.12，Node.js 为 18+。
- GitHub 默认分支为 `feat/sse-realtime-push`，保留该名称作为项目主线。开始修改前检查 `git status --short`、当前分支、近期 `git log` 和相关 `git diff`；以远端实际设置及现场 Git 状态为准，不把历史生产快照当成当前提交。
- 保留用户已有修改和有价值的分析、审计产物。最终报告统一放在 `outputs/reports/`，截图和运维审计保留在 `outputs/`，不加入业务提交。用户要求清理时，可删除已确认无运行/验证用途的临时输入、一次性脚本和缓存；不能仅因未跟踪就删除最终报告或审计记录。

## 项目结构与职责

```text
watch-nanda/
├── AGENTS.md                       # 项目前置提示词及持续维护规则
├── README.md                       # 使用、部署、功能说明
├── docs/PROJECT_CONTEXT.md         # 架构、改动进度与生产核对快照
├── docs/DEPLOYMENT.md              # 当前生产入口、升级、迁移检查及恢复
├── setup.sh                        # 初始化入口；依赖、数据库、迁移、构建
├── start.sh / stop.sh               # 调用生命周期管理器
├── install-agent.sh                # 中心侧交互安装 Agent 的入口
├── monitor_db.sql                  # 新库结构；不能用于覆盖已有生产库
├── backend/
│   ├── main.py                     # REST、Agent 接收、SSE、运维路由挂载
│   ├── core/
│   │   ├── config.py               # 环境变量/.env 配置与新鲜度阈值
│   │   ├── status.py               # REST/SSE/告警/历史共用状态计算
│   │   ├── history.py              # 历史查询、有效样本统计、北京时间日聚合
│   │   ├── agent.py                # 上报模型、Token 校验、防重放、入库
│   │   ├── agent_health.py         # Agent 最近有效上报的只读诊断
│   │   ├── ssh_credentials.py      # SSH 密码加密、解密与连接身份绑定
│   │   ├── ops_auth.py             # 运维口令、会话、同源与 HTTPS 校验
│   │   ├── operations.py           # SSH 检测、指纹信任、WSS 终端、部署任务
│   │   ├── ssh_profiles.py         # 常用 SSH 凭据加密文件及受认证 CRUD
│   │   └── agent_batch.py          # 批量重部署串行队列、凭据回退和节点日志
│   ├── models/database.py          # 数据库引擎与 Session
│   ├── models/models.py            # servers/server_stats/agent_credentials/
│   │                              # email_configs/alert_configs 五张表
│   ├── worker/tasks.py             # SSH 采集、去重队列、落历史、邮件调度
│   ├── migrate_agent.py            # 幂等增量迁移；--check/--require-ready 只读
│   ├── manage_agent.py             # 本机管理员签发/撤销 Agent Token
│   ├── manage_ssh.py               # SSH 密码加密密钥及节点凭据管理
│   ├── manage_ops.py               # 设置独立网页运维口令
│   ├── agent_job.py                # 网页部署的安装器子进程入口
│   ├── .env.example                # 配置模板；真实 .env 不入库
│   ├── requirements*.txt           # 运行/测试依赖
│   └── tests/                     # SQLite、模拟 SSH/SMTP 及脚本回归
├── frontend/
│   ├── src/main.js / App.vue       # 初始化与全局壳层
│   ├── src/router/index.js         # 页面路由与旧书签重定向
│   ├── src/api.js                  # 同源 HTTPS API 封装
│   ├── src/composables/            # useServers/useNodeAccess/useTheme/useOperatorSession
│   ├── src/views/                  # Dashboard、BigScreen、History、Settings、
│   │                              # Cleanup、NodeOperations、AgentDeploy、AgentRedeploy、
│   │                              # AgentBatchDeploy、SshCredentials
│   ├── src/components/             # OperatorGate、节点详情/新增、上报诊断、连接面板、
│   │                              # 运维布局、远程终端、铃铛、图标及主题开关
│   ├── src/utils/                  # 状态/指标、历史图表、主题、HTTPS 工具
│   ├── src/styles/                 # 主题变量与运维共用样式
│   ├── tests/*.test.mjs            # Node 内置测试运行器
│   ├── vite.config.js              # HTTPS 开发/预览、API 与 WebSocket 代理
│   └── package*.json               # 依赖与锁文件
├── agent/
│   ├── monitor_agent.py            # 节点本机采样与 HTTPS 主动上报
│   ├── install_node.py             # 节点部署会话、旧进程终止、覆盖准备/激活/回滚
│   ├── build.sh / build_bundle.py  # 含 Python 运行时的 Linux 独立包
│   ├── node-monitor-agent.service  # 低权限常驻服务
│   ├── config.example.json         # 不含真实 Token 的配置模板
│   └── README.md                   # 安装、协议、验证与回退
├── scripts/
│   ├── initialize.py              # 配置准备、数据库准备、增量结构初始化
│   ├── lifecycle.py               # systemd/后台模式识别、归属检查、健康检查
│   └── install_agent.py           # 中心 SSH 安装编排、包校验及回滚
└── deploy/production/             # 基础 Nginx、雷池控制台配置
```

## 必须保持的业务约定

- SSH 每 60 秒采集，Agent 默认每 300 秒上报；历史每 10 分钟保存，SSE 每 10 秒只读推送。页面刷新和 SSE 订阅不能触发全量 SSH。
- 实时指标在 `servers.latest_status_data`；历史在 `server_stats`。`status.py` 统一计算 `online/offline/stale/pending`，前端 `serverStatus.js` 继续计算断网期间的数据年龄。
- SSH 默认 180 秒过期；Agent 默认 720 秒过期、960 秒失联，实际配置可覆盖。Agent 采样时间与服务端接收时间分开使用，重复/乱序上报不能刷新在线时间。
- `useServers` 是共享单例：SSE 为主、REST 首次加载和断流兜底。保留乱序响应保护、请求取消、订阅清理和编辑表单保护。
- 前端经同源 HTTPS 访问 API，开发/预览也通过 Vite 代理；后端不再维护旧的通配 CORS 配置。后端通过启动脚本或 Uvicorn CLI 启动，只监听回环地址，不提供 `python main.py` 的对外调试入口。
- 历史 1–7 天返回原始快照，超过 7 天按北京时间自然日聚合。缺失、离线、过期和无 GPU 保留缺口，不伪造为 0；平均值按有效原始样本加权。显存利用率、GPU 计算利用率和功率是不同指标。
- 已有节点运维页是 `/operations` 或 `/nodes/:id/operations`，只做连接检查和终端。首次部署页为 `/servers/new/:id/agent`；已有 Agent 的上报检查区域始终显示「重新部署 Agent」入口（检测前、正常或异常均可进入），提供 `/nodes/:id/agent/redeploy` 独立修复页，固定目标、复用运维认证与部署逻辑，不带终端；旧 `/agents`、`/nodes/:id/agent` 仍重定向到运维。
- 手动 SSH 凭据在验证成功后按页面请求加密同步；失败不能覆盖已存凭据。SSH 节点先进入待采集并重新排队，采集成功才在线；Agent 节点保留采集模式和 Token。配置变化后丢弃旧采集结果。
- SSH 未知或变化指纹默认拒绝。SSH 测试/终端遇到变化时，连接面板弹出目标地址、原/新 SHA256 指纹；用户明确允许后才调用受认证 `trust-host`，保存后继续原测试或终端，取消不写入。替换请求包含 `previous_fingerprint`，前端同时绑定 `expected_host/expected_port`；后端重新握手校验新指纹，并在锁内复核已存旧指纹、节点地址及部署空闲状态。仅替换该端点记录（兼容哈希主机名和别名），保留其他主机，600 权限备份并原子写入 known_hosts。地址/凭据变化或退出登录撤销待确认；批量队列遇到变化也等待用户核验原/新指纹，禁止自动信任或继续试密码。确认不自动开始单节点 Agent 安装。
- 新增节点和节点配置页在 Agent/SSH 两种模式下都显示 SSH 用户名与端口（默认 22），Agent 模式用于后续部署和运维；新增时两种模式均校验端口为 1–65535 整数。只有 SSH 采集模式显示并提交采集密码，切换模式不重置已填端口；不能把 SSH 连接端口与 Agent HTTPS 上报端口混为一谈。
- Agent 健康检查只查平台上报状态，不连接 SSH、不重启节点、不轮换 Token。节点邮件铃铛同时控制离线/恢复提醒，并受全局开关控制。
- 重部署使用受运维认证保护的 `/api/ops/nodes/:id/redeploy`，仅适用于已有 Agent 节点；必须主动点击才开始，保留节点 ID、分组和历史，更新 Token/程序，失败恢复原凭据与服务。提交时及签发凭据前均校验采集模式，不能误把已切回 SSH 的节点重新切换。
- 数据清理页支持自动选择全部 Agent 采集节点或手动勾选，进入 `/agents/batch-redeploy` 独立页；进入页面不启动安装，运维登录后主动点击才创建 `/api/ops/agent-batches` 批次。批量队列串行执行并共享中心最多 2 个/同节点最多 1 个的任务名额；逐次执行前复查采集方式和地址，不能误切 SSH 节点。缺凭据/认证失败/未知指纹的节点单独等待，后续节点继续；可手动补密、明确确认指纹、重试所有等待凭据节点，或停止未开始的节点，不能中断正在部署/回滚的节点。
- 数据清理页按节点 ID 保留独立记录，不能按名称或 IP 去重；`utils/cleanupNodes.js` 支持名称/ID/地址/端口/分组/采集方式搜索及仅看重名，重名计数基于完整列表，重名节点相邻排列。表格显示端口、采集方式和最近成功采集，清理/删除确认必须包含 ID、连接地址及采集方式；重名不代表可安全删除，不自动选定或合并重复节点。勾选框仅用于 Agent 重部署，SSH 节点仍可逐条清理或删除。
- 常用凭据页 `/settings/ssh-credentials` 从设置页进入，API `/api/ops/ssh-profiles` 受运维认证保护；密码以现有独立 Fernet 密钥加密，绑定凭据 ID 与用户名，不回显、不放浏览器存储、不加入 Git。按优先级（小的先）尝试启用的常用凭据，再尝试节点已存凭据，同用户名最多自动认证 3 次；网络/主机指纹错误不继续试密码。全部失败等待手动输入；手动输入仅在内存中用于当前节点重试，不自动保存到常用列表或节点。
- 批次元数据、日志与手动输入保存在当前单进程内，最多保留最近 5 个批次；服务重启后不能承诺恢复队列，应先核验节点状态再重新选择。进度轮询只读，日志按节点按需读取；退出登录和卸载页面清理浏览器密码及过期响应。
- 安装器检查并验证节点到上报入口的出站 TCP/TLS；UFW/firewalld 仅添加目的 IP/端口的出站规则及持久配置，不开放入站、不关闭或重载防火墙、不覆盖其他规则。`--check-only` 只检查；部署失败恢复本次新增规则，无法确认远端恢复时保留规则及安装记录供核对。自定义 nftables/iptables 或上游 ACL 仍阻断时必须失败提示，不能伪报成功。
- 安装包必须包含当前 `install_node.py`；修改安装器后重新构建匹配的 Linux 独立包。成功判定仍要求以 `node-monitor` 服务账户完成真实 HTTPS 上报并确认常驻服务运行，root 的 TLS 检查通过不能单独当作部署成功。
- 首次部署和重部署统一覆盖，不因旧 Agent 健康跳过；`--check-only` 保持只读。中心通过带心跳的 SSH 会话持有 `/run/watch-nanda-agent-session.lock` 直至结束，会话 EOF 或 45 秒无心跳释放；节点单步仍用原操作锁并核对会话归属，失效会话拒绝写入。先准备新版、备份当前状态、确认项目旧服务及遗留进程 TERM/KILL 后完全停止并移除旧 unit/config/current，才允许中心签发 Token；失败恢复本次备份，成功后清理旧 releases、旧源码和 venv。旧 `.install-pending.json` 在独占会话内自动归档到 `.install-history/` 后接管，不要求人工清锁；仍在执行的部署不能被覆盖。不能杀死非本项目进程；D 状态/停止超时必须明确失败，不能继续轮换 Token 或自动重启整机/GPU。子命令超时后的进程回收也必须有时限；无法确认恢复时保留凭据、网络规则及受保护记录。
- 管理页共用 `styles/theme.css`、`useTheme` 和主题工具；图表切换主题保留缩放和图例选择。远程终端保持深色；算力大屏保留其现有独立展示设计。
- 后端调度、运维会话、部署任务与终端注册表在单进程内；不要直接增加多个 Uvicorn worker，否则会重复调度且状态不共享。部署任务中心并行上限为 2，同节点最多 1 个。

## 生产环境与部署边界

- 主机：`192.168.1.24`；项目目录：`/home/test/Downloads/watch-nanda/monitor_platform`。2026-10-19 只读核对使用 root 登录；密码仅使用用户当次提供的凭据，不写入文档、命令文件、日志或 Git。
- 平台 `https://192.168.1.24`；雷池控制台 `https://192.168.1.24:9443`。管理入口允许 `192.168.1.0/24`，包括 SD-WAN SNAT 网关 `.199`，不得再次单独拒绝该网关。外部入口只开放 Agent 上报路径。
- 生产流量为 Nginx HTTPS 443 → 雷池 8088 → 回环 Nginx 18080 → 静态前端或回环 FastAPI 7980。前端发布目录为 `/var/www/watch-nanda`；后端由 `watch-nanda-backend.service` 管理。
- 2026-10-19 实际后端使用 `/usr/bin/python3`，尚无 `backend/.venv`；`setup.sh` 新式部署会创建 venv。升级前先核对 systemd 实际 ExecStart，不要假定仓库模板等于现场。
- systemd 服务由 `scripts/lifecycle.py` 生成，没有独立静态后端服务模板；表和索引由 `scripts/initialize.py` 幂等补齐。升级与恢复按 `docs/DEPLOYMENT.md` 执行；依赖、表结构与服务配置未改变时，可按其中的小范围发布流程保留现场环境，按改动范围更新源码、静态资源和匹配的 Agent 包，必要时重启后端；仅前端/文档变化不重启后端。
- 7980 仅监听 `127.0.0.1`，旧 80/3000 未开放；前端/API/SSE 同源 HTTPS，终端使用 WSS。回环代理和本机健康检查可用 HTTP。
- `deploy/production/nginx.conf` 是基础配置，不是当前完整 WAF 配置。保留现场 `watch-nanda-managed-root` 归属标记、WAF 代理、访问策略、SSE 无缓冲和 WebSocket Upgrade；不得直接用基础模板覆盖现场。
- 雷池控制台代理为 `/etc/nginx/snippets/watch-nanda-safeline-console.conf` → `https://127.0.0.1:19443`；证书与 CA 在 `/etc/node-monitor/tls/`。
- MySQL 库为 `monitor_db`。已有库使用 `migrate_agent.py` 增量迁移，不重新导入 `monitor_db.sql`，不重建历史表，不删除数据卷，保留节点 ID 和历史记录。
- 2026-10-21 开机恢复核对：生产 MySQL 为 Docker 容器 `mysql`，数据 `/data2/mysqldata` 挂载到 `/var/lib/mysql`，后端连接 `127.0.0.1:3306`。旧容器 `mysql_nfs_backup` 使用另一份数据且也配置宿主 3306，已停止并设为 `restart=no`；保留其数据，不能与生产库同时启动争抢端口。开机后先核对端口归属、原挂载、容器网络及 `monitor_db` 可读，再启动后端；`Unknown database` 不能直接当成需要新建库。恢复步骤见 `docs/DEPLOYMENT.md`。
- SSH 只用密码与严格主机指纹验证，禁用私钥和 ssh-agent 回退。密码使用 Fernet；默认密钥 `/etc/node-monitor/ssh-credentials.key`。运维口令为独立 PBKDF2 摘要，默认 `/etc/node-monitor/ops-password.json`，不跟随系统密码变化。常用 SSH 凭据文件默认 `/etc/node-monitor/ssh-profiles.json`，可用 `SSH_PROFILES_FILE` 覆盖，权限 600、归属服务用户，须与独立加密密钥一起受保护备份。
- Agent 数据库只存 Token 摘要；节点配置含真实 Token，不入库、不输出到聊天。Agent 只上报指标，无远程命令或自动更新通道；网页部署优先要求独立运行包，不能因缺包静默改用系统 Python 安装。
- 生产操作按用户授权范围执行。项目理解、检查和文档维护本身不意味着部署、重启、运行迁移、轮换凭据或批量改节点；实际发布前核对现场差异与恢复办法。

## 苏州实例

- 2026-10-23 核对的苏州主机为 `10.168.100.150`，目录 `/home/test/watch-tower/watch-nanda`，仓库归 root 所有，使用 test 登录后通过 sudo 管理。此实例独立于南京的 `192.168.1.24`，不要套用南京的 WAF、数据库容器或证书。
- 苏州采用 `watch-nanda-backend.service` 和 `watch-nanda-frontend.service`：后端 `127.0.0.1:7980`，Vite preview 提供同源 HTTPS `https://10.168.100.150:3000`。启动健康检查需设置 `WATCH_NANDA_HTTPS_HOST=10.168.100.150`；具体命令见 `docs/DEPLOYMENT.md`。systemd 的 `WorkingDirectory=` 使用路径值，不能复用 ExecStart 参数的双引号语法；真实 Linux 解析器验证覆盖含空格和 `%` 的路径。
- 实际后端为项目 `backend/.venv`，基于 `/root/.pyenv/versions/3.12.13/bin/python3.12`；Node 位于 `/root/.nvm/versions/node/v24.18.0/bin`。系统默认 Python 3.14 不用于本项目初始化，升级前重新核对可执行文件。
- 数据库复用 `mysql-app` 的 `monitor_db`，原挂载 `/home/test/watch-tower/mysqldata/mysql_data` → `/var/lib/mysql`，保留 `.env` 中的连接配置；只做增量迁移和索引补齐。该机已有 Redis/PostgreSQL 容器不属于当前平台依赖，不能随平台启停而删除或重建。
- 苏州独立 CA、服务器证书及私钥在 `/etc/node-monitor/tls/`，客户端需信任苏州 CA；网页运维口令仍需管理员单独设置。由旧版私钥采集升级的节点需补充 SSH 密码，不自动复用私钥或把中心登录密码当作节点密码。启用苏州 Agent 部署前需完成上报地址及独立包适配，现有网页安装器仍按南京地址选路；命令行安装需显式指定苏州 URL 和 CA。

## 验证方式

按变更范围选择验证，不为纯文档改动修改业务代码或启动生产服务：

```bash
# 后端：先在隔离环境安装 backend/requirements-test.txt；测试使用 SQLite/模拟连接
python3 -m unittest discover -s backend/tests -v

# 前端：在 frontend/ 下执行，使用已有锁文件安装依赖
node --test tests/*.test.mjs
npm run build

# 生产结构只读检查：在实际后端 Python 环境与 backend/ 目录下执行
python3 migrate_agent.py --check
```

Linux 专用的 systemd、权限、Shell、Agent 安装/回滚检查需 Linux 环境，Windows 上的通过结果不能替代它们。MySQL 专项脚本为 `backend/tests/check_history_mysql.py`，只操作连接内临时表，仍应先确认所连环境和执行范围。

## 每次改动后的前置提示词检查（必须执行）

每次完成一轮修改，在交付前查看最终差异，逐项判断：

1. 是否新增、删除、移动目录、模块、页面、路由、脚本或测试入口？
2. 是否改变技术栈、依赖环境、配置项、接口约定、数据模型、采集时序、状态计算或指标口径？
3. 是否改变登录/凭据、网络入口、WAF、证书、部署路径、服务管理、发布/回退方式？
4. 是否使本文件、README、架构说明或迁移说明中的描述过时？

任一项成立，就在同一轮修改中同步更新本文件相关段落，并按需更新 `docs/PROJECT_CONTEXT.md`、`README.md` 或 Agent 文档；删除失效描述，避免只追加互相矛盾的说明。生产快照必须注明核对日期，不能只根据本地改动宣布生产已更新。

若仅修正文案、样式或内部实现且上述约定均未改变，可不改本文件，但必须完成检查。在最终交付中简短说明“前置提示词已同步”或“已检查，前置提示词无需更新”，并列出实际验证和未验证部分。不要为了标记检查而每次改写日期、提交号或易变节点数量，也不要把本文件变成逐提交流水账。
