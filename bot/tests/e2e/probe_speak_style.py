"""一次性活体验证：新 API key + 新版「说话规则」下的聊天回复抽样。

从本连接注入测试消息（假 self_id），回复只会回到本连接、不会发到真实群。
用途：key 更换后链路复验 + 第一眼观察反 AI 味规则的输出形态。
"""
import asyncio
import json
import sys
import time
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

# 场景：覆盖 闲聊 / 知识问答（历史最容易触发长篇+分段的场景）
CASES = [
    ("生活建议", 29911, "周末想去爬山，有啥推荐吗"),
    ("知识问答", 29912, "怪物猎人世界的大师等级怎么提升啊"),
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


async def collect_reply(ws, timeout: float = 180.0, tail: float = 6.0):
    """等第一条 send_msg，随后 tail 秒内收集后续消息（贴图/补话大多是连发的）。

    返回 [(text, has_media)]。避免"表情包先到"被误读成上一 case 的回复。
    """
    msgs: list[tuple[str, bool]] = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = (deadline - time.monotonic()) if not msgs else tail
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
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
            msgs.append((text, media))
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
        elif action:
            if action == "get_login_info":
                data = {"user_id": SELF_ID, "nickname": "dd19"}
            elif action == "get_group_member_info":
                data = {"user_id": d.get("params", {}).get("user_id"), "nickname": "probe", "card": ""}
            else:
                data = {}
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))
    return msgs


async def main() -> None:
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        for i, (name, uid, text) in enumerate(CASES):
            mid = 8810 + i
            await ws.send(json.dumps(ev(mid, uid, text)))
            msgs = await collect_reply(ws)
            print(f"[{name}] {text}")
            if not msgs:
                print("  -> TIMEOUT")
            for text_out, media in msgs:
                nl = text_out.count("\n")
                tag = "｜含换行!" if nl else ""
                if text_out.strip():
                    print(f"  -> ({len(text_out)}字{tag}) {text_out[:260]}")
                if media:
                    print("  -> [图片/表情]")
            print("---")
            if i < len(CASES) - 1:
                await asyncio.sleep(3)  # 与下一条拉开一点，减少聚批干扰
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
