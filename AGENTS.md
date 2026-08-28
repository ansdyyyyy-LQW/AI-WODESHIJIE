# AGENTS.md

## 唯一产品目标

在 Minecraft 1.20.1 Forge 中，以 Touhou Little Maid 1.5.3 的真实 `EntityMaid` 作为唯一身体，完成可长期运行的外置自主 Agent、Windows 控制中心和每五日研发系统。

## 施工顺序

严格按 `docs/TECHNICAL_SPEC_ORIGINAL.md` 第 35 章 Step 1→12 推进。步骤是依赖顺序，不是等待人工确认的 Gate。

## 架构硬规则

1. Mineflayer 不得成为生产身体。
2. 网络线程不得访问或修改 Minecraft World。
3. 导航只能由 `MotionArbiter` 持有和写入。
4. Action 必须有 timeout/cancel/result；Step 与 Goal 必须再做后置条件验证。
5. Runtime 与 R&D 的 API Profile、权限、Token Ledger 完全分开。
6. 模型名和 Base URL 原样透传，不做渠道猜测或重命名。
7. API Key 不进入普通配置、日志、诊断包或 Git。
8. 默认禁止 xray/teleport/give/setBlock 伪装生存。
9. R&D 只能写隔离 worktree 和 handoff，不能操作正式 mods。
10. 用户正常使用不得依赖终端、Python、JSON、UUID 或 WebSocket 地址。

## 当前兼容锁

- Minecraft 1.20.1
- Forge 47.4.23
- Java 17
- Touhou Little Maid 1.5.3 Forge 1.20.1
- Python 开发支持 3.11–3.13；Windows 打包固定 3.12

## 上下文恢复

只读：`AGENTS.md`、`RUN_STATE.md`、当前 Step 与相关技术章节。不要反复重读整份总策划。
