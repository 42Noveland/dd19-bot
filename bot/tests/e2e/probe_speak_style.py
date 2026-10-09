"""一次性活体验证：新 API key + 新版「说话规则」下的聊天回复抽样。

从本连接注入测试消息（假 self_id），回复只会回到本连接、不会发到真实群。
用途：key 更换后链路复验 + 第一眼观察反 AI 味规则的输出形态。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10003  # 假 id：适配器拒绝重复 X-Self-ID
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()

CASES = [
    ("闲聊", "今天加班到九点，好累"),
    ("求助", "帮我推荐几本书呗"),
]


def ev(mid: int, uid: int, text: str) -> dict:
    return {
        "time": 1790966000, "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": mid,
        "group_id": GROUP, "user_id": uid,
        "message": [{"type": "at", "data": {"qq": str(SELF_ID)}}, {"type": "text", "data": {"text": " " + text}}],
        "raw_message": "", "font": 0,
        "sender": {"user_id": uid, "nickname": "probe", "card": "", "role": "member"},
    }


async def wait_reply(ws, timeout: float = 180.0):
    """等一条 send_msg，回 ack，返回 (文本, 是否含图片/表情)。"""
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        d = json.loads(raw)
        action = d.get("action")
        echo = d.get("echo")
        if action == "send_msg":
            msg = d.get("params", {}).get("message")
            text, media = "", False
            if isinstance(msg, list):
                for s in msg:
                    t = s.get("type")
                    if t == "text":
                        text += str(s.get("data", {}).get("text", ""))
                    elif t in ("image", "face", "mface"):
                        media = True
            else:
                text = str(msg)
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
            return text, media
        if action:
            if action == "get_login_info":
                data = {"user_id": SELF_ID, "nickname": "dd19"}
            elif action == "get_group_member_info":
                data = {"user_id": d.get("params", {}).get("user_id"), "nickname": "probe", "card": ""}
            else:
                data = {}
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))


async def main() -> None:
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        for i, (name, text) in enumerate(CASES):
            mid = 8800 + i
            await ws.send(json.dumps(ev(mid, 29880 + i, text)))
            try:
                reply, media = await wait_reply(ws)
                tag = " [+图]" if media else ""
                print(f"[{name}] {text}\n  -> {reply[:300]}{tag}\n---")
            except asyncio.TimeoutError:
                print(f"[{name}] {text}\n  -> TIMEOUT\n---")
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
