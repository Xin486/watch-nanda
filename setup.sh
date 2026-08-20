#!/bin/bash
# =====================================================================
# Node Monitor 一键环境初始化脚本
# ---------------------------------------------------------------------
# 功能：
#   1. 自动生成 backend/.env（不存在时，随机生成数据库密码）
#   2. 准备数据库：
#        - .env 中 DB_HOST 为本机(127.0.0.1/localhost) 且无现成数据库
#          → 自动用 Docker 创建 MySQL 容器（数据持久化到数据卷）
#        - 数据库指向远程 → 使用本机 mysql 客户端直连
#      （两种情况都会自动建库并导入 monitor_db.sql 建表，幂等可重复执行）
#   3. 安装后端 (pip，已内置清华镜像源) 与前端 (npm) 依赖
#
# 用法：bash setup.sh
# =====================================================================
set -e

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
ENV_FILE="$BACKEND_DIR/.env"
ENV_EXAMPLE="$BACKEND_DIR/.env.example"
SQL_FILE="$PROJECT_ROOT/monitor_db.sql"
CONTAINER_NAME="watch-nanda-mysql"
VOLUME_NAME="watch-nanda-mysql-data"
# 国内网络拉取 Docker Hub 慢时，可换成：docker.m.daocloud.io/mysql:8.0
MYSQL_IMAGE="mysql:8.0"

log()  { echo -e "\033[1;34m[INFO]\033[0m $1"; }
ok()   { echo -e "\033[1;32m[ OK ]\033[0m $1"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $1"; }
fail() { echo -e "\033[1;31m[FAIL]\033[0m $1"; exit 1; }

echo "===================================================="
echo "🚀 Node Monitor 环境初始化"
echo "===================================================="

# ---------------------------------------------------------------------
# 1. 基础依赖检查
# ---------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "未检测到 docker，请先安装 Docker"
docker info >/dev/null 2>&1 || fail "Docker 未运行或无权限，请启动 Docker（必要时把当前用户加入 docker 组）"
ok "Docker 可用"

if command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3"; else PYTHON_BIN="python"; fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "未检测到 Python，请先安装 Python 3.9+"
PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python 版本: $PY_VER"
case "$PY_VER" in
  3.13|3.14|3.15)
    warn "pydantic 2.5.2 无对应预编译包，Python $PY_VER 安装可能失败；"
    warn "推荐 3.9~3.12（pyenv 环境可执行: pyenv local 3.12.13 后重跑本脚本）"
    ;;
esac

command -v npm >/dev/null 2>&1 || fail "未检测到 npm，请先安装 Node.js 18+"
ok "npm 可用: $(npm --version)"

# ---------------------------------------------------------------------
# 2. 生成 backend/.env
# ---------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    log "未检测到 backend/.env，从模板生成（默认账号 root，密码 NJU.edu@2026）..."
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    # 静态默认账号密码（如需更换，修改 backend/.env 后重跑本脚本即可）
    sed -i "s|^DB_USER=.*|DB_USER=root|" "$ENV_FILE"
    sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=NJU.edu@2026|" "$ENV_FILE"
    ok "已生成 backend/.env（账号 root / 密码 NJU.edu@2026）"
else
    ok "已存在 backend/.env"
fi

# 逐行解析 .env（不用 source，避免密码含特殊字符时被 shell 求值）
read_env() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2-; }
DB_HOST=$(read_env DB_HOST); DB_HOST=${DB_HOST:-127.0.0.1}
DB_PORT=$(read_env DB_PORT); DB_PORT=${DB_PORT:-3306}
DB_USER=$(read_env DB_USER); DB_USER=${DB_USER:-root}
DB_PASSWORD=$(read_env DB_PASSWORD)
DB_NAME=$(read_env DB_NAME); DB_NAME=${DB_NAME:-monitor_db}

[ -n "$DB_PASSWORD" ] || fail "backend/.env 中 DB_PASSWORD 为空，请先填写数据库密码"

log "数据库配置: $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"

# 是否指向"本机数据库"（本机才走 Docker 自动创建）
case "$DB_HOST" in
  127.0.0.1|localhost|::1) LOCAL_DB=1 ;;
  *) LOCAL_DB=0 ;;
esac

# ---------------------------------------------------------------------
# 3. 准备数据库（Docker 自动创建 / 远程直连）
# ---------------------------------------------------------------------
# MYSQL_EXEC：后续统一通过它执行 SQL（密码经 MYSQL_PWD 环境变量传递，不暴露在命令行）
MYSQL_EXEC=""
if [ "$LOCAL_DB" = "1" ]; then
    RUNNING=$(docker ps --format '{{.Names}}' | grep -x "$CONTAINER_NAME" || true)
    EXISTS=$(docker ps -a --format '{{.Names}}' | grep -x "$CONTAINER_NAME" || true)

    if [ -n "$RUNNING" ]; then
        ok "MySQL 容器已在运行: $CONTAINER_NAME"
    elif [ -n "$EXISTS" ]; then
        log "MySQL 容器已存在但未运行，正在启动..."
        docker start "$CONTAINER_NAME" >/dev/null
        ok "已启动 MySQL 容器"
    else
        # 端口占用检测（避免与本机已有 MySQL 冲突）
        PORT_BUSY=$( (ss -tln 2>/dev/null || netstat -tln 2>/dev/null) | grep -E "[:.]$DB_PORT[[:space:]]" || true )
        if [ -n "$PORT_BUSY" ]; then
            warn "端口 $DB_PORT 已被占用（本机可能已装有 MySQL）"
            warn "脚本将跳过 Docker 创建，尝试直连本机 MySQL..."
            command -v mysql >/dev/null 2>&1 || fail "未找到 mysql 客户端；请修改 .env 指向可用数据库后重试"
            MYSQL_EXEC="mysql -h$DB_HOST -P$DB_PORT -u$DB_USER"
        else
            log "创建 MySQL 容器（首次会拉取镜像 $MYSQL_IMAGE，请耐心等待）..."
            docker run -d --name "$CONTAINER_NAME" \
                -e MYSQL_ROOT_PASSWORD="$DB_PASSWORD" \
                -e MYSQL_DATABASE="$DB_NAME" \
                -p "$DB_PORT:3306" \
                -v "$VOLUME_NAME:/var/lib/mysql" \
                --restart unless-stopped \
                "$MYSQL_IMAGE" \
                --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci \
                --innodb-buffer-pool-size=256M \
                --performance-schema=OFF >/dev/null
            ok "MySQL 容器已创建"

            # 若 .env 配置了非 root 账号，首次创建容器后自动建号授权
            if [ "$DB_USER" != "root" ]; then
                MYSQL_PWD="$DB_PASSWORD" docker exec -i "$CONTAINER_NAME" mysql -uroot -e \
                    "CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PASSWORD'; GRANT ALL PRIVILEGES ON *.* TO '$DB_USER'@'%' WITH GRANT OPTION; FLUSH PRIVILEGES;"
            fi
        fi
    fi

    # 等待容器内 MySQL 就绪（最多 120 秒；用真实查询探测，ping 通不代表可执行 SQL）
    if [ -z "$MYSQL_EXEC" ]; then
        log "等待 MySQL 就绪..."
        READY=0
        for i in $(seq 1 60); do
            if MYSQL_PWD="$DB_PASSWORD" docker exec "$CONTAINER_NAME" mysql -u"$DB_USER" -N -e "SELECT 1" >/dev/null 2>&1; then
                READY=1; break
            fi
            sleep 2
        done
        [ "$READY" = "1" ] || fail "MySQL 容器启动超时，请检查日志: docker logs $CONTAINER_NAME"
        sleep 3   # 缓冲：确保初始化完全稳定后再导表
        MYSQL_EXEC="docker exec -i $CONTAINER_NAME mysql -u$DB_USER"
        ok "MySQL 已就绪"
    fi
else
    # 远程数据库：用本机 mysql 客户端直连
    command -v mysql >/dev/null 2>&1 || fail "数据库为远程 ($DB_HOST)，但本机未安装 mysql 客户端；请安装后重试，或手动在远程库执行 monitor_db.sql"
    MYSQL_EXEC="mysql -h$DB_HOST -P$DB_PORT -u$DB_USER"
    log "远程数据库模式，跳过 Docker"
fi

# ---------------------------------------------------------------------
# 4. 建库 + 导入表结构（幂等，可重复执行）
# ---------------------------------------------------------------------
log "创建数据库 $DB_NAME（如不存在）..."
MYSQL_PWD="$DB_PASSWORD" $MYSQL_EXEC -e \
    "CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 使用 --force 导入：已存在的表/索引报错会被忽略，缺失的结构自动补齐
log "导入表结构 monitor_db.sql（幂等，最多重试 3 次）..."
for attempt in 1 2 3; do
    if MYSQL_PWD="$DB_PASSWORD" $MYSQL_EXEC --force "$DB_NAME" < "$SQL_FILE"; then
        break
    fi
    warn "导入中断（第 $attempt 次）。常见原因：宿主机内存不足，MySQL 被 OOM 杀死（可用 free -h 查看）"
    warn "10 秒后重试..."
    sleep 10
done

# 最终校验：四张核心表必须全部存在
FINAL_TABLE_COUNT=$(MYSQL_PWD="$DB_PASSWORD" $MYSQL_EXEC -N -e \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$DB_NAME' AND table_type='BASE TABLE';" 2>/dev/null || echo "0")
if [ "$FINAL_TABLE_COUNT" -ge 4 ]; then
    ok "数据表已就绪（$FINAL_TABLE_COUNT 张：servers / server_stats / alert_configs / email_configs）"
else
    fail "表结构不完整（$FINAL_TABLE_COUNT/4）。请检查容器日志: docker logs $CONTAINER_NAME，以及内存: free -h"
fi

# ---------------------------------------------------------------------
# 5. 安装前后端依赖
# ---------------------------------------------------------------------
log "安装后端依赖 (pip) ..."
cd "$BACKEND_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip -q
"$PYTHON_BIN" -m pip install -r requirements.txt -q
ok "后端依赖安装完成"

log "安装前端依赖 (npm) ..."
cd "$FRONTEND_DIR"
npm install --no-audit --no-fund
ok "前端依赖安装完成"

# ---------------------------------------------------------------------
# 完成
# ---------------------------------------------------------------------
echo "===================================================="
echo "🎉 环境初始化全部完成！"
echo "   启动平台 : cd $PROJECT_ROOT && bash start.sh"
echo "   访问地址 : http://<本机IP>:3000"
echo "   数据库   : MySQL 容器 $CONTAINER_NAME（端口 $DB_PORT）"
echo "===================================================="
