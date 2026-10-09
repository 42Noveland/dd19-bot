"""启动报告 P0 单测：报告结构 / GBK 安全 / 自检警告 / 连接监控。"""
import asyncio
import time
import types

import nonebot
from nonebot.config import Config


def _noop_deco(*a, **k):
    def deco(f):
        return f

    return deco


nonebot._driver = types.SimpleNamespace(config=Config(), on_startup=_noop_deco, on_ready=_noop_deco)

from core import config as core_config  # noqa: E402
from core import context  # noqa: E402
from core.config import load_config  # noqa: E402
from plugins import startup_report  # noqa: E402


def _cfg(**over):
    env = {"LLM_ENABLED": "1", "SEARCH_API_KEY": "k", "SUPERUSERS": "123,456"}
    env.update(over)
    return load_config(env)


def test_report_contains_sections_and_gbk(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(), raising=False)
    out = startup_report.build_report(now_str="2026-10-09 20:23:42")
    for token in ("[模型]", "[用量]", "[群组]", "[人设]", "[数据]", "[开关]", "[提示]"):
        assert token in out
    out.encode("gbk")  # GBK 安全：CMD 窗口下不能有编码错误字符


def test_report_warns_missing_key(tmp_path, monkeypatch):
    context.reset(tmp_path / "ctx.db")
    monkeypatch.setattr(core_config, "_config", _cfg(SEARCH_API_KEY=""), raising=False)
    out = startup_report.build_report(now_str="x")
    assert "[警告]" in out and "SEARCH_API_KEY" in out


def test_watch_connection_ok():
    emitted: list[str] = []
    seq = iter([{}, {}, {"c": types.SimpleNamespace(self_id="180517257")}])
    asyncio.run(
        startup_report.watch_connection(
            time.time(),
            probe=lambda: next(seq, {"c": types.SimpleNamespace(self_id="180517257")}),
            interval=0.01,
            wait_max=2,
            warn_at=10,
            emit=emitted.append,
        )
    )
    assert len(emitted) == 1 and "[OK]" in emitted[0] and "180517257" in emitted[0]


def test_watch_connection_warns_and_times_out():
    emitted: list[str] = []
    asyncio.run(
        startup_report.watch_connection(
            time.time(),
            probe=lambda: {},
            interval=0.02,
            wait_max=0.15,
            warn_at=0.05,
            emit=emitted.append,
        )
    )
    assert any("还没等到" in s for s in emitted)  # 30 秒等效提醒
    assert any("仍未连接" in s for s in emitted)  # 超时提醒
