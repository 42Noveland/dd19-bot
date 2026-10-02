"""core.budget 单元测试：估算/截断/记账/持久化/跨天重置/额度判断。"""
import json

from core import budget as b
from core import config as core_config
from core.config import load_config


def _fresh(path):
    b._state = None
    b.reset(path)


def test_estimate_tokens():
    assert b.estimate_tokens("") == 0
    assert b.estimate_tokens("你好") == 2
    assert b.estimate_tokens("hello") == 1


def test_truncate_to_tokens():
    long_text = "啊" * 100
    cut = b.truncate_to_tokens(long_text, 10)
    assert cut.endswith("内容过长已截断）")
    assert cut.count("啊") == 10
    assert b.truncate_to_tokens("短", 10) == "短"
    assert b.truncate_to_tokens("啊", 0) == ""


def test_record_and_persist(tmp_path):
    p = tmp_path / "usage.json"
    _fresh(p)
    b.record("deepseek", 100, path=p)
    b.record("opencode_go", 50, path=p)
    b.record("deepseek", 25, path=p)
    st = b.status(p)
    assert st["total"] == 175
    assert st["by_provider"] == {"deepseek": 125, "opencode_go": 50}
    b._state = None  # 模拟进程重启后从文件读回
    assert b.used_today(p) == 175


def test_old_date_resets(tmp_path):
    p = tmp_path / "usage.json"
    p.write_text(
        json.dumps({"date": "2000-01-01", "total": 999, "by_provider": {"x": 999}}),
        encoding="utf-8",
    )
    b._state = None
    assert b.used_today(p) == 0


def test_exhausted_and_remaining(tmp_path, monkeypatch):
    p = tmp_path / "usage.json"
    _fresh(p)
    monkeypatch.setattr(
        core_config, "_config", load_config({"LLM_DAILY_TOKEN_LIMIT": "100"}), raising=False
    )
    assert not b.exhausted(p)
    assert b.remaining(p) == 100
    b.record("x", 100, path=p)
    assert b.exhausted(p)
    assert b.remaining(p) == 0
