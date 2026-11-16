"""BreezeBlue 文本转语音客户端。

官方接口与 OpenAI TTS 不兼容，这里按 BreezeBlue 文档实现：

* ``POST {base_url}/v1/text-to-speech/{voice_id}?output_format=mp3``
* 鉴权头 ``xi-api-key``
* 同步请求直接返回音频二进制

错误信息只保留状态码与响应片段，绝不回显 API Key。
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp

_MIN_AUDIO_BYTES = 600
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class BreezeTTSError(RuntimeError):
    """BreezeBlue 合成失败。"""

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status in _RETRYABLE_STATUS

    @property
    def key_rejected(self) -> bool:
        return self.status in {401, 403}

    @property
    def quota_exhausted(self) -> bool:
        return self.status == 402


def _looks_like_audio(data: bytes, output_format: str) -> bool:
    if len(data) < _MIN_AUDIO_BYTES:
        return False
    head = data[:4]
    if output_format == "wav":
        return head[:4] == b"RIFF"
    # MP3: ID3 标签，或 MPEG 帧同步（0xFFEx/0xFFFx）
    return head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)


async def synthesize(
    *,
    api_base_url: str,
    api_key: str,
    voice_id: str,
    text: str,
    model_id: str = "",
    language_code: str = "zh",
    output_format: str = "mp3",
    instructions: str = "",
    speed: float = 1.0,
    timeout_sec: float = 30.0,
) -> bytes:
    """调用 BreezeBlue 同步 TTS 接口并返回音频二进制。

    Raises:
        BreezeTTSError: 配置缺失、鉴权失败、额度不足、服务端错误或返回内容不是音频。
    """
    base_url = (api_base_url or "https://api.breeze.blue").rstrip("/")
    if not api_key.strip():
        raise BreezeTTSError("未配置 BreezeBlue API Key")
    if not voice_id.strip():
        raise BreezeTTSError("未配置音色 Voice ID")

    url = f"{base_url}/v1/text-to-speech/{quote(voice_id.strip(), safe='')}"
    params = {"output_format": "wav" if output_format == "wav" else "mp3"}
    payload: Dict[str, Any] = {
        "text": text,
        "language_code": language_code or "zh",
        "voice_settings": {
            "speed": float(speed),
            "volume": 1.0,
        },
    }
    if model_id.strip():
        payload["model_id"] = model_id.strip()
    if instructions.strip():
        payload["instructions"] = instructions.strip()

    headers = {
        "xi-api-key": api_key.strip(),
        "content-type": "application/json",
        "accept": "audio/mpeg" if params["output_format"] == "mp3" else "audio/wav",
    }
    timeout = aiohttp.ClientTimeout(total=timeout_sec, connect=min(10.0, timeout_sec))

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, params=params, json=payload, headers=headers) as response:
                body = await response.read()
                if response.status != 200:
                    detail = body[:300].decode("utf-8", errors="replace").replace("\n", " ").strip()
                    raise BreezeTTSError(
                        f"BreezeBlue 返回 HTTP {response.status}: {detail or '无响应内容'}",
                        status=response.status,
                    )
    except aiohttp.ClientError as exc:
        # 连接失败或读到一半断开：请求是否已计费未知，上层按失败回退文字，不重试。
        raise BreezeTTSError(f"BreezeBlue 连接失败: {type(exc).__name__}") from exc
    except TimeoutError as exc:
        raise BreezeTTSError("BreezeBlue 合成超时，结果未知，不自动重试") from exc

    if not _looks_like_audio(body, params["output_format"]):
        raise BreezeTTSError(f"BreezeBlue 返回的内容不是有效音频（{len(body)} 字节）")
    return body
