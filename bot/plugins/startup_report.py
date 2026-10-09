"""启动报告（P0）：开机一屏状态摘要 + 连接等待提示 + 配置自检。

- 输出只用 GBK 安全字符（中文/ASCII/框线/√×）——CMD 窗口是 GBK 代码页，emoji 会乱码；
- 文件日志不受影响（INFO 级业务输出照常双写控制台与 logs/bot-*.log）；
- 连接监控：连上打 [OK]（含启动耗时）；30 秒未连提醒查 NapCat；超时仍未连再提示。
"""
from __future__ import annotations

import asyncio
import time

import nonebot
from loguru import logger as _log
from nonebot import get_driver

from core import budget, context, llm, personas
from core.config import get_config

driver = get_driver()

_T0 = time.time()  # 模块导入时刻 ≈ 进程启动时刻（用于"启动用 X 秒"）
_LINE = "=" * 46


def _format_tokens(n: int) -> str:
    n = int(n or 0)
    if n >= 10_000:
        return f"{n / 10_000:.1f}w"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _thinking_label(provider) -> str:
    payload = getattr(provider, "extra_payload", None) or {}
    if "reasoning_effort" in payload:
        return f"思考 {payload['reasoning_effort']}"
    th = payload.get("thinking")
    if isinstance(th, dict) and th.get("type") == "disabled":
        return "思考 关"
    return ""


def build_report(*, now_str: str) -> str:
    """组装启动报告（纯函数，便于测试；缺数据的行自然缺省）。"""
    cfg = get_config()
    out: list[str] = [_LINE, f" dd19 bot · 启动于 {now_str}", _LINE]

    try:
        pname = llm.current_default()
        provider = cfg.providers[pname]
        th = _thinking_label(provider)
        out.append(f"[模型] {pname} / {provider.model}" + (f"（{th}）" if th else ""))
    except Exception:  # noqa: BLE001
        out.append("[模型] 读取失败（检查 .env 的 LLM_PROVIDER）")

    try:
        used = budget.used_today()
        limit = int(getattr(cfg, "llm_daily_token_limit", 0) or 0)
        out.append(f"[用量] 今日 {_format_tokens(used)} / {_format_tokens(limit)}（明细 /usage）")
    except Exception:  # noqa: BLE001
        pass

    try:
        gids = sorted(int(g) for g in cfg.allowed_group_ids)
        if gids:
            parts = []
            for g in gids[:8]:
                try:
                    nm = personas.name_for(g)
                except Exception:  # noqa: BLE001
                    nm = ""
                parts.append(f"{g}（{nm}）" if nm else str(g))
            out.append(f"[群组] {len(gids)} 个：" + " · ".join(parts))
        else:
            out.append("[群组] （未配置白名单群：检查 ALLOWED_GROUPS）")
    except Exception:  # noqa: BLE001
        pass

    try:
        names = [str(p.get("name") or p.get("stem") or "") for p in personas.list_personas()]
        names = [n for n in names if n]
        if names:
            out.append("[人设] 可用：" + "、".join(names[:8]) + "（/persona 切换）")
    except Exception:  # noqa: BLE001
        pass

    try:
        c = context.counts()
        out.append(
            f"[数据] 消息 {c['messages']} 条 · 图库 {c['images']} 张 · 记忆 {c['memories']} 条"
            f" · 待提醒 {c['reminders']} 条"
        )
    except Exception:  # noqa: BLE001
        pass

    def _mark(ok: bool) -> str:
        return "√" if ok else "×"

    out.append(
        "[开关] "
        f"搜索 {_mark(bool(cfg.search_enabled) and bool(cfg.search_api_key))} "
        f"贴图 {_mark(bool(cfg.sticker_enabled))} "
        f"记忆 {_mark(bool(cfg.memory_enabled))} "
        f"心情 {_mark(bool(cfg.mood_enabled))} "
        f"接话 {_mark(bool(cfg.auto_reply_enabled))} "
        f"协议 {_mark(bool(cfg.send_protocol_enabled))}"
    )

    # 配置自检：缺失才显示
    try:
        if not cfg.search_api_key:
            out.append("[警告] SEARCH_API_KEY 未配置：搜索 / 读链接不可用")
    except Exception:  # noqa: BLE001
        pass
    try:
        su = nonebot.get_driver().config.superusers
        if not su:
            out.append("[警告] SUPERUSERS 未配置：管理命令（/model /memory /mood 等）不可用")
    except Exception:  # noqa: BLE001
        pass

    out.append("[提示] 连上后 @dd19 即可聊天；/help 查看命令")
    out.append(_LINE)
    return "\n".join(out)


def _emit(s: str) -> None:
    _log.info(s)


async def watch_connection(
    t0: float,
    *,
    probe=None,
    interval: float = 1.0,
    wait_max: float = 60.0,
    warn_at: float = 30.0,
    emit=None,
) -> None:
    """等 QQ 连接：连上打 [OK]；warn_at 秒未连提醒查 NapCat；wait_max 秒仍未连再提醒。"""
    emit = emit or _emit
    probe = probe or (lambda: nonebot.get_bots())
    warned = False
    while time.time() - t0 < wait_max:
        await asyncio.sleep(interval)
        try:
            bots = probe()
        except Exception:  # noqa: BLE001
            bots = {}
        if bots:
            name = ""
            try:
                name = str(getattr(next(iter(bots.values())), "self_id", "") or "")
            except Exception:  # noqa: BLE001
                pass
            emit(f"[OK] QQ 已连接（{name}）——可以聊天了（启动用 {time.time() - t0:.0f} 秒）")
            return
        if not warned and time.time() - t0 >= warn_at:
            warned = True
            emit("[!] 还没等到 QQ 连接——请检查 NapCat 窗口是否在运行（继续等待中）")
    emit(f"[!] 超过 {wait_max:.0f} 秒仍未连接——请检查 NapCat 与网络后重启 bot")


@driver.on_startup
async def _report_on_start() -> None:
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    _log.info(build_report(now_str=now_str))
    _log.info("[连接] 等待 QQ 连接中……（首次连接一般几秒内完成）")
    asyncio.create_task(watch_connection(_T0))
