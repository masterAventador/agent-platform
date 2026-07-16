import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import NoReturn

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]
from pydantic import JsonValue, TypeAdapter

MAX_RUN_INPUT_BYTES = 64 * 1024
MAX_RUN_OUTPUT_BYTES = 256 * 1024
_SCHEMA_METADATA_KEYS = frozenset({"$schema", "title", "description"})
_INPUT_ROOT_KEYS = _SCHEMA_METADATA_KEYS | frozenset(
    {"type", "required", "properties", "additionalProperties"}
)
_INPUT_FIELD_KEYS = _SCHEMA_METADATA_KEYS | frozenset(
    {
        "type",
        "format",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "enum",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "contentMediaType",
        "x-agent-platform-control",
    }
)
_INPUT_FILE_FIELD_KEYS = _SCHEMA_METADATA_KEYS | frozenset(
    {"type", "format", "contentMediaType", "x-agent-platform-control"}
)
_INPUT_ARRAY_ITEM_KEYS = _SCHEMA_METADATA_KEYS | frozenset(
    {
        "type",
        "format",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "enum",
    }
)


@dataclass(frozen=True, slots=True)
class DynamicSchemaIssue:
    schema_name: str
    message: str
    path: tuple[str, ...]


class InvalidDynamicSchema(ValueError):
    def __init__(self, issue: DynamicSchemaIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


class DynamicInputTooLarge(ValueError):
    pass


class DynamicOutputTooLarge(ValueError):
    pass


class DynamicInputValidationFailed(ValueError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__("run input does not match employee input schema")
        self.errors = errors


class DynamicOutputValidationFailed(ValueError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__("run output does not match employee output schema")
        self.errors = errors


def validate_employee_io_schemas(
    *,
    input_schema: Mapping[str, object],
    output_schema: Mapping[str, object],
    file_upload_enabled: bool | None = None,
) -> None:
    _check_schema(schema_name="input_schema", schema=input_schema)
    _check_schema(schema_name="output_schema", schema=output_schema)
    _check_form_compatible_input_schema(
        input_schema=input_schema,
        file_upload_enabled=file_upload_enabled,
    )


def validate_run_input(
    *,
    input_schema: Mapping[str, object],
    value: Mapping[str, JsonValue],
    file_upload_enabled: bool | None = None,
) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_RUN_INPUT_BYTES:
        raise DynamicInputTooLarge
    _check_schema(schema_name="input_schema", schema=input_schema)
    _check_form_compatible_input_schema(
        input_schema=input_schema,
        file_upload_enabled=file_upload_enabled,
    )
    validator = Draft202012Validator(dict(input_schema), format_checker=FormatChecker())
    errors = tuple(sorted(validator.iter_errors(dict(value)), key=str))
    if errors:
        raise DynamicInputValidationFailed(
            tuple(_format_validation_error(error) for error in errors[:5])
        )


def validate_run_output(
    *,
    output_schema: Mapping[str, object],
    value: JsonValue,
) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_RUN_OUTPUT_BYTES:
        raise DynamicOutputTooLarge
    _check_schema(schema_name="output_schema", schema=output_schema)
    validator = Draft202012Validator(dict(output_schema), format_checker=FormatChecker())
    errors = tuple(sorted(validator.iter_errors(value), key=str))
    if errors:
        raise DynamicOutputValidationFailed(
            tuple(_format_validation_error(error) for error in errors[:5])
        )


def has_effective_output_schema(output_schema: Mapping[str, object] | None) -> bool:
    """Return whether an employee output schema should drive structured output behavior."""
    if not output_schema:
        return False
    effective_keys = set(output_schema) - _SCHEMA_METADATA_KEYS
    if not effective_keys:
        return False
    return not (effective_keys == {"type"} and output_schema.get("type") == "object")


def coerce_output_for_schema(
    *,
    output_schema: Mapping[str, object] | None,
    value: JsonValue,
) -> JsonValue:
    if not isinstance(value, str) or not has_effective_output_schema(output_schema):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not _parsed_output_matches_schema_hint(
        output_schema=output_schema,
        original=value,
        parsed=parsed,
    ):
        return value
    return TypeAdapter(JsonValue).validate_python(parsed)


def file_field_names(input_schema: Mapping[str, object]) -> tuple[str, ...]:
    """Return dynamic file-control field names from a validated input schema."""
    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return ()
    return tuple(
        name
        for name, field_schema in properties.items()
        if isinstance(name, str)
        and isinstance(field_schema, Mapping)
        and _is_file_field_schema(field_schema)
    )


def _check_schema(*, schema_name: str, schema: Mapping[str, object]) -> None:
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError as error:
        raise InvalidDynamicSchema(
            DynamicSchemaIssue(
                schema_name=schema_name,
                message=str(error.message),
                path=tuple(str(item) for item in error.absolute_path),
            )
        ) from error


def _check_form_compatible_input_schema(
    *,
    input_schema: Mapping[str, object],
    file_upload_enabled: bool | None,
) -> None:
    """Keep API-accepted input schemas aligned with the generated frontend form."""
    _check_no_unsupported_input_keywords(
        schema=input_schema,
        allowed_keys=_INPUT_ROOT_KEYS,
        path=(),
    )
    schema_type = input_schema.get("type")
    if schema_type not in (None, "object"):
        _raise_input_schema_issue(
            "动态输入 Schema 顶层必须是 object",
            ("type",),
        )
    properties = input_schema.get("properties")
    if properties is None:
        return
    if not isinstance(properties, Mapping):
        _raise_input_schema_issue("动态输入 properties 必须是对象", ("properties",))
    required = input_schema.get("required")
    if required is not None and (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes))
        or not all(isinstance(item, str) for item in required)
    ):
        _raise_input_schema_issue("动态输入 required 必须是字符串数组", ("required",))
    if isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
        unknown_required = [item for item in required if item not in properties]
        if unknown_required:
            _raise_input_schema_issue(
                "动态输入 required 不能引用未声明字段",
                ("required", str(unknown_required[0])),
            )
    additional_properties = input_schema.get("additionalProperties")
    if additional_properties is not None and not isinstance(additional_properties, bool):
        _raise_input_schema_issue(
            "动态输入 additionalProperties 只能是布尔值",
            ("additionalProperties",),
        )
    if additional_properties is not False:
        _raise_input_schema_issue(
            "动态输入存在 properties 时必须关闭 additionalProperties",
            ("additionalProperties",),
        )
    for name, field_schema in properties.items():
        if not isinstance(name, str):
            _raise_input_schema_issue("动态输入字段名必须是字符串", ("properties", str(name)))
        if not isinstance(field_schema, Mapping):
            _raise_input_schema_issue(
                "动态输入字段 Schema 必须是对象",
                ("properties", name),
            )
        _check_form_field_schema(
            schema=field_schema,
            path=("properties", name),
            file_upload_enabled=file_upload_enabled,
        )


def _check_form_field_schema(
    *,
    schema: Mapping[str, object],
    path: tuple[str, ...],
    file_upload_enabled: bool | None,
) -> None:
    if _is_file_field_schema(schema):
        _check_no_unsupported_input_keywords(
            schema=schema,
            allowed_keys=_INPUT_FILE_FIELD_KEYS,
            path=path,
        )
        if file_upload_enabled is False:
            _raise_input_schema_issue(
                "动态文件输入必须同时启用 capabilities.file_upload",
                path + ("x-agent-platform-control",),
            )
        schema_type = schema.get("type")
        if schema_type not in (None, "string"):
            _raise_input_schema_issue("动态文件输入字段类型必须是 string", path + ("type",))
        schema_format = schema.get("format")
        if schema_format is not None and schema_format != "binary":
            _raise_input_schema_issue(
                "动态文件输入字段只支持 binary format",
                path + ("format",),
            )
        control = schema.get("x-agent-platform-control")
        if control is not None and control != "file":
            _raise_input_schema_issue(
                "动态文件输入控件必须声明为 file",
                path + ("x-agent-platform-control",),
            )
        content_media_type = schema.get("contentMediaType")
        if content_media_type is not None and not isinstance(content_media_type, str):
            _raise_input_schema_issue(
                "动态文件输入 contentMediaType 必须是字符串",
                path + ("contentMediaType",),
            )
        return
    schema_type = schema.get("type")
    if schema_type not in ("string", "number", "integer", "boolean", "array"):
        _raise_input_schema_issue(
            "动态输入字段只支持 string、number、integer、boolean、array 和 file",
            path + ("type",),
        )
    _check_no_unsupported_input_keywords(
        schema=schema,
        allowed_keys=_INPUT_FIELD_KEYS,
        path=path,
    )
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            _raise_input_schema_issue("动态数组输入必须声明 items", path + ("items",))
        if _is_file_field_schema(items):
            _raise_input_schema_issue(
                "动态数组输入当前不支持文件 items",
                path + ("items", "type"),
            )
        item_type = items.get("type")
        if item_type not in ("string", "number", "integer", "boolean"):
            _raise_input_schema_issue(
                "动态数组 items 只支持 string、number、integer 和 boolean",
                path + ("items", "type"),
            )
        _check_no_unsupported_input_keywords(
            schema=items,
            allowed_keys=_INPUT_ARRAY_ITEM_KEYS,
            path=path + ("items",),
        )
        _check_supported_format(schema=items, path=path + ("items",))
        _check_supported_pattern(schema=items, path=path + ("items",))
    _check_supported_format(schema=schema, path=path)
    _check_supported_pattern(schema=schema, path=path)


def _check_supported_format(*, schema: Mapping[str, object], path: tuple[str, ...]) -> None:
    schema_format = schema.get("format")
    if schema_format is not None and schema_format not in {"date", "binary"}:
        _raise_input_schema_issue(
            "动态输入当前只支持 date 和 binary format",
            path + ("format",),
        )


def _check_supported_pattern(*, schema: Mapping[str, object], path: tuple[str, ...]) -> None:
    pattern = schema.get("pattern")
    if pattern is None:
        return
    if not isinstance(pattern, str):
        _raise_input_schema_issue(
            "动态输入 pattern 必须是字符串",
            path + ("pattern",),
        )
    try:
        re.compile(pattern)
    except re.error as error:
        raise InvalidDynamicSchema(
            DynamicSchemaIssue(
                schema_name="input_schema",
                message="动态输入 pattern 必须是有效正则",
                path=path + ("pattern",),
            )
        ) from error
    if _uses_non_browser_regexp_group(pattern):
        _raise_input_schema_issue(
            "动态输入 pattern 必须兼容浏览器 RegExp",
            path + ("pattern",),
        )


def _uses_non_browser_regexp_group(pattern: str) -> bool:
    index = 0
    while True:
        index = pattern.find("(?", index)
        if index == -1:
            return False
        if _is_escaped(pattern, index):
            index += 2
            continue
        if (
            pattern.startswith("(?:", index)
            or pattern.startswith("(?=", index)
            or pattern.startswith("(?!", index)
        ):
            index += 2
            continue
        return True


def _is_escaped(value: str, index: int) -> bool:
    slash_count = 0
    cursor = index - 1
    while cursor >= 0 and value[cursor] == "\\":
        slash_count += 1
        cursor -= 1
    return slash_count % 2 == 1


def _check_no_unsupported_input_keywords(
    *,
    schema: Mapping[str, object],
    allowed_keys: frozenset[str],
    path: tuple[str, ...],
) -> None:
    for key in schema:
        if key not in allowed_keys:
            _raise_input_schema_issue(
                "动态输入 Schema 包含当前表单无法一致表达的关键字",
                path + (str(key),),
            )


def _is_file_field_schema(schema: Mapping[str, object]) -> bool:
    return (
        schema.get("x-agent-platform-control") == "file"
        or schema.get("format") == "binary"
        or isinstance(schema.get("contentMediaType"), str)
    )


def _raise_input_schema_issue(message: str, path: tuple[str, ...]) -> NoReturn:
    raise InvalidDynamicSchema(
        DynamicSchemaIssue(
            schema_name="input_schema",
            message=message,
            path=path,
        )
    )


def _parsed_output_matches_schema_hint(
    *,
    output_schema: Mapping[str, object] | None,
    original: str,
    parsed: object,
) -> bool:
    if output_schema is None:
        return False
    enum_values = output_schema.get("enum")
    if isinstance(enum_values, Sequence) and not isinstance(enum_values, (str, bytes)):
        if any(parsed == item for item in enum_values):
            return True
        if any(original == item for item in enum_values):
            return False
    schema_types = _schema_type_values(output_schema)
    if schema_types:
        return _json_value_matches_any_type(parsed, schema_types)
    return True


def _schema_type_values(schema: Mapping[str, object]) -> frozenset[str]:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return frozenset({schema_type})
    if isinstance(schema_type, Sequence) and not isinstance(schema_type, (str, bytes)):
        return frozenset(item for item in schema_type if isinstance(item, str))
    return frozenset()


def _json_value_matches_any_type(value: object, schema_types: frozenset[str]) -> bool:
    return any(_json_value_matches_type(value, schema_type) for schema_type in schema_types)


def _json_value_matches_type(value: object, schema_type: str) -> bool:
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "object":
        return isinstance(value, Mapping)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "null":
        return value is None
    return False


def _format_validation_error(error: ValidationError) -> str:
    path = ".".join(str(item) for item in error.absolute_path)
    if path:
        return f"{path}: {error.message}"
    return str(error.message)
