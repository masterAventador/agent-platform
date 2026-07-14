from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path


def imported_modules(source_file: Path, *, source_root: Path) -> tuple[str, ...]:
    parsed = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    imported: list[str] = []
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                relative_parent = source_file.relative_to(source_root).parent
                package = ".".join((source_root.name, *relative_parent.parts))
                module = resolve_name(f"{'.' * node.level}{module}", package)
            imported.append(module)
            imported.extend(f"{module}.{alias.name}" for alias in node.names)
    return tuple(imported)


def module_matches(module: str, forbidden_package: str) -> bool:
    return module == forbidden_package or module.startswith(f"{forbidden_package}.")


def uses_dynamic_import_primitive(source_file: Path) -> bool:
    """Reject dynamic-import primitives without simulating Python execution."""

    parsed = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    builtins_aliases = {"builtins"}
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if module_matches(alias.name, "importlib"):
                    return True
                if alias.name == "builtins":
                    builtins_aliases.add(alias.asname or "builtins")
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and module_matches(node.module, "importlib"):
                return True
            if node.module == "builtins" and any(
                alias.name == "__import__" for alias in node.names
            ):
                return True
        elif (isinstance(node, ast.Name) and node.id == "__import__") or (
            isinstance(node, ast.Attribute)
            and node.attr == "__import__"
            and isinstance(node.value, ast.Name)
            and node.value.id in builtins_aliases
        ):
            return True
    return False
