from __future__ import annotations

import json
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from maid_agent.building.models import Blueprint, BlueprintBlock


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    return value


def _item(operation: dict[str, Any]) -> str:
    item = str(operation.get("item") or operation.get("item_id") or "").strip()
    if not item or ":" not in item:
        raise ValueError("建筑材料必须是完整 item id，例如 minecraft:cobblestone")
    return item


def _block(x: int, y: int, z: int, operation: dict[str, Any]) -> BlueprintBlock:
    item = _item(operation)
    return BlueprintBlock(
        x=x,
        y=y,
        z=z,
        item_id=item,
        block_id=str(operation.get("block_id") or item),
        face=str(operation.get("face") or "UP"),
        optional=bool(operation.get("optional", False)),
    )


def floor(operation: dict[str, Any]) -> Iterable[BlueprintBlock]:
    x1, x2 = sorted((_integer(operation.get("x1"), "x1"), _integer(operation.get("x2"), "x2")))
    z1, z2 = sorted((_integer(operation.get("z1"), "z1"), _integer(operation.get("z2"), "z2")))
    y = _integer(operation.get("y"), "y")
    for x in range(x1, x2 + 1):
        for z in range(z1, z2 + 1):
            yield _block(x, y, z, operation)


def _line(x1: int, z1: int, x2: int, z2: int) -> Iterable[tuple[int, int]]:
    dx, dz = abs(x2 - x1), abs(z2 - z1)
    step_x, step_z = (1 if x1 < x2 else -1), (1 if z1 < z2 else -1)
    error = dx - dz
    while True:
        yield x1, z1
        if x1 == x2 and z1 == z2:
            break
        twice = 2 * error
        if twice > -dz:
            error -= dz
            x1 += step_x
        if twice < dx:
            error += dx
            z1 += step_z


def wall(operation: dict[str, Any]) -> Iterable[BlueprintBlock]:
    start, end = operation.get("start"), operation.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        raise ValueError("wall.start/end 必须包含 x/y/z")
    x1, y1, z1 = (_integer(start.get(key), f"start.{key}") for key in ("x", "y", "z"))
    x2, y2, z2 = (_integer(end.get(key), f"end.{key}") for key in ("x", "y", "z"))
    if y1 != y2:
        raise ValueError("wall.start.y 与 end.y 必须相同")
    height = _integer(operation.get("height"), "height")
    if height < 1:
        raise ValueError("wall.height 必须大于 0")
    for x, z in _line(x1, z1, x2, z2):
        for dy in range(height):
            yield _block(x, y1 + dy, z, operation)


def box(operation: dict[str, Any]) -> Iterable[BlueprintBlock]:
    origin, size = operation.get("origin"), operation.get("size")
    if not isinstance(origin, dict) or not isinstance(size, dict):
        raise ValueError("box.origin/size 必须包含 x/y/z")
    ox, oy, oz = (_integer(origin.get(key), f"origin.{key}") for key in ("x", "y", "z"))
    sx, sy, sz = (_integer(size.get(key), f"size.{key}") for key in ("x", "y", "z"))
    if min(sx, sy, sz) < 1:
        raise ValueError("box.size 必须全部大于 0")
    hollow = bool(operation.get("hollow", False))
    for dx in range(sx):
        for dy in range(sy):
            for dz in range(sz):
                shell = dx in {0, sx - 1} or dy in {0, sy - 1} or dz in {0, sz - 1}
                if not hollow or shell:
                    yield _block(ox + dx, oy + dy, oz + dz, operation)


def outline_rectangle(operation: dict[str, Any]) -> Iterable[BlueprintBlock]:
    x1, x2 = sorted((_integer(operation.get("x1"), "x1"), _integer(operation.get("x2"), "x2")))
    z1, z2 = sorted((_integer(operation.get("z1"), "z1"), _integer(operation.get("z2"), "z2")))
    y = _integer(operation.get("y"), "y")
    for x in range(x1, x2 + 1):
        yield _block(x, y, z1, operation)
        if z2 != z1:
            yield _block(x, y, z2, operation)
    for z in range(z1 + 1, z2):
        yield _block(x1, y, z, operation)
        if x2 != x1:
            yield _block(x2, y, z, operation)


def doorway(operation: dict[str, Any]) -> Iterable[BlueprintBlock]:
    origin = operation.get("origin")
    if not isinstance(origin, dict):
        raise ValueError("doorway.origin 必须包含 x/y/z")
    ox, oy, oz = (_integer(origin.get(key), f"origin.{key}") for key in ("x", "y", "z"))
    width = _integer(operation.get("width"), "width")
    height = _integer(operation.get("height"), "height")
    axis = str(operation.get("axis") or "X").upper()
    if width < 1 or height < 2 or axis not in {"X", "Z"}:
        raise ValueError("doorway 需要 width>=1、height>=2、axis 为 X 或 Z")
    for dy in range(height + 1):
        for offset in (0, width + 1):
            yield _block(ox + (offset if axis == "X" else 0), oy + dy, oz + (offset if axis == "Z" else 0), operation)
    for offset in range(1, width + 1):
        yield _block(ox + (offset if axis == "X" else 0), oy + height, oz + (offset if axis == "Z" else 0), operation)


PRIMITIVES = {
    "floor": floor,
    "wall": wall,
    "box": box,
    "outline_rectangle": outline_rectangle,
    "doorway": doorway,
}


def compile_dsl(spec: dict[str, Any]) -> Blueprint:
    operations = spec.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("建筑 DSL 必须包含非空 operations")
    by_position: dict[tuple[int, int, int], BlueprintBlock] = {}
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"operations[{index}] 必须是对象")
        name = str(operation.get("op") or "").strip().lower()
        primitive = PRIMITIVES.get(name)
        if primitive is None:
            raise ValueError(f"未知建筑原语：{name}")
        for block in primitive(operation):
            position = (block.x, block.y, block.z)
            existing = by_position.get(position)
            if existing and (existing.item_id != block.item_id or existing.optional != block.optional):
                raise ValueError(f"建筑原语在 {position} 使用了冲突材料")
            by_position[position] = block
            if len(by_position) > 100_000:
                raise ValueError("DSL 展开超过 100000 个方块")
    blueprint_id = str(spec.get("blueprint_id") or "").strip()
    if not blueprint_id:
        # The same persisted build_dsl step must reopen the same checkpoint after
        # an Agent restart.  A random id would silently start a second building.
        canonical = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        blueprint_id = str(uuid5(NAMESPACE_URL, f"maid-ai-building-dsl:{canonical}"))
    return Blueprint(
        blueprint_id=blueprint_id,
        name=str(spec.get("name") or "DSL 建筑"),
        version=int(spec.get("version") or 1),
        segment_size=int(spec.get("segment_size") or 24),
        blocks=list(by_position.values()),
        metadata={"dsl": spec, **dict(spec.get("metadata") or {})},
    )
