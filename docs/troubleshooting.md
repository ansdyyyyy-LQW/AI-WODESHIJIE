# 故障排查

## Bridge 断开

确认 Minecraft 正在运行、Bridge JAR 与 TLM 1.5.3 都在同一实例 `mods`。Agent 断线后女仆会进入 `SAFE_IDLE`，不会继续旧动作。

## API 401/403

重新检查 API Key。模型名不会被软件自动改写，确认中转站要求的精确模型名和 Chat Completion 路径。

## 端口占用

Control Center 会优先使用 8765/8766；被占用时选择空闲端口。若 Bridge 仍指向旧端口，需要按界面提示重启 Minecraft。

## 动作卡住

Action 最多进行有限重试，随后返回 `STUCK`、`PATH_NOT_FOUND` 等机器码，不会无限循环。导出诊断包可获得最近动作、错误和版本状态。
