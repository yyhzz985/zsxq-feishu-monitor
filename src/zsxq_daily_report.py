#!/usr/bin/env python3
"""Generate ZSXQ daily Markdown/PDF reports and optionally upload PDFs to Feishu."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


INTRO = "\u9ad8\u6e05\u5927\u56fe\u53ca\u6587\u4ef6\u8bf7\u79fb\u6b65\u7fa4\u5185\u67e5\u9605\uff0c\u6bcf\u5929\u5b9e\u65f6\u66f4\u65b0\uff0c\u8fdb\u7fa4\u8054\u7cfb\u6e23\u59d0VX\uff1a237219265"
COMMENT_LABEL = "\u8bc4\u8bba\uff1a"
IMAGE_ALT = "\u56fe\u7247"
REPLY_WORD = "\u56de\u590d"
INDENT = "\u3000\u3000"
REPORT_SUFFIX = "\u542b\u5168\u90e8\u8bc4\u8bba"
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

CSS = """
@page { size: A4; margin: 18mm 17mm; }
* { box-sizing: border-box; }
body {
  margin: 0;
  color: #0b1f33;
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.68;
}
h1 { margin: 0 0 12px; font-size: 28px; line-height: 1.28; font-weight: 800; letter-spacing: 0; }
.intro-box { margin: 0 0 25px; padding: 12px 15px; border-left: 5px solid #f0bf2f; border-radius: 6px; background: #fff2a8; color: #13263a; font-weight: 700; }
h2 { margin: 20px 0 12px; padding: 0; border: 0; font-size: 23px; line-height: 1.35; font-weight: 800; letter-spacing: 0; }
p { margin: 0 0 12px; }
.attachment { color: #24384d; }
.comment-heading { margin: 17px 0 8px; }
mark { display: inline-block; padding: 3px 8px; border-radius: 4px; background: #ffe994; color: #0b1f33; }
blockquote { margin: 6px 0; padding: 8px 12px 8px 14px; border-left: 4px solid #9eb2ca; background: #f6f9fd; color: #0c2238; break-inside: auto; page-break-inside: auto; }
blockquote strong { font-weight: 800; }
blockquote.image-quote { background: #fbfdff; padding-top: 10px; padding-bottom: 10px; }
img { display: block; max-width: 100%; max-height: 215mm; width: auto; height: auto; object-fit: contain; margin: 8px 0; border-radius: 4px; break-inside: avoid; page-break-inside: avoid; }
blockquote img { margin: 4px 0 2px; }
.post-divider { display: block; height: 0; margin: 24px 0 22px; border: 0; border-top: 2px solid #d8e1eb; }
"""


class PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("br", "p", "div", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("p", "div", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


@dataclass
class DayOutput:
    date: dt.date
    title: str
    md_path: Path
    pdf_path: Path
    topic_count: int
    comment_count: int
    image_count: int
    pdf_stats: dict[str, Any]
    uploaded: bool = False
    file_token: str = ""


class ZsxqClient:
    def __init__(self, token: str, request_gap: float) -> None:
        self.token = token
        self.request_gap = request_gap
        self.last_request_at = 0.0
        self.ctx = ssl.create_default_context()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": f"zsxq_access_token={token}; abtest_env=product",
            "Referer": "https://wx.zsxq.com/",
            "Accept": "application/json, text/plain, */*",
        }

    def throttle(self) -> None:
        wait = self.request_gap - (time.time() - self.last_request_at)
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.time()

    def get(self, path: str, params: dict[str, Any] | None = None, retries: int = 8) -> dict[str, Any]:
        if path.startswith("http"):
            url = path
            if params:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        else:
            url = "https://api.zsxq.com/v2" + path
            if params:
                url += "?" + urllib.parse.urlencode(params)

        last: Exception | dict[str, Any] | None = None
        for attempt in range(retries):
            self.throttle()
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=45, context=self.ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data.get("succeeded"):
                    return data
                last = data
                if data.get("code") in (1059, "1059"):
                    time.sleep(min(55, 8 + attempt * 8))
                    continue
                if attempt < retries - 1:
                    time.sleep(2 + attempt)
                    continue
                return data
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < retries - 1:
                    time.sleep(2 + attempt * 2)
                    continue
                raise

        if isinstance(last, Exception):
            raise last
        return last or {"succeeded": False, "code": "unknown"}

    def fetch_topics_range(self, group_id: str, start: str, end: str) -> list[dict[str, Any]]:
        topics: list[dict[str, Any]] = []
        seen: set[str] = set()
        end_time = end
        for _page in range(100):
            data = self.get(
                f"/groups/{group_id}/topics",
                {
                    "scope": "all",
                    "count": 20,
                    "end_time": end_time,
                    "_t": int(time.time() * 1000),
                },
            )
            if not data.get("succeeded"):
                raise RuntimeError(f"topics failed: code={data.get('code')} info={data.get('info')}")
            batch = data.get("resp_data", {}).get("topics", [])
            if not batch:
                break
            for topic in batch:
                tid = str(topic.get("topic_uid") or topic.get("topic_id"))
                create_time = topic.get("create_time", "")
                if tid not in seen and start <= create_time < end:
                    topics.append(topic)
                    seen.add(tid)
            oldest = batch[-1].get("create_time", "")
            if oldest <= start or oldest == end_time:
                break
            end_time = oldest
        return sorted(topics, key=lambda item: item.get("create_time", ""), reverse=True)

    def fetch_comments(self, topic: dict[str, Any], count: int, max_pages: int) -> list[dict[str, Any]]:
        topic_uid = str(topic.get("topic_uid") or topic.get("topic_id"))
        comments: list[dict[str, Any]] = []
        seen: set[str] = set()
        index = ""
        for page in range(max_pages):
            params: dict[str, Any] = {
                "sort": "asc",
                "count": count,
                "_t": int(time.time() * 1000),
            }
            if index:
                params["index"] = index
            data = self.get(f"/topics/{topic_uid}/comments", params)
            if not data.get("succeeded"):
                raise RuntimeError(f"comments failed: topic={topic_uid} code={data.get('code')} info={data.get('info')}")
            resp_data = data.get("resp_data", {})
            batch = resp_data.get("comments", [])
            for comment in batch:
                cid = str(comment.get("comment_id"))
                if cid not in seen:
                    comments.append(comment)
                    seen.add(cid)
            index = str(resp_data.get("index") or "")
            if not index:
                break
            if page == max_pages - 1:
                raise RuntimeError(f"comment pagination exceeded max pages: topic={topic_uid}")

        total = len(comments) + sum(len(c.get("replied_comments") or []) for c in comments)
        expected = int(topic.get("comments_count") or 0)
        if total != expected:
            raise RuntimeError(
                f"comment count mismatch: topic={topic_uid} expected={expected} got={total} top={len(comments)}"
            )
        return comments

    def image_url(self, image: dict[str, Any]) -> str:
        direct = image.get("url") or image.get("href")
        if direct:
            return str(direct)
        for key in ("large", "original", "thumbnail"):
            obj = image.get(key) or {}
            if isinstance(obj, dict) and obj.get("url"):
                return str(obj["url"])
        image_id = image.get("image_id")
        if image_id:
            data = self.get(f"/images/{image_id}", {"_t": int(time.time() * 1000)})
            if not data.get("succeeded"):
                raise RuntimeError(f"image url failed: image_id={image_id} code={data.get('code')}")
            img_data = data.get("resp_data", {}).get("image", {})
            for key in ("large", "original", "thumbnail"):
                obj = img_data.get(key) or {}
                if isinstance(obj, dict) and obj.get("url"):
                    return str(obj["url"])
        return ""


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def config_value(env: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = os.environ.get(key) or env.get(key)
        if value:
            return value
    return ""


def apply_env_defaults(args: argparse.Namespace, env: dict[str, str]) -> None:
    defaults: dict[str, tuple[str, ...]] = {
        "timezone": ("ZSXQ_DAILY_TIMEZONE", "TZ"),
        "group_id": ("ZSXQ_DAILY_GROUP_ID",),
        "group_name": ("ZSXQ_DAILY_GROUP_NAME",),
        "output_dir": ("ZSXQ_DAILY_OUTPUT_DIR",),
        "feishu_doc": ("ZSXQ_DAILY_FEISHU_DOC_URL",),
        "insert_before_date": ("ZSXQ_DAILY_INSERT_BEFORE_DATE",),
        "lark_cli": ("ZSXQ_DAILY_LARK_CLI",),
        "lark_as": ("ZSXQ_DAILY_LARK_AS",),
        "feishu_app_id": ("ZSXQ_DAILY_FEISHU_APP_ID", "ZSXQ_FEISHU_APP_ID", "FEISHU_APP_ID"),
        "feishu_app_secret": ("ZSXQ_DAILY_FEISHU_APP_SECRET", "ZSXQ_FEISHU_APP_SECRET", "FEISHU_APP_SECRET"),
        "feishu_receive_id": ("ZSXQ_DAILY_FEISHU_RECEIVE_ID",),
        "feishu_receive_id_type": ("ZSXQ_DAILY_FEISHU_RECEIVE_ID_TYPE",),
    }
    for attr, keys in defaults.items():
        if not getattr(args, attr):
            value = config_value(env, *keys)
            if value:
                setattr(args, attr, value)
    if args.date is None:
        args.date = [config_value(env, "ZSXQ_DAILY_DATE") or "yesterday"]
    if not args.timezone:
        args.timezone = "Asia/Shanghai"
    if not args.output_dir:
        args.output_dir = str(Path.cwd() / "data")
    if not args.lark_cli:
        args.lark_cli = "lark-cli"
    if not args.lark_as:
        args.lark_as = "user"
    if not args.feishu_receive_id_type:
        args.feishu_receive_id_type = "open_id"


def validate_args(args: argparse.Namespace) -> None:
    if not args.group_id:
        raise RuntimeError("missing --group-id or ZSXQ_DAILY_GROUP_ID")
    if not args.group_name:
        raise RuntimeError("missing --group-name or ZSXQ_DAILY_GROUP_NAME")
    if args.send_to_feishu:
        if not args.feishu_app_id or not args.feishu_app_secret:
            raise RuntimeError("missing Feishu app id/secret for --send-to-feishu")
        if not args.feishu_receive_id:
            raise RuntimeError("missing --feishu-receive-id or ZSXQ_DAILY_FEISHU_RECEIVE_ID for --send-to-feishu")


def today_in_timezone(timezone: str) -> dt.date:
    try:
        return dt.datetime.now(ZoneInfo(timezone)).date()
    except Exception:
        return dt.datetime.now().date()


def normalize_date(value: str, timezone: str = "Asia/Shanghai") -> dt.date:
    value = value.strip()
    lowered = value.lower()
    if lowered in ("yesterday", "\u6628\u5929"):
        return today_in_timezone(timezone) - dt.timedelta(days=1)
    if lowered in ("today", "\u4eca\u5929"):
        return today_in_timezone(timezone)
    if re.fullmatch(r"\d{8}", value):
        return dt.datetime.strptime(value, "%Y%m%d").date()
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


def expand_dates(values: list[str], timezone: str = "Asia/Shanghai") -> list[dt.date]:
    dates: set[dt.date] = set()
    for raw in values:
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if ".." in chunk:
                start_raw, end_raw = chunk.split("..", 1)
                start = normalize_date(start_raw, timezone)
                end = normalize_date(end_raw, timezone)
                if end < start:
                    raise ValueError(f"date range end is before start: {chunk}")
                cur = start
                while cur <= end:
                    dates.add(cur)
                    cur += dt.timedelta(days=1)
            else:
                dates.add(normalize_date(chunk, timezone))
    if not dates:
        raise ValueError("at least one date is required")
    return sorted(dates)


def zsxq_time(day: dt.date) -> str:
    return day.strftime("%Y-%m-%d") + "T00:00:00.000+0800"


def stamp(day: dt.date) -> str:
    return day.strftime("%Y%m%d")


def report_title(group_name: str, day: dt.date) -> str:
    return f"{group_name}{stamp(day)}{REPORT_SUFFIX}"


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()


def strip_html(value: str) -> str:
    if not value:
        return ""
    parser = PlainTextParser()
    parser.feed(html.unescape(value))
    return parser.text()


def topic_time(topic: dict[str, Any]) -> str:
    return (topic.get("create_time", "") or "")[:16].replace("T", " ")


def topic_parts(topic: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    text = ""
    images: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    if topic.get("type") == "q&a":
        for key in ("question", "answer"):
            part = topic.get(key) or {}
            part_text = strip_html(part.get("text", ""))
            if part_text:
                text = (text + "\n\n" + part_text).strip() if text else part_text
            images.extend(part.get("images") or [])
            files.extend(part.get("files") or [])
    else:
        talk = topic.get("talk") or {}
        text = strip_html(talk.get("text", ""))
        images.extend(talk.get("images") or [])
        files.extend(talk.get("files") or [])

    ref = topic.get("referenced_topic") or {}
    ref_topic = ref.get("topic") if isinstance(ref, dict) else None
    if ref_topic:
        ref_text, ref_images, ref_files = topic_parts(ref_topic)
        ref_time = (ref_topic.get("create_time", "") or "")[:16].replace("T", " ")
        ref_block = f"\u5f15\u7528\u539f\u6587 ({ref_time})"
        if ref_text:
            ref_block += "\n" + ref_text
        text = (text + "\n\n" + ref_block).strip() if text else ref_block
        images.extend(ref_images)
        files.extend(ref_files)
    return text, images, files


def render_text_lines(text: str) -> list[str]:
    out: list[str] = []
    for para in (text or "").split("\n\n"):
        para = para.strip()
        if not para:
            continue
        out.extend(para.split("\n"))
        out.append("")
    while out and out[-1] == "":
        out.pop()
    return out


def owner_name(obj: dict[str, Any]) -> str:
    owner = obj.get("owner") or {}
    return str(owner.get("name") or owner.get("nickname") or "")


def repliee_name(obj: dict[str, Any]) -> str:
    repliee = obj.get("repliee") or {}
    return str(repliee.get("name") or repliee.get("nickname") or "")


def md_label(value: str) -> str:
    return value.replace("*", r"\*")


def add_quote_with_media(client: ZsxqClient, lines: list[str], label: str, text: str, images: list[dict[str, Any]]) -> None:
    clean_text = strip_html(text)
    chunks = clean_text.split("\n") if clean_text else [""]
    first = chunks[0] if chunks else ""
    lines.append(f"> **{md_label(label)}** {first}".rstrip())
    for extra in chunks[1:]:
        lines.append(f"> {extra}")
    for idx, img in enumerate(images or [], 1):
        url = client.image_url(img)
        if url:
            lines.append(f"> ![{IMAGE_ALT}{idx}]({url})")


def render_comments(client: ZsxqClient, comments: list[dict[str, Any]]) -> list[str]:
    if not comments:
        return []
    lines = [f"<mark><strong>{COMMENT_LABEL}</strong></mark>", ""]
    for comment in comments:
        add_quote_with_media(client, lines, f"{owner_name(comment)}\uff1a", comment.get("text", ""), comment.get("images") or [])
        for reply in comment.get("replied_comments") or []:
            target = repliee_name(reply)
            if target:
                label = f"{INDENT}{owner_name(reply)}{REPLY_WORD}{target}\uff1a"
            else:
                label = f"{INDENT}{owner_name(reply)}\uff1a"
            add_quote_with_media(client, lines, label, reply.get("text", ""), reply.get("images") or [])
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def render_day_md(client: ZsxqClient, group_name: str, day: dt.date, items: list[dict[str, Any]]) -> str:
    title = report_title(group_name, day)
    lines = [f"# {title}", "", '<div class="intro-box">', INTRO, "</div>", ""]
    for idx, item in enumerate(items):
        if idx > 0:
            lines.extend(["", "---", ""])
        topic = item["topic"]
        comments = item["comments"]
        lines.append(f"## {topic_time(topic)}")
        lines.append("")
        text, images, files = topic_parts(topic)
        body_lines = render_text_lines(text)
        if body_lines:
            lines.extend(body_lines)
            lines.append("")
        for img_idx, img in enumerate(images, 1):
            url = client.image_url(img)
            if url:
                lines.append(f"![{IMAGE_ALT}{img_idx}]({url})")
                lines.append("")
        for file_item in files:
            name = file_item.get("name") or file_item.get("file_name") or ""
            if name:
                lines.append(f"- {name}")
        if files:
            lines.append("")
        comment_lines = render_comments(client, comments)
        if comment_lines:
            lines.extend(comment_lines)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


IMG_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^\s)]+)\)")
BOLD_RE = re.compile(r"\*\*(.*?)\*\*")


def render_text_inline(value: str) -> str:
    escaped = escape(value, quote=False)
    return BOLD_RE.sub(lambda match: "<strong>" + match.group(1) + "</strong>", escaped)


def render_inline(value: str) -> str:
    parts: list[str] = []
    pos = 0
    for match in IMG_RE.finditer(value):
        parts.append(render_text_inline(value[pos : match.start()]))
        alt = escape(match.group(1), quote=True)
        src = escape(match.group(2), quote=True)
        parts.append(f'<img src="{src}" alt="{alt}">')
        pos = match.end()
    parts.append(render_text_inline(value[pos:]))
    return "".join(parts)


def markdown_to_html(md_text: str) -> str:
    html_parts: list[str] = []
    in_intro = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith("# "):
            html_parts.append(f"<h1>{escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            html_parts.append(f"<h2>{escape(line[3:].strip())}</h2>")
        elif stripped == "---":
            html_parts.append('<hr class="post-divider">')
        elif line.startswith('<div class="intro-box">'):
            html_parts.append(line)
            in_intro = True
        elif in_intro:
            if stripped == "</div>":
                html_parts.append(line)
                in_intro = False
            else:
                html_parts.append(escape(line))
        elif line.startswith("<mark"):
            html_parts.append(f'<p class="comment-heading">{line}</p>')
        elif line.startswith(">"):
            content = line[1:]
            if content.startswith(" "):
                content = content[1:]
            cls = ' class="image-quote"' if IMG_RE.search(content) else ""
            html_parts.append(f"<blockquote{cls}>{render_inline(content)}</blockquote>")
        elif line.startswith("- "):
            html_parts.append(f'<p class="attachment">{render_inline(line[2:])}</p>')
        elif IMG_RE.fullmatch(line.strip()):
            html_parts.append(f'<p class="image-line">{render_inline(line.strip())}</p>')
        else:
            html_parts.append(f"<p>{render_inline(line)}</p>")
    return '<!doctype html><html><head><meta charset="utf-8"><style>' + CSS + "</style></head><body>" + "\n".join(html_parts) + "</body></html>"


def write_pdf(md_text: str, pdf_path: Path) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Python playwright is required. Install it and run: playwright install chromium") from exc

    html_doc = markdown_to_html(md_text)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 900, "height": 1200}, device_scale_factor=1)
        page.set_content(html_doc, wait_until="networkidle", timeout=180000)
        page.wait_for_timeout(2500)
        stats = page.evaluate(
            """() => {
              const imgs = Array.from(document.images);
              const h2WithBorder = Array.from(document.querySelectorAll('h2')).filter(h => getComputedStyle(h).borderBottomWidth !== '0px').length;
              const pageBreakish = Array.from(document.querySelectorAll('*')).filter(el => {
                const cs = getComputedStyle(el);
                return cs.breakAfter === 'page' || cs.pageBreakAfter === 'always';
              }).length;
              return {
                imagesTotal: imgs.length,
                imagesLoaded: imgs.filter(img => img.complete && img.naturalWidth > 0).length,
                h2WithBorder,
                pageBreakish,
                dividers: document.querySelectorAll('hr.post-divider').length
              };
            }"""
        )
        if stats["imagesLoaded"] < stats["imagesTotal"]:
            page.wait_for_timeout(5000)
            stats = page.evaluate(
                """() => {
                  const imgs = Array.from(document.images);
                  const h2WithBorder = Array.from(document.querySelectorAll('h2')).filter(h => getComputedStyle(h).borderBottomWidth !== '0px').length;
                  const pageBreakish = Array.from(document.querySelectorAll('*')).filter(el => {
                    const cs = getComputedStyle(el);
                    return cs.breakAfter === 'page' || cs.pageBreakAfter === 'always';
                  }).length;
                  return {
                    imagesTotal: imgs.length,
                    imagesLoaded: imgs.filter(img => img.complete && img.naturalWidth > 0).length,
                    h2WithBorder,
                    pageBreakish,
                    dividers: document.querySelectorAll('hr.post-divider').length
                  };
                }"""
            )
        if stats["imagesLoaded"] < stats["imagesTotal"]:
            browser.close()
            raise RuntimeError(f"image load failed for {pdf_path.name}: {stats}")
        page.pdf(path=str(pdf_path), format="A4", print_background=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        browser.close()
    return stats


def resolve_command(command: str) -> list[str]:
    path = shutil.which(command)
    if not path:
        for suffix in (".cmd", ".bat", ".exe", ".ps1"):
            path = shutil.which(command + suffix)
            if path:
                break
    if not path:
        candidate = Path(command)
        if candidate.exists():
            path = str(candidate)
    if not path:
        raise RuntimeError(f"command not found: {command}")
    if path.lower().endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    return [path]


def run_lark(lark_cli: str, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd = resolve_command(lark_cli) + args
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError("lark-cli failed:\nSTDOUT:\n%s\nSTDERR:\n%s" % (result.stdout, result.stderr))
    return result


def http_post_bytes(url: str, body: bytes, headers: dict[str, str], timeout: int = 60) -> tuple[bytes, int]:
    req = urllib.request.Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            return resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        return exc.read(), exc.code


def parse_feishu_result(response: bytes, status: int) -> dict[str, Any]:
    text = response.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    code = data.get("code")
    ok = 200 <= status < 300 and code in (0, None)
    return {
        "ok": ok,
        "status": status,
        "data": data.get("data") or {},
        "json": data,
        "error": "" if ok else f"Feishu API failed: status={status}; code={code}; msg={data.get('msg') or data.get('message')}; body={text[:500]}",
    }


def http_post_json(url: str, payload: dict[str, Any], tenant_token: str | None = None, timeout: int = 60) -> dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if tenant_token:
        headers["Authorization"] = f"Bearer {tenant_token}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response, status = http_post_bytes(url, body, headers, timeout)
    return parse_feishu_result(response, status)


def multipart_body(fields: dict[str, str], files: dict[str, tuple[str, str, bytes]]) -> tuple[str, bytes]:
    boundary = "----ZSXQDaily" + os.urandom(8).hex()
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for name, (filename, content_type, data) in files.items():
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


def feishu_uuid(value: str) -> str:
    if len(value) <= 50:
        return value
    import hashlib

    return value[:33] + "-" + hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def get_feishu_tenant_token(app_id: str, app_secret: str) -> str:
    if not app_id or not app_secret:
        raise RuntimeError("missing Feishu app id/secret for sending files")
    result = http_post_json(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    )
    if not result["ok"]:
        raise RuntimeError(result["error"])
    token = result["data"].get("tenant_access_token") or result["json"].get("tenant_access_token")
    if not token:
        raise RuntimeError("Feishu tenant_access_token missing in response")
    return str(token)


def feishu_upload_message_file(path: Path, tenant_token: str) -> str:
    data = path.read_bytes()
    import mimetypes

    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    boundary, body = multipart_body(
        {"file_type": "stream", "file_name": path.name},
        {"file": (path.name, content_type, data)},
    )
    response, status = http_post_bytes(
        f"{FEISHU_API_BASE}/im/v1/files",
        body,
        {
            "Authorization": f"Bearer {tenant_token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        timeout=120,
    )
    result = parse_feishu_result(response, status)
    if not result["ok"]:
        raise RuntimeError(result["error"])
    file_key = result["data"].get("file_key")
    if not file_key:
        raise RuntimeError("Feishu file_key missing in response")
    return str(file_key)


def feishu_send_message(
    tenant_token: str,
    receive_id_type: str,
    receive_id: str,
    msg_type: str,
    content: dict[str, Any],
    uuid: str,
) -> dict[str, Any]:
    payload = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
        "uuid": feishu_uuid(uuid),
    }
    result = http_post_json(
        f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type={urllib.parse.quote(receive_id_type)}",
        payload,
        tenant_token=tenant_token,
        timeout=60,
    )
    if not result["ok"]:
        raise RuntimeError(result["error"])
    return result


def send_outputs_to_feishu_account(args: argparse.Namespace, outputs: list[DayOutput]) -> None:
    if not args.send_to_feishu:
        return
    if not args.feishu_receive_id:
        raise RuntimeError("missing --feishu-receive-id for --send-to-feishu")
    tenant_token = get_feishu_tenant_token(args.feishu_app_id, args.feishu_app_secret)
    for item in outputs:
        text = (
            f"{item.title}\n"
            f"帖子：{item.topic_count} 条\n"
            f"评论：{item.comment_count} 条\n"
            f"文件：Markdown 和 PDF 已生成，下面发送。"
        )
        feishu_send_message(
            tenant_token,
            args.feishu_receive_id_type,
            args.feishu_receive_id,
            "text",
            {"text": text},
            f"zsxq-daily-text-{stamp(item.date)}",
        )
        for suffix, path in (("md", item.md_path), ("pdf", item.pdf_path)):
            file_key = feishu_upload_message_file(path, tenant_token)
            feishu_send_message(
                tenant_token,
                args.feishu_receive_id_type,
                args.feishu_receive_id,
                "file",
                {"file_key": file_key, "file_name": path.name},
                f"zsxq-daily-file-{suffix}-{stamp(item.date)}",
            )


def extract_first_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start < 0:
        return {}
    decoder = json.JSONDecoder()
    try:
        obj, _end = decoder.raw_decode(text[start:])
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def upload_outputs(args: argparse.Namespace, outputs: list[DayOutput]) -> None:
    if args.no_upload or not args.feishu_doc:
        return
    by_date = {item.date: item for item in outputs}
    upload_order = sorted(by_date, reverse=True)
    anchor_date = normalize_date(args.insert_before_date) if args.insert_before_date else None

    for day in upload_order:
        item = by_date[day]
        file_arg = ".\\" + item.pdf_path.name if os.name == "nt" else "./" + item.pdf_path.name
        cmd_args = [
            "docs",
            "+media-insert",
            "--as",
            args.lark_as,
            "--doc",
            args.feishu_doc,
            "--file",
            file_arg,
            "--type",
            "file",
            "--file-view",
            "card",
            "--format",
            "json",
        ]
        if anchor_date:
            anchor_name = safe_filename(report_title(args.group_name, anchor_date) + ".pdf")
            cmd_args.extend(["--selection-with-ellipsis", anchor_name, "--before"])
        result = run_lark(args.lark_cli, cmd_args, item.pdf_path.parent)
        payload = extract_first_json(result.stdout)
        item.uploaded = True
        item.file_token = str(payload.get("data", {}).get("file_token", ""))
        anchor_date = day

    verify_feishu_order(args, outputs)


def verify_feishu_order(args: argparse.Namespace, outputs: list[DayOutput]) -> None:
    expected_sizes = [str(item.pdf_path.stat().st_size) for item in sorted(outputs, key=lambda item: item.date)]
    result = run_lark(
        args.lark_cli,
        [
            "docs",
            "+fetch",
            "--api-version",
            "v2",
            "--as",
            args.lark_as,
            "--doc",
            args.feishu_doc,
            "--detail",
            "with-ids",
            "--format",
            "json",
            "--jq",
            ".data.document.content",
        ],
        Path.cwd(),
    )
    actual_sizes = re.findall(r'mime="application/pdf" size="(\d+)"', result.stdout)
    expected_joined = ",".join(expected_sizes)
    actual_joined = ",".join(actual_sizes)
    if expected_joined not in actual_joined:
        raise RuntimeError(f"Feishu order verification failed: expected contiguous sizes {expected_joined}, got {actual_joined}")


def build_outputs(args: argparse.Namespace, client: ZsxqClient, dates: list[dt.date]) -> list[DayOutput]:
    start = zsxq_time(min(dates))
    end = zsxq_time(max(dates) + dt.timedelta(days=1))
    topics = client.fetch_topics_range(args.group_id, start, end)
    day_map: dict[dt.date, list[dict[str, Any]]] = {day: [] for day in dates}
    for topic in topics:
        create_date = normalize_date((topic.get("create_time", "") or "")[:10], args.timezone)
        if create_date in day_map:
            comments = client.fetch_comments(topic, args.comment_count, args.max_comment_pages)
            day_map[create_date].append({"topic": topic, "comments": comments})
            comment_total = len(comments) + sum(len(c.get("replied_comments") or []) for c in comments)
            print(f"fetched {create_date.isoformat()} {topic_time(topic)} {topic.get('topic_uid') or topic.get('topic_id')} comments={comment_total}")

    outputs: list[DayOutput] = []
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for day in dates:
        items = sorted(day_map[day], key=lambda item: item["topic"].get("create_time", ""), reverse=True)
        title = report_title(args.group_name, day)
        md_path = output_dir / safe_filename(title + ".md")
        pdf_path = output_dir / safe_filename(title + ".pdf")
        md_text = render_day_md(client, args.group_name, day, items)
        md_path.write_text(md_text, encoding="utf-8")
        pdf_stats = write_pdf(md_text, pdf_path)
        comment_count = sum(len(item["comments"]) + sum(len(c.get("replied_comments") or []) for c in item["comments"]) for item in items)
        image_count = sum(1 for line in md_text.splitlines() if "![图片" in line or "![\u56fe\u7247" in line)
        outputs.append(DayOutput(day, title, md_path, pdf_path, len(items), comment_count, image_count, pdf_stats))
        print(f"rendered {day.isoformat()} topics={len(items)} comments={comment_count} images={image_count} pdf={pdf_path}")
    return outputs


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ZSXQ daily Markdown/PDF reports and optionally upload PDFs to Feishu.")
    parser.add_argument("--date", action="append", default=None, help="Date, comma list, range, or yesterday. Examples: 2026-06-01, 20260601, yesterday, 2026-06-01..2026-06-03")
    parser.add_argument("--timezone", default="", help="Timezone for today/yesterday")
    parser.add_argument("--group-id", default="", help="ZSXQ group id")
    parser.add_argument("--group-name", default="", help="Display name and file title prefix")
    parser.add_argument("--output-dir", default="", help="Output directory for .md and .pdf files")
    parser.add_argument("--feishu-doc", default="", help="Optional Feishu docx URL to upload PDFs into")
    parser.add_argument("--insert-before-date", default="", help="Optional existing later date to insert before, e.g. 2026-06-04")
    parser.add_argument("--no-upload", action="store_true", help="Generate local files only")
    parser.add_argument("--env-file", default=os.environ.get("ZSXQ_DAILY_ENV_FILE", str(Path.home() / ".hermes" / ".env")), help="Env file containing ZSXQ_ACCESS_TOKEN")
    parser.add_argument("--token-env", default="ZSXQ_ACCESS_TOKEN", help="Environment variable name for the ZSXQ token")
    parser.add_argument("--request-gap", type=float, default=1.1, help="Minimum seconds between ZSXQ API requests")
    parser.add_argument("--comment-count", type=int, default=30, help="Comments page size; 30 is known to work")
    parser.add_argument("--max-comment-pages", type=int, default=20, help="Maximum comment pages per topic")
    parser.add_argument("--lark-cli", default="", help="lark-cli executable")
    parser.add_argument("--lark-as", default="", choices=("user", "bot"), help="lark-cli identity for document upload")
    parser.add_argument("--send-to-feishu", action="store_true", help="Send generated Markdown/PDF files to a Feishu account/chat via OpenAPI")
    parser.add_argument("--feishu-app-id", default="", help="Feishu app id for sending files")
    parser.add_argument("--feishu-app-secret", default="", help="Feishu app secret for sending files")
    parser.add_argument("--feishu-receive-id", default="", help="Feishu receiver id, such as user open_id or chat_id")
    parser.add_argument("--feishu-receive-id-type", default="", help="Feishu receive_id_type: open_id, user_id, union_id, email, or chat_id")
    args = parser.parse_args(argv)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    env = load_env_file(Path(args.env_file).expanduser())
    apply_env_defaults(args, env)
    validate_args(args)
    dates = expand_dates(args.date, args.timezone)
    token = os.environ.get(args.token_env) or env.get(args.token_env)
    if not token:
        raise RuntimeError(f"missing {args.token_env}; set it in environment or {args.env_file}")
    client = ZsxqClient(token, args.request_gap)
    outputs = build_outputs(args, client, dates)
    send_outputs_to_feishu_account(args, outputs)
    upload_outputs(args, outputs)
    summary = {
        "dates": [item.date.isoformat() for item in outputs],
        "outputs": [
            {
                "date": item.date.isoformat(),
                "title": item.title,
                "markdown": str(item.md_path),
                "pdf": str(item.pdf_path),
                "topics": item.topic_count,
                "comments": item.comment_count,
                "images": item.image_count,
                "pdf_stats": item.pdf_stats,
                "uploaded": item.uploaded,
                "file_token": item.file_token,
            }
            for item in outputs
        ],
        "uploaded_to": args.feishu_doc if args.feishu_doc and not args.no_upload else "",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
