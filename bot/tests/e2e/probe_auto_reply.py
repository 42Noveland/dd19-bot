"""活体探针：决策层主动接话（不@也会回复）。

用法：python tests/e2e/probe_auto_reply.py ["dd19 在吗，出来冒个泡"]
说明：统计性探针——判定环节模型有权说不接；本探针用"提到机器人名字"的消息（必判）提高确定性。
注意：接话后每群冷却（AUTO_REPLY_COOLDOWN，默认 240s），重复跑请等冷却结束。
"""
import asyncio
import json
import sys
import time

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

SELF_ID = 10007  # 假 self_id（重复真实 id 会被适配器 403 拒绝）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}
GROUP = 111111111
DEFAULT_MSG = "dd19 在吗，出来冒个泡"


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
    mid = int(time.time()) % 10**6 * 10 + 9
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        await ws.send(json.dumps(ev([{"type": "text", "data": {"text": msg}}], mid, 29951, "小孙")))
        got_text, got_image = None, None
        deadline = time.time() + 220
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
                reply_seg = next((x for x in m if isinstance(x, dict) and x.get("type") == "reply"), None)
                if "image" in kinds and got_image is None:
                    got_image = "有"
                    print("[配图] 主动接话里带表情包")
                if text and got_text is None:
                    got_text = text
                    rid = (reply_seg or {}).get("data", {}).get("id")
                    print("[文字] 引用 =", rid, "->", text[:150])
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
                if got_text:
                    break
            elif action:
                if action == "get_login_info":
                    data = {"user_id": SELF_ID, "nickname": "dd19"}
                elif action == "get_group_member_info":
                    uid = d.get("params", {}).get("user_id")
                    data = {"user_id": uid, "nickname": f"用户{uid}", "card": f"用户{uid}"}
                else:
                    data = {}
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))
        print("RESULT: 主动接话 =", got_text is not None, "| 配图 =", got_image is not None)
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
