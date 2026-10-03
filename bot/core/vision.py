"""图片感知管道：下载（即收即下，QQ CDN 链接会过期）→ md5 去重 → VLM 描述 → 缓存。

- 云 vision（opencode-go 中转的 deepseek vision 模型）为主；本地 MiniCPM-V 兜底。
- 描述写入 core.context 的 images 表（caption 缓存 + 贴图库底账），并按 token 记账。
- 任何失败都返回 ""，调用方保持 "[图片]" 占位，不阻塞聊天。
"""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path

import httpx

from . import budget, context
from .config import Config

_log = logging.getLogger("qqbot.vision")

_IMAGES_DIR = Path(__file__).resolve().parents[1] / "data" / "images"
_MAX_BYTES = 10 * 1024 * 1024
_transport: httpx.AsyncBaseTransport | None = None  # 测试注入用

_CAPTION_PROMPT = (
    "用一两句话描述这张图片的内容。若是表情包或梗图，说明它表达的情绪或梗；"
    "如果图中有文字，把文字读出来。只输出描述本身，不要客套。"
)


def _sniff_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


async def _download(url: str, timeout: float) -> bytes:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=_transport) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    if not data or len(data) > _MAX_BYTES:
        raise ValueError(f"图片为空或过大（{len(data)} bytes）")
    return data


def _payload(data: bytes, model: str) -> dict:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _CAPTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{_sniff_mime(data)};base64,{base64.b64encode(data).decode()}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": 800,  # vision 模型先思考（reasoning）再输出，预算给小了正文会被挤空
    }


async def _caption_cloud(data: bytes, cfg: Config) -> str:
    provider = cfg.providers.get("opencode_go")
    if provider is None or not provider.api_key:
        return ""
    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "x-opencode-session": "qqbot-vision",
    }
    async with httpx.AsyncClient(timeout=cfg.vision_timeout, transport=_transport) as client:
        resp = await client.post(url, headers=headers, json=_payload(data, cfg.vision_model))
        resp.raise_for_status()
        result = resp.json()
    total = int((result.get("usage") or {}).get("total_tokens") or 0)
    if total > 0:
        budget.record("vision", total)
    return str((result["choices"][0].get("message") or {}).get("content") or "").strip()


async def _caption_local(data: bytes, cfg: Config) -> str:
    if not cfg.vision_local_url:
        return ""
    url = cfg.vision_local_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=cfg.vision_timeout, transport=_transport) as client:
        resp = await client.post(url, json=_payload(data, "minicpm-v"))
        resp.raise_for_status()
        result = resp.json()
    return str((result["choices"][0].get("message") or {}).get("content") or "").strip()


async def describe(url: str, cfg: Config) -> str:
    """下载并生成图片描述；失败返回 ""。已识别过的图片走 md5 缓存（零成本）。"""
    if not cfg.vision_enabled or not url:
        return ""
    try:
        data = await _download(url, cfg.vision_timeout)
    except Exception as exc:  # noqa: BLE001 —— 链接过期/网络失败：放弃，保持占位
        _log.warning("vision download failed: %s", exc)
        return ""
    md5 = hashlib.md5(data).hexdigest()
    cached = context.image_get(md5)
    if cached and cached.get("caption"):
        context.image_touch(md5)
        return str(cached["caption"])
    if budget.exhausted():
        return ""
    caption, model = "", ""
    try:
        caption = await _caption_cloud(data, cfg)
        model = cfg.vision_model
    except Exception:  # noqa: BLE001
        caption = ""
    if not caption:
        try:
            caption = await _caption_local(data, cfg)
            model = "minicpm-v(local)"
        except Exception:  # noqa: BLE001
            caption = ""
    if not caption:
        _log.warning("vision caption empty (cloud+local) for %s", url[:120])
    path = ""
    try:
        _IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        ext = {"image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(_sniff_mime(data), ".jpg")
        f = _IMAGES_DIR / f"{md5}{ext}"
        if not f.exists():
            f.write_bytes(data)
        path = str(f)
    except Exception:  # noqa: BLE001
        path = ""
    context.image_upsert(md5, path, caption, model)
    return caption
