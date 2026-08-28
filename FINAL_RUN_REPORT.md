# FINAL_RUN_REPORT

该文件由 `tools/final_acceptance.py` 和 GitHub Actions 在最终构建时更新/附带生成。

## 验收范围

- Python 模型、数据库、Provider 原样透传、Token 分账；
- Bridge / Control WebSocket 端到端；
- Action ACK/RESULT、取消、超时、重连；
- R&D Day 5 触发、Handoff manifest、禁止自动安装；
- Forge Bridge 编译与 JAR 产出；
- Windows PyInstaller onedir 打包；
- 诊断包脱敏。

## 实机边界

真实 `EntityMaid` 在完整 Minecraft 客户端内的长时 Day 1、丧尸波次与中型 Blueprint 场景，需要在用户的实际整合包中执行 `docs/REAL_GAME_ACCEPTANCE_CHECKLIST.md`。自动化结果不得冒充真实游玩结果。
