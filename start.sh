#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ACTION=start
case "${1:-}" in
  --help|-h) echo '用法：bash start.sh [--restart]；自动使用本项目的 systemd 服务，否则后台启动并记录 PID。'; exit 0 ;;
  --restart) ACTION=restart ;;
  '') ;;
  *) echo '未知参数；使用 bash start.sh --help。' >&2; exit 2 ;;
esac
(( $# <= 1 )) || { echo '参数过多。' >&2; exit 2; }
PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  # Compatibility with existing deployments before their next setup.sh run.
  PYTHON="${PYTHON_BIN:-python3}"
fi
exec "$PYTHON" "$PROJECT_ROOT/scripts/lifecycle.py" "$ACTION"
