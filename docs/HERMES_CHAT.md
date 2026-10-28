# Hermes 悬浮对话

全站右下角的图标展开对话窗，随平台主题变化，切换路由或收起不丢失当前对话。支持逐步显示回复、停止生成、新建对话和退出运维登录。输入框 Enter 发送、Shift + Enter 换行；回复按纯文本呈现，不执行模型返回的 HTML。

收起聊天框只隐藏界面，已提交的任务仍由服务器后台执行，不依赖浏览器持续轮询。悬浮入口显示“任务正在后台执行”，完成/失败/停止后提示点击查看；重新打开会接回同一任务，不重复提交。需要结束工作时点击“停止任务”；退出运维登录、会话到期、任务超时或聊天服务停服也会中断任务。这里的后台执行不承诺跨服务重启续跑。

Hermes 的长期运维职责模板在 `deploy/hermes-monitor-role.md`，部署到其 `home/SOUL.md` 的独立标记区块，保留原有内容及私有备份。职责要求先只读读取 `http://127.0.0.1:7980/api/servers`，按节点 ID、名称和 SSH 地址定位，仅执行用户指定目标与任务；歧义先确认，不自动扩展巡检或修改其他节点。它不授予额外凭据或权限。新建对话加载更新后的职责；已有对话可能保留原系统上下文，不为刷新指令中断正在执行的任务。

## 运行结构

- `frontend/src/components/HermesChat.vue` 挂载在 App 外壳，Teleport 到 body，避免路由或大屏布局裁剪。
- `frontend/src/utils/hermesChat.js` 管理只读快照轮询、请求取消及旧响应保护。生成时约 800 毫秒、打开且空闲时 10 秒刷新；最小化后仅生成期间刷新。失败的消息提交不自动重试，后续只读查询核对实际状态。
- `backend/hermes_bridge.py` 是单独 FastAPI 应用，**单 worker**，回环 `127.0.0.1:7981`；不导入监控调度、数据库或主应用。每次请求向 `127.0.0.1:7980/api/ops/session` 核验现有 Cookie，生成期间也持续复核。
- `/api/ops/hermes/conversations` 创建对话，`/:id` 查询/删除，`/:id/messages` 提交消息，`/:id/stop` 停止。接口仅同源 HTTPS，复用路径为 `/api/ops` 的 HttpOnly/Secure 运维 Cookie。对话所有权绑定 Cookie 摘要，不允许客户端提供 Hermes 原生 session ID。
- `hermes_worker.py` 在 Hermes 官方启动器解析的独立运行时内，通过官方网关 `session.create/resume` 和 JSONL 展示接口工作，stdin 收取消息；续聊仅使用服务自己取得的 session ID。停止时显式发送带执行代数的 `session.interrupt` 并关闭该会话，桥接服务在取消/异常后再次通过控制专用子进程确认中断与关闭；无法确认时明确报错。不能只结束普通 CLI 客户端，否则已提交的任务可能继续。stderr 和原始工具输入/输出不返回浏览器或写入服务日志。工具执行状态仅显示工具名，模型回复本身仍可能包含用户要求的内容。
- 同一登录会话最多 1 个、全局最多 2 个生成；单次任务默认最多 30 分钟，可通过服务环境变量 `HERMES_CHAT_TURN_TIMEOUT_SECONDS` 设置（60–3600 秒，仍受运维会话有效期约束）；消息最多 8000 字符、回复最多 128000 字符；每对话最多 100 条消息，最多 50 个内存对话，空闲超过 1 小时在新建时清理。
- 登出/会话到期后，下一次复核终止生成；停止与 systemd 停服清理专属进程组。对话权限和前端快照只在内存中，重启后需新建，不承诺浏览器恢复原会话；Hermes 自己的原生历史按其配置保留。

## 南京安装与发布

2026-10-28 安装的 Hermes 位于 `/data2/hermes-cli`，源码 `hermes-agent/`，配置与工具 `home/`，`/home/test/.hermes` 链接到 `home/`。默认 DeepSeek `deepseek-flash`，通过云端 API 调用；密钥只保存在远程 `home/.env`，权限 600。Hermes 自有 Python 3.14 与监控 Python 3.10–3.12 分开，不替换监控运行时。

1. 在确认 Hermes CLI 可正常运行后，把 `backend/hermes_bridge.py` 和 `backend/hermes_worker.py` 放到 `/data2/hermes-cli/web-bridge/`，属主 `test`。桥接使用可导入项目 FastAPI/uvicorn 的兼容 Python；南京为 `/usr/bin/python3`。适配器按 Hermes v0.22.5 的网关接口验证，升级 Hermes 后须复测续聊和停止；官方运行时 machine boundary 避免绑定过期 venv。
2. 按现场路径调整 `deploy/production/watch-nanda-hermes.service`，安装到 `/etc/systemd/system/`。它以 `test` 运行，工作区 `/data2/hermes-cli/workspace`，无需 root 或额外 sudo 权限；不会自动注入平台状态、数据库连接或 SSH 密码。需要交互审批的工具操作可回到 CLI 完成，不启用 `--yolo`。
3. 在**现有应用 server 内** include `deploy/production/hermes-chat-location.conf`。南京应放到回环 18080 的 origin server，保留 443 → 雷池 8088 → 18080 链路和现有 LAN/SD-WAN 策略。不要用仓库基础 Nginx 模板覆盖现场配置。
4. 先 `nginx -t`，再启动 `watch-nanda-hermes.service` 并平滑 reload Nginx。此接入无需重启主监控后端、数据库迁移或运行整套 setup。
5. 基于当前生产前端，仅加入 App 挂载及上述新组件/工具文件后构建。备份首页与源码，先复制新哈希资源，最后原子替换首页；保留旧资源支持已打开的浏览器。

示例服务启停（完成上述审阅及路径调整后）：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now watch-nanda-hermes.service
sudo nginx -t && sudo systemctl reload nginx
```

开发/预览的 `/api/ops/hermes` 代理到回环 7981，其他 `/api` 仍到 7980。未配置独立服务的站点可以看到入口，但登录/发送时会明确提示服务不可用。

## 验证和回退

```bash
python3 -m unittest discover -s backend/tests -p test_hermes_bridge.py -v
# frontend 内
node --test tests/hermes-chat.test.mjs
npm run build
```

还需在 Linux 验证 systemd 用户权限、同源代理、未登录 401、不同登录会话隔离、真实多轮对话、停止生成以及登出后的取消。主监控服务进程和采集/告警不应因发布重启。

回退先恢复备份首页、App 源码及 Nginx 配置，`nginx -t` 后 reload，再停用 `watch-nanda-hermes`；不要删除 Hermes 密钥、原生历史、现有静态资源或用户文件。`setup.sh` 暂不管理可选聊天服务，未来整套升级须核对并保留自定义 location。
