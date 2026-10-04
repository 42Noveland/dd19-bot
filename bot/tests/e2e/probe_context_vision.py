"""活体探针：验证感知层（群上下文记忆 + 图片识别）。

Part A：先发一条普通消息（应被观察者记录），再 @bot 提问 → bot 应能"记得"（回答 73）。
Part B：注入一条图片消息（URL 指向本地 HTTP 服务），等异步 caption 完成，再 @bot 问图
        → bot 应能答出图中内容（苹果数量 = 42）。

用法：
    python -m http.server 8099 --directory D:\\agent-workspace\\llmtest\\dl   # 另起
    python tests/e2e/probe_context_vision.py http://127.0.0.1:8099/vision_test.png
"""
import asyncio
import json
import sys
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10003  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()


def ev(segs: list, mid: int, uid: int, nickname: str) -> dict:
    return {
        "time": 1790969000, "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": mid,
        "group_id": GROUP, "user_id": uid, "message": segs,
        "raw_message": "", "font": 0,
        "sender": {"user_id": uid, "nickname": nickname, "card": nickname, "role": "member"},
    }


def at_bot() -> dict:
    return {"type": "at", "data": {"qq": str(SELF_ID)}}


def txt(text: str) -> dict:
    return {"type": "text", "data": {"text": text}}


async def wait_reply(ws, timeout: float = 200.0) -> str:
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        d = json.loads(raw)
        action = d.get("action")
        echo = d.get("echo")
        if action == "send_msg":
            msg = d.get("params", {}).get("message")
            if isinstance(msg, list):
                text = "".join(str(s.get("data", {}).get("text", "")) for s in msg if s.get("type") == "text")
            else:
                text = str(msg)
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
            return text
        if action:
            if action == "get_login_info":
                data = {"user_id": SELF_ID, "nickname": "dd19"}
            elif action == "get_group_member_info":
                uid = d.get("params", {}).get("user_id")
                data = {"user_id": uid, "nickname": f"用户{uid}", "card": f"用户{uid}"}
            else:
                data = {}
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))


async def wait_reply_containing(ws, needle: str, timeout: float = 220.0) -> str:
    """等待包含指定关键词的回复（跳过不相干的主动接话等消息）。"""
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return ""
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return ""
        d = json.loads(raw)
        action = d.get("action")
        echo = d.get("echo")
        if action == "send_msg":
            msg = d.get("params", {}).get("message")
            if isinstance(msg, list):
                text = "".join(str(s.get("data", {}).get("text", "")) for s in msg if s.get("type") == "text")
            else:
                text = str(msg)
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
            if needle in text:
                return text
            continue
        if action:
            if action == "get_login_info":
                data = {"user_id": SELF_ID, "nickname": "dd19"}
            elif action == "get_group_member_info":
                uid = d.get("params", {}).get("user_id")
                data = {"user_id": uid, "nickname": f"用户{uid}", "card": f"用户{uid}"}
            else:
                data = {}
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))


async def main() -> None:
    img_url = sys.argv[1] if len(sys.argv) > 1 else ""
    base = int(time.time()) % 10**6 * 10  # 时间戳派生消息 id，避免重跑时被去重
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        # ---- Part A：群上下文记忆（提问者与陈述者应为同一人）----
        await ws.send(json.dumps(ev([txt("我最喜欢的数字是73")], base + 1, 29911, "小明")))
        await asyncio.sleep(2)
        await ws.send(json.dumps(ev([at_bot(), txt("我最喜欢的数字是几？")], base + 2, 29911, "小明")))
        r1 = await wait_reply_containing(ws, "73")
        print("[A 上下文记忆] ->", r1[:220])
        print("  >> 期望包含 73 :", "73" in r1)
        print("---")
        # ---- Part B：图片识别 ----
        if img_url:
            await ws.send(json.dumps(ev([{"type": "image", "data": {"url": img_url, "file": "vision_test.png", "summary": ""}}], base + 3, 29913, "小刚")))
            await asyncio.sleep(12)  # 等异步 caption（下载 + 云 vision）
            await ws.send(json.dumps(ev([at_bot(), txt("刚才那张图里，苹果数量是多少？")], base + 4, 29914, "小丽")))
            r2 = await wait_reply_containing(ws, "42")
            print("[B 图片识别] ->", r2[:220])
            print("  >> 期望包含 42 :", "42" in r2)
        else:
            print("[B 图片识别] 未提供图片 URL，跳过")
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
