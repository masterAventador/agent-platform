from __future__ import annotations

from pathlib import Path

import pytest
from import_boundary import (
    imported_modules,
    module_matches,
    uses_dynamic_import_primitive,
)

_SOURCE_ROOT = Path(__file__).parents[3] / "src" / "agent_platform"


@pytest.mark.parametrize(
    ("package_name", "forbidden_package"),
    [
        ("video_studio", "agent_platform.capabilities.social_operations"),
        ("social_operations", "agent_platform.capabilities.video_studio"),
    ],
)
def test_capability_packages_do_not_import_each_other(
    package_name: str,
    forbidden_package: str,
) -> None:
    package_root = _SOURCE_ROOT / "capabilities" / package_name

    for source_file in package_root.rglob("*.py"):
        assert not uses_dynamic_import_primitive(source_file)
        assert all(
            not module_matches(imported_module, forbidden_package)
            for imported_module in imported_modules(source_file, source_root=_SOURCE_ROOT)
        )


# 组合根与能力包自身允许 import 具体能力包；其余顶层目录一律视为 Core 并受保护，
# 新增顶层目录无需修改本测试即默认纳入扫描。
_CAPABILITY_IMPORT_ALLOWLIST = frozenset({"bootstrap", "capabilities"})


def _core_top_level_directories() -> list[str]:
    return sorted(
        entry.name
        for entry in _SOURCE_ROOT.iterdir()
        if entry.is_dir()
        and entry.name != "__pycache__"
        and entry.name not in _CAPABILITY_IMPORT_ALLOWLIST
    )


def test_core_business_modules_do_not_import_concrete_capabilities() -> None:
    concrete_packages = (
        "agent_platform.capabilities.video_studio",
        "agent_platform.capabilities.social_operations",
    )

    core_directories = _core_top_level_directories()
    # 防止扫描目标失效：枚举结果必须包含当前真实存在的代表性 Core 目录。
    assert {"api", "infrastructure", "platform", "runtimes", "sandbox", "workers"} <= set(
        core_directories
    )

    violations: list[str] = []
    for core_directory in core_directories:
        for source_file in (_SOURCE_ROOT / core_directory).rglob("*.py"):
            assert not uses_dynamic_import_primitive(source_file)
            violations.extend(
                f"{source_file.relative_to(_SOURCE_ROOT)} imports {imported_module}"
                for imported_module in imported_modules(source_file, source_root=_SOURCE_ROOT)
                if any(
                    module_matches(imported_module, concrete_package)
                    for concrete_package in concrete_packages
                )
            )

    assert violations == [], "Core imports concrete capability packages:\n" + "\n".join(violations)


def test_relative_capability_import_is_resolved_absolutely(tmp_path: Path) -> None:
    source_root = tmp_path / "agent_platform"
    source_file = source_root / "capabilities" / "video_studio" / "adapter.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("from .. import social_operations\n", encoding="utf-8")

    modules = imported_modules(source_file, source_root=source_root)

    assert "agent_platform.capabilities.social_operations" in modules


def test_relative_core_import_of_capability_is_resolved_absolutely(tmp_path: Path) -> None:
    source_root = tmp_path / "agent_platform"
    source_file = source_root / "memory" / "adapter.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("from ..capabilities import video_studio\n", encoding="utf-8")

    modules = imported_modules(source_file, source_root=source_root)

    assert "agent_platform.capabilities.video_studio" in modules


def test_module_matching_uses_segment_boundaries() -> None:
    forbidden = "agent_platform.capabilities.video_studio"

    assert module_matches(forbidden, forbidden)
    assert module_matches(f"{forbidden}.manifest", forbidden)
    assert not module_matches(f"{forbidden}_backup", forbidden)


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\n",
        "import importlib as machinery\n",
        "import importlib.util\n",
        "import importlib.util as util\n",
        "from importlib import import_module\n",
        "from importlib.util import find_spec\n",
        '__import__("agent_platform.capabilities.video_studio")\n',
        "load = __import__\n",
        "import builtins\nload = builtins.__import__\n",
        "import builtins as runtime\nload = runtime.__import__\n",
        "from builtins import __import__\n",
        "from builtins import __import__ as load_module\n",
        (
            "from importlib import import_module\n"
            "load = import_module\n"
            "def unused():\n"
            "    global load\n"
            "    load = print\n"
            'load("agent_platform.capabilities.video_studio")\n'
        ),
        (
            "from importlib import import_module\n"
            "load = print\n"
            "def unused():\n"
            "    global load\n"
            "    load = import_module\n"
            'load("agent_platform.capabilities.video_studio")\n'
        ),
    ],
)
def test_dynamic_import_primitives_are_rejected(source: str, tmp_path: Path) -> None:
    source_file = tmp_path / "adapter.py"
    source_file.write_text(source, encoding="utf-8")

    assert uses_dynamic_import_primitive(source_file)


@pytest.mark.parametrize(
    "source",
    [
        'loader.import_module("agent_platform.capabilities.video_studio")\n',
        "import builtins\nprint(builtins.str)\n",
    ],
)
def test_unrelated_references_are_not_dynamic_import_false_positives(
    source: str,
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "adapter.py"
    source_file.write_text(source, encoding="utf-8")

    assert not uses_dynamic_import_primitive(source_file)
