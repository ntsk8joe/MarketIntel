#!/bin/sh
set -eu
PROJECT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PYTHON_BIN="${MARKETINTEL_PYTHON:-python3}"
cd "$PROJECT_DIR"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3,9), "需要 Python 3.9 或更新版本"'
exec "$PYTHON_BIN" "$PROJECT_DIR/app.py" "$@"
