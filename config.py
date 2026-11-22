"""BreezeBlue 语音插件配置模型。"""

from __future__ import annotations

from typing import List, Literal

from maibot_sdk import Field, PluginConfigBase

SUPPORTED_CONFIG_VERSION = "1.1.0"
DEFAULT_VOICE_ID = "voc_u3nxkd5578wc"
DEFAULT_VOICE_NAME = "大肥鱼·机灵版"
DEFAULT_API_BASE_URL = "https://api.breeze.blue"
DEFAULT_INSTRUCTIONS = (
    "标准普通话，声音清亮、轻软，平时有一点小迷糊；"
    "像在 QQ 群里随口聊天一样自然，语速略快，不要拖长尾音。"
)


class PluginSectionConfig(PluginConfigBase):
    """基础设置"""

    __ui_label__ = "基础设置"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="是否启用 BreezeBlue 按需语音。关闭后即使模型请求语音也会按文字发送。",
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置文件版本号（内部使用，无需修改）",
    )


class BreezeConfig(PluginConfigBase):
    """BreezeBlue 语音服务"""

    __ui_label__ = "BreezeBlue 语音服务"
    __ui_order__ = 1

    api_key: str = Field(
        default="",
        description="BreezeBlue API Key（brz_ 开头）。只从本配置读取，不会写入日志或聊天消息。",
        json_schema_extra={
            "x-widget": "password",
            "placeholder": "brz_...",
            "hint": "在 https://breezeblue.ai/app/developer/api-keys 创建；留空时语音功能自动跳过。",
        },
    )
    api_base_url: str = Field(
        default=DEFAULT_API_BASE_URL,
        description="BreezeBlue API 地址，一般不需要修改",
        json_schema_extra={"placeholder": DEFAULT_API_BASE_URL},
    )
    voice_id: str = Field(
        default=DEFAULT_VOICE_ID,
        description="音色 Voice ID。机灵版 voc_u3nxkd5578wc；迷糊版 voc_8yud7hz7c9q2。",
        json_schema_extra={"placeholder": DEFAULT_VOICE_ID},
    )
    voice_name: str = Field(
        default=DEFAULT_VOICE_NAME,
        description="音色显示名（仅用于日志和配置页说明，不影响合成）",
    )
    model_id: str = Field(
        default="breeze-tts-2",
        description="TTS 模型 ID；留空则由服务端按语言自动选择",
    )
    language_code: str = Field(
        default="zh",
        description="语言代码，普通话使用 zh",
    )
    output_format: Literal["mp3", "wav"] = Field(
        default="mp3",
        description="音频输出格式；SnowLuma 适配器可直接发送 MP3",
    )
    instructions: str = Field(
        default=DEFAULT_INSTRUCTIONS,
        description="语气与表达风格提示词，可留空",
        json_schema_extra={"x-widget": "textarea", "rows": 3},
    )
    speed: float = Field(
        default=1.0,
        ge=0.5,
        le=2.0,
        description="语速，0.5–2.0，默认 1.0",
        json_schema_extra={"step": 0.1},
    )
    timeout_sec: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="单次合成超时（秒）；超时按文字回退，不自动重试",
        json_schema_extra={"step": 1},
    )


class TriggerConfig(PluginConfigBase):
    """触发与限额"""

    __ui_label__ = "触发与限额"
    __ui_order__ = 2

    mode: Literal["on_demand", "random_llm"] = Field(
        default="on_demand",
        description=(
            "on_demand=只有模型主动调用 breeze_voice_say 才转语音；"
            "random_llm=按概率随机转语音，同时保留模型主动请求"
        ),
    )
    probability: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="random_llm 模式下每条短回复转语音的概率；0.05 表示约 5%",
        json_schema_extra={"step": 0.05},
    )
    short_reply_max_chars: int = Field(
        default=60,
        ge=10,
        le=200,
        description="随机触发只作用于不超过该字数的短回复；超过仍发文字",
    )
    decision_ttl: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
        description="同一轮回复的随机判定缓存秒数；避免多句回复连发多条语音",
        json_schema_extra={"step": 1},
    )

    max_text_length: int = Field(
        default=120,
        ge=10,
        le=500,
        description="单条语音最大字数；超过则保留文字，不截断",
    )
    min_text_length: int = Field(
        default=2,
        ge=1,
        le=50,
        description="少于该字数不合成语音",
    )
    daily_limit: int = Field(
        default=50,
        ge=0,
        le=1000,
        description="每日语音条数上限，0 表示不限制；按北京时间自然日统计",
    )
    daily_char_limit: int = Field(
        default=3000,
        ge=0,
        le=100000,
        description="每日语音总字数上限，0 表示不限制",
    )
    cooldown_sec: float = Field(
        default=5.0,
        ge=0.0,
        le=300.0,
        description="同一聊天流两次语音之间的最小间隔（秒）",
        json_schema_extra={"step": 1},
    )
    group_enabled: bool = Field(default=True, description="是否允许群聊语音")
    private_enabled: bool = Field(default=True, description="是否允许私聊语音")
    allowed_group_ids: List[str] = Field(
        default_factory=list,
        description="允许语音的群号；留空表示所有群。填写后仅这些群可用",
        json_schema_extra={"x-widget": "list", "placeholder": "群号"},
    )
    allowed_account_ids: List[str] = Field(
        default_factory=list,
        description=(
            "只在这些机器人账号上发送语音；留空表示不限制。"
            "QQ 官方适配器不支持 voice 段，建议只填支持语音的 SnowLuma 机器人账号。"
        ),
        json_schema_extra={"x-widget": "list", "placeholder": "机器人账号"},
    )


class DeliveryConfig(PluginConfigBase):
    """发送与回退"""

    __ui_label__ = "发送与回退"
    __ui_order__ = 3

    fallback_to_text: bool = Field(
        default=True,
        description="合成或发送准备失败时保留文字回复；关闭后失败会取消本条消息",
    )
    remove_reply: bool = Field(
        default=True,
        description="语音消息不携带引用回复（QQ 语音条不支持引用组合）",
    )


class BreezeVoiceConfig(PluginConfigBase):
    """BreezeBlue 按需语音配置"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    breeze: BreezeConfig = Field(default_factory=BreezeConfig)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)


for _config_model in (
    PluginSectionConfig,
    BreezeConfig,
    TriggerConfig,
    DeliveryConfig,
    BreezeVoiceConfig,
):
    _config_model.model_rebuild(_types_namespace=globals())
del _config_model
