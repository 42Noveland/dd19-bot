"""活体探针：好感度（设置 → 互动 → 变化）。

流程：① 管理员 /affection set 29991 90；② 小红 @bot 说"谢谢你呀，你最厉害啦"（触发聊天+好感度结算）；
     ③ /affection 查看。期望：29991 的等级 >90（夸赞/感谢 + 心情修正；封顶 100）。
用法：python tests/e2e/probe_affection.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # bot/ 根目录（import core）

from core import affection  # noqa: E402

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10015  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()
ADMIN = _probe_env.admin()


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
            if text.strip() and needle in text:
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
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/affection set 29991 90"}}], base + 1, ADMIN, "管理员")))
        r1 = await _drain_until(ws, "已设置", 30)
        print("[设置] ->", (r1 or "（无回复）")[:120])
        await asyncio.sleep(1)
        await ws.send(
            json.dumps(
                ev(
                    [
                        {"type": "at", "data": {"qq": str(SELF_ID)}},
                        {"type": "text", "data": {"text": "谢谢你呀，你最厉害啦"}},
                    ],
                    base + 2,
                    29991,
                    "小红",
                )
            )
        )
        r2 = await _drain_until(ws, "", 200)
        print("[回复] ->", (r2 or "（无回复）")[:100])
        await asyncio.sleep(0.5)
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/affection"}}], base + 3, ADMIN, "管理员")))
        r3 = await _drain_until(ws, "好感度（本群", 30)
        print("[查看] ->", (r3 or "（无回复）")[:200])
    rows = {x["user_id"]: x for x in affection.top_list(GROUP, limit=50)}
    lvl = int(rows.get(29991, {}).get("level", -1))
    print(f"[核对] 29991 当前好感度 = {lvl}（设置后 90）")
    print("RESULT: 设置 =", bool(r1), "| 有回复 =", bool(r2), "| 等级上升 =", lvl > 90)
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
