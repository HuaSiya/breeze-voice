"""朗读文本提取与清洗。"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

_CQ_PATTERN = re.compile(r"\[CQ:[^\]]+\]")
_URL_PATTERN = re.compile(r"https?://\S+")
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_LATIN_PATTERN = re.compile(r"[A-Za-z]")
_CODE_PATTERN = re.compile(r"```|`[^`\n]{1,80}`")
_EMOJI_PATTERN = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\u2190-\u21FF\u2B00-\u2BFF\ufe0f\u200d]"
)
_LEAD_NOISE_PATTERN = re.compile(r"^[\s'\"“”‘’「」『』（）()\[\]【】《》<>~～\-—*_`#>、，,。]+")
_TAIL_NOISE_PATTERN = re.compile(r"[\s'\"“”‘’「」『』（）()\[\]【】《》<>*_`~～\-—、，,]+$")
_SENTENCE_END = "。！？!?"


def plain_text_message(message: Dict[str, Any]) -> Optional[str]:
    """取出纯文本内容。

    只接受 text(+reply) 组合，且必须恰好一个 text 段；图片、语音、文件等
    混合消息保持原样，不做语音替换。
    """
    raw = message.get("raw_message")
    if not isinstance(raw, list) or not raw:
        return None
    segments = [item for item in raw if isinstance(item, dict)]
    types = [str(segment.get("type") or "") for segment in segments]
    if any(kind not in {"text", "reply"} for kind in types) or types.count("text") != 1:
        return None
    segment = segments[types.index("text")]
    data = segment.get("data")
    if isinstance(data, str):
        text = data
    elif isinstance(data, dict):
        text = data.get("text")
    else:
        text = None
    if not isinstance(text, str):
        return None
    text = _CQ_PATTERN.sub("", text).strip()
    return text or None


def sanitize_for_tts(text: str) -> Optional[str]:
    """清洗成适合朗读的一句普通话。

    去掉 URL、CQ 码、表情、引号与零宽字符，折叠重复标点，补句末标点。
    清洗后没有中文或只剩符号时返回 None。
    """
    cleaned = _URL_PATTERN.sub("", text)
    cleaned = _CQ_PATTERN.sub("", cleaned)
    cleaned = _EMOJI_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]", "", cleaned)
    cleaned = re.sub(r"[「」『』“”‘’\"'（）()【】\[\]《》〈〉]", "", cleaned)
    cleaned = cleaned.replace("\u3000", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff，。！？])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = _LEAD_NOISE_PATTERN.sub("", cleaned)
    cleaned = _TAIL_NOISE_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[~～]+", "，", cleaned)
    cleaned = re.sub(r"(…+|\.{3,}|。{2,})", "，", cleaned)
    cleaned = re.sub(r"[!！]{2,}", "！", cleaned)
    cleaned = re.sub(r"[?？]{2,}", "？", cleaned)
    cleaned = re.sub(r"[，,、]{2,}", "，", cleaned)
    cleaned = re.sub(r"，+([。！？])", r"\1", cleaned)
    cleaned = cleaned.strip("，,、 ")
    if not cleaned or not _CJK_PATTERN.search(cleaned):
        return None
    if cleaned[-1] not in _SENTENCE_END:
        cleaned += "。"
    return cleaned


def is_speakable(text: str, *, min_length: int, max_length: int) -> bool:
    """判断清洗后的文本是否适合合成。"""
    length = len(text)
    if length < min_length or length > max_length:
        return False
    if _CODE_PATTERN.search(text):
        return False
    # 纯英文、网址、代码之类不适合用普通话音色朗读；少量英文词可以保留。
    if len(_LATIN_PATTERN.findall(text)) > len(_CJK_PATTERN.findall(text)):
        return False
    return True
