"""读取 .env / 环境变量，转换为强类型配置对象（含三 LLM 后端与思考参数）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

_DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_ON_VALUES = {"1", "true", "yes", "on"}
_EFFORTS = ("low", "medium", "high", "max")
_REPLY_MODES = ("command", "mention", "all")


def _read_dotenv(path: Path) -> dict[str, str]:
    """极简 dotenv 解析：KEY=VALUE，# 开头为注释。"""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def _get_key(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name, "").strip()
        if value:
            return value
    return ""


def _is_on(env: Mapping[str, str], key: str, default: str) -> bool:
    return env.get(key, default).strip().lower() in _ON_VALUES


def _norm_effort(raw: str, default: str = "high") -> str:
    value = raw.strip().lower()
    if value == "xhigh":
        return "max"
    return value if value in _EFFORTS else default


@dataclass
class Provider:
    name: str
    base_url: str
    model: str
    api_key: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_payload: dict[str, Any] = field(default_factory=dict)
    max_output: int | None = None  # 该后端允许的输出上限（可选钳制）
    supports_tools: bool = False  # 是否支持 function calling（web_search 工具）


_PROVIDER_ALIASES = {
    "local": "local",
    "opencode_go": "opencode_go",
    "opencode-go": "opencode_go",
    "opencode": "opencode_go",
    "go": "opencode_go",
}


def normalize_provider_name(raw: str) -> str | None:
    return _PROVIDER_ALIASES.get(raw.strip().lower())



def _opencode_payload(env: Mapping[str, str]) -> dict[str, Any]:
    if not _is_on(env, "LLM_OPENCODE_GO_THINKING", "on"):
        return {"thinking": {"type": "disabled"}}
    effort = _norm_effort(env.get("LLM_OPENCODE_GO_REASONING_EFFORT", "high"))
    # Go 中转对 deepseek-v* 型号：只发顶层 reasoning_effort（与 Hermes opencode-go 插件实际做法一致）
    return {"reasoning_effort": effort}


def _opt_int(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name, "").strip()
    return int(raw) if raw.isdigit() else None


def _build_providers(env: Mapping[str, str]) -> dict[str, Provider]:
    return {
        "local": Provider(
            name="local",
            base_url=env.get("LLM_LOCAL_BASE_URL", "http://127.0.0.1:8080/v1"),
            model=env.get("LLM_LOCAL_MODEL", "qwen"),
            api_key=_get_key(env, "LLM_LOCAL_API_KEY"),
            max_output=_opt_int(env, "LLM_LOCAL_MAX_OUTPUT"),
            supports_tools=_is_on(env, "LLM_LOCAL_TOOLS", "0"),
        ),
        "opencode_go": Provider(
            name="opencode_go",
            base_url=env.get("LLM_OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1"),
            model=env.get("LLM_OPENCODE_GO_MODEL", "deepseek-v4.1-flash"),
            api_key=_get_key(env, "LLM_OPENCODE_GO_API_KEY", "OPENCODE_GO_API_KEY", "OPENCODE_API_KEY"),
            extra_payload=_opencode_payload(env),
            max_output=_opt_int(env, "LLM_OPENCODE_GO_MAX_OUTPUT"),
            supports_tools=_is_on(env, "LLM_OPENCODE_GO_TOOLS", "1"),
        ),
    }


def _parse_provider_list(raw: str, known: set[str]) -> list[str]:
    names: list[str] = []
    for part in raw.split(","):
        name = normalize_provider_name(part)
        if name and name in known and name not in names:
            names.append(name)
    return names


@dataclass
class Config:
    allowed_group_ids: set[int] = field(default_factory=set)
    bot_name: str = "十九"
    llm_enabled: bool = False
    llm_provider: str = "local"
    llm_fallbacks: list[str] = field(default_factory=list)
    llm_reply_mode: str = "all"
    llm_system_prompt: str = ""
    llm_max_tokens: int = 2000
    llm_daily_token_limit: int = 10_000_000
    llm_quota_reply: str = "白饭吃完了QAQ"
    llm_tool_max_rounds: int = 3
    llm_cooldown: float = 5.0
    llm_timeout: float = 180.0
    llm_show_provider: bool = False
    llm_show_reasoning: bool = False
    usage_file: str = "logs/token-usage.json"
    search_enabled: bool = True
    search_api_key: str = ""
    search_base_url: str = "https://api.firecrawl.dev"
    search_max_results: int = 5
    search_timeout: float = 30.0
    llm_context_messages: int = 12
    vision_enabled: bool = True
    vision_model: str = "deepseek-v4-flash-vision-exp"
    vision_local_url: str = "http://127.0.0.1:8082/v1"
    vision_timeout: float = 60.0
    sticker_enabled: bool = True
    providers: dict[str, Provider] = field(default_factory=dict)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        merged: dict[str, str] = {**_read_dotenv(_DOTENV_PATH), **os.environ}
        env = merged
    providers = _build_providers(env)
    primary = normalize_provider_name(env.get("LLM_PROVIDER", "local")) or "local"
    if primary not in providers:
        primary = "local"
    fallbacks = [
        name for name in _parse_provider_list(env.get("LLM_FALLBACKS", ""), set(providers)) if name != primary
    ]
    reply_mode = env.get("LLM_REPLY_MODE", "mention").strip().lower()
    if reply_mode not in _REPLY_MODES:
        reply_mode = "mention"
    # 人设（system prompt）：LLM_SYSTEM_PROMPT 直写（支持 \n 转义）优先；
    # 否则读取 LLM_PERSONA_FILE 指向的人设文件（相对路径基于 bot/ 目录）
    system_prompt = env.get("LLM_SYSTEM_PROMPT", "").strip().replace("\\n", "\n")
    if not system_prompt:
        persona_file = env.get("LLM_PERSONA_FILE", "").strip()
        if persona_file:
            persona_path = Path(persona_file)
            if not persona_path.is_absolute():
                persona_path = _DOTENV_PATH.parent / persona_path
            try:
                if persona_path.is_file():
                    system_prompt = persona_path.read_text(encoding="utf-8").strip()
            except OSError:
                system_prompt = ""
    return Config(
        allowed_group_ids=_parse_ids(env.get("ALLOWED_GROUP_IDS", "")),
        bot_name=env.get("BOT_NAME", "十九"),
        llm_enabled=env.get("LLM_ENABLED", "0").strip().lower() in _ON_VALUES,
        llm_provider=primary,
        llm_fallbacks=fallbacks,
        llm_reply_mode=reply_mode,
        llm_system_prompt=system_prompt,
        llm_max_tokens=int(env.get("LLM_MAX_TOKENS", "2000")),
        llm_daily_token_limit=int(env.get("LLM_DAILY_TOKEN_LIMIT", "10000000")),
        llm_quota_reply=env.get("LLM_QUOTA_REPLY", "白饭吃完了QAQ").strip() or "白饭吃完了QAQ",
        llm_tool_max_rounds=max(1, int(env.get("LLM_TOOL_MAX_ROUNDS", "3"))),
        llm_cooldown=float(env.get("LLM_COOLDOWN", "5")),
        llm_timeout=float(env.get("LLM_TIMEOUT", "180")),
        llm_show_provider=_is_on(env, "LLM_SHOW_PROVIDER", "0"),
        llm_show_reasoning=_is_on(env, "LLM_SHOW_REASONING", "0"),
        usage_file=env.get("USAGE_FILE", "logs/token-usage.json").strip() or "logs/token-usage.json",
        search_enabled=_is_on(env, "SEARCH_ENABLED", "1"),
        search_api_key=_get_key(env, "SEARCH_API_KEY", "FIRECRAWL_API_KEY"),
        search_base_url=env.get("SEARCH_BASE_URL", "https://api.firecrawl.dev"),
        search_max_results=int(env.get("SEARCH_MAX_RESULTS", "5")),
        search_timeout=float(env.get("SEARCH_TIMEOUT", "30")),
        llm_context_messages=max(0, int(env.get("LLM_CONTEXT_MESSAGES", "12"))),
        vision_enabled=_is_on(env, "VISION_ENABLED", "1"),
        vision_model=env.get("VISION_MODEL", "deepseek-v4-flash-vision-exp").strip()
        or "deepseek-v4-flash-vision-exp",
        vision_local_url=env.get("VISION_LOCAL_URL", "http://127.0.0.1:8082/v1").strip(),
        vision_timeout=float(env.get("VISION_TIMEOUT", "60")),
        sticker_enabled=_is_on(env, "STICKER_ENABLED", "1"),
        providers=providers,
    )


_config: Config | None = None


def get_config() -> Config:
    """进程级单例；首次调用时读取 .env。"""
    global _config
    if _config is None:
        _config = load_config()
    return _config
