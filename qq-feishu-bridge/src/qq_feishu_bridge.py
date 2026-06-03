#!/usr/bin/env python3
"""
QQ -> Feishu Bridge v3
- Clean text/pics/files (no sender/time metadata)
- Direct URL download for QQ media
- SQLite dedup
"""

import asyncio, json, os, sqlite3, ssl, sys, time, traceback, uuid, mimetypes, urllib.request, urllib.error

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


_config = {}

def load_config():
    global _config
    if not _config:
        try:
            cf = os.environ.get("QQ_BRIDGE_CONFIG", "/app/config.json")
            with open(cf) as f:
                _config.update(json.load(f))
        except:
            pass
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
    return " ".join(w for w in text.split() if not w.startswith("http"))

def send_alert(text):
    cid = get_alert_chat_id()
    log("ALERT: " + text[:100])
    return fs_send_text("[QQ Bridge] " + text, cid)

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [QQ] {msg}", flush=True)

# ---- SQLite ----
def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS qq_messages (
        message_id TEXT PRIMARY KEY, group_id INTEGER, sender_name TEXT,
        msg_type TEXT, content TEXT, status TEXT DEFAULT 'received',
        feishu_msg_id TEXT, retry_count INTEGER DEFAULT 0,
        last_error TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    conn.commit()
    return conn

def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def set_meta(conn, k, v):
    ts = now_text()
    conn.execute("INSERT INTO meta(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (k, str(v), ts))
    conn.commit()

def msg_exists(conn, mid):
    return conn.execute("SELECT 1 FROM qq_messages WHERE message_id=?", (str(mid),)).fetchone() is not None

def insert_msg(conn, mid, gid, sname, mtype, content):
    ts = now_text()
    conn.execute("INSERT OR IGNORE INTO qq_messages(message_id,group_id,sender_name,msg_type,content,status,created_at,updated_at) VALUES(?,?,?,?,?,'received',?,?)",
                 (str(mid), int(gid), sname or "", mtype or "", content or "", ts, ts))
    conn.commit()
    return conn.total_changes > 0

def mark_sent(conn, mid, fmsgid=""):
    conn.execute("UPDATE qq_messages SET status='sent',feishu_msg_id=?,last_error=NULL,updated_at=? WHERE message_id=?", (fmsgid, now_text(), str(mid)))
    conn.commit()

def mark_failed(conn, mid, err):
    conn.execute("UPDATE qq_messages SET status='failed',retry_count=retry_count+1,last_error=?,updated_at=? WHERE message_id=?", (str(err)[:2000], now_text(), str(mid)))
    conn.commit()

# ---- Feishu API ----
_token_cache = {"t": "", "e": 0}

def get_fs_token():
    now = time.time()
    if _token_cache["t"] and now < _token_cache["e"] - 300:
        return _token_cache["t"]
    b = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request(f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal", data=b, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        d = json.loads(r.read())
        _token_cache["t"] = d["tenant_access_token"]
        _token_cache["e"] = now + d.get("expire", 7200)
        return _token_cache["t"]

def fs_api(url, payload, timeout=30):
    token = get_fs_token()
    b = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=b, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())

def fs_send_text(text, chat_id=None):
    if not text or not text.strip():
        return True
    cid = chat_id or get_content_chat_id()
    uid = str(uuid.uuid4())
    r = fs_api(f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
               {"receive_id": cid, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False), "uuid": uid})
    ok = r.get("code") == 0
    if not ok:
        log(f"  text FAIL: {r.get('code')} {r.get('msg','')}")
    return ok

def fs_upload_media(filepath, media_type="image"):
    token = get_fs_token()
    with open(filepath, "rb") as f:
        data = f.read()
    ct = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    boundary = "----QQBridge" + os.urandom(8).hex()
    fname = os.path.basename(filepath)
    parts = []
    if media_type == "image":
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="image_type"\r\n\r\nmessage\r\n')
    else:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(b'Content-Disposition: form-data; name="file_type"\r\n\r\nstream\r\n')
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="file_name"\r\n\r\n{fname}\r\n'.encode())
    field = "image" if media_type == "image" else "file"
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="{field}"; filename="{fname}"\r\nContent-Type: {ct}\r\n\r\n'.encode())
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    url = f"{FEISHU_API_BASE}/im/v1/images" if media_type == "image" else f"{FEISHU_API_BASE}/im/v1/files"
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            d = json.loads(r.read())
            if d.get("code") == 0:
                key = d.get("data", {}).get("image_key") or d.get("data", {}).get("file_key", "")
                return True, key
            return False, str(d)
    except Exception as e:
        return False, str(e)

def fs_send_media(filepath, media_type="image"):
    ok, key = fs_upload_media(filepath, media_type)
    if not ok:
        log(f"  upload FAIL: {key[:80]}")
        return False
    uid = str(uuid.uuid4())
    content = {"image_key": key} if media_type == "image" else {"file_key": key, "file_name": os.path.basename(filepath)}
    r = fs_api(f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
               {"receive_id": FEISHU_CHAT_ID, "msg_type": media_type, "content": json.dumps(content, ensure_ascii=False), "uuid": uid})
    ok = r.get("code") == 0
    if not ok:
        log(f"  send FAIL: {r.get('code')}")
    return ok

def download_qq_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://qq.com/"})
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return r.read()
    except Exception as e:
        log(f"  download err: {e}")
        return None

# ---- Message processing ----
async def process_message(ws, event, conn):
    gid = event.get("group_id", 0)
    mid = event.get("message_id", 0)
    segs = event.get("message", [])

    content_cid = get_content_chat_id()
    if isinstance(segs, str):
        if not segs.strip():
            return
        insert_msg(conn, mid, gid, "", "text", segs)
        fs_send_text(segs)
        mark_sent(conn, mid)
        return

    # Build content for dedup + process parts
    text_buf = []
    all_content = []

    for seg in segs:
        t = seg.get("type", "")
        d = seg.get("data", {})

        if t in ("forward", "json"):
            log("  SKIP " + t)
            continue
        if t == "xml":
            if "http" in str(d.get("data", "")).lower():
                log("  SKIP xml")
                continue
        if t == "text":
            txt = d.get("text", "")
            if has_url(txt):
                txt = strip_urls(txt)
                if not txt.strip():
                    continue
            text_buf.append(txt)
            all_content.append(txt)
        elif t == "image":
            # Flush text
            txt = "".join(text_buf).strip()
            text_buf = []
            if txt:
                fs_send_text(txt)

            url = d.get("url", "")
            all_content.append("[img]")
            if url:
                data = download_qq_url(url)
                if data:
                    ext = ".png" if "png" in url.lower() else ".jpg"
                    fpath = os.path.join(TEMP_DIR, f"qqimg_{os.urandom(4).hex()}{ext}")
                    with open(fpath, "wb") as f:
                        f.write(data)
                    fs_send_media(fpath, "image")
                    try: os.remove(fpath)
                    except: pass
        elif t == "file":
            txt = "".join(text_buf).strip()
            text_buf = []
            if txt:
                fs_send_text(txt)

            url = d.get("url", "")
            fname = d.get("file", "file")
            all_content.append(f"[file:{fname}]")
            if url and url.startswith("http"):
                data = download_qq_url(url)
                if data:
                    fpath = os.path.join(TEMP_DIR, fname)
                    with open(fpath, "wb") as f:
                        f.write(data)
                    fs_send_media(fpath, "file")
                    try: os.remove(fpath)
                    except: pass
        elif t == "at":
            name = d.get("name", "") or f"@{d.get('qq', '')}"
            text_buf.append(name + " ")
            all_content.append(name + " ")
        elif t == "face":
            text_buf.append("[emoji]")
            all_content.append("[emoji]")
        elif t == "record":
            text_buf.append("[voice]")
            all_content.append("[voice]")
        elif t == "video":
            text_buf.append("[video]")
            all_content.append("[video]")

    # Flush remaining text
    txt = "".join(text_buf).strip()
    if txt:
        fs_send_text(txt)

    # DB
    content_str = "".join(all_content)[:500]
    if content_str:
        insert_msg(conn, mid, gid, "", "mixed", content_str)
        mark_sent(conn, mid)

# ---- Main loop ----
async def main_loop():
    if not FEISHU_APP_ID or not FEISHU_CHAT_ID:
        log("ERROR: Missing Feishu config")
        return

    config_file = os.environ.get("QQ_BRIDGE_CONFIG", "/app/config.json")
    config = {"groups": []}
    try:
        with open(config_file) as f:
            config = json.load(f)
        log(f"Config: {len(config.get('groups',[]))} groups")
    except:
        log("No config, all groups OK")

    conn = init_db()
    set_meta(conn, "last_startup", now_text())

    delay = RECONNECT_DELAY_INITIAL
    disc_time = None

    while True:
        try:
            headers = {"Authorization": f"Bearer {NAPCAT_TOKEN}"} if NAPCAT_TOKEN else None
            log(f"Connecting {NAPCAT_WS}...")
            async with websockets.connect(NAPCAT_WS, extra_headers=headers, ping_interval=30, ping_timeout=10, max_size=50*1024*1024) as ws:
                log("Connected!")
                delay = RECONNECT_DELAY_INITIAL
                if disc_time:
                    log(f"Recovered after {int(time.time()-disc_time)}s")
                    disc_time = None
                set_meta(conn, "last_connect", now_text())

                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except:
                        continue

                    if event.get("post_type") == "message" and event.get("message_type") == "group":
                        gid = event.get("group_id", 0)
                        target_ids = [g.get("group_id") for g in config.get("groups", [])]
                        if target_ids and int(gid) not in target_ids:
                            continue

                        mid = event.get("message_id", 0)
                        if msg_exists(conn, mid):
                            continue

                        log(f"msg {mid}")
                        await process_message(ws, event, conn)
                        set_meta(conn, "last_event", now_text())

        except ConnectionClosed as e:
            log(f"Closed: {e}")
        except OSError as e:
            log(f"OS err: {e}")
        except Exception as e:
            log(f"Err: {e}")

        if not disc_time:
            disc_time = time.time()
            set_meta(conn, "last_disconnect", now_text())
        log(f"Reconnect in {delay}s...")
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_DELAY_MAX)

if __name__ == "__main__":
    log("QQ-Feishu Bridge v3 starting")
    asyncio.run(main_loop())
