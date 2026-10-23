#!/usr/bin/env bash
# Complete fresh installation and in-place upgrade. Never reset application data.
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
  cat <<'EOF'
用法：sudo bash setup.sh [--host IP或域名] [--systemd|--standalone] [--skip-deps]
  自动准备 Python 3.10–3.12、Node.js 18+、依赖、MySQL、增量结构、HTTPS、运维登录和服务。
  默认 root/systemd 系统安装自启服务；其他环境使用后台模式。完成后自动启动并检查 HTTPS。
  已运行的本项目会停止后覆盖安装；失败恢复依赖、构建及服务配置，保留数据库增量变更。
  --host              首次安装的 HTTPS 地址；省略时自动选择主机地址。已有站点保留原值。
  --skip-deps         复用现有依赖（仍迁移、构建和校验）。
  --skip-agent-build  不构建独立 Agent 包（仅监控/运维；网页 Agent 部署暂不可用）。
  --ops-password-file 从当前账户拥有、权限 600 的文件读取首次运维口令；已配置时保留原口令。
  --no-start          初始化完成后保持停止。
  --no-bootstrap     不自动安装缺少的系统组件；离线/受控环境使用。
支持 Linux Debian/Ubuntu（apt）及 RHEL 系列（dnf）。已有 Python/Node/MySQL 可直接复用。
配置、密钥、证书和数据库不覆盖；备份及恢复说明保存在 .runtime/setup-backups/。
EOF
  exit 0
fi
[[ "$(uname -s)" == Linux ]] || { echo 'setup.sh 仅支持 Linux。' >&2; exit 1; }
BOOTSTRAP_PYTHON=""
find_bootstrap_python() {
  local candidate
  for candidate in "${PYTHON_BIN:-python3}" "$PROJECT_ROOT/backend/.venv/bin/python" python3 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys;sys.exit(sys.version_info[:2]<(3,9))' 2>/dev/null; then
      BOOTSTRAP_PYTHON="$candidate"
      return 0
    fi
  done
  return 1
}
if ! find_bootstrap_python; then
  [[ " $* " != *" --no-bootstrap "* && $EUID == 0 ]] || { echo '需要 python3 或 root 自动安装。' >&2; exit 1; }
  if command -v apt-get >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates
  elif command -v dnf >/dev/null; then
    dnf install -y python3.12 python3.12-pip ca-certificates
  else echo '不支持的包管理器，请先安装 Python 3。' >&2; exit 1; fi
fi
find_bootstrap_python || { echo '引导程序需要 Python 3.9+；请使用受支持的 Linux 或指定 PYTHON_BIN。' >&2; exit 1; }
exec "$BOOTSTRAP_PYTHON" "$PROJECT_ROOT/scripts/setup_project.py" "$@"
