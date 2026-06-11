#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DIR="${ZSXQ_DAILY_INSTALL_DIR:-/opt/zsxq-daily-report}"
APP_DIR="$INSTALL_DIR/app"
CONFIG_DIR="$INSTALL_DIR/config"
DATA_DIR="${ZSXQ_DAILY_DATA_DIR:-/data/zsxq-daily-report}"
VENV_DIR="$INSTALL_DIR/venv"

mkdir -p "$APP_DIR" "$CONFIG_DIR" "$DATA_DIR"

cp "$REPO_DIR/src/zsxq_daily_report.py" "$APP_DIR/zsxq_daily_report.py"
cp "$SCRIPT_DIR/zsxq-daily-report.service" /etc/systemd/system/zsxq-daily-report.service
cp "$SCRIPT_DIR/zsxq-daily-report.timer" /etc/systemd/system/zsxq-daily-report.timer

if [ ! -f "$CONFIG_DIR/.env" ]; then
  cp "$SCRIPT_DIR/zsxq-daily-report.env.example" "$CONFIG_DIR/.env"
  chmod 600 "$CONFIG_DIR/.env"
  echo "Created $CONFIG_DIR/.env. Fill it before enabling the timer."
else
  echo "Keeping existing $CONFIG_DIR/.env"
fi

PYTHON_BIN="${ZSXQ_DAILY_PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.9+ is required. Install python3.11 first." >&2
  exit 1
fi

if command -v dnf >/dev/null 2>&1; then
  dnf install -y atk at-spi2-atk at-spi2-core libXcomposite libXdamage libXrandr mesa-libgbm || true
elif command -v yum >/dev/null 2>&1; then
  yum install -y atk at-spi2-atk at-spi2-core libXcomposite libXdamage libXrandr mesa-libgbm || true
fi

if ! command -v lark-cli >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  npm install -g @larksuite/cli@1.0.46
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install playwright

if [ "$(id -u)" -eq 0 ]; then
  "$VENV_DIR/bin/python" -m playwright install --with-deps chromium || "$VENV_DIR/bin/python" -m playwright install chromium
else
  "$VENV_DIR/bin/python" -m playwright install chromium
fi

systemctl daemon-reload

echo
echo "Installed daily report files under $INSTALL_DIR"
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/.env"
echo "  2. Test once:"
echo "     systemctl start zsxq-daily-report.service"
echo "  3. Enable 05:00 daily timer:"
echo "     systemctl enable --now zsxq-daily-report.timer"
