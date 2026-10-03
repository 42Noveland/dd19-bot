"""模拟 NapCat 反向 WS 客户端，对运行中的 bot 做端到端自测。

前置：
  1. bot/.env 的 ALLOWED_GROUP_IDS 至少有 1 个群号；bot 已启动（另开终端）
  2. 在 bot/ 目录运行：.venv/Scripts/python.exe tests/e2e/fake_napcat.py

覆盖场景（按 bot/.env 的 LLM_REPLY_MODE 分支）：
  [OK] /ping、/jrrp
  [OK] 非白名单群完全静默
  [OK] 默认聊天路由：mention=不@不聊 / all=都聊 / command=都不聊
  [OK] 真实 LLM 一轮（@机器人，或 all 模式普通消息；走主选+回退链）
  [OK] 文本形式 @（"@QQ号" 当普通文字）：命令与聊天均可触发（兼容层）
  [OK] 联网搜索：/search 指令 与 LLM 按需调用 web_search 工具
  [OK] /model 列表/切换/还原（需 .env 配置 SUPERUSERS）

成功时最后一行输出 ALL PASS，退出码 0。
环境变量 E2E_LLM_WAIT 可覆盖 LLM 回复等待秒数（默认 240）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

try:  # websockets >= 14
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # websockets 13.x
    from websockets.client import connect as ws_connect

SELF_ID = 10001
DENIED_GROUP = 999999999
TEST_USER = 20002

REPLY_ACTIONS = {"send_group_msg", "send_msg"}

# OneBot v11 反向 WS 握手头（真实 NapCat 会自动携带；bot 侧强制要求 X-Self-ID）
_WS_HEADERS = {"X-Self-ID": str(SELF_ID), "X-Client-Role": "Universal"}



def _load_env() -> dict[str, str]:
    path = Path(__file__).resolve().parents[2] / ".env"
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _text_seg(text: str) -> dict:
    return {"type": "text", "data": {"text": text}}


def _event(group_id: int, message: list, message_id: int, user_id: int = TEST_USER) -> dict:
    raw = "".join(seg["data"].get("text", "") for seg in message if seg["type"] == "text")
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "time": int(time.time()),
        "self_id": SELF_ID,
        "message_id": message_id,
        "group_id": group_id,
        "user_id": user_id,
        "message": message,
        "raw_message": raw,
        "font": 0,
        "sender": {"user_id": user_id, "nickname": "tester"},
    }


def _ok_data(action: str) -> dict:
    if action == "get_login_info":
        return {"user_id": SELF_ID, "nickname": "fake-napcat"}
    if action == "get_version_info":
        return {
            "app_name": "fake-napcat",
            "app_version": "0.0.1",
            "protocol_version": "v11",
            "status": {"good": True, "online": True},
        }
    if action == "get_status":
        return {"good": True, "online": True}
    return {"message_id": 1}


def _params_text(action: dict) -> str:
    return json.dumps(action.get("params") or {}, ensure_ascii=False)


async def _wait_for(ws, predicate, timeout: float) -> dict | None:
    """等待一个满足 predicate 的 API 调用帧（期间自动应答所有 API 调用），超时返回 None。"""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            return None
        data = json.loads(frame)
        if isinstance(data, dict) and "action" in data and "echo" in data:
            reply = {"status": "ok", "retcode": 0, "data": _ok_data(data["action"]), "echo": data["echo"]}
            await ws.send(json.dumps(reply))
            if predicate(data):
                return data


def _is_chat_reply(action: dict) -> bool:
    return action.get("action") in REPLY_ACTIONS


async def main() -> int:
    env = _load_env()
    port = int(env.get("PORT", "8081"))
    mode = (env.get("LLM_REPLY_MODE", "mention").strip().lower()) or "mention"
    assert mode in ("command", "mention", "all"), f"未知 LLM_REPLY_MODE: {mode}"
    allowed_ids = [x.strip() for x in env.get("ALLOWED_GROUP_IDS", "").split(",") if x.strip().isdigit()]
    assert allowed_ids, "bot/.env 里没有配置 ALLOWED_GROUP_IDS"
    allowed_group = int(allowed_ids[0])
    try:
        superusers = [str(x) for x in json.loads(env.get("SUPERUSERS", "[]"))]
    except Exception:
        superusers = []
    wait_llm = float(env.get("E2E_LLM_WAIT", "240"))

    print(f"连接 ws://127.0.0.1:{port}/onebot/v11/ws …… 模式={mode}，测试群={allowed_group}")
    url = f"ws://127.0.0.1:{port}/onebot/v11/ws"
    try:  # websockets >= 14
        _conn = ws_connect(url, max_size=2**22, additional_headers=_WS_HEADERS)
    except TypeError:  # websockets 13.x
        _conn = ws_connect(url, max_size=2**22, extra_headers=_WS_HEADERS)
    async with _conn as ws:
        # 模拟 NapCat 上线
        await ws.send(
            json.dumps(
                {
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                    "time": int(time.time()),
                    "self_id": SELF_ID,
                }
            )
        )
        await asyncio.sleep(1.0)

        # 1) /ping
        await ws.send(json.dumps(_event(allowed_group, [_text_seg("/ping")], 2001)))
        action = await _wait_for(ws, lambda d: _is_chat_reply(d) and "pong" in _params_text(d), 8)
        assert action, "/ping 未收到 pong 回复"
        print("[OK] /ping ->", _params_text(action)[:100])

        # 2) /jrrp
        await ws.send(json.dumps(_event(allowed_group, [_text_seg("/jrrp")], 2002)))
        action = await _wait_for(ws, lambda d: _is_chat_reply(d) and "人品" in _params_text(d), 8)
        assert action, "/jrrp 未收到回复"
        print("[OK] /jrrp ->", _params_text(action)[:100])

        # 3) 非白名单群：不应有任何回复
        await ws.send(json.dumps(_event(DENIED_GROUP, [_text_seg("/ping")], 2003)))
        action = await _wait_for(ws, _is_chat_reply, 2.5)
        assert action is None, f"非白名单群不应有回复: {_params_text(action)}"
        print("[OK] 非白名单群无回复")

        # 4) 默认聊天路由：先测“不该回”的情形，再测应回情形（真实 LLM 一轮）
        at_segs = [{"type": "at", "data": {"qq": str(SELF_ID)}}, _text_seg(" 你好，用一句话介绍你自己")]
        if mode == "all":
            await ws.send(json.dumps(_event(allowed_group, [_text_seg("下午吃什么好呀")], 2004)))
            action = await _wait_for(ws, _is_chat_reply, wait_llm)
            assert action, "all 模式：普通消息应有 LLM 回复"
            print("[OK] all 模式普通消息 ->", _params_text(action)[:150])
        else:
            await ws.send(json.dumps(_event(allowed_group, [_text_seg("下午吃什么好呀")], 2004)))
            action = await _wait_for(ws, _is_chat_reply, 2.5)
            assert action is None, f"{mode} 模式：普通消息不应有回复: {_params_text(action)}"
            print(f"[OK] {mode} 模式：普通消息静默")

        if mode in ("mention", "all"):
            await ws.send(json.dumps(_event(allowed_group, at_segs, 2005)))
            action = await _wait_for(ws, _is_chat_reply, wait_llm)
            assert action, f"{mode} 模式：@机器人后应有 LLM 回复（等待 {wait_llm:.0f}s 超时）"
            text = _params_text(action)
            assert "说得太快啦" not in text, "触发了限频（前端发送过快），请重跑脚本"
            print("[OK] @机器人 -> LLM 回复:", text[:150])
        else:  # command 模式：@ 不回，/chat 回
            await ws.send(json.dumps(_event(allowed_group, at_segs, 2005)))
            action = await _wait_for(ws, _is_chat_reply, 2.5)
            assert action is None, f"command 模式：@机器人不应有回复: {_params_text(action)}"
            print("[OK] command 模式：@机器人不回复")
            await ws.send(json.dumps(_event(allowed_group, [_text_seg("/chat 你好")], 2006)))
            action = await _wait_for(ws, _is_chat_reply, wait_llm)
            assert action, "command 模式：/chat 应有 LLM 回复"
            print("[OK] command 模式 /chat ->", _params_text(action)[:150])

        # 4b) 文本形式的 @（兼容层）："@QQ号" 当普通文字也应触发（命令 + 聊天）
        await ws.send(json.dumps(_event(allowed_group, [_text_seg(f"@{SELF_ID} /ping")], 2007)))
        action = await _wait_for(ws, lambda d: _is_chat_reply(d) and "pong" in _params_text(d), 8)
        assert action, "文本@ + /ping 未收到 pong（兼容层未生效）"
        print("[OK] 文本@命令 -> pong")

        if mode in ("mention", "all"):
            await ws.send(
                json.dumps(
                    _event(allowed_group, [_text_seg(f"@{SELF_ID} 请只回复两个字：收到")], 2008, user_id=20003)
                )
            )
            action = await _wait_for(ws, _is_chat_reply, wait_llm)
            assert action, "文本@ 聊天未收到回复（兼容层未生效）"
            text = _params_text(action)
            assert "说得太快啦" not in text, "触发了限频，请重跑脚本"
            print("[OK] 文本@聊天 -> LLM 回复:", text[:150])

        # 4c) 联网搜索：/search 指令 + LLM 按需调用 web_search 工具
        search_on = env.get("SEARCH_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
        if search_on:
            await ws.send(json.dumps(_event(allowed_group, [_text_seg("/search 北京今天天气")], 2009, user_id=20004)))
            action = await _wait_for(ws, _is_chat_reply, wait_llm)
            assert action, "/search 未收到回复"
            text = _params_text(action)
            assert "搜索失败" not in text, f"/search 失败: {text[:200]}"
            assert "参考链接" in text or "http" in text, f"/search 回复不含链接: {text[:200]}"
            print("[OK] /search ->", text[:150])

            if mode in ("mention", "all"):
                tool_segs = [
                    {"type": "at", "data": {"qq": str(SELF_ID)}},
                    _text_seg(" 帮我搜一下北京今天的天气，然后告诉我"),
                ]
                await ws.send(json.dumps(_event(allowed_group, tool_segs, 2010, user_id=20005)))
                action = await _wait_for(ws, _is_chat_reply, wait_llm * 1.5)
                assert action, "LLM 按需搜索（工具调用）未收到回复"
                text = _params_text(action)
                assert "白饭吃完了" not in text, "意外触发配额提示"
                print("[OK] LLM 按需搜索 -> ", text[:150])
        else:
            print("[SKIP] SEARCH_ENABLED=0，跳过搜索场景")

        # 5) /model（仅当 .env 配置了 SUPERUSERS 时）
        if superusers:
            admin = int(superusers[0])
            await ws.send(json.dumps(_event(allowed_group, [_text_seg("/model")], 2101, user_id=admin)))
            action = await _wait_for(ws, lambda d: _is_chat_reply(d) and "opencode_go" in _params_text(d), 8)
            assert action, "/model 列表未收到回复"
            print("[OK] /model 列表 ->", _params_text(action)[:120])

            await ws.send(json.dumps(_event(allowed_group, [_text_seg("/model local")], 2102, user_id=admin)))
            action = await _wait_for(ws, lambda d: _is_chat_reply(d) and "已切换" in _params_text(d), 8)
            assert action, "/model 切换未收到回复"
            print("[OK] /model local ->", _params_text(action)[:120])

            await ws.send(json.dumps(_event(allowed_group, [_text_seg("/model opencode_go")], 2103, user_id=admin)))
            action = await _wait_for(ws, lambda d: _is_chat_reply(d) and "已切换" in _params_text(d), 8)
            assert action, "/model 还原未收到回复"
            print("[OK] /model opencode_go ->", _params_text(action)[:120])
        else:
            print("[SKIP] 未配置 SUPERUSERS，跳过 /model 场景")

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
