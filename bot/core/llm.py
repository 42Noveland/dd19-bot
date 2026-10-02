"""LLM 调用（local / deepseek / opencode_go 三后端）+ 思考模式 + 失败回退 + 限频。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from .config import Provider, get_config

# opencode.ai 前面有 Cloudflare：库默认 UA 可能被 403（实测参考：opencode-go-usage 技能）
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 qqbot/1.0"

# 内联思维链块（Qwen 等模板风格）；结构化字段缺失时由此兜底
_INLINE_THINK_RE = re.compile(r"<think(?:ing)?>([\s\S]*?)</think(?:ing)?>", re.IGNORECASE)

_last_call: dict[tuple[int, int], float] = {}
_default_override: str | None = None


class EmptyReplyError(RuntimeError):
    """模型没有给出正文（常见于思考占满输出预算）。触发回退链换下一个后端。"""


@dataclass
class Completion:
    text: str
    reasoning: str = ""
    truncated: bool = False


@dataclass
class Reply:
    provider: str
    text: str
    reasoning: str = ""
    truncated: bool = False


def reset_rate_limit() -> None:
    """测试用：清空限频状态。"""
    _last_call.clear()


def allow(group_id: int, user_id: int, *, now: float | None = None, cooldown: float | None = None) -> bool:
    """同一用户在同一群内每 cooldown 秒最多触发一次；返回是否放行。"""
    if cooldown is None:
        cooldown = get_config().llm_cooldown
    moment = time.monotonic() if now is None else now
    key = (group_id, user_id)
    if moment - _last_call.get(key, float("-inf")) < cooldown:
        return False
    _last_call[key] = moment
    return True


def current_default() -> str:
    return _default_override or get_config().llm_provider


def set_default(name: str) -> None:
    """运行时切换默认后端（仅进程内生效，重启后回到 .env 配置）。"""
    global _default_override
    if name not in get_config().providers:
        raise ValueError(f"未知提供商: {name}")
    _default_override = name


def _build_headers(provider: Provider, session_key: str | None) -> dict[str, str]:
    headers = {"User-Agent": _USER_AGENT}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    if "opencode.ai" in provider.base_url:
        # opencode-go 按会话亲和路由；缺失该头会被 400（MissingSessionID），
        # 即使调用方没传 session_key 也兜底一个稳定值，避免静默降级
        headers["x-opencode-session"] = session_key or "qqbot-default"
    headers.update(provider.extra_headers)
    return headers


def _split_completion(message: dict[str, Any]) -> tuple[str, str]:
    """返回 (正文, 思维链)。字段口径对齐 Hermes 的 extract_reasoning：
    reasoning / reasoning_content（含类型化内容块）→ 内联  thinking 块兜底。"""
    parts: list[str] = []
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    content = message.get("content")
    if isinstance(content, list):  # 少见：类型化内容块（thinking/text 块）
        texts: list[str] = []
        thinks: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                think = str(block.get("thinking") or block.get("text") or "").strip()
                if think:
                    thinks.append(think)
            elif isinstance(block, dict):
                texts.append(str(block.get("text") or block.get("content") or ""))
            elif isinstance(block, str):
                texts.append(block)
        content = "".join(texts)
        parts = thinks + parts
    text = content if isinstance(content, str) else ""
    inline = [m.group(1).strip() for m in _INLINE_THINK_RE.finditer(text)]
    if inline:
        text = _INLINE_THINK_RE.sub("", text)
        if not parts:
            parts = [p for p in inline if p]
    return text.strip(), "\n\n".join(dict.fromkeys(p for p in parts if p))


async def chat_once(
    text: str,
    provider: Provider,
    *,
    session_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Completion:
    """向单个后端发一次请求；解析正文与思维链。空正文抛 EmptyReplyError。"""
    cfg = get_config()
    url = provider.base_url.rstrip("/") + "/chat/completions"
    messages: list[dict[str, Any]] = []
    if cfg.llm_system_prompt:
        messages.append({"role": "system", "content": cfg.llm_system_prompt})
    messages.append({"role": "user", "content": text})
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "max_tokens": cfg.llm_max_tokens,
        "stream": False,
    }
    payload.update(provider.extra_payload)
    async with httpx.AsyncClient(timeout=cfg.llm_timeout, transport=transport) as client:
        resp = await client.post(url, headers=_build_headers(provider, session_key), json=payload)
        resp.raise_for_status()
        data = resp.json()
    choice = data["choices"][0]
    finish_reason = choice.get("finish_reason")
    body, reasoning = _split_completion(choice.get("message") or {})
    if not body:
        raise EmptyReplyError(f"空回复（finish_reason={finish_reason}，思考 {len(reasoning)} 字）")
    return Completion(text=body, reasoning=reasoning, truncated=finish_reason == "length")


async def chat(
    text: str,
    *,
    chain: Sequence[str] | None = None,
    session_key: str | None = None,
) -> Reply:
    """按 主选 → 回退链 依次尝试，返回第一个成功的结果；全失败则抛出最后一个异常。"""
    cfg = get_config()
    if chain is None:
        chain = [current_default(), *cfg.llm_fallbacks]
    names: list[str] = []
    for name in chain:
        if name in cfg.providers and name not in names:
            names.append(name)
    last_exc: Exception | None = None
    for name in names:
        try:
            completion = await chat_once(text, cfg.providers[name], session_key=session_key)
            return Reply(
                provider=name,
                text=completion.text,
                reasoning=completion.reasoning,
                truncated=completion.truncated,
            )
        except Exception as exc:  # noqa: BLE001 —— 回退链需要吞掉单个后端的错误
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise LookupError("没有可用的 LLM 提供商（检查 bot/.env 的 LLM_PROVIDER/LLM_FALLBACKS）")
