# Maid AI + DeepSeek Harness

面向 **Minecraft Java 1.20.1 / Forge 47.4.x / Touhou Little Maid 1.5.3** 的实体女仆自主 Agent 工程。

正式产品由四部分组成：

1. `Maid AI Control.exe`：非程序员唯一入口；
2. `agent-core`：Goal / Plan / Task / Memory / Skill / Token / R&D；
3. `maid-ai-bridge`：Forge Addon，真实控制 `EntityMaid`；
4. `rnd-runner`：每 5 游戏日生成独立研发周期和 Handoff。

## 不可违反的实现边界

- 真正执行身体是 `EntityMaid`，不是 Mineflayer 或假玩家；
- LLM 只决定战略、Goal 和 Plan，不做每 Tick 微操；
- 世界修改全部进入 Forge 服务端线程；
- 同一女仆只有 `MotionArbiter` 可以写入导航；
- 默认严格生存，不开放透视、传送、give、远程 setBlock；
- Runtime 与 R&D Token 独立记账；
- R&D 产物仅进入 Handoff，不自动写入用户 `mods`。

## 开发构建

Windows 开发者可运行：

```bat
BUILD_ALL_WINDOWS.bat
PACKAGE_WINDOWS.bat
```

最终用户不需要运行源码命令，只需使用打包目录中的 `Maid AI Control.exe`，并把 `MaidAI-Bridge-*.jar` 人工放入对应实例的 `mods` 文件夹。

## 目录

- `maid-ai-bridge/`：Forge 1.20.1 Java 17；
- `agent-core/`：Python 3.11+；
- `control-center/`：PySide6 + PyInstaller；
- `rnd-runner/`：隔离研发工作流；
- `docs/`：协议、架构、产品、验收；
- `references/REFERENCE_LOCK.json`：参考源版本锁；
- `FINAL_RUN_REPORT.md`：最终自动验收结果。

完整实施依据位于 `docs/TECHNICAL_SPEC_ORIGINAL.md`。
