# Changelog

## 1.1.1

- 随机模式默认概率从 0.15 调整为 0.05（5%）。
- Manifest 适配 MaiBot 1.4.x（Host 最高 1.4.99），SDK 最高支持放宽到 2.98.99。

## 1.1.0

- 新增 `trigger.mode`：`on_demand`（仅模型请求）与 `random_llm`（概率随机 + 模型请求）。
- 新增 `trigger.probability`、`trigger.short_reply_max_chars`、`trigger.decision_ttl`。
- 随机判定按聊天流缓存，一轮多句回复最多发一条语音，避免连发。
- 随机模式只处理短回复；模型明确请求时仍可使用 `max_text_length`。
- 版本号与配置版本升级到 1.1.0。

## 1.0.0

- 初始版本：模型通过 `breeze_voice_say` 工具按需触发，出站 Hook 把文本回复替换为 BreezeBlue 语音。
- 支持单条字数、每日条数 / 字数、冷却、群聊 / 私聊、群号 / 机器人账号限制。
- 合成失败、超长、超限或未配置 Key 时自动回退为文字，不重复发送。
