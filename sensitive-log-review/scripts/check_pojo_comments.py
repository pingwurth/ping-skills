#!/usr/bin/env python3
"""POJO 字段注释检查工具（公共模块版）。

流程:
  1. git diff <branch> 获取变更文件（git -C <repo> 贯通）
  2. common.pojo_config 统一判定 POJO 文件（src/main/java 范围内）
  3. POJO 变动文件列表写入 pojo-changed.list
  4. 校验 checkstyle jar SHA256 基线（P2-5）
  5. 运行 checkstyle pojo-field-doc.xml 规则检查字段注释
  6. 结果写入 pojo-miss-comments.txt

stdout 末尾输出机器可读摘要行，供 main.py 解析:
  SUMMARY: pojo_changed=<n> miss_comments=<n>

退出码:
  0: 检查通过（或无变更文件）
  1: checkstyle 发现注释违规
  2: 执行错误（依赖缺失、jar 校验失败、git 失败等）
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.errors import ErrorCode, print_error  # noqa: E402
from common.git_utils import get_changed_files, resolve_repo, validate_ref  # noqa: E402
from common.paths import POJO_CHANGED, POJO_COMMENTS, artifact, get_output_dir  # noqa: E402
from common.pojo_config import is_pojo_file, load_pojo_config  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
POJO_CONFIG_FILE = SCRIPT_DIR / "rules" / "pojo.config"
CHECKSTYLE_DIR = SCRIPT_DIR / "checkstyle"
JAR_NAME = "checkstyle-13.7.0-all.jar"


def locate_dependencies() -> tuple[Path, Path]:
    """定位 checkstyle jar 与 pojo-field-doc.xml，缺失时 SystemExit(2)。"""
    jar = CHECKSTYLE_DIR / JAR_NAME
    if not jar.is_file():
        jar = SCRIPT_DIR / JAR_NAME
    if not jar.is_file():
        print(f"错误: 找不到 {JAR_NAME}", file=sys.stderr)
        raise SystemExit(2)

    config = CHECKSTYLE_DIR / "pojo-field-doc.xml"
    if not config.is_file():
        config = SCRIPT_DIR / "rules" / "pojo-field-doc.xml"
    if not config.is_file():
        print("错误: 找不到 pojo-field-doc.xml 配置文件", file=sys.stderr)
        raise SystemExit(2)

    return jar, config


def verify_jar_sha256(jar: Path) -> None:
    """校验 checkstyle jar 的 SHA256 基线（P2-5 防篡改）。

    基线文件 <jar>.sha256 不存在时仅告警（允许首次引导）；
    存在但摘要不一致时 SystemExit(2)。
    """
    baseline = jar.with_suffix(jar.suffix + ".sha256")
    if not baseline.is_file():
        print(f"警告: jar 完整性基线缺失，跳过校验: {baseline}", file=sys.stderr)
        return
    expected = baseline.read_text(encoding="utf-8").split()[0].strip().lower()
    actual = hashlib.sha256(jar.read_bytes()).hexdigest()
    if actual != expected:
        # 结构化错误提示（E007），退出码契约保持 2 不变
        print_error(ErrorCode.E007_JAR_CHECKSUM_MISMATCH, location=str(jar),
                    detail=f"期望={expected} 实际={actual}")
        raise SystemExit(2)
    print(f"# jar 完整性校验通过: {jar.name}", file=sys.stderr)


def collect_changed_pojo_files(repo: Path, branch: str) -> list[Path]:
    """获取变更文件中的 POJO Java 文件集合（统一判定）。"""
    changed = get_changed_files(repo, branch)
    directories, suffixes = load_pojo_config(POJO_CONFIG_FILE)

    pojo_files: list[Path] = []
    for relative in changed:
        if not relative.endswith(".java"):
            continue
        file_path = (repo / relative).resolve()
        if not file_path.is_file():
            continue
        if is_pojo_file(file_path, repo, directories, suffixes):
            pojo_files.append(file_path)
    return sorted(set(pojo_files), key=lambda item: str(item).casefold())


def decode_output(data: bytes) -> str:
    """解码子进程输出，utf-8 失败时回退 gbk（Windows 中文环境）。"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def run_checkstyle(jar: Path, config: Path, java_files: list[Path], output_file: Path) -> tuple[int, str]:
    """运行 checkstyle，返回 (退出码, 输出文本)。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as argfile:
        for f in java_files:
            argfile.write(str(f) + "\n")
        argfile_path = argfile.name

    try:
        cmd = [
            "java",
            "-cp", str(jar),
            "com.puppycrawl.tools.checkstyle.Main",
            "-c", str(config),
            f"@{argfile_path}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            # 结构化错误提示（E002），仅影响步骤6 POJO 注释检查
            print_error(ErrorCode.E002_JAVA_NOT_FOUND, location="java")
            return 2, ""
    finally:
        Path(argfile_path).unlink(missing_ok=True)

    output_text = decode_output(result.stdout) + decode_output(result.stderr)
    try:
        output_file.write_text(output_text, encoding="utf-8")
    except OSError as exc:
        print(f"错误: 写入文件失败 {output_file}: {exc}", file=sys.stderr)
        return 2, output_text
    return result.returncode, output_text


def count_error_lines(output_text: str) -> int:
    """统计 checkstyle 输出中的 [ERROR] 行数。"""
    return sum(1 for line in output_text.splitlines() if line.startswith("[ERROR]"))


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="POJO 字段注释检查（checkstyle pojo-field-doc）",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="git 仓库目录（默认: 当前目录）",
    )
    parser.add_argument(
        "-b", "--branch",
        type=str,
        default="master",
        help="目标分支名称（默认: master）",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        default=None,
        help="审查产物输出目录（默认: 环境变量 SLR_OUTPUT_DIR 或系统临时目录）",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="并行运行隔离子目录名（CI 场景）",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="启用详细输出模式",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    repo = resolve_repo(args.repo)
    try:
        branch = validate_ref(args.branch)
    except ValueError as exc:
        # 结构化错误提示（E003）
        print_error(ErrorCode.E003_BRANCH_INVALID, location=args.branch, detail=str(exc))
        return 2
    try:
        out_dir = get_output_dir(args.output_dir, args.run_id)
    except ValueError as exc:
        # 结构化错误提示（E005）
        print_error(ErrorCode.E005_RUN_ID_INVALID, location=str(args.run_id), detail=str(exc))
        return 2
    except OSError as exc:
        # 结构化错误提示（E006）
        print_error(ErrorCode.E006_OUTPUT_DIR_UNWRITABLE, location=str(args.output_dir), detail=str(exc))
        return 2

    pojo_changed_file = artifact(out_dir, POJO_CHANGED)
    comments_output_file = artifact(out_dir, POJO_COMMENTS)

    jar, config = locate_dependencies()
    verify_jar_sha256(jar)

    # 获取变更 POJO 文件
    try:
        pojo_files = collect_changed_pojo_files(repo, branch)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"错误: git diff 执行失败: {exc.stderr.strip()}", file=sys.stderr)
        return 2

    print(f"找到 {len(pojo_files)} 个变更的 POJO Java 文件")

    # 写 POJO 变动清单（无变更时写空文件，供下游 analyze 步骤使用）
    try:
        with open(pojo_changed_file, "w", encoding="utf-8") as f:
            for file_path in pojo_files:
                f.write(str(file_path) + "\n")
    except OSError as exc:
        print(f"错误: 写入文件失败 {pojo_changed_file}: {exc}", file=sys.stderr)
        return 2

    if not pojo_files:
        comments_output_file.write_text("", encoding="utf-8")
        print("没有需要检查的文件")
        print("SUMMARY: pojo_changed=0 miss_comments=0")
        return 0

    if args.verbose:
        print("待检查文件:")
        for f in pojo_files:
            print(f"  {f}")

    print(f"开始 checkstyle 检查 {len(pojo_files)} 个文件...")
    exit_code, output_text = run_checkstyle(jar, config, pojo_files, comments_output_file)
    if exit_code == 2:
        return 2

    miss_count = count_error_lines(output_text)
    print(f"SUMMARY: pojo_changed={len(pojo_files)} miss_comments={miss_count}")

    if exit_code == 0:
        print("检查完成: 所有 POJO 字段注释正确")
        return 0
    print(f"检查完成: 发现 {miss_count} 处注释问题 -> {comments_output_file}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n操作已取消。", file=sys.stderr)
        raise SystemExit(130)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        finally:
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as e:
        print(f"错误: 发生未预期的异常: {e}", file=sys.stderr)
        raise SystemExit(2)
