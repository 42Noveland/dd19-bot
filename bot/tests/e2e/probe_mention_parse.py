"""活体探针：向运行中的 bot 注入 @ 解析相关的测试消息，捕获真实回复。

用途：排查"@ 后字符被忽略"类问题。经 WS 注入（事件来自本连接，回复只会回到本连接，
不会发到真实群）。四种形态：
  T1 用户复刻版："回复1；@如果看到后面了就回复2"
  T2 原始版（10-03 00:15 实测）："你看到这个就回复一个是@你看到后面了就回复不是"
  T3 复述测试：要求原样复述含 @ 的整段（验证 LLM 是否真的看到 @ 后内容）
  T4 中间真实 @ 段：text + at(他人) + text（验证 at 段可见性）
"""
import asyncio
import json

try:
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # 兼容旧版 websockets
    from websockets.client import connect as ws_connect

SELF_ID = 10002  # 不能用真实 bot 的 123456789：适配器会拒绝重复的 X-Self-ID（握手 403）
URL = "ws://127.0.0.1:8081/onebot/v11/ws"
HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}


def ev(group: int, segs: list, mid: int, uid: int) -> dict:
    return {
        "time": 1790965000, "self_id": SELF_ID, "post_type": "message",
        "message_type": "group", "sub_type": "normal", "message_id": mid,
        "group_id": group, "user_id": uid, "message": segs,
        "raw_message": "", "font": 0,
        "sender": {"user_id": uid, "nickname": "probe", "card": "", "role": "member"},
    }


async def wait_reply(ws, timeout: float = 180.0) -> str:
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        d = json.loads(raw)
        action = d.get("action")
        echo = d.get("echo")
        if action == "send_msg":
            msg = d.get("params", {}).get("message")
            if isinstance(msg, list):
                text = "".join(str(s.get("data", {}).get("text", "")) for s in msg if s.get("type") == "text")
            else:
                text = str(msg)
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}, "echo": echo}))
            return text
        if action:
            if action == "get_login_info":
                data = {"user_id": SELF_ID, "nickname": "dd19"}
            elif action == "get_group_member_info":
                uid = d.get("params", {}).get("user_id")
                data = {"user_id": uid, "nickname": "老王.13900000000", "card": "老王.13900000000"}
            else:
                data = {}
            await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": echo}))


async def main() -> None:
    tests = [
        ("T1 回复1/回复2 版", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": "你看到这个就回复1；@如果看到后面了就回复2"}},
        ], 4001, 29901),
        ("T2 原始 00:15 版", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": "你看到这个就回复一个是@你看到后面了就回复不是"}},
        ], 4002, 29902),
        ("T3 复述测试", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": "请一字不差地复述引号内整段文字（包括@后的全部内容）：\"开头标记@尾巴标记-香蕉菠萝\""}},
        ], 4003, 29903),
        ("T4 中间真实@段", [
            {"type": "at", "data": {"qq": str(SELF_ID)}},
            {"type": "text", "data": {"text": "我想问问"}},
            {"type": "at", "data": {"qq": "1234567890"}},
            {"type": "text", "data": {"text": "是个怎样的人"}},
        ], 4004, 29904),
    ]
    async with ws_connect(URL, max_size=2**22, additional_headers=HEADERS) as ws:
        for name, segs, mid, uid in tests:
            await ws.send(json.dumps(ev(111111111, segs, mid, uid)))
            try:
                reply = await wait_reply(ws)
                print(f"[{name}] -> {reply[:220]}")
            except asyncio.TimeoutError:
                print(f"[{name}] -> TIMEOUT")
            print("---")
    print("PROBE DONE")


if __name__ == "__main__":
    asyncio.run(main())
