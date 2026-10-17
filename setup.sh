#!/usr/bin/env bash
# Install/upgrade without deleting data or changing existing database credentials.
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
  cat <<'EOF'
用法：bash setup.sh [--systemd] [--skip-deps]
  默认：独立虚拟环境、依赖安装、数据库初始化/迁移、前端构建。
  --systemd  安装/更新本项目 systemd 服务（需要 root）。
  --skip-deps 使用已安装的项目依赖，仍校验数据库、执行迁移并构建前端。

前置环境：Linux、Python 3.10–3.12（含 venv）、Node.js 18+、npm。
已有本地或远程 MySQL 8+ 无需 Docker；只有自动创建本地 MySQL 时需要 Docker。
可配置 MYSQL_CONTAINER、MYSQL_VOLUME、MYSQL_IMAGE、PYTHON_BIN、PIP_INDEX_URL。
已有 .env 不覆盖；首次创建使用随机密码，不在终端打印密码。
EOF
}
SYSTEMD=0
SKIP_DEPS=0
for arg in "$@"; do
  case "$arg" in
    --systemd) SYSTEMD=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    --help|-h) usage; exit 0 ;;
    *) printf '未知参数：%s\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done
trap 'printf "初始化失败（第 %s 行）。已有数据库和凭据不会删除，请修复错误后重跑。\n" "$LINENO" >&2' ERR
command -v "$PYTHON_BIN" >/dev/null || { echo '需要 Python 3.10–3.12。' >&2; exit 1; }
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else "需要 Python 3.10–3.12，以匹配项目依赖。")'
command -v node >/dev/null && command -v npm >/dev/null || { echo '需要安装 Node.js 18+ 和 npm。' >&2; exit 1; }
node -e 'if(Number(process.versions.node.split(".")[0])<18) {console.error("需要 Node.js 18+");process.exit(1)}'
if (( ! SYSTEMD )); then
  MODE=$("$PYTHON_BIN" "$PROJECT_ROOT/scripts/lifecycle.py" mode)
  if [[ "$MODE" == systemd ]]; then SYSTEMD=1; fi
fi
if (( SYSTEMD )); then
  (( EUID == 0 )) || { echo '--systemd 需要 root；普通安装无需 root。' >&2; exit 1; }
  [[ -d /run/systemd/system ]] || { echo '此系统未运行 systemd。' >&2; exit 1; }
fi
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/lifecycle.py" prepare
VENV="$PROJECT_ROOT/backend/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV" || { echo '无法创建 venv；Ubuntu/Debian 请安装对应版本的 python3-venv。' >&2; exit 1; }
fi
"$VENV/bin/python" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else "项目 venv 版本不兼容，请用 Python 3.10–3.12 重新建立 venv。")'
if (( ! SKIP_DEPS )); then
  printf '安装后端依赖到 %s\n' "$VENV"
  "$VENV/bin/python" -m pip install -r "$PROJECT_ROOT/backend/requirements.txt"
  printf '安装前端依赖（使用锁文件）…\n'
  (cd "$PROJECT_ROOT/frontend" && npm ci --no-audit --no-fund)
fi
"$VENV/bin/python" "$PROJECT_ROOT/scripts/initialize.py"
(cd "$PROJECT_ROOT/frontend" && npm run build)
if (( SYSTEMD )); then
  "$VENV/bin/python" "$PROJECT_ROOT/scripts/lifecycle.py" install
fi
printf '\n初始化完成。启动：bash "%s/start.sh"\n' "$PROJECT_ROOT"
printf '已启动的服务请用 bash "%s/start.sh" --restart 加载新版本。\n' "$PROJECT_ROOT"
printf '页面：生产环境 https://<服务器IP>；独立模式 https://<服务器IP>:3000。证书配置见 README.md。\n'
