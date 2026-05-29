#!/usr/bin/env python3
"""Validate the ZSXQ monitor deploy bundle before uploading to a server."""
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent

REQUIRED_FILES = [
    "zsxq_monitor.py",
    "setup_server.sh",
    "zsxq-poll.service",
    "zsxq-poll.timer",
    "zsxq-poll.env",
    "install_windows_task.ps1",
    "ZSXQ_Feishu_Monitor_部署与切换手册.md",
]

SENSITIVE_PATTERNS = [
    re.compile(r"ZSXQ_ACCESS_TOKEN=(?!REPLACE_WITH_REAL_TOKEN|不要写进文档)\S+", re.I),
    re.compile(r"FEISHU_APP_SECRET=(?!REPLACE_WITH_FEISHU_APP_SECRET|your_app_secret|不要写进文档)\S+", re.I),
    re.compile(r'"access_token"\s*:\s*"[^"]+"', re.I),
]


def read_text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def add_error(errors, message):
    errors.append("FAIL: " + message)


def add_ok(messages, message):
    messages.append("OK: " + message)


def validate_required_files(errors, messages):
    for name in REQUIRED_FILES:
        path = ROOT / name
        if not path.is_file():
            add_error(errors, f"missing {name}")
        else:
            add_ok(messages, f"found {name}")


def validate_service(errors, messages):
    service = read_text("zsxq-poll.service")
    required = [
        "Type=oneshot",
        "ExecStart=/opt/zsxq-monitor/venv/bin/python /opt/zsxq-monitor/app/zsxq_monitor.py",
        "RuntimeMaxSec=5min",
        "Environment=ZSXQ_CONFIG_FILE=/opt/zsxq-monitor/config/config.json",
        "EnvironmentFile=/opt/zsxq-monitor/config/.env",
    ]
    for text in required:
        if text not in service:
            add_error(errors, f"service missing: {text}")
    if not any(err.startswith("FAIL: service") for err in errors):
        add_ok(messages, "systemd service looks consistent")


def validate_timer(errors, messages):
    timer = read_text("zsxq-poll.timer")
    for text in ("OnUnitActiveSec=1min", "Persistent=true", "Unit=zsxq-poll.service"):
        if text not in timer:
            add_error(errors, f"timer missing: {text}")
    if not any(err.startswith("FAIL: timer") for err in errors):
        add_ok(messages, "systemd timer looks consistent")


def validate_env_example(errors, messages):
    env = read_text("zsxq-poll.env")
    required = [
        "ZSXQ_ACCESS_TOKEN=REPLACE_WITH_REAL_TOKEN",
        "FEISHU_SEND_MODE=openapi",
        "FEISHU_APP_ID=REPLACE_WITH_FEISHU_APP_ID",
        "FEISHU_APP_SECRET=REPLACE_WITH_FEISHU_APP_SECRET",
    ]
    for text in required:
        if text not in env:
            add_error(errors, f"env example missing: {text}")
    if not any(err.startswith("FAIL: env") for err in errors):
        add_ok(messages, "env example contains required keys")


def validate_no_secrets(errors, messages):
    scanned = [
        "zsxq-poll.env",
        "setup_server.sh",
        "zsxq-poll.service",
        "zsxq-poll.timer",
        "ZSXQ_Feishu_Monitor_部署与切换手册.md",
    ]
    leaked = []
    for name in scanned:
        text = read_text(name)
        for pattern in SENSITIVE_PATTERNS:
            if pattern.search(text):
                leaked.append(name)
                break
    if leaked:
        add_error(errors, "possible secret in: " + ", ".join(sorted(set(leaked))))
    else:
        add_ok(messages, "no obvious secrets in deploy docs/examples")


def validate_main_script(errors, messages):
    script = read_text("zsxq_monitor.py")
    required = [
        "with SQLite status tracking",
        "FEISHU_SEND_MODE",
        "feishu_send_image_openapi",
        "check_disk_space",
        "send_daily_health_report",
    ]
    for text in required:
        if text not in script:
            add_error(errors, f"main script missing: {text}")
    if not any(err.startswith("FAIL: main script") for err in errors):
        add_ok(messages, "main script has current reliability features")


def validate_bundle():
    errors = []
    messages = []
    validate_required_files(errors, messages)
    if errors:
        return errors, messages
    validate_service(errors, messages)
    validate_timer(errors, messages)
    validate_env_example(errors, messages)
    validate_no_secrets(errors, messages)
    validate_main_script(errors, messages)
    return errors, messages


def main():
    errors, messages = validate_bundle()
    for line in messages:
        print(line)
    for line in errors:
        print(line)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
