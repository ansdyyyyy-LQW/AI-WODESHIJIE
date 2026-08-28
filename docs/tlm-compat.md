# Touhou Little Maid 1.5.3 兼容记录

目标：Forge 1.20.1、Java 17、TLM 1.5.3。

从实际 1.20 分支与用户 JAR 核验：

- 扩展入口为 `@LittleMaidExtension` + `ILittleMaid`。
- `IMaidTask` 除 `getUid/createBrainTasks` 外，还要求 `getIcon/getAmbientSound`。
- `EntityMaid` 提供 `getMaidInv/getAvailableInv/getAvailableBackpackInv/getHandsInvWrapper/getHunger/getTask/setTask/canDestroyBlock/canPlaceBlock/destroyBlock/placeItemBlock`。
- 本项目注册 `maid_ai_bridge:autonomous`，关闭原任务的随机走动、panic 和 eating，由 `MotionArbiter`、`ReflexEngine` 和 Action Engine 统一控制。
