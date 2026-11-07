"""Telegram 推播給維護者（票卷不足／該加值）。

只在出票事件時打一次 HTTPS POST，沒有常駐連線或輪詢。
TG_BOT_TOKEN／TG_CHAT_ID 沒設就直接略過；送失敗只印 log，不影響出票。
"""
import asyncio
import json
import os
import urllib.request


def _send_sync(text: str) -> None:
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        return
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:
        print(f"TG 通知失敗: {e}", flush=True)


async def notify_tg(text: str) -> None:
    await asyncio.to_thread(_send_sync, text)
