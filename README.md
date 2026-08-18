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

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt

cd ../frontend
npm install
```

### 2. 配置数据库连接（唯一需要改的配置）

```bash
cd backend
cp .env.example .env
vim .env    # 修改 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
```

`.env` 说明：

- 已被 `.gitignore` 忽略，不会提交到版本库（密码安全）。
- 环境变量优先级高于 `.env` 文件（例如 docker 部署可直接 `export DATABASE_URL=...` 整体覆盖）。
- 密码支持特殊字符，无需手动转义。

### 3. 初始化数据库

```bash
mysql -u root -p < monitor_db.sql
```

### 4. 配置 SSH 免密（后端 → 被监控服务器）

运行后端的机器必须能以节点配置的账号（默认 root）免密 SSH 登录每台被监控服务器，
否则节点将一直显示离线：

```bash
ssh-copy-id root@<被监控服务器IP>
```

### 5. 启动 / 停止

```bash
bash start.sh   # 同时拉起后端(7980)与前端(3000)
bash stop.sh    # 一键安全关闭
```

启动后访问：`http://<服务器IP>:3000`

## 目录结构

```
watch-nanda/
├── start.sh / stop.sh        # 启停脚本
├── monitor_db.sql            # 数据库建表语句
├── README.md
├── backend/                  # FastAPI 后端
│   ├── main.py               # API 路由入口
│   ├── requirements.txt      # 依赖清单
│   ├── .env.example          # 数据库连接配置模板
│   ├── .env                  # 实际配置（本地修改，不入库）
│   ├── core/config.py        # 配置加载（读 .env）
│   ├── models/               # ORM 模型与数据库连接
│   └── worker/tasks.py       # SSH 采集 / 告警 / 定时调度
└── frontend/                 # Vue 3 前端
    └── src/
        ├── api.js            # 统一后端 API 封装
        ├── App.vue           # 全局外壳（侧边栏 + 路由出口）
        ├── components/       # 通用组件（环形进度图等）
        └── views/            # 控制台 / 大屏 / 历史 / 设置 / 清理
```
