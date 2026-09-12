# Node Monitor 服务器监控平台

通过 SSH 免密登录采集集群中每台服务器的 CPU / 内存 / GPU 数据，
提供控制台卡片、算力大屏、历史曲线与离线邮件告警。

## 技术栈

| 组件 | 技术 | 端口 |
|------|------|------|
| 前端 | Vue 3 + Vite | 3000 |
| 后端 | FastAPI + APScheduler + Paramiko | 7980 |
| 数据库 | MySQL（库名 `monitor_db`） | 3306 |

## 部署步骤

### 方式一：一键初始化（推荐）

要求机器已安装 Docker、Python 3.9~3.12、Node.js 18+，然后：

```bash
bash setup.sh    # 自动：生成 .env → Docker 拉起 MySQL → 建库导表 → 安装前后端依赖
bash start.sh    # 启动平台
```

`setup.sh` 是幂等的，可重复执行；若 `.env` 中 `DB_HOST` 指向远程数据库，
脚本会跳过 Docker 创建，直接用本机 `mysql` 客户端建库导表。

### 方式二：手动部署

#### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt

cd ../frontend
npm install
```

#### 2. 配置数据库连接（唯一需要改的配置）

```bash
cd backend
cp .env.example .env
vim .env    # 修改 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
```

`.env` 说明：

- 已被 `.gitignore` 忽略，不会提交到版本库（密码安全）。
- 环境变量优先级高于 `.env` 文件（例如 docker 部署可直接 `export DATABASE_URL=...` 整体覆盖）。
- 密码支持特殊字符，无需手动转义。

#### 3. 初始化数据库

```bash
mysql -u root -p < monitor_db.sql
```

### 配置 SSH 免密（后端 → 被监控服务器）

运行后端的机器必须能以节点配置的账号（默认 root）免密 SSH 登录每台被监控服务器，
否则节点将一直显示离线：

```bash
ssh-copy-id root@<被监控服务器IP>
```

### 启动 / 停止

```bash
bash start.sh   # 同时拉起后端(7980)与前端(3000)
bash stop.sh    # 一键安全关闭
```

启动后访问：`http://<服务器IP>:3000`

## 实时数据架构

### 采集与数据新鲜度

- SSH 采集分为读取配置、无数据库连接的网络采集、短事务写回三个阶段。
- 启动和每分钟调度共用去重队列；同一节点排队或执行期间不会再次提交。
- REST 与 SSE 共用状态计算：`online` 在线、`offline` 明确采集失败、
  `stale` 数据过期、`pending` 尚未采集。`collection_status` 保留最近采集结果。
- `latest_status_data.timestamp` 是采集完成时间；无需修改已有数据库表。
- `STALE_AFTER_SECONDS` 环境变量控制过期阈值，默认 180 秒，最小 60 秒。
  浏览器按服务端数据年龄持续计时，网络断开时也会显示过期。
- 过期数据不计入大屏在线算力，历史快照以对应状态记录，查询时显示为空值缺口。
- SMTP 密码读取接口仅返回 `has_password`；保存时省略 `smtp_password` 保留原值。

### 回归测试

```bash
python3 -m unittest discover -s backend/tests -v
cd frontend
node --test tests/state.test.mjs
npm run build
```

后端测试使用独立临时 SQLite，SSH 与 SMTP 均模拟，不访问生产节点或发送邮件。
前端状态测试覆盖 SSE/REST 竞争、断流后的过期判断、表单编辑保护、历史请求乱序及详情实时指标。

平台采用 **SSE (Server-Sent Events)** 替代传统轮询：

- **后端**：`/api/stream/servers` 端点每 10 秒推送一次最新数据（只读数据库，不触发 SSH）
- **SSH 采集**：每 60 秒并发采集一次
- **前端**：`useServers` composable 管理单例 EventSource 连接，多组件共享
- **启动即采集**：FastAPI 启动时立即执行一次全量 SSH 采集，页面刷新后零延迟
- **历史查询优化**：`server_stats` 表的 `(server_id, timestamp)` 复合索引加速历史曲线加载

运行 `add_index.sql` 可为已有数据库添加复合索引（新建库已包含）：
```bash
mysql -u root -p monitor_db < add_index.sql
```

## 目录结构

```
watch-nanda/
├── setup.sh                  # 一键环境初始化（.env + Docker MySQL + 建表 + 依赖）
├── start.sh / stop.sh        # 启停脚本
├── monitor_db.sql            # 数据库建表语句
├── add_index.sql             # 历史查询性能优化索引（可选执行）
├── README.md
├── backend/                  # FastAPI 后端
│   ├── main.py               # API 路由入口 + SSE 实时推送端点
│   ├── requirements.txt      # 依赖清单
│   ├── .env.example          # 数据库连接配置模板
│   ├── .env                  # 实际配置（本地修改，不入库）
│   ├── core/config.py        # 配置加载（读 .env）
│   ├── models/               # ORM 模型与数据库连接
│   └── worker/tasks.py       # SSH 采集 / 告警 / 定时调度
└── frontend/                 # Vue 3 前端
    └── src/
        ├── api.js            # 统一后端 API 封装（REST 接口）
        ├── composables/      # 组合式函数
        │   └── useServers.js # SSE 实时数据推送（替代 60s 轮询）
        ├── App.vue           # 全局外壳（侧边栏 + 路由出口）
        ├── components/       # 通用组件（环形进度图等）
        └── views/            # 控制台 / 大屏 / 历史 / 设置 / 清理
```
