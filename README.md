# Node Monitor 服务器监控平台

支持节点主动通过 HTTPS 上报，无需 SSH 免密密钥。新节点默认每 5 分钟上报、每 10 分钟保存历史；已有节点继续 SSH 采集，可逐台迁移。
已有库通过初始化脚本增量升级。生产升级见 [部署说明](docs/DEPLOYMENT.md)，节点接入、Token 签发和回退见 [Agent 接入说明](agent/README.md)。

通过节点 HTTPS 主动上报或已有 SSH 采集获取 CPU / 内存 / GPU 数据，
提供控制台卡片、算力大屏、历史曲线与离线邮件告警。

数据清理页可按名称、节点 ID、IP/端口、分组或采集方式搜索，并勾选「仅看重名节点」集中查看重复名称。每条记录显示 ID、连接地址、采集方式及最近成功采集；删除前核对确认中的具体 ID。同名或同地址不会自动合并，删除会销毁该 ID 的历史数据；表格勾选框用于 Agent 批量重部署，SSH 节点也可以通过每行按钮单独删除。

## 技术栈

| 组件 | 技术 | 端口 |
|------|------|------|
| 对外入口 | Nginx HTTPS → 雷池 WAF → 平台 | 443 |
| 前端 | Vue 3 + Vite（生产环境由 Nginx 托管） | 独立模式 HTTPS 3000 |
| 后端 | FastAPI + APScheduler + Paramiko（仅本机监听） | 127.0.0.1:7980 |
| 数据库 | MySQL（库名 `monitor_db`） | 3306 |

## 部署步骤

### 方式一：一键初始化（推荐）

要求机器已安装 Python 3.10~3.12（含 venv）、Node.js 18+ 和 npm；自动创建本地数据库时还需要 Docker，然后：

```bash
bash setup.sh    # 创建独立 venv、安装依赖、准备数据库、迁移并构建前端
bash start.sh    # 后台启动；重复执行不会新增实例
```

`setup.sh` 可重复执行，保留已有 `.env` 和数据；只有新建 `.env` 时才生成随机数据库密码，且不打印密码。
已有本地/远程 MySQL 8+ 均直接连接，不需要 Docker 或 mysql 命令行客户端；仅无本地数据库时自动使用 Docker 创建 MySQL，默认只绑定回环地址。
不会删除或重建原有数据卷，也不会用 `--force` 忽略 SQL 错误。

Python 依赖安装到 `backend/.venv`，前端使用锁文件执行 `npm ci`。可用 `PIP_INDEX_URL` 指定镜像。

生产服务托管及升级：

先按 [部署说明](docs/DEPLOYMENT.md) 核对现场服务、备份配置及数据库，再在维护窗口升级：

```bash
bash stop.sh
sudo bash setup.sh --systemd
sudo bash start.sh
```

已经安装本项目 systemd 服务时，setup 会自动识别该模式（需 root）。安装时要求项目停止，防止替换正在使用的依赖。
首次安装 systemd 模式默认创建后端和前端预览服务；在当前 192.168.1.24 上会复用仅属于本项目的 Nginx，并更新静态前端目录。
不会覆盖其他项目的同名服务，也不会接管共享 Nginx。通过 `WATCH_NANDA_SERVICE_USER` 可指定运行账户，默认 root 以兼容现有 SSH 节点；普通账户需要对应的文件、数据库和采集权限。
初始化不会创建 SSH 免密密钥。节点 Agent 的 HTTPS 入口及内部 CA 接入步骤见 [Agent 接入说明](agent/README.md)。
监控中心初始化后，可先用 `bash agent/build.sh` 构建含运行时的独立 Agent 包，再运行 `sudo bash install-agent.sh` 输入目标 IP、SSH 用户和密码安装；节点无需 Python/pip，只检查可加 `--check-only`。
脚本复用已有节点并保留历史，不创建 SSH 免密密钥；192.168.1.* 目标默认用内网 HTTPS，其余默认用 114.212.165.149:59443，详见 Agent 接入说明。
`--skip-deps` 可跳过已安装依赖的更新，仍执行数据库校验、迁移和前端构建。

### 方式二：手动部署

#### 1. 安装依赖

```bash
# 以下命令均在仓库根目录执行
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
(cd frontend && npm ci)
```

#### 2. 配置数据库连接

```bash
cp backend/.env.example backend/.env
chmod 600 backend/.env
vim backend/.env    # 填写 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
```

`.env` 说明：

- 已被 `.gitignore` 忽略，不会提交到版本库（密码安全）。
- 环境变量优先级高于 `.env` 文件（例如 docker 部署可直接 `export DATABASE_URL=...` 整体覆盖）。
- 密码支持特殊字符，无需手动转义。

#### 3. 初始化数据库

```bash
backend/.venv/bin/python scripts/initialize.py
(cd frontend && npm run build)
```

### 配置 SSH 用户名密码（后端 → 被监控服务器）

SSH 采集只使用密码认证，禁用本地私钥和 ssh-agent。首次接入必须由管理员核验目标主机指纹并加入运行后端账户的 `~/.ssh/known_hosts`，未知或变化的指纹会拒绝连接。

```bash
cd backend
sudo python3 manage_ssh.py --init-key
sudo python3 manage_ssh.py --server-id <节点ID> --user test
```

命令交互输入密码，验证连接成功后加密保存，不接受命令行密码。已有密钥文件不会被覆盖。也可以使用 HTTPS 页面编辑 SSH 密码；HTTP 页面禁止提交密码，留空保留已存密码，修改地址、端口或用户会清除旧密码。切换 Agent 后，可保留已配置的密码供受保护的网页运维使用；Agent 采集不会使用 SSH。错误密码仅尝试一次，更新密码后才重新尝试。

生产内网页面使用 `https://192.168.1.24`（需信任内部 CA）。该 HTTPS 管理界面仅允许 `192.168.1.0/24`，公网入口仍只开放 Agent 上报路径。密码使用 SSH/TLS 加密传输、Fernet 加密保存；加密密钥默认 `/etc/node-monitor/ssh-credentials.key`，权限 600，可通过 `SSH_CREDENTIAL_KEY_FILE` 指定其他安全路径。请独立备份该密钥，丢失后需重新输入所有 SSH 密码。

### 启动 / 停止

```bash
bash start.sh           # 自动识别本项目 systemd 服务；否则启动后台实例
bash start.sh --restart # 加载新配置/版本
bash stop.sh            # 只停止本项目，保留 MySQL 和其他项目
```

生产环境访问：`https://192.168.1.24`。页面、API、SSE 使用同源 HTTPS，网页终端使用 WSS；旧 HTTP 3000 入口已关闭。雷池控制台为 `https://192.168.1.24:9443`。

监控平台和雷池控制台均允许 `192.168.1.0/24`，包括 SD-WAN 的 SNAT 网关 `192.168.1.199`；不得单独拒绝该网关，否则 SD-WAN 页面及 API 会返回 403。雷池控制台仍要求管理员登录，其他来源默认拒绝。

现有雷池控制台的代理配置保存在 `deploy/production/safeline-console.conf`，生产副本为 `/etc/nginx/snippets/watch-nanda-safeline-console.conf`，由现有 Nginx 的 `http` 块引入。此配置要求雷池容器仅发布到 `127.0.0.1:19443`，并依赖现有 `$connection_upgrade` 映射；无需将雷池容器端口直接暴露到外网。

独立模式和 Vite 开发预览也使用 HTTPS：默认读取 `/etc/node-monitor/tls/server.crt` 和 `server.key`，可通过 `WATCH_NANDA_TLS_CERT`、`WATCH_NANDA_TLS_KEY` 指定证书文件，访问 `https://<证书中的服务器地址>:3000`。`/api` 由 Vite 代理到本机后端，不对外开放 7980。没有证书时启动失败，不回退 HTTP。

启动脚本验证 HTTPS 证书，默认健康检查地址为 `192.168.1.24`，可用 `WATCH_NANDA_HTTPS_HOST` 指定证书中的主机名/IP；私有 CA 默认 `/etc/node-monitor/tls/ca.crt`，可用 `WATCH_NANDA_TLS_CA` 覆盖。浏览器和节点需信任该 CA。HTTP 仅用于服务器回环地址上的内部代理及后端健康检查。

已接入雷池的生产 Nginx 使用 `# watch-nanda-managed-root: <项目绝对路径>` 标记归属。启动、升级脚本识别此标记并保留现有 WAF 配置，不创建第二个前端服务。`deploy/production/nginx.conf` 是未接入 WAF 时的基础 HTTPS 配置，不应覆盖现有雷池入口。

普通后台模式将 PID、进程启动时间和命令指纹保存在 `.runtime`，日志分别在 `backend/backend.log`、`frontend/frontend.log`。
启动前校验迁移状态和 7980/3000 端口；端口冲突直接报错，不会悄悄改用 3001。
停止时验证进程身份再发送 SIGTERM，不会按 `vite`/`npm` 名称全局杀进程。
systemd 模式日志使用 `journalctl -u watch-nanda-backend`；停止、启动应使用安装时的同一账户，或在 systemd 模式下使用 sudo。

## 实时数据架构

### 节点提醒与网页运维

节点卡片的小铃铛分别控制该节点的离线告警与恢复邮件，默认开启，并受「设置」中的全局邮件开关控制；关闭提醒不影响采集和历史记录。

点击已有节点卡片可查看运行状态、显卡详情及节点配置；底部「测试 SSH」进入该节点的连接测试，「进入运维命令行」打开独立运维页。侧边栏「运维命令行」也可选择已有节点。运维页只提供连接测试和命令行，不包含 Agent 部署按钮。

已有 Agent 节点的「节点配置」提供「检测 Agent 上报可用性」，即时查询平台最近接收的有效上报、接收时间与采样时间，并按现有超时规则显示正常、延迟、离线或待上报。该检查只读、不连接 SSH、不重启服务；下方提示栏提供在节点查看 `node-monitor-agent` 服务状态与最近日志的命令。检测缺少上报凭据时会提示先完成部署或重新配置，不会展示 Token。

Agent 上报检查区域始终显示「重新部署 Agent」，无需先检测，也不因上报正常或检测失败隐藏，打开 `/nodes/:id/agent/redeploy` 独立页面。页面固定当前 Agent 节点，登录运维并验证 SSH 后，点击「重新部署 Agent」才执行；保留节点编号、分组和历史，更新采集程序与上报凭据，失败时尝试恢复原服务。该页面可查看部署日志并重新检测上报，不提供终端或其他节点选择。

Dashboard 在「算力大屏」右侧提供月亮 / 太阳按钮：白天采用浅灰蓝，夜间采用深蓝灰。侧边栏、节点卡片、节点弹窗、历史负载及图表、运维与部署面板、设置、数据清理均共享主题；其他管理页面也可在侧边栏标题旁切换。图表切换保留缩放与隐藏曲线，命令行视口保持深色。首次访问跟随系统主题，手动选择保存在当前浏览器，刷新和其他同源标签页会保持一致，不改变监控数据或采集状态。

新节点首次部署从「增加服务器」流程进入：选择 Agent 采集后点击「添加并部署 Agent」，先创建节点，再跳转到 `/servers/new/:id/agent` 为该新节点检查并安装。部署页固定目标节点，只显示连接身份、环境检查和部署日志，不包含命令行。安装仍需点击「一键部署 Agent」才开始；刷新部署页可以继续查看任务。原 `/agents` 和 `/nodes/:id/agent` 书签转到运维页面。

首次部署网页运维需在中心运行 `sudo python3 backend/manage_ops.py`，交互设置至少 12 字符的运维口令。口令仅保存 PBKDF2 校验摘要，默认文件 `/etc/node-monitor/ops-password.json`（权限 600，可用 `OPS_PASSWORD_FILE` 指定）。网页运维仅允许同源 HTTPS；登录使用 Secure、HttpOnly、SameSite=Strict Cookie，有效期 1 小时。运维口令与系统账号密码独立，系统 root 密码变化后需单独重新设置。

运维页面支持使用已配置的节点用户名密码，或手动输入新的用户名密码。点击「测试 SSH」或进入命令行认证成功后，手动凭据自动加密同步到节点配置，并切换为已配置凭据；认证失败不会覆盖原密码。SSH 采集节点清除旧失败状态、显示「待采集」并立即重新排队采集，采集成功后才显示在线。若旧采集仍在运行，会丢弃旧配置的结果并接续新任务。Agent 节点保留原采集方式和 Token：在线节点保留有效指标，离线节点显示「待采集」等待新上报，超过上报超时后仍未更新则恢复离线。首次连接必须核验主机指纹；变化的主机指纹不能在网页直接覆盖。不使用私钥或 ssh-agent，不创建免密授权。

网页命令行通过 WSS 转发交互式 SSH 终端，页面关闭会断开 SSH；闲置 15 分钟、最长 1 小时自动结束，退出运维登录也会结束已有终端。项目不记录终端输入或输出，目标系统仍可能按自身策略记录命令和登录。

一键部署复用独立 Agent 安装器，检查管理员权限、时钟、证书和运行环境，安装成功后验证 HTTPS 上报；失败时执行现有回滚。每台节点最多一个部署任务，中心最多并行两个；不自动修复系统时钟、磁盘或驱动。部署日志不包含登录密码或节点 Token。任务在单后端进程内保留最多 24 小时，页面可重新打开查看；后台重启后应直接核验目标节点状态。Nginx 模板包含 WebSocket Upgrade 配置，后端需安装 requirements.txt 中的 websockets 依赖。

### 批量 Agent 重部署与常用凭据

在「数据清理与节点管理」自动选择全部 Agent 节点或手动勾选，点击「一键批量重部署」进入独立页面。登录运维后点击「开始批量重部署」才创建任务；页面打开和进度刷新均不执行安装。队列逐台处理，保留节点 ID、分组及历史，复用单节点的安装、真实上报验证与失败回滚。

「设置 → 常用 SSH 用户名和密码」可新增、编辑、停用、删除加密保存的常用凭据，并设置优先级（小的先）。密码不回显；编辑时留空保留，修改用户名需要重填密码。队列优先尝试启用的常用凭据，再尝试节点已存凭据，同一用户名最多自动认证 3 次；网络或指纹错误不会继续尝试其他密码。没有可用凭据时提示手动输入，其他节点继续。手动密码仅用于本次重试。补充常用凭据后，可点击「重试所有等待凭据节点」继续。未知主机指纹必须明确核对后才可信任，不自动覆盖变化的指纹。

批次页可查看每台状态、按需加载日志、补充凭据和停止后续队列。关闭页面后服务端继续处理，停止时当前节点会完成或回滚，待执行节点取消。批次保存在当前后端进程内（最近 5 批）；服务重启后请先核验节点上报，再重新选择。

常用凭据默认保存在服务器 `/etc/node-monitor/ssh-profiles.json`，由 `SSH_PROFILES_FILE` 覆盖，使用已有 SSH Fernet 密钥加密，文件权限 600；与密钥一并备份，不提交 Git。本功能不新增数据库表或要求结构迁移。

### 采集与数据新鲜度

- SSH 采集分为读取配置、无数据库连接的网络采集、短事务写回三个阶段。
- 启动和每分钟调度共用去重队列；同一节点排队或执行期间不会再次提交。
- REST 与 SSE 共用状态计算：`online` 在线、`offline` 明确采集失败、
  `stale` 数据过期、`pending` 尚未采集。`collection_status` 保留最近采集结果。
- SSH 的 `latest_status_data.timestamp` 是采集完成时间；Agent 的 timestamp 是节点采样时间，received_at 是中心接收时间。
- SSH 的 `STALE_AFTER_SECONDS` 默认 180 秒、最小 60 秒；Agent 的 `AGENT_STALE_SECONDS` / `AGENT_OFFLINE_SECONDS` 默认 720 / 960 秒。
  浏览器按服务端数据年龄持续计时，网络断开时也会显示过期。
- 过期数据不计入大屏在线算力，历史快照以对应状态记录，查询时显示为空值缺口。
- SMTP 密码读取接口仅返回 `has_password`；保存时省略 `smtp_password` 保留原值。

### 实时推送

- **后端**：`/api/stream/servers` 端点每 10 秒推送一次最新数据（只读数据库，不触发 SSH）
- **SSH 采集**：每 60 秒并发采集一次
- **前端**：`useServers` composable 管理单例 EventSource 连接，多组件共享
- **启动采集**：FastAPI 启动时提交首轮 SSH 任务，接口不等待采集完成；页面先显示现有缓存及其新鲜度
- **历史查询优化**：`server_stats` 表的 `(server_id, timestamp)` 复合索引加速历史曲线加载

历史查询索引由初始化脚本幂等补齐，已有索引不会重复创建。

## 目录结构

面向后续开发的项目前置提示词见 [AGENTS.md](AGENTS.md)，完整架构、Git 改动阶段及有日期的生产核对结果见 [项目上下文](docs/PROJECT_CONTEXT.md)。每次改动后应检查这些说明是否需要同步，具体规则写在 AGENTS.md 中。

```
watch-nanda/
├── AGENTS.md                 # 项目前置提示词、模块职责与每轮更新检查规则
├── README.md                 # 使用与部署说明
├── docs/                     # 架构与进度（PROJECT_CONTEXT）、部署（DEPLOYMENT）
├── setup.sh                  # 初始化依赖、数据库、迁移并构建前端
├── start.sh / stop.sh        # 幂等启停入口（systemd 或受控后台模式）
├── install-agent.sh          # 中心侧交互安装 Agent
├── monitor_db.sql            # 新库结构；已有库使用增量迁移
├── backend/
│   ├── main.py               # REST、SSE、Agent 上报及运维路由挂载
│   ├── core/                 # 配置、状态、历史、上报、健康检查、凭据与运维
│   ├── models/               # 五张业务表的 ORM 与数据库连接
│   ├── worker/tasks.py       # SSH 采集、历史落库、邮件告警和调度
│   ├── migrate_agent.py      # 已有库幂等迁移；--check 只读
│   ├── manage_*.py           # Agent Token、SSH 凭据、运维口令管理
│   ├── agent_job.py          # 网页部署子进程入口
│   ├── .env.example          # 配置模板；真实 .env 不入库
│   └── tests/                # 隔离后端与安装/启停回归
├── frontend/
│   ├── src/api.js            # 同源 HTTPS API
│   ├── src/router/           # 路由与旧入口重定向
│   ├── src/composables/      # 共享实时数据、节点连接、主题状态
│   ├── src/components/       # 节点详情、Agent 诊断、终端等共用组件
│   ├── src/views/            # 控制台/大屏/历史/设置/清理/运维/新节点部署
│   ├── src/styles/           # 共用主题变量与运维样式
│   ├── src/utils/            # 状态、指标、图表、主题、传输工具
│   ├── tests/                # Node 前端回归
│   └── vite.config.js        # HTTPS 开发/预览与 API/WebSocket 代理
├── agent/                    # 节点采样上报、安装回滚、独立包构建与说明
├── scripts/                  # 数据库初始化、生命周期、中心安装编排
└── deploy/production/        # 基础 Nginx 与雷池控制台配置
```

## 历史折线图

- 1 天/1 周显示 10 分钟快照；季度/半年/年度按北京时间自然日聚合。MySQL 8+ 的 JSON_TABLE 在数据库端提取显存百分比，避免传输完整 GPU 进程 JSON。响应包含统一的 start/end、聚合方式和采样统计。
- CPU、内存和显存分别剔除缺失、越界与非数值样本；离线/过期不计作 0。无 GPU 不计作 0% 显存。平均值按有效原始样本计算，峰值取原始采样峰值；部分日期仅统计查询范围内样本。
- 图表默认聚焦已有记录，可切换完整范围；日期自动避让，支持滑块缩放和图例开关。缺失采集保留断点。查询超时为 45 秒，失败可直接重试，关闭弹窗取消请求，旧响应不能覆盖新范围。

## 回归测试

```bash
python3 -m pip install -r backend/requirements-test.txt
python3 -m unittest discover -s backend/tests -v
cd frontend
node --test tests/*.test.mjs
npm run build
```

后端测试使用独立临时 SQLite，SSH 与 SMTP 均模拟，不访问生产节点或发送邮件；安装、权限、systemd 和进程管理回归需要 Linux。前端覆盖历史查询、SSE/REST、新鲜度、表单保护、节点运维/部署、Agent 检查、主题和 HTTPS。

MySQL 专项：在 backend 下运行 `python3 tests/check_history_mysql.py`，仅使用连接内临时表校验北京时间边界、按样本加权及缺失值规则，不修改正式历史表。
