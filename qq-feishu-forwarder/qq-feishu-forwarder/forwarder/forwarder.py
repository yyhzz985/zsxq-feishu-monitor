"""
QQ群消息 → 飞书群 转发脚本
支持：文本、图片、语音、文件、@消息、表情、合并转发
"""

import asyncio
import json
import os
import logging
import hashlib
import hmac
import base64
import time
import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

# ──────────────────────────────────────────────
#  配置（从环境变量读取，也可直接在这里填写）
# ──────────────────────────────────────────────
NAPCAT_WS      = os.getenv("NAPCAT_WS",      "ws://napcat:3001")
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
TARGET_GROUP   = int(os.getenv("TARGET_GROUP_ID", "0"))   # 要监控的QQ群号
FEISHU_SECRET  = os.getenv("FEISHU_SECRET",  "")          # 飞书签名密钥，没开启留空

RECONNECT_DELAY = 10   # 断线后等待N秒重连

# ──────────────────────────────────────────────
#  日志配置
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("forwarder")


# ──────────────────────────────────────────────
#  飞书签名生成（安全设置里开启了签名才需要）
# ──────────────────────────────────────────────
def feishu_sign(secret: str, timestamp: int) -> str:
    content = f"{timestamp}\n{secret}".encode("utf-8")
    return base64.b64encode(
        hmac.new(content, digestmod=hashlib.sha256).digest()
    ).decode("utf-8")


# ──────────────────────────────────────────────
#  发送飞书消息（纯文本卡片格式）
# ──────────────────────────────────────────────
async def send_to_feishu(session: aiohttp.ClientSession, text: str):
    if not FEISHU_WEBHOOK:
        log.warning("FEISHU_WEBHOOK 未配置，跳过发送")
        return

    payload: dict = {"msg_type": "text", "content": {"text": text}}

    if FEISHU_SECRET:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = feishu_sign(FEISHU_SECRET, ts)

    try:
        async with session.post(FEISHU_WEBHOOK, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            result = await resp.json()
            if result.get("code") != 0:
                log.error(f"飞书发送失败: {result}")
            else:
                log.info(f"飞书发送成功: {text[:40]}...")
    except Exception as e:
        log.error(f"飞书请求异常: {e}")


# ──────────────────────────────────────────────
#  解析 OneBot 消息段 → 可读文本
# ──────────────────────────────────────────────
def parse_message_segments(segments: list) -> str:
    """把 OneBot 消息段列表解析成可读字符串"""
    parts = []
    for seg in segments:
        t = seg.get("type", "")
        d = seg.get("data", {})

        if t == "text":
            parts.append(d.get("text", ""))

        elif t == "image":
            # 图片：尽量附上文件名或URL
            name = d.get("file", "图片")
            url  = d.get("url", "")
            if url:
                parts.append(f"[图片] {url}")
            else:
                parts.append(f"[图片: {name}]")

        elif t == "record":
            # 语音
            parts.append("[语音消息]")

        elif t == "video":
            parts.append("[视频消息]")

        elif t == "file":
            name = d.get("file", "未知文件")
            parts.append(f"[文件: {name}]")

        elif t == "at":
            qq   = d.get("qq", "")
            name = d.get("name", "")
            tag  = name if name else f"@{qq}"
            parts.append(tag)

        elif t == "face":
            parts.append("[表情]")

        elif t == "forward":
            parts.append("[合并转发消息]")

        elif t == "json":
            # 小程序/卡片消息，尝试解析标题
            try:
                data = json.loads(d.get("data", "{}"))
                meta = data.get("meta", {})
                # 兼容多种小程序格式
                title = (
                    meta.get("detail_1", {}).get("title")
                    or meta.get("news", {}).get("title")
                    or "[卡片消息]"
                )
                parts.append(f"[小程序/卡片: {title}]")
            except Exception:
                parts.append("[卡片消息]")

        elif t == "xml":
            parts.append("[XML消息]")

        else:
            parts.append(f"[{t}]")

    return "".join(parts)


# ──────────────────────────────────────────────
#  处理单条群消息事件
# ──────────────────────────────────────────────
async def handle_group_message(event: dict, session: aiohttp.ClientSession):
    group_id  = event.get("group_id", 0)
    sender    = event.get("sender", {})
    nickname  = sender.get("card") or sender.get("nickname", "未知")  # 优先群名片
    raw_msg   = event.get("message", [])

    # 兼容字符串格式（极少数情况）
    if isinstance(raw_msg, str):
        content = raw_msg
    else:
        content = parse_message_segments(raw_msg)

    if not content.strip():
        return  # 空消息不转发

    forward_text = f"【QQ群消息】\n发送人：{nickname}\n内容：{content}"
    log.info(f"群{group_id} | {nickname}: {content[:60]}")
    await send_to_feishu(session, forward_text)


# ──────────────────────────────────────────────
#  WebSocket 主循环（含断线重连）
# ──────────────────────────────────────────────
async def main_loop():
    log.info(f"监控群号: {TARGET_GROUP}")
    log.info(f"NapCat WS: {NAPCAT_WS}")
    log.info(f"飞书 Webhook: {FEISHU_WEBHOOK[:40]}..." if FEISHU_WEBHOOK else "飞书 Webhook: 未配置")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                log.info("正在连接 NapCat WebSocket...")
                async with websockets.connect(
                    NAPCAT_WS,
                    ping_interval=30,   # 每30秒发一次心跳，防止连接断开
                    ping_timeout=10,
                ) as ws:
                    log.info("✅ 已连接 NapCat，开始监听群消息...")

                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # 只处理群消息事件
                        if (
                            event.get("post_type") == "message"
                            and event.get("message_type") == "group"
                            and event.get("group_id") == TARGET_GROUP
                        ):
                            await handle_group_message(event, session)

            except ConnectionClosed as e:
                log.warning(f"WebSocket 断开: {e}，{RECONNECT_DELAY}秒后重连...")
            except OSError as e:
                log.warning(f"无法连接 NapCat ({e})，{RECONNECT_DELAY}秒后重试...")
            except Exception as e:
                log.error(f"未知错误: {e}，{RECONNECT_DELAY}秒后重连...")

            await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    asyncio.run(main_loop())
