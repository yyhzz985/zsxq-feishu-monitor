#!/usr/bin/env python3
# QQ -> Feishu Bridge v3
# Content -> formal group, Alerts -> test group, Filter forwards + URLs

import asyncio, json, os, sqlite3, ssl, sys, time, traceback, uuid, mimetypes
import urllib.request, urllib.error

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:
    websockets = None

def env(k, d=""):
    return os.environ.get(k, d)

NAPCAT_WS = env("NAPCAT_WS", "ws://napcat:3001")
NAPCAT_TOKEN = env("NAPCAT_TOKEN", "")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
FEISHU_APP_ID = env("ZSXQ_FEISHU_APP_ID") or env("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = env("ZSXQ_FEISHU_APP_SECRET") or env("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = env("FEISHU_CHAT_ID", "")
DB_FILE = env("QQ_BRIDGE_DB", "/app/data/qq_bridge.db")
TEMP_DIR = env("QQ_BRIDGE_TEMP", "/app/data/temp")
RECONNECT_DELAY_INITIAL = 10
RECONNECT_DELAY_MAX = 300
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
ctx = ssl.create_default_context()

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [QQ] {msg}", flush=True)

_config = None
def load_config():
    global _config
    if _config is None:
        try:
            cf = os.environ.get("QQ_BRIDGE_CONFIG", "/app/config.json")
            with open(cf) as f:
                _config = json.load(f)
        except:
            _config = {}
    return _config

def get_content_chat_id():
    return load_config().get("feishu", {}).get("content_chat_id") or FEISHU_CHAT_ID

def get_alert_chat_id():
    return load_config().get("feishu", {}).get("alert_chat_id") or FEISHU_CHAT_ID

def get_target_groups():
    return [g.get("group_id") for g in load_config().get("groups", [])]

def has_url(text):
    return "http://" in text or "https://" in text

def strip_urls(text):
    words = text.split()
    result = []
    for w in words:
        if w.startswith("http://") or w.startswith("https://"):
            continue
        result.append(w)
    return " ".join(result)