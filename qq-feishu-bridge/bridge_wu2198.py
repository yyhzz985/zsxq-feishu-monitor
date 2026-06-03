#!/usr/bin/env python3
"""
QQ -> Feishu Note Image Bridge for wu2198
=========================================
Connects to NapCat WebSocket, fetches messages from a QQ group,
renders them as note images (便签图) with watermark, and sends to Feishu.

Requirements: Python 3.6+, websockets, Pillow, Chinese fonts (wqy-microhei)
Usage: Set env vars or edit config inline, then run.
"""

import asyncio, json, os, ssl, time, uuid, urllib.request, urllib.error, io
from PIL import Image, ImageDraw, ImageFont

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

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    exit(1)

# === CONFIG (edit these or set via env) ===
NAPCAT_WS = os.environ.get("NAPCAT_WS", "ws://localhost:3001")
NAPCAT_TOKEN = os.environ.get("NAPCAT_TOKEN", "")  # from onebot11 config
GROUP_ID = int(os.environ.get("GROUP_ID", "475733753"))
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "cli_a924dc876a799bc8")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_CONTENT_CHATS = os.environ.get("FEISHU_CONTENT_CHATS", "").split(",")  # comma-separated chat_ids
FEISHU_ALERT_CHAT = os.environ.get("FEISHU_ALERT_CHAT", "")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
NOTES_API = "https://notes.fangyuanxiaozhan.com/api"
WATERMARK_TEXT = os.environ.get("WATERMARK_TEXT", "更新加V：237219265")
FOOTER_BRAND = os.environ.get("FOOTER_BRAND", "wu2198")
TEMP_DIR = os.environ.get("TEMP_DIR", "/tmp/qq_note_bridge")
ARCHIVE_DIR = os.environ.get("ARCHIVE_DIR", "/opt/qq-note-bridge/archive")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
RECONNECT_DELAY_INITIAL = 10
RECONNECT_DELAY_MAX = 300

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)
ctx_ssl = ssl.create_default_context()

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [NOTE] {msg}", flush=True)

# ---- Feishu API ----
_token_cache = {"t": "", "e": 0}

def get_fs_token():
    now = time.time()
    if _token_cache["t"] and now < _token_cache["e"] - 300:
        return _token_cache["t"]
    b = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
    req = urllib.request.Request(f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
                                 data=b, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=ctx_ssl) as r:
        d = json.loads(r.read())
        _token_cache["t"] = d["tenant_access_token"]
        _token_cache["e"] = now + d.get("expire", 7200)
        return _token_cache["t"]

def fs_api(url, payload, timeout=30):
    token = get_fs_token()
    b = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=b,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx_ssl) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())

def fs_send_image_to_chats(filepath):
    """Upload image once, send to all configured content chats."""
    token = get_fs_token()
    with open(filepath, "rb") as f:
        data = f.read()

    boundary = "----NOTE" + os.urandom(8).hex()
    fname = os.path.basename(filepath)
    # Build multipart without f-strings (Python 3.6 compatible)
    CRLF = "\r\n"
    parts = [
        ("--" + boundary + CRLF).encode(),
        b'Content-Disposition: form-data; name="image_type"' + CRLF.encode() + CRLF.encode() + b'message' + CRLF.encode(),
        ("--" + boundary + CRLF).encode(),
        ('Content-Disposition: form-data; name="image"; filename="' + fname + '"' + CRLF + 'Content-Type: image/png' + CRLF + CRLF).encode(),
        data,
        (CRLF + "--" + boundary + "--" + CRLF).encode(),
    ]
    body = b"".join(parts)

    req = urllib.request.Request(f"{FEISHU_API_BASE}/im/v1/images", data=body,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60, context=ctx_ssl) as r:
        d = json.loads(r.read())
        if d.get("code") != 0:
            log(f"  upload FAIL: {d}")
            return False
        key = d["data"]["image_key"]

    ok_all = True
    for cid in FEISHU_CONTENT_CHATS:
        if not cid.strip():
            continue
        uid = str(uuid.uuid4())
        r = fs_api(f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
                   {"receive_id": cid.strip(), "msg_type": "image",
                    "content": json.dumps({"image_key": key}, ensure_ascii=False), "uuid": uid})
        if r.get("code") != 0:
            log(f"  send FAIL to {cid}: {r.get('code')}")
            ok_all = False
    return ok_all

def fs_send_file_to_chats(filepath):
    """Upload file once, send to all content chats."""
    token = get_fs_token()
    with open(filepath, "rb") as f:
        data = f.read()

    boundary = "----NOTE" + os.urandom(8).hex()
    fname = os.path.basename(filepath)
    CRLF = "\r\n"
    parts = [
        ("--" + boundary + CRLF).encode(),
        b'Content-Disposition: form-data; name="file_type"' + CRLF.encode() + CRLF.encode() + b'stream' + CRLF.encode(),
        ("--" + boundary + CRLF).encode(),
        ('Content-Disposition: form-data; name="file_name"' + CRLF + CRLF + fname + CRLF).encode(),
        ("--" + boundary + CRLF).encode(),
        ('Content-Disposition: form-data; name="file"; filename="' + fname + '"' + CRLF + 'Content-Type: application/octet-stream' + CRLF + CRLF).encode(),
        data,
        (CRLF + "--" + boundary + "--" + CRLF).encode(),
    ]
    body = b"".join(parts)

    req = urllib.request.Request(f"{FEISHU_API_BASE}/im/v1/files", data=body,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60, context=ctx_ssl) as r:
        d = json.loads(r.read())
        if d.get("code") != 0:
            log(f"  file upload FAIL: {d}")
            return False
        key = d["data"]["file_key"]

    ok_all = True
    for cid in FEISHU_CONTENT_CHATS:
        if not cid.strip():
            continue
        uid = str(uuid.uuid4())
        r = fs_api(f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
                   {"receive_id": cid.strip(), "msg_type": "file",
                    "content": json.dumps({"file_key": key, "file_name": fname}, ensure_ascii=False), "uuid": uid})
        if r.get("code") != 0:
            log(f"  file send FAIL to {cid}: {r.get('code')}")
            ok_all = False
    return ok_all

def send_alert(text):
    if not FEISHU_ALERT_CHAT:
        return
    uid = str(uuid.uuid4())
    fs_api(f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
           {"receive_id": FEISHU_ALERT_CHAT, "msg_type": "text",
            "content": json.dumps({"text": "[NOTE Bridge] " + text}, ensure_ascii=False), "uuid": uid})

# ---- Note Image Rendering ----
def notes_export(markdown):
    data = json.dumps({"markdown": markdown, "theme": "default",
                       "footerBrand": FOOTER_BRAND, "footerVia": ""}).encode()
    req = urllib.request.Request(f"{NOTES_API}/export", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60, context=ctx_ssl) as r:
        return r.read()

def notes_import_image(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    boundary = "----Notes" + os.urandom(8).hex()
    fname = os.path.basename(filepath)
    CRLF = "\r\n"
    parts = [
        ("--" + boundary + CRLF).encode(),
        ('Content-Disposition: form-data; name="image"; filename="' + fname + '"' + CRLF + 'Content-Type: image/png' + CRLF + CRLF).encode(),
        data,
        (CRLF + "--" + boundary + "--" + CRLF).encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(f"{NOTES_API}/images/import", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30, context=ctx_ssl) as r:
        return json.loads(r.read()).get("url", "")

def add_watermark(image_bytes):
    """Add full-screen repeating diagonal watermark.
    IMPORTANT: Single text per tile → rotate → tile with spacing.
    Do NOT create multi-line tiles (causes watermark duplication)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    W, H = img.size
    font_size = max(22, W // 35)

    # Font fallback chain
    font = None
    for font_name in ["simhei.ttf", "msyh.ttf",
                      "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc"]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = WATERMARK_TEXT
    tmp = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad = 20
    tile = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((pad, pad), text, font=font, fill=(128, 128, 128, 102))
    tile = tile.rotate(15, expand=True, resample=Image.BICUBIC)

    rt_w, rt_h = tile.size
    spacing_x = rt_w + min(300, max(40, W // 10))
    spacing_y = rt_h + min(300, max(30, H // 8))
    for y in range(-rt_h, H + rt_h, spacing_y):
        for x in range(-rt_w, W + rt_w, spacing_x):
            img.paste(tile, (x, y), tile)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def render_note_image(ctime_str, text_parts, image_urls):
    """Render markdown → notes API → watermark → save to archive."""
    md_parts = [ctime_str]
    if text_parts:
        md_parts.append(text_parts)
    for url in image_urls:
        md_parts.append(f"![image]({url})")
    md = "\n\n".join(md_parts)

    png = notes_export(md)
    if not png:
        log("  notes export FAILED")
        return None

    png = add_watermark(png)

    # Save to archive
    date_str = ctime_str[:10].replace("-", "")
    archive_dir = os.path.join(ARCHIVE_DIR, date_str)
    os.makedirs(archive_dir, exist_ok=True)
    time_str = ctime_str[11:16].replace(":", "")
    save_path = os.path.join(archive_dir, f"{date_str}{time_str}.png")
    counter = 1
    while os.path.exists(save_path):
        save_path = os.path.join(archive_dir, f"{date_str}{time_str}_{counter}.png")
        counter += 1

    with open(save_path, "wb") as f:
        f.write(png)

    # Upload to OSS
    if _oss and _oss.OSS_ENABLED and _oss.OSS_BUCKET:
        oss_key = f"wu2198/{date_str}/{os.path.basename(save_path)}"
        if _oss.oss_upload(save_path, oss_key):
            log(f"  OSS uploaded: {oss_key}")

    return save_path

# ---- QQ Download ----
def download_qq_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://qq.com/"})
        with urllib.request.urlopen(req, timeout=30, context=ctx_ssl) as r:
            return r.read()
    except Exception as e:
        log(f"  download err: {e}")
        return None

# ---- Message Processing ----
async def process_message(event):
    segs = event.get("message", [])
    ctime = time.strftime("%Y-%m-%d %H:%M:%S",
                          time.localtime(event.get("time", time.time())))

    if isinstance(segs, str):
        if not segs.strip() or "http://" in segs or "https://" in segs:
            return
        save_path = render_note_image(ctime, segs, [])
        if save_path:
            fs_send_image_to_chats(save_path)
        return

    text_parts = []
    image_urls = []
    files_to_send = []

    for seg in segs:
        t = seg.get("type", "")
        d = seg.get("data", {})

        if t in ("forward", "json"):
            return
        if t == "xml" and "http" in str(d.get("data", "")).lower():
            return

        if t == "text":
            txt = d.get("text", "")
            if "http://" in txt or "https://" in txt:
                return
            text_parts.append(txt)
        elif t == "image":
            url = d.get("url", "")
            if url:
                data = download_qq_url(url)
                if data:
                    fpath = os.path.join(TEMP_DIR, f"note_img_{os.urandom(4).hex()}.png")
                    with open(fpath, "wb") as f:
                        f.write(data)
                    public_url = notes_import_image(fpath)
                    if public_url:
                        image_urls.append(public_url)
                    try:
                        os.remove(fpath)
                    except:
                        pass
        elif t == "file" or t == "record":
            url = d.get("url", "")
            fname = d.get("file", "file")
            if url and url.startswith("http"):
                data = download_qq_url(url)
                if data:
                    fpath = os.path.join(TEMP_DIR, fname)
                    with open(fpath, "wb") as f:
                        f.write(data)
                    files_to_send.append(fpath)
        elif t == "at":
            name = d.get("name", "") or ("@" + str(d.get("qq", "")))
            text_parts.append("@" + name + " ")
        elif t == "face":
            text_parts.append("[emoji]")

    # Render note image with text + images
    text = "\n".join(text_parts).strip() if text_parts else ""
    if text or image_urls:
        save_path = render_note_image(ctime, text, image_urls)
        if save_path:
            fs_send_image_to_chats(save_path)

    # Send files separately
    for fpath in files_to_send:
        fs_send_file_to_chats(fpath)
        try:
            os.remove(fpath)
        except:
            pass

# ---- Main Loop ----
async def main_loop():
    if not FEISHU_APP_SECRET or not FEISHU_CONTENT_CHATS:
        log("ERROR: Missing Feishu config")
        return

    log(f"Target QQ group: {GROUP_ID}")
    log(f"Content chats: {FEISHU_CONTENT_CHATS}")
    log(f"Alert chat: {FEISHU_ALERT_CHAT or '(none)'}")
    log(f"Footer: {FOOTER_BRAND}")

    delay = RECONNECT_DELAY_INITIAL

    while True:
        try:
            headers = {"Authorization": f"Bearer {NAPCAT_TOKEN}"} if NAPCAT_TOKEN else None
            log(f"Connecting {NAPCAT_WS}...")
            async with websockets.connect(NAPCAT_WS, extra_headers=headers,
                                          ping_interval=30, ping_timeout=10,
                                          max_size=50*1024*1024) as ws:
                log("Connected!")
                delay = RECONNECT_DELAY_INITIAL

                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except:
                        continue

                    if event.get("post_type") == "message" and event.get("message_type") == "group":
                        gid = event.get("group_id", 0)
                        if int(gid) != int(GROUP_ID):
                            continue
                        log(f"msg {event.get('message_id', 0)}")
                        await process_message(event)

        except Exception as e:
            log(f"Err: {e}")

        log(f"Reconnect in {delay}s...")
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_DELAY_MAX)

if __name__ == "__main__":
    log("Note Image Bridge starting")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main_loop())
