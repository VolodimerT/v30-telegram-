#!/usr/bin/env sh
set -eu

echo "BOOT: railway_start.sh started"
echo "BOOT: pwd=$(pwd)"
echo "BOOT: files in /app:"
ls -la /app | head -80 || true

PYBIN="/app/.venv/bin/python"

if [ ! -x "$PYBIN" ]; then
  echo "FATAL: Python venv not found at $PYBIN"
  echo "DEBUG: /app/.venv/bin:"
  ls -la /app/.venv/bin 2>/dev/null || true
  exit 127
fi

echo "BOOT: python found: $PYBIN"
"$PYBIN" --version

echo "BOOT: compiling main.py"
"$PYBIN" -m py_compile /app/main.py
echo "BOOT: compile OK"

echo "BOOT: starting bot main.py"
exec "$PYBIN" -u /app/main.py
