# OpenAI-compatible API 规则

Runtime 与 R&D 使用两套独立 Profile 和 Token Ledger。两套 Profile 可以相同，但账本不能合并。

模型名称和 Base URL 只去除首尾空格，其他字符原样发送。程序不会把用户模型名自动改成 `deepseek-chat`，不会自动追加 `/v1`。Chat Completion 路径可配置，默认 `/chat/completions`。

“测试连接”发送一个最小 Chat Completion，不依赖 `/models`。API Key 只从 Windows Credential Manager、DPAPI 或加密后备存储读取，并通过受管 Worker 的临时环境传递；普通配置和日志不包含明文 Key。
