"""今日人品（JRRP）：按 QQ 号 + 日期生成确定性的 0-100 值。"""
from __future__ import annotations

import datetime as _dt
import hashlib


def calc_jrrp(user_id: int, day: str) -> int:
    digest = hashlib.md5(f"{user_id}:{day}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 101


def today_str() -> str:
    """返回北京时间日期，如 2026-10-02。"""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    return _dt.datetime.now(tz).strftime("%Y-%m-%d")
