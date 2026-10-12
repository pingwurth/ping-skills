"""极简 JSON Schema 校验器（纯 stdlib，无第三方依赖）。

只实现 sensors.schema.json 实际用到的 draft-07 关键字：
  type / required / properties / additionalProperties / items /
  enum / minItems / minimum / $ref（仅 #/definitions/... 形式）
`format`（如 date-time）、`title`、`description` 等不参与结构校验。

用法：
  errors = validate(instance, schema)   # 返回错误列表，空列表 = 通过
错误信息形如 "$.paradigms[0].entry_functions[1]: 缺少必需字段 'grep_pattern'"。
"""
from __future__ import annotations

import json

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def _type_matches(instance, expected: str) -> bool:
    py = _TYPES.get(expected)
    if py is None:
        return False
    if not isinstance(instance, py):
        return False
    # bool 是 int 的子类：True 不是合法的 integer/number
    if expected in ("integer", "number") and isinstance(instance, bool):
        return False
    return True


def validate(instance, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """递归校验 instance 是否符合 schema，返回错误信息列表。"""
    root = root if root is not None else schema
    errors: list[str] = []

    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/definitions/"):
            return [f"{path}: 不支持的 $ref 形式 {ref}"]
        target = root.get("definitions", {}).get(ref.split("/", 2)[2])
        if target is None:
            return [f"{path}: 未知的 $ref {ref}"]
        return validate(instance, target, root, path)

    expected_type = schema.get("type")
    if expected_type and not _type_matches(instance, expected_type):
        return [f"{path}: 期望 {expected_type}，实际 {type(instance).__name__}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} 不在枚举 {schema['enum']} 内")

    if ("minimum" in schema and isinstance(instance, (int, float))
            and not isinstance(instance, bool) and instance < schema["minimum"]):
        errors.append(f"{path}: {instance} 小于最小值 {schema['minimum']}")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: 缺少必需字段 '{key}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: 不允许的额外字段 '{key}'")
        for key, sub in instance.items():
            if key in props:
                errors.extend(validate(sub, props[key], root, f"{path}.{key}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: 至少需要 {schema['minItems']} 项，实际 {len(instance)}")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, item_schema, root, f"{path}[{i}]"))

    return errors


def load_schema(path) -> dict:
    """读取 schema 文件。"""
    return json.loads(path.read_text(encoding="utf-8"))
