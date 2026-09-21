#!/usr/bin/env python3
"""第 2 步：从 sensors.json 提取埋点追踪入口函数候选，写入 entry.txt 供人工复核。

entry.txt 格式约定（locate_call_sites.py 据此读取）：
  - 以 # 开头的行是注释，空行忽略
  - 其余每行是一个正则表达式，用于在第 3 步 grep 定位调用点
  - 人工可直接删行（误报）、增行（漏报）或改正则
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from utils import check_rg_available, next_step, setup_logging

LOG = None


def _rg(pattern: str, root: Path) -> list[str]:
    """用 ripgrep 搜索，返回匹配行所在的文件:行号列表。"""
    try:
        proc = subprocess.run(
            ["rg", "-n", "--no-heading", "--color=never", "-e", pattern, "."],
            capture_output=True, text=True, cwd=str(root),
        )
    except FileNotFoundError:
        return []
    if proc.returncode not in (0, 1):
        return []
    results: list[str] = []
    for ln in proc.stdout.splitlines():
        idx1 = ln.find(":")
        idx2 = ln.find(":", idx1 + 1)
        if idx1 == -1 or idx2 == -1:
            continue
        file = ln[:idx1]
        if file.startswith("./"):
            file = file[2:]
        try:
            lineno = int(ln[idx1 + 1:idx2])
        except ValueError:
            continue
        results.append(f"{file}:{lineno}")
    return results


def _py_scan(pattern: str, root: Path, exts: set[str]) -> list[str]:
    """纯 Python 回退扫描。"""
    compiled = re.compile(pattern)
    results: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in exts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in compiled.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            results.append(f"{path.relative_to(root)}:{lineno}")
    return results


SENSOR_KEYWORDS = re.compile(
    r"(?i)\b(?:sensors?|track|report)\w*(?:init|setup|config|_fn|Func)?\b"
    r"|\b\w*(?:sensors?|track|report)(?:Init|Setup|Config)\b"
)


def audit_uncovered(root: Path, sensors_data: dict, have_rg: bool) -> list[str]:
    """扫描代码库里含 sensors/track 关键词的函数定义，返回未被 sensors.json 覆盖的候选。

    Returns list of "name @ file:line" strings.
    """
    known_names: set[str] = set()
    for p in sensors_data.get("paradigms", []):
        for ef in p.get("entry_functions", []):
            known_names.add(ef.get("name", ""))

    exts = {
        ".js", ".jsx", ".ts", ".tsx", ".vue", ".mjs", ".cjs",
        ".py", ".java", ".kt", ".swift", ".go", ".dart",
        ".html", ".svelte", ".astro",
    }

    # 匹配函数定义的正则：function 声明、export function、箭头赋值、var/let/const 声明、方法定义
    def_pattern = (
        r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"  # function foo / export async function foo
        r"|(?:var|let|const)\s+(\w+)\s*=\s*(?:async\s+)?function"  # const foo = function / async function
        r"|(?:var|let|const)\s+(\w+)\s*=\s*(?:async\s+)?"  # const foo = (arrow) / = await
        r"|(\w+)\s*(?::\s*\w+)?\s*\([^)]*\)\s*(?::\s*\w+)?\s*{"  # methodName(...) { (class methods)
        r"|def\s+(\w+)\s*\("  # Python def foo(
    )
    def_re = re.compile(def_pattern)

    candidates: list[str] = []
    scanned_files = 0

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in exts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        scanned_files += 1
        rel = str(path.relative_to(root))
        for i, line in enumerate(text.splitlines(), 1):
            if not SENSOR_KEYWORDS.search(line):
                continue
            m = def_re.search(line)
            if not m:
                continue
            # 提取第一个非空的捕获组
            name = next((g for g in m.groups() if g), None)
            if not name:
                continue
            if name not in known_names:
                candidates.append(f"{name} @ {rel}:{i}")

    if LOG:
        LOG.info("audit_uncovered: scanned %d files, found %d candidates", scanned_files, len(candidates))
    return candidates


def audit_sdk_coverage(root: Path, sensors_data: dict, have_rg: bool) -> dict[str, list[str]]:
    """用 global_sdk_apis 的名称搜索调用点，返回未被 entry_function 的 grep_pattern 覆盖的位置。

    Returns {api_name: ["file:line", ...]} for uncovered locations.
    """
    # 收集所有 entry_function 的 grep_pattern 命中位置
    covered: set[str] = set()
    exts = {
        ".js", ".jsx", ".ts", ".tsx", ".vue", ".mjs", ".cjs",
        ".py", ".java", ".kt", ".swift", ".go", ".dart",
        ".html", ".svelte", ".astro",
    }
    for p in sensors_data.get("paradigms", []):
        for ef in p.get("entry_functions", []):
            pat = ef.get("grep_pattern", "").strip()
            if not pat:
                continue
            if have_rg:
                covered.update(_rg(pat, root))
            else:
                covered.update(_py_scan(pat, root, exts))

    # 用每个 sdk_api 的名称搜调用点
    uncovered: dict[str, list[str]] = {}
    for api in sensors_data.get("meta", {}).get("global_sdk_apis", []):
        api_name = api.get("name", "")
        if not api_name:
            continue
        # 用 \b 匹配 API 名的完整调用形式
        api_pattern = re.escape(api_name).replace(r"\.", r"[.\[]")
        if have_rg:
            api_locs = _rg(api_pattern, root)
        else:
            api_locs = _py_scan(api_pattern, root, exts)
        diff = [loc for loc in api_locs if loc not in covered]
        if diff:
            uncovered[api_name] = diff[:20]  # 截断，避免日志过大

    if LOG:
        total_uncovered = sum(len(v) for v in uncovered.values())
        LOG.info("audit_sdk_coverage: %d APIs, %d uncovered call sites",
                 len(uncovered), total_uncovered)
    return uncovered


def load_sensors(path: Path) -> dict:
    """加载 sensors.json 并做基本结构校验（纯 stdlib，无第三方依赖）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"sensors.json 根必须是对象，实际为 {type(data).__name__}")
    if "paradigms" not in data:
        raise ValueError("sensors.json 缺少必需字段 'paradigms'")
    if not isinstance(data["paradigms"], list):
        raise ValueError(f"'paradigms' 必须是数组，实际为 {type(data['paradigms']).__name__}")
    return data


def main() -> int:
    global LOG
    ap = argparse.ArgumentParser(description="从 sensors.json 提取埋点入口函数候选 -> entry.txt")
    ap.add_argument("--sensors", default="sensors.json", help="sensors.json 路径（默认当前目录）")
    ap.add_argument("--out", default="entry.txt", help="输出 entry.txt 路径（默认当前目录）")
    ap.add_argument("--log", default=None, help="日志文件路径（默认: <out>.log）")
    ap.add_argument("--audit", action="store_true", help="启用独立审计：扫描代码库查找可能遗漏的入口函数")
    ap.add_argument("--root", default=None, help="审计模式下目标代码库根目录（--audit 时必需）")
    args = ap.parse_args()

    out = Path(args.out)
    log_path = Path(args.log) if args.log else out.with_suffix(".log")
    LOG = setup_logging("extract_entries", log_path)

    exit_code = 0
    audit_issues = False
    n = 0

    try:
        sensors_path = Path(args.sensors)
        if not sensors_path.exists():
            LOG.error("找不到 %s，请先由 LLM 完成 step 1 生成 sensors.json", sensors_path)
            exit_code = 1
            return exit_code

        data = load_sensors(sensors_path)
        paradigms = data.get("paradigms", [])
        if not paradigms:
            LOG.error("sensors.json 中没有 paradigms")
            exit_code = 1
            return exit_code

        # ---- 审计模式 ----
        if args.audit:
            root = Path(args.root) if args.root else None
            if not root or not root.is_dir():
                LOG.error("--audit 需要有效的 --root 参数")
                exit_code = 1
                return exit_code

            LOG.info("=== 审计模式：独立扫描代码库 %s ===", root)
            have_rg = check_rg_available()

            # 1) 未覆盖的传感器相关函数
            candidates = audit_uncovered(root, data, have_rg)
            if candidates:
                audit_issues = True
                LOG.warning("发现 %d 个疑似埋点函数未被 sensors.json 覆盖：", len(candidates))
                for c in candidates:
                    LOG.warning("  - %s", c)
                LOG.warning("请回 Step 1 检查这些函数，补充 entry_function 后重新运行")
            else:
                LOG.info("未覆盖函数检查：通过（无遗漏候选）")

            # 2) SDK API 调用点覆盖验证
            uncovered_apis = audit_sdk_coverage(root, data, have_rg)
            if uncovered_apis:
                audit_issues = True
                LOG.warning("以下 SDK API 有调用点未被任何 entry_function 覆盖：")
                for api_name, locs in uncovered_apis.items():
                    LOG.warning("  %s: %d 处未覆盖（如 %s）", api_name, len(locs), locs[0])
                LOG.warning("请回 Step 1 检查，补充缺失的 entry_function 或 paradigm")
            else:
                LOG.info("SDK API 覆盖检查：通过")

            if audit_issues:
                LOG.info("=== 审计完成：发现问题，请查看上方日志 ===")
            else:
                LOG.info("=== 审计完成：全部通过 ===")

        # ---- 提取 entry_functions ----
        lines: list[str] = [
            "# 埋点追踪入口函数候选（由 extract_entries.py 自动生成，请人工复核）",
            f"# 生成时间: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "# 格式：非 # 开头、非空的行 = 一个正则；locate_call_sites.py 据此 grep 定位调用点",
            "# 复核说明：",
            "#   - 误报：整行删除或把 # 加到行首注释掉",
            "#   - 漏报：新增一行正则",
            "#   - 正则需匹配该入口的实际调用形式（函数调用/指令/装饰器/hook 等）",
            "# ---- 以下每行：# [paradigm] name (kind) @ file:line  ；下一行是对应正则",
        ]

        for p in paradigms:
            pid = p.get("id", "?")
            for ef in p.get("entry_functions", []):
                name = ef.get("name", "?")
                kind = ef.get("kind", "?")
                loc = ef.get("location", {})
                file = loc.get("file", "?")
                line = loc.get("start_line", "?")
                pattern = ef.get("grep_pattern", "").strip()
                note = ef.get("note", "")
                lines.append(f"# [{pid}] {name} ({kind}) @ {file}:{line}" + (f"  note: {note}" if note else ""))
                if not pattern:
                    lines.append(f"# [WARN] 缺少 grep_pattern，已跳过：{name}")
                    continue
                lines.append(pattern)
                n += 1
        lines.append("# ---- 复核完成后在此文件任意位置添加 '# CONFIRMED'，然后运行 locate_call_sites.py")

        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        LOG.info("提取 %d 个候选入口正则，写入 %s", n, out)

    except Exception as exc:
        LOG.exception("执行失败: %s", exc)
        exit_code = 1

    finally:
        if exit_code != 0:
            next_step(f"执行失败，请查看日志: {log_path}")
        elif audit_issues:
            next_step(f"审计发现疑似遗漏函数，请查看日志 {log_path}，修正 sensors.json 后重新运行")
        else:
            next_step(f"请人工复核 {out}，确认无误后添加 '# CONFIRMED' 标记，然后运行 locate_call_sites.py")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
