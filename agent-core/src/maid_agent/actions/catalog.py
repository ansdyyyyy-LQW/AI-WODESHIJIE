from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


class ToolValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolArgument:
    name: str
    kind: str
    required: bool = False
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    description: str = ""

    def schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {"description": self.description}
        if self.kind == "number":
            result["type"] = "number"
        elif self.kind == "integer":
            result["type"] = "integer"
        elif self.kind == "boolean":
            result["type"] = "boolean"
        elif self.kind in {"object", "array", "string"}:
            result["type"] = self.kind
        elif self.kind == "uuid":
            result.update({"type": "string", "format": "uuid"})
        elif self.kind == "position":
            result.update({
                "type": "object",
                "required": ["x", "y", "z"],
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                },
                "additionalProperties": False,
            })
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.choices:
            result["enum"] = list(self.choices)
        if self.default is not None:
            result["default"] = self.default
        return result


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    arguments: tuple[ToolArgument, ...] = ()
    result_description: str = ""
    strict_survival_notes: tuple[str, ...] = ()
    runtime_only: bool = False
    side_effect: bool = False
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def argument_names(self) -> set[str]:
        return {arg.name for arg in self.arguments}

    def arguments_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {arg.name: arg.schema() for arg in self.arguments},
            "required": [arg.name for arg in self.arguments if arg.required],
            "additionalProperties": False,
        }

    def prompt_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments_schema": self.arguments_schema(),
            "result": self.result_description,
            "strict_survival_notes": list(self.strict_survival_notes),
            "runtime_only": self.runtime_only,
        }


P = ToolArgument


def _pos(prefix: str = "") -> tuple[ToolArgument, ...]:
    return (
        P(f"{prefix}x", "number", True),
        P(f"{prefix}y", "number", True),
        P(f"{prefix}z", "number", True),
    )


class ToolCatalog:
    def __init__(self) -> None:
        observe = ("只返回女仆真实可观察到的信息。",)
        close = ("必须在真实交互距离内，不会偷偷移动或远程读取。",)
        self._tools: dict[str, ToolContract] = {
            "get_status": ToolContract("get_status", "读取当前女仆状态。", result_description="StateSnapshot", strict_survival_notes=observe),
            "get_inventory": ToolContract("get_inventory", "读取女仆自身背包。", result_description="inventory", strict_survival_notes=observe),
            "inspect_area": ToolContract("inspect_area", "观察周围可见实体、暴露方块和工作站。", (P("radius", "integer", False, 24, 2, 48),), "observations", observe),
            "find_visible_block": ToolContract("find_visible_block", "在当前可见方块中寻找物品/标签。", (P("query", "string", True), P("limit", "integer", False, 16, 1, 64)), "matches + position", observe),
            "find_place_position": ToolContract("find_place_position", "在女仆附近寻找有支撑、无碰撞且可合法放置方块的位置。", (P("item_id", "string", True), P("radius", "integer", False, 4, 1, 8)), "position", observe),
            "find_entity": ToolContract("find_entity", "在当前观察范围寻找实体。", (P("query", "string", True), P("hostile_only", "boolean", False, False)), "matches", observe),
            "inspect_entity": ToolContract(
                "inspect_entity",
                "读取一个已进入女仆可观察范围的指定实体。",
                (P("uuid", "uuid", True),),
                "存在状态、类型、相对位置、距离、生命状态、移动状态、是否正以女仆为目标、是否可见",
                observe,
            ),
            "inspect_nearby_entities": ToolContract(
                "inspect_nearby_entities",
                "按半径、类别、敌对状态和追踪状态筛选女仆附近可观察实体。",
                (
                    P("radius", "number", False, 16, 1, 48),
                    P("category", "string", False, "ANY", choices=("ANY", "HOSTILE", "PASSIVE", "PLAYER", "ITEM", "OTHER")),
                    P("hostile_only", "boolean", False, False),
                    P("targeting_maid_only", "boolean", False, False),
                ),
                "数量、方向、距离、最近目标和多方向威胁标记",
                observe,
            ),
            "inspect_block": ToolContract(
                "inspect_block",
                "读取女仆可见范围内指定方块的当前状态。",
                _pos(),
                "方块编号、状态、空气、可替换、可交互和存在状态",
                observe,
            ),
            "inspect_local_space": ToolContract(
                "inspect_local_space",
                "检查女仆身边的阻挡、支撑、水火、跌落风险和指定位置空间。",
                (P("target", "position", False, None),),
                "局部空间安全状态",
                observe,
            ),
            "has_item": ToolContract("has_item", "查询女仆自身背包是否有足够物品。", (P("item_id", "string", True), P("count", "integer", False, 1, 1, 1728)), "是否拥有和实际数量", observe),
            "inspect_container": ToolContract("inspect_container", "近距离查看真实容器内容。", _pos(), "slots", close),
            "move_to": ToolContract("move_to", "让真实女仆寻路到坐标。", _pos() + (P("range", "number", False, 1.5, .25, 8), P("speed", "number", False, .8, .1, 1.5)), "remaining_distance", side_effect=True),
            "look_at": ToolContract("look_at", "让女仆看向坐标。", _pos(), side_effect=True),
            "face_position": ToolContract(
                "face_position", "在有限时间内让女仆面向世界坐标。",
                _pos() + (P("max_duration_ticks", "integer", False, 40, 1, 1200), P("tolerance_degrees", "number", False, 5, .5, 45)),
                "最终朝向误差", side_effect=True,
            ),
            "face_entity": ToolContract(
                "face_entity", "在有限时间内面向已观察实体，并可持续跟随朝向。",
                (P("uuid", "uuid", True), P("max_duration_ticks", "integer", False, 100, 1, 1200), P("track", "boolean", False, False)),
                "最终朝向误差", side_effect=True,
            ),
            "stop": ToolContract("stop", "停止当前动作和导航。", side_effect=True),
            "follow_entity": ToolContract("follow_entity", "跟随已观察实体。", (P("uuid", "uuid", True), P("range", "number", False, 2, 1, 16)), side_effect=True),
            "move_forward": ToolContract("move_forward", "按女仆当前朝向向前移动一小段。", (P("max_distance", "number", False, 3, .25, 16), P("max_duration_ticks", "integer", False, 100, 1, 1200), P("stop_condition", "string", False, "ANY", choices=("ANY", "DISTANCE_REACHED", "OBSTACLE"))), side_effect=True),
            "move_backward": ToolContract("move_backward", "按女仆当前朝向向后移动一小段。", (P("max_distance", "number", False, 3, .25, 16), P("max_duration_ticks", "integer", False, 100, 1, 1200), P("stop_condition", "string", False, "ANY", choices=("ANY", "DISTANCE_REACHED", "OBSTACLE"))), side_effect=True),
            "strafe_left": ToolContract("strafe_left", "按女仆当前朝向向左横移一小段。", (P("max_distance", "number", False, 3, .25, 16), P("max_duration_ticks", "integer", False, 100, 1, 1200), P("stop_condition", "string", False, "ANY", choices=("ANY", "DISTANCE_REACHED", "OBSTACLE"))), side_effect=True),
            "strafe_right": ToolContract("strafe_right", "按女仆当前朝向向右横移一小段。", (P("max_distance", "number", False, 3, .25, 16), P("max_duration_ticks", "integer", False, 100, 1, 1200), P("stop_condition", "string", False, "ANY", choices=("ANY", "DISTANCE_REACHED", "OBSTACLE"))), side_effect=True),
            "approach_entity": ToolContract("approach_entity", "靠近已观察实体到指定距离。", (P("uuid", "uuid", True), P("target_distance", "number", False, 2, .5, 32), P("max_duration_ticks", "integer", False, 200, 1, 2400)), side_effect=True),
            "move_away_from_entity": ToolContract("move_away_from_entity", "远离已观察实体到指定距离。", (P("uuid", "uuid", True), P("target_distance", "number", False, 10, 1, 48), P("max_duration_ticks", "integer", False, 200, 1, 2400)), side_effect=True),
            "maintain_distance": ToolContract("maintain_distance", "由连接组件持续调整与实体的距离。", (P("uuid", "uuid", True), P("min_distance", "number", True, minimum=.5, maximum=48), P("max_distance", "number", True, minimum=.5, maximum=48), P("timeout_ticks", "integer", False, 200, 1, 2400)), side_effect=True),
            "jump": ToolContract("jump", "执行一次有限时长的跳跃。", (P("max_duration_ticks", "integer", False, 20, 1, 100),), side_effect=True),
            "sneak_on": ToolContract("sneak_on", "开启潜行状态。", (P("max_duration_ticks", "integer", False, 20, 1, 100),), side_effect=True),
            "sneak_off": ToolContract("sneak_off", "关闭潜行状态。", (P("max_duration_ticks", "integer", False, 20, 1, 100),), side_effect=True),
            "short_sprint": ToolContract("short_sprint", "按当前朝向进行一次有上限的短冲刺。", (P("max_distance", "number", False, 5, .5, 24), P("max_duration_ticks", "integer", False, 100, 1, 600), P("stop_condition", "string", False, "ANY", choices=("ANY", "DISTANCE_REACHED", "OBSTACLE"))), side_effect=True),
            "break_block": ToolContract("break_block", "近距离真实挖掘可见方块。", _pos(), "block_broken", ("不透视，不远程破坏。",), side_effect=True),
            "place_block": ToolContract("place_block", "从女仆背包消耗方块物品并真实放置。", _pos() + (P("item_id", "string", True), P("face", "string", False, "UP", choices=("UP", "DOWN", "NORTH", "SOUTH", "EAST", "WEST"))), "block_placed", ("必须消耗真实背包物品。",), side_effect=True),
            "use_block": ToolContract("use_block", "近距离使用受支持的方块交互。", _pos(), "interaction", ("不使用 setBlock 伪造交互；不支持就返回 UNSUPPORTED。",), side_effect=True),
            "use_item": ToolContract("use_item", "使用女仆背包中的物品。", (P("item_id", "string", True),), side_effect=True),
            "use_main_hand": ToolContract("use_main_hand", "使用女仆当前主手物品。", (P("max_duration_ticks", "integer", False, 80, 1, 1200),), "真实物品使用结果", side_effect=True),
            "use_off_hand": ToolContract("use_off_hand", "使用女仆当前副手物品。", (P("max_duration_ticks", "integer", False, 80, 1, 1200),), "真实物品使用结果", side_effect=True),
            "use_item_on_block": ToolContract("use_item_on_block", "用指定手中物品对近距离方块面执行真实交互。", (P("position", "position", True), P("face", "string", True, choices=("UP", "DOWN", "NORTH", "SOUTH", "EAST", "WEST")), P("hand", "string", False, "MAIN_HAND", choices=("MAIN_HAND", "OFF_HAND"))), "真实方块使用结果", close, side_effect=True),
            "interact_block": ToolContract("interact_block", "近距离对指定方块面执行原生统一交互。", (P("position", "position", True), P("face", "string", False, "UP", choices=("UP", "DOWN", "NORTH", "SOUTH", "EAST", "WEST")), P("hand", "string", False, "MAIN_HAND", choices=("MAIN_HAND", "OFF_HAND"))), "真实方块交互结果或明确的不支持返回码", close, side_effect=True),
            "pickup_nearby": ToolContract("pickup_nearby", "拾取附近掉落物。", (P("radius", "number", False, 12, 1, 24), P("item_id", "string", False, "")), side_effect=True),
            "equip": ToolContract("equip", "装备背包中的物品。", (P("item_id", "string", True), P("slot", "string", False, "MAINHAND", choices=("MAINHAND", "OFFHAND", "HEAD", "CHEST", "LEGS", "FEET"))), side_effect=True),
            "select_item": ToolContract("select_item", "从女仆背包选择物品并移到主手。", (P("item_id", "string", True),), side_effect=True),
            "move_item_to_main_hand": ToolContract("move_item_to_main_hand", "把背包中的指定物品移到主手。", (P("item_id", "string", True),), side_effect=True),
            "move_item_to_off_hand": ToolContract("move_item_to_off_hand", "把背包中的指定物品移到副手。", (P("item_id", "string", True),), side_effect=True),
            "craft": ToolContract("craft", "只通过普通 CraftingRecipe 合成，并真实消耗材料。", (P("item_id", "string", True), P("count", "integer", False, 1, 1, 256)), "requested + produced", ("不能把熔炼/切石等配方当普通合成。",), side_effect=True),
            "smelt": ToolContract("smelt", "使用真实熔炉、真实燃料和真实配方熔炼。", _pos() + (P("input_item_id", "string", True), P("output_item_id", "string", True), P("count", "integer", False, 1, 1, 256)), "requested + collected", close, side_effect=True),
            "transfer_container": ToolContract("transfer_container", "在近距离和真实容器之间转移物品。", _pos() + (P("item_id", "string", True), P("count", "integer", False, 1, 1, 1728), P("direction", "string", False, "TO_CONTAINER", choices=("TO_CONTAINER", "FROM_CONTAINER")), P("allow_partial", "boolean", False, True)), "requested + moved", close, side_effect=True),
            "eat": ToolContract("eat", "吃女仆背包中的真实食物。", (P("item_id", "string", False, ""),), side_effect=True),
            "open_container": ToolContract("open_container", "验证并打开近距离真实容器交互上下文。", _pos(), "容器是否可用", close, side_effect=True),
            "take_from_container": ToolContract("take_from_container", "从近距离真实容器取出物品。", _pos() + (P("item_id", "string", True), P("count", "integer", False, 1, 1, 1728), P("allow_partial", "boolean", False, True)), "请求数量和实际移动数量", close, side_effect=True),
            "put_into_container": ToolContract("put_into_container", "向近距离真实容器放入物品。", _pos() + (P("item_id", "string", True), P("count", "integer", False, 1, 1, 1728), P("allow_partial", "boolean", False, True)), "请求数量和实际移动数量", close, side_effect=True),
            "attack_entity": ToolContract("attack_entity", "在程序内部按上限攻击已观察实体。", (P("uuid", "uuid", True), P("max_attack_count", "integer", False, 3, 1, 64), P("max_duration_ticks", "integer", False, 200, 1, 2400), P("stop_condition", "string", False, "ANY", choices=("ANY", "TARGET_GONE", "MAX_ATTACKS", "OUT_OF_RANGE"))), side_effect=True),
            "interact_entity": ToolContract("interact_entity", "对近距离已观察实体执行统一交互。", (P("uuid", "uuid", True), P("hand", "string", False, "MAIN_HAND", choices=("MAIN_HAND", "OFF_HAND"))), "真实实体交互结果或明确的不支持返回码", close, side_effect=True),
            "retreat_from": ToolContract("retreat_from", "远离已知威胁实体。", (P("uuid", "uuid", True), P("distance", "number", False, 12, 4, 48)), side_effect=True),
            "hold_position": ToolContract("hold_position", "在当前位置等待并保持安全。", (P("duration_ticks", "integer", False, 100, 1, 1200),), side_effect=True),
            "wait": ToolContract("wait", "等待固定且有上限的游戏时间。", (P("duration_ticks", "integer", True, minimum=1, maximum=72000), P("timeout_ticks", "integer", True, minimum=1, maximum=72000)), "等待完成或超时", side_effect=True),
            "wait_until": ToolContract("wait_until", "等待已批准的世界状态条件，达到上限即明确失败。", (P("condition", "object", True), P("timeout_ticks", "integer", True, minimum=1, maximum=72000), P("failure_code", "string", False, "CONDITION_TIMEOUT")), "条件成立、取消或指定失败返回码", ("只读取已注册状态，不执行任意代码。",), side_effect=True),
            "dig_region": ToolContract("dig_region", "小范围逐块真实挖掘。", (P("min", "position", True), P("max", "position", True)), side_effect=True),
            "place_region": ToolContract("place_region", "小范围逐块消耗材料并真实放置。", (P("min", "position", True), P("max", "position", True), P("item_id", "string", True)), side_effect=True),
            "build_chunk": ToolContract("build_chunk", "执行 Runtime 已计算好的最多128个蓝图方块；逐块消耗真实材料并返回准确索引。", (P("placements", "array", True), P("allow_partial", "boolean", False, True)), "placed_indices + completed_indices", ("仅处理明确坐标和材料，不生成方块。",), side_effect=True),
            "get_action_status": ToolContract("get_action_status", "读取 Bridge 当前动作。", strict_survival_notes=observe),
            "cancel_action": ToolContract("cancel_action", "取消当前 Bridge 动作。", (P("request_id", "string", False, ""),), side_effect=True),
            "run_skill": ToolContract("run_skill", "执行一个已批准 ACTIVE Skill。", (P("skill_id", "string", True), P("version", "integer", False, 0, 0), P("parameters", "object", False, {})), "skill results", runtime_only=True, side_effect=True),
            "build_blueprint": ToolContract("build_blueprint", "按蓝图分段真实施工，材料不足时生成资源子目标。", (P("blueprint", "object", True), P("origin", "position", True), P("rotation", "integer", False, 0, choices=(0, 90, 180, 270))), "build checkpoint", runtime_only=True, side_effect=True),
            "build_dsl": ToolContract("build_dsl", "将 floor/wall/box/outline_rectangle/doorway 原语展开为同一蓝图执行器施工。", (P("dsl", "object", True), P("origin", "position", True), P("rotation", "integer", False, 0, choices=(0, 90, 180, 270))), "build checkpoint", runtime_only=True, side_effect=True),
        }

    def names(self, *, include_runtime: bool = True) -> set[str]:
        return {name for name, contract in self._tools.items() if include_runtime or not contract.runtime_only}

    def get(self, name: str) -> ToolContract:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolValidationError("UNKNOWN_TOOL", f"未知工具：{name}") from exc

    def prompt_payload(self, *, include_runtime: bool = True) -> list[dict[str, Any]]:
        return [self._tools[name].prompt_schema() for name in sorted(self.names(include_runtime=include_runtime))]

    @staticmethod
    def _is_template(value: Any) -> bool:
        return isinstance(value, str) and value.startswith("$")

    def validate(self, name: str, args: dict[str, Any] | None, *, allow_templates: bool = False) -> dict[str, Any]:
        contract = self.get(name)
        if not isinstance(args, dict):
            raise ToolValidationError("INVALID_ARGS", f"{name}.args 必须是对象")
        args = dict(args)
        for alias, canonical in contract.aliases.items():
            if alias in args and canonical not in args:
                args[canonical] = args.pop(alias)
        unknown = set(args) - contract.argument_names
        if unknown:
            raise ToolValidationError("INVALID_ARGS", f"{name} 包含未知参数：{', '.join(sorted(unknown))}")
        normalized: dict[str, Any] = {}
        for spec in contract.arguments:
            if spec.name not in args:
                if spec.required:
                    code = "MISSING_TARGET_UUID" if spec.kind == "uuid" else "MISSING_REQUIRED_ARGUMENT"
                    raise ToolValidationError(code, f"{name} 缺少参数 {spec.name}")
                normalized[spec.name] = spec.default
                continue
            value = args[spec.name]
            if allow_templates and self._is_template(value):
                normalized[spec.name] = value
                continue
            if spec.kind == "string":
                if not isinstance(value, str):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name} 必须是文本")
            elif spec.kind == "uuid":
                if not isinstance(value, str):
                    raise ToolValidationError("MISSING_TARGET_UUID", f"{spec.name} 必须是 UUID")
                try:
                    UUID(value)
                except (ValueError, AttributeError) as exc:
                    raise ToolValidationError("MISSING_TARGET_UUID", f"{spec.name} 不是有效 UUID") from exc
            elif spec.kind == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name} 必须是数字")
                value = float(value)
            elif spec.kind == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name} 必须是整数")
            elif spec.kind == "boolean":
                if not isinstance(value, bool):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name} 必须是布尔值")
            elif spec.kind == "object":
                if not isinstance(value, dict):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name} 必须是对象")
            elif spec.kind == "array":
                if not isinstance(value, list):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name} 必须是数组")
            elif spec.kind == "position":
                if not isinstance(value, dict) or not all(k in value for k in ("x", "y", "z")):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name} 必须包含 x/y/z")
                if not all(isinstance(value[k], (int, float)) and not isinstance(value[k], bool) for k in ("x", "y", "z")):
                    raise ToolValidationError("INVALID_ARGS", f"{spec.name}.x/y/z 必须是数字")
            if spec.minimum is not None and value < spec.minimum:
                raise ToolValidationError("INVALID_ARGS", f"{spec.name} 不能小于 {spec.minimum}")
            if spec.maximum is not None and value > spec.maximum:
                raise ToolValidationError("INVALID_ARGS", f"{spec.name} 不能大于 {spec.maximum}")
            if spec.choices and value not in spec.choices:
                raise ToolValidationError("INVALID_ARGS", f"{spec.name} 只能是 {', '.join(map(str, spec.choices))}")
            normalized[spec.name] = value
        if name in {"dig_region", "place_region"}:
            a, b = normalized["min"], normalized["max"]
            volume = (abs(int(a["x"]) - int(b["x"])) + 1) * (abs(int(a["y"]) - int(b["y"])) + 1) * (abs(int(a["z"]) - int(b["z"])) + 1)
            if volume > 4096:
                raise ToolValidationError("INVALID_REGION", "区域超过 4096 个方块")
        if name == "maintain_distance" and normalized["min_distance"] > normalized["max_distance"]:
            raise ToolValidationError("INVALID_DISTANCE_RANGE", "min_distance 不能大于 max_distance")
        if name == "wait" and normalized["duration_ticks"] > normalized["timeout_ticks"]:
            raise ToolValidationError("INVALID_TIMEOUT", "duration_ticks 不能大于 timeout_ticks")
        if name == "wait_until":
            self._validate_wait_condition(normalized["condition"])
        return normalized

    @staticmethod
    def _validate_wait_condition(condition: dict[str, Any]) -> None:
        allowed = {
            "ENTITY_EXISTS", "ENTITY_GONE", "ENTITY_DISTANCE_AT_MOST", "ENTITY_DISTANCE_AT_LEAST",
            "BLOCK_ID_EQUALS", "BLOCK_AIR", "POSITION_REACHED", "ACTION_COMPLETE", "HAS_ITEM",
            "HEALTH_AT_LEAST", "HEALTH_BELOW",
        }
        if set(condition) - {"type", "args"}:
            raise ToolValidationError("INVALID_CONDITION", "wait_until.condition 只允许 type 和 args")
        condition_type = condition.get("type")
        condition_args = condition.get("args", {})
        if condition_type not in allowed or not isinstance(condition_args, dict):
            raise ToolValidationError("INVALID_CONDITION", "wait_until 使用了未注册条件")
        required: dict[str, tuple[str, ...]] = {
            "ENTITY_EXISTS": ("uuid",),
            "ENTITY_GONE": ("uuid",),
            "ENTITY_DISTANCE_AT_MOST": ("uuid", "distance"),
            "ENTITY_DISTANCE_AT_LEAST": ("uuid", "distance"),
            "BLOCK_ID_EQUALS": ("position", "block_id"),
            "BLOCK_AIR": ("position",),
            "POSITION_REACHED": ("position",),
            "ACTION_COMPLETE": ("request_id",),
            "HAS_ITEM": ("item_id",),
            "HEALTH_AT_LEAST": ("value",),
            "HEALTH_BELOW": ("value",),
        }
        if any(key not in condition_args for key in required[condition_type]):
            raise ToolValidationError("INVALID_CONDITION", "wait_until.condition 缺少必需参数")
        if "uuid" in condition_args:
            try:
                UUID(str(condition_args["uuid"]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ToolValidationError("INVALID_CONDITION", "wait_until.condition.uuid 无效") from exc
        if "position" in condition_args:
            position = condition_args["position"]
            if not isinstance(position, dict) or not all(
                isinstance(position.get(key), (int, float)) and not isinstance(position.get(key), bool)
                for key in ("x", "y", "z")
            ):
                raise ToolValidationError("INVALID_CONDITION", "wait_until.condition.position 无效")
        for number_key in ("distance", "radius", "count", "value"):
            if number_key in condition_args and (
                not isinstance(condition_args[number_key], (int, float)) or isinstance(condition_args[number_key], bool)
            ):
                raise ToolValidationError("INVALID_CONDITION", f"wait_until.condition.{number_key} 必须是数字")


CATALOG = ToolCatalog()
SAFE_TOOLS = frozenset(CATALOG.names(include_runtime=True))
BRIDGE_TOOLS = frozenset(CATALOG.names(include_runtime=False))
