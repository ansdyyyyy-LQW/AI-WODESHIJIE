Maid AI 0.3.0 使用说明
======================

1. 将本目录的 MaidAI-Bridge.jar 复制到目标 Minecraft 1.20.1 Forge 47.x 实例的 mods 文件夹（正式构建使用 47.4.23）。
2. 确认同一实例已安装 Touhou Little Maid 1.5.3。
3. 双击 “Maid AI Control.exe”。
4. 按首次向导选择 Minecraft 实例，并填写日常 AI 与五日研发 API。
5. 进入世界，确保要绑定的女仆已加载；在控制中心点击“发现女仆”并绑定。
6. 点击“启动 AI”。

重要边界：
- 软件不会自动把研发 Mod 安装到 mods，也不会自动重启 Minecraft。
- API Key 不写入普通配置文件；Windows 正式版优先保存到系统凭据管理器。
- 无 API 时，系统只使用有限的确定性安全后备策略，不会伪装成完整大模型自主运行。
- 研发周期的一亿 Token 是累计预算，不是单次上下文窗口。

遇到问题：
在“诊断”页点击“导出诊断包”，再提供生成的 ZIP。诊断包会脱敏 API Key、Authorization 和 Cookie。
