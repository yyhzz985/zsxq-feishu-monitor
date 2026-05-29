#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/zsxq-monitor"
ARCHIVE_DIR="/data/zsxq-archive/击球区小能手"
LOG_DIR="/var/log/zsxq-monitor"
SERVICE_NAME="zsxq-poll"
SERVICE_USER="${ZSXQ_SERVICE_USER:-${SUDO_USER:-$USER}}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== ZSXQ monitor server setup =="
echo "service user: ${SERVICE_USER}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required"
  exit 1
fi

if [ -f /etc/debian_version ]; then
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip fonts-wqy-microhei
elif [ -f /etc/redhat-release ]; then
  sudo yum install -y python3 python3-pip wqy-microhei-fonts
else
  echo "unknown distro; install Python 3, Pillow and a Chinese font manually"
fi

sudo mkdir -p \
  "${INSTALL_DIR}/app" \
  "${INSTALL_DIR}/config" \
  "${INSTALL_DIR}/data" \
  "${INSTALL_DIR}/temp" \
  "${ARCHIVE_DIR}" \
  "${LOG_DIR}"

sudo cp "${SOURCE_DIR}/zsxq_monitor.py" "${INSTALL_DIR}/app/zsxq_monitor.py"
sudo cp "${SOURCE_DIR}/zsxq-poll.env" "${INSTALL_DIR}/config/.env.example"

if [ ! -f "${INSTALL_DIR}/config/config.json" ]; then
  sudo tee "${INSTALL_DIR}/config/config.json" >/dev/null <<'JSON'
{
  "group_id": "REPLACE_WITH_ZSXQ_GROUP_ID",
  "group_name": "击球区小能手的星球",
  "fetch_count": 1,
  "last_seen_time": null
}
JSON
fi

if [ ! -f "${INSTALL_DIR}/config/.env" ]; then
  sudo cp "${INSTALL_DIR}/config/.env.example" "${INSTALL_DIR}/config/.env"
fi

sudo python3 -m venv "${INSTALL_DIR}/venv"
sudo "${INSTALL_DIR}/venv/bin/python" -m pip install --upgrade pip
sudo "${INSTALL_DIR}/venv/bin/python" -m pip install Pillow

sudo cp "${SOURCE_DIR}/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo sed -i "s/^User=.*/User=${SERVICE_USER}/" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo cp "${SOURCE_DIR}/${SERVICE_NAME}.timer" "/etc/systemd/system/${SERVICE_NAME}.timer"

sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}" "${ARCHIVE_DIR}" "${LOG_DIR}"
sudo systemctl daemon-reload

echo ""
echo "Setup files installed."
echo "Before enabling the timer:"
echo "  1. Edit ${INSTALL_DIR}/config/config.json"
echo "  2. Edit ${INSTALL_DIR}/config/.env and fill real ZSXQ/Feishu OpenAPI credentials"
echo "  3. Test: sudo -u ${SERVICE_USER} ${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/app/zsxq_monitor.py --check"
echo "  4. Enable: sudo systemctl enable --now ${SERVICE_NAME}.timer"
