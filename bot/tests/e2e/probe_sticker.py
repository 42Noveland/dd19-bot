"""活体探针：验证表情包回应（send_sticker 工具 + 贴图库）。

流程：注入 "@bot 来张'得意'的表情包" → 期待机器人发出 image 段（图库里匹配到的图），
随后还会有正常的文字回复。两步都捕获。

用法：python tests/e2e/probe_sticker.py
"""
import asyncio
import json
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10004  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()


def ev(segs: list, mid: int, uid: int, nickname: str) -> dict:
    return {
        "time": 1790971000, "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": mid,
        "group_id": GROUP, "user_id": uid, "message": segs,
        "raw_message": "", "font": 0,
        "sender": {"user_id": uid, "nickname": nickname, "card": nickname, "role": "member"},
    }


async def main() -> None:
    base = int(time.time()) % 10**6 * 10
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(
            json.dumps(
                ev(
                    [
                        {"type": "at", "data": {"qq": str(SELF_ID)}},
                        {"type": "text", "data": {"text": "来张表情包，表达一下\"得意\"的感觉"}},
                    ],
                    base + 1,
                    29921,
                    "小明",
                )
            )
        )
        got_image, got_text = None, None
        deadline = time.time() + 220
        while time.time() < deadline and (got_image is None or got_text is None):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            d = json.loads(raw)
            action = d.get("action")
            echo = d.get("echo")
            if action == "send_msg":
                msg = d.get("params", {}).get("message") or []
                if not isinstance(msg, list):
                    msg = []
                kinds = [s.get("type") for s in msg if isinstance(s, dict)]
                text = "".join(
                    str(s.get("data", {}).get("text", "")) for s in msg if isinstance(s, dict) and s.get("type") == "text"
                )
                if "image" in kinds and got_image is None:
                    file = next(
                        (str(s.get("data", {}).get("file")) for s in msg if s.get("type") == "image"), ""
                    )
                    got_image = file
                    print("[贴图] 收到图片发送 ->", file[:110])
                if text and got_text is None:
                    got_text = text
                    print("[文字] ->", text[:150])
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
            elif action:
                if action == "get_login_info":
                    data = {"user_id": SELF_ID, "nickname": "dd19"}
                elif action == "get_group_member_info":
                    uid = d.get("params", {}).get("user_id")
                    data = {"user_id": uid, "nickname": f"用户{uid}", "card": f"用户{uid}"}
                else:
                    data = {}
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))
        print("RESULT: 发出表情包 =", got_image is not None, "| 文字回复 =", (got_text or "")[:80])
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
