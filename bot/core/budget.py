"""Token 用量预算：单次会话上限 + 单日上限（按北京日期重置，JSON 持久化）。

- 单次会话上限（LLM_MAX_TOKENS）：由 llm.chat_once 在发请求时执行（输入估算 + 输出上限 ≤ 上限）
- 单日上限（LLM_DAILY_TOKEN_LIMIT）：本模块记账；达到后 exhausted()=True，
  聊天路径只输出 LLM_QUOTA_REPLY（默认"白饭吃完了QAQ"）
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from pathlib import Path
from typing import Any

from .config import get_config

_lock = threading.Lock()
_state: dict[str, Any] | None = None

_CJK_RANGES = (("\u4e00", "\u9fff"), ("\u3040", "\u30ff"), ("\uac00", "\ud7af"))


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：CJK 约 1 字/token，其余约 3.5 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return max(1, int(cjk + other / 3.5))


def truncate_to_tokens(text: str, limit: int) -> str:
    """把文本截断到估算不超过 limit 个 token（超长时附加截断标记）。"""
    if limit <= 0:
        return ""
    if estimate_tokens(text) <= limit:
        return text
    budget = 0.0
    cut = 0
    for i, ch in enumerate(text):
        budget += 1.0 if _is_cjk(ch) else 1 / 3.5
        if budget > limit:
            cut = i
            break
    else:
        return text
    return text[:cut].rstrip() + "……（内容过长已截断）"


def _today() -> str:
    tz = _dt.timezone(_dt.timedelta(hours=8))
    return _dt.datetime.now(tz).strftime("%Y-%m-%d")


def _resolve_path(path: Path | str | None) -> Path:
    if path is not None:
        return Path(path)
    cfg = get_config()
    p = Path(cfg.usage_file)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p


def _load(path: Path | str | None = None) -> dict[str, Any]:
    """读取当日用量；日期变化/文件缺失时重置。调用方需持有 _lock。"""
    global _state
    target = _resolve_path(path)
    if _state is None or _state.get("date") != _today():
        fresh: dict[str, Any] = {"date": _today(), "total": 0, "by_provider": {}}
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("date") == fresh["date"]:
                fresh = raw
        except Exception:  # noqa: BLE001 —— 文件缺失/损坏都从零开始
            pass
        _state = fresh
    return _state


def _save(state: dict[str, Any], path: Path | str | None = None) -> None:
    target = _resolve_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass  # 记账失败不影响主流程


def record(provider: str, tokens: int, path: Path | str | None = None) -> None:
    """累加一次调用的 token 用量。"""
    if tokens <= 0:
        return
    with _lock:
        state = _load(path)
        state["total"] = int(state.get("total", 0)) + int(tokens)
        by_provider = state.setdefault("by_provider", {})
        by_provider[provider] = int(by_provider.get(provider, 0)) + int(tokens)
        _save(state, path)


def status(path: Path | str | None = None) -> dict[str, Any]:
    with _lock:
        state = _load(path)
        return {
            "date": state.get("date"),
            "total": int(state.get("total", 0)),
            "by_provider": dict(state.get("by_provider", {})),
        }


def used_today(path: Path | str | None = None) -> int:
    return status(path)["total"]


def remaining(path: Path | str | None = None) -> int:
    limit = get_config().llm_daily_token_limit
    return max(0, limit - used_today(path))


def exhausted(path: Path | str | None = None) -> bool:
    return remaining(path) <= 0


def reset(path: Path | str | None = None) -> None:
    """测试/维护用：清空当日用量。"""
    global _state
    with _lock:
        _state = {"date": _today(), "total": 0, "by_provider": {}}
        _save(_state, path)
