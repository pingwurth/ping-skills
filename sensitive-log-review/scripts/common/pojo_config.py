"""pojo.config 唯一解析器与 POJO 文件统一判定（P1-4）。

修复前存在三份行为不一的解析器：
- check_tostring_annotation: 配置缺失返回空集合（扫描 0 文件）
- extract_java_fields:       配置缺失回退默认值
- check_pojo_comments:       文件名用精确匹配（UserDTO.java 永不命中）

本模块统一为：配置缺失/空节回退默认值；文件名一律 endswith 后缀匹配；
目录判定限定在 src/main/java 之后的路径段。
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_DIRS = frozenset({
    "config", "configuration", "pojo", "po", "do", "dto",
    "bo", "vo", "entity", "model", "reqvo", "respvo",
})
DEFAULT_SUFFIXES = (
    "config.java", "configuration.java", "po.java", "do.java",
    "dto.java", "bo.java", "vo.java", "entity.java",
    "model.java", "properties.java",
)


def load_pojo_config(config_path: Path) -> tuple[frozenset[str], tuple[str, ...]]:
    """解析 pojo.config，配置缺失或空节时回退默认值。

    配置文件格式::

        [pojo-dir]
        config
        ...

        [pojo-file]
        config.java
        ...

    Returns:
        (目录名集合[小写], 文件名后缀元组[小写])
    """
    directories: set[str] = set()
    suffixes: list[str] = []

    if config_path.is_file():
        try:
            current_section = None
            with open(config_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        current_section = line[1:-1].lower()
                        continue
                    if current_section == "pojo-dir":
                        directories.add(line.lower())
                    elif current_section == "pojo-file":
                        suffixes.append(line.lower())
        except OSError:
            directories, suffixes = set(), []

    if not directories:
        directories = set(DEFAULT_DIRS)
    if not suffixes:
        suffixes = list(DEFAULT_SUFFIXES)

    # 去重并保持顺序
    suffixes = list(dict.fromkeys(suffixes))
    return frozenset(directories), tuple(suffixes)


def src_main_java_index(parts: tuple[str, ...]) -> int | None:
    """查找 src/main/java 目录结构中 java 目录在路径段中的索引，未找到返回 None。"""
    for index in range(len(parts) - 2):
        if tuple(part.lower() for part in parts[index:index + 3]) == ("src", "main", "java"):
            return index + 2
    return None


def is_pojo_file(
    path: Path,
    project_root: Path,
    directories: frozenset[str],
    suffixes: tuple[str, ...],
) -> bool:
    """统一 POJO 判定：src/main/java 范围内 + (目录名命中 或 文件名后缀命中)。

    Args:
        path: 待判定文件（绝对或相对路径均可）
        project_root: 项目根目录（用于计算相对路径段）
        directories: POJO 目录名集合（小写）
        suffixes: POJO 文件名后缀（小写，如 "dto.java"）

    Returns:
        是否为 POJO 文件
    """
    try:
        relative_parts = path.relative_to(project_root).parts
    except ValueError:
        # 不在 project_root 下（例如绝对路径传入但根不同），退化为按完整路径段判断
        relative_parts = path.parts

    java_index = src_main_java_index(relative_parts)
    if java_index is None:
        return False

    # 目录判定只看 src/main/java 之后、文件名之前的路径段
    source_parts = relative_parts[java_index + 1:-1]
    directory_match = any(part.lower() in directories for part in source_parts)
    filename_match = path.name.lower().endswith(suffixes)
    return directory_match or filename_match


def find_java_files(
    project_root: Path,
    directories: frozenset[str],
    suffixes: tuple[str, ...],
) -> list[Path]:
    """在项目中查找全部 POJO Java 文件（去重、按路径不区分大小写排序）。"""
    matched: set[Path] = set()
    for path in project_root.rglob("*.java"):
        if is_pojo_file(path, project_root, directories, suffixes):
            matched.add(path.resolve())
    return sorted(matched, key=lambda item: str(item).casefold())
