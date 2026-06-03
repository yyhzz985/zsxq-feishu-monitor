#!/usr/bin/env python3
"""QQ->Feishu Bridge v3.2 — multi-group per-group routing"""
import asyncio, json, os, re, sqlite3, ssl, sys, time, traceback, uuid, mimetypes, urllib.request, urllib.error
from services.media_processing_service import process_downloaded_image

# OSS integration (disabled by default)
try:
    import oss_helpers as _oss
    _oss.OSS_ENABLED = os.environ.get("OSS_ENABLED", "0") == "1"
    _oss.OSS_BUCKET = os.environ.get("OSS_BUCKET", "")
    _oss.OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
    _oss.OSS_ACCESS_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID", "")
    _oss.OSS_ACCESS_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET", "")
except ImportError:
    _oss = None

def oss_archive_media(filepath, media_type, gid=0):
    """Upload media file to OSS archive if enabled."""
    if not _oss or not _oss.OSS_ENABLED:
        return
    try:
        ts = time.strftime("%Y%m%d/%H%M%S")
        ext = os.path.splitext(filepath)[1] or ".bin"
        oss_key = f"qq-bridge/{gid}/{media_type}/{ts}_{os.urandom(4).hex()}{ext}"
        _oss.oss_upload(filepath, oss_key)
    except Exception as e:
        log("  OSS upload err: " + str(e)[:80])
try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError:
    websockets = None

def env(k, d=""): return os.environ.get(k, d)
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
UA = "Mozilla/5.0"
os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
ctx_ssl = ssl.create_default_context()

_config = {}
def load_config():
    global _config
    if not _config:
        try:
            with open(os.environ.get("QQ_BRIDGE_CONFIG", "/app/config.json")) as f:
                _config.update(json.load(f))
        except: pass
    return _config

def get_group_config(gid):
    for g in load_config().get("groups", []):
        if int(g.get("group_id", 0)) == int(gid): return g
    return None

def get_chat_ids(gid, key="content_chat_ids"):
    gc = get_group_config(gid)
    if gc and gc.get(key): return gc[key]
    cfg = load_config().get("feishu", {})
    cids = cfg.get(key, [])
    if cids: return cids
    single = cfg.get(key.replace("_ids", "_id")) or FEISHU_CHAT_ID
    return [single] if single else []

def get_alert_chat_id(gid):
    gc = get_group_config(gid)
    if gc and gc.get("alert_chat_id"): return gc["alert_chat_id"]
    return load_config().get("feishu", {}).get("alert_chat_id") or FEISHU_CHAT_ID

def log(msg):
    print("%s [QQ] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)

def should_skip_text(text):
    """Filter QQ system messages and noise. Keep real user content."""
    if not text or not text.strip():
        return True
    t = text.strip()
    # Skip: 群聊合集 / 群公告 / 系统消息
    if re.search(r'群聊.{0,2}合集|群公告|系统消息|【.+】已开启全员禁言|【.+】已关闭全员禁言', t):
        return True
    # Skip: pure group numbers or "群号:xxxxx"
    if re.match(r'^\d{5,15}$', t):
        return True
    if re.match(r'^群号[：:]\s*\d+', t):
        return True
    # Skip: pure timestamps like "2026-06-02 16:30:00"
    if re.match(r'^\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}(:\d{2})?$', t):
        return True
    # Skip: very short messages that are just numbers/symbols
    if len(t) <= 2 and not any(c.isalpha() for c in t):
        return True
    # Do NOT skip: 百度网盘 links (keep for forwarding)
    if 'pan.baidu.com' in t or '百度' in t:
        return False
    return False

# ---- SQLite ----
def init_db():
    conn = sqlite3.connect(DB_FILE); conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS qq_messages(message_id TEXT PRIMARY KEY, group_id INTEGER, sender_name TEXT, msg_type TEXT, content TEXT, status TEXT DEFAULT 'received', feishu_msg_id TEXT, retry_count INTEGER DEFAULT 0, last_error TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit(); return conn

def now_text(): return time.strftime("%Y-%m-%d %H:%M:%S")
def msg_exists(conn, mid): return conn.execute("SELECT 1 FROM qq_messages WHERE message_id=?", (str(mid),)).fetchone() is not None
def get_max_retries():
    return int(load_config().get("retry", {}).get("max_retries", 3))
def should_skip_message(conn, mid):
    row = conn.execute("SELECT status,retry_count FROM qq_messages WHERE message_id=?", (str(mid),)).fetchone()
    if not row:
        return False
    if row["status"] == "sent":
        return True
    if row["status"] == "failed" and int(row["retry_count"] or 0) >= get_max_retries():
        return True
    return False
def insert_msg(conn, mid, gid, sname="", mtype="", content=""):
    ts = now_text()
    conn.execute("INSERT OR IGNORE INTO qq_messages(message_id,group_id,sender_name,msg_type,content,status,created_at,updated_at) VALUES(?,?,?,?,?,'received',?,?)", (str(mid), int(gid), sname, mtype, content or "", ts, ts))
    conn.commit()
def update_msg_content(conn, mid, mtype, content):
    conn.execute("UPDATE qq_messages SET msg_type=?,content=?,updated_at=? WHERE message_id=?", (mtype, content or "", now_text(), str(mid)))
    conn.commit()
def mark_sent(conn, mid, fmsgid=""):
    conn.execute("UPDATE qq_messages SET status='sent',feishu_msg_id=?,updated_at=? WHERE message_id=?", (fmsgid, now_text(), str(mid))); conn.commit()
def mark_failed(conn, mid, err):
    conn.execute("UPDATE qq_messages SET status='failed',retry_count=retry_count+1,last_error=?,updated_at=? WHERE message_id=?", (str(err)[:2000], now_text(), str(mid))); conn.commit()

# ---- Feishu API ----
_token_cache = {"t": "", "e": 0}
def get_fs_token():
    now = time.time()
    if _token_cache["t"] and now < _token_cache["e"] - 300: return _token_cache["t"]
    b = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request("%s/auth/v3/tenant_access_token/internal" % FEISHU_API_BASE, data=b, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=ctx_ssl) as r:
        d = json.loads(r.read()); _token_cache["t"] = d["tenant_access_token"]; _token_cache["e"] = now + d.get("expire", 7200)
        return _token_cache["t"]

def fs_api(url, payload, timeout=30):
    token = get_fs_token()
    b = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=b, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx_ssl) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: return json.loads(e.read())

def fs_send_text(text, gid=0):
    if not text or not text.strip(): return True
    ok_all = True
    for cid in get_chat_ids(gid):
        uid = str(uuid.uuid4())
        r = fs_api("%s/im/v1/messages?receive_id_type=chat_id" % FEISHU_API_BASE,
                   {"receive_id": cid, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False), "uuid": uid})
        if r.get("code") != 0:
            log("  text FAIL %s: %s" % (cid, r.get("code"))); ok_all = False
    return ok_all

def fs_send_text_to_chat(text, chat_id):
    if not text or not text.strip(): return True
    uid = str(uuid.uuid4())
    r = fs_api("%s/im/v1/messages?receive_id_type=chat_id" % FEISHU_API_BASE,
               {"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False), "uuid": uid})
    return r.get("code") == 0

def send_alert(text, gid=0):
    cid = get_alert_chat_id(gid)
    log("ALERT: " + text[:100])
    return fs_send_text_to_chat("[QQ Bridge] " + text, cid)

def build_multipart(filepath, media_type):
    with open(filepath, "rb") as f: data = f.read()
    ct = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    boundary = "----QQ" + os.urandom(8).hex()
    fname = os.path.basename(filepath)
    CRLF = "\r\n"
    parts = [("--" + boundary + CRLF).encode()]
    if media_type == "image":
        parts.append(b'Content-Disposition: form-data; name="image_type"' + CRLF.encode() + CRLF.encode() + b'message' + CRLF.encode())
    else:
        parts.append(b'Content-Disposition: form-data; name="file_type"' + CRLF.encode() + CRLF.encode() + b'stream' + CRLF.encode())
        parts.append(("--" + boundary + CRLF).encode())
        parts.append(('Content-Disposition: form-data; name="file_name"' + CRLF + CRLF + fname + CRLF).encode())
    field = "image" if media_type == "image" else "file"
    parts.append(("--" + boundary + CRLF).encode())
    parts.append(('Content-Disposition: form-data; name="' + field + '"; filename="' + fname + '"' + CRLF + 'Content-Type: ' + ct + CRLF + CRLF).encode())
    parts.append(data)
    parts.append((CRLF + "--" + boundary + "--" + CRLF).encode())
    body = b"".join(parts)
    return body, boundary

def fs_upload_media(filepath, media_type="image"):
    token = get_fs_token()
    body, boundary = build_multipart(filepath, media_type)
    url = ("%s/im/v1/images" if media_type == "image" else "%s/im/v1/files") % FEISHU_API_BASE
    req = urllib.request.Request(url, data=body, headers={"Authorization": "Bearer " + token, "Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx_ssl) as r:
            d = json.loads(r.read())
            if d.get("code") == 0:
                key = d.get("data", {}).get("image_key") or d.get("data", {}).get("file_key", "")
                return True, key
            return False, str(d)
    except Exception as e: return False, str(e)

def fs_send_media(filepath, media_type="image", gid=0):
    ok, key = fs_upload_media(filepath, media_type)
    if not ok: log("  upload FAIL: " + str(key)[:80]); return False
    ok_all = True
    for cid in get_chat_ids(gid):
        uid = str(uuid.uuid4())
        content = {"image_key": key} if media_type == "image" else {"file_key": key, "file_name": os.path.basename(filepath)}
        r = fs_api("%s/im/v1/messages?receive_id_type=chat_id" % FEISHU_API_BASE,
                   {"receive_id": cid, "msg_type": media_type, "content": json.dumps(content, ensure_ascii=False), "uuid": uid})
        if r.get("code") != 0: log("  send FAIL %s: %s" % (cid, r.get("code"))); ok_all = False
    return ok_all

def download_qq_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://qq.com/"})
        with urllib.request.urlopen(req, timeout=30, context=ctx_ssl) as r: return r.read()
    except Exception as e: log("  download err: " + str(e)); return None

def get_media_processing_config(gid):
    gc = get_group_config(gid)
    if gc and "media_processing" in gc:
        return gc.get("media_processing", {})
    return load_config().get("media_processing", {})

def prepare_image_for_upload(filepath, gid, mid, conn):
    try:
        return process_downloaded_image(filepath, TEMP_DIR, get_media_processing_config(gid))
    except Exception as e:
        err = "watermark clean failed: " + str(e)
        insert_msg(conn, mid, gid, "", "image", "[img]")
        mark_failed(conn, mid, err)
        send_alert("image watermark clean failed; forwarding stopped. message_id=%s error=%s" % (mid, str(e)[:200]), gid)
        return None

def safe_remove(path):
    try:
        os.remove(path)
    except:
        pass

def mark_delivery_failed(conn, mid, gid, mtype, content, err):
    insert_msg(conn, mid, gid, "", mtype, content)
    update_msg_content(conn, mid, mtype, content)
    mark_failed(conn, mid, err)
    send_alert("message forwarding failed; message_id=%s error=%s" % (mid, str(err)[:200]), gid)

async def process_message(ws, event, conn):
    gid = event.get("group_id", 0); mid = event.get("message_id", 0); segs = event.get("message", [])
    if isinstance(segs, str):
        if not segs.strip(): return
        if should_skip_text(segs): log("  SKIP text: " + str(mid)); return
        insert_msg(conn, mid, gid, "", "text", segs)
        if fs_send_text(segs, gid):
            mark_sent(conn, mid)
        else:
            mark_delivery_failed(conn, mid, gid, "text", segs, "text send failed")
        return

    text_buf = []; all_content = []
    def content_summary():
        return "".join(all_content)[:500]
    def flush_text():
        nonlocal text_buf
        txt = "".join(text_buf).strip()
        text_buf = []
        if not txt:
            return True
        if fs_send_text(txt, gid):
            return True
        mark_delivery_failed(conn, mid, gid, "mixed", content_summary() or txt, "text send failed")
        return False

    for seg in segs:
        t = seg.get("type", ""); d = seg.get("data", {})
        if t in ("forward", "json"): log("  SKIP %s: %s" % (t, mid)); return
        if t == "xml" and "http" in str(d.get("data", "")).lower(): log("  SKIP xml: " + str(mid)); return
        if t == "text":
            txt = d.get("text", "")
            if should_skip_text(txt): log("  SKIP text: " + str(mid)); return
            text_buf.append(txt); all_content.append(txt)
        elif t == "image":
            all_content.append("[img]")
            if not flush_text(): return
            url = d.get("url", "")
            if not url:
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "image url missing")
                return
            data = download_qq_url(url)
            if not data:
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "image download failed")
                return
            ext = ".png" if "png" in url.lower() else ".jpg"
            fpath = os.path.join(TEMP_DIR, "qqimg_" + os.urandom(4).hex() + ext)
            with open(fpath, "wb") as f: f.write(data)
            upload_path = prepare_image_for_upload(fpath, gid, mid, conn)
            if not upload_path:
                safe_remove(fpath)
                return
            ok = fs_send_media(upload_path, "image", gid)
            if ok:
                oss_archive_media(upload_path, "image", gid)
            if upload_path != fpath:
                safe_remove(upload_path)
            safe_remove(fpath)
            if not ok:
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "image send failed")
                return
        elif t == "file":
            fname = os.path.basename(d.get("file", "file")) or "file"
            all_content.append("[file:" + fname + "]")
            if not flush_text(): return
            url = d.get("url", "")
            if not url.startswith("http"):
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "file url missing")
                return
            data = download_qq_url(url)
            if not data:
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "file download failed")
                return
            fpath = os.path.join(TEMP_DIR, fname)
            with open(fpath, "wb") as f: f.write(data)
            ok = fs_send_media(fpath, "file", gid)
            if ok:
                oss_archive_media(fpath, "file", gid)
            safe_remove(fpath)
            if not ok:
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "file send failed")
                return
        elif t == "record":
            all_content.append("[voice]")
            if not flush_text(): return
            url = d.get("url", "")
            if not url.startswith("http"):
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "voice url missing")
                return
            data = download_qq_url(url)
            if not data:
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "voice download failed")
                return
            fpath = os.path.join(TEMP_DIR, "qqvoice_" + os.urandom(4).hex() + ".amr")
            with open(fpath, "wb") as f: f.write(data)
            ok = fs_send_media(fpath, "file", gid)
            if ok:
                oss_archive_media(fpath, "file", gid)
            safe_remove(fpath)
            if not ok:
                mark_delivery_failed(conn, mid, gid, "mixed", content_summary(), "voice send failed")
                return
        elif t == "at":
            name = d.get("name", "") or ("@" + str(d.get("qq", "")))
            text_buf.append(name + " "); all_content.append(name + " ")
        elif t == "face": text_buf.append("[emoji]"); all_content.append("[emoji]")

    if not flush_text(): return
    content_str = content_summary()
    if content_str:
        insert_msg(conn, mid, gid, "", "mixed", content_str)
        update_msg_content(conn, mid, "mixed", content_str)
        mark_sent(conn, mid)

async def main_loop():
    if not FEISHU_APP_ID: log("ERROR: No Feishu config"); return
    try:
        with open(os.environ.get("QQ_BRIDGE_CONFIG", "/app/config.json")) as f: config = json.load(f)
        log("Config: %d groups" % len(config.get("groups",[])))
        for g in config.get("groups", []): log("  QQ %s -> %s" % (g["group_id"], g.get("content_chat_ids",[])))
    except Exception as e: log("Config error: " + str(e))

    conn = init_db(); delay = RECONNECT_DELAY_INITIAL
    while True:
        try:
            headers = {"Authorization": "Bearer " + NAPCAT_TOKEN} if NAPCAT_TOKEN else None
            log("Connecting %s..." % NAPCAT_WS)
            async with websockets.connect(NAPCAT_WS, extra_headers=headers, ping_interval=30, ping_timeout=10, max_size=50*1024*1024) as ws:
                log("Connected!"); delay = RECONNECT_DELAY_INITIAL
                async for raw in ws:
                    try: event = json.loads(raw)
                    except: continue
                    if event.get("post_type") == "message" and event.get("message_type") == "group":
                        gid = event.get("group_id", 0)
                        target_ids = [g.get("group_id") for g in config.get("groups", [])]
                        if target_ids and int(gid) not in target_ids: continue
                        mid = event.get("message_id", 0)
                        if should_skip_message(conn, mid): continue
                        log("msg %s gid=%s" % (mid, gid))
                        await process_message(ws, event, conn)
        except ConnectionClosed as e: log("Closed: " + str(e))
        except Exception as e: log("Err: " + str(e)); traceback.print_exc()
        log("Reconnect in %ds..." % delay); await asyncio.sleep(delay); delay = min(delay * 2, RECONNECT_DELAY_MAX)

if __name__ == "__main__":
    log("QQ-Feishu Bridge v3.2 starting")
    asyncio.run(main_loop())
