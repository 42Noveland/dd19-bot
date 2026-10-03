"""活体探针：自然配图（不明确要图，只给情绪语境，观察模型是否主动配表情包）。

用法：python tests/e2e/probe_sticker_natural.py ["唉今天也太无语了，感觉整个人都裂开了"]
说明：这是"统计性"探针——模型有权判断本轮不配图；结果如实打印（配了/没配 + 文字）。
"""
import asyncio
import json
import sys
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

SELF_ID = 10005  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = 111111111
DEFAULT_MSG = "唉今天也太无语了，感觉整个人都裂开了"


def ev(segs: list, mid: int, uid: int, nickname: str) -> dict:
    return {
        "time": 1790971000, "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": mid,
        "group_id": GROUP, "user_id": uid, "message": segs,
        "raw_message": "", "font": 0,
        "sender": {"user_id": uid, "nickname": nickname, "card": nickname, "role": "member"},
    }


async def main() -> None:
    msg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MSG
    base = int(time.time()) % 10**6 * 10
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(
            json.dumps(
                ev(
                    [
                        {"type": "at", "data": {"qq": str(SELF_ID)}},
                        {"type": "text", "data": {"text": msg}},
                    ],
                    base + 1,
                    29931,
                    "小刚",
                )
            )
        )
        got_image, got_text = None, None
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
                kinds = [x.get("type") for x in m if isinstance(x, dict)]
                text = "".join(
                    str(x.get("data", {}).get("text", "")) for x in m if isinstance(x, dict) and x.get("type") == "text"
                )
                if "image" in kinds and got_image is None:
                    got_image = next((str(x.get("data", {}).get("file")) for x in m if x.get("type") == "image"), "")
                    print("[配图] 模型主动发出表情包 ->", got_image[:110])
                if text and got_text is None:
                    got_text = text
                    print("[文字] ->", got_text[:150])
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
                if got_text is not None and got_image is not None:
                    break
                if got_text is not None:
                    # 文字已出，再等 20s 看有没有后随的图
                    if time.time() > deadline - 160:
                        deadline = min(deadline, time.time() + 20)
            elif action:
                if action == "get_login_info":
                    data = {"user_id": SELF_ID, "nickname": "dd19"}
                elif action == "get_group_member_info":
                    uid = d.get("params", {}).get("user_id")
                    data = {"user_id": uid, "nickname": f"用户{uid}", "card": f"用户{uid}"}
                else:
                    data = {}
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))
        print("RESULT: 主动配图 =", got_image is not None, "| 文字 =", (got_text or "")[:90])
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
