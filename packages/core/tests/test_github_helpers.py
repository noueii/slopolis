"""Shared respx fixtures for the GitHub client/publisher tests.

githubkit validates every REST response against its generated schemas, so a
partial JSON body fails parsing. :func:`fixture` derives a complete, valid body
from the model's own JSON schema and then applies the overrides a test cares
about — no hand-maintained mega-fixtures, no network.
"""

from __future__ import annotations

import copy
from typing import Any, TypeGuard

from pydantic import BaseModel

__all__ = ["UNSET", "fixture"]

_UNSET = "<UNSET>"
_DATETIME = "2024-01-01T00:00:00Z"


def _resolve_ref(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        node: Any = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return node
    return schema


def _is_unset(schema: dict[str, Any]) -> bool:
    return schema.get("const") == _UNSET or schema.get("enum") == [_UNSET]


def _pick_branch(options: list[dict[str, Any]], root: dict[str, Any]) -> dict[str, Any] | None:
    for option in options:
        resolved = _resolve_ref(option, root)
        if resolved.get("type") == "null" or _is_unset(resolved):
            continue
        return resolved
    return None


def _gen(schema: dict[str, Any], root: dict[str, Any], depth: int = 0) -> Any:
    schema = _resolve_ref(schema, root)
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for sub in schema["allOf"]:
            merged.update(_resolve_ref(sub, root))
        schema = {**schema, **merged}
    for combiner in ("anyOf", "oneOf"):
        if combiner in schema:
            chosen = _pick_branch(schema[combiner], root)
            schema = chosen if chosen is not None else {"type": "null"}
    if _is_unset(schema):
        return _UNSET
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "null":
        return None
    if kind == "string":
        return _DATETIME if schema.get("format") == "date-time" else "x"
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        items = schema.get("items")
        return [_gen(items, root, depth + 1)] if items and depth < 20 else []
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = set(schema.get("required", props.keys()))
        out: dict[str, Any] = {}
        for name, sub in props.items():
            if name not in required:
                continue
            value = _gen(sub, root, depth + 1)
            if value != _UNSET:
                out[name] = value
        return out
    return None


def _is_str_dict(value: object) -> TypeGuard[dict[str, Any]]:
    """Narrow ``value`` to a string-keyed dict (isinstance narrows to Unknown)."""
    return isinstance(value, dict)


def _as_dict(value: object) -> dict[str, Any] | None:
    """Return ``value`` as a string-keyed dict, or ``None`` if it is not one."""
    if _is_str_dict(value):
        return value
    return None


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Apply overrides, deep-merging nested dicts so partials stay complete."""
    for key, value in overrides.items():
        nested = _as_dict(base.get(key))
        if nested is not None and _is_str_dict(value):
            _merge(nested, value)
        else:
            base[key] = value


def fixture[M: BaseModel](model: type[M], **overrides: object) -> dict[str, Any]:
    """Return a complete, schema-valid JSON body for ``model`` plus overrides."""
    root = copy.deepcopy(model.model_json_schema())
    raw: Any = _gen(root, root)
    if not _is_str_dict(raw):
        raise TypeError(f"{model.__name__} schema did not produce an object")
    result: dict[str, Any] = raw
    _merge(result, overrides)
    model.model_validate(result)  # proves completeness; keep the clean dict
    return result


UNSET = _UNSET
