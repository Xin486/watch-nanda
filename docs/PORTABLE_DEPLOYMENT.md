# 换网络部署

项目源码不包含生产 IP、上报分流网段或模型密钥。每套安装保留自己的数据库、地址、证书与助手配置。

## 新安装

在 Linux 上拉取主线分支，在仓库根目录执行：

```bash
sudo bash setup.sh --host <IP或域名> --port 3000
# 需要助手时，可在首次安装添加 --with-hermes
sudo bash setup.sh --host <IP或域名> --port 3000 --with-hermes
```

Python 3.10–3.12、Node.js 18+、MySQL 和 HTTPS 由 setup 准备或复用。已有库只增量补结构。新安装需要可访问依赖源；离线环境应预先准备运行时、依赖和 Agent 包，不能把下载失败当安装成功。管理入口应由所在网络的防火墙/WAF 限定，脚本不自动扩大管理网范围。

`--port` 是独立 HTTPS 页面/Agent 入口端口；`--backend-port` 默认 7980，`--hermes-port` 默认 7981，后两者仅监听回环且三个端口不能重复。IP 支持 IPv4/IPv6，也可使用域名。已有站点的地址、证书不会因再次 setup 自动轮换。

## 网页设置

- **Agent 上报入口**：填写节点能连接的中心 IP/域名和端口；省略协议和路径时补为 HTTPS 和 `/api/agent/report`。可以设置 CIDR 网段分流，重叠时更具体的网段优先；网页预览与实际安装使用同一规则。
- **证书来源**：本站 CA、公网系统信任链或自定义 PEM CA 公钥证书。不要上传私钥。自定义 CA 不替换网站证书；需确保节点校验证书中的主机名与上报地址匹配。
- **Hermes**：先安装可选运行环境，再填写兼容 API 的 HTTPS 地址、模型 ID 和 API Key，最后开启。模型通过 API 调用，不下载模型权重。留空密钥保留原值；更换 API 地址必须重新填写密钥，勾选清除才删除。
- **站点名称**：所有浏览器共享平台名称和标题；升级时未配置的站点继续采用浏览器已有名称。

保存上报设置只影响后续安装/重部署，不批量修改已运行节点。助手关闭或模型配置变更会中断正在生成的任务，确认停止失败会明确报错；重试保存可再次确认。更改后新建对话。收起聊天窗继续后台执行，服务重启不承诺续跑。

## 配置与备份

| 文件 | 用途 |
| --- | --- |
| `backend/.env` | 本站数据库与环境覆盖，禁止提交 |
| `.runtime/site.json` | 本站地址、端口、CA、运行时和助手路径；环境变量/`.env` 优先 |
| `.runtime/platform.json` | 网页站点名称、安装上报入口、CIDR 和 CA 选择；`PLATFORM_CONFIG_FILE` 可改路径 |
| `.runtime/agent-ca-<摘要>.crt` | 自定义上报 CA 公钥证书 |
| `HERMES_CONFIG_FILE` 指向的私有 JSON | 助手开关、模型、API Key、配置版本；密钥不返回浏览器 |
| `/etc/node-monitor/` | 本站 TLS、SSH 加密密钥及常用凭据；按权限备份 |

网页 Agent 设置保存后作为安装入口配置，`.env`/site.json 中的 `AGENT_REPORT_URL` 只作为未保存网页配置时的默认值。备份配置及加密密钥时保持目录 700、秘密文件 600；不要把旧站点 `.env`、CA 私钥或助手私有目录复制到新站点。

## 可选助手安装

```bash
sudo backend/.venv/bin/python scripts/setup_hermes.py
# 无法访问 GitHub 时，提供包含固定提交的官方仓库或完整浅克隆 Git bundle
sudo backend/.venv/bin/python scripts/setup_hermes.py --source /path/hermes.bundle
```

安装器固定官方 Hermes v0.22.5 提交，为新站点创建独立普通账户、官方托管运行时及 `watch-nanda-hermes.service`，工具不以 root 执行。复用已有服务时先核对普通用户和目录，保留私有 home/历史/密钥；有活动助手任务时拒绝覆盖。主后端可通过同源路径转发，无需额外 Nginx location。主监控安装成功后才安装助手；助手失败不回退健康的监控与数据库。重试可单独运行上述安装器，不能用 `--standalone --with-hermes`。

## 已有代理与更新

已有 Nginx/WAF 配置必须保留，尤其管理网段、SSE 无缓冲和 WebSocket 转发。参考模板包含占位符，不能原样安装。需要新模板时先生成到独立文件审阅：

```bash
python3 scripts/render_nginx.py --host monitor.example.com \
  --allow-network 10.0.0.0/8 --port 443 \
  --static-dir /var/www/node-monitor --output /tmp/node-monitor-nginx.conf
```

该命令不安装或 reload。Nginx 托管站点应保留精确项目归属标记，并在 site.json 设置 `WATCH_NANDA_STATIC_DIR`；独立站点使用 HTTPS preview 双服务。不要在其他网络套用南京雷池入口。

升级前确认无终端、Agent 安装、批量队列或助手生成，备份现有配置和代码差异，再更新已发布提交。结构/依赖未变时按部署说明小范围发布；完整升级使用 `sudo bash setup.sh --systemd`，可按需加入 `--with-hermes`。保持单个后端 worker，不增加 Uvicorn workers。
