import re
import stat
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

import yaml

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILE_COUNT = 100
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)


class SkillBundleError(ValueError):
    """上传的 Skill 包不符合平台安全与格式约束。"""


@dataclass(frozen=True)
class SkillBundle:
    name: str
    description: str
    digest: str
    files: list[str]
    _contents: dict[str, bytes]

    def read_text(self, path: str) -> str:
        try:
            return self.read_bytes(path).decode("utf-8")
        except UnicodeDecodeError as error:
            raise SkillBundleError(f"Skill 文件不是 UTF-8 文本：{path}") from error

    def read_bytes(self, path: str) -> bytes:
        try:
            return self._contents[path]
        except KeyError as error:
            raise SkillBundleError(f"Skill 文件不存在：{path}") from error


def parse_skill_bundle(content: bytes) -> SkillBundle:
    if not content or len(content) > MAX_ARCHIVE_BYTES:
        raise SkillBundleError("Skill ZIP 大小必须在 1 字节到 10 MB 之间")
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            root = _bundle_root(infos)
            contents = _read_files(archive, infos, root)
    except BadZipFile as error:
        raise SkillBundleError("Skill 包必须是有效的 ZIP 文件") from error

    try:
        skill_markdown = contents["SKILL.md"].decode("utf-8")
    except KeyError as error:
        raise SkillBundleError("Skill 包根目录必须包含 SKILL.md") from error
    except UnicodeDecodeError as error:
        raise SkillBundleError("SKILL.md 必须使用 UTF-8 编码") from error

    name, description = _manifest(skill_markdown)
    files = sorted(contents)
    digest = _digest(contents)
    return SkillBundle(
        name=name,
        description=description,
        digest=digest,
        files=files,
        _contents=contents,
    )


def _bundle_root(infos: list[ZipInfo]) -> str | None:
    paths = [_safe_path(info.filename) for info in infos]
    if PurePosixPath("SKILL.md") in paths:
        return None
    candidates = {path.parts[0] for path in paths if len(path.parts) > 1}
    if len(candidates) == 1:
        candidate = next(iter(candidates))
        if PurePosixPath(candidate, "SKILL.md") in paths:
            return candidate
    return None


def _read_files(
    archive: ZipFile,
    infos: list[ZipInfo],
    root: str | None,
) -> dict[str, bytes]:
    if len(infos) > MAX_FILE_COUNT:
        raise SkillBundleError(f"Skill 包最多包含 {MAX_FILE_COUNT} 个文件")
    total_size = 0
    contents: dict[str, bytes] = {}
    for info in infos:
        path = _safe_path(info.filename)
        if stat.S_ISLNK(info.external_attr >> 16):
            raise SkillBundleError(f"Skill 包禁止包含符号链接：{info.filename}")
        if info.file_size > MAX_FILE_BYTES:
            raise SkillBundleError(f"Skill 单文件不能超过 2 MB：{info.filename}")
        total_size += info.file_size
        if total_size > MAX_TOTAL_BYTES:
            raise SkillBundleError("Skill 包解压后不能超过 20 MB")
        normalized = PurePosixPath(*path.parts[1:]) if root is not None else path
        normalized_name = normalized.as_posix()
        if normalized_name in contents:
            raise SkillBundleError(f"Skill 包存在重复路径：{normalized_name}")
        contents[normalized_name] = archive.read(info)
    return contents


def _safe_path(value: str) -> PurePosixPath:
    if "\\" in value:
        raise SkillBundleError(f"Skill 包存在非法路径：{value}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillBundleError(f"Skill 包存在非法路径：{value}")
    return path


def _manifest(content: str) -> tuple[str, str]:
    match = FRONTMATTER_PATTERN.match(content)
    if match is None:
        raise SkillBundleError("SKILL.md 必须包含 YAML frontmatter")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise SkillBundleError("SKILL.md frontmatter 不是有效 YAML") from error
    if not isinstance(metadata, dict):
        raise SkillBundleError("SKILL.md frontmatter 必须是对象")
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or len(name) > 64 or SKILL_NAME_PATTERN.fullmatch(name) is None:
        raise SkillBundleError("Skill name 必须由小写字母、数字和单个连字符组成")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        raise SkillBundleError("Skill description 长度必须在 1 到 1024 个字符之间")
    return name, description.strip()


def _digest(contents: dict[str, bytes]) -> str:
    hasher = sha256()
    for path in sorted(contents):
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(sha256(contents[path]).digest())
    return hasher.hexdigest()
