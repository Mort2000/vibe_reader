from __future__ import annotations

import pathlib
from dataclasses import fields, is_dataclass, replace
from typing import Any, Callable

FieldErrorFactory = Callable[[str, str], dict[str, str]]
ConstraintValidator = Callable[[str, Any, list[dict[str, str]]], None]


def _append_error(
    errors: list[dict[str, str]] | None,
    field_error: FieldErrorFactory | None,
    path: str,
    message: str,
) -> None:
    if errors is None:
        return
    if field_error is None:
        errors.append({"path": path, "message": message})
    else:
        errors.append(field_error(path, message))


def _coerce_bool(
    value: Any,
    current: bool,
    path: str,
    *,
    errors: list[dict[str, str]] | None,
    field_error: FieldErrorFactory | None,
    strict: bool,
) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int | float) and not strict:
        return bool(value)
    _append_error(errors, field_error, path, "必须是布尔值")
    return current


def _coerce_int(
    value: Any,
    current: int,
    path: str,
    *,
    errors: list[dict[str, str]] | None,
    field_error: FieldErrorFactory | None,
) -> int:
    if isinstance(value, bool):
        _append_error(errors, field_error, path, "必须是整数")
        return current
    try:
        return int(value)
    except (TypeError, ValueError):
        _append_error(errors, field_error, path, "必须是整数")
        return current


def _coerce_float(
    value: Any,
    current: float,
    path: str,
    *,
    errors: list[dict[str, str]] | None,
    field_error: FieldErrorFactory | None,
) -> float:
    if isinstance(value, bool):
        _append_error(errors, field_error, path, "必须是数字")
        return current
    try:
        return float(value)
    except (TypeError, ValueError):
        _append_error(errors, field_error, path, "必须是数字")
        return current


def _coerce_string_list(
    value: Any,
    current: list[Any],
    path: str,
    *,
    errors: list[dict[str, str]] | None,
    field_error: FieldErrorFactory | None,
) -> list[Any]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    _append_error(errors, field_error, path, "必须是字符串列表")
    return current


def _coerce_scalar(
    value: Any,
    current: Any,
    path: str,
    *,
    errors: list[dict[str, str]] | None,
    field_error: FieldErrorFactory | None,
    validate_constraints: ConstraintValidator | None,
    strict: bool,
) -> Any:
    if isinstance(current, bool):
        coerced = _coerce_bool(
            value,
            current,
            path,
            errors=errors,
            field_error=field_error,
            strict=strict,
        )
    elif isinstance(current, int) and not isinstance(current, bool):
        coerced = _coerce_int(
            value,
            current,
            path,
            errors=errors,
            field_error=field_error,
        )
    elif isinstance(current, float):
        coerced = _coerce_float(
            value,
            current,
            path,
            errors=errors,
            field_error=field_error,
        )
    elif isinstance(current, list):
        coerced = _coerce_string_list(
            value,
            current,
            path,
            errors=errors,
            field_error=field_error,
        )
    elif isinstance(current, pathlib.Path):
        coerced = pathlib.Path(str(value or current))
    else:
        coerced = "" if value is None else str(value)

    if validate_constraints is not None and errors is not None:
        validate_constraints(path, coerced, errors)
    return coerced


def coerce_dataclass_group(
    group_name: str,
    base: Any,
    payload: Any,
    *,
    errors: list[dict[str, str]] | None = None,
    field_error: FieldErrorFactory | None = None,
    validate_constraints: ConstraintValidator | None = None,
    prefix: str = "",
    reject_unknown: bool = False,
    strict: bool = True,
) -> Any:
    if not isinstance(payload, dict):
        _append_error(errors, field_error, group_name, "配置分组必须是对象")
        return base

    valid_names = {item.name for item in fields(base)}
    if reject_unknown:
        for key in payload:
            if key not in valid_names:
                _append_error(
                    errors,
                    field_error,
                    f"{group_name}.{prefix}{key}",
                    "未知配置项",
                )

    updates: dict[str, Any] = {}
    for item in fields(base):
        if item.name not in payload:
            continue
        current = getattr(base, item.name)
        path = f"{group_name}.{prefix}{item.name}"
        if is_dataclass(current):
            updates[item.name] = coerce_dataclass_group(
                group_name,
                current,
                payload[item.name],
                errors=errors,
                field_error=field_error,
                validate_constraints=validate_constraints,
                prefix=f"{prefix}{item.name}.",
                reject_unknown=reject_unknown,
                strict=strict,
            )
        else:
            updates[item.name] = _coerce_scalar(
                payload[item.name],
                current,
                path,
                errors=errors,
                field_error=field_error,
                validate_constraints=validate_constraints,
                strict=strict,
            )
    return replace(base, **updates)


def read_path(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def replace_path_value(obj: Any, parts: list[str], value: Any) -> Any:
    if not parts:
        return value
    head, *tail = parts
    child = getattr(obj, head)
    return replace(obj, **{head: replace_path_value(child, tail, value)})
