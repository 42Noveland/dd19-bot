"""活体探针：风格学习（种对话对 → /style extract → 落库核对）。

流程：① 直接往群消息表种一对「用户→Bot(真实QQ)」对话；② 管理员 /style extract；
     ③ /style 查看计数；④ 直接查库核对。期望：提取 ≥1、计数 ≥1、落库含种入的对话对。
用法：python tests/e2e/probe_style.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # bot/ 根目录（import core）

from core import context, style_pairs  # noqa: E402

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10013  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()
ADMIN = _probe_env.admin()
BOT_QQ = _probe_env.bot_qq()


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
    # ① 种一对对话（只有真实 bot QQ 的回复行才会被提取）
    stamp = int(time.time()) % 100000
    sit = f"探针{stamp}：你们觉得那个新出的游戏怎么样"
    exp = f"探针{stamp}：我还没玩，看评价挺两极的，你玩了吗"
    context.record_message(GROUP, 29991, "小红", sit)
    context.record_message(GROUP, BOT_QQ, "十九", exp)
    base = int(time.time()) % 10**6 * 10
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/style extract"}}], base + 1, ADMIN, "管理员")))
        r1 = await _drain_until(ws, "提取完成", 60)
        print("[提取] ->", (r1 or "（无回复）")[:160])
        await asyncio.sleep(0.5)
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/style"}}], base + 2, ADMIN, "管理员")))
        r2 = await _drain_until(ws, "风格库", 30)
        print("[状态] ->", (r2 or "（无回复）")[:160])
    rows = style_pairs.list_pairs(GROUP, limit=100)
    found = any(sit[:16] in str(r.get("situation") or "") for r in rows)
    saved_ok = "新增/刷新" in (r1 or "") and not (r1 or "").endswith("0 对")
    print(f"[落库] 本群风格对 {len(rows)} 条，含种入对话对 = {found}")
    print("RESULT: 提取 =", bool(r1), "| 状态 =", bool(r2), "| 落库 =", found)
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
