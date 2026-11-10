# Hermes 悬浮对话

全站右下角的图标展开对话窗，随平台主题变化，切换路由或收起不丢失当前对话。无需运维口令登录，页面自动准备浏览器会话，支持逐步显示回复、停止生成及新建对话。输入框 Enter 发送、Shift + Enter 换行；回复按纯文本呈现，不执行模型返回的 HTML。

节点总览卡片底部中间的 Hermes 小图标可一键复制节点名并展开助手，把名称追加到现有草稿后，保留未发送内容；不会自动发送消息或执行任务。已有后台任务时等待其结束后再发送。剪贴板权限被拒绝时仍填入名称并明确提示；追加后超过 8000 字符则保留原文，提示整理后粘贴或重试。只传递用户点击的节点名称，同名节点仍需按助手职责核对 ID/地址。

收起聊天框只隐藏界面，已提交的任务仍由服务器后台执行，不依赖浏览器持续轮询。悬浮入口显示“任务正在后台执行”，完成/失败/停止后提示点击查看；重新打开会接回同一任务，不重复提交。需要结束工作时点击“停止任务”；浏览器会话到期、任务超时或聊天服务停服也会中断任务。这里的后台执行不承诺跨服务重启续跑。

Hermes 的长期运维职责模板在 `deploy/hermes-monitor-role.md`，部署到其 `home/SOUL.md` 的独立标记区块，保留原有内容及私有备份。职责要求先只读读取 `WATCH_NANDA_MONITOR_URL` 指向的本站 `/api/servers`，按节点 ID、名称和 SSH 地址定位，仅执行用户指定目标与任务；歧义先确认，不自动扩展巡检或修改其他节点。它不授予额外凭据或权限。新建对话加载更新后的职责；已有对话可能保留原系统上下文，不为刷新指令中断正在执行的任务。

## 运行结构

- `frontend/src/components/HermesChat.vue` 挂载在 App 外壳，Teleport 到 body，避免路由或大屏布局裁剪。
- `frontend/src/utils/hermesChat.js` 管理只读快照轮询、请求取消及旧响应保护。生成时约 800 毫秒、打开且空闲时 10 秒刷新；最小化后仅生成期间刷新。失败的消息提交不自动重试，后续只读查询核对实际状态。
- `backend/hermes_bridge.py` 是单独 FastAPI 应用，**单 worker**，回环 `127.0.0.1:7981`；不导入监控调度、数据库或主应用。每次请求向 `127.0.0.1:7980/api/ops/session?validate_only=true` 只核验现有 Cookie，生成期间也持续复核；该模式不能自动补发或续期 Cookie，避免将失效或伪造会话误当有效会话。普通 `/api/ops/session` 由浏览器自动调用以准备会话，无需操作者口令。访问权依赖现有内网/WAF 边界，会话只用于浏览器隔离。
- `/api/ops/hermes/conversations` 创建对话，`/:id` 查询/删除，`/:id/messages` 提交消息，`/:id/stop` 停止。接口仅同源 HTTPS，复用路径为 `/api/ops` 的 HttpOnly/Secure 运维 Cookie。对话所有权绑定 Cookie 摘要，不允许客户端提供 Hermes 原生 session ID。
- `hermes_worker.py` 在 Hermes 官方启动器解析的独立运行时内，通过官方网关 `session.create/resume` 和 JSONL 展示接口工作，stdin 收取消息；续聊仅使用服务自己取得的 session ID。停止时显式发送带执行代数的 `session.interrupt` 并关闭该会话，桥接服务在取消/异常后再次通过控制专用子进程确认中断与关闭；无法确认时明确报错。不能只结束普通 CLI 客户端，否则已提交的任务可能继续。stderr 和原始工具输入/输出不返回浏览器或写入服务日志。工具执行状态仅显示工具名，模型回复本身仍可能包含用户要求的内容。
- 同一浏览器会话最多 1 个、全局最多 2 个生成；单次任务默认最多 30 分钟，可通过服务环境变量 `HERMES_CHAT_TURN_TIMEOUT_SECONDS` 设置（60–3600 秒，仍受运维会话有效期约束）；消息最多 8000 字符、回复最多 128000 字符；每对话最多 100 条消息，最多 50 个内存对话，空闲超过 1 小时在新建时清理。
- 浏览器会话失效后，下一次复核终止生成；停止与 systemd 停服清理专属进程组。对话权限和前端快照只在内存中，重启后需新建，不承诺浏览器恢复原会话；Hermes 自己的原生历史按其配置保留。

## 安装与配置

首次安装可使用 `sudo bash setup.sh --host <本站地址> --with-hermes`；已有站点可单独运行 `sudo backend/.venv/bin/python scripts/setup_hermes.py`。新安装默认关闭，在网页“设置”填写 HTTPS API 地址、模型 ID 与 API Key 后开启。密钥私有保存、不回显、不写入浏览器缓存；更换 API 地址需重新填密钥。模型是云端 API 调用，无需下载权重。详见 [模板部署](PORTABLE_DEPLOYMENT.md)。

助手运行时与监控 Python 独立，固定官方 v0.22.5 提交。安装器生成单 worker 回环桥接服务，并创建独立普通账户和私有 home；已有安装核对目录和非 root 用户后复用，不删除原生历史或密钥。角色模板写入 SOUL 标记区，监控列表地址由 `WATCH_NANDA_MONITOR_URL` 提供，随内部端口生成。

所有开发/独立预览 `/api` 请求先到主后端，再将 Hermes 请求转发至独立桥接；已有南京专用 Nginx location 可保留。端口分别通过 `WATCH_NANDA_BACKEND_PORT` 和 `HERMES_BRIDGE_PORT` 配置，默认 7980/7981。未开启助手时不显示悬浮入口或卡片快捷图标，服务端也拒绝新任务；关闭或配置版本变更会停止旧任务并确认原生清理，不能仅隐藏页面。

南京既有安装位于 `/data2/hermes-cli`，保留普通用户 `test`、私有 home、历史和既有 DeepSeek 配置；新网页配置文件由 `HERMES_CONFIG_FILE` 指定。苏州及新站点不自动复用南京密钥。现有 WAF、CA 和管理网策略保持各站独立，禁止用仓库基础 Nginx 模板覆盖现场配置。

## 验证和回退

```bash
python3 -m unittest discover -s backend/tests -p test_hermes_bridge.py -v
# frontend 内
node --test tests/hermes-chat.test.mjs
npm run build
```

还需在 Linux 验证 systemd 用户权限、同源代理、缺失/伪造会话 401、不同浏览器会话隔离、真实多轮对话、停止生成以及会话撤销后的取消。仅前端发布不重启主监控；修改会话接口时需先确认无活动运维/部署/Hermes 任务，再在维护窗口重启相关服务。

回退先恢复备份首页、App 源码及 Nginx 配置，`nginx -t` 后 reload，再停用 `watch-nanda-hermes`；不要删除 Hermes 密钥、原生历史、现有静态资源或用户文件。回退助手需同时恢复匹配的桥接代码、服务和私有配置版本；升级可通过 `--with-hermes` 管理可选服务，保留现场自定义 location。
