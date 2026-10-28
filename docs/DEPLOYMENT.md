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

## 苏州实例（10.168.100.150）

2026-10-23 核对：苏州仓库位于 `/home/test/watch-tower/watch-nanda`，归 root 所有，使用 test 登录后通过 sudo 管理。此实例使用独立的 `watch-nanda-backend.service` 和 `watch-nanda-frontend.service`，开机自启；后端仅监听 `127.0.0.1:7980`，前端 Vite preview 在 `https://10.168.100.150:3000` 提供页面、同源 API、SSE 和 WSS。此处没有配置南京实例的 Nginx/雷池链路。

苏州使用项目 `backend/.venv`（Python 3.12.13），基础解释器 `/root/.pyenv/versions/3.12.13/bin/python3.12`，Node/npm 位于 `/root/.nvm/versions/node/v24.18.0/bin`。系统默认 Python 3.14 不符合本项目范围，升级前先核对上述路径仍存在。日常启停与只读结构检查：

```bash
cd /home/test/watch-tower/watch-nanda
sudo bash start.sh
sudo bash stop.sh
sudo backend/.venv/bin/python backend/migrate_agent.py --require-ready
```

重新初始化或升级时，脚本自动发现兼容的现有 pyenv/NVM 运行时，保留现场 `.env`、密钥、证书并自行停止、覆盖及启动本项目：

```bash
sudo bash setup.sh --systemd --host 10.168.100.150
```

MySQL 为原有 Docker 容器 `mysql-app`，数据挂载 `/home/test/watch-tower/mysqldata/mysql_data` → `/var/lib/mysql`，仍使用 `.env` 中的 `10.168.100.150:3306/monitor_db`。本次在完整备份后新增 `agent_credentials`、节点的 `collection_mode` / `ssh_password_encrypted` / `email_alert_enabled`，并补齐 `server_stats(server_id,timestamp)` 复合索引；原节点 ID 和历史保留。已有 Redis/PostgreSQL 容器维持原状，不作为平台依赖操作。备份和恢复记录位于该机 `/root/watch-nanda-suzhou-upgrade-20261023-161620`，数据库备份包含敏感信息，按 root 私有文件保护，不放入 Git。

TLS 文件在 `/etc/node-monitor/tls/`，使用苏州独立 CA，SAN 包含 `10.168.100.150`。客户端需导入该机 `ca.crt` 后通过 HTTPS 访问；CA 私钥不传给客户端。2026-10-23 已设置网页运维口令，并通过 HTTPS 登录、会话和常用凭据接口验证。运维摘要独立于系统账号保存；系统密码变化不会自动同步。管理员可运行 `sudo backend/.venv/bin/python backend/manage_ops.py` 单独修改。旧版 SSH 私钥采集节点仍需逐台补充密码才能继续采集。

新初始化流程将苏州 Agent 上报入口 `https://10.168.100.150:3000/api/agent/report` 和本站 CA 保存到 `.runtime/site.json`，网页/命令行安装器共同读取；不再沿用南京入口。默认构建本机架构的独立包，其他架构需另外准备。目标机实际部署仍需管理员主动发起、提供该节点凭据并核验指纹，成功必须验证真实 HTTPS 上报。

## 配置来源

可选的全站 Hermes 悬浮对话使用独立 `watch-nanda-hermes.service` 和回环 7981，沿用 `/api/ops` 的运维认证，不重启监控后端；南京仅在现有 18080 origin 加专用 location。安装、权限、验证及回退见 [Hermes 接入说明](HERMES_CHAT.md)。整套 setup 暂不管理该可选服务，升级前后需保留现场聊天代理。

| 配置 | 当前来源与用途 |
| --- | --- |
| 后端服务 | `scripts/lifecycle.py` 按仓库位置、venv 和运行账户生成 `watch-nanda-backend.service`；不维护另一份静态服务模板 |
| 数据库及阈值 | 环境变量优先于 `backend/.env`，配置模板为 `backend/.env.example` |
| 本站地址与运行时 | `.runtime/site.json`（不入 Git）；环境变量和 `.env` 优先；保存 HTTPS、Node、密钥路径及独立站点 Agent 入口 |
| 生产 Nginx/WAF | 保留现场配置和 `# watch-nanda-managed-root: <仓库绝对路径>` 标记；基础模板 `deploy/production/nginx.conf` 不能覆盖现有 WAF 配置 |
| 雷池控制台代理 | `deploy/production/safeline-console.conf`；生产副本 `/etc/nginx/snippets/watch-nanda-safeline-console.conf` |
| TLS | `/etc/node-monitor/tls/`；节点只需受信任的 CA 公钥证书 |
| SSH 密码加密 | `/etc/node-monitor/ssh-credentials.key`，或 `SSH_CREDENTIAL_KEY_FILE` 指定的文件 |
| 常用 SSH 凭据 | `/etc/node-monitor/ssh-profiles.json`，或 `SSH_PROFILES_FILE` 指定；权限 600，使用 SSH 独立密钥加密，须一起备份 |
| 网页运维口令摘要 | `/etc/node-monitor/ops-password.json`，或 `OPS_PASSWORD_FILE` 指定的文件 |

2026-10-19 生产后端仍使用 `/usr/bin/python3`；新式 `setup.sh` 会建立 `backend/.venv` 并更新服务。不要把现场旧依赖环境直接写死到新的服务文件。

## 标准升级

先检查当前分支、工作区差异、`systemctl cat watch-nanda-backend` 及 Nginx 的项目归属。备份数据库配置、节点配置、`.env`、独立加密密钥、常用 SSH 凭据文件和现场代理配置；备份按受保护的运维流程保存，不提交 Git。保留既有历史数据备份策略。

在维护窗口使用当前部署所需的管理员账户：

1. 结束网页终端，完成/停止 Agent 安装及批量队列，保存工作区差异和当前提交。
2. 更新到已确认提交，保留 `.env`、本地差异和本地 `AGENTS.md`（仓库已停止同步该文件；首次跨此提交更新前另存并在更新后恢复）。
3. 执行覆盖升级，脚本会停止已确认归属的服务，完成后自动启动：

```bash
sudo bash setup.sh --systemd
```

`setup.sh` 准备 Python/Node、项目依赖、MySQL、HTTPS、运维认证、增量结构、前端和 Agent 包。已有配置不覆盖；升级备份位于 `.runtime/setup-backups/<时间>/`，包含旧依赖、构建、服务配置、受保护配置及已有库的一致性 `database-before.sql.gz`。自动恢复只回退依赖、构建及服务配置，源码需按记录恢复匹配的 Git 版本，数据库不自动降级。备份包含敏感数据，目录权限 700、秘密文件 600，不上传 Git；保留足够磁盘并按现场保留策略整理成功备份。

识别本项目 Nginx 时保持代理/WAF 运行，只停止后端，发布静态资源时保留旧哈希文件、最后替换首页。独立站点创建后端与 HTTPS preview 两个服务。缺失或无法连接原数据库时不会为已有服务创建替代容器或空库，先恢复原容器/端口/挂载。旧 nohup 入口会按监听 PID、启动时间、完整命令及项目工作目录核验，确认归属后 TERM/KILL 并限时等待；未知进程保留并提示，不能按名称全局杀进程或遇 D 状态继续覆盖。

`--skip-deps` 复用现有依赖；`--no-start` 完成后保持停止；`--no-bootstrap` 适用于已准备运行时的离线环境。首次安装用 `--host` 指定证书中的 IPv4/域名；已有地址及证书不自动替换。首次运维口令交互输入，非交互时仅写入 `.runtime/initial-ops-password`（600）；亦可通过 `--ops-password-file` 输入受保护文件。已有摘要保持不变。

新独立站点默认 HTTPS 3000，主机或上游防火墙限制访问时，应按现场管理网策略放行该入口；初始化不自动扩大管理网访问范围。下载依赖可用 `PIP_INDEX_URL` 指定可信镜像，例如南大镜像 `https://mirrors.nju.edu.cn/pypi/web/simple`；联网源不可用时安装失败并保留恢复记录，不忽略依赖错误。

初始化默认构建或复用通过清单及源码哈希校验的本机架构独立 Agent 包，使用 Docker/manylinux。只做监控可显式 `--skip-agent-build`，随后再运行 `bash agent/build.sh`；不能在缺包时宣称网页部署可用。跨架构包需在对应 Linux 构建机准备。节点防火墙及重部署流程见 [Agent 说明](../agent/README.md#出站网络与目标机防火墙)。

已有库不能重新导入 `monitor_db.sql`，也不需要执行单独的建索引 SQL；索引由 `scripts/initialize.py` 根据现有结构补齐。首次部署、独立 HTTPS 模式及环境要求见 [README](../README.md)。

## 已有生产的小范围发布

若依赖、数据库结构和服务配置均未改变，可保留实际运行环境进行小范围发布：先确认生产工作区干净、无运行中的部署任务及等待处理的批量队列（`/api/ops/agent-batches` 需运维登录读取），保存当前提交、静态文件和 Agent 包的受保护备份；只读迁移检查通过后，将源码快进到已推送的提交。

使用锁文件对应的前端构建结果发布，保留旧哈希资源，最后原子替换首页。将经过清单及源码哈希校验、原生自检的 Agent 包和两个摘要侧文件一起替换；常驻后端模块变化时再重启 `watch-nanda-backend.service`。仅修改 `scripts/install_agent.py`、节点安装器/服务模板及匹配包时，在确认没有部署任务或运行队列后一起发布；每个部署由新的 `backend/agent_job.py` 子进程读取安装器与包，不必重启后端。其他无需改动的 Nginx/WAF、systemd 配置和依赖环境保持不动，不执行初始化或迁移写入。仅前端和文档变化时，只更新对应源码与静态文件，不必替换未变化的 Agent 包或重启后端。发布后验证 HTTPS、API、SSE、WSS 路由及 Agent 上报；记录可恢复的备份路径。回退时恢复相互匹配的原提交、首页与 Agent 包，涉及常驻模块时重启后端，不回退或覆盖数据库。

## 关机后的启动恢复

2026-10-21 现场恢复核对：MySQL 运行在 Docker 容器 `mysql`，原数据目录为 `/data2/mysqldata`，挂载到容器 `/var/lib/mysql`；后端通过 `127.0.0.1:3306` 连接 `monitor_db`。容器重启策略为 `unless-stopped`。旧 `mysql_nfs_backup` 挂载 `/mnt/nfs_share/zulingdatabase`，也曾配置宿主 3306，已停止并改为 `restart=no`，保留数据供核对。不要同时启动这两个容器使用相同宿主端口。

主机开机后，先核对 Docker、Nginx、后端服务状态及容器的实际挂载、重启策略和端口归属。若页面可开而 API 返回 502，查看后端启动日志；本次曾因旧备份容器抢占端口，导致后端连到没有 `monitor_db` 的库。不能因此导入新库结构、清空数据或修改生产凭据。

确认现场仍与上述快照一致时，先保存两容器状态、挂载、重启策略和日志，再停止反复启动失败的后端，关闭旧备份容器自启并正常停止它，启动原 `mysql`。端口冲突失败后，Docker 可能留下 `HostConfig.NetworkMode=bridge`、但 `NetworkSettings.Networks={}` 的状态：MySQL 进程虽运行，宿主 3306 仍不可达。仅在确认原网络确为默认 `bridge` 且连接已丢失时，用 `docker network connect bridge mysql` 恢复原连接，并核对原端口映射；不要重建容器或替换数据目录。

使用实际后端环境只读验证 `monitor_db`、原节点和历史表，运行 `migrate_agent.py --require-ready`。验证通过后，在项目目录执行 `bash start.sh` 恢复已有服务，再检查 HTTPS 页面、API、SSE 和 Agent 上报。既有源码和静态资源完整时直接恢复运行；需要重新发布时按前述小范围发布流程操作。恢复记录放在受保护的运维目录；不要仅为复现而再次重启整机。

## 迁移检查

使用实际运行后端的 Python 环境，在 `backend/` 中执行：

```bash
python3 migrate_agent.py --require-ready
```

使用 venv 时把 `python3` 换成 `.venv/bin/python`。`--check` 只打印状态，`--require-ready` 还会在缺结构时退出 2；两者都不修改数据库。检查项为：

- `monitor_db.sql` 的全部业务表、字段及索引（包括历史复合索引）；检查不改类型、默认值或既有记录。

确实缺失时，在备份及维护窗口内运行 `python3 migrate_agent.py`。该脚本与初始化共用 `core/schema.py`，只补缺表/列/索引，不重建历史、不重置 ID、模式或 Token，也不收缩列或删除旧字段。缺失历史指标/时间的已有记录保持 NULL，不编造 0 或当前时间；已有数据缺少主键或无默认的身份字段时先明确失败，需从备份恢复，不能自动伪造。索引同名但定义冲突时保留原样并提示。MySQL DDL 会隐式提交，部分失败后应排查并重跑，不能假定事务整体回滚。

## 发布验证与恢复

常用凭据文件在首次保存时创建；读取列表不写文件，不需运行数据库迁移。批量重部署 API 与常用凭据 API 均在 `/api/ops/` 下，复用同源 HTTPS 运维认证。批次和手动密码不持久化，升级重启前应结束或停止当前队列，不能将重启后的空列表视为节点已恢复。

检查后端和 Nginx 服务状态、HTTPS 页面/API/SSE、WSS 运维入口，以及节点最近上报/采集时间；确认 7980 仅回环监听、管理访问策略和雷池链路未改变。前端发布保留旧哈希资源，最后替换首页，兼容已打开的页面。

服务日志使用 `journalctl -u watch-nanda-backend`。生产版本回退时恢复已确认兼容的源码、依赖、前端和服务/代理配置，保留现有新增数据库结构及历史。不能直接切到不识别 Agent 的老后端，否则可能重新对 Agent 节点进行 SSH 采集。

单节点切换与安装回滚见 [Agent 接入说明](../agent/README.md#试点及回退)。Agent 安装失败保留安装器恢复记录；不要手动删除 release、`.rollback` 或 `.install-pending.json`。再次主动部署会在独占会话中接管中断记录，确认旧 Agent 彻底停止后覆盖安装；真实并发任务和无法终止的 D 状态进程仍会明确拒绝。首次部署与重部署统一采用此流程，详见 Agent 说明。
