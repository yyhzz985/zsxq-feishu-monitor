#!/usr/bin/env python3
# OSS integration (disabled by default; set ZSXQ_OSS_ENABLED=1 in .env)
try:
    from . import oss_helpers as _oss
    _oss.OSS_ENABLED = is_truthy(os.environ.get("ZSXQ_OSS_ENABLED", "0"))
    _oss.OSS_BUCKET = os.environ.get("ZSXQ_OSS_BUCKET", "")
    _oss.OSS_ENDPOINT = os.environ.get("ZSXQ_OSS_ENDPOINT", "oss-cn-hangzhou-internal.aliyuncs.com")
    _oss.OSS_ACCESS_KEY_ID = os.environ.get("ZSXQ_OSS_ACCESS_KEY_ID", "")
    _oss.OSS_ACCESS_KEY_SECRET = os.environ.get("ZSXQ_OSS_ACCESS_KEY_SECRET", "")
except ImportError:
    _oss = None
"""ZSXQ -> note image -> watermark -> Feishu, with SQLite status tracking."""
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get(
    "ZSXQ_CONFIG_FILE", os.path.join(SCRIPTS_DIR, "zsxq_poller_config.json")
)
DB_FILE = os.environ.get("ZSXQ_DB_FILE", os.path.join(SCRIPTS_DIR, "zsxq_monitor.db"))
LOCK_FILE = os.environ.get(
    "ZSXQ_LOCK_FILE", os.path.join(SCRIPTS_DIR, "zsxq_poll_击球区.lock")
)
ENV_FILE = os.environ.get("ZSXQ_ENV_FILE", os.path.join(HERMES_HOME, ".env"))
LOG_DIR = os.environ.get("ZSXQ_LOG_DIR", os.path.join(HERMES_HOME, "logs"))
LOG_FILE = os.environ.get("ZSXQ_LOG_FILE", os.path.join(LOG_DIR, "zsxq_poll.log"))
TEMP_DIR = os.environ.get("ZSXQ_TEMP_DIR", os.path.join(HERMES_HOME, "temp", "zsxq_media"))

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

CTX = ssl.create_default_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

NOTES_API = "https://notes.fangyuanxiaozhan.com/api"
NOTES_EXPORT = f"{NOTES_API}/export"
NOTES_IMPORT = f"{NOTES_API}/images/import"
WATERMARK_TEXT = os.environ.get("WATERMARK_TEXT", "更新加V：237219265")
SAVE_BASE = os.environ.get("ZSXQ_SAVE_DIR", r"D:\财经课程更新\击球区小能手")
DEFAULT_LARK_CLI = (
    r"C:\Users\1\AppData\Roaming\npm\lark-cli.cmd" if os.name == "nt" else "lark-cli"
)
LARK_CLI = os.environ.get("LARK_CLI_PATH", DEFAULT_LARK_CLI)
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "oc_7759cabf77c5502c6a1910aff10b3229")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

MAX_PAGES = 50
IMAGE_API_INTERVAL = 1.0
LOCK_STALE_SECONDS = 6 * 60 * 60
PENDING_TOPIC_LIMIT = 100
PENDING_FILE_LIMIT = 100
ALERT_FAILURE_THRESHOLD = 3
DISK_ALERT_FREE_BYTES = int(os.environ.get("ZSXQ_DISK_ALERT_GB", "10")) * 1024 * 1024 * 1024
FEISHU_FILE_MAX_SIZE = int(os.environ.get("ZSXQ_FEISHU_FILE_MAX_MB", "30")) * 1024 * 1024
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".ogg", ".flac", ".aac", ".wma", ".opus", ".amr", ".m4b"}
FILE_MAX_RETRIES = int(os.environ.get("ZSXQ_FILE_MAX_RETRIES", "5"))


def now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def safe_console_text(text, encoding=None):
    encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")


def log_msg(message):
    line = f"{now_text()} {message}"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(safe_console_text(message))


def load_env():
    env = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def load_config():
    return load_config_file(CONFIG_FILE)


def load_config_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def is_dry_run():
    return is_truthy(os.environ.get("ZSXQ_MONITOR_DRY_RUN", "0"))


def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Cache-Control", "no-cache, no-store, must-revalidate")
    req.add_header("Pragma", "no-cache")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        return resp.read()


def http_post(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, method="POST")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            return resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        return exc.read(), exc.code


def runtime_value(key, default=""):
    return os.environ.get(key) or load_env().get(key) or default


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text) if text else ""


def topic_id_value(value):
    text = str(value)
    return int(text) if text.isdigit() else text


def topic_id_key(value):
    return str(value)


def row_to_dict(row):
    data = dict(row)
    if "topic_id" in data:
        data["topic_id"] = topic_id_value(data["topic_id"])
    return data


def init_db(db_path=DB_FILE):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        create table if not exists topics (
            topic_id text primary key,
            create_time text not null,
            topic_json text not null,
            status text not null default 'discovered',
            archive_path text,
            feishu_message_id text,
            retry_count integer not null default 0,
            last_error text,
            created_at text not null,
            updated_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists files (
            file_key text primary key,
            topic_id text not null,
            create_time text not null,
            file_id text not null,
            name text not null,
            archive_path text,
            status text not null default 'discovered',
            retry_count integer not null default 0,
            last_error text,
            created_at text not null,
            updated_at text not null
        )
        """
    )
    conn.execute(
        """
        create table if not exists meta (
            key text primary key,
            value text not null,
            updated_at text not null
        )
        """
    )
    conn.commit()
    return conn


def set_meta(conn, key, value):
    ts = now_text()
    conn.execute(
        """
        insert into meta(key, value, updated_at) values(?, ?, ?)
        on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, str(value), ts),
    )
    conn.commit()


def get_meta(conn, key, default=None):
    row = conn.execute("select value from meta where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def increment_meta_int(conn, key):
    value = int(get_meta(conn, key, "0")) + 1
    set_meta(conn, key, value)
    return value


def upsert_topic(conn, topic):
    tid = topic_id_key(topic["topic_id"])
    create_time = topic.get("create_time", "")
    ts = now_text()
    conn.execute(
        """
        insert into topics(topic_id, create_time, topic_json, status, created_at, updated_at)
        values(?, ?, ?, 'discovered', ?, ?)
        on conflict(topic_id) do update set
            topic_json = excluded.topic_json,
            updated_at = excluded.updated_at
        where topics.status != 'sent'
        """,
        (tid, create_time, json.dumps(topic, ensure_ascii=False), ts, ts),
    )
    conn.commit()


def get_pending_topics(conn, limit=PENDING_TOPIC_LIMIT):
    rows = conn.execute(
        """
        select * from topics
        where status in ('discovered', 'rendered', 'failed')
        order by create_time asc, retry_count asc
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def mark_topic_rendered(conn, topic_id, archive_path):
    ts = now_text()
    conn.execute(
        """
        update topics
        set status = 'rendered', archive_path = ?, last_error = null, updated_at = ?
        where topic_id = ?
        """,
        (archive_path, ts, topic_id_key(topic_id)),
    )
    conn.commit()


def mark_topic_sent(conn, topic_id, archive_path=None, feishu_message_id=None):
    ts = now_text()
    conn.execute(
        """
        update topics
        set status = 'sent',
            archive_path = coalesce(?, archive_path),
            feishu_message_id = ?,
            last_error = null,
            updated_at = ?
        where topic_id = ?
        """,
        (archive_path, feishu_message_id or "", ts, topic_id_key(topic_id)),
    )
    conn.commit()


def mark_topic_failed(conn, topic_id, error):
    ts = now_text()
    conn.execute(
        """
        update topics
        set status = 'failed',
            retry_count = retry_count + 1,
            last_error = ?,
            updated_at = ?
        where topic_id = ?
        """,
        (str(error)[:2000], ts, topic_id_key(topic_id)),
    )
    conn.commit()


def file_key(topic_id, file_id):
    return f"{topic_id_key(topic_id)}:{file_id}"


def upsert_file_records(conn, topic_id, create_time, files):
    ts = now_text()
    for item in files:
        fid = item.get("file_id")
        if not fid:
            continue
        name = item.get("name", "attachment")
        conn.execute(
            """
            insert into files(file_key, topic_id, create_time, file_id, name, created_at, updated_at)
            values(?, ?, ?, ?, ?, ?, ?)
            on conflict(file_key) do update set
                name = excluded.name,
                updated_at = excluded.updated_at
            where files.status != 'sent'
            """,
            (file_key(topic_id, fid), topic_id_key(topic_id), create_time, str(fid), name, ts, ts),
        )
    conn.commit()


def get_pending_files(conn, limit=PENDING_FILE_LIMIT):
    rows = conn.execute(
        """
        select * from files
        where status in ('discovered', 'failed')
        order by create_time asc, retry_count asc
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def count_by_status(conn, table):
    rows = conn.execute(
        f"select status, count(*) as count from {table} group by status order by status"
    ).fetchall()
    return {row["status"]: row["count"] for row in rows}


def get_status_summary(conn):
    return {
        "topics": count_by_status(conn, "topics"),
        "files": count_by_status(conn, "files"),
        "last_heartbeat": get_meta(conn, "last_heartbeat", ""),
        "consecutive_failures": int(get_meta(conn, "consecutive_failures", "0")),
    }


def format_status_summary(summary):
    def fmt_counts(counts):
        if not counts:
            return "none"
        return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))

    return (
        "ZSXQ monitor status\n"
        f"topics: {fmt_counts(summary['topics'])}\n"
        f"files: {fmt_counts(summary['files'])}\n"
        f"last_heartbeat: {summary['last_heartbeat'] or 'never'}\n"
        f"consecutive_failures: {summary['consecutive_failures']}"
    )


def add_check_result(results, name, status, message):
    results.append({"name": name, "status": status, "message": message})


def is_placeholder_value(value):
    text = str(value or "").strip().lower()
    return (
        not text
        or "replace" in text
        or "your_" in text
        or text in ("oc_xxx", "cli_xxx", "xxx")
        or "不要写" in text
    )


def get_check_results(
    cfg=None,
    env=None,
    db_path=DB_FILE,
    lark_cli_path=LARK_CLI,
    save_base=SAVE_BASE,
    log_dir=LOG_DIR,
    temp_dir=TEMP_DIR,
):
    results = []

    if cfg is None:
        try:
            cfg = load_config()
            add_check_result(results, "config_file", "OK", CONFIG_FILE)
        except Exception as exc:
            cfg = {}
            add_check_result(results, "config_file", "FAIL", str(exc))
    if env is None:
        env = load_env()

    if cfg.get("group_id"):
        add_check_result(results, "config.group_id", "OK", "configured")
    else:
        add_check_result(results, "config.group_id", "FAIL", "missing group_id")

    has_env_token = bool(os.environ.get("ZSXQ_ACCESS_TOKEN") or env.get("ZSXQ_ACCESS_TOKEN"))
    has_config_token = bool(cfg.get("access_token"))
    if has_env_token:
        add_check_result(results, "zsxq_token", "OK", "present in environment")
    elif has_config_token:
        add_check_result(results, "zsxq_token", "OK", "present in config legacy field")
    else:
        add_check_result(results, "zsxq_token", "FAIL", "missing ZSXQ_ACCESS_TOKEN")

    if has_config_token:
        add_check_result(
            results,
            "token_storage",
            "WARN",
            "access_token is still in config; move it to .env later",
        )
    else:
        add_check_result(results, "token_storage", "OK", "no legacy token in config")

    send_mode = (os.environ.get("FEISHU_SEND_MODE") or env.get("FEISHU_SEND_MODE") or "cli").strip().lower()
    if send_mode not in ("cli", "openapi"):
        add_check_result(results, "feishu_send_mode", "FAIL", f"invalid mode: {send_mode}")
    else:
        add_check_result(results, "feishu_send_mode", "OK", send_mode)

    if send_mode == "openapi":
        app_id = os.environ.get("FEISHU_APP_ID") or env.get("FEISHU_APP_ID")
        app_secret = os.environ.get("FEISHU_APP_SECRET") or env.get("FEISHU_APP_SECRET")
        chat_id = os.environ.get("FEISHU_CHAT_ID") or env.get("FEISHU_CHAT_ID") or FEISHU_CHAT_ID
        if is_placeholder_value(app_id) or is_placeholder_value(app_secret) or is_placeholder_value(chat_id):
            add_check_result(
                results,
                "feishu_openapi_credentials",
                "FAIL",
                "missing or placeholder FEISHU_APP_ID, FEISHU_APP_SECRET or FEISHU_CHAT_ID",
            )
        elif app_id and app_secret and chat_id:
            add_check_result(results, "feishu_openapi_credentials", "OK", "configured")
        else:
            add_check_result(
                results,
                "feishu_openapi_credentials",
                "FAIL",
                "missing FEISHU_APP_ID, FEISHU_APP_SECRET or FEISHU_CHAT_ID",
            )
    else:
        if os.path.isfile(lark_cli_path):
            add_check_result(results, "lark_cli", "OK", lark_cli_path)
        else:
            add_check_result(results, "lark_cli", "FAIL", f"not found: {lark_cli_path}")

    if os.path.isdir(save_base):
        add_check_result(results, "archive_dir", "OK", save_base)
    else:
        add_check_result(results, "archive_dir", "FAIL", f"not found: {save_base}")

    for name, path in (("log_dir", log_dir), ("temp_dir", temp_dir)):
        if os.path.isdir(path):
            add_check_result(results, name, "OK", path)
        else:
            add_check_result(results, name, "FAIL", f"not found: {path}")

    conn = None
    try:
        conn = init_db(db_path)
        summary = get_status_summary(conn)
        topic_count = sum(summary["topics"].values())
        file_count = sum(summary["files"].values())
        add_check_result(results, "sqlite_db", "OK", f"topics={topic_count}, files={file_count}")
    except Exception as exc:
        add_check_result(results, "sqlite_db", "FAIL", str(exc))
    finally:
        if conn is not None:
            conn.close()

    return results


def format_check_results(results):
    lines = ["ZSXQ monitor check"]
    for item in results:
        lines.append(f"[{item['status']}] {item['name']}: {item['message']}")
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    lines.append(
        f"summary: ok={counts.get('OK', 0)}, warn={counts.get('WARN', 0)}, "
        f"fail={counts.get('FAIL', 0)}"
    )
    return "\n".join(lines)


def format_gb(byte_count):
    return f"{byte_count / (1024 * 1024 * 1024):.1f}GB"


def disk_free_bytes(path):
    return shutil.disk_usage(path).free


def check_disk_space(
    conn,
    path=SAVE_BASE,
    min_free_bytes=DISK_ALERT_FREE_BYTES,
    free_bytes=None,
    today=None,
    alert_func=None,
):
    today = today or time.strftime("%Y-%m-%d")
    alert_func = alert_func or send_alert
    free_bytes = disk_free_bytes(path) if free_bytes is None else free_bytes
    low = free_bytes < min_free_bytes
    if not low:
        return {"low": False, "alerted": False, "free_bytes": free_bytes}

    meta_key = "disk_alert_date"
    if get_meta(conn, meta_key, "") == today:
        return {"low": True, "alerted": False, "free_bytes": free_bytes}

    alert_func(
        f"磁盘空间不足：{path} 剩余 {format_gb(free_bytes)}，"
        f"低于阈值 {format_gb(min_free_bytes)}"
    )
    set_meta(conn, meta_key, today)
    return {"low": True, "alerted": True, "free_bytes": free_bytes}


def count_rows_like_date(conn, table, column, today, extra_where=""):
    sql = f"select count(*) as count from {table} where {column} like ?"
    if extra_where:
        sql += f" and {extra_where}"
    row = conn.execute(sql, (f"{today}%",)).fetchone()
    return row["count"]


def build_health_report(conn, today=None, free_bytes=None, save_base=SAVE_BASE):
    today = today or time.strftime("%Y-%m-%d")
    free_bytes = disk_free_bytes(save_base) if free_bytes is None else free_bytes
    summary = get_status_summary(conn)
    topic_failed = summary["topics"].get("failed", 0)
    file_failed = summary["files"].get("failed", 0)
    latest_sent = conn.execute(
        "select max(updated_at) as ts from topics where status = 'sent'"
    ).fetchone()["ts"]
    today_discovered = count_rows_like_date(conn, "topics", "created_at", today)
    today_sent = count_rows_like_date(
        conn,
        "topics",
        "updated_at",
        today,
        "status = 'sent'",
    )

    return (
        f"[ZSXQ监控健康日报] {today}\n"
        f"今日发现帖子数：{today_discovered}\n"
        f"今日成功发送数：{today_sent}\n"
        f"当前 topic failed：{topic_failed}\n"
        f"当前 file failed：{file_failed}\n"
        f"最近一次成功发送：{latest_sent or '无'}\n"
        f"剩余磁盘空间：{format_gb(free_bytes)}\n"
        f"最近心跳：{summary['last_heartbeat'] or '无'}"
    )


def send_daily_health_report(
    conn,
    today=None,
    free_bytes=None,
    send_func=None,
):
    if not is_truthy(os.environ.get("ZSXQ_HEALTH_REPORT", "1")):
        return {"sent": False, "reason": "disabled"}

    today = today or time.strftime("%Y-%m-%d")
    meta_key = "last_health_report_date"
    if get_meta(conn, meta_key, "") == today:
        return {"sent": False, "reason": "already_sent"}

    send_func = send_func or fs_send_text
    text = build_health_report(conn, today=today, free_bytes=free_bytes)
    result = send_func(text, idempotency_key=f"zsxq-health-{today}")
    if result.get("ok"):
        set_meta(conn, meta_key, today)
        return {"sent": True, "reason": "sent"}

    log_msg(f"HEALTH_REPORT_ERR: {result.get('error', 'unknown error')}")
    return {"sent": False, "reason": result.get("error", "send_failed")}


def print_check():
    results = get_check_results()
    print(format_check_results(results))
    return 1 if any(item["status"] == "FAIL" for item in results) else 0


def print_migrate_token():
    result = migrate_token_to_env()
    print(result["message"])
    return 0 if result["ok"] else 1


def mark_file_sent(conn, key, archive_path):
    ts = now_text()
    conn.execute(
        """
        update files
        set status = 'sent', archive_path = ?, last_error = null, updated_at = ?
        where file_key = ?
        """,
        (archive_path, ts, key),
    )
    conn.commit()


def mark_file_failed(conn, key, error):
    ts = now_text()
    conn.execute(
        """
        update files
        set status = 'failed',
            retry_count = retry_count + 1,
            last_error = ?,
            updated_at = ?
        where file_key = ?
        """,
        (str(error)[:2000], ts, key),
    )
    conn.commit()


class FileLock:
    def __init__(self, path):
        self.path = path
        self.fd = None

    def read_pid(self):
        try:
            with open(self.path, encoding="ascii") as f:
                text = f.read().strip()
            return int(text) if text.isdigit() else None
        except OSError:
            return None

    def pid_is_running(self, pid):
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def remove_lock(self, reason):
        try:
            os.remove(self.path)
            log_msg(f"LOCK_REMOVED: {reason} {self.path}")
        except FileNotFoundError:
            pass

    def acquire(self):
        if os.path.exists(self.path):
            pid = self.read_pid()
            if pid and not self.pid_is_running(pid):
                self.remove_lock(f"dead pid {pid}")
            elif pid == os.getpid():
                self.remove_lock(f"current pid {pid}")
        if os.path.exists(self.path):
            age = time.time() - os.path.getmtime(self.path)
            if age > LOCK_STALE_SECONDS:
                self.remove_lock("stale")
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode("ascii"))
            return True
        except FileExistsError:
            return False

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


# ---- Watermark ----
def add_watermark(image_bytes):
    """Add full-screen repeating diagonal watermark. Memory-optimized: RGB + direct tile paste (no full-size overlay)."""
    import io as _io

    buf_in = _io.BytesIO(image_bytes)
    img = Image.open(buf_in).convert("RGB")
    W, H = img.size

    try:
        font = ImageFont.truetype("simhei.ttf", max(22, W // 35))
    except Exception:
        try:
            font = ImageFont.truetype("msyh.ttf", max(22, W // 35))
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/wqy-microhei/wqy-microhei.ttc", max(22, W // 35))
            except Exception:
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

    buf_out = _io.BytesIO()
    img.save(buf_out, format="PNG")
    return buf_out.getvalue()

# ---- Feishu (lark-cli) ----
import subprocess as _subprocess


def parse_lark_cli_result(returncode, stdout, stderr):
    stdout = stdout or ""
    stderr = stderr or ""
    parsed = None
    error_parts = []
    message_id = ""

    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            error_parts.append(f"stdout is not json: {exc}")

    ok = bool(parsed and parsed.get("ok") is True and returncode == 0)
    if parsed:
        data = parsed.get("data") or {}
        message = data.get("message") if isinstance(data, dict) else {}
        if not isinstance(message, dict):
            message = {}
        message_id = (
            parsed.get("message_id")
            or data.get("message_id")
            or message.get("message_id")
            or ""
        )
        if not ok:
            for key in ("error", "msg", "message", "code"):
                if parsed.get(key):
                    error_parts.append(f"{key}={parsed.get(key)}")

    if returncode != 0:
        error_parts.append(f"returncode={returncode}")
    if stdout.strip() and not ok:
        error_parts.append(f"stdout={stdout.strip()}")
    if stderr.strip():
        error_parts.append(f"stderr={stderr.strip()}")
    if not stdout.strip() and not stderr.strip() and not ok:
        error_parts.append("empty lark-cli output")

    return {
        "ok": ok,
        "message_id": message_id,
        "error": " | ".join(error_parts),
        "stdout": stdout,
        "stderr": stderr,
        "returncode": returncode,
    }


def run_lark_cli(args, timeout, cwd=None):
    try:
        r = _subprocess.run(
            [LARK_CLI] + args,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
        return parse_lark_cli_result(r.returncode, r.stdout, r.stderr)
    except Exception as exc:
        return {
            "ok": False,
            "message_id": "",
            "error": f"lark-cli exception: {exc}",
            "stdout": "",
            "stderr": "",
            "returncode": -1,
        }


def with_idempotency(args, idempotency_key):
    if idempotency_key:
        return args + ["--idempotency-key", idempotency_key]
    return args


def get_feishu_send_mode():
    return runtime_value("FEISHU_SEND_MODE", "cli").strip().lower()


def get_feishu_chat_id():
    return runtime_value("FEISHU_CHAT_ID", FEISHU_CHAT_ID)


def split_chat_ids(raw):
    return [cid.strip() for cid in str(raw or "").split(",") if cid.strip()]


def dedupe_chat_ids(chat_ids):
    result = []
    seen = set()
    for cid in chat_ids:
        if cid and cid not in seen:
            result.append(cid)
            seen.add(cid)
    return result


def get_feishu_content_chat_ids():
    """Return a list of chat IDs from FEISHU_CONTENT_CHAT_IDS (comma-separated)."""
    return split_chat_ids(runtime_value("FEISHU_CONTENT_CHAT_IDS", ""))


def get_feishu_alert_chat_ids():
    """Return chat IDs for alert messages.

    Priority: explicit FEISHU_ALERT_CHAT_IDS > content/test groups > primary.
    Alerts go to test groups by default, NOT to formal content groups.
    """
    raw = runtime_value("FEISHU_ALERT_CHAT_IDS", "") or runtime_value("FEISHU_ALERT_CHAT_ID", "")
    alert_ids = split_chat_ids(raw)
    if alert_ids:
        return dedupe_chat_ids(alert_ids)
    # Fallback: use content/test groups instead of primary formal group
    content_ids = get_feishu_content_chat_ids()
    if content_ids:
        return content_ids
    # Last resort: primary formal group
    primary = get_feishu_chat_id()
    return [primary] if primary else []


def get_feishu_alert_chat_id():
    chat_ids = get_feishu_alert_chat_ids()
    return chat_ids[0] if chat_ids else get_feishu_chat_id()


def get_all_feishu_chat_ids():
    """Return all groups that should receive synced ZSXQ content.

    Alert chat IDs are NOT included here. Alerts route separately via
    fs_send_text -> get_feishu_alert_chat_ids -> test groups.
    """
    primary = get_feishu_chat_id()
    return dedupe_chat_ids([primary] + get_feishu_content_chat_ids())


def format_feishu_multi_chat_error(failures):
    parts = []
    for result in failures:
        chat_id = result.get("chat_id", "?")
        error = result.get("error", "unknown error")
        parts.append(f"{chat_id}: {error}")
    return "Feishu send failed for chat_ids: " + "; ".join(parts)


def send_openapi_to_all_chats(send_func, target, idempotency_key=None, chat_ids=None):
    results = []
    for cid in chat_ids if chat_ids is not None else get_all_feishu_chat_ids():
        key = f"{idempotency_key}_{cid}" if idempotency_key else None
        result = dict(send_func(target, idempotency_key=key, chat_id=cid) or {})
        result["chat_id"] = cid
        results.append(result)

    if not results:
        return {"ok": False, "message_id": "", "error": "no chat_ids configured", "results": []}

    failures = [r for r in results if not r.get("ok")]
    for result in failures:
        log_msg(f"SEND_FAILED to {result.get('chat_id','?')}: {result.get('error','')[:200]}")
    if failures:
        return {
            "ok": False,
            "message_id": "",
            "error": format_feishu_multi_chat_error(failures),
            "results": results,
        }

    first = dict(results[0])
    first["results"] = results
    return first


def parse_feishu_api_result(status, body):
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body or "")
    try:
        parsed = json.loads(text) if text else {}
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "message_id": "",
            "error": f"Feishu response is not json: {exc}; status={status}; body={text[:500]}",
            "status": status,
        }

    ok = status == 200 and parsed.get("code") == 0
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
    message_id = data.get("message_id") or ""
    error = ""
    if not ok:
        error = f"Feishu API failed: status={status}; code={parsed.get('code')}; msg={parsed.get('msg') or parsed.get('message')}; body={text[:500]}"
    return {
        "ok": ok,
        "message_id": message_id,
        "error": error,
        "status": status,
        "data": data,
        "json": parsed,
    }


def http_post_json(url, payload, token=None, timeout=30, post_func=http_post):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response, status = post_func(url, body, headers, timeout)
    return parse_feishu_api_result(status, response)


def multipart_body(fields, files):
    boundary = "----ZSXQFeishu" + os.urandom(8).hex()
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, info in files.items():
        filename, content_type, data = info
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


def get_feishu_tenant_token(post_func=http_post):
    app_id = runtime_value("ZSXQ_FEISHU_APP_ID") or runtime_value("FEISHU_APP_ID")
    app_secret = runtime_value("ZSXQ_FEISHU_APP_SECRET") or runtime_value("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("missing FEISHU_APP_ID / ZSXQ_FEISHU_APP_ID or FEISHU_APP_SECRET / ZSXQ_FEISHU_APP_SECRET")
    result = http_post_json(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        post_func=post_func,
    )
    if not result["ok"]:
        raise RuntimeError(result["error"])
    token = result["data"].get("tenant_access_token") or result["json"].get("tenant_access_token")
    if not token:
        raise RuntimeError("Feishu tenant_access_token missing in response")
    return token


def feishu_uuid(idempotency_key):
    if not idempotency_key:
        return None
    text = str(idempotency_key)
    if len(text) <= 50:
        return text
    import hashlib as _hashlib
    return text[:33] + "-" + _hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def feishu_upload_image_openapi(filepath, tenant_token, post_func=http_post):
    with open(filepath, "rb") as f:
        data = f.read()
    content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    boundary, body = multipart_body(
        {"image_type": "message"},
        {"image": (os.path.basename(filepath), content_type, data)},
    )
    response, status = post_func(
        f"{FEISHU_API_BASE}/im/v1/images",
        body,
        {
            "Authorization": f"Bearer {tenant_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        60,
    )
    result = parse_feishu_api_result(status, response)
    if not result["ok"]:
        raise RuntimeError(result["error"])
    image_key = result["data"].get("image_key")
    if not image_key:
        raise RuntimeError("Feishu image_key missing in response")
    return image_key


def feishu_upload_file_openapi(filepath, tenant_token, post_func=http_post):
    with open(filepath, "rb") as f:
        data = f.read()
    content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    boundary, body = multipart_body(
        {"file_type": "stream", "file_name": os.path.basename(filepath)},
        {"file": (os.path.basename(filepath), content_type, data)},
    )
    response, status = post_func(
        f"{FEISHU_API_BASE}/im/v1/files",
        body,
        {
            "Authorization": f"Bearer {tenant_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        120,
    )
    result = parse_feishu_api_result(status, response)
    if not result["ok"]:
        raise RuntimeError(result["error"])
    file_key = result["data"].get("file_key")
    if not file_key:
        raise RuntimeError("Feishu file_key missing in response")
    return file_key


def feishu_send_message_openapi(msg_type, content, idempotency_key=None, tenant_token=None, chat_id=None, post_func=http_post):
    tenant_token = tenant_token or get_feishu_tenant_token(post_func=post_func)
    payload = {
        "receive_id": chat_id or get_feishu_chat_id(),
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }
    uuid = feishu_uuid(idempotency_key)
    if uuid:
        payload["uuid"] = uuid
    return http_post_json(
        f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
        payload,
        token=tenant_token,
        timeout=60,
        post_func=post_func,
    )


def feishu_send_image_openapi(filepath, idempotency_key=None, tenant_token=None, chat_id=None, post_func=http_post):
    try:
        tenant_token = tenant_token or get_feishu_tenant_token(post_func=post_func)
        image_key = feishu_upload_image_openapi(filepath, tenant_token, post_func=post_func)
        return feishu_send_message_openapi(
            "image",
            {"image_key": image_key},
            idempotency_key=idempotency_key,
            tenant_token=tenant_token,
            chat_id=chat_id,
            post_func=post_func,
        )
    except Exception as exc:
        return {"ok": False, "message_id": "", "error": f"Feishu OpenAPI image send failed: {exc}"}


def feishu_send_file_openapi(filepath, idempotency_key=None, tenant_token=None, chat_id=None, post_func=http_post):
    try:
        tenant_token = tenant_token or get_feishu_tenant_token(post_func=post_func)
        file_key = feishu_upload_file_openapi(filepath, tenant_token, post_func=post_func)
        return feishu_send_message_openapi(
            "file",
            {"file_key": file_key, "file_name": os.path.basename(filepath)},
            idempotency_key=idempotency_key,
            tenant_token=tenant_token,
            chat_id=chat_id,
            post_func=post_func,
        )
    except Exception as exc:
        return {"ok": False, "message_id": "", "error": f"Feishu OpenAPI file send failed: {exc}"}


def feishu_send_text_openapi(text, idempotency_key=None, tenant_token=None, chat_id=None, post_func=http_post):
    try:
        return feishu_send_message_openapi(
            "text",
            {"text": text},
            idempotency_key=idempotency_key,
            tenant_token=tenant_token,
            chat_id=chat_id,
            post_func=post_func,
        )
    except Exception as exc:
        return {"ok": False, "message_id": "", "error": f"Feishu OpenAPI text send failed: {exc}"}


def fs_send_image(filepath, idempotency_key=None):
    if get_feishu_send_mode() == "openapi":
        return send_openapi_to_all_chats(feishu_send_image_openapi, filepath, idempotency_key)
    return run_lark_cli(
        with_idempotency(
            ["im", "+messages-send", "--as", "bot", "--chat-id", get_feishu_chat_id(), "--image", os.path.basename(filepath)],
            idempotency_key,
        ),
        30,
        cwd=os.path.dirname(filepath) or ".",
    )


def fs_send_file(filepath, idempotency_key=None):
    if get_feishu_send_mode() == "openapi":
        return send_openapi_to_all_chats(feishu_send_file_openapi, filepath, idempotency_key)
    return run_lark_cli(
        with_idempotency(
            ["im", "+messages-send", "--as", "bot", "--chat-id", get_feishu_chat_id(), "--file", os.path.basename(filepath)],
            idempotency_key,
        ),
        60,
        cwd=os.path.dirname(filepath) or ".",
    )


def fs_send_text(text, idempotency_key=None):
    if get_feishu_send_mode() == "openapi":
        return send_openapi_to_all_chats(
            feishu_send_text_openapi,
            text,
            idempotency_key,
            chat_ids=get_feishu_alert_chat_ids(),
        )
    return run_lark_cli(
        with_idempotency(
            ["im", "+messages-send", "--as", "bot", "--chat-id", get_feishu_alert_chat_id(), "--text", text],
            idempotency_key,
        ),
        30,
        cwd=".",
    )


def send_alert(message):
    text = f"[ZSXQ监控告警] {message}"
    log_msg(f"ALERT: {message}")
    result = fs_send_text(text)
    if not result["ok"]:
        log_msg(f"ALERT_SEND_FAILED: {result['error']}")


# ---- ZSXQ ----
def zsxq_req(url, token):
    return json.loads(
        http_get(url, {"Cookie": f"zsxq_access_token={token}; abtest_env=product"}).decode("utf-8")
    )


def zsxq_topics(group_id, token, count=20, end_time=None):
    ts = int(time.time() * 1000)
    url = f"https://api.zsxq.com/v2/groups/{group_id}/topics?scope=all&count={count}&_t={ts}"
    if end_time:
        url += f"&end_time={end_time}"
    return zsxq_req(url, token)


def zsxq_image_url(image_id, token):
    for attempt in range(5):
        time.sleep(IMAGE_API_INTERVAL)
        try:
            data = zsxq_req(f"https://api.zsxq.com/v2/images/{image_id}", token)
            if not data.get("succeeded"):
                wait = 10.0 * (attempt + 1) if data.get("code") == 1059 else 0.5
                if attempt < 4:
                    time.sleep(wait)
                continue
            img_data = data.get("resp_data", {}).get("image", {})
            # Try multiple size keys; ZSXQ changes these without notice
            url = ""
            for size_key in ("large", "original", "thumbnail"):
                candidate = img_data.get(size_key, {}).get("url", "")
                if candidate:
                    url = candidate
                    break
            if url:
                return url
            if attempt < 4:
                time.sleep(0.5)
        except Exception:
            if attempt < 4:
                time.sleep(1.0)
    return ""


def zsxq_file_dl(file_id, token):
    for attempt in range(5):
        try:
            data = zsxq_req(f"https://api.zsxq.com/v2/files/{file_id}/download_url", token)
            if not data.get("succeeded"):
                wait = 2.0 if data.get("code") == 1059 else 0.5
                if attempt < 4:
                    time.sleep(wait)
                continue
            url = data.get("resp_data", {}).get("download_url", "")
            if url:
                return url
            if attempt < 4:
                time.sleep(0.5)
        except Exception:
            if attempt < 4:
                time.sleep(1.0)
    return ""


def is_auth_error(error):
    text = str(error).lower()
    return "401" in text or "unauthorized" in text or "forbidden" in text or "403" in text


def is_access_block_error(error):
    text = str(error)
    return "1059" in text or "非官方工具" in text or "garden.zsxq.com/skill" in text


# ---- Notes API ----
def notes_import_image(filepath):
    boundary = "----FB" + os.urandom(8).hex()
    with open(filepath, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{os.path.basename(filepath)}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    r, status = http_post(
        NOTES_IMPORT,
        body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        30,
    )
    if status == 200:
        return json.loads(r).get("url", "")
    return ""


def notes_export(markdown_text, footer_brand="击球区小能手的星球"):
    data, status = http_post(
        NOTES_EXPORT,
        json.dumps(
            {
                "markdown": markdown_text,
                "theme": "default",
                "footerBrand": footer_brand,
                "footerVia": "",
            }
        ).encode(),
        {"Content-Type": "application/json"},
        60,
    )
    if status == 200:
        return data
    raise RuntimeError(f"NOTE_API_ERR: HTTP {status}")


# ---- Save helpers ----
def get_save_dir(post_create_time=None):
    if post_create_time:
        date_str = post_create_time[:10].replace("-", "")
    else:
        date_str = time.strftime("%Y%m%d")
    d = os.path.join(SAVE_BASE, date_str)
    os.makedirs(d, exist_ok=True)
    return d


def make_note_name(create_time):
    ct = create_time[:19].replace("-", "").replace(":", "").replace("T", "")
    base = ct[:12]
    date_str = create_time[:10].replace("-", "")
    target_dir = os.path.join(SAVE_BASE, date_str)
    candidate = base
    suffix = 0
    while os.path.exists(os.path.join(target_dir, candidate + ".png")):
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate + ".png"


def safe_filename(name):
    name = os.path.basename(name or "attachment")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name[:180] or "attachment"


def unique_path(directory, filename):
    base, ext = os.path.splitext(filename)
    path = os.path.join(directory, filename)
    suffix = 1
    while os.path.exists(path):
        path = os.path.join(directory, f"{base}_{suffix}{ext}")
        suffix += 1
    return path


def compress_for_feishu(note_png, save_path):
    if len(note_png) <= 4 * 1024 * 1024:
        return save_path, note_png

    import io as _io

    Image.MAX_IMAGE_PIXELS = None
    tmp_img = Image.open(_io.BytesIO(note_png))
    w, h = tmp_img.size
    best = None
    for try_w, try_q in [(1600, 50), (1400, 40), (1200, 35), (1000, 30)]:
        ratio = try_w / w
        rsz = tmp_img.resize((try_w, int(h * ratio)), Image.LANCZOS) if ratio < 1 else tmp_img
        buf = _io.BytesIO()
        rsz.save(buf, format="JPEG", quality=try_q, optimize=True)
        compressed = buf.getvalue()
        buf.close()
        best = compressed
        if len(compressed) < 5 * 1024 * 1024:
            break
    tmp_img.close()

    if not best or len(best) >= 5 * 1024 * 1024:
        raise RuntimeError("COMPRESS_ERR: image still exceeds Feishu limit")

    save_path_jpg = save_path.replace(".png", ".jpg")
    with open(save_path_jpg, "wb") as f:
        f.write(best)
    return save_path_jpg, best
def extract_topic_content(topic, include_reference=True):
    """Extract text, images, files from a topic.

    If include_reference and the topic has a referenced_topic, append the
    referenced content after the main text with a separator.
    """
    topic_type = topic.get("type", "talk")
    text = ""
    images = []
    files = []

    if topic_type == "q&a":
        question = topic.get("question", {})
        answer = topic.get("answer", {}) if topic.get("answer") else {}
        q_text = strip_html(question.get("text", "")) if question else ""
        a_text = strip_html(answer.get("text", "")) if answer else ""
        if q_text:
            text = f"❓ {q_text}"
        if a_text:
            text = text + ("\n\n💡 " + a_text) if text else f"💡 {a_text}"
        images = (question.get("images", []) if question else []) + (answer.get("images", []) if answer else [])
        files = (question.get("files", []) if question else []) + (answer.get("files", []) if answer else [])
    else:
        talk = topic.get("talk", {})
        text = strip_html(talk.get("text", ""))
        images = talk.get("images", [])
        files = talk.get("files", [])

    # Handle referenced_topic: include quoted content
    if include_reference:
        ref_wrapper = topic.get("referenced_topic", {})
        ref_topic = ref_wrapper.get("topic") if isinstance(ref_wrapper, dict) else None
        if ref_topic:
            ref_text, ref_images, ref_files = extract_topic_content(
                ref_topic, include_reference=False
            )
            ref_time = (ref_topic.get("create_time", "") or "")[:16].replace("T", " ")
            ref_label = f"📌 引用原文 ({ref_time})"
            ref_block = ref_label
            if ref_text:
                ref_block = ref_label + "\n" + ref_text

            # Add referenced file info to text
            ref_file_lines = []
            for fitem in ref_files:
                fname = fitem.get("name", "")
                if not fname:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    ftime = (fitem.get("create_time", "") or "")[:16].replace("-", "").replace("T", "").replace(":", "")
                    if ftime:
                        ref_file_lines.append(f"[{ftime}_音频]")
                    else:
                        ref_file_lines.append(f"[音频] {fname}")
                else:
                    ref_file_lines.append(f"📎 {fname}")
            if ref_file_lines:
                ref_block = ref_block + "\n" + "\n".join(ref_file_lines)

            if text:
                text = text + "\n\n---\n\n" + ref_block
            else:
                text = ref_block
            images = images + ref_images
            files = files + ref_files

    return text, images, files


def render_topic_note(topic, ztok, group_name=None):
    text, images, files = extract_topic_content(topic)
    ctime_raw = topic.get("create_time", "")
    ctime = ctime_raw[:16].replace("T", " ")
    md_parts = [ctime]
    if text:
        md_parts.append(text)

    img_urls = []
    for img in images:
        img_id = img.get("image_id")
        if not img_id:
            continue
        local = os.path.join(TEMP_DIR, f"zsxq_{img_id}.png")
        try:
            zsxq_url = zsxq_image_url(img_id, ztok)
            if not zsxq_url:
                raise RuntimeError(f"image url missing: {img_id}")
            with open(local, "wb") as f:
                f.write(http_get(zsxq_url))
            public_url = notes_import_image(local)
            if not public_url:
                raise RuntimeError(f"note image import failed: {img_id}")
            img_urls.append(public_url)
        finally:
            try:
                os.remove(local)
            except FileNotFoundError:
                pass

    for url in img_urls:
        md_parts.append(f"![image]({url})")

    file_names = []
    for item in files:
        fname = item.get("name", "")
        if not fname:
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            ftime = (item.get("create_time", "") or "")[:16].replace("-", "").replace("T", "").replace(":", "")
            if ftime:
                file_names.append(f"[{ftime}_音频]")
            else:
                file_names.append(fname)
        else:
            file_names.append(fname)
    if file_names:
        md_parts.append("\n📎 " + "、".join(file_names))

    md_text = "\n\n".join(md_parts)
    footer = group_name or "击球区小能手的星球"
    note_png = notes_export(md_text, footer_brand=footer)

    # Pre-resize to reduce memory for watermark (critical on 2GB servers)
    import io as _io
    _tmp_img = Image.open(_io.BytesIO(note_png))
    _w, _h = _tmp_img.size
    if _w > 1200:
        _ratio = 1200 / _w
        _tmp_img = _tmp_img.resize((1200, int(_h * _ratio)), Image.LANCZOS)
        _buf = _io.BytesIO()
        _tmp_img.save(_buf, format='PNG')
        note_png = _buf.getvalue()
        _buf.close()
    _tmp_img.close()

    note_png = add_watermark(note_png)

    save_dir = get_save_dir(ctime_raw)
    note_name = make_note_name(ctime_raw)
    save_path = os.path.join(save_dir, note_name)
    with open(save_path, "wb") as f:
        f.write(note_png)
    save_path, _ = compress_for_feishu(note_png, save_path)

    # Upload to OSS after local save
    if _oss and _oss.OSS_ENABLED and _oss.OSS_BUCKET:
        date_str = ctime_raw[:10].replace("-", "")
        oss_key = _oss.oss_key_for_archive(group_name or "unknown", date_str, os.path.basename(save_path))
        if _oss.oss_upload(save_path, oss_key, log_func=log_msg):
            save_path = f"oss://{_oss.OSS_BUCKET}/{oss_key}"

    return save_path, files


def process_topic_record(conn, record, ztok, group_name=None):
    tid = record["topic_id"]
    archive_path = record.get("archive_path")

    local_missing = not archive_path or (
        not archive_path.startswith("oss://") and not os.path.exists(archive_path)
    )
    if local_missing:
        if archive_path and archive_path.startswith("oss://") and _oss and _oss.OSS_ENABLED:
            local_copy = os.path.join(TEMP_DIR, os.path.basename(archive_path))
            if _oss.oss_download(archive_path.replace(f"oss://{_oss.OSS_BUCKET}/", ""), local_copy, log_func=log_msg):
                archive_path = local_copy
            else:
                archive_path = None
        if not archive_path or (not archive_path.startswith("oss://") and not os.path.exists(archive_path)):
            topic = json.loads(record["topic_json"])
            save_path, files = render_topic_note(topic, ztok, group_name=group_name)
        upsert_file_records(conn, tid, topic.get("create_time", ""), files)
        mark_topic_rendered(conn, tid, save_path)
        archive_path = save_path

    # Always include timestamp in idempotency key so every send attempt is
    # unique — prevents Feishu from returning cached old results on re-renders
    idem_key = f"zsxq-topic-{topic_id_key(tid)}-v{int(time.time())}"
    result = fs_send_image(archive_path, idempotency_key=idem_key)
    if result["ok"]:
        mark_topic_sent(conn, tid, archive_path, result.get("message_id"))
        log_msg(f"TOPIC_SENT: {tid} {archive_path}")
        return True

    mark_topic_failed(conn, tid, result["error"])
    send_alert(f"飞书便签图片发送失败，topic_id={tid}，错误={result['error'][:500]}")
    return False


def process_pending_topics(conn, ztok, group_name=None):
    sent = 0
    failed = 0
    for record in get_pending_topics(conn):
        try:
            if process_topic_record(conn, record, ztok, group_name=group_name):
                sent += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            mark_topic_failed(conn, record["topic_id"], exc)
            log_msg(f"POST_ERR: topic_id={record['topic_id']} {exc}")
            traceback.print_exc()
    return sent, failed


def compress_audio_for_feishu(filepath, max_size=FEISHU_FILE_MAX_SIZE):
    """Re-encode audio file to fit within Feishu file size limit using ffmpeg."""
    import subprocess as _sp

    ext = os.path.splitext(filepath)[1].lower()
    if ext not in AUDIO_EXTENSIONS:
        raise ValueError(f"Not an audio file: {ext}")

    orig_size = os.path.getsize(filepath)
    if orig_size <= max_size:
        return filepath, False

    try:
        probe = _sp.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=_sp.PIPE, stderr=_sp.PIPE, universal_newlines=True, timeout=30
        )
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else None
    except Exception:
        duration = None

    if not duration or duration <= 0:
        raise RuntimeError(f"Cannot determine audio duration for compression: {filepath}")

    target_bytes = int(max_size * 0.9)
    target_bitrate = max(16, int(target_bytes * 8 / duration / 1000))
    if target_bitrate > 128:
        target_bitrate = 128
    log_msg(f"COMPRESS_AUDIO: {os.path.basename(filepath)} {orig_size/(1024*1024):.1f}MB, "
            f"duration={duration:.0f}s, target_bitrate={target_bitrate}kbps")

    compressed_path = filepath.rsplit(".", 1)[0] + "_compressed.mp3"
    result = _sp.run(
        ["ffmpeg", "-y", "-i", filepath,
         "-acodec", "libmp3lame", "-b:a", f"{target_bitrate}k",
         "-ac", "1", "-ar", "22050",
         compressed_path],
        stdout=_sp.PIPE, stderr=_sp.PIPE, universal_newlines=True, timeout=300
    )

    if result.returncode != 0 or not os.path.exists(compressed_path):
        err = result.stderr[-500:] if result.stderr else "unknown ffmpeg error"
        raise RuntimeError(f"Audio compression failed: {err}")

    new_size = os.path.getsize(compressed_path)
    if new_size > max_size:
        os.remove(compressed_path)
        raise RuntimeError(
            f"Compressed audio still too large: {new_size/(1024*1024):.1f}MB > {max_size/(1024*1024)}MB limit"
        )

    log_msg(f"COMPRESS_AUDIO_OK: {os.path.basename(filepath)} -> "
            f"{orig_size/(1024*1024):.1f}MB -> {new_size/(1024*1024):.1f}MB")
    return compressed_path, True


def process_pending_files(conn, ztok):
    sent = 0
    failed = 0
    for record in get_pending_files(conn):
        key = record["file_key"]
        # Use formatted name for audio files
        local_name = safe_filename(record["name"])
        ext = os.path.splitext(local_name)[1].lower()
        if ext in AUDIO_EXTENSIONS:
            ftime = (record.get("create_time", "") or "")[:16].replace("-", "").replace("T", "").replace(":", "")
            if ftime:
                local_name = f"{ftime}_音频{ext}"
        local = os.path.join(TEMP_DIR, local_name)
        local_to_send = None
        try:
            dl_url = zsxq_file_dl(record["file_id"], ztok)
            if not dl_url:
                raise RuntimeError("file download url missing")
            with open(local, "wb") as f:
                f.write(http_get(dl_url, {"Cookie": f"zsxq_access_token={ztok}; abtest_env=product"}))
            save_dir = get_save_dir(record["create_time"])
            # Rename audio files to {timestamp}_音频{ext} format
            save_name = safe_filename(record["name"])
            ext = os.path.splitext(save_name)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                ftime = (record.get("create_time", "") or "")[:16].replace("-", "").replace("T", "").replace(":", "")
                if ftime:
                    save_name = f"{ftime}_音频{ext}"
            archive_path = unique_path(save_dir, f"{record['topic_id']}_{save_name}")
            with open(archive_path, "wb") as f:
                with open(local, "rb") as src:
                    f.write(src.read())

            # Upload to OSS
            if _oss and _oss.OSS_ENABLED and _oss.OSS_BUCKET:
                date_str = (record.get("create_time", "") or "")[:10].replace("-", "")
                oss_key = _oss.oss_key_for_archive("files", date_str, os.path.basename(archive_path))
                if _oss.oss_upload(archive_path, oss_key, log_func=log_msg):
                    archive_path = f"oss://{_oss.OSS_BUCKET}/{oss_key}"

            # Check file size against Feishu limit
            file_size = os.path.getsize(local)
            ext = os.path.splitext(record["name"])[1].lower()
            local_to_send = local

            if file_size > FEISHU_FILE_MAX_SIZE and ext in AUDIO_EXTENSIONS:
                try:
                    compressed_path, _ = compress_audio_for_feishu(local, FEISHU_FILE_MAX_SIZE)
                    if compressed_path != local:
                        local_to_send = compressed_path
                        log_msg(f"FILE_COMPRESSED: {key} {file_size/(1024*1024):.1f}MB -> {os.path.getsize(compressed_path)/(1024*1024):.1f}MB")
                except Exception as comp_err:
                    raise RuntimeError(f"Audio compression failed for {file_size/(1024*1024):.1f}MB file: {comp_err}")
            elif file_size > FEISHU_FILE_MAX_SIZE:
                raise RuntimeError(
                    f"File too large ({file_size/(1024*1024):.1f}MB > {FEISHU_FILE_MAX_SIZE/(1024*1024)}MB), "
                    f"not an audio file (ext={ext}), cannot compress"
                )

            idempotency_key = "zsxq-file-" + key.replace(":", "-")
            result = fs_send_file(local_to_send, idempotency_key=idempotency_key)
            if result["ok"]:
                mark_file_sent(conn, key, archive_path)
                sent += 1
                log_msg(f"FILE_SENT: {key} {archive_path}")
            else:
                raise RuntimeError(result["error"])
        except Exception as exc:
            failed += 1
            mark_file_failed(conn, key, exc)
            retry_count = record.get("retry_count", 0) + 1
            error_str = str(exc)
            if retry_count >= FILE_MAX_RETRIES or "file size exceed" in error_str.lower():
                try:
                    conn.execute(
                        "UPDATE files SET status='permanent_fail', last_error=? WHERE file_key=?",
                        (f"Max retries ({FILE_MAX_RETRIES}) or unrecoverable: {error_str[:400]}", key)
                    )
                    conn.commit()
                    log_msg(f"FILE_PERMANENT_FAIL: {key} retry_count={retry_count}, error={error_str[:200]}")
                except Exception:
                    pass
            else:
                send_alert(f"飞书附件发送失败，file_key={key}，错误={error_str[:500]}")
        finally:
            try:
                os.remove(local)
            except FileNotFoundError:
                pass
    return sent, failed


def save_config_file(cfg, path=CONFIG_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def save_config(cfg):
    save_config_file(cfg, CONFIG_FILE)


def env_file_has_key(path, key):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and line.startswith(f"{key}="):
                    return True
    except FileNotFoundError:
        pass
    return False


def append_env_value(path, key, value):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    if content and not content.endswith("\n"):
        content += "\n"
    content += f"{key}={value}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def migrate_token_to_env(config_path=CONFIG_FILE, env_path=ENV_FILE):
    try:
        cfg = load_config_file(config_path)
    except Exception as exc:
        return {"ok": False, "changed": False, "message": f"config read failed: {exc}"}

    token = cfg.get("access_token")
    env_has_token = env_file_has_key(env_path, "ZSXQ_ACCESS_TOKEN")

    if not token:
        if env_has_token:
            return {"ok": True, "changed": False, "message": "token already stored in .env"}
        return {
            "ok": False,
            "changed": False,
            "message": "missing access_token in config and ZSXQ_ACCESS_TOKEN in .env",
        }

    try:
        if env_has_token:
            message = "removed legacy access_token from config; existing .env token kept"
        else:
            append_env_value(env_path, "ZSXQ_ACCESS_TOKEN", token)
            message = "migrated access_token from config to .env"
        del cfg["access_token"]
        save_config_file(cfg, config_path)
        return {"ok": True, "changed": True, "message": message}
    except Exception as exc:
        return {"ok": False, "changed": False, "message": f"migration failed: {exc}"}


def send_limited_alert(conn, meta_key, message, today=None):
    today = today or time.strftime("%Y-%m-%d")
    if get_meta(conn, meta_key, "") == today:
        return False
    send_alert(message)
    set_meta(conn, meta_key, today)
    return True


def handle_fetch_error(conn, error, today=None):
    log_msg(f"FETCH_ERR: {error}")
    count = increment_meta_int(conn, "consecutive_failures")
    if is_auth_error(error):
        send_limited_alert(
            conn,
            "auth_alert_date",
            f"ZSXQ token 可能已过期或无权限，错误={str(error)[:500]}",
            today=today,
        )
    elif is_access_block_error(error):
        send_limited_alert(
            conn,
            "access_block_alert_date",
            f"ZSXQ 可能阻断非官方访问，错误={str(error)[:500]}",
            today=today,
        )
    elif count >= ALERT_FAILURE_THRESHOLD:
        send_limited_alert(
            conn,
            "consecutive_failure_alert_date",
            f"ZSXQ 连续拉取失败 {count} 次，错误={str(error)[:500]}",
            today=today,
        )


def compute_next_last_seen_time(topics, page_limit_reached):
    if page_limit_reached:
        return None
    latest_time = None
    for topic in topics:
        ct = topic.get("create_time", "")
        if latest_time is None or ct > latest_time:
            latest_time = ct
    return latest_time


def fetch_topics(gid, ztok, last_seen, alert_on_limit=True):
    all_topics = []
    seen_ids = set()
    end_time = None
    page_limit_reached = False

    for page in range(MAX_PAGES):
        data = zsxq_topics(gid, ztok, 20, end_time)
        if not data.get("succeeded"):
            raise RuntimeError(
                "ZSXQ API failed: "
                + json.dumps(data, ensure_ascii=False, default=str)[:1000]
            )
        batch = data["resp_data"]["topics"]
        if not batch:
            break

        added = 0
        for topic in batch:
            tid = topic["topic_id"]
            if tid not in seen_ids:
                seen_ids.add(tid)
                all_topics.append(topic)
                added += 1
        if added == 0:
            break

        if last_seen is not None:
            oldest = batch[-1]
            if oldest.get("create_time", "") > last_seen:
                end_time = oldest.get("create_time", "")
                if not end_time:
                    break
            else:
                break
        else:
            break
        time.sleep(0.5)
        if page == MAX_PAGES - 1:
            page_limit_reached = True

    if page_limit_reached and alert_on_limit:
        send_alert(f"本轮拉取达到 {MAX_PAGES} 页上限，可能还有更早的新帖未追完")

    return {"topics": all_topics, "page_limit_reached": page_limit_reached}


def run_monitor(dry_run=False):
    lock = FileLock(LOCK_FILE)
    if not lock.acquire():
        log_msg("LOCKED: previous run still active")
        return

    conn = None
    try:
        conn = init_db(DB_FILE)
        if not dry_run:
            set_meta(conn, "last_heartbeat", now_text())

        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        env = load_env()
        gid = cfg["group_id"]
        footer_brand = cfg.get("footer_brand") or cfg.get("group_name", "击球区小能手的星球")
        ztok = env.get("ZSXQ_ACCESS_TOKEN") or cfg.get("access_token")
        if not ztok:
            if dry_run:
                log_msg("DRY_RUN: missing ZSXQ_ACCESS_TOKEN")
            else:
                send_alert("缺少 ZSXQ_ACCESS_TOKEN，请检查 .env 或配置文件")
            return
        last_seen = cfg.get("last_seen_time")

        try:
            fetch_result = fetch_topics(gid, ztok, last_seen, alert_on_limit=not dry_run)
        except Exception as exc:
            if dry_run:
                log_msg(f"DRY_RUN_FETCH_ERR: {exc}")
            else:
                handle_fetch_error(conn, exc)
            # Don't return - still process any pending topics/files already in DB
            topic_sent, topic_failed = process_pending_topics(conn, ztok, group_name=footer_brand)
            file_sent, file_failed = process_pending_files(conn, ztok)
            update_sent, update_failed = process_updated_topics(conn, ztok, group_name=footer_brand)
            set_meta(conn, "last_heartbeat", now_text())
            log_msg(
                f"RUN_DONE (fetch failed): topics sent={topic_sent}, topics failed={topic_failed}, "
                f"topics updated={update_sent}, update_failed={update_failed}, "
                f"files sent={file_sent}, files failed={file_failed}"
            )
            return

        set_meta(conn, "consecutive_failures", 0)
        all_topics = fetch_result["topics"]
        latest_time = compute_next_last_seen_time(
            all_topics,
            fetch_result["page_limit_reached"],
        )

        if last_seen is None:
            if latest_time:
                if dry_run:
                    log_msg(f"DRY_RUN_BOOTSTRAP: would set last_seen_time={latest_time}")
                    return
                cfg["last_seen_time"] = latest_time
                save_config(cfg)
                log_msg(f"BOOTSTRAP: set last_seen_time={latest_time} (no posts sent)")
            return

        new_topics = [topic for topic in all_topics if topic.get("create_time", "") > last_seen]
        new_topics.sort(key=lambda topic: topic.get("create_time", ""))
        if dry_run:
            would_update = latest_time if new_topics and latest_time else "no"
            log_msg(
                f"DRY_RUN: fetched={len(all_topics)}, new={len(new_topics)}, "
                f"would_update_last_seen={would_update}"
            )
            return

        for topic in new_topics:
            upsert_topic(conn, topic)

        if latest_time and new_topics:
            cfg["last_seen_time"] = latest_time
            save_config(cfg)
            log_msg(f"DISCOVERED: {len(new_topics)} new topics, last_seen_time={latest_time}")
        elif fetch_result["page_limit_reached"] and new_topics:
            log_msg(
                f"DISCOVERED_WITHOUT_CURSOR_ADVANCE: {len(new_topics)} new topics, "
                "page limit reached"
            )

        topic_sent, topic_failed = process_pending_topics(conn, ztok, group_name=footer_brand)
        file_sent, file_failed = process_pending_files(conn, ztok)
        set_meta(conn, "last_heartbeat", now_text())
        try:
            check_disk_space(conn)
        except Exception as exc:
            log_msg(f"DISK_CHECK_ERR: {exc}")
        if _oss and _oss.OSS_ENABLED:
            try:
                oss_ok = _oss.check_oss_health()
                if oss_ok is False:
                    log_msg("OSS_HEALTH: unreachable")
            except Exception as exc2:
                log_msg(f"OSS_HEALTH_ERR: {exc2}")
        try:
            send_daily_health_report(conn)
        except Exception as exc:
            log_msg(f"HEALTH_REPORT_ERR: {exc}")
        log_msg(
            f"RUN_DONE: topics sent={topic_sent}, topics failed={topic_failed}, "
            f"files sent={file_sent}, files failed={file_failed}"
        )
    finally:
        if conn is not None:
            conn.close()
        lock.release()


def print_status():
    conn = init_db(DB_FILE)
    try:
        print(format_status_summary(get_status_summary(conn)))
    finally:
        conn.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry_run = is_dry_run()
    if "--status" in argv:
        print_status()
        return 0
    if "--check" in argv:
        return print_check()
    if "--migrate-token" in argv:
        return print_migrate_token()
    if "--dry-run" in argv:
        dry_run = True
    run_monitor(dry_run=dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
