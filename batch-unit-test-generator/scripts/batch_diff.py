#!/usr/bin/env python3
"""批量差异分析(batch_diff 阶段, 纯分析不跑 mvn)。

工作流位置:
    select_worktree 选定工作树后, 以 --project-root <worktree> 运行本脚本,
    产出差异候选类清单并触发确认门禁(ask_user), 用户确认范围与门槛后
    进入 batch_init。

流程:
    git diff --name-only --diff-filter=ACMR target...HEAD(或两点) → 每文件 --numstat
    → 过滤链(仅 src/main/java, 剔除测试类/接口/枚举/注解/pom jacoco excludes)
    → 源路径→FQCN + 模块定位 → 探测已有测试类 → 候选清单 → ask_user 确认门禁

输出(NEXT_STEP):
    有候选类 → ask_user(呈现清单, 请求范围 --all/--top N/--classes 与门槛);
    无候选类 → finish(差异为空)。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from fnmatch import fnmatchcase
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, gitops, javasrc, maven  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import Decision, Route  # noqa: E402
from jaut.protocol import make_next_step  # noqa: E402

SCRIPT_NAME = "batch_diff"

TEST_CLASS_PREFIX = "Test"
TEST_CLASS_SUFFIXES = ("Test", "Tests", "TestCase")


# --------------------------------------------------------------------------- #
# 过滤辅助
# --------------------------------------------------------------------------- #
def is_test_file(filepath: str) -> bool:
    """判断是否为测试相关文件(测试目录或测试类命名)。"""
    normalized = filepath.replace("\\", "/")
    if "/src/test/" in normalized:
        return True
    basename = os.path.basename(normalized)
    class_name = basename[:-5] if basename.endswith(".java") else basename
    if (class_name.startswith(TEST_CLASS_PREFIX)
            and len(class_name) > len(TEST_CLASS_PREFIX)
            and class_name[len(TEST_CLASS_PREFIX)].isupper()):
        return True
    if class_name.endswith(TEST_CLASS_SUFFIXES):
        return True
    return False


def _ant_segment_to_regex(segment: str) -> str:
    """将 ant 风格的单个路径段转为正则片段(* 不跨目录, ? 匹配单字符)。"""
    out = ""
    for ch in segment:
        if ch == "*":
            out += "[^/]*"
        elif ch == "?":
            out += "[^/]"
        else:
            out += re.escape(ch)
    return out


def matches_jacoco_pattern(filepath: str, pattern: str) -> bool:
    """判断源文件是否匹配 jacoco 排除规则(ant 风格语义)。"""
    normalized = filepath.replace("\\", "/")
    marker = "src/main/java/"
    idx = normalized.find(marker)
    class_path = normalized[idx + len(marker):] if idx >= 0 else normalized
    if class_path.endswith(".java"):
        class_path = class_path[:-5]

    pat = pattern.strip().replace("\\", "/")
    if pat.endswith(".class"):
        pat = pat[:-6]

    segments = [s for s in pat.split("/") if s != ""]
    regex_parts = []
    prev_double_star = False
    for i, seg in enumerate(segments):
        if regex_parts and not prev_double_star:
            regex_parts.append("/")
        if seg == "**":
            if i == 0:
                regex_parts.append("(?:[^/]+/)*")
            elif i == len(segments) - 1:
                regex_parts.append("(?:[^/]+/)*(?:[^/]+)?")
            else:
                regex_parts.append("(?:[^/]+/)+")
            prev_double_star = True
        else:
            regex_parts.append(_ant_segment_to_regex(seg))
            prev_double_star = False
    regex = "".join(regex_parts)
    return re.fullmatch(regex, class_path) is not None


def _is_interface_or_enum(source_path: Path) -> bool:
    """源码启发式: 判断是否为接口/枚举/注解(无方法体的声明)。"""
    try:
        content = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    stripped = javasrc.strip_comments_and_strings(content)
    # 检查类声明前的修饰符
    decl_re = re.compile(
        r"\b(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+)*"
        r"(interface|enum|@interface)\b")
    return bool(decl_re.search(stripped))


def _count_methods(source_path: Path) -> int:
    """统计源文件中的 public/protected 方法数(排除构造器和抽象方法)。

    基于源码正则启发式统计, 与 jacoco 报告中的 pending 方法数存在口径差异
    (pending 数需在 batch_init 阶段运行 jacoco 后方可获知)。

    使用正则启发式, 与 _is_interface_or_enum 保持一致的分析风格。
    """
    try:
        content = source_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    stripped = javasrc.strip_comments_and_strings(content)
    # 匹配方法声明: 修饰符 + 返回类型 + 方法名 + 参数列表
    # 排除字段声明(无括号)和构造器(类名与方法名相同)
    method_re = re.compile(
        r"(?:public|protected)\s+"
        r"(?:static\s+)?(?:final\s+)?(?:abstract\s+)?(?:synchronized\s+)?"
        r"(?:<[^>]+>\s+)?"  # 泛型返回类型
        r"(?:\w+(?:<[^>]*>)?(?:\[\])*)\s+"  # 返回类型
        r"(\w+)\s*\("  # 方法名 + 参数开始
    )
    return len(method_re.findall(stripped))


def _parse_pom_jacoco_excludes(project_root: Path) -> list[str]:
    """解析项目所有 pom.xml 中的 jacoco-maven-plugin excludes 配置。"""
    excludes: list[str] = []
    for dirpath, dirnames, filenames in os.walk(str(project_root)):
        dirnames[:] = [d for d in dirnames if d not in config.PRUNE_DIRS]
        if "pom.xml" not in filenames:
            continue
        try:
            tree = ET.parse(Path(dirpath) / "pom.xml")
        except (ET.ParseError, OSError):
            continue
        root = tree.getroot()
        # 搜索 jacoco-maven-plugin 的 configuration/excludes/exclude
        for plugin in root.iter():
            tag = str(plugin.tag).split("}")[-1]
            if tag != "plugin":
                continue
            art_id = None
            for child in plugin:
                child_tag = str(child.tag).split("}")[-1]
                if child_tag == "artifactId":
                    art_id = (child.text or "").strip()
            if art_id != "jacoco-maven-plugin":
                continue
            # 找到 jacoco 插件, 搜索其 configuration/excludes/exclude
            for desc in plugin.iter():
                desc_tag = str(desc.tag).split("}")[-1]
                if desc_tag == "exclude" and desc.text:
                    excludes.append(desc.text.strip())
    return excludes


def _detect_existing_test(project_root: Path, fqcn: str, module: str) -> bool:
    """探测 src/test/java 下是否已有对应测试类。"""
    pkg, _, simple = fqcn.rpartition(".")
    pkg_dir = pkg.replace(".", "/") if pkg else ""
    test_names = [simple + s for s in TEST_CLASS_SUFFIXES] + [TEST_CLASS_PREFIX + simple]
    module_dir = project_root if module == "." else project_root / module
    base = module_dir / "src" / "test" / "java"
    if pkg_dir:
        base = base / pkg_dir
    for name in test_names:
        if (base / f"{name}.java").is_file():
            return True
    return False


# --------------------------------------------------------------------------- #
# Git diff
# --------------------------------------------------------------------------- #
def _resolve_target_branch(project_root: Path, target: str | None) -> str | None:
    """自动探测目标分支(master > main > develop), 或使用指定值。"""
    from jaut.proc import resolve_tool, run_command

    if target and target != "auto":
        return target
    git = resolve_tool("git")
    if git is None:
        return None
    for branch in config.DEFAULT_TARGET_BRANCHES:
        result = gitops.run_command(  # type: ignore[attr-defined]
            [git, "rev-parse", "--verify", branch],
            cwd=str(project_root), capture=True,
            timeout=config.GIT_COMMAND_TIMEOUT_SECONDS)
        if result.ok:
            return branch
    return None


def _get_diff_files(project_root: Path, target: str | None,
                    diff_mode: str = "auto") -> list[tuple[str, int, int]]:
    """返回差异文件列表 [(路径, 增行, 删行)]; 不可用返回空列表。"""
    from jaut.proc import resolve_tool, run_command

    git = resolve_tool("git")
    if git is None:
        return []
    cwd = str(project_root)

    # 确定基准
    if target is None:
        return []

    # 尝试三点 merge-base, 失败回退两点
    merge_base_cmd = [git, "merge-base", target, "HEAD"]
    mb_result = run_command(merge_base_cmd, cwd=cwd, capture=True,
                            timeout=config.GIT_COMMAND_TIMEOUT_SECONDS)
    if diff_mode == "three" or (diff_mode == "auto" and mb_result.ok and mb_result.stdout):
        base = mb_result.stdout.strip()
        diff_ref = f"{base}...HEAD"
    elif diff_mode == "two":
        diff_ref = f"{target}..HEAD"
    else:
        # auto: 三点可用则用三点, 否则两点
        if mb_result.ok and mb_result.stdout:
            base = mb_result.stdout.strip()
            diff_ref = f"{base}...HEAD"
        else:
            diff_ref = f"{target}..HEAD"

    # 获取变更文件名
    name_cmd = [git, "diff", "--name-only", "--diff-filter=ACMR", diff_ref]
    name_result = run_command(name_cmd, cwd=cwd, capture=True,
                              timeout=config.GIT_COMMAND_TIMEOUT_SECONDS)
    if not name_result.ok or not name_result.stdout:
        return []

    files = [f.strip() for f in name_result.stdout.splitlines() if f.strip()]

    # 获取每个文件的增删行数
    numstat_cmd = [git, "diff", "--numstat", diff_ref]
    stat_result = run_command(numstat_cmd, cwd=cwd, capture=True,
                              timeout=config.GIT_COMMAND_TIMEOUT_SECONDS)
    stats: dict[str, tuple[int, int]] = {}
    if stat_result.ok and stat_result.stdout:
        for line in stat_result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                try:
                    add = int(parts[0]) if parts[0] != "-" else 0
                    dele = int(parts[1]) if parts[1] != "-" else 0
                    stats[parts[2]] = (add, dele)
                except ValueError:
                    pass

    result: list[tuple[str, int, int]] = []
    for f in files:
        add, dele = stats.get(f, (0, 0))
        result.append((f, add, dele))
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--target", default="auto",
                        help="目标分支(auto 探测 master/main/develop)")
    parser.add_argument("--diff-mode", choices=["auto", "three", "two"], default="auto")
    parser.add_argument("--exclude-glob", action="append", default=None, metavar="PATTERN",
                        help="排除 glob 模式(可重复)")
    parser.add_argument("--sort-policy", choices=["module-coverage", "coverage", "diff-size"],
                        default="module-coverage")
    parser.add_argument("--workdir", default=None)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        raise StepError.exec_error(f"项目根目录不存在: {project_root}")

    # workdir 用于日志(批量 workdir 在 batch_init 时才创建)
    from jaut.state import default_workdir
    workdir = Path(args.workdir).resolve() if args.workdir else default_workdir(project_root)
    workdir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(SCRIPT_NAME, workdir)

    target = _resolve_target_branch(project_root, args.target)
    if target is None:
        raise StepError.exec_error(
            "无法确定目标分支(自动探测 master/main/develop 均失败)",
            question="请通过 --target <分支名> 显式指定目标分支")

    logger.info(f"差异分析: worktree={project_root}, target={target}, diff_mode={args.diff_mode}")

    # 工作树当前分支
    branch = gitops.current_branch(project_root)

    # 获取差异文件
    raw_files = _get_diff_files(project_root, target, args.diff_mode)
    if not raw_files:
        logger.info("差异为空, 无候选类")
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary="差异分析完成: 无差异文件, 无候选类",
            route=Route.FINISH, reason="差异为空, 无需批量补测")
        return decision, EmitContext(workdir=workdir)

    # 过滤链
    pom_excludes = _parse_pom_jacoco_excludes(project_root)
    user_excludes = args.exclude_glob or []
    candidates: list[dict] = []
    for filepath, add, dele in raw_files:
        normalized = filepath.replace("\\", "/")
        # 仅 src/main/java
        if "/src/main/java/" not in normalized and not normalized.startswith("src/main/java/"):
            continue
        # 剔除测试类
        if is_test_file(normalized):
            continue
        # 剔除 package-info / module-info
        basename = os.path.basename(normalized)
        if basename in ("package-info.java", "module-info.java"):
            continue
        # pom jacoco excludes 过滤
        if any(matches_jacoco_pattern(normalized, pat) for pat in pom_excludes):
            logger.info(f"排除(jacoco excludes): {normalized}")
            continue
        # 用户 --exclude-glob 过滤
        if any(fnmatchcase(normalized, pat) for pat in user_excludes):
            logger.info(f"排除(--exclude-glob): {normalized}")
            continue

        # 源路径 → FQCN
        source_file = project_root / normalized
        fqcn = javasrc.fqcn_from_source_path(normalized)
        if fqcn is None:
            # 尝试从绝对路径解析
            fqcn = javasrc.fqcn_from_source_path(str(source_file))
        if fqcn is None:
            logger.warning(f"无法解析 FQCN: {normalized}")
            continue

        # 剔除接口/枚举/注解
        if _is_interface_or_enum(source_file):
            logger.info(f"排除(接口/枚举/注解): {fqcn}")
            continue

        # 模块定位
        module = maven.find_module_for_source(project_root, source_file)

        # 探测已有测试类
        has_tests = _detect_existing_test(project_root, fqcn, module)

        # 复杂度分析: 方法数
        method_count = _count_methods(source_file)

        candidates.append({
            "fqcn": fqcn,
            "source_file": normalized,
            "module": module,
            "diff_add": add,
            "diff_del": dele,
            "has_existing_tests": has_tests,
            "method_count": method_count,
        })

    if not candidates:
        logger.info("过滤后无候选类")
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary="差异分析完成: 过滤后无候选类(均为接口/枚举/已排除)",
            route=Route.FINISH, reason="无候选类, 无需批量补测")
        return decision, EmitContext(workdir=workdir)

    # 排序(默认 module-coverage: 模块聚类, 差异行数降序)
    if args.sort_policy == "diff-size":
        candidates.sort(key=lambda c: c["diff_add"] + c["diff_del"], reverse=True)
    elif args.sort_policy == "coverage":
        candidates.sort(key=lambda c: c["fqcn"])
    else:  # module-coverage
        candidates.sort(key=lambda c: (c["module"], -(c["diff_add"] + c["diff_del"])))

    # 构建确认门禁 ask_user
    lines = [
        f"差异分析完成: 目标分支 {target}, 当前分支 {branch or '未知'}",
        f"候选类 {len(candidates)} 个:",
        "",
    ]
    for i, c in enumerate(candidates, 1):
        test_flag = "有" if c["has_existing_tests"] else "无"
        is_large = (c["diff_add"] >= config.LARGE_CLASS_DIFF_THRESHOLD
                    or c["method_count"] >= config.LARGE_CLASS_METHOD_THRESHOLD)
        large_tag = " [大类]" if is_large else ""
        lines.append(
            f"  {i}. [{c['module']}] {c['fqcn']} "
            f"(+{c['diff_add']}/-{c['diff_del']}, {c['method_count']}方法(源码), {test_flag}测试类){large_tag}")

    lines.extend([
        "",
        f"排序策略: {args.sort_policy}",
        "",
        "请确认批量补测范围与门槛:",
        "  - 全部: --all --threshold 80",
        f"  - 前 N 个: --top N --threshold 80",
        "  - 指定类: --classes <FQCN,...> --threshold 80",
        "",
        "确认后运行 batch_init.py 开始批量基线。",
    ])
    question = "\n".join(lines)

    # 保存候选清单到 workdir 供 batch_init 读取
    import json
    candidates_file = workdir / "batch_candidates.json"
    with open(candidates_file, "w", encoding="utf-8") as f:
        json.dump({"target_branch": target, "candidates": candidates,
                   "sort_policy": args.sort_policy}, f, ensure_ascii=False, indent=2)

    decision = Decision(
        status="needs_input", exit_code=config.EXIT_CONTINUE,
        summary=f"差异分析完成: {len(candidates)} 个候选类, 等待用户确认范围与门槛",
        route=Route.ASK_USER, reason="确认门禁: 用户需确认批量补测范围与门槛",
        question=question)
    return decision, EmitContext(workdir=workdir)


def main(argv: list[str] | None = None) -> int:
    return run_cli(SCRIPT_NAME, add_arguments, handler, argv)


if __name__ == "__main__":
    sys.exit(main())
