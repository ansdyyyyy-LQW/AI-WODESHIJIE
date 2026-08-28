# 验证策略

开发期只运行与改动直接相关的快速测试。最终自动验收覆盖：

- Python 协议、Provider、Goal 后置条件、SQLite、Skill、Token 分账、R&D 触发、Handoff 安全。
- Control Center 配置、凭据、API 探测、Minecraft JAR 元数据、诊断脱敏、Worker 配置。
- Fake Bridge 端到端：HELLO、STATE_RESYNC、Snapshot、Action、Maid Discovery。
- Forge Java 17 真实编译和 JAR 内容检查。
- Windows PyInstaller onedir 构建和 EXE 启动烟雾测试。

真实 Minecraft A–G 实机验收不能由无图形 Minecraft 运行环境冒充，见 `REAL_GAME_ACCEPTANCE_CHECKLIST.md`。
