"""BreezeBlue 按需语音回复插件。

工作方式：

1. 模型在需要语音时调用工具 ``breeze_voice_say``，为当前聊天流登记一次语音许可；
2. 宿主准备发送这条回复时，``send_service.before_send`` 钩子把纯文本回复替换成
   BreezeBlue 合成好的语音段（base64 内联，不依赖跨容器共享文件）；
3. 合成失败、超长、超限或未授权时原样发送文字，不重复发送。

只会读取最终对外的纯文本回复，不接触系统提示词、思维链或工具结果。
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, cast
from zoneinfo import ZoneInfo

from maibot_sdk import HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .breeze import BreezeTTSError, synthesize
from .config import BreezeVoiceConfig
from .text import is_speakable, plain_text_message, sanitize_for_tts

_VOICE_SEGMENT_TYPE = "voice"
_VOICE_REQUEST_TTL_SEC = 60.0
_TTL_CACHE_MAX_ENTRIES = 512
_WARN_INTERVAL_SEC = 60.0
_TZ = ZoneInfo("Asia/Shanghai")


class BreezeVoicePlugin(MaiBotPlugin):
    """把「模型明确要求」的纯文本回复替换成 BreezeBlue 语音条。"""

    config_model = BreezeVoiceConfig

    @property
    def config(self) -> "BreezeVoiceConfig":
        instance = self._plugin_config_instance
        if instance is None:
            raise RuntimeError("插件配置尚未完成注入")
        return cast("BreezeVoiceConfig", instance)

    def __init__(self) -> None:
        super().__init__()
        self._voice_requests: Dict[str, float] = {}
        self._random_decisions: Dict[str, tuple[float, bool]] = {}
        self._last_voice_at: Dict[str, float] = {}
        self._warned_at: Dict[str, float] = {}
        self._lock: Optional[asyncio.Lock] = None

    # --------------------------------------------------------------- 生命周期

    async def on_load(self) -> None:
        """插件加载时检查配置并打印启用状态（不显示任何密钥内容）。"""
        cfg = self.config
        self._log_startup_status()
        if cfg.plugin.enabled and not self._api_key():
            self.ctx.logger.warning(
                "BreezeBlue 语音已启用但尚未配置 API Key：请到插件配置页填写，"
                "在此之前模型请求语音会自动回退为文字"
            )

    async def on_unload(self) -> None:
        """插件卸载时清理内存状态。"""
        self._voice_requests.clear()
        self._random_decisions.clear()
        self._last_voice_at.clear()

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        """配置热更新后重新注入设置。"""
        if scope != "self":
            return
        self.set_plugin_config(config_data)
        if version:
            self.ctx.logger.debug(f"BreezeBlue 语音收到配置更新: {version}")
        self._log_startup_status()

    # ------------------------------------------------------------ 模型工具

    @Tool(
        "breeze_voice_say",
        description=(
            "让这一轮的回复用 BreezeBlue 语音说出来。"
            "只在对方明确想听声音、或这句话用语音更自然时调用；"
            "调用后正常生成回复即可，内容会自动转成语音发出。"
            "代码、长说明、链接和需要逐字阅读的内容不要调用。"
        ),
        visibility="visible",
        parameters={"type": "object", "properties": {}},
    )
    async def request_voice_reply(self, **kwargs: Any) -> Dict[str, Any]:
        """登记一次语音许可，供本轮出站回复使用。"""
        stream_id = self._stream_id_from_kwargs(kwargs)
        if not stream_id:
            return {"success": False, "content": "无法获取当前聊天流，本轮只能用文字回复。"}
        if not self.config.plugin.enabled:
            return {"success": False, "content": "BreezeBlue 语音已在配置中停用，本轮只能用文字回复。"}
        if not self._api_key():
            return {
                "success": False,
                "content": "BreezeBlue 语音尚未配置 API Key，本轮只能用文字回复。",
            }
        now = time.monotonic()
        self._voice_requests[stream_id] = now + _VOICE_REQUEST_TTL_SEC
        self._prune_ttl_cache(self._voice_requests, now)
        return {
            "success": True,
            "content": "这一轮会尝试用语音回复；若当前账号或适配器不支持语音，会自动改用文字。",
        }

    # --------------------------------------------------------------- 出站 Hook

    @HookHandler(
        "send_service.before_send",
        name="breeze_voice_before_send",
        description="按配置概率或模型请求把纯文本回复替换为 BreezeBlue 语音，失败时按配置回退文字",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        # 合成超时最长可配到 120s，钩子必须比它更宽；超时后宿主会按文字原样发送。
        timeout_ms=130000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def convert_outbound_text(
        self,
        message: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """按配置的触发模式把出站纯文本回复替换成语音段。"""
        cfg = self.config
        if not cfg.plugin.enabled or not isinstance(message, dict):
            return None

        outbound = copy.deepcopy(message)
        text = plain_text_message(outbound)
        if text is None:
            return None
        stream_id = self._stream_id_from_message(outbound, kwargs)
        if not stream_id:
            return None

        requested = self._take_voice_request(stream_id)
        if not requested and cfg.trigger.mode != "random_llm":
            return None

        # 到这里本次语音许可已被消费：无论后面成功还是回退，都不会再影响下一条回复。
        if not self._api_key():
            self._warn_once("no-key", "BreezeBlue API Key 为空，本条按文字发送")
            return None

        account_id = str(outbound.get("account_id") or "").strip()
        if not self._account_allowed(account_id):
            self.ctx.logger.info(
                "BreezeBlue 语音跳过：机器人账号 %s 不在 allowed_account_ids 中"
                "（QQ 官方适配器不支持 voice 段，已回退文字）",
                account_id or "未知",
            )
            return None

        scope, group_id = self._scope_of(outbound)
        if not self._scope_allowed(scope, group_id):
            self.ctx.logger.info("BreezeBlue 语音跳过：当前会话类型或群号不在允许范围")
            return None

        cleaned = sanitize_for_tts(text)
        max_length = (
            int(cfg.trigger.max_text_length)
            if requested
            else int(cfg.trigger.short_reply_max_chars)
        )
        if cleaned is None or not is_speakable(
            cleaned,
            min_length=int(cfg.trigger.min_text_length),
            max_length=max_length,
        ):
            self.ctx.logger.info(
                "BreezeBlue 语音跳过：文本不适合合成（原 %d 字，清洗后 %s）",
                len(text),
                f"{len(cleaned)} 字" if cleaned else "空",
            )
            return None

        if not self._quota_available(len(cleaned)):
            self.ctx.logger.warning("BreezeBlue 语音跳过：今日条数或字数已达上限")
            return None

        if self._in_cooldown(stream_id):
            self.ctx.logger.info("BreezeBlue 语音跳过：同一聊天流仍在冷却时间内")
            return None

        if not requested and not self._random_voice_allowed(stream_id):
            self.ctx.logger.debug("BreezeBlue 语音跳过：本轮随机判定为文字")
            return None
        # 本轮已经决定发语音（随机命中或模型请求）：抑制同一轮后续句子的随机判定，
        # 避免一条长回复被拆成多条语音。
        self._suppress_random_voice(stream_id)

        started = time.monotonic()
        try:
            audio = await self._synthesize_with_lock(cleaned)
        except BreezeTTSError as exc:
            self._log_synthesis_error(exc)
            if not cfg.delivery.fallback_to_text:
                return {"action": "abort"}
            return None

        self._record_usage(len(cleaned))
        self._last_voice_at[stream_id] = time.monotonic()

        outbound["raw_message"] = [
            {
                "type": _VOICE_SEGMENT_TYPE,
                "data": "",
                "hash": hashlib.sha256(audio).hexdigest(),
                "binary_data_base64": base64.b64encode(audio).decode("ascii"),
            }
        ]
        outbound["processed_plain_text"] = text
        if cfg.delivery.remove_reply:
            had_reply = bool(kwargs.get("set_reply")) or bool(outbound.get("reply_to"))
            outbound["reply_to"] = None
            kwargs["set_reply"] = False
            kwargs["reply_message_id"] = ""
            if had_reply:
                self.ctx.logger.info("QQ 语音不支持引用组合，已移除本条的引用段")

        kwargs["message"] = outbound
        self.ctx.logger.info(
            "BreezeBlue 语音已生成：%d 字，%.1fs，%d 字节，音色=%s",
            len(cleaned),
            time.monotonic() - started,
            len(audio),
            cfg.breeze.voice_name or cfg.breeze.voice_id,
        )
        return {"action": "continue", "modified_kwargs": kwargs}

    # --------------------------------------------------------------- 合成

    async def _synthesize_with_lock(self, text: str) -> bytes:
        if self._lock is None:
            self._lock = asyncio.Lock()
        cfg = self.config
        async with self._lock:
            return await synthesize(
                api_base_url=cfg.breeze.api_base_url,
                api_key=self._api_key(),
                voice_id=cfg.breeze.voice_id,
                text=text,
                model_id=cfg.breeze.model_id,
                language_code=cfg.breeze.language_code,
                output_format=cfg.breeze.output_format,
                instructions=cfg.breeze.instructions,
                speed=float(cfg.breeze.speed),
                timeout_sec=float(cfg.breeze.timeout_sec),
            )

    def _log_synthesis_error(self, exc: BreezeTTSError) -> None:
        if exc.key_rejected:
            self._warn_once(
                f"http-{exc.status}",
                "BreezeBlue API Key 无效或无权限（HTTP %s），请到插件配置更新；本条按文字发送",
                exc.status,
            )
        elif exc.quota_exhausted:
            self._warn_once(
                "quota",
                "BreezeBlue 额度不足（HTTP 402），本条按文字发送；请检查账户余额或 Key 的额度上限",
            )
        elif exc.retryable:
            self._warn_once(
                f"retry-{exc.status}",
                "BreezeBlue 服务暂时不可用（HTTP %s），本条按文字发送且不重试，避免重复计费",
                exc.status,
            )
        else:
            self._warn_once(f"err-{type(exc).__name__}", "BreezeBlue 合成失败：%s；本条按文字发送", exc)

    # --------------------------------------------------------------- 用量

    def _api_key(self) -> str:
        """优先读取插件配置，其次读取环境变量；只返回值，不打印。"""
        configured = str(self.config.breeze.api_key or "").strip()
        if configured:
            return configured
        return os.environ.get("BREEZE_API_KEY", "").strip()

    def _usage_path(self) -> Path:
        return Path(self.ctx.paths.data_dir) / "usage.json"

    def _load_usage(self) -> Dict[str, Any]:
        path = self._usage_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _today(self) -> str:
        return datetime.now(_TZ).date().isoformat()

    def _current_usage(self) -> Dict[str, Any]:
        today = self._today()
        usage = self._load_usage()
        if str(usage.get("date") or "") != today:
            return {"date": today, "requests": 0, "chars": 0}
        return {
            "date": today,
            "requests": int(usage.get("requests") or 0),
            "chars": int(usage.get("chars") or 0),
        }

    def _quota_available(self, text_length: int) -> bool:
        cfg = self.config.trigger
        usage = self._current_usage()
        if int(cfg.daily_limit) > 0 and int(usage["requests"]) >= int(cfg.daily_limit):
            return False
        if int(cfg.daily_char_limit) > 0 and int(usage["chars"]) + text_length > int(cfg.daily_char_limit):
            return False
        return True

    def _record_usage(self, text_length: int) -> None:
        usage = self._current_usage()
        usage["requests"] = int(usage["requests"]) + 1
        usage["chars"] = int(usage["chars"]) + text_length
        path = self._usage_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(usage, ensure_ascii=False), encoding="utf-8")
            temp_path.replace(path)
        except OSError as exc:
            self.ctx.logger.warning("BreezeBlue 用量记录写入失败（不影响发送）：%s", exc)

    # --------------------------------------------------------------- 过滤与状态

    def _scope_of(self, message: Dict[str, Any]) -> tuple[str, str]:
        scope = str(message.get("scope") or "").strip()
        info = message.get("message_info")
        info = info if isinstance(info, dict) else {}
        group_info = info.get("group_info")
        group_info = group_info if isinstance(group_info, dict) else {}
        group_id = str(group_info.get("group_id") or "").strip()
        if not scope:
            scope = "group" if group_id else "private"
        return scope, group_id

    def _scope_allowed(self, scope: str, group_id: str) -> bool:
        cfg = self.config.trigger
        if scope == "group":
            if not cfg.group_enabled:
                return False
            allowed = [str(item).strip() for item in cfg.allowed_group_ids if str(item).strip()]
            return not allowed or group_id in allowed
        if scope == "private":
            return bool(cfg.private_enabled)
        return False

    def _account_allowed(self, account_id: str) -> bool:
        """只允许 SnowLuma 这类支持 voice 段的账号发语音。

        QQ 官方适配器会把 voice 段直接判为发送失败，整条回复都会丢失；
        在出站前按 account_id 拦掉，改走文字。
        """
        allowed = [str(item).strip() for item in self.config.trigger.allowed_account_ids if str(item).strip()]
        if not allowed:
            self._warn_once(
                "no-account-limit",
                "BreezeBlue 未限制 allowed_account_ids：QQ 官方适配器收到 voice 段会发送失败，"
                "建议只填 SnowLuma 的机器人账号",
            )
            return True
        return bool(account_id) and account_id in allowed

    def _stream_id_from_kwargs(self, kwargs: Dict[str, Any]) -> str:
        return str(kwargs.get("stream_id") or kwargs.get("session_id") or "").strip()

    def _stream_id_from_message(self, message: Dict[str, Any], kwargs: Dict[str, Any]) -> str:
        return str(
            kwargs.get("stream_id") or message.get("session_id") or ""
        ).strip()

    def _take_voice_request(self, stream_id: str) -> bool:
        expires_at = self._voice_requests.pop(stream_id, 0.0)
        return expires_at > time.monotonic()

    def _random_voice_allowed(self, stream_id: str) -> bool:
        """按聊天流做一次随机判定，并在 decision_ttl 内复用结果。"""
        now = time.monotonic()
        entry = self._random_decisions.get(stream_id)
        if entry is not None and entry[0] >= now:
            return entry[1]
        allowed = random.random() < float(self.config.trigger.probability)
        self._random_decisions[stream_id] = (
            now + float(self.config.trigger.decision_ttl),
            allowed,
        )
        if len(self._random_decisions) > _TTL_CACHE_MAX_ENTRIES:
            self._random_decisions = {
                key: value for key, value in self._random_decisions.items() if value[0] >= now
            }
            if len(self._random_decisions) > _TTL_CACHE_MAX_ENTRIES:
                self._random_decisions.clear()
        self.ctx.logger.debug(
            "BreezeBlue 随机语音判定: %s（概率 %.0f%%）",
            "语音" if allowed else "文字",
            float(self.config.trigger.probability) * 100,
        )
        return allowed

    def _suppress_random_voice(self, stream_id: str) -> None:
        """本轮已发语音后，抑制同一聊天流后续句子的随机命中。"""
        ttl = float(self.config.trigger.decision_ttl)
        self._random_decisions[stream_id] = (time.monotonic() + ttl, False)

    def _in_cooldown(self, stream_id: str) -> bool:
        last = self._last_voice_at.get(stream_id, 0.0)
        return last > 0 and (time.monotonic() - last) < float(self.config.trigger.cooldown_sec)

    def _prune_ttl_cache(self, cache: Dict[str, float], now: float) -> None:
        if len(cache) <= _TTL_CACHE_MAX_ENTRIES:
            return
        expired = [key for key, expires_at in cache.items() if expires_at <= now]
        for key in expired:
            cache.pop(key, None)
        if len(cache) > _TTL_CACHE_MAX_ENTRIES:
            cache.clear()

    def _warn_once(self, key: str, message: str, *args: Any) -> None:
        now = time.monotonic()
        if now - self._warned_at.get(key, 0.0) < _WARN_INTERVAL_SEC:
            return
        self._warned_at[key] = now
        self.ctx.logger.warning(message, *args)

    def _log_startup_status(self) -> None:
        cfg = self.config
        if not cfg.plugin.enabled:
            self.ctx.logger.info("BreezeBlue 语音已停用（plugin.enabled=false）")
            return
        key_state = "已配置" if self._api_key() else "未配置"
        mode_desc = (
            "仅模型请求"
            if cfg.trigger.mode == "on_demand"
            else f"随机 {float(cfg.trigger.probability) * 100:.0f}% + 模型请求"
        )
        self.ctx.logger.info(
            "BreezeBlue 语音已启用：音色=%s(%s)，API Key=%s，触发=%s，"
            "上限=%s 条/%s 字每日",
            cfg.breeze.voice_name or "未命名",
            cfg.breeze.voice_id,
            key_state,
            mode_desc,
            cfg.trigger.daily_limit or "不限",
            cfg.trigger.daily_char_limit or "不限",
        )


def create_plugin() -> BreezeVoicePlugin:
    """插件工厂。"""
    return BreezeVoicePlugin()
