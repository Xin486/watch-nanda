# 节点主动上报

监控中心升级、迁移检查和当前生产入口见 [部署说明](../docs/DEPLOYMENT.md)。

本地读取 CPU、内存和 NVIDIA GPU 指标，默认每 300 秒上报一次，加 0–15 秒错峰。
没有监听端口，不使用 SSH 密钥，不接受远程命令或自动更新。节点仅需访问对应的 HTTPS 上报入口（内网 443 / 公网 59443）。
CPU 为采集时 1 秒窗口的利用率；其余指标为当时快照，不是过去 5 分钟的平均值或峰值。

## 独立运行包（推荐）

构建机只需 Linux + Docker；节点无需 Python、venv、pip 或访问 PyPI。
独立包包含 Python、OpenSSL、NVML Python 绑定及证书依赖，NVIDIA 驱动由节点原系统提供，不随包安装或更换。
目前支持 Linux x86_64/aarch64，需在对应架构构建；默认 manylinux2014 基线为 glibc 2.17，不适用于 Alpine/musl。

在监控中心的仓库根目录执行一次：

```bash
bash agent/build.sh
sudo bash install-agent.sh
```

构建产物位于 `agent/dist/node-monitor-agent-linux-<架构>.tar.gz`，附 SHA-256 校验文件及 manifest，记录源代码、安装器、构建镜像、依赖版本和逐文件校验值。
安装器发现对应包时优先使用独立程序，检测架构、验证安装包与文件完整性后部署；目标机器不调用 Python/pip，不替换系统 Python。
独立目录可整体复制，无需临时解压执行；程序由 root 管理，常驻服务以 node-monitor 账户运行。
包不含密码、Token、CA 私钥或 SSH 私钥；中心的 CA 公钥证书和每节点配置在部署时分别传输。

```bash
tar -xzf node-monitor-agent-linux-x86_64.tar.gz
./node-monitor-agent/node-monitor-agent --self-test
./node-monitor-agent/node-monitor-agent --config /etc/node-monitor-agent/config.json --once
```

`--self-test` 只检查运行时和依赖，不读取节点凭据、不向中心上报。需要额外指定安装包时使用 `--bundle /path/to/package.tar.gz`。
程序内置的本地安装子命令仅供管理员通过 SSH 调用，不通过上报接口接收远程命令。
旧版源码安装方式仍可通过 `--source` 使用；没有构建包时保留原 Python/venv 流程，只有该流程要求目标 Python 3.10+。

## 交互式检查与自动安装

先在监控中心完成 `setup.sh` 初始化及 HTTPS 配置，再从仓库根目录运行：

```bash
sudo bash install-agent.sh
```

按提示输入目标 IP、SSH 用户（默认 root）、SSH 端口（默认 22）和密码。密码隐藏输入，不写入参数或配置文件。
首次连接会显示 SSH 主机指纹，核对后输入 `yes`；已知主机的指纹变化会拒绝连接。这里只保存公开的主机指纹，不创建 SSH 私钥，不修改目标的 authorized_keys 或 crontab。
重装后需要更换已知指纹时，可先在网页连接检查中核对原/新指纹，点击「允许更改并继续连接」完成受认证的更新；取消不改信任，保存前会重新核对指纹和地址。命令行安装器本身仍不会自动覆盖已知指纹。
非 root 用户需要 sudo 权限，脚本使用同一个密码执行 sudo；不支持必须分配终端或使用不同 sudo 密码的配置。

脚本按 IP 和 SSH 端口匹配已有节点，保留节点 ID、分组及历史；未登记的 IP 自动新增节点。
SD-WAN/NAT 管理地址与登记地址不一致，或同 IP 对应多个节点时，请明确指定已有节点 ID：

```bash
sudo bash install-agent.sh --ip 172.16.66.177 --user test --port 22 --server-id 73
sudo bash install-agent.sh --ip 172.16.66.177 --user test --port 22 --check-only
```

`--check-only` 仅检查 SSH/sudo、Linux/systemd、Python、节点时钟、TLS 证书及已有 Agent 状态，不安装系统包、不修改节点记录、不签发 Token。

### 出站网络与目标机防火墙

Agent 不监听端口。默认部署先核对目标机防火墙，并为启用的 UFW/firewalld 补充仅到上报入口 IP 和 TCP 端口的出站放行规则；内网为 `192.168.1.24:443`，公网为 `114.212.165.149:59443`。规则同时持久化，不开放节点入站端口、不关闭/清空/重载防火墙，也不更改其他规则。已有相同规则保留；同时启用多个管理器时停止并提示人工核对。

安装器验证 TCP/TLS 后，还必须以 `node-monitor` 服务账户完成一次真实 HTTPS 上报并确认常驻服务正常，才报告成功。证书失败、出站阻断、中心拒绝和时钟/采样错误分别提示；自定义 nftables/iptables、服务账户专属策略或上游 ACL 仍可阻断连接，这时部署会失败，不能用 root 的握手通过代替服务账户验证。

`--check-only` 不添加防火墙规则。实际部署失败会恢复本次新增规则，保留管理员原有规则；无法确认远端服务回滚时保留当前网络配置和安装记录，避免中断可能仍在运行的新 Agent。受保护的 `network-changes.json` 记录新增规则的恢复命令，成功激活时也保留在该 release 的 `.rollback` 中。域名入口按检查时解析的地址放行，地址改变时需重新检查网络配置。

规则语法依据：[UFW 手册](https://manpages.ubuntu.com/manpages/noble/man8/ufw.8.html)、[firewalld direct 接口](https://firewalld.org/documentation/man-pages/firewalld.direct.html)。不对未知自定义策略执行全局放行。

### 已有 Agent 重新部署

在节点配置的 Agent 上报检查区域点击始终显示的「重新部署 Agent」，进入独立重部署页面；无需先检测，上报正常、异常或检测失败时均可进入。页面固定节点，先完成运维登录和 SSH 身份检查，再主动点击重新部署；不会因为打开页面或检测失败自动执行安装。

也可在中心明确指定已有 Agent 节点：

```bash
sudo bash install-agent.sh --ip <节点IP> --port <SSH端口> --user <SSH用户> --server-id <已有节点ID> --reinstall
```

重部署保留节点 ID、分组和历史，即使旧版本仍显示健康也执行程序与凭据更新；不能用于新增节点或已经切回 SSH 的节点。只读检查保持 `--check-only`。安装器源码变化后，需先执行 `bash agent/build.sh` 构建与当前源码一致的独立包；网页不会使用不匹配的旧包。
### 上报入口与运行要求

`192.168.1.0/24` 中的目标默认上报到 `https://192.168.1.24/api/agent/report`，其余目标默认使用 `https://114.212.165.149:59443/api/agent/report`。
两个入口均自动传输 `/etc/node-monitor/tls/ca.crt` 公钥证书，保留证书和 IP 校验；不会传输中心 CA 或服务器私钥。其他中心可通过 `--url` 指定：

```bash
sudo bash install-agent.sh --url https://monitor.example.com/api/agent/report
sudo bash install-agent.sh --url https://10.0.0.24/api/agent/report --ca-file /path/to/ca.crt
```

目标需运行 Linux 和 systemd，并能访问对应 HTTPS 上报入口（内网 443 / 公网 59443）；独立包部署不要求目标连接软件源。
只有使用旧源码方式时才需要 Python 3.10+、venv 和软件源/PyPI，缺失时尝试使用系统 apt/dnf 安装；旧发行版默认 Python 低于 3.10 时停止，不替换系统 Python。
节点时间需与中心相差不超过 55 秒。无 GPU 的机器也可以安装；GPU 读取失败仍按 Agent 原有行为记录错误。

安装包或源码依赖在 `/opt/node-monitor-agent/releases/<版本ID>` 中准备好之后才签发单节点 Token。配置保存为 root:node-monitor 的 640 文件，服务由独立低权限账户运行，每 300 秒上报。
首次部署、重部署和再次运行安装命令统一执行覆盖安装，健康 Agent 也不跳过。`--check-only` 仍只检查，不停止服务、移除文件或轮换 Token。
中心通过带心跳的 SSH 会话持有目标机 `/run/watch-nanda-agent-session.lock`，整个部署期间互斥；另一个正在执行的部署会被拒绝。会话断开或 45 秒未收到心跳后释放，单步操作仍有独立文件锁；已失效的会话不能继续修改节点。
准备新版独立包后，先确认项目服务归属，向旧服务及属于本项目的遗留进程发送 TERM，必要时 KILL，确认进程和服务停止，再移除旧服务文件、配置及 current 链接；完成这些步骤后中心才签发 Token。新版本仍先以服务账户完成真实 HTTPS 上报，再启用 systemd 常驻和开机启动。确认成功后清理旧 releases 和旧源码/venv 安装文件，只保留当前运行版本及受保护的恢复记录。
不会操作监控中心服务，不会覆盖不属于本项目的同名服务或自定义 systemd drop-in。

安装失败会尝试恢复原有 Agent 服务和配置，并恢复已有节点原凭据与采集模式；新节点保留 ID 和已有历史，撤销本次 Token 后留在待采集状态。
如果 SSH 中断导致无法确认恢复结果，脚本保留当前凭据，输出节点上的受保护备份和临时目录，避免把可能已运行的新 Agent 再次断开。
上次中断留下的 `/opt/node-monitor-agent/.install-pending.json` 不再永久阻止安装：持有本次独占会话后自动归档到 `.install-history/`，接管当前实际状态并覆盖安装，不手动删除记录或恢复旧 Token。被旧会话延迟执行的步骤会因会话/安装标记不匹配而拒绝继续。
本次覆盖前的配置与服务备份位于新版本目录的 `.rollback`，仅 root 可读，用于本次失败恢复；成功后不保留旧版安装程序。若少量旧文件因权限或 I/O 错误未清理，会在成功日志中明确提示，不能把清理警告当作卸载完成。
若进程处于 D（内核不可中断等待）状态，TERM/KILL 都可能无法结束。安装器会在有限等待后列出阻塞 PID 和状态，停止覆盖；旧进程未退出前不签发新 Token。需要先处理 GPU/驱动或节点故障，不会自动重启整机、重置 GPU 或结束其他用户的计算任务。

## 1. 监控中心迁移

先备份数据库，在维护窗口暂停旧后端，进入 backend 并使用后端的 Python 环境执行：

```bash
python3 migrate_agent.py
```

此操作幂等：检查并补齐采集方式、Agent 凭据表、加密 SSH 密码和节点提醒字段。
不改变已有采集方式、节点 ID 或历史记录。全新数据库由 setup.sh 初始化。
完成后启动新后端；不要仅启动新代码而跳过迁移。

新增节点默认为主动上报；现有节点可以在详情里切换采集方式。切换模式会清空实时缓存并撤销旧 Token，历史保留。
切换至主动上报后，签发 Token 前显示待采集，不会自动回退 SSH。

## 2. HTTPS 入口

准备节点可访问的域名及受信任的 HTTPS 服务端证书。可使用现有反向代理，在 TLS server 中添加：

```nginx
location = /api/agent/report {
    client_max_body_size 64k;
    client_body_timeout 15s;
    proxy_pass http://127.0.0.1:7980;
    proxy_set_header Host $host;
    proxy_set_header Authorization $http_authorization;
    proxy_connect_timeout 5s;
    proxy_read_timeout 20s;
}
```

只需把上报接口提供给节点，现有管理接口继续限定在管理网络。不要将未鉴权的全部管理接口公开到互联网。
反向代理日志不得记录 Authorization 头或请求正文。可在代理层加请求限速和并发连接限制。
管理网页、API 和 SSE 统一使用同源 HTTPS，对 /api/stream/servers 关闭 proxy_buffering；网页终端使用 WSS。后端 7980 仅监听 127.0.0.1，旧 HTTP 页面入口关闭；本机反向代理到后端的回环连接可使用 HTTP。

Agent 强制 HTTPS、验证证书并拒绝重定向。自建 CA 可在节点配置中增加 ca_file，指向只含 CA 证书的文件；不需要节点 TLS 私钥。

## 3. 签发单节点 Token

在监控中心执行（示例节点 ID 为 73，请替换）：

```bash
cd backend
umask 077
python3 manage_agent.py issue 73 --url https://monitor.example.com/api/agent/report > agent-73.json
```

该命令切换节点至 agent 模式，输出完整 JSON 配置及仅此一次可见的随机 Token。
数据库只存 SHA-256 摘要；Token 只能向自己的节点上报，不能控制节点配置。
文件包含凭据，请通过已有可信管理途径交给节点管理员，不提交 Git、不放进聊天或公共下载地址。
重新 issue 会使旧 Token 立即失效；不要对所有机器共用一个配置文件。

```bash
python3 manage_agent.py revoke 73
```

revoke 立即使旧 Token 失效，不启用 SSH。节点需重新 issue 才能继续上报。

## 4. 在 Linux 节点安装

将此 agent 目录中的程序、requirements.txt、service 文件及该节点 JSON 配置传到节点。
先安装 Python 3.10+ 和 venv；下面命令在 agent 目录执行：

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin node-monitor
sudo install -d -m 755 /opt/node-monitor-agent
sudo python3 -m venv /opt/node-monitor-agent/venv
sudo /opt/node-monitor-agent/venv/bin/pip install -r requirements.txt
sudo install -o root -g root -m 644 monitor_agent.py /opt/node-monitor-agent/monitor_agent.py
sudo install -d -o root -g node-monitor -m 750 /etc/node-monitor-agent
sudo install -o root -g node-monitor -m 640 agent-73.json /etc/node-monitor-agent/config.json
sudo install -o root -g root -m 644 node-monitor-agent.service /etc/systemd/system/node-monitor-agent.service
sudo systemctl daemon-reload
```

账户若已存在，跳过 useradd。服务以普通账户运行，程序和虚拟环境由 root 管理，不授予 sudo 或 docker 权限。
NVIDIA 驱动需在节点上已安装；若 GPU 设备仅授权 video/render 等组，按本机设备权限仅授予必要设备访问组。
无需 GPU 的节点也可正常上报 CPU/内存；GPU 读取失败会在日志和 gpu_error 标记，不填假零值。

先验证一次再启动常驻服务：

```bash
sudo -u node-monitor /opt/node-monitor-agent/venv/bin/python /opt/node-monitor-agent/monitor_agent.py --config /etc/node-monitor-agent/config.json --once
sudo systemctl enable --now node-monitor-agent
sudo journalctl -u node-monitor-agent -n 30 --no-pager
```

确认日志为 accepted=True，并在页面看到节点在线及最近上报时间。--once 采集或上报失败时以非零状态退出。
安装包应通过管理员审核的版本发布；没有在线脚本下发或自更新功能。配置 Token 更新后重启此服务。

## 数据与状态

- 每 5 分钟采集上报，允许配置 60–300 秒；若需更慢，须同步调整服务端过期、失联、采样时间接受窗口及文档。
- 每 10 分钟保存最新有效历史样本，以节点实际采样时间为时间戳，同一报告不重复保存。
- 同一 report_id 重试、旧采样和乱序报告不刷新在线时间；失败重试复用同一份报告，最多三次。
- 在线状态使用服务端接收时间；默认超过 720 秒过期，超过 960 秒失联。节点未首次上报时显示待采集。
- 节点和中心需时间同步：拒绝超过 10 分钟的旧采样和提前超过 60 秒的采样。
- 每节点至少间隔 30 秒接受一份新报告；请求最大 64 KiB，GPU 最多 64 张，拒绝未知字段和非法数值。
- 告警需在设置页启用并配置 SMTP；主动上报节点等待时间取 16 分钟与设置页阈值较大者，每 24 小时最多重复一次，恢复上报后发送恢复通知。
- 无效/过期历史快照保留缺口，不作为零负载。前端保持 SSE 推送，并显示最近采集时间（主动上报节点为最近成功接收距今）。
- 采集当前采样窗口内 CPU 占用最高的进程（用户名、进程名、PID、CPU、内存）；仅读取普通权限可访问的 /proc，不采集命令行参数。CPU 按单核 100% 计算，多线程进程可超过 100%；无 CPU 活动时以占用内存排序。受 hidepid 等权限限制时只能显示可见进程。旧 Agent 仍能上报，但需升级才能显示进程。NVIDIA GPU 的显存与计算利用率分别上报，不混用。

## 试点及回退

先迁移一台现有节点，确认 CPU/内存/GPU、断网状态、恢复通知和至少一个 10 分钟历史周期正常，再迁移其余节点。
主动上报节点不会进入 SSH 采集队列；切换前尚在运行的 SSH 任务，其旧结果会被丢弃。
若需回退单节点，在详情里改回 SSH，继续使用原来的 SSH 连接配置；agent Token 自动撤销，随后在该节点停用 agent 服务。
数据库新增列和表可以保留，不需要删除历史数据。

## 本地回归

```bash
pip install -r backend/requirements-test.txt
python3 -m unittest discover -s backend/tests -v
cd frontend
node --test tests/*.test.mjs
npm run build
```

后端测试仅用独立 SQLite，SSH、邮件和节点采集均模拟，不访问生产数据库。
GPU 硬件、systemd 服务及 HTTPS 证书链仍需在 Linux 试点节点验证。

## 批量重新部署

数据清理页支持自动选择已有 Agent 采集节点或逐台勾选，进入独立批量队列页面。先在设置中的「常用 SSH 用户名和密码」维护启用凭据及优先级；队列先尝试常用凭据，再尝试节点已存凭据，同一用户名最多自动认证 3 次，仍失败则提示手动输入。未知或变化的主机指纹均使该节点单独等待；核对页面中的目标地址和原/新指纹，明确勾选确认后才保存并重试，其他可执行节点继续，不自动信任。

批量操作复用单节点独立包、出站防火墙检查、服务账户真实上报验证与失败回滚，保留 ID、分组和历史。打开队列页面不启动安装，需要运维登录并主动开始；关闭页面不会停止，停止后续队列不会中断当前安装/回滚。服务重启后批次不恢复，先检查节点实际状态再重试。
