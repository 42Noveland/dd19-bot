"""活体探针：记忆检索升级（聊到谁就想起谁）。

用法：python tests/e2e/probe_memory_recall.py mention|name
  mention: "@bot @小明(29911) 他最喜欢的数字是什么？" → 期望答出 73（@ 提及路径，按 uid 检索）
  name:    "@bot 小陈喜欢的颜色是什么？"             → 期望答出 蓝色（名字出现在消息里的路径）

两种模式互不污染（回复中不含对方答案），可先后运行。
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

SELF_ID = 10010  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()

MODES = {
    "mention": (
        [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "at", "data": {"qq": "29911"}},
            {"type": "text", "data": {"text": "他最喜欢的数字是什么？"}},
        ],
        "73",
    ),
    "name": (
        [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": "小陈喜欢的颜色是什么？"}},
        ],
        "蓝色",
    ),
}


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "mention"
    segs, needle = MODES[mode]
    mid = int(time.time()) % 10**6 * 10 + 5
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(
            json.dumps(
                {
                    "time": 1790971000, "self_id": SELF_ID, "post_type": "message",
                    "message_type": "group", "sub_type": "normal", "message_id": mid,
                    "group_id": GROUP, "user_id": 29971, "message": segs,
                    "raw_message": "", "font": 0,
                    "sender": {"user_id": 29971, "nickname": "小芳", "card": "小芳", "role": "member"},
                }
            )
        )
        got = ""
        deadline = time.time() + 200
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
                text = "".join(
                    str(x.get("data", {}).get("text", "")) for x in m if isinstance(x, dict) and x.get("type") == "text"
                ) if isinstance(m, list) else str(m)
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
                if needle in text:
                    got = text
                    break
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
        print(f"[{mode}] 期望含 {needle!r} ->", (got or "（未收到/未命中）")[:140])
        print("RESULT:", mode, "=", bool(got))
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
