import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).parents[3]
PACKAGE_ROOT = BACKEND_ROOT / "src" / "agent_platform"


@pytest.mark.parametrize(
    ("package", "forbidden_packages"),
    [
        ("runtimes", frozenset({"api", "infrastructure", "workers"})),
        (
            "platform",
            frozenset({"api", "infrastructure", "runtimes", "workers"}),
        ),
    ],
)
def test_package_dependency_direction(
    package: str,
    forbidden_packages: frozenset[str],
) -> None:
    violations: list[str] = []
    for path in sorted((PACKAGE_ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_parts = path.relative_to(PACKAGE_ROOT).with_suffix("").parts
        current_package = ("agent_platform", *relative_parts[:-1])
        for node in ast.walk(tree):
            imported_modules: list[str]
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    imported_modules = [node.module] if node.module is not None else []
                else:
                    keep_parts = len(current_package) - (node.level - 1)
                    prefix = current_package[:keep_parts]
                    suffixes = (
                        [node.module]
                        if node.module is not None
                        else [alias.name for alias in node.names]
                    )
                    imported_modules = [
                        ".".join((*prefix, *suffix.split("."))) for suffix in suffixes
                    ]
            else:
                continue
            for imported_module in imported_modules:
                parts = imported_module.split(".")
                if (
                    len(parts) >= 2
                    and parts[0] == "agent_platform"
                    and parts[1] in forbidden_packages
                ):
                    relative_path = path.relative_to(BACKEND_ROOT)
                    violations.append(f"{relative_path}:{node.lineno} imports {imported_module}")

    assert violations == [], "invalid dependency direction:\n" + "\n".join(violations)
