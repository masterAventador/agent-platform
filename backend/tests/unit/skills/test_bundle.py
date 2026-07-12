from io import BytesIO
from stat import S_IFLNK
from zipfile import ZipFile, ZipInfo

import pytest

from agent_platform.platform.skills.bundle import SkillBundleError, parse_skill_bundle


def _bundle(files: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


def test_parse_skill_bundle_reads_manifest_files_and_stable_digest() -> None:
    content = _bundle(
        {
            "report-writer/SKILL.md": (
                b"---\nname: report-writer\n"
                b"description: Create a sourced business report.\n---\n\n# Report writer\n"
            ),
            "report-writer/references/style.md": b"Use concise language.\n",
            "report-writer/scripts/render.py": b"print('ok')\n",
        }
    )

    first = parse_skill_bundle(content)
    second = parse_skill_bundle(content)

    assert first.name == "report-writer"
    assert first.description == "Create a sourced business report."
    assert first.files == ["SKILL.md", "references/style.md", "scripts/render.py"]
    assert first.digest == second.digest
    assert first.read_text("references/style.md") == "Use concise language.\n"


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"README.md": b"missing"}, "SKILL.md"),
        ({"../SKILL.md": b"bad"}, "非法路径"),
        (
            {"SKILL.md": b"---\nname: Bad_Name\ndescription: invalid\n---\n"},
            "Skill name",
        ),
    ],
)
def test_parse_skill_bundle_rejects_invalid_packages(
    files: dict[str, bytes], message: str
) -> None:
    with pytest.raises(SkillBundleError, match=message):
        parse_skill_bundle(_bundle(files))


def test_parse_skill_bundle_rejects_symbolic_links() -> None:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "SKILL.md",
            b"---\nname: safe-skill\ndescription: Safe skill.\n---\n",
        )
        link = ZipInfo("references/escape.md")
        link.create_system = 3
        link.external_attr = (S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../secret")

    with pytest.raises(SkillBundleError, match="符号链接"):
        parse_skill_bundle(output.getvalue())
