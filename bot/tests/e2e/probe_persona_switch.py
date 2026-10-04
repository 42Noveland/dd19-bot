"""活体探针：按群人设 + 动态切换（管理员群内操作，立即生效）。

流程：① /persona elena → "已切换"；② /persona → 显示当前 Elena；
     ③ 另一个人 @bot 问一句（打印回复供检视）；④ /persona default → "已恢复"。
用法：python tests/e2e/probe_persona_switch.py
"""
import asyncio
import json
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10012  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
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
            if text.strip() and needle in text:  # 图片-only 消息（text 为空）跳过；needle="" 时等任意非空文字
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
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/persona elena"}}], base + 1, ADMIN, "管理员")))
        r1 = await _drain_until(ws, "已切换", 30)
        print("[切换] ->", (r1 or "（无回复）")[:120])

        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/persona"}}], base + 2, ADMIN, "管理员")))
        r2 = await _drain_until(ws, "当前人设", 30)
        print("[状态] ->", (r2 or "（无回复）")[:160])

        await ws.send(
            json.dumps(
                ev(
                    [
                        {"type": "at", "data": {"qq": str(SELF_ID)}},
                        {"type": "text", "data": {"text": "你好呀，简单介绍一下你自己？"}},
                    ],
                    base + 3,
                    29991,
                    "小红",
                )
            )
        )
        r3 = await _drain_until(ws, "", 200)
        print("[回复] ->", (r3 or "（无回复）")[:160])

        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/persona default"}}], base + 4, ADMIN, "管理员")))
        r4 = await _drain_until(ws, "已恢复", 30)
        print("[恢复] ->", (r4 or "（无回复）")[:120])

        print(
            "RESULT: 切换 =", "已切换" in (r1 or ""),
            "| 状态含Elena =", "Elena" in (r2 or ""),
            "| 有回复 =", bool(r3),
            "| 恢复 =", "已恢复" in (r4 or ""),
        )
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
