#!/usr/bin/env python3
"""Static validation for the self-hosted Notes renderer deployment."""

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "notes-renderer.compose.yml"
INSTALLER = ROOT / "deploy" / "install_notes_renderer.sh"
PRODUCTION_SOURCES = [
    ROOT / "src" / "note_renderer.py",
    ROOT / "src" / "zsxq_monitor.py",
    ROOT / "qq-feishu-bridge" / "bridge_wu2198.py",
]


def require(errors, text, expected, label):
    if expected not in text:
        errors.append("FAIL: %s missing %r" % (label, expected))


def main():
    errors = []
    for path in [COMPOSE, INSTALLER] + PRODUCTION_SOURCES:
        if not path.is_file():
            errors.append("FAIL: missing %s" % path.relative_to(ROOT))
    if errors:
        print("\n".join(errors))
        return 1

    compose = COMPOSE.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    require(errors, compose, "127.0.0.1:18080:3001", "compose")
    require(errors, compose, "mem_limit: 640m", "compose")
    require(errors, compose, "memswap_limit: 1536m", "compose")
    require(errors, compose, "cpus: 1.0", "compose")
    require(errors, compose, "pids_limit: 256", "compose")
    require(errors, compose, "ipc: host", "compose")
    require(errors, compose, "init: true", "compose")
    require(errors, compose, "restart: unless-stopped", "compose")
    if "zhaoolee/notes:latest" in compose:
        errors.append("FAIL: compose uses floating latest tag")
    if not re.search(r"zhaoolee/notes@sha256:[0-9a-f]{64}", compose):
        errors.append("FAIL: compose image is not pinned by digest")

    for target in (
        "/opt/zsxq-monitor/app",
        "/opt/zsxq-monitor-honghao/app",
        "/opt/zsxq-monitor-kkkkk/app",
        "/opt/zsxq-monitor-ggggg/app",
        "/opt/qq-feishu-bridge-wu2198",
    ):
        require(errors, installer, target, "installer")
    require(errors, installer, "docker cp hermes-notes:/app/src/assets/fonts/.", "installer")
    require(errors, installer, '"$ROOT_DIR/src/zsxq_monitor.py"', "installer")
    require(errors, installer, '"$ROOT_DIR/qq-feishu-bridge/bridge_wu2198.py"', "installer")

    for path in PRODUCTION_SOURCES:
        source = path.read_text(encoding="utf-8")
        if "notes.fangyuanxiaozhan.com" in source:
            errors.append("FAIL: public Notes domain remains in %s" % path.relative_to(ROOT))

    if errors:
        print("\n".join(errors))
        return 1
    print("OK: Notes renderer deployment is pinned, loopback-only, resource-limited, and public-domain-free")
    return 0


if __name__ == "__main__":
    sys.exit(main())
