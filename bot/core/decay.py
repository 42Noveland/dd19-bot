"""共享衰减工具：15 天二次新鲜度曲线（借鉴 astrbot self-learning / MaiBot 的衰减算法）。

- 0 天 = 1.0；7.5 天 ≈ 0.8；≥15 天 = floor（默认 0.2）——老条目仍可用，只是优先级降低。
- 供贴图库 / 风格样本 / 黑话候选等"按新鲜度降权"的场景共用。
"""
from __future__ import annotations

import time

DECAY_DAYS = 15.0  # 衰减窗口
DECAY_FLOOR = 0.2  # 地板：衰减到 floor 后不再继续降


def freshness(
    ts: float | None,
    *,
    days: float = DECAY_DAYS,
    floor: float = DECAY_FLOOR,
    now: float | None = None,
) -> float:
    """按"最近活跃时间戳"算新鲜度（1.0 → floor 的二次曲线）。ts<=0 → floor。"""
    try:
        last = float(ts or 0)
    except (TypeError, ValueError):
        last = 0.0
    if last <= 0:
        return floor
    cur = time.time() if now is None else now
    age_days = max((cur - last) / 86400.0, 0.0)
    ratio = min(age_days, days) / days
    return 1.0 - (ratio**2) * (1.0 - floor)
