#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
case "${1:-}" in
  --help|-h) echo '用法：bash stop.sh；只停止本项目服务，保留数据库与其他项目进程。'; exit 0 ;;
  '') ;;
  *) echo '未知参数；使用 bash stop.sh --help。' >&2; exit 2 ;;
esac
(( $# == 0 )) || { echo '参数过多。' >&2; exit 2; }
PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="${PYTHON_BIN:-python3}"
exec "$PYTHON" "$PROJECT_ROOT/scripts/lifecycle.py" stop
