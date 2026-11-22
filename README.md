# BreezeBlue 语音

给 MaiBot 增加 BreezeBlue 云端 TTS 语音回复能力，支持两种触发方式：

- `on_demand`：只有模型明确要求时调用 `breeze_voice_say` 才转语音；
- `random_llm`：按概率随机转语音，同时保留模型主动请求（默认 5%）。

不改变聊天模型、人设、记忆、群权限和 QQ 连接方式。

## 工作方式

1. 按 `trigger.mode` 判断这一轮是否发语音：随机命中或模型调用工具；
2. 宿主准备发送这条回复时，插件把纯文本替换成 BreezeBlue 合成的语音条；
3. 合成失败、文本过长、超过每日限额或未配置 Key 时，原样发送文字，不重复发送。

随机模式只作用于不超过 `short_reply_max_chars` 的短回复，并按聊天流缓存判定，
避免一轮多句回复连发多条语音。语音段以 base64 内联交给 SnowLuma 适配器，
不依赖 Bot 容器与 QQ 容器共享文件路径。

## 安装

- 插件市场搜索 **BreezeBlue 语音**，或插件 ID `github.husiya.breeze-voice`
- 手动安装：把仓库目录放到 `MaiBot/plugins/breeze-voice/`，重启 MaiBot
- 依赖：`aiohttp`（MaiBot 主程序通常已自带）

## 配置

在 MaiBot WebUI 的插件配置页填写：

| 配置项 | 说明 |
| --- | --- |
| `breeze.api_key` | BreezeBlue API Key（`brz_` 开头），密码框输入，不会写入日志 |
| `breeze.voice_id` | 默认 `voc_u3nxkd5578wc`（大肥鱼·机灵版）；迷糊版为 `voc_8yud7hz7c9q2` |
| `breeze.instructions` | 语气提示词，可留空 |
| `trigger.mode` | `on_demand`=仅模型请求；`random_llm`=概率随机 + 模型请求，默认 `random_llm` |
| `trigger.probability` | 随机模式下每条短回复转语音的概率，默认 `0.05`（5%） |
| `trigger.short_reply_max_chars` | 随机模式只处理不超过该字数的回复，默认 60 |
| `trigger.decision_ttl` | 同一轮回复随机判定的缓存秒数，默认 10，避免多句连发语音 |
| `trigger.max_text_length` | 单条语音最大字数，默认 120；超长保留文字 |
| `trigger.daily_limit` | 每日语音条数上限，默认 50；`0` 为不限 |
| `trigger.daily_char_limit` | 每日语音总字数上限，默认 3000；`0` 为不限 |
| `trigger.allowed_group_ids` | 允许语音的群号；留空表示所有群 |
| `trigger.allowed_account_ids` | 允许发语音的机器人账号；默认空表示不限制。QQ 官方适配器不支持 `voice` 段，建议只填 SnowLuma 的账号 |
| `delivery.fallback_to_text` | 失败时保留文字，默认开启 |

也可以把 Key 放在容器环境变量 `BREEZE_API_KEY` 中；配置页的 `api_key` 优先。

## 启用 / 停用 / 回退

- 停用语音：把 `plugin.enabled` 设为 `false`，或在插件管理页禁用插件。聊天不受影响。
- 完全回退：禁用本插件，并重新启用原来的 MiMo 语音插件即可。
- 用量记录：插件数据目录下的 `usage.json`，按北京时间自然日统计。

## 注意

- 同一个 MaiBot 里可能同时装着 SnowLuma 适配器和 QQ 官方适配器。QQ 官方适配器
  不支持 `voice` 消息段，强行发送会导致整条回复丢失；建议在
  `trigger.allowed_account_ids` 里只填支持语音的 SnowLuma 机器人账号，官方账号会自动回退文字。
- 如果两个适配器都在同一个群里，还会出现重复回复和重复工具名警告。
  不需要官方适配器时，在插件管理页停用它即可；需要保留时不要把它的账号加入
  `allowed_account_ids`。
- 随机触发按聊天流缓存；如果你希望完全只在明确要求时发语音，把 `trigger.mode`
  设为 `on_demand`。
- 不要同时开启 MiMo 语音的随机触发和本插件，否则可能出现一条回复两种声音。
- BreezeBlue 接口不是 OpenAI TTS 兼容接口：鉴权头为 `xi-api-key`，
  `voice_id` 在 URL 路径中，`output_format` 是查询参数。
- 同步请求失败不会自动重试，避免超时场景重复计费；失败按配置回退文字。
- 首次接入请先在指定测试群发一条短语音，分别确认音频生成、QQ 送达和手机/电脑播放。

## 许可证

MIT
