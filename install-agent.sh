#!/usr/bin/env bash
# Run on the monitoring server; SSH passwords are read without terminal echo.
set -Eeuo pipefail
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="$PROJECT_ROOT/backend/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="${PYTHON_BIN:-python3}"
exec "$PYTHON" "$PROJECT_ROOT/scripts/install_agent.py" "$@"
