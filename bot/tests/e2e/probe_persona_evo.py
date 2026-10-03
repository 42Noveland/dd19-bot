"""活体探针：人格演化审查（pending → 批准 → 生效 → 回滚）。

流程：① 直接注入一条待审建议；② /persona review 看到；③ /persona approve <id>；
     ④ /persona patches 看到；⑤ resolve 含补充；⑥ /persona patch rm <id> 回滚；⑦ resolve 不再含。
     （另：/persona evolve 生成一轮，打印结果不作断言。）
用法：python tests/e2e/probe_persona_evo.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # bot/ 根目录（import core）

from core import persona_evo, personas  # noqa: E402

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

SELF_ID = 10016  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
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


async def _send_cmd(ws, base: int, n: int, text: str, needle: str, timeout: float = 30) -> str:
    await ws.send(json.dumps(ev([{"type": "text", "data": {"text": text}}], base + n, ADMIN, "管理员")))
    out = await _drain_until(ws, needle, timeout)
    await asyncio.sleep(0.4)
    return out


async def main() -> None:
    stamp = int(time.time()) % 100000
    marker = f"探针建议{stamp}：多聊一点游戏"
    pid = persona_evo.add_pending(GROUP, "十九", marker)
    base = int(time.time()) % 10**6 * 10
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        r1 = await _send_cmd(ws, base, 1, "/persona review", f"[{pid}]")
        print("[待审] ->", (r1 or "（无回复）")[:150])
        r2 = await _send_cmd(ws, base, 2, f"/persona approve {pid}", "已批准")
        print("[批准] ->", (r2 or "（无回复）")[:150])
        r3 = await _send_cmd(ws, base, 3, "/persona patches", marker)
        print("[补充列表] ->", (r3 or "（无回复）")[:150])
        _, text = personas.resolve(GROUP)
        applied = marker in text
        print("[生效核对] resolve 含补充 =", applied)
        patches = [p for p in persona_evo.list_patches(GROUP) if p["text"] == marker]
        rid = patches[0]["id"] if patches else 0
        r4 = await _send_cmd(ws, base, 4, f"/persona patch rm {rid}", "已回滚")
        print("[回滚] ->", (r4 or "（无回复）")[:150])
        _, text2 = personas.resolve(GROUP)
        removed = marker not in text2
        print("[回滚核对] resolve 不再含补充 =", removed)
        r5 = await _send_cmd(ws, base, 5, "/persona evolve", "生成完成", 120)
        print("[生成一轮] ->", (r5 or "（无回复）")[:200])
    print("RESULT: 待审 =", bool(r1), "| 批准 =", bool(r2), "| 生效 =", applied, "| 回滚 =", removed)
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
