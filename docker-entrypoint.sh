#!/bin/sh
set -e

# Ensure environment
export FLASK_APP="app:create_app"
PYTHON_PATH_DIR="${PYTHON_PATH:-/app}"
INSTANCE_DIR="${PYTHON_PATH_DIR}/instance"
DB_PATH="${INSTANCE_DIR}/replica.db"
APP_DIR="${PYTHON_PATH_DIR}"

mkdir -p "$INSTANCE_DIR"
cd "$APP_DIR"

# Always try DB upgrade (idempotent). If migrations not initialized, init + migrate + upgrade.
echo "[entrypoint] Running DB migrations (idempotent)..."
if [ -d "$APP_DIR/migrations" ]; then
  if ! flask db upgrade; then
    echo "[entrypoint] 'flask db upgrade' failed, trying init+migrate+upgrade..."
    flask db init || true
    flask db migrate -m "init schema" || true
    flask db upgrade || true
  fi
else
  flask db init || true
  flask db migrate -m "init schema" || true
  flask db upgrade || true
fi

# Hand off to the main process
exec "$@"
