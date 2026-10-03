"""活体探针：情绪状态（设置 → 注入）。

流程：① 管理员发 /mood set 得意 0.9 刚被群友夸了；② 另一人 @bot 问"你现在心情怎么样？"
      → 期望回答带出"得意"（状态块被注入且被问到时会说）。
用法：python tests/e2e/probe_mood.py
"""
import asyncio
import json
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

SELF_ID = 10011  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = 111111111
ADMIN = 1234567890


def ev(segs: list, mid: int, uid: int, nickname: str) -> dict:
    return {
        "time": 1790971000, "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": mid,
        "group_id": GROUP, "user_id": uid, "message": segs,
        "raw_message": "", "font": 0,
        "sender": {"user_id": uid, "nickname": nickname, "card": nickname, "role": "member"},
    }


async def _drain_until(ws, needle: str, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
        except asyncio.TimeoutError:
            return ""
        d = json.loads(raw)
        action = d.get("action")
        echo = d.get("echo")
        if action == "send_msg":
            m = d.get("params", {}).get("message") or []
            text = "".join(
                str(x.get("data", {}).get("text", "")) for x in m if isinstance(x, dict) and x.get("type") == "text"
            ) if isinstance(m, list) else str(m)
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
    return ""


async def main() -> None:
    base = int(time.time()) % 10**6 * 10
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(
            json.dumps(ev([{"type": "text", "data": {"text": "/mood set 得意 0.9 刚被群友夸了"}}], base + 1, ADMIN, "管理员"))
        )
        r1 = await _drain_until(ws, "已设置", 30)
        print("[设置] ->", (r1 or "（无回复）")[:100])
        await asyncio.sleep(1)
        await ws.send(
            json.dumps(
                ev(
                    [
                        {"type": "at", "data": {"qq": str(SELF_ID)}},
                        {"type": "text", "data": {"text": "你现在心情怎么样？"}},
                    ],
                    base + 2,
                    29981,
                    "小郑",
                )
            )
        )
        r2 = await _drain_until(ws, "得意", 200)
        print("[回答] ->", (r2 or "（无回复/未含得意）")[:140])
        print("RESULT: 设置 =", bool(r1), "| 注入（含得意）= ", "得意" in (r2 or ""))
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
