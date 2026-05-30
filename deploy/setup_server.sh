#!/usr/bin/env bash
# ============================================================
# ZSXQ → Feishu Monitor — Server Setup Script
# Supports single or multi-instance deployment on 2GB+ servers
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_BASE="/opt"
VENV_DIR="/opt/zsxq-monitor/venv"
LOG_DIR="/var/log/zsxq-monitor"
SOURCE_PY="$SCRIPT_DIR/../src/zsxq_monitor.py"

# ── Color output ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Check root ──
if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root"
    exit 1
fi

# ── Instance definitions ──
# Format: "name|group_id|group_name|footer_brand|interval|on_boot_sec|chat_id"
# Add/remove instances here for your setup
declare -a INSTANCES=(
    "poll|51115584422414|击球区小能手的星球|击球区小能手的星球|1min|1min|oc_xxx"
    "poll-ggggg|48885181184458|香港旅游资讯|港龍亚洲Asia Strategies|3min|70s|oc_xxx"
    "poll-kkkkk|51115858542414|开kkkkkkk史|跨市场资产策略笔记|4min|100s|oc_xxx"
    "poll-honghao|88885882121542|洪灝的宏观策略|洪灝的宏观策略|5min|40s|oc_xxx"
)

# ── Step 1: Install system dependencies ──
info "Step 1/6: Installing system dependencies..."

if command -v yum &>/dev/null; then
    yum install -y python3 python3-pip wqy-microhei-fonts 2>/dev/null || true
elif command -v apt-get &>/dev/null; then
    apt-get update && apt-get install -y python3 python3-venv python3-pip fonts-wqy-microhei 2>/dev/null || true
fi

# ── Step 2: Create swap if RAM < 3GB ──
info "Step 2/6: Checking memory..."
TOTAL_MEM_MB=$(free -m | awk '/Mem:/{print $2}')
if [[ $TOTAL_MEM_MB -lt 3000 ]] && [[ ! -f /swapfile2 ]]; then
    warn "RAM=${TOTAL_MEM_MB}MB (<3GB). Creating 2GB swap file..."
    dd if=/dev/zero of=/swapfile2 bs=1M count=2048 status=progress
    chmod 600 /swapfile2
    mkswap /swapfile2
    swapon /swapfile2
    grep -q 'swapfile2' /etc/fstab || echo '/swapfile2 swap swap defaults 0 0' >> /etc/fstab
    info "Swap created: $(free -m | awk '/Swap:/{print $2}')MB total"
else
    info "Swap OK: $(free -m | awk '/Swap:/{print $2}')MB"
fi

# ── Step 3: Create Python venv ──
info "Step 3/6: Setting up Python venv..."
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install Pillow
    info "Venv created at $VENV_DIR"
else
    info "Venv exists: $VENV_DIR"
fi

# ── Step 4: Create directories and deploy per instance ──
info "Step 4/6: Deploying instances..."

mkdir -p "$LOG_DIR"

for inst_def in "${INSTANCES[@]}"; do
    IFS='|' read -r NAME GID GNAME FOOTER INTERVAL BOOT_SEC CHAT_ID <<< "$inst_def"
    INST_DIR="$INSTALL_BASE/zsxq-monitor-$NAME"
    
    info "  Setting up $NAME ($GNAME)..."
    
    # Create directories
    mkdir -p "$INST_DIR"/{app,config,data,temp}
    
    # Copy script
    if [[ -f "$SOURCE_PY" ]]; then
        cp "$SOURCE_PY" "$INST_DIR/app/zsxq_monitor.py"
    else
        err "Source script not found: $SOURCE_PY"
        exit 1
    fi
    
    # Config file
    cat > "$INST_DIR/config/config.json" << EOFCFG
{
  "group_id": $GID,
  "group_name": "$GNAME",
  "fetch_count": 1,
  "last_seen_time": null,
  "footer_brand": "$FOOTER"
}
EOFCFG
    
    # .env file (fill in real values after setup!)
    cat > "$INST_DIR/config/.env" << EOFENV
ZSXQ_ACCESS_TOKEN=REPLACE_WITH_YOUR_ZSXQ_TOKEN
ZSXQ_SAVE_DIR=/data/zsxq-archive/$GNAME
WATERMARK_TEXT=更新加V：237219265
FEISHU_SEND_MODE=openapi
FEISHU_APP_ID=REPLACE_WITH_FEISHU_APP_ID
FEISHU_APP_SECRET=REPLACE_WITH_FEISHU_APP_SECRET
FEISHU_CHAT_ID=$CHAT_ID
EOFENV
    chmod 600 "$INST_DIR/config/.env"
    
    # Archive directory
    mkdir -p "/data/zsxq-archive/$GNAME"
    
    # systemd service
    SAFE_NAME=$(echo "$NAME" | tr '.' '-')
    cat > "/etc/systemd/system/zsxq-$SAFE_NAME.service" << EOFSVC
[Unit]
Description=ZSXQ to Feishu monitor ($GNAME)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=$INST_DIR/app
Environment=ZSXQ_CONFIG_FILE=$INST_DIR/config/config.json
Environment=ZSXQ_DB_FILE=$INST_DIR/data/zsxq_monitor.db
Environment=ZSXQ_LOCK_FILE=$INST_DIR/data/zsxq_monitor.lock
Environment=ZSXQ_ENV_FILE=$INST_DIR/config/.env
Environment=ZSXQ_LOG_DIR=$LOG_DIR
Environment=ZSXQ_TEMP_DIR=$INST_DIR/temp
EnvironmentFile=$INST_DIR/config/.env
ExecStart=$VENV_DIR/bin/python $INST_DIR/app/zsxq_monitor.py
RuntimeMaxSec=5min

StandardOutput=journal
StandardError=journal
NoNewPrivileges=yes
PrivateTmp=yes
EOFSVC
    
    # systemd timer (staggered)
    cat > "/etc/systemd/system/zsxq-$SAFE_NAME.timer" << EOFTMR
[Unit]
Description=Run ZSXQ $NAME monitor every $INTERVAL
Requires=zsxq-$SAFE_NAME.service

[Timer]
OnBootSec=$BOOT_SEC
OnUnitActiveSec=$INTERVAL
AccuracySec=10s
Persistent=true
Unit=zsxq-$SAFE_NAME.service

[Install]
WantedBy=timers.target
EOFTMR
    
    info "    $NAME deployed"
done

# ── Step 5: Compile and check ──
info "Step 5/6: Compiling and validating..."
for inst_def in "${INSTANCES[@]}"; do
    IFS='|' read -r NAME _ <<< "$inst_def"
    SAFE_NAME=$(echo "$NAME" | tr '.' '-')
    INST_DIR="$INSTALL_BASE/zsxq-monitor-$NAME"
    
    if "$VENV_DIR/bin/python" -m py_compile "$INST_DIR/app/zsxq_monitor.py"; then
        info "  $NAME: compile OK"
    else
        err "  $NAME: compile FAILED"
        exit 1
    fi
    
    # Dry-run check
    ZSXQ_CONFIG_FILE="$INST_DIR/config/config.json" \
    ZSXQ_DB_FILE="$INST_DIR/data/zsxq_monitor.db" \
    ZSXQ_ENV_FILE="$INST_DIR/config/.env" \
    "$VENV_DIR/bin/python" "$INST_DIR/app/zsxq_monitor.py" --check 2>&1 | tail -3
done

# ── Step 6: Enable and start timers ──
info "Step 6/6: Enabling systemd timers..."
systemctl daemon-reload

for inst_def in "${INSTANCES[@]}"; do
    IFS='|' read -r NAME _ <<< "$inst_def"
    SAFE_NAME=$(echo "$NAME" | tr '.' '-')
    systemctl enable "zsxq-$SAFE_NAME.timer"
    systemctl restart "zsxq-$SAFE_NAME.timer"
    info "  zsxq-$SAFE_NAME.timer: enabled + started"
done

# ── Show status ──
echo ""
info "========== Setup Complete =========="
info "Timers:"
systemctl list-timers --no-pager 2>/dev/null | grep zsxq || true
echo ""
warn "NEXT: Edit each instance's .env file with real credentials:"
for inst_def in "${INSTANCES[@]}"; do
    IFS='|' read -r NAME _ <<< "$inst_def"
    echo "  vim $INSTALL_BASE/zsxq-monitor-$NAME/config/.env"
done
echo ""
info "Logs: tail -f $LOG_DIR/zsxq_poll.log"
info "Journal: journalctl -u zsxq-poll.service -f"
