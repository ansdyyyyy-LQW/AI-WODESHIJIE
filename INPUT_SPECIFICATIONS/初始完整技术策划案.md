# Maid AI + DeepSeek Harness：Minecraft 自主生存、自我发展与自我研发系统技术策划案

> **文档版本**：全新开发完整技术版  
> **核验日期**：2026-08-27  
> **目标 Minecraft**：Java Edition 1.20.1  
> **Mod Loader**：Forge 47.x  
> **Java**：17  
> **AI 身体**：Touhou Little Maid / 车万女仆 1.5.3 Forge 1.20.1  
> **世界设定**：原版物品/资源体系 + “惊变100天”类丧尸压力变化；不以通关为主，以生存、发展、防御、建设、自我改进为主。  
> **最终产品**：`Maid AI Control.exe` + `Maid Agent Core` + `Maid AI Bridge.jar` + `DeepSeek R&D Harness`。  
> **文档定位**：Codex 可直接执行的完整工程实施规范，同时面向完全不懂代码的最终用户；除非真实 API / Forge 映射 / Touhou Little Maid 源码与本文核验结果冲突，否则不得擅自改变核心架构。

---

# 0. 一句话目标

建立一个真正控制 `EntityMaid` 的外置自主 Agent：

- 女仆是 AI 在 Minecraft 中唯一的“身体”；
- AI 能观察世界、设定长期/中期/当前目标，并自主完成生存、挖矿、采集、食物、工具、合成、熔炼、储存、战斗、逃生、基地建设和长期发展；
- 日常行动由“LLM 战略/规划 + 确定性任务状态机 + Tick 级反射”三层共同完成，LLM 不负责每 Tick 微操；
- 世界基础资源和物品体系保持原版 Minecraft，“惊变100天”类模组只负责逐步增加丧尸数量、种类、属性和生存压力；
- AI 不以通关为主要目标，而是根据实际环境自行形成资源、基地、防御、建设和长期发展路线；
- 当丧尸压力提升时，系统只提供客观威胁信息和通用动作能力，不预写“壕沟、岩浆陷阱、防御走廊”等答案，具体战略必须由 AI 自己产生；
- 生存体系稳定后，AI 可以继续发展大型建筑、蓝图、材料规划、长期施工和基地美化，而不是停留在基础生存；
- 每 5 个游戏日开启一次与日常预算**完全独立**的 DeepSeek Harness 研发阶段；
- 研发 Agent 可以分析最近五日日志、失败原因、技能成功率和源码，修改 Agent、写新 Skill、写辅助脚本、开发新的 Forge Addon，或搜索适合的新模组；
- AI 找到或开发的新模组**不自动安装**，只输出到 handoff 并在控制中心通知用户，由用户人工安装并重启 Minecraft；
- 最终必须提供 `Maid AI Control.exe` 图形控制中心：用户可通过 GUI 填写 API、测试连接、启动/暂停 AI、绑定女仆、查看当前目标/任务/Token/Memory/Skill/R&D 结果和 Mod Handoff；
- 最终用户完全不懂编程，因此正常使用不得要求用户打开终端、运行 Python、修改 `.env`、手写 JSON、查实体 UUID 或自行分析开发日志。

> **最终产品不是单独一个 Mod。**正式交付由 `Maid AI Control.exe`、外置 `Maid Agent Core`、Forge 1.20.1 `Maid AI Bridge.jar`、DeepSeek R&D Harness 和数据/日志/Handoff 目录共同组成；其中真正改变 Minecraft 世界的动作必须由 Forge 服务端中的真实 `EntityMaid` 执行。

---

# 1. 不可修改的核心原则

## 1.1 女仆必须是真正的执行实体

禁止采用以下伪实现：

- Mineflayer 玩家在后台真正干活，女仆只跟随/播放动画；
- 生成第二个假玩家代替女仆完成挖矿与建造；
- AI 直接 `/fill`、`/give`、`/tp` 完成正常生存任务；
- 服务器后台直接改方块，而女仆与目标距离无关；
- 直接把物品塞进女仆背包而不消耗材料/执行生产流程。

正常生存阶段所有实际动作最终必须落到：

```text
External Agent
    -> Maid Bridge
        -> EntityMaid
            -> Navigation / interaction / inventory / combat
                -> Minecraft Server World
```

## 1.2 大模型不控制每 Tick 动作

Minecraft 每秒 20 Tick。LLM 不得负责：

- 每 Tick 决定前后左右；
- 每 Tick 调整 yaw/pitch；
- 每一剑都重新请求模型；
- 每挖一个方块都重新做战略推理。

必须分层：

1. **反射层（0 LLM token）**：Tick 级处理危险、溺水、着火、被攻击、卡住等。
2. **执行层（0 LLM 或极少 LLM）**：几十秒到数分钟的确定性任务状态机。
3. **战略/规划层（LLM）**：决定“接下来做什么、为什么、目标完成条件是什么”。
4. **五日研发层（独立 Harness）**：源码、技能、工具、Mod 级自我改进。

## 1.3 初始能力必须通用，禁止提前泄露丧尸策略

允许初始原子能力：

- 观察附近环境；
- 移动到目标；
- 挖一个可合法挖掘的方块；
- 放置一个方块；
- 使用桶、门、工作台等物品/方块；
- 合成、熔炼、吃饭、储存；
- 攻击、撤退、防御；
- 挖指定区域；
- 放置指定区域；
- 保存地点和事实。

禁止初始直接提供：

```text
build_zombie_moat()
build_lava_trap()
build_kill_corridor()
build_zombie_fortress()
```

如果 AI 最终想出“壕沟 + 岩浆”，必须由它用通用动作组合出来，或在研发阶段自己创造新 Skill。

## 1.4 默认严格生存，禁止初始透视

基础 Bridge 默认不得暴露：

- 任意半径地下矿物透视；
- 无视遮挡读取远处所有怪物；
- 远程开箱；
- 远程破坏/放置；
- 紧急传送；
- 强制拾取；
- 创造模式物品生成。

如果后期 AI 自己研发出“矿物扫描 Mod / 透视工具”，那属于实验结果，应作为新的能力版本记录，而不是基础系统偷偷提供。

---

# 2. 已核验的参考项目与使用方式

> **Codex 执行要求**：开始工程骨架建设时必须把以下核心仓库克隆到 `references/`，记录当前 commit SHA 到 `references/REFERENCE_LOCK.json`。这些项目大多版本不同，**不得整仓复制到目标工程**；只允许按本节指定目的研究/移植，并遵守 LICENSE。

## 2.1 Touhou Little Maid（必须依赖）

- GitHub：<https://github.com/TartaricAcid/TouhouLittleMaid>
- 1.20 分支：<https://github.com/TartaricAcid/TouhouLittleMaid/tree/1.20>
- 当前目标发布版：<https://www.curseforge.com/minecraft/mc-mods/touhou-little-maid/files/8061847>
- 当前核验版本：`touhoulittlemaid-1.5.3-forge+mc1.20.1.jar`，2026-05-09 发布。
- 官方开发入口：<https://github.com/TartaricAcid/TouhouLittleMaid/wiki/%E5%A6%82%E4%BD%95%E5%BC%80%E5%A7%8B>
- 工作模式 API：<https://github.com/TartaricAcid/TouhouLittleMaid/wiki/%E6%B7%BB%E5%8A%A0%E6%96%B0%E7%9A%84%E5%B7%A5%E4%BD%9C%E6%A8%A1%E5%BC%8F%EF%BC%88%E4%B8%8A%EF%BC%89>
- `ILittleMaid`：<https://github.com/TartaricAcid/TouhouLittleMaid/blob/1.20/src/main/java/com/github/tartaricacid/touhoulittlemaid/api/ILittleMaid.java>
- `IMaidTask`：<https://github.com/TartaricAcid/TouhouLittleMaid/blob/1.20/src/main/java/com/github/tartaricacid/touhoulittlemaid/api/task/IMaidTask.java>
- `EntityMaid`：<https://github.com/TartaricAcid/TouhouLittleMaid/blob/1.20/src/main/java/com/github/tartaricacid/touhoulittlemaid/entity/passive/EntityMaid.java>

### 已核验可用 API

扩展入口：

```java
@LittleMaidExtension
public final class MaidAiTlmExtension implements ILittleMaid {
}
```

`ILittleMaid` 当前 1.20 分支包含：

```java
void addMaidTask(TaskManager manager)
void addExtraMaidBrain(ExtraMaidBrainManager manager)
void registerAITool(ToolRegister register)          // AvailableSince 1.5.1
void registerAIMaidContext(GameContextRegister register) // AvailableSince 1.5.1
```

`EntityMaid` 已核验存在：

```java
ItemStackHandler getMaidInv()
CombinedInvWrapper getAvailableInv(boolean handsFirst)
CombinedInvWrapper getAvailableBackpackInv()
EntityHandsInvWrapper getHandsInvWrapper()
boolean destroyBlock(BlockPos pos)
boolean destroyBlock(BlockPos pos, boolean dropBlock)
PathNavigation getNavigation() // 继承实体导航接口
```

### 本项目如何使用

- **必须**作为 AI 身体层；
- 使用 `@LittleMaidExtension` 做无侵入 Addon；
- 新增一个专用 `IMaidTask`：`maid_ai_bridge:autonomous`；
- `registerAITool/registerAIMaidContext` 可作为未来兼容原生女仆 AI 的入口，但**不作为主 Agent 核心**；
- 运行时主脑由外部 Agent Core 控制。

---

## 2.2 Maid Intelligence（女仆身体控制直接参考）

- GitHub：<https://github.com/RhineIris/touhou-little-maid-maidintelligence>
- 目标环境：MC 1.20.1 + Forge 47 + Java 17 + Touhou Little Maid。
- README 声明能力：A*、PathExecutor、渐进扫描、服务端挖掘、猎杀、TaskPlan、LLM JSON 任务。

关键目录：

```text
src/main/java/com/maidintelligence/
  engine/
    interact/
    pathfind/
      MaidAStarPathFinder.java
      MaidPathNode.java
      PathExecutor.java
    plan/
    scan/
  task/
```

关键参考文件：

<https://github.com/RhineIris/touhou-little-maid-maidintelligence/blob/main/src/main/java/com/maidintelligence/engine/pathfind/PathExecutor.java>

已核验 `PathExecutor` 的执行方式：

```java
maid.getNavigation().moveTo(...)
```

并通过 `maid.getPersistentData()` 保存 waypoint/index/stuck 状态。

### 只借什么

- `EntityMaid` 实际导航调用方式；
- A* 与 waypoint 执行结构；
- 扫描任务分批执行思路；
- Addon 与 `IMaidTask` 集成方式；
- 服务端真实挖掘生命周期。

### 不允许直接照搬什么

该项目 README 已明确承认存在“女仆在目标点与主人之间横跳”的移动冲突。

因此不得原样复制它“每 Tick 重发 `moveTo` + 原女仆 Brain 同时运行”的控制方式。本项目必须实现 **Single Motion Owner（单一移动所有者）**，见第 7 章。

---

## 2.3 mc_aiplayer / AIBot（确定性 Goal/Task 架构第一参考）

- GitHub：<https://github.com/zoyluoblue/mc_aiplayer>
- 当前项目本身：Fabric 1.21.3、Java 21，不能直接放进 Forge 1.20.1。
- README 核验：63 个工具、34 个确定性 Task 状态机、9 类 typed Goal；核心原则为：

```text
LLM chooses intent.
Goals define completion.
Deterministic tasks execute.
```

关键目录：

```text
src/main/java/io/github/zoyluo/aibot/
  action/
  brain/
  goal/
    Goal.java
    GoalPlanner.java
    GoalExecutor.java
    GoalPredicate.java
    GoalResult.java
    GoalSnapshot*.java
  task/
  persist/
  observe/
  pathfinding/
```

`Goal.java` 当前使用 Java sealed interface + record 定义强类型 Goal，例如：

```java
Goal.HaveItem(Item item, int count)
Goal.HavePickaxeTier(int tier)
Goal.MineOre(Set<Block> ores, int count)
Goal.HarvestCrop(...)
Goal.Armor()
Goal.Workstation()
Goal.Stockpile(...)
Goal.Food(...)
Goal.Build(String blueprint)
```

### 本项目必须借鉴

- Goal 与 Task 分离；
- Task “执行结束”不等于 Goal 成功；
- Goal 必须使用世界/背包后置条件再次验证；
- cancel / replace / pause / resume；
- 重启恢复快照；
- strict survival capability policy；
- Action 不允许 LLM 绕过 Task 层直接改世界。

### 禁止直接移植

- Fabric API；
- Yarn 类型；
- ServerPlayer 假玩家实现；
- 1.21.3 特定注册/网络代码。

---

## 2.4 Minecraft-Agent（外置 Agent、自主循环、Plan/Memory 第一参考）

- GitHub：<https://github.com/kevin-liu-01/minecraft-agent>
- 当前架构：Python MCP Server + Node Mineflayer Bridge。
- README 核验：60+ tools、Skill Library、Multi-step Planner、Persistent World Memory、Error Recovery、Autonomous Survival。

关键目录：

```text
src/minecraft_dedalus_mcp/
  server.py
  bridge_client.py
  models.py
  playbook.py
  skills/store.py
  planning/planner.py
  memory/world_memory.py
  memory/session.py
  recovery/retry.py
  agent/autonomous.py
```

已核验自主循环：

```text
inspect -> recommend goal -> run agent -> remember results -> repeat
```

已核验 `planning/planner.py`：

```python
class PlanStep(BaseModel):
    step_id: str
    description: str
    tool_name: str
    tool_args: dict
    status: str
    result: ...
    error: ...

class Plan(BaseModel):
    plan_id: str
    goal: str
    steps: list[PlanStep]
    status: str
```

已核验 `memory/world_memory.py`：

- `LocationEntry`
- `ResourceDeposit`
- `StructureRecord`
- 持久化世界位置、资源点、建筑记录。

### 本项目必须借鉴

- Agent Core 外置；
- 自主循环；
- Plan checkpoint；
- World Memory；
- Skill Store；
- Error Recovery；
- MCP Tool 思想。

### 不直接使用

- Dedalus 是参考项目依赖，不应成为本项目硬依赖；
- Mineflayer Bridge 必须替换成 Maid Bridge；
- 不要求 ngrok；本项目默认只在 localhost 通信。

---

## 2.5 Minecraft Agent Swarm（自我进化与技能评价第一参考）

- GitHub：<https://github.com/JesseRWeigel/minecraft-agent-swarm>
- 关键文件/目录：

```text
src/bot/
  brain.ts
  actions.ts
  perception.ts
  navigation.ts
  memory.ts
  scoreboard.ts
  curriculum.ts
  trajectory.ts
src/skills/
skills/voyager/
finetune/
```

README 当前明确实现：

- TypeScript skills；
- 57 个 Voyager-style JS skills；
- runtime dynamic skill generation；
- skill success rate；
- 失败技能淘汰；
- code error -> source + error -> LLM 修复；
- session scoreboard；
- trajectory logging。

### 本项目必须借鉴

- 技能成功/失败统计；
- 技能版本化；
- 动态技能生成；
- 技能失败自动进入 refinement queue；
- 研发阶段根据真实运行数据决定是否改代码，而不是凭感觉改。

---

## 2.6 Mindcraft（模型路由、代码模型、Prompt/Memory 参考）

- GitHub：<https://github.com/mindcraft-bots/mindcraft>
- 2026-03 仍有 release；支持 DeepSeek。
- 当前 profile 可分别配置：

```json
{
  "model": { ... },
  "code_model": { ... },
  "vision_model": { ... },
  "embedding": { ... },
  "speak_model": "..."
}
```

### 本项目借鉴

- `runtime_model` 与 `code_model` 分开；
- 模型 Provider 抽象；
- example/skill 检索；
- 长任务上下文压缩；
- coding 不与普通行动共享权限。

### 安全要求

Mindcraft 官方明确警告“允许模型写/执行代码存在 prompt injection 风险”。本项目研发 Agent 必须在隔离 worktree 中运行，禁止直接覆盖正在运行的正式实例。

---

## 2.7 qxfMCAI（Forge 1.20.1 LLM -> 实体执行参考）

- GitHub：<https://github.com/QXF19/qxfMCAI>
- 当前 README：Forge 1.20.1、DeepSeek/OpenAI-compatible、NPC 真实挖矿/建造/战斗/种田。

### 本项目用途

只研究：

- Forge 1.20.1 中如何组织 LLM 请求；
- 实体执行层；
- 任务队列；
- 中文日志/控制台；
- API Key 配置方式。

不以它作为最终 Agent 主脑。

---

## 2.8 次级参考

### mc-agents

<https://github.com/jblemee/mc-agents>

重点借鉴“LLM Strategy + 程序 Reflex + persistent MEMORY”的分层思路。

### Minecraft Multimodal Agent（后期建筑/视觉模块）

<https://github.com/win10ogod/mc-multimodal-agent>

后期可参考：

- 多模态观察；
- persistent goal tree；
- LevelDB memory；
- `.litematic` 蓝图施工；
- web search；
- 技能记录。

**不得把此项目作为前期生存闭环的前置依赖。**

---


## 2.9 Voyager / Odyssey（Skill、自主课程、长期探索参考）

### Voyager

- GitHub：<https://github.com/MineDojo/Voyager>
- 项目主页：<https://voyager.minedojo.org/>

重点研究：

- 自动课程（automatic curriculum）；
- 代码 Skill Library；
- 执行失败后的代码修正；
- 成功技能的长期复用。

本项目只借其“技能成长”思想，不直接依赖它的 Mineflayer/MineDojo 运行环境。

### Odyssey

- GitHub：<https://github.com/zju-vipa/odyssey>

重点研究：

- 原子技能与组合技能；
- 长期规划；
- 自主探索；
- 多技能组合完成复杂目标。

用于补充本项目 Skill 组合和中长期规划设计。

---

## 2.10 建筑智能参考

### Minecraft Agentic Builder

- GitHub：<https://github.com/NoblerWorks-HQ/minecraft-agentic>

重点研究：

- 用高层建筑原语代替逐方块 LLM 输出；
- `walls / floor / box / ring / cone / door / window` 等建筑 DSL；
- Blueprint 压缩；
- 大型建筑规划；
- 建后视觉复查和修正。

该项目用于本项目中后期大型建筑系统，不作为前期生存必需依赖。

### Minecraft Multimodal Agent

- GitHub：<https://github.com/win10ogod/mc-multimodal-agent>

重点研究：

- 多模态观察；
- persistent goal tree；
- 长期记忆；
- `.litematic` 蓝图施工；
- web search；
- Skill 记录。

### APT / Minecraft 建筑规划研究

- 论文：<https://arxiv.org/abs/2411.17255>

重点研究“文字需求 → Blueprint → 建造 → 视觉反思 → 修改”的建筑闭环。

---

## 2.11 外部 Mod / 脚本能力参考

### Modrinth API

- API 文档：<https://docs.modrinth.com/api/operations/getprojectversions/>

用于 AI 后期搜索：

- Minecraft 版本；
- Forge Loader；
- Mod 版本；
- 下载文件；
- 哈希；
- 依赖。

AI 可以搜索和推荐兼容 Mod，但正式安装仍由用户确认。

### KubeJS

- 文档：<https://kubejs.com/wiki/folder-structure/startup-scripts>

可作为 AI 后期生成轻量 Minecraft 逻辑/脚本的可选能力。简单能力优先 Skill 或脚本，只有确实需要 Forge 级功能时才开发完整 Mod。

### Forgematica

- Modrinth：<https://modrinth.com/mod/forgematica>

作为 Forge 1.20.1 建筑蓝图工具研究对象之一。是否真正安装必须由 AI 根据运行需求提出，并由用户确认。

---

# 3. 最终总体架构

最终产品不是“一个 JAR”，而是 **Windows 控制中心 + 外置 Agent Core + Forge Bridge Mod + R&D Harness** 四层协同；用户只需要直接操作控制中心和 Minecraft。

```text
┌──────────────────────────────────────────────────────────────────────┐
│                     Maid AI Control.exe                              │
│                      （用户唯一主入口）                               │
│                                                                      │
│ 首次向导 / API设置 / 启停 / 女仆绑定 / 实时状态 / Token / 记忆 / 技能 │
│ 研发中心 / Mod通知 / 日志 / 诊断导出 / 打开文件夹 / 版本检查          │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ Local Control API / Process Supervisor
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        agent-core (Python)                            │
│                                                                      │
│ AutonomousLoop   StrategyManager   GoalManager                        │
│ Planner          ToolRouter        PostconditionVerifier              │
│ MemoryStore      SkillStore        ReflectionQueue                    │
│ TokenLedger      EventJournal      RndTrigger                         │
│ RuntimeProvider  RndProvider       ControlApi                         │
└───────────────┬───────────────────────────────┬───────────────────────┘
                │                               │
                │ OpenAI-compatible             │ R&D Handoff / Harness
                ▼                               ▼
┌───────────────────────────────┐      ┌───────────────────────────────┐
│ Runtime LLM / DeepSeek        │      │ DeepSeek Harness R&D          │
│ 日常预算                       │      │ 独立1亿Token预算/周期          │
└───────────────────────────────┘      └───────────────────────────────┘
                │
                │ WebSocket JSON Protocol
                │ ws://127.0.0.1:8765
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  maid-ai-bridge (Forge 1.20.1)                       │
│                                                                      │
│ WsClient          ObservationService   EventCollector                │
│ ActionEngine      TaskStateMachine     MotionArbiter                 │
│ ReflexEngine      InventoryService     CombatService                 │
│ CraftService      SmeltService         BuildService                  │
│ SavedData         Diagnostics          MaidDiscoveryService          │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
                       Touhou Little Maid
                            EntityMaid
                               │
                               ▼
                        Minecraft World
```

### 3.1 用户视角的运行形式

用户正常流程必须只有：

```text
双击 Maid AI Control.exe
    -> 首次使用时按向导填写 Minecraft 路径和 API
    -> 点击“测试连接”
    -> 点击“启动 AI”
    -> 启动/进入 Minecraft
    -> 控制中心自动发现可绑定女仆
    -> 用户点击“绑定”
    -> 点击“开始自主发展”
```

用户不需要知道 Python、WebSocket、端口、SQLite、Gradle、MCP、UUID、环境变量等实现细节。

### 3.2 每 5 游戏日研发流程

```text
agent-core 检测游戏日
    -> 生成 R&D Handoff
    -> 控制中心“研发中心”显示待开始/进行中
    -> DeepSeek Harness 使用独立 rnd ledger
    -> 输出 Skill / Patch / 新 Forge Mod / 推荐第三方 Mod
    -> 自动编译/静态测试/可执行测试
    -> Control Center 弹出“需要人工操作”
    -> 用户点“打开文件夹”
    -> 用户手动安装 Mod 并重启 Minecraft
    -> 控制中心标记该 Handoff 已处理
```

**AI 不允许自行修改用户正式 `mods` 目录，不允许自行关闭/重启 Minecraft。**

---

# 4. 目标仓库结构

Codex 必须创建 monorepo。**控制中心是正式产品模块，不是后期可选 UI。**

```text
maid-ai-project/
├─ README.md
├─ AGENTS.md
├─ THIRD_PARTY_NOTICES.md
├─ CHANGELOG.md
├─ BUILD_ALL_WINDOWS.bat
├─ PACKAGE_WINDOWS.bat
├─ docs/
│  ├─ architecture.md
│  ├─ protocol.md
│  ├─ capability-policy.md
│  ├─ product-ui.md
│  ├─ api-provider.md
│  ├─ testing.md
│  └─ troubleshooting.md
├─ references/
│  ├─ REFERENCE_LOCK.json
│  └─ README.md
├─ maid-ai-bridge/
│  ├─ build.gradle
│  ├─ gradle.properties
│  ├─ settings.gradle
│  ├─ gradlew / gradlew.bat
│  ├─ BUILD_WINDOWS.bat
│  └─ src/
│     ├─ main/java/com/maidaibridge/
│     │  ├─ MaidAiBridgeMod.java
│     │  ├─ compat/tlm/MaidAiTlmExtension.java
│     │  ├─ task/MaidAutonomousTask.java
│     │  ├─ controller/MaidAiController.java
│     │  ├─ controller/MotionArbiter.java
│     │  ├─ transport/MaidWsClient.java
│     │  ├─ transport/ProtocolCodec.java
│     │  ├─ protocol/*.java
│     │  ├─ observe/MaidObservationService.java
│     │  ├─ observe/VisibilityService.java
│     │  ├─ discover/MaidDiscoveryService.java
│     │  ├─ action/ActionEngine.java
│     │  ├─ action/ActionContext.java
│     │  ├─ action/ActionRegistry.java
│     │  ├─ action/impl/*.java
│     │  ├─ reflex/ReflexEngine.java
│     │  ├─ inventory/MaidInventoryService.java
│     │  ├─ craft/MaidCraftService.java
│     │  ├─ smelt/MaidSmeltService.java
│     │  ├─ combat/MaidCombatService.java
│     │  ├─ build/MaidBuildService.java
│     │  ├─ persist/MaidAiSavedData.java
│     │  ├─ command/MaidAiCommand.java       # 仅调试/兜底
│     │  └─ diagnostic/*.java
│     ├─ main/resources/
│     └─ gametest/
├─ agent-core/
│  ├─ pyproject.toml
│  ├─ uv.lock
│  ├─ .env.example                           # 仅开发调试，不是用户配置入口
│  ├─ src/maid_agent/
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ transport/ws_server.py
│  │  ├─ control/api_server.py
│  │  ├─ control/events.py
│  │  ├─ protocol/models.py
│  │  ├─ llm/provider.py
│  │  ├─ llm/openai_compatible.py
│  │  ├─ llm/harness_adapter.py
│  │  ├─ brain/autonomous_loop.py
│  │  ├─ brain/strategy.py
│  │  ├─ brain/planner.py
│  │  ├─ brain/tool_loop.py
│  │  ├─ goal/models.py
│  │  ├─ goal/manager.py
│  │  ├─ goal/postconditions.py
│  │  ├─ actions/client.py
│  │  ├─ memory/store.py
│  │  ├─ memory/schema.py
│  │  ├─ skills/models.py
│  │  ├─ skills/store.py
│  │  ├─ skills/executor.py
│  │  ├─ reflection/queue.py
│  │  ├─ metrics/scoreboard.py
│  │  ├─ tokens/ledger.py
│  │  ├─ rnd/trigger.py
│  │  └─ rnd/handoff.py
│  ├─ prompts/
│  ├─ migrations/
│  └─ tests/
├─ control-center/
│  ├─ pyproject.toml
│  ├─ src/maid_control/
│  │  ├─ main.py
│  │  ├─ app.py
│  │  ├─ models/
│  │  │  ├─ settings.py
│  │  │  ├─ runtime_status.py
│  │  │  └─ handoff.py
│  │  ├─ services/
│  │  │  ├─ process_supervisor.py
│  │  │  ├─ config_repository.py
│  │  │  ├─ credential_store.py
│  │  │  ├─ api_probe.py
│  │  │  ├─ agent_control_client.py
│  │  │  ├─ minecraft_locator.py
│  │  │  ├─ diagnostics_exporter.py
│  │  │  └─ handoff_service.py
│  │  ├─ ui/
│  │  │  ├─ main_window.py
│  │  │  ├─ wizard.py
│  │  │  ├─ pages/dashboard.py
│  │  │  ├─ pages/api_settings.py
│  │  │  ├─ pages/maid_binding.py
│  │  │  ├─ pages/runtime.py
│  │  │  ├─ pages/tokens.py
│  │  │  ├─ pages/memory.py
│  │  │  ├─ pages/skills.py
│  │  │  ├─ pages/rnd_center.py
│  │  │  ├─ pages/mod_handoff.py
│  │  │  ├─ pages/logs.py
│  │  │  ├─ pages/diagnostics.py
│  │  │  └─ pages/settings.py
│  │  └─ resources/
│  ├─ tests/
│  └─ packaging/
│     ├─ maid_ai_control.spec
│     └─ version_info.txt
├─ rnd-runner/
│  ├─ README.md
│  ├─ prepare_cycle.py
│  ├─ validate_patch.py
│  ├─ build_candidate.py
│  └─ templates/
├─ handoff/
└─ dist/
   └─ MaidAI/
      ├─ Maid AI Control.exe
      ├─ MaidAI-Bridge-*.jar
      ├─ _internal/                 # PyInstaller onedir 内部文件，用户无需操作
      └─ README_CN.txt
```

正常运行数据**禁止写回程序安装目录**，统一进入 Windows 用户数据目录，详见第 33 章。

---

# 5. Forge / TLM 依赖要求

## 5.1 固定环境

```text
Minecraft: 1.20.1
Forge: 47.x（以用户实际整合包可运行版本为准，优先 47.4.x）
Java: 17
Touhou Little Maid: 1.5.3 Forge 1.20.1
```

`mods.toml` 必须对 TLM 设置显式依赖范围，至少：

```toml
[[dependencies.maid_ai_bridge]]
modId="touhou_little_maid"
mandatory=true
versionRange="[1.5.3,1.6.0)"
ordering="AFTER"
side="BOTH"
```

## 5.2 Gradle 依赖处理

优先使用 CurseMaven 解析当前 TLM 1.5.3 文件 ID `8061847`。若开发环境无法拉取，则允许把用户已有的 1.5.3 JAR 放到 `maid-ai-bridge/libs/`，用 `fg.deobf(files(...))` 作为本地开发依赖。

**不得**为了方便把 TLM 源码复制进本项目。

---

# 6. TLM 扩展入口

创建：

```java
@LittleMaidExtension
public final class MaidAiTlmExtension implements ILittleMaid {
    @Override
    public void addMaidTask(TaskManager manager) {
        manager.add(new MaidAutonomousTask());
    }

    @Override
    public void registerAIMaidContext(GameContextRegister register) {
        // 只用于兼容 TLM 原生 AI / 调试，不作为外部 Agent 的主数据通道。
    }

    @Override
    public void registerAITool(ToolRegister register) {
        // v1 可为空；未来可把少量 bridge 调试动作暴露给 TLM 原生 AI。
    }
}
```

## 6.1 `MaidAutonomousTask`

必须实现 `IMaidTask`，目标是把女仆切入“外部 Agent 独占控制模式”。

核心要求：

```java
public final class MaidAutonomousTask implements IMaidTask {
    public static final ResourceLocation UID =
        ResourceLocation.fromNamespaceAndPath("maid_ai_bridge", "autonomous");

    @Override
    public ResourceLocation getUid() { return UID; }

    @Override
    public List<Pair<Integer, BehaviorControl<? super EntityMaid>>> createBrainTasks(EntityMaid maid) {
        return List.of(Pair.of(5, new ExternalAgentBehavior()));
    }

    @Override
    public boolean enableLookAndRandomWalk(EntityMaid maid) {
        return false;
    }

    @Override
    public boolean enablePanic(EntityMaid maid) {
        // 基础 panic 由本项目 ReflexEngine 管，避免两个移动控制器争抢。
        return false;
    }

    @Override
    public boolean enableEating(EntityMaid maid) {
        // 推荐 false，由 ReflexEngine / EatAction 统一负责，避免动作竞争。
        return false;
    }
}
```

> Codex 必须以当前 1.5.3 源码实际语义复核这些布尔开关；若 API 发生差异，以 1.20 分支源码为准并记录到 `docs/tlm-compat.md`。

---

# 7. 最关键：Single Motion Owner（移动单一所有权）

Maid Intelligence 已经证明如果原 Brain 与自定义寻路同时写移动目标，会出现“横跳”。本项目必须从架构上禁止。

## 7.1 `MotionArbiter`

任何系统不得直接调用：

```java
maid.getNavigation().moveTo(...)
```

除了 `MotionArbiter`。

接口：

```java
public interface MotionArbiter {
    MotionLease acquire(UUID actionId, MotionPriority priority);
    void tick(EntityMaid maid);
    void cancel(UUID actionId, String reason);
    Optional<MotionLease> current();
}
```

优先级：

```text
EMERGENCY_REFLEX = 100
COMBAT          = 80
TASK            = 50
IDLE            = 10
```

规则：

- 同时只允许一个 lease 控制 `Navigation`；
- 高优先级可以抢占低优先级；
- 抢占时旧 Action 收到 `CANCELLED_PREEMPTED`；
- 任务结束必须释放 lease；
- 断线/异常/女仆死亡时强制 `navigation.stop()` 并清空 lease；
- 禁止每个模块自行重复 `moveTo`。

## 7.2 导航执行

v1 可以使用 TLM/原版 `PathNavigation`，不要一开始自写完整 Baritone。

`MoveToAction` 状态：

```text
INIT
 -> REQUEST_PATH
 -> MOVING
 -> ARRIVED
 -> DONE

MOVING
 -> STUCK_DETECTED
 -> RECOVER_1_REPATH
 -> RECOVER_2_LOCAL_NUDGE
 -> RECOVER_3_CLEAR_SMALL_OBSTACLE (only if policy allows)
 -> FAILED_STUCK
```

卡住检测禁止只看“当前速度 = 0”。使用窗口：

```text
last_progress_pos
last_progress_game_tick
remaining_distance
```

如果 40 Tick 内距离减少 < 0.4 block：一级重寻路；连续 3 次无进展 -> 明确失败返回 Agent。

**严禁无限重试。**

---

# 8. WebSocket 通信架构

## 8.1 为什么 Forge 做 Client

Java 17 自带 `java.net.http.WebSocket` Client，因此：

- Python `agent-core` 启动本地 WebSocket Server；
- Forge Mod 主动连接 `ws://127.0.0.1:8765`；
- 不需要在 Mod 中 shading 第三方 WebSocket Server 库。

## 8.2 线程原则

WebSocket callback **绝对不能直接读取/修改 Minecraft World**。

必须：

```text
WebSocket callback thread
  -> decode JSON
  -> ConcurrentLinkedQueue<InboundCommand>
  -> return

ServerTickEvent / Maid Behavior tick
  -> poll queue
  -> world/entity mutation on Minecraft server thread
```

观察数据相反：

```text
server thread
  -> create immutable StateSnapshot DTO
  -> outbound queue
  -> websocket thread serialize/send
```

## 8.3 协议统一包络

所有消息：

```json
{
  "protocol_version": 1,
  "type": "ACTION_REQUEST",
  "session_id": "...",
  "message_id": "uuid",
  "maid_uuid": "uuid",
  "game_tick": 123456,
  "timestamp_ms": 1780000000000,
  "payload": {}
}
```

## 8.4 必须实现的消息类型

### `HELLO`

Forge -> Agent：

```json
{
  "type": "HELLO",
  "payload": {
    "bridge_version": "0.1.0",
    "minecraft": "1.20.1",
    "forge": "47.x",
    "tlm": "1.5.3",
    "capabilities": [
      "observe.status",
      "observe.visible_blocks",
      "action.move_to",
      "action.break_block"
    ]
  }
}
```

### `STATE_SNAPSHOT`

Forge -> Agent，基础频率 2 Hz；危险事件可立即增量推送。

```json
{
  "type": "STATE_SNAPSHOT",
  "payload": {
    "dimension": "minecraft:overworld",
    "day": 3,
    "time_of_day": 12400,
    "position": {"x": 12.3, "y": 64.0, "z": -8.4},
    "health": 18.0,
    "max_health": 20.0,
    "hunger": 16,
    "air": 300,
    "on_fire": false,
    "in_water": false,
    "inventory": [
      {"slot": 0, "id": "minecraft:oak_log", "count": 12}
    ],
    "nearby_entities": [],
    "visible_blocks": [],
    "current_action": null,
    "reflex_state": "NONE"
  }
}
```

### `EVENT`

重要事件即时推送：

```text
DAMAGE_TAKEN
ENTITY_KILLED
MAID_DEATH
BLOCK_BROKEN
ITEM_ACQUIRED
INVENTORY_FULL
ACTION_STUCK
ACTION_FAILED
ACTION_COMPLETED
HOSTILE_WAVE_DETECTED
BASE_BLOCK_DAMAGED（若可可靠检测）
WORLD_SAVE
DAY_CHANGED
```

### `ACTION_REQUEST`

Agent -> Forge：

```json
{
  "type": "ACTION_REQUEST",
  "message_id": "req-123",
  "payload": {
    "action": "move_to",
    "args": {"x": 120, "y": 64, "z": -40, "range": 1.5},
    "timeout_ticks": 600
  }
}
```

### `ACTION_ACK`

只表示请求被接受，不表示完成：

```json
{
  "type": "ACTION_ACK",
  "payload": {
    "request_id": "req-123",
    "action_id": "act-999"
  }
}
```

### `ACTION_RESULT`

```json
{
  "type": "ACTION_RESULT",
  "payload": {
    "request_id": "req-123",
    "action_id": "act-999",
    "status": "SUCCESS",
    "code": "ARRIVED",
    "data": {},
    "world_delta": {}
  }
}
```

统一状态：

```text
SUCCESS
FAILED
CANCELLED
PREEMPTED
TIMEOUT
```

## 8.5 心跳/重连

- `PING/PONG`：5 秒；
- 15 秒无 Agent 心跳：Bridge 切入 `SAFE_IDLE`；
- 不允许因为 Agent 断线让女仆继续无限执行旧 Action；
- Agent 重连后进行 `STATE_RESYNC`；
- action_id 必须幂等，重复消息不能执行两次。

---

# 9. Maid 观察系统

创建 `MaidObservationService`，所有数据在服务器线程构造。

## 9.1 状态

至少包含：

- 位置、朝向；
- 生命值/最大生命；
- hunger（若女仆系统可读取）；
- air/fire/water/fall；
- 当前主手/副手/护甲；
- `getMaidInv()` / `getAvailableInv()` 汇总背包；
- 当前 Action/Goal ID；
- 当前维度；
- 世界日、时间、天气；
- 当前所在 biome；
- 当前导航状态。

## 9.2 实体观察

默认观察半径建议：32 block。

记录：

```json
{
  "uuid": "...",
  "type": "minecraft:zombie",
  "category": "HOSTILE",
  "distance": 8.4,
  "relative": {"dx": 4, "dy": 0, "dz": -7},
  "health": 20,
  "line_of_sight": true,
  "targeting_maid": true
}
```

### 兼容“惊变100天”自定义丧尸

不得只写：

```java
entity instanceof Zombie
```

`ThreatClassifier` 应综合：

- `MobCategory.MONSTER`；
- `Enemy`/`Monster` 类族；
- 当前实体是否把 Maid 设为 target；
- 最近是否对 Maid 造成伤害；
- EntityType ResourceLocation；
- 配置里的额外 hostile tags。

这样未知增强丧尸也能被当作威胁，而无需知道其具体 Mod 名称。

## 9.3 方块观察与“禁止初始透视”

`visible_blocks` 只能来自：

- 附近暴露表面；
- 射线/可见性检测能确认的方块；
- 女仆实际刚刚挖开/经过的位置；
- 已经写入长期记忆的旧发现。

不要直接把半径 64 内所有地下矿物发给 Agent。

矿物获取应通过：

- 探索天然洞穴；
- 通用 `dig_tunnel` / `strip_mine`；
- 挖掘后观察新暴露面。

---

# 10. Action Engine：原子动作与确定性执行

## 10.1 基类

```java
public interface MaidAction {
    UUID id();
    ActionType type();
    ActionState state();

    void start(ActionContext ctx);
    void tick(ActionContext ctx);
    void cancel(ActionContext ctx, CancelReason reason);

    boolean isTerminal();
    ActionResult result();
}
```

`ActionEngine`：

```java
public final class ActionEngine {
    Optional<MaidAction> activeAction();
    ActionAcceptResult submit(ActionRequest request);
    void tick(EntityMaid maid, ServerLevel level);
    void cancelActive(String reason);
}
```

v1 不支持同时执行两个会修改身体/世界的动作。

## 10.2 v1 必须实现的 Action

### 观察

```text
get_status
get_inventory
inspect_area
find_visible_block
find_entity
inspect_container
```

### 移动

```text
move_to
look_at
stop
follow_entity（短距离）
```

### 世界交互

```text
break_block
place_block
use_block
use_item
pickup_nearby
```

### 物品/生产

```text
equip
craft
smelt
transfer_container
eat
```

### 战斗

```text
attack_entity
retreat_from
hold_position
```

### 通用工程

```text
dig_region
place_region
```

注意：`dig_region/place_region` 只是通用批量施工，不包含“壕沟”等语义。

---

# 11. 挖掘实现

优先调用 TLM 已有合法破坏入口：

```java
maid.destroyBlock(targetPos, true)
```

但必须先执行：

1. target 在允许交互距离；
2. target 已被观察/任务合法指定；
3. `canDestroyBlock` 允许；
4. 当前工具符合 policy；
5. 女仆走到合理位置；
6. 面向目标；
7. 根据硬度模拟合理工作时长，而不是瞬间批量删除；
8. 实际掉落进入 `getAvailableInv(false)` 或世界掉落；
9. 验证 block state 已变化；
10. 返回结果。

如果为了兼容 TLM 直接调用 `destroyBlock` 会瞬间完成，v1 仍必须在 Action 层加入 `work_ticks_remaining`，避免 AI 表现为远程瞬挖。

---

# 12. 放置实现

`PlaceBlockAction` 必须验证：

- 背包确有物品；
- 目标可替换；
- 支撑面存在；
- 交互距离合法；
- 方块放置成功后才扣/确认物品；
- 不允许直接 `level.setBlock()` 当成正常建造主路径。

应尽可能通过与玩家/实体使用方块相近的 `BlockPlaceContext` / Item 使用路径完成，确保：

- 朝向方块正确；
- 门、楼梯、活板门等状态正确；
- 触发 Forge 事件；
- Mod 方块兼容性更高。

`level.setBlock` 仅允许：

- 测试 fixture；
- 明确标记的 developer/operator capability；
- AI 后期自己研发的新特权工具。

---

# 13. 合成与熔炼

## 13.1 Craft

因为女仆不是 Player，不能简单依赖玩家 GUI。

`MaidCraftService` 采用“**验证真实配方 + 消耗真实材料 + 要求真实工作站**”的确定性抽象。

流程：

1. 通过 `RecipeManager` 查找目标 recipe；
2. 判断是否需要 crafting table；
3. 若需要，附近必须存在已观察到且可到达的工作台；
4. 女仆走到工作台交互距离；
5. 从 `getAvailableInv(...)` 模拟 recipe ingredient 匹配；
6. 全部 ingredient 足够才执行；
7. 消耗原材料；
8. 生成 recipe result；
9. 处理容器残留（桶等）；
10. 结果插入背包，满则失败/掉落；
11. 再次检查 inventory postcondition。

禁止 `give item`。

## 13.2 Smelt

优先真实使用世界中的 Furnace BlockEntity：

```text
FIND_FURNACE
 -> MOVE_TO_FURNACE
 -> INSERT_INPUT
 -> INSERT_FUEL
 -> WAIT_COOK
 -> COLLECT_OUTPUT
 -> VERIFY
```

不得简单“等待 N 秒后 raw_iron -= 1, iron_ingot += 1”。

---

# 14. Tick 级 ReflexEngine

反射层不调用 LLM。

## 14.1 初始允许反射

```text
DROWNING_ESCAPE
FIRE_ESCAPE
CRITICAL_HEALTH_RETREAT
IMMEDIATE_MELEE_DEFENSE
FALL_HAZARD_STOP
STUCK_ABORT
INVENTORY_FULL_NOTIFY
```

不要一开始加入过强自动战略。

## 14.2 抢占

例如当前正在 `BuildAction`：

```text
Zombie hits maid
 -> ReflexEngine threat >= HIGH
 -> MotionArbiter preempt TASK lease
 -> BuildAction = PAUSED_PREEMPTED
 -> Combat/Retreat
 -> danger cleared
 -> planner decides resume / abandon
```

不是让建造任务和战斗任务同时写导航。

---

# 15. Agent Core 技术栈

建议：

```text
Python 3.11+
uv
pydantic v2
websockets
httpx
sqlite3（标准库）
pytest
```

不要让 Node/Mineflayer 成为生产运行依赖。

## 15.1 LLM Provider

统一接口：

```python
class LLMProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        model_role: str,
    ) -> LLMResponse: ...
```

`model_role`：

```text
runtime_fast
runtime_strategy
runtime_reflection
rnd_code
rnd_research
vision_future
```

配置必须来自 `.env` / `config.toml`，不硬编码模型名：

```env
RUNTIME_BASE_URL=https://api.deepseek.com
RUNTIME_API_KEY=...
RUNTIME_MODEL=...
STRATEGY_MODEL=...
RND_PROVIDER=harness
```

如果用户使用 DeepSeek Harness 中转/路由，只需要替换：

```text
base_url
model
api_key / auth
```

上层 Agent 不变。

---

# 16. 自主循环

不要照抄 Minecraft-Agent 中“固定 beat-minecraft”目标。

本项目顶层唯一不可变目标：

```text
在当前世界中持续生存、自主发展、提高长期安全性与生产/建设能力。
不要求通关。
```

伪代码：

```python
async def autonomous_loop():
    while running:
        snapshot = await world.wait_for_snapshot()
        events = journal.drain_high_signal_events()

        if executor.busy() and not strategy_review_due(events):
            await executor.monitor_current_plan()
            continue

        context = context_builder.build(
            snapshot=snapshot,
            active_strategy=strategy.current,
            active_goal=goals.current,
            recent_events=events,
            recalled_memory=memory.recall(...),
            available_skills=skills.rank(...),
        )

        decision = await strategy_model.decide(context)

        if decision.keep_current_goal:
            await executor.resume_or_continue()
            continue

        goal = Goal.from_structured_decision(decision)
        goals.set(goal)

        plan = await planner.make_plan(goal, context)
        plans.save(plan)
        await executor.execute(plan)
```

## 16.1 不要每 5 秒做一次人生规划

战略重评触发：

- 当前 Goal 达成；
- 当前 Goal 失败；
- 高危事件；
- 日夜阶段变化；
- 资源/装备到达关键阈值；
- 长任务 checkpoint；
- 固定低频心跳（例如现实 60~180 秒一次，而不是 Tick 级）。

---

# 17. Goal / Plan / Task 数据模型

## 17.1 Goal

Agent Core 用 Pydantic 强类型模型，不允许只存一句自由文本。

```python
class Goal(BaseModel):
    goal_id: UUID
    type: Literal[
        "ACQUIRE_ITEM",
        "SECURE_FOOD",
        "IMPROVE_EQUIPMENT",
        "EXPLORE",
        "BUILD",
        "DEFEND",
        "RECOVER",
        "CUSTOM"
    ]
    objective: str
    priority: int
    success_conditions: list[Condition]
    failure_conditions: list[Condition] = []
    created_game_day: int
    deadline_game_tick: int | None = None
```

## 17.2 Plan

```python
class PlanStep(BaseModel):
    step_id: UUID
    description: str
    tool: str
    args: dict
    preconditions: list[Condition]
    success_conditions: list[Condition]
    status: Literal["PENDING","RUNNING","DONE","FAILED","BLOCKED","SKIPPED"]
    retry_count: int = 0
    max_retries: int = 2

class Plan(BaseModel):
    plan_id: UUID
    goal_id: UUID
    steps: list[PlanStep]
    status: str
    checkpoint: dict
```

## 17.3 关键规则

**Action success != Step success != Goal success。**

例：

```text
Action: mine block -> SUCCESS
Step: acquire 16 iron -> 只有 inventory raw_iron/iron_ingot 达数量才 DONE
Goal: iron equipment -> 只有装备后置条件成立才 SUCCESS
```

这一点必须按 `mc_aiplayer` 的后置条件思想实现。

---

# 18. Tool Router

LLM 只能看到 Agent Core 注册的安全工具。

v1 ToolSpec 建议：

```text
world_status()
inventory()
inspect_area(radius)
find_visible_block(block_query)
find_entity(query)
remember_location(...)
recall_memory(...)
move_to(...)
break_block(...)
place_block(...)
use_item(...)
craft(...)
smelt(...)
equip(...)
eat(...)
transfer_container(...)
attack_entity(...)
retreat_from(...)
dig_region(...)
place_region(...)
get_action_status(...)
cancel_action(...)
```

Tool Router 不应暴露 Java 内部对象、NBT 任意写、命令执行。

---

# 19. 战略系统

## 19.1 三层目标

```text
LongTermStrategy
  -> MidTermObjective
     -> CurrentGoal
        -> Plan
           -> Step
              -> Action
```

示例仅用于说明数据结构，**不得写进提示词当作丧尸答案**：

```text
LongTerm: 提高基地长期安全与自给能力
MidTerm: 解决最近夜间伤亡
CurrentGoal: 调查突破方向与地形
```

## 19.2 StrategyState

```python
class StrategyState(BaseModel):
    long_term_objective: str
    mid_term_objectives: list[str]
    current_focus: str
    known_constraints: list[str]
    open_problems: list[str]
    decision_summary: str
    last_review_game_day: int
```

只保存**可展示的决策摘要**，不要要求/记录模型私有 chain-of-thought。

---

# 20. Memory 系统

v1 使用 SQLite，而不是无限 JSON。

数据库：`agent-core/state/maid_agent.sqlite3`

## 20.1 表

### `world_locations`

```text
id
name
dimension
x y z
tags_json
confidence
first_seen_tick
last_seen_tick
notes
```

### `resource_observations`

```text
resource_id
block_id
position
observed_exposed
estimated_count
last_seen
exhausted
```

### `events`

```text
id
game_day
game_tick
type
severity
position
payload_json
```

### `goals`

保存 Goal 生命周期。

### `plans` / `plan_steps`

支持重启恢复。

### `skills`

```text
skill_id
name
version
kind
spec_json
source_path
success_count
failure_count
consecutive_failures
avg_duration
status
created_by
```

### `rnd_cycles`

```text
cycle_id
trigger_day
runtime_period_start_day
runtime_period_end_day
token_budget
status
artifact_dir
summary
```

### `token_usage`

```text
ledger
model
prompt_tokens
completion_tokens
total_tokens
request_id
created_at
```

---

# 21. Skill 系统

## 21.1 v1 不允许 AI 随便执行任意 Python/JS

先实现安全 Skill DSL：

```json
{
  "name": "gather_logs_then_store",
  "version": 1,
  "parameters": {"count": "int"},
  "steps": [
    {"tool": "find_visible_block", "args": {"query": "#minecraft:logs"}},
    {"tool": "move_to", "args": {"target": "$result.position"}},
    {"tool": "break_block", "args": {"target": "$result.position"}},
    {"tool": "transfer_container", "args": {"tag": "base_storage"}}
  ],
  "success": ["inventory_or_storage_gain >= count"]
}
```

## 21.2 技能评价

每次执行更新：

```text
success_count
failure_count
failure_code histogram
duration
resource_delta
health_delta
```

rank score 可先简单：

```text
(success + 1) / (success + failure + 2)
```

低成功率技能只降低推荐权重，**不要自动永久删除源文件**。

## 21.3 后期代码技能

研发 Harness 可以生成代码型 Skill，但：

- 进入 `candidate_skills/`；
- 静态检查；
- 单元测试；
- 模拟/测试世界运行；
- 版本化；
- 通过后才 promote；
- 保留旧版回滚。

参考 Agent Swarm 的 dynamic skill refinement，但不要在生产世界直接 `eval()` 未审查代码。

---

# 22. 日常 token 与五日研发 token 必须分账

## 22.1 两个 Ledger

```text
runtime
rnd
```

两者数据库记录、预算、统计图和 UI 显示完全独立；不共享余额，不允许“日常超支扣研发额度”。

内部配置模型：

```python
class TokenBudget(BaseModel):
    enabled: bool = False
    max_per_real_hour: int | None = None
    max_per_game_day: int | None = None

class RndTokenBudget(BaseModel):
    enabled: bool = True
    budget_per_cycle: int = 100_000_000
    cycle_game_days: int = 5
```

默认：

- 日常 runtime 可以不设硬上限，由用户在控制中心选择“无限制/自定义”；
- R&D 默认每 5 游戏日 `100,000,000` Token；
- 用户可在 UI 修改数值，但**运行时和研发账本始终分离**。

**1 亿 token 表示该研发周期累计可消耗上限，不表示单次上下文窗口是 1 亿。**

每次 API/Harness 调用必须根据 provider 返回的 `usage` 写入 ledger；provider 未返回 usage 时，允许用 tokenizer 估算，但必须将记录标记 `estimated=true`。

控制中心至少显示：

```text
日常 AI：今日 token / 当前五日周期 token
研发：已用 / 100,000,000 / 剩余 / 百分比
按用途：规划 / 工具调用 / 反思 / 源码阅读 / 编程 / 测试
```

不得把私有 chain-of-thought 保存为“token 可视化”；只显示 API usage 与可展示的决策摘要。

---

# 23. 五日 R&D Harness

## 23.1 触发

由 `DAY_CHANGED` 驱动：

```python
if game_day > 0 and game_day % cycle_game_days == 0 and not cycle_already_created(game_day):
    create_rnd_handoff(game_day)
```

创建周期后必须同时向 Control Center 发布：

```json
{
  "event": "RND_CYCLE_CREATED",
  "cycle_id": "cycle-005",
  "game_day": 20,
  "budget": 100000000,
  "status": "READY"
}
```

是否暂停 Minecraft 世界由用户决定。系统至少支持：

- `SAFE_IDLE`：女仆停止新战略任务但保留危险反射；
- 控制中心“暂停 AI”按钮；
- 调试命令 `/maidai rnd pause` 仅作为兜底。

## 23.2 Handoff 输入包必须包含

```text
handoff/rnd-input/cycle-005/
  README_FOR_AGENT.md
  period_summary.json
  event_timeline.jsonl
  deaths.json
  combat_metrics.json
  resource_metrics.json
  goal_history.json
  failed_actions.json
  skill_scoreboard.json
  strategy_state.json
  world_locations.json
  current_capabilities.json
  source_manifest.json
  repo_commit.txt
  screenshots/       # 后期可选
```

## 23.3 Harness 权限

R&D Harness 可用：

- 读当前项目源码；
- Git；
- Shell；
- 编译；
- 单元测试；
- 本地测试世界/模拟器；
- Web 搜索；
- 查询 GitHub/Modrinth；
- 写新 Skill；
- 修改 Agent Core；
- 修改 Maid Bridge；
- 创建新的 Forge Addon 项目。

但必须在独立 worktree：

```text
.rnd-worktrees/cycle-005/
```

禁止直接修改正在运行的 production checkout；禁止直接操作正式 Minecraft `mods` 目录。

## 23.4 输出

```text
handoff/cycle-005/
  handoff_manifest.json
  RND_REPORT.md
  CHANGE_SUMMARY.md
  USER_ACTION_REQUIRED.md
  artifacts/
    *.jar
    *.zip
  candidate-mods/
    MOD_RECOMMENDATIONS.md
  checksums.sha256
  validation/
    test-results.txt
    build-log.txt
```

`handoff_manifest.json` 是控制中心读取的机器接口，最少：

```json
{
  "cycle_id": "cycle-005",
  "status": "COMPLETED",
  "requires_user_action": true,
  "summary": "新增区域挖掘技能并推荐一个建筑辅助模组",
  "artifacts": [
    {
      "type": "forge_mod",
      "name": "MaidDefenseEngineering",
      "version": "0.1.0",
      "path": "artifacts/maid-defense-engineering-0.1.0.jar",
      "sha256": "...",
      "minecraft": "1.20.1",
      "loader": "forge",
      "dependencies": []
    }
  ],
  "recommendations": []
}
```

`USER_ACTION_REQUIRED.md` 必须简单明确写：

```text
研发完成。
需要人工操作：是/否

如果是：
1. 关闭 Minecraft。
2. 把 xxx.jar 放入 mods。
3. 需要依赖：yyy.jar。
4. 删除/替换旧版：zzz.jar。
5. 重新启动。
6. 进入原存档。
```

Control Center 必须把上述信息转换成图形化通知：

```text
AI研发完成一个新模组
[打开文件夹] [查看研发报告] [标记为已安装]
```

这符合用户要求：**AI 找到/做好 Mod 后告诉用户，由用户安装并重启。**

---

# 24. AI 自己寻找 Mod 的技术边界

这一功能放到 R&D 研发模块，不放 Runtime Agent。

搜索结果必须经过：

```text
Minecraft version == 1.20.1
loader includes Forge
license recorded
latest file known
required dependencies resolved
conflict notes recorded
source/reputation inspected
```

不得因为搜索结果标题包含“1.20.1”就直接推荐安装。

输出必须有：

```text
项目名
项目主页
版本
Loader
下载页
依赖
为什么需要
解决什么瓶颈
是否改变实验公平性
是否建议安装
```

**不要自动写入用户正式 `mods/`。**

---

# 25. 丧尸压力与战略闭环

“惊变100天”只作为动态压力，不需要第一版直接集成其内部 API。

## 25.1 AI 应获得事实，不获得答案

事件汇总可提供：

```text
某游戏日夜间遭遇 hostile 数
伤害次数
受到总伤害
死亡次数
实体主要出现方向
哪些已有方块被破坏（若事件可捕获）
战斗持续时间
逃跑次数
```

然后由战略模型自己判断。

## 25.2 Wave Analytics

`ThreatAnalytics` 只做数据聚合，不提出“建壕沟”。

例如：

```json
{
  "window": "night_day_23",
  "hostile_contacts": 47,
  "damage_taken": 18,
  "deaths": 0,
  "entry_direction_histogram": {
    "E": 22,
    "NE": 13,
    "S": 7
  }
}
```

LLM 看这些事实后自己做战略。

---

# 26. 建筑发展架构（中期以后）

v1 先实现小型 blueprint executor，后续再接视觉/大型建筑。

## 26.1 Blueprint 数据

```python
class BuildBlock(BaseModel):
    dx: int
    dy: int
    dz: int
    block_id: str
    state: dict[str, str] = {}

class Blueprint(BaseModel):
    name: str
    size: tuple[int,int,int]
    blocks: list[BuildBlock]
    required_items: dict[str,int]
```

## 26.2 生存材料闭环

`BuildGoal`：

```text
Blueprint
 -> calculate material requirements
 -> compare inventory/storage
 -> generate acquire-material subgoals
 -> return to construction
 -> place blocks
 -> verify structure
```

禁止材料不够时自动生成物品。

## 26.3 后期扩展

后期可研究：

- `.litematic`；
- 视觉截图审查；
- 建筑语法（wall/floor/ring/arch/tower 等）；
- AI 搜索建筑辅助 Mod。

这部分必须在基础生存稳定后开发，避免拖垮 v1。

---

# 27. Agent 提示词原则

`runtime_system.md` 不应该教它具体游戏路线。

必须包含：

```text
你是这个 Minecraft 世界中拥有实体身体的自主 Agent。
你的长期责任是持续生存、自主发展，并根据实际经历改善安全、资源、生产和建设能力。
你没有固定通关目标。
不要假设某个策略一定正确；先观察世界、执行、验证结果。
只能使用当前工具和当前已经批准的技能。
工具执行失败时先读取失败原因，不要无限重复相同动作。
长期计划必须允许被新的危险和事实修正。
```

不得包含：

```text
第20天应该挖壕沟
第30天应该放岩浆
第50天建城堡
```

这些属于实验结果，不是系统答案。

---

# 28. 失败恢复

所有失败必须有机器可读 code。

建议：

```text
PATH_NOT_FOUND
STUCK
TARGET_NOT_VISIBLE
TARGET_GONE
OUT_OF_RANGE
NO_TOOL
WRONG_TOOL
NO_MATERIAL
INVENTORY_FULL
NO_WORKSTATION
RECIPE_NOT_FOUND
CONTAINER_FULL
BLOCK_PROTECTED
PREEMPTED_BY_REFLEX
ENTITY_DEAD
MAID_DEAD
WORLD_UNLOADED
AGENT_DISCONNECTED
TIMEOUT
```

同一 Step 默认最多自动重复 2 次。

第三次必须：

- BLOCKED；
- 返回 Planner/LLM；
- 或创建 reflection entry。

**不允许死循环。**

---

# 29. 持久化与重启恢复

Minecraft 端：

- 绑定 Maid UUID；
- Bridge session state；
- 当前 Action 最小 checkpoint；
- autonomy enabled；
- 协议版本。

Agent 端：

- 当前 Strategy；
- Goal；
- Plan / Step；
- World memory；
- Skill stats；
- Token ledgers；
- R&D cycle state。

重启后：

1. Forge 发送 HELLO；
2. Agent 检查 maid UUID/world；
3. 做 State Resync；
4. 旧 RUNNING Action 不直接当作仍成功运行；
5. 重新验证世界后将其设为 `NEEDS_REVALIDATION`；
6. Planner 决定继续/重做。

---

# 30. 日志、视频可视化与用户诊断

必须同时生成机器日志和用户可读事件流。

机器日志：

```text
logs/runtime/YYYY-MM-DD.jsonl
logs/actions/YYYY-MM-DD.jsonl
logs/strategy/YYYY-MM-DD.jsonl
logs/llm/YYYY-MM-DD.jsonl
logs/errors/YYYY-MM-DD.log
logs/rnd/cycle-N/*
```

每条 Action log：

```json
{
  "game_day": 12,
  "goal_id": "...",
  "plan_id": "...",
  "step_id": "...",
  "action": "move_to",
  "args": {},
  "start_tick": 1,
  "end_tick": 50,
  "status": "SUCCESS",
  "code": "ARRIVED"
}
```

战略日志只记录**可展示摘要**：

```text
当前问题：食物储备不足
选择目标：建立稳定食物来源
依据：过去两天食物低于安全阈值 3 次
```

不要记录模型私有 chain-of-thought。

## 30.1 Control Center 用户事件流

UI 只展示经过归类的高层事件：

```text
20:31  开始寻找铁矿
20:34  路径被阻挡，正在重新规划
20:35  路径恢复成功
20:37  遭遇3只丧尸，生存反射接管
20:38  战斗结束
20:39  恢复铁矿任务
```

未来可直接用于视频 HUD：

```text
AI 当前长期/阶段/当前目标
当前任务与进度
生命/食物
今日资源变化
当前危险
简短决策摘要
本日Token / 本轮研发Token
最近一次研发获得的新能力
```

## 30.2 一键导出诊断包

Control Center 必须提供 `[导出诊断包]`，生成：

```text
MaidAI_Diagnostics_YYYYMMDD-HHMMSS.zip
```

包含：

- 最近 N MB 的 agent/bridge/error/action 日志；
- 当前配置的**脱敏副本**；
- Bridge/Agent/Control Center/Java/Forge/TLM 版本；
- 当前绑定 maid UUID 的哈希/可公开识别信息；
- 最近 100 个 ActionResult；
- 最近 20 个 LLM 请求的模型名、HTTP 状态、usage、延迟，但**不包含 API Key 和私有推理正文**；
- 最近一次 R&D manifest；
- 系统状态快照。

所有 API Key、Authorization、Cookie、敏感自定义 Header 必须在压缩前二次红action。

---

# 31. 游戏内控制与调试命令

**正常用户不得依赖命令完成日常使用。** 绑定、启动、暂停、状态查看都必须可在 `Maid AI Control.exe` 完成。

命令仅作为开发、故障恢复和高级调试兜底：

```text
/maidai bind                 绑定附近属于玩家的女仆
/maidai unbind
/maidai start
/maidai stop
/maidai status
/maidai action
/maidai goal
/maidai reconnect
/maidai debug on|off
/maidai rnd status
/maidai rnd export
```

不得要求用户输入复杂 UUID。

## 31.1 游戏内简化面板

完整产品应提供一个轻量游戏内面板，目标不是替代 Windows Control Center，而是提供“录制时快速控制”：

```text
AI 女仆
状态：已连接
女仆：<name>
当前目标：获取稳定铁资源
当前任务：挖掘铁矿 18/32

[开始自主模式] [暂停]
[切换/绑定女仆]
[查看简要状态]
```

实现优先级：

1. 在身体绑定链路完成时同步实现 Control Center 的发现/绑定女仆；
2. 基础控制链与 Agent Core 稳定后再实现 Minecraft 内 GUI/按键，避免前期为了 UI 注入破坏核心闭环。

---

# 32. 配置、API Provider 与凭据规则

这一章是**内部配置规范**。普通用户的真实入口是第 33 章 Control Center。

## 32.1 Bridge 配置

`config/maid_ai_bridge-server.toml`：

```toml
websocket_url = "ws://127.0.0.1:8765"
snapshot_hz = 2
entity_observe_radius = 32
visible_block_radius = 12
strict_survival = true
allow_hidden_block_scan = false
allow_remote_world_edit = false
action_timeout_ticks = 1200
```

普通用户不需要编辑该文件；Control Center 的高级设置可修改并做范围校验。

## 32.2 API Profile 必须分 Runtime / R&D

内部模型：

```python
from pydantic import BaseModel, Field, HttpUrl

class OpenAICompatibleProfile(BaseModel):
    profile_id: str
    display_name: str
    base_url: str
    model: str
    timeout_seconds: int = Field(default=120, ge=5, le=1800)
    extra_headers_secret_id: str | None = None
    api_key_secret_id: str

class AiConfiguration(BaseModel):
    runtime_profile: OpenAICompatibleProfile
    rnd_profile: OpenAICompatibleProfile
```

UI 中两套 Profile 可以使用相同 API，也可以完全不同。

## 32.3 模型名称必须“原样透传”

这是硬约束，尤其兼容中转站：

```python
payload["model"] = profile.model
```

禁止：

- 自动把用户填写的模型名改成 `deepseek-chat`；
- 根据字符串猜渠道商后改名；
- 自动添加前缀/后缀；
- 对大小写做转换；
- 用本地别名替换用户输入的远端模型名。

如果用户输入：

```text
Rim-3.1-channel-A
```

请求中必须仍然是：

```json
{"model":"Rim-3.1-channel-A"}
```

## 32.4 Base URL 规则

不得擅自把用户输入的地址改成另一个域名。允许做的归一化仅限：

- 去除首尾空格；
- UI 明确提示 URL 是否有效；
- 对末尾 `/` 做安全拼接。

OpenAI-compatible provider 默认以 `base_url + /chat/completions` 发送；若目标中转站需要不同路径，控制中心“高级设置”必须允许用户指定 `chat_completions_path`，默认 `/chat/completions`。

不得强制给用户 Base URL 自动追加 `/v1`。如果用户需要 `/v1`，应在界面中填写真实 Base URL。

## 32.5 `.env` 仅供开发，不是产品入口

允许仓库保留 `.env.example` 供 CI/开发测试，但正式 `Maid AI Control.exe`：

- 不要求用户创建 `.env`；
- 不要求用户设置系统环境变量；
- 不要求用户改 TOML/JSON 才能填 API；
- API Key 通过 Windows 凭据系统保存。

---

# 33. 非程序员产品层：`Maid AI Control.exe`

本章属于**最高优先级产品要求**。用户不懂编程，因此 Codex 不得把“能通过命令行跑起来”视为交付完成。

## 33.1 最终用户硬约束

正常使用必须满足：

- 不打开 CMD/PowerShell；
- 不执行 `python xxx.py`；
- 不执行 `pip install`；
- 不编辑 `.env`；
- 不手写 JSON/TOML；
- 不查实体 ID/UUID；
- 不手动输入 WebSocket 地址/端口；
- 不读后台技术日志才能判断是否启动成功；
- 不需要理解 Forge/Gradle/Python/SQLite/MCP。

允许用户做的正常操作只有：

- 选择文件夹；
- 填 API 地址/API Key/模型名；
- 点击测试；
- 点击启动/暂停；
- 在列表中选择女仆；
- 查看状态；
- 打开研发结果文件夹；
- 手动把 AI 找到/开发的 Mod 安装进 Minecraft 并重启；
- 一键导出诊断包。

## 33.2 技术栈

控制中心统一使用：

```text
Python 3.12（开发）
PySide6
qasync
pydantic
httpx
keyring
PyInstaller
```

最终以 **PyInstaller onedir** 打包，用户入口只有：

```text
Maid AI Control.exe
```

选择 `onedir` 而不是默认 `onefile` 的原因：

- 启动更快；
- 大型 PySide6/Agent 依赖不需要每次解压；
- 更容易包含本地 runtime、模板和资源；
- 仍然可以做到用户只点一个 EXE。

不得要求用户额外安装 Python。

## 33.3 控制中心进程拓扑

推荐：Control Center 为主进程，Agent Core 为受管 Worker。

```text
Maid AI Control.exe
  ├─ Qt UI
  ├─ ProcessSupervisor
  └─ 启动同发行包 Agent Worker
        ├─ Bridge WS: 127.0.0.1:8765
        └─ Control WS/HTTP: 127.0.0.1:8766
```

Agent Worker 可以是同一 PyInstaller 程序的隐藏子模式：

```text
Maid AI Control.exe --agent-worker --control-token <random>
```

或打包内部 `MaidAgentWorker.exe`，但不得暴露为用户必须手动启动的第二入口。

`ProcessSupervisor` 必须：

```python
class ProcessSupervisor:
    def start_agent(self) -> None: ...
    def stop_agent_gracefully(self, timeout_s: int = 10) -> None: ...
    def restart_agent(self) -> None: ...
    def is_running(self) -> bool: ...
    def last_exit_code(self) -> int | None: ...
```

UI 关闭时应弹出：

```text
AI 当前仍在运行。
[停止AI并退出] [最小化到托盘] [取消]
```

第一版可以不做托盘，但不得“直接杀进程导致状态损坏”。

## 33.4 本地控制接口

Control Center 不直接操作 Minecraft world。所有运行控制通过 Agent Core：

```text
Control Center -> Local Control API -> Agent Core -> Bridge -> EntityMaid
```

至少支持：

```text
GET_STATUS
START_AUTONOMOUS
PAUSE_AUTONOMOUS
STOP_AUTONOMOUS
DISCOVER_MAIDS
BIND_MAID
UNBIND_MAID
GET_GOALS
GET_TOKEN_LEDGER
GET_MEMORY_SUMMARY
GET_SKILLS
GET_RND_STATUS
EXPORT_RND_NOW
ACK_HANDOFF
```

Control API 只绑定 `127.0.0.1`，启动时生成随机 session token；Control Center 自动持有，不要求用户输入。

## 33.5 首次启动向导

第一次打开必须自动进入 Wizard，不能显示一堆空白配置。

### Step 1：Minecraft 环境

显示：

```text
Minecraft 文件夹
[ C:\Users\...\.minecraft ] [自动寻找] [选择文件夹]

检查：
Forge 1.20.1            ✓/✗
Touhou Little Maid      ✓/✗
Maid AI Bridge          ✓/✗
```

自动寻找至少检查：

```text
%APPDATA%\.minecraft
```

并允许用户手动选择第三方启动器实例目录。

检测逻辑不得只看文件名；优先读取 JAR `META-INF/mods.toml` / manifest，识别 modId、Minecraft/Forge 版本。

如果 Bridge 不在 `mods`：

```text
未检测到 Maid AI Bridge。
[打开Bridge所在文件夹] [打开Minecraft mods文件夹]
```

第一版不要求自动复制安装，以免误操作实例。

### Step 2：日常 AI API

字段：

```text
类型：OpenAI Compatible
API Base URL
API Key
模型名称
[测试连接]
```

### Step 3：五日研发 API / Harness

字段：

```text
[ ] 使用与日常AI相同API
或
API Base URL
API Key
模型名称

R&D 每周期预算：100,000,000 Token
周期：每 5 个 Minecraft 游戏日

[测试研发API]
```

如果 R&D 使用独立 Harness CLI/工程，再提供高级项：

```text
Harness 运行器：[自动检测/选择路径]
Harness 工作目录：[选择]
[测试Harness]
```

普通用户不填写命令模板；程序使用项目内固定 Adapter。

### Step 4：完成检查

```text
Minecraft 环境 ✓
日常 API ✓
研发 API/Harness ✓
数据目录 ✓

[完成并进入控制台]
```

测试失败时允许保存后进入，但主界面必须明确显示红色状态并禁止“启动自主 AI”。

## 33.6 API 设置页

必须分成两个卡片：

```text
日常行动模型
  Base URL
  API Key [显示/隐藏]
  Model
  Timeout
  [测试连接]

五日研发模型
  [使用日常配置]
  Base URL
  API Key [显示/隐藏]
  Model
  每周期Token
  周期天数
  [测试连接]
```

高级设置折叠区：

- `chat_completions_path`；
- 自定义 HTTP Header；
- 超时；
- 最大重试；
- 代理（以后需要再加，v1 可不做）；
- 是否流式。

### API Key 显示规则

默认：

```text
sk-************************
```

点击“显示”需要当前 Windows 会话解密，只在输入框临时显示；切换页面后重新隐藏。

## 33.7 API 配置代码结构

`control-center/src/maid_control/models/settings.py`：

```python
from pydantic import BaseModel, Field

class ProviderProfile(BaseModel):
    profile_id: str
    display_name: str
    base_url: str
    model: str
    api_key_secret_id: str
    chat_completions_path: str = "/chat/completions"
    timeout_seconds: int = Field(120, ge=5, le=1800)
    max_retries: int = Field(3, ge=0, le=10)
    extra_headers_secret_id: str | None = None

class RuntimeBudgetSettings(BaseModel):
    enabled: bool = False
    max_per_game_day: int | None = None
    max_per_real_hour: int | None = None

class RndBudgetSettings(BaseModel):
    budget_per_cycle: int = 100_000_000
    cycle_game_days: int = 5

class AppSettings(BaseModel):
    minecraft_instance_dir: str | None = None
    runtime_profile: ProviderProfile | None = None
    rnd_profile: ProviderProfile | None = None
    runtime_budget: RuntimeBudgetSettings = RuntimeBudgetSettings()
    rnd_budget: RndBudgetSettings = RndBudgetSettings()
```

**禁止在此 model 中保存 `api_key: str` 明文。**

## 33.8 API Key 安全存储

Windows 正式版优先使用 `keyring` -> Windows Credential Manager。

约定 service names：

```text
MaidAI/runtime/<profile_id>
MaidAI/rnd/<profile_id>
MaidAI/headers/<profile_id>
```

接口：

```python
class CredentialStore:
    def put_secret(self, secret_id: str, value: str) -> None: ...
    def get_secret(self, secret_id: str) -> str | None: ...
    def delete_secret(self, secret_id: str) -> None: ...
```

如果 `keyring` backend 不可用，Windows fallback 可以使用 DPAPI (`CryptProtectData/CryptUnprotectData`)；不得退化成明文 `config.json`。

日志过滤器必须对以下字段无条件脱敏：

```text
Authorization
api_key
x-api-key
Cookie
Set-Cookie
自定义 header 中标记 sensitive=true 的值
```

## 33.9 模型名与中转站兼容

这是产品硬约束。

用户在 Model 输入框填什么，就请求什么：

```python
request_json = {
    "model": profile.model,
    "messages": messages,
    **other_options,
}
```

不允许：

```python
if "deepseek" in model:
    model = "deepseek-chat"   # 禁止
```

控制中心只能验证“返回成功/失败”，不得偷偷修正模型名。

## 33.10 “测试连接”实现

不要只调用 `/models`，因为许多 OpenAI-compatible 中转站不实现或限制该接口。

测试应发送一次最小 Chat Completion：

```python
async def probe_chat(profile: ProviderProfile, api_key: str) -> ProbeResult:
    url = join_url(profile.base_url, profile.chat_completions_path)
    payload = {
        "model": profile.model,
        "messages": [{"role": "user", "content": "Reply with OK only."}],
        "max_tokens": 8,
        "temperature": 0,
    }
    ...
```

UI 返回：

```text
连接成功
HTTP: 200
模型：用户原样填写的名称
延迟：1.84s
Prompt tokens: ...
Completion tokens: ...
```

失败必须显示简单原因和可展开技术详情：

```text
连接失败：API Key 无效（HTTP 401）
[查看技术详情]
```

不要只显示 `Exception: ...`。

## 33.11 主仪表盘 Dashboard

至少包含：

```text
Minecraft       ● 运行中/未检测
Maid Bridge     ● 已连接/断开
Agent Core      ● 运行中/停止
日常 API        ● 正常/失败
R&D Harness     ● 就绪/运行/错误
绑定女仆        ● <name>/未绑定

[启动 AI] [暂停 AI] [停止 AI]

游戏日：Day 13
模式：自主发展
长期目标：……
阶段目标：……
当前目标：……
当前任务：挖掘铁矿 18/32
生命：18/20
食物：14/20
附近威胁：Zombie ×3 / 最近11.4格
```

按钮状态必须与实际状态机绑定：

- Agent 未运行时“开始自主”禁用；
- Bridge 未连接时“绑定女仆”禁用；
- API 测试失败时“开始自主”给明确确认/阻止策略；
- 已自主运行时“启动”禁用，“暂停”启用。

## 33.12 决策摘要

UI 必须显示模型生成的**可公开摘要**，而不是私有 chain-of-thought。

Agent 每次重大 Goal/Strategy 变化时额外输出：

```json
{
  "decision_summary": "铁资源不足且丧尸压力正在增加，因此优先补充铁和护甲。",
  "evidence": [
    "iron_ingot=7",
    "recent_hostile_encounters=4"
  ]
}
```

用于视频展示和用户理解。

## 33.13 女仆发现与绑定 GUI

不得要求 UUID。

Bridge 新增 `DISCOVER_MAIDS`：

```json
{
  "type": "MAID_LIST",
  "maids": [
    {
      "uuid": "...",
      "entity_id": 382,
      "name": "Maid #2",
      "owner_uuid": "...",
      "owner_name": "Player",
      "dimension": "minecraft:overworld",
      "position": [10.5, 64.0, -20.5],
      "distance_to_owner": 7.2,
      "loaded": true
    }
  ]
}
```

控制中心：

```text
发现 3 名可绑定女仆
○ Maid #1  3m
● Maid #2  7m
○ Maid #3 18m
[绑定选中女仆]
```

绑定后持久化 UUID，但 UI 只显示名称/距离/维度。

## 33.14 Runtime 页面

展示：

```text
长期目标
阶段目标
当前目标
当前 Plan Steps：完成/进行/等待/失败
当前 Action
最近一次失败与恢复
当前 Strategy Summary
```

至少能查看最近 50 条高层事件，不需要打开日志文件。

## 33.15 Token 页面

必须从 `TokenLedger` 实时订阅：

```text
日常 AI
今天：428,612
当前5日周期：2,613,741

研发 Harness
37,283,192 / 100,000,000
剩余：62,716,808
37.2%
```

进度条不能用浮点除零；预算关闭时显示“未设置上限”，而不是 0/0。

## 33.16 Memory / Skills 页面

Memory 至少按：

- 地点；
- 资源；
- 建筑；
- 重要事件；
- 战略摘要。

Skills 至少显示：

```text
名称
版本
调用次数
成功率
平均耗时
最近失败
来源：builtin/generated/rnd
```

这些页面默认只读，避免用户误删 AI 长期记忆；高级“清除/重置”操作必须二次确认并自动备份。

## 33.17 R&D Center

状态：

```text
READY / RUNNING / VALIDATING / COMPLETED / FAILED / WAITING_USER
```

进行中显示：

```text
Cycle 4: Day 15 -> 20
预算：100,000,000
已使用：41,272,283
当前阶段：源码分析 / 编程 / 测试 ...
当前公开摘要：正在改善大型施工效率
```

完成显示：

```text
新增能力
修改能力
生成的Mod
推荐的第三方Mod
测试结果
是否需要人工操作
```

按钮：

```text
[打开产物文件夹]
[查看研发报告]
[查看测试结果]
[标记为已安装/已处理]
```

## 33.18 Mod Handoff 页面

每个 Mod 卡片必须包含：

```text
名称 / 版本
来源：AI自研 / GitHub / Modrinth
Minecraft版本
Loader
依赖
下载/产物路径
SHA-256
AI为什么需要它（公开摘要）
兼容性测试状态
```

AI 只输出和通知；**禁止自动复制到 `mods`**。

`[打开 mods 文件夹]` 可以提供，但复制动作仍由用户完成。

## 33.19 日志与诊断页面

用户日志分级：

```text
INFO：正常事件
WARN：自动恢复过的问题
ERROR：需要关注
ACTION_REQUIRED：需要用户安装/重启/重新填API
```

按钮：

```text
[导出诊断包]
[打开日志文件夹]
[复制错误编号]
```

不要要求用户“去第 3876 行找异常”。

## 33.20 Windows 数据目录

正式版所有可写数据放：

```text
%APPDATA%\MaidAI\
  config\
    app.json                # 不含API Key
  state\
    maid_ai.sqlite3
  logs\
  handoff\
  backups\
  diagnostics\

%LOCALAPPDATA%\MaidAI\
  cache\
  runtime\
```

程序安装目录只读也必须正常运行。

## 33.21 一键构建与打包

开发仓库必须有：

```text
BUILD_ALL_WINDOWS.bat
PACKAGE_WINDOWS.bat
```

`PACKAGE_WINDOWS.bat` 完成：

1. 构建 Forge Bridge；
2. 运行 Python tests；
3. PyInstaller 打包控制中心；
4. 把 Bridge JAR 复制到 `dist/MaidAI/`；
5. 写版本 manifest；
6. 生成 SHA-256；
7. 输出一个可直接交付目录。

最终用户不运行这些脚本；这是给 Codex/开发者构建正式包。

## 33.22 启动/停止容错

Control Center 启动：

```text
1. 加载非敏感配置
2. 解密/读取凭据
3. 检查 agent worker 是否残留
4. 启动 worker
5. 等待 control API ready
6. 检查 Minecraft/Bridge
7. 刷新 Dashboard
```

如果端口被占用：

- 先判断是否是自己的残留进程；
- 能安全接管则接管；
- 不能则自动选择备用内部端口并把 Bridge 端口配置同步/提示重启；
- 不得只抛 `[WinError 10048]` 给用户。

正常停止：

```text
UI -> STOP_AUTONOMOUS
   -> Agent cancel current plan
   -> Bridge STOP/SAFE_IDLE
   -> flush SQLite/logs
   -> worker shutdown
```

## 33.23 非程序员产品硬要求

最终成品必须保证完全不懂编程的用户也能直接使用，正常使用过程中不得要求用户运行任何源码命令。

通过条件：

1. 双击 `Maid AI Control.exe`；
2. 首次向导能找到/选择 Minecraft；
3. UI 填写两套 API；
4. 测试连接能明确成功/失败；
5. API Key 保存后重启软件仍可用；
6. 启动 AI 无需终端；
7. GUI 发现并绑定女仆；
8. Dashboard 能看到真实生命/背包/目标/任务；
9. 可以开始、暂停、停止；
10. Token 页面数字变化正确；
11. R&D 完成后能看到产物和“打开文件夹”；
12. Mod 不会被自动安装；
13. 一键导出诊断 ZIP；
14. 全流程不需要编辑 `.env`/JSON/TOML；
15. 全流程不需要复制 UUID 或手敲 WebSocket 地址。

上述任一项缺失，都表示产品本身尚未完成。

## 33.24 Codex 必须新增的核心类/文件

```text
control-center/
  src/maid_control/
    app.py
    models/settings.py
    models/runtime_status.py
    services/process_supervisor.py
    services/config_repository.py
    services/credential_store.py
    services/api_probe.py
    services/agent_control_client.py
    services/minecraft_locator.py
    services/diagnostics_exporter.py
    services/handoff_service.py
    ui/main_window.py
    ui/wizard.py
    ui/pages/*.py

agent-core/
  src/maid_agent/control/api_server.py
  src/maid_agent/control/events.py

maid-ai-bridge/
  .../discover/MaidDiscoveryService.java
```

控制中心不得直接 import Forge/Java 实现细节；通过 Agent Control API 解耦。

---

# 34. 开发期间的最小自检原则

本项目不建立独立“测试体系”，也不把测试工具本身作为开发目标。Codex 在开发过程中只做**与当前改动直接相关、能快速暴露真实错误的最小自检**。

允许的自检仅包括：

- 当前 Java / Forge 模块是否能编译；
- 当前 Python 模块是否能启动并完成基本调用；
- 当前 UI 页面/按钮是否能实际触发对应后端；
- 当前协议消息能否正常 encode/decode；
- 当前修改是否出现明显崩溃、死循环、无法连接或数据格式错误；
- 修复一个真实 Bug 后，只重跑与该 Bug 直接相关的场景。

明确禁止：

- 为本项目专门建设大规模 Unit Test / GameTest / Fake Bridge 测试平台；
- 为测试工具再开发测试工具；
- 为辅助验证建立独立子项目；
- 为了“证明正确”而反复扫描、哈希、审计整个 Minecraft / JDK / Python / Git；
- 因为辅助测试没有达到完美覆盖而阻塞产品主线；
- 在核心功能尚未完成时耗费大量时间扩展测试矩阵。

**所有核心功能完成、完成打包和真实模块集成后，再按照第 39 章进行一次最终整体运行检查。**

---


# 35. 一次性开发的基础执行顺序

本项目从空工程开始持续开发到完整产品。下面的执行步骤只负责维持工程依赖顺序，不产生阻塞式 Gate，也不能左做一点右做一点。

下面 12 个步骤是**工程执行顺序**，不是停工点。前一步形成可供后一步使用的最小可运行接口后即可继续；独立模块可在同一步内部并行。

## Step 1：建立工程骨架与参考源

目标：让所有后续模块有稳定目录和真实参考源码。

执行：

- 创建 monorepo；
- 建立 `maid-ai-bridge / agent-core / control-center / harness / runtime / references / dist`；
- clone 第 2 章所有核心参考仓库；
- 记录 URL / branch / commit SHA / license；
- 创建 `THIRD_PARTY_NOTICES.md`；
- 建立 Forge 1.20.1 + Java 17 基础工程；
- 解析 Touhou Little Maid 1.5.3。

**这里不开发额外审计系统。** Reference Lock 的目的只是让 Codex知道使用了哪个真实版本，不允许把 Reference verifier 本身做成一个子项目。

---

## Step 2：先打通真正的 `EntityMaid` 身体

目标：证明后续所有智能最终控制的都是真实女仆。

执行：

- `@LittleMaidExtension`；
- `MaidAutonomousTask`；
- Maid 发现/绑定；
- Observation；
- `MotionArbiter`；
- Navigation；
- `move_to / stop / look_at`；
- 服务端线程队列；
- WebSocket 基础协议。

不要接 DeepSeek 之后才补身体层。

---

## Step 3：建立 Agent Core 基础运行时

目标：让外部主脑能够长期运行并与 Bridge 可靠通信。

执行：

- Python Agent Core；
- WebSocket Server；
- session/reconnect；
- state cache；
- Action Router；
- Goal/Plan/Task 数据模型；
- cancellation/timeout；
- SQLite 数据层；
- 运行日志。

此时可先用程序生成测试 Goal，不需要等待 LLM。

---

## Step 4：同步完成 Control Center 产品入口

目标：用户从此以后不需要直接碰 Python/JSON/终端。

执行：

- PySide6 主窗口；
- 首次启动向导；
- Minecraft/HMCL 检测；
- Runtime/R&D API Profile；
- CredentialStore；
- API 测试连接；
- Agent 进程启动/停止；
- Bridge 状态；
- Maid 列表与绑定；
- Dashboard；
- 日志/诊断；
- PyInstaller 打包。

Control Center 必须和后端同步发展，不允许最后再套壳。

---

## Step 5：补齐原子 Action 与确定性任务层

目标：形成不依赖 LLM 微操的 Minecraft 操作能力。

实现：

- break/place；
- pickup；
- equip；
- eat；
- craft；
- smelt；
- container transfer；
- attack/retreat；
- use item/block；
- dig area；
- build from blueprint primitive；
- task pause/resume/cancel；
- postcondition verification。

所有真实世界变化仍由 `EntityMaid` 执行。

---

## Step 6：建立原版生存闭环

目标：不接复杂战略时，确定性执行器本身已经能够支撑长期生存任务。

能力链：

```text
找木头
→ 工具
→ 食物
→ 石头
→ 铁
→ 熔炼
→ 装备
→ 基础住所
→ 储存
→ 农业/稳定食物
```

加入 ReflexEngine 和常见失败恢复。

不要在这里预写丧尸战略答案。

---

## Step 7：接入 DeepSeek Runtime 与真正自主循环

目标：从“程序自动执行”升级为“AI 自己决定发展”。

实现：

- OpenAI-compatible Provider；
- Runtime model；
- 模型名原样透传；
- Tool/structured output；
- Autonomous Loop；
- StrategyState；
- Goal selection；
- Planner；
- Memory retrieval；
- decision summary；
- Token ledger。

此后 AI 应能在不给人工游戏命令时自己选择生存与发展目标。

---

## Step 8：长期 Memory、Skill 与经验系统

目标：AI 不只是“每次重新想”，而是形成可积累能力。

实现：

- world memory；
- event memory；
- strategy memory；
- skill registry；
- skill statistics；
- skill version；
- failure/refinement queue；
- repeat task → Skill reuse；
- checkpoint/recovery。

---

## Step 9：丧尸压力与战略闭环

目标：让 AI 根据真实袭击事实自己形成防御策略。

实现：

- Threat Analytics；
- 攻击方向/数量/伤害/破坏统计；
- strategic review trigger；
- 资源与基地风险评估；
- 通用工程工具。

禁止直接实现 `build_zombie_moat`、`build_lava_trap` 等答案函数。

---

## Step 10：R&D Harness 与五日自我研发

目标：完成本项目最重要的“能力进化”机制。

实现：

- 游戏日触发器；
- Runtime/R&D 独立 Token Ledger；
- 五日 Handoff；
- 最近五日总结；
- 源码/Skill/失败数据输入；
- code model / research loop；
- workspace；
- Skill 修改；
- Agent 修改；
- Mod research；
- Forge Addon build；
- Handoff manifest；
- Control Center R&D Center。

开发测试时使用小额 Token 验证工作流；正式运行预算可配置为一亿 Token/周期，不需要为了测试真的消耗一亿。

---

## Step 11：大型建筑与中后期发展

目标：生存稳定后 AI 能真正把剩余发展能力投入建设，而不是永远循环挖矿。

实现：

- Blueprint DSL；
- 建筑材料统计；
- 材料缺口自动转资源 Goal；
- 分区施工；
- checkpoint；
- 中断恢复；
- 建筑原语；
- `.litematic` 研究；
- 可选视觉复查；
- 外部建筑 Mod 搜索/Handoff。

---

## Step 12：全部模块集成、打包和最终修 Bug

全部核心组件都完成后再做一次整体集成：

```text
Control Center
→ Agent Core
→ DeepSeek Runtime
→ WebSocket
→ Maid AI Bridge
→ EntityMaid
→ Minecraft
→ Memory/Skill
→ R&D Harness
→ Handoff
```

出现问题时只修真正失败的产品模块，不重新发明治理/审计/验证系统。

最后输出：

- `Maid AI Control.exe`；
- `MaidAI-Bridge.jar`；
- Agent Core runtime；
- Harness；
- 一键启动脚本；
- 用户说明；
- 最终运行日志/已知问题。

---

# 36. Codex 工程与效率硬规则

1. 真正游戏身体必须是 `EntityMaid`。
2. Mineflayer 只能作为参考项目的历史实现，不能成为生产身体。
3. LLM 不进行 Tick/WASD 微操。
4. 网络线程不得直接修改 world。
5. 一个女仆只有一个 Motion Owner。
6. Action 必须有 timeout/cancel/result；Goal 必须有真实 postcondition。
7. 正常生存禁止使用 teleport/xray/give/setblock 伪装成功。
8. API Base URL 和模型名称按用户输入原样透传，不擅自映射。
9. API Key 不硬编码、不写普通日志；正式产品通过 CredentialStore/DPAPI 等安全方式保存。
10. Runtime 与 R&D Token 分账。
11. Control Center 是正式产品，不是最后补的 Debug UI。
12. AI 找到/开发 Mod 后只 Handoff，不自动修改用户正式 `mods` 或重启游戏。
13. 第三方项目只按本策划案指定目标借鉴，不整仓复制不同版本/Fabric/Mineflayer 生产代码。
14. 允许最多 5 个并行子代理；所有代理使用当前环境允许的最高 reasoning。Lead 负责依赖顺序和合并，子代理必须有明确文件所有权。
15. 子代理主要用于并行开发：Forge/EntityMaid、Agent Core、Control Center/Harness、Survival/Skill/Building、QA/Bug Hunter。
16. QA 只找真实产品 Bug，不允许把 QA 工具、安全扫描器、hash verifier、reference verifier 自身发展成新项目。
17. 不创建独立的前置审计工程，不设置逐步骤 Gate，不在步骤之间停工等待确认。
18. 不做 Git object/reflog 全库安全研究、JDK/Python 全文件供应链审计、SFX/gzip/junction/TOCTOU 研究，除非它们真的成为当前产品 Bug。
19. Reference Lock 只需要真实 URL / commit SHA / license / 使用目的。
20. Forge 只针对目标 Minecraft/Forge/TLM 环境，不做无必要的多个小版本兼容矩阵。
21. 上下文压缩后不要重新阅读全文；只重新读取 `AGENTS.md`、当前执行步骤、`RUN_STATE.md` 简短摘要和相关技术章节。
22. `RUN_STATE.md` 只记录当前完成模块、正在做什么、真实 blocker、下一步，禁止写成长篇审计日志。
23. 同一实现路线如果连续两次明确失败，优先换成熟参考实现或简化内部实现；不要无限研究同一个辅助问题。
24. 一个模块最小可用接口形成后就继续下一个有依赖的步骤，不需要为每一步生成长报告。
25. 开发过程中只运行与当前改动直接相关的快速自测；所有核心功能组装完成后再进行一次整体运行检查。
26. 用户中途不参与开发；只有无法自行解决的外部硬阻塞（例如缺少必须由用户提供的文件/权限）才允许停止询问。
27. 不频繁输出状态汇报；持续开发到完整产品或真实外部硬阻塞。

---

# 37. 完整产品 DoD（Definition of Done）

只有最终完整系统同时具备以下能力才称为完成；这些是最终产品目标，不是逐项等待用户签字的 Gate。

### Minecraft / Bridge

- [ ] Forge 1.20.1 可构建；
- [ ] Touhou Little Maid 1.5.3 作为真实依赖；
- [ ] 可发现和绑定唯一 `EntityMaid`；
- [ ] Agent 与 Maid 通过 localhost 通信；
- [ ] 断线时女仆安全停止；
- [ ] 可观察真实坐标/生命/背包/附近实体；
- [ ] 默认没有地下矿物坐标泄露；
- [ ] `move_to` 真正移动 Maid；
- [ ] `break_block` 真正由 Maid 挖掘；
- [ ] `place_block` 真正消耗 Maid 物品；
- [ ] craft/smelt/container 使用真实资源；
- [ ] combat/reflex 不依赖 LLM Tick 响应；
- [ ] MotionArbiter 保证单一运动所有权。

### Agent / 自主发展

- [ ] Runtime LLM 自己提出 Goal；
- [ ] Planner 可拆分复杂 Goal；
- [ ] Action/Step/Goal 三层结果分离；
- [ ] Goal 使用真实后置条件；
- [ ] AI 能完成早期原版生存；
- [ ] AI 能长期管理食物/资源/装备/基地；
- [ ] AI 能根据丧尸压力自行改变战略；
- [ ] SQLite/持久化长期记忆；
- [ ] 重启后恢复重要状态；
- [ ] Skill 能保存、统计、复用、版本化；
- [ ] runtime/rnd Token 完全分账。

### R&D Harness

- [ ] 每 5 游戏日形成研发周期；
- [ ] 能汇总过去五日失败/Skill/资源/威胁数据；
- [ ] 能分析 Agent/Skill 源码；
- [ ] 能输出 Skill/Agent 改进；
- [ ] 能开发新的辅助 Addon/Mod；
- [ ] 能搜索兼容外部 Mod；
- [ ] 研发产物进入 handoff；
- [ ] 不自动污染正式 `mods`；
- [ ] Control Center 能显示研发原因、进度、Token和产物。

### 建筑与长期发展

- [ ] 支持 Blueprint/建筑 DSL；
- [ ] 能计算大型工程材料；
- [ ] 缺材料时自动生成资源子目标；
- [ ] 能由 `EntityMaid` 真实施工；
- [ ] 大工程可 checkpoint/recovery；
- [ ] AI 生存稳定后能主动转向大型建设；
- [ ] 可选接入 `.litematic` / 建筑辅助 Mod Handoff。

### 非程序员产品层

- [ ] `Maid AI Control.exe` 双击运行；
- [ ] 用户不需要 Python/IDE/CMD；
- [ ] GUI 可选择/检测 Minecraft；
- [ ] GUI 可填写 Runtime/R&D API；
- [ ] API Key 不明文放普通配置；
- [ ] 模型名/Base URL 原样使用；
- [ ] GUI 可发现/绑定女仆；
- [ ] GUI 可启动/暂停/停止自主运行；
- [ ] Dashboard 显示目标/任务/生命/威胁/简短决策摘要；
- [ ] Token、Memory、Skills、R&D、Handoff、日志页面可用；
- [ ] 一键打开 AI 生成/推荐的 Mod 文件夹；
- [ ] 能导出诊断包；
- [ ] 有一键启动脚本和中文用户说明。

---

# 38. Codex 一次性开发组织方式

项目从空目录开始。Codex 开工后不要重新设计项目，按照第 35 章 Step 1→12 持续推进。

建议最多 5 个并行工作流：

```text
Agent A：Forge/TLM Bridge + EntityMaid
Agent B：Agent Core + Runtime LLM + Memory/Goal
Agent C：Control Center + Process Supervisor + API/Credential
Agent D：Survival/Skill/Strategy/Harness/Building（按依赖逐步接入）
Agent E：QA/Bug Hunter，只针对已经接通的真实产品功能持续找 Bug
```

Lead 负责：

- 维护接口契约；
- 防止重复实现；
- 控制依赖顺序；
- 合并模块；
- 遇到一个模块真实 blocker 时，把其它独立模块继续向前推进；
- 不创建逐步骤 Gate；
- 不因 QA 辅助问题停止整个项目。

第 35 章的 Step 只是施工顺序。Codex 不需要每完成一个 Step 就停下来汇报或等待用户。

---

# 39. 全部功能完成后的唯一整体运行检查

所有核心功能完成、打包并集成后，只进行一次最终整体运行检查，重点检查真正产品链路，而不是开发治理工具。

至少覆盖：

### A. 非程序员启动

- 双击 `Maid AI Control.exe`；
- GUI 配 API；
- 启动 Agent；
- 发现并绑定真实女仆；
- 不打开终端/编辑配置。

### B. Day 1 自主生存

- 不给人工游戏任务；
- AI 自己形成至少多个连续目标；
- `EntityMaid` 真正获得资源、工具、食物和基础住所。

### C. 身体与失败恢复

- 移动/挖掘/放置/合成/容器/战斗来自真实 Maid；
- 不可达、目标变化、背包满、API 短暂失败不会造成永久死循环。

### D. 丧尸战略

- 只给威胁事实；
- AI 能自己形成至少一种防御调整；
- 不依赖预制“丧尸陷阱”答案函数。

### E. Memory / Skill

- 记得重要地点和资源；
- 重复多步任务能形成/复用 Skill；
- 重启后关键状态可恢复。

### F. R&D

- 用小额测试预算模拟第 5 日触发；
- runtime/rnd 分账正确；
- 形成 Handoff；
- 能产生至少一种 Skill/Agent/Mod 改进路径；
- 不为了测试真的消耗一亿 Token。

### G. 建筑

- 生成一个中型以上 Blueprint；
- 统计材料；
- 缺材料会转资源目标；
- 真实 Maid 施工；
- 中断后继续。

发现 Bug 后只修对应产品模块并重测相关场景，不重新建立独立审计工程或多层 Gate。

最终只需输出简洁 `FINAL_RUN_REPORT.md`：

- 已完成功能；
- 最终运行结果；
- 仍存在的真实 Bug；
- 需要用户手动安装的 Mod/外部依赖；
- 最终产物路径。

---

# 40. 参考源核验说明

本策划案截至 2026-08-26 已核验的关键事实：

1. Touhou Little Maid Forge 1.20.1 正式版已经提供可扩展的 `EntityMaid` / `IMaidTask` / `ILittleMaid` 体系。
2. `ILittleMaid` 1.20 分支已经存在 `registerAITool` 与 `registerAIMaidContext`，可以作为女仆原生 AI 兼容入口，但本项目主脑仍为外部 Agent Core。
3. `EntityMaid` 已提供背包、导航、方块破坏等真实实体能力，本项目无需用假玩家代替身体。
4. Maid Intelligence 已经证明 TLM 1.20.1 Addon 可以实现 A*/PathExecutor/扫描/挖掘；它同时暴露了移动控制冲突，因此本项目必须自己实现单一 Motion Owner。
5. mc_aiplayer 的 typed Goal + Planner + Executor + postcondition 适合作为确定性执行架构参考，但其 Fabric/不同 MC 版本代码不直接移植。
6. Minecraft-Agent 已提供自主循环、Plan、World Memory、Skill Store、Recovery 等外部 Agent 结构；Mineflayer Bridge 必须替换。
7. Minecraft Agent Swarm 已提供 Skill 成功率、动态技能、失败修复、trajectory 等自我改进参考。
8. Mindcraft 提供 runtime/code/vision 等模型分离和上下文管理参考。
9. Voyager / Odyssey 可用于 Skill Library、自动课程和组合技能研究。
10. Minecraft Agentic Builder / APT / Multimodal Agent 可用于中后期大型建筑、Blueprint、视觉复查和 `.litematic` 研究。

Codex clone 后如果上游 HEAD/API 已变化，应以真实源码为准，记录所用 commit，不静默改变本项目核心架构。

---

# 41. 核心项目与资料地址汇总

## 41.1 身体与 Forge 执行层

**Touhou Little Maid**  
GitHub：<https://github.com/TartaricAcid/TouhouLittleMaid>  
1.20 分支：<https://github.com/TartaricAcid/TouhouLittleMaid/tree/1.20>  
目标发布版：<https://www.curseforge.com/minecraft/mc-mods/touhou-little-maid/files/8061847>  
开发 Wiki：<https://github.com/TartaricAcid/TouhouLittleMaid/wiki/%E5%A6%82%E4%BD%95%E5%BC%80%E5%A7%8B>

**Maid Intelligence**  
<https://github.com/RhineIris/touhou-little-maid-maidintelligence>

**qxfMCAI**  
<https://github.com/QXF19/qxfMCAI>

---

## 41.2 Agent / Goal / Memory / Skill

**mc_aiplayer / AIBot**  
<https://github.com/zoyluoblue/mc_aiplayer>

**Minecraft-Agent**  
<https://github.com/Kevin-Liu-01/Minecraft-Agent>

**Minecraft Agent Swarm**  
<https://github.com/JesseRWeigel/minecraft-agent-swarm>

**Mindcraft**  
<https://github.com/mindcraft-bots/mindcraft>

**mc-agents**  
<https://github.com/jblemee/mc-agents>

**Voyager**  
<https://github.com/MineDojo/Voyager>  
<https://voyager.minedojo.org/>

**Odyssey**  
<https://github.com/zju-vipa/odyssey>

---

## 41.3 建筑与视觉

**Minecraft Agentic Builder**  
<https://github.com/NoblerWorks-HQ/minecraft-agentic>

**Minecraft Multimodal Agent**  
<https://github.com/win10ogod/mc-multimodal-agent>

**APT 建筑规划研究**  
<https://arxiv.org/abs/2411.17255>

**Forgematica**  
<https://modrinth.com/mod/forgematica>

---

## 41.4 AI 自主寻找 Mod / 脚本

**Modrinth API**  
<https://docs.modrinth.com/api/operations/getprojectversions/>

**KubeJS 文档**  
<https://kubejs.com/wiki/folder-structure/startup-scripts>

---

# 42. 最终工程判断

这个项目不是一个简单的 API 接入任务，而是一个完整的 **Embodied Agent Runtime + Windows 产品控制层 + 周期性研发系统**。

核心链必须完整成立：

```text
真实世界观察
    ↓
自主战略与 Goal
    ↓
可恢复 Plan / Task
    ↓
确定性 Action
    ↓
真实 EntityMaid 执行
    ↓
真实后置条件验证
    ↓
长期 Memory
    ↓
Skill 成长
    ↓
丧尸压力下的战略变化
    ↓
每五日 R&D 自我改进
    ↓
大型建设与长期发展
```

任何一个环节都不能用“LLM 看起来很聪明”来代替真实执行。

同时，项目最终必须是一个不会编程的人能直接使用的产品：API、模型、启动、女仆绑定、AI状态、Token、R&D、Handoff 和诊断都通过 `Maid AI Control.exe` 完成。

Codex 应按照第 35 章的基础执行顺序一次持续开发到完整产品；这些步骤只负责保持工程依赖清晰，**不设置逐步骤 Gate、不在步骤之间停工，也不允许把辅助验证系统做成主项目。**
