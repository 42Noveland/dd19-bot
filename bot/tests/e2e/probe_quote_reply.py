"""活体探针：验证引用回复（聊天回复应带 reply 段，引用触发消息的 message_id）。

用法：python tests/e2e/probe_quote_reply.py
"""
import asyncio
import json
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

SELF_ID = 10006  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = 111111111


def ev(segs: list, mid: int, uid: int, nickname: str) -> dict:
    return {
        "time": 1790971000, "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": mid,
        "group_id": GROUP, "user_id": uid, "message": segs,
        "raw_message": "", "font": 0,
        "sender": {"user_id": uid, "nickname": nickname, "card": nickname, "role": "member"},
    }


async def main() -> None:
    mid = int(time.time()) % 10**6 * 10 + 7
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(
            json.dumps(
                ev(
                    [
                        {"type": "at", "data": {"qq": str(SELF_ID)}},
                        {"type": "text", "data": {"text": "你好呀，随便说句话测一下"}},
                    ],
                    mid,
                    29941,
                    "小明",
                )
            )
        )
        reply_id, text = None, None
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            d = json.loads(raw)
            action = d.get("action")
            echo = d.get("echo")
            if action == "send_msg":
                m = d.get("params", {}).get("message") or []
                if not isinstance(m, list):
                    m = []
                reply_seg = next((x for x in m if isinstance(x, dict) and x.get("type") == "reply"), None)
                body = "".join(
                    str(x.get("data", {}).get("text", "")) for x in m if isinstance(x, dict) and x.get("type") == "text"
                )
                if reply_seg is not None or body:
                    reply_id = (reply_seg or {}).get("data", {}).get("id")
                    text = body
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
                if text:
                    break
            elif action:
                if action == "get_login_info":
                    data = {"user_id": SELF_ID, "nickname": "dd19"}
                elif action == "get_group_member_info":
                    uid = d.get("params", {}).get("user_id")
                    data = {"user_id": uid, "nickname": f"用户{uid}", "card": f"用户{uid}"}
                else:
                    data = {}
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))
        print("RESULT: reply段 =", repr(reply_id), "| 期望 =", str(mid), "| 匹配 =", reply_id == str(mid))
        print("        文本 =", (text or "")[:100])
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
