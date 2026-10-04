"""活体探针：黑话（种消息 → /jargon mine → 列表核对）。

流程：① 往群消息表种 6 条含生造黑话「蓝瘦」的消息（同一用户）；② 管理员 /jargon mine；
     ③ /jargon 查看列表；④ 直接查库核对含义。期望：写入 ≥1、列表含该词。
用法：python tests/e2e/probe_jargon.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # bot/ 根目录（import core）

from core import context, jargon  # noqa: E402

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10014  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()
ADMIN = _probe_env.admin()
TERM = "蓝瘦"


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
    # ① 种 6 条含「蓝瘦」的消息（同一用户，集中度高 → 候选分高）
    for i in range(6):
        context.record_message(GROUP, 29991, "小红", f"今天蓝瘦了哈哈（{i}）")
    base = int(time.time()) % 10**6 * 10
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/jargon mine"}}], base + 1, ADMIN, "管理员")))
        r1 = await _drain_until(ws, "挖掘完成", 90)
        print("[挖掘] ->", (r1 or "（无回复）")[:160])
        await asyncio.sleep(0.5)
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/jargon"}}], base + 2, ADMIN, "管理员")))
        r2 = await _drain_until(ws, "黑话库", 30)
        print("[列表] ->", (r2 or "（无回复）")[:200])
    known = {t: m for t, m in jargon.list_known(GROUP, limit=50)}
    got = TERM in known
    print(f"[落库] 已知黑话 {len(known)} 条，含「{TERM}」= {got}，含义 = {known.get(TERM, '')}")
    print("RESULT: 挖掘 =", bool(r1), "| 列表含词 =", TERM in (r2 or ""), "| 落库含词 =", got)
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
