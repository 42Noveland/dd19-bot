"""活体探针：记忆系统（提炼 → 注入）。

流程：
  ① 注入一条事实消息（"我最喜欢的颜色是蓝色…"）
  ② 用 13 条填充消息把它挤出 12 条上下文窗口
  ③ 以管理员身份发 /memory extract 触发提炼（确定性，不等后台循环）
  ④ @bot 提问 → 期望回答包含"蓝色"——此时窗口里没有它，只能来自长期记忆。

前置：积压已追平（否则 extract 处理的是更旧的批次）；会真实消耗 token。
用法：python tests/e2e/probe_memory.py
"""
import asyncio
import json
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

import _probe_env

SELF_ID = 10009  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = _probe_env.group()
ADMIN = _probe_env.admin()

FACT = "我最喜欢的颜色是蓝色，以后问我记得答蓝色哈"
FILLERS = [
    "今天有点累", "中午吃了拉面", "下午要开会", "这周过得真快", "空调有点冷", "想喝奶茶了",
    "晚上去跑步", "手机快没电了", "周末想去爬山", "快递到了没", "刚看完个电影", "有点困了", "明天要早起",
]


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
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": FACT}}], base + 1, 29961, "小陈")))
        for i, f in enumerate(FILLERS):
            await ws.send(json.dumps(ev([{"type": "text", "data": {"text": f}}], base + 2 + i, 29961, "小陈")))
            await asyncio.sleep(0.35)
        await asyncio.sleep(1.5)
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": "/memory extract"}}], base + 30, ADMIN, "管理员")))
        r = await _drain_until(ws, "提炼完成", 220)
        print("[提炼] ->", (r or "（无回复）")[:150])
        await ws.send(
            json.dumps(
                ev(
                    [
                        {"type": "at", "data": {"qq": str(SELF_ID)}},
                        {"type": "text", "data": {"text": "你知道我最喜欢的颜色是什么吗？"}},
                    ],
                    base + 40,
                    29961,
                    "小陈",
                )
            )
        )
        r2 = await _drain_until(ws, "蓝色", 220)
        print("[回答] ->", (r2 or "（无回复/未含蓝色）")[:150])
        print("RESULT: 记忆提炼 =", bool(r), "| 记忆注入（含蓝色）= ", "蓝色" in (r2 or ""))
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
