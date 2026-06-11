#!/usr/bin/env bash
# Prepare the self-hosted Notes renderer. Run only after production approval.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="/opt/notes-renderer"
COMPOSE_FILE="$INSTALL_DIR/compose.yml"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root" >&2
    exit 1
fi

for command in docker python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "ERROR: missing command: $command" >&2
        exit 1
    fi
done

if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: docker compose plugin is required" >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR/storage/images" "$INSTALL_DIR/fonts"
install -m 0644 "$SCRIPT_DIR/notes-renderer.compose.yml" "$COMPOSE_FILE"

docker compose -f "$COMPOSE_FILE" pull
docker compose -f "$COMPOSE_FILE" up -d

for _attempt in $(seq 1 60); do
    if python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:18080/api/health", timeout=2) as response:
    payload = json.loads(response.read().decode("utf-8"))
    raise SystemExit(0 if payload.get("ok") is True else 1)
PY
    then
        break
    fi
    sleep 2
done

python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:18080/api/health", timeout=5) as response:
    payload = json.loads(response.read().decode("utf-8"))
if payload.get("ok") is not True:
    raise SystemExit("notes renderer health check failed")
PY

docker cp hermes-notes:/app/src/assets/fonts/. "$INSTALL_DIR/fonts/"
chmod 0644 "$INSTALL_DIR"/fonts/OPPOSans-*.ttf

declare -a ZSXQ_TARGET_DIRS=(
    "/opt/zsxq-monitor/app"
    "/opt/zsxq-monitor-honghao/app"
    "/opt/zsxq-monitor-kkkkk/app"
    "/opt/zsxq-monitor-ggggg/app"
)

for target_dir in "${ZSXQ_TARGET_DIRS[@]}"; do
    if [[ ! -d "$target_dir" ]]; then
        echo "ERROR: target service directory does not exist: $target_dir" >&2
        exit 1
    fi
    install -m 0644 "$ROOT_DIR/src/zsxq_monitor.py" "$target_dir/zsxq_monitor.py"
    install -m 0644 "$ROOT_DIR/src/note_renderer.py" "$target_dir/note_renderer.py"
    install -m 0644 "$ROOT_DIR/src/local_notes_fallback.py" "$target_dir/local_notes_fallback.py"
    python3 -m py_compile \
        "$target_dir/zsxq_monitor.py" \
        "$target_dir/note_renderer.py" \
        "$target_dir/local_notes_fallback.py"
done

WU_TARGET_DIR="/opt/qq-feishu-bridge-wu2198"
if [[ ! -d "$WU_TARGET_DIR" ]]; then
    echo "ERROR: target service directory does not exist: $WU_TARGET_DIR" >&2
    exit 1
fi
install -m 0644 "$ROOT_DIR/qq-feishu-bridge/bridge_wu2198.py" "$WU_TARGET_DIR/bridge_wu2198.py"
install -m 0644 "$ROOT_DIR/src/note_renderer.py" "$WU_TARGET_DIR/note_renderer.py"
install -m 0644 "$ROOT_DIR/src/local_notes_fallback.py" "$WU_TARGET_DIR/local_notes_fallback.py"
python3 -m py_compile \
    "$WU_TARGET_DIR/bridge_wu2198.py" \
    "$WU_TARGET_DIR/note_renderer.py" \
    "$WU_TARGET_DIR/local_notes_fallback.py"

echo "Notes renderer prepared and healthy."
echo "Production services were not restarted. Restart them only after explicit approval."
echo "  systemctl restart zsxq-poll.service zsxq-poll-honghao.service zsxq-poll-kkkkk.service zsxq-poll-ggggg.service"
echo "  systemctl restart qq-bridge-wu2198.service"
