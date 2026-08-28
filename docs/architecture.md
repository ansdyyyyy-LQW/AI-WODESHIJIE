# Maid AI 架构

产品分为四层：

```text
Maid AI Control.exe
  -> Agent Core（Goal / Plan / Skill / Memory / Token）
      -> localhost WebSocket
          -> Maid AI Bridge（Forge 服务端）
              -> 真实 EntityMaid

每 5 游戏日：Agent Core -> 隔离 R&D worktree -> Handoff -> 用户人工安装/重启
```

## 身体所有权

`EntityMaid` 是唯一身体。正常生存动作不允许由 Mineflayer、假玩家、命令、`/fill`、`/give`、远程 `setBlock` 代做。

## 分层控制

- Tick 反射层：溺水、着火、低血量撤退、近战防御、卡住终止，0 LLM Token。
- 确定性执行层：Action/Task 状态机、超时、取消、后置条件。
- 战略层：低频选择 Goal 和 Plan。
- 五日研发层：隔离源码/Skill/Mod 改进。

## 单一移动所有权

只有 `MotionArbiter` 可以调用 `maid.getNavigation().moveTo()` 或 `stop()`。抢占顺序为 `EMERGENCY_REFLEX > COMBAT > TASK > IDLE`。
