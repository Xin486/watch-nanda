# 节点主动上报

192.168.1.24 的当前结构核对与生产执行顺序见 [生产迁移说明](PRODUCTION_MIGRATION.md)。

本地读取 CPU、内存和 NVIDIA GPU 指标，默认每 300 秒上报一次，加 0–15 秒错峰。
没有监听端口，不使用 SSH 密钥，不接受远程命令或自动更新。节点仅需访问监控中心 HTTPS 443。
CPU 为采集时 1 秒窗口的利用率；其余指标为当时快照，不是过去 5 分钟的平均值或峰值。

## 交互式检查与自动安装（推荐）

先在监控中心完成 `setup.sh` 初始化及 HTTPS 配置，再从仓库根目录运行：

```bash
sudo bash install-agent.sh
```

按提示输入目标 IP、SSH 用户（默认 root）、SSH 端口（默认 22）和密码。密码隐藏输入，不写入参数或配置文件。
首次连接会显示 SSH 主机指纹，核对后输入 `yes`；已知主机的指纹变化会拒绝连接。这里只保存公开的主机指纹，不创建 SSH 私钥，不修改目标的 authorized_keys 或 crontab。
非 root 用户需要 sudo 权限，脚本使用同一个密码执行 sudo；不支持必须分配终端或使用不同 sudo 密码的配置。

脚本按 IP 和 SSH 端口匹配已有节点，保留节点 ID、分组及历史；未登记的 IP 自动新增节点。
SD-WAN/NAT 管理地址与登记地址不一致，或同 IP 对应多个节点时，请明确指定已有节点 ID：

```bash
sudo bash install-agent.sh --ip 172.16.66.177 --user test --port 22 --server-id 73
sudo bash install-agent.sh --ip 172.16.66.177 --user test --port 22 --check-only
```

`--check-only` 仅检查 SSH/sudo、Linux/systemd、Python、节点时钟、TLS 证书及已有 Agent 状态，不安装系统包、不修改节点记录、不签发 Token。
默认上报到 `https://192.168.1.24/api/agent/report`，自动传输 `/etc/node-monitor/tls/ca.crt` 公钥证书；不会传输中心 CA 或服务器私钥。其他中心可指定：

```bash
sudo bash install-agent.sh --url https://monitor.example.com/api/agent/report
sudo bash install-agent.sh --url https://10.0.0.24/api/agent/report --ca-file /path/to/ca.crt
```

目标需运行 Linux 和 systemd，支持 Python 3.10+。Python/venv 缺失时尝试使用系统 apt/dnf 仓库安装；旧发行版默认 Python 低于 3.10 时停止并提示先升级，不替换系统 Python。
目标需能访问监控中心 443 以及软件源/PyPI；脚本安装独立 venv 和 NVIDIA NVML Python 包，不安装或更换 GPU 驱动。
节点时间需与中心相差不超过 55 秒。无 GPU 的机器也可以安装；GPU 读取失败仍按 Agent 原有行为记录错误。

依赖在 `/opt/node-monitor-agent/releases/<版本ID>` 中准备好之后才签发单节点 Token。配置保存为 root:node-monitor 的 640 文件，服务由独立低权限账户运行，每 300 秒上报。
脚本先以服务账户验证一次真实 HTTPS 上报，再启用 systemd 开机启动；凭据、版本、证书及配置一致且近期已上报的健康 Agent 会跳过重复安装。
不会操作监控中心服务，不会覆盖不属于本项目的同名服务或自定义 systemd drop-in。

安装失败会尝试恢复原有 Agent 服务和配置，并恢复已有节点原凭据与采集模式；新节点保留 ID 和已有历史，撤销本次 Token 后留在待采集状态。
如果 SSH 中断导致无法确认恢复结果，脚本保留当前凭据，输出节点上的受保护备份和临时目录，避免把可能已运行的新 Agent 再次断开。
发生此类中断后先检查节点 `systemctl status node-monitor-agent` 和中心的最近上报；未结束的安装由 `/opt/node-monitor-agent/.install-pending.json` 标识，不自动覆盖。
此前版本的配置及服务备份位于对应新版本目录的 `.rollback`（仅 root 可读），不删除历史版本。

## 1. 监控中心迁移

先备份数据库，在维护窗口暂停旧后端，进入 backend 并使用后端的 Python 环境执行：

```bash
python3 migrate_agent.py
```

此操作幂等：为 servers 增加 collection_mode，创建 agent_credentials。
已有节点保持 ssh，不改变节点 ID 或历史记录。全新数据库使用更新后的 monitor_db.sql。
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
若网页也使用 HTTPS，需要代理其 /api/ 路径，并对 /api/stream/servers 关闭 proxy_buffering；前端会自动使用同源地址。
保持现有 HTTP 管理页面时仍使用 7980，不必为了上报迁移管理页面。

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
- 第一版不采集进程详情，top_process 显示未采集。NVIDIA GPU 的显存与计算利用率分别上报，不混用。

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
