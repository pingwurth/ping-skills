#!/usr/bin/env python3
"""第 3 步：按 entry.txt 中的正则定位代码库内所有埋点调用点，写入 call_sites.json。

每个调用点记录：file:start_line-end_line（含匹配到的 pattern、片段）。
调用点按 (file, start_line) 去重，同一位置被多个 pattern 命中会合并记录 patterns 列表。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from utils import ARTIFACT_DIR, check_rg_available, next_step, setup_logging

LOG = None


def check_confirmed(entry_path: Path) -> bool:
    """检查 entry.txt 是否包含人工确认标记 '# CONFIRMED'。"""
    for line in entry_path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "# CONFIRMED":
            return True
    return False

# 仅扫描这些后缀（覆盖主流前后端语言）；如需扩展改这里
DEFAULT_EXTS = {
    ".js", ".jsx", ".ts", ".tsx", ".vue", ".mjs", ".cjs",
    ".py", ".java", ".kt", ".swift", ".go", ".dart",
    ".html", ".svelte", ".astro",
}

# rg 与纯 Python 回退统一排除的目录：依赖产物与本 skill 自身的产物目录。
# 不排除的话：无 .gitignore 时 node_modules 会被扫入；回退模式会把
# .agent/ 下落盘的源码后缀文件当成调用点，且两种模式结果不一致。
EXCLUDED_DIRS = {".agent", "node_modules", "vendor", "dist", "build", "venv", ".venv"}

# rg 侧等价的 glob 排除参数（每个排除目录一条）
_RG_EXCLUDE_ARGS = [f"--glob=!{d}/**" for d in sorted(EXCLUDED_DIRS)]


def _is_excluded(rel_parts: tuple[str, ...]) -> bool:
    """相对路径的任一段命中排除目录或为隐藏目录则排除（与 rg 默认跳过隐藏一致）。"""
    for part in rel_parts:
        if part in EXCLUDED_DIRS:
            return True
    # 隐藏目录对齐 rg 默认行为（rg 不加 --hidden 时跳过 . 开头路径）；
    # 文件名本身以 . 开头（如 .foo.js）也一并跳过，与 rg 一致
    return any(p.startswith(".") for p in rel_parts)


def read_patterns(entry_path: Path) -> list[str]:
    """读取 entry.txt：非 # 开头、非空行视为正则。"""
    patterns: list[str] = []
    for raw in entry_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        patterns.append(s)
    return patterns


def rg_search(pattern: str, root: Path) -> tuple[list[tuple[str, int, str]], bool]:
    """用 ripgrep 搜索；返回 ([(相对file, line_no, matched_text)], ok)。

    ok=False 表示 rg 不支持该正则（Rust regex 引擎不支持 lookahead /
    反向引用等 Python 语法），调用方应回退纯 Python 搜索该 pattern，
    不能静默丢弃——否则该正则的调用点会整体丢失且无从察觉。
    """
    try:
        proc = subprocess.run(
            ["rg", "-n", "--no-heading", "--color=never",
             *_RG_EXCLUDE_ARGS, "-e", pattern, "."],
            capture_output=True, text=True, cwd=str(root),
        )
    except FileNotFoundError:
        return [], False
    if proc.returncode not in (0, 1):  # 1 = 无匹配；2 = 正则语法错误等
        # 正则语法等问题：告知调用方回退（rg 内部错误无中文消息可读，写日志即可）
        sys.stderr.write(f"[WARN] rg 无法处理正则 {pattern!r}（错误码 {proc.returncode}），回退 Python 搜索\n")
        return [], False
    out: list[tuple[str, int, str]] = []
    for ln in proc.stdout.splitlines():
        # 形如 path/to/file.ts:42:matched text（路径相对 root，可能带 ./ 前缀）
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
        matched = ln[idx2 + 1:]
        out.append((file, lineno, matched))
    return out, True


def py_search(pattern: str, root: Path, compiled: re.Pattern,
              exts: set[str] | None = None) -> list[tuple[str, int, str]]:
    """纯 Python 回退搜索（无 rg 或 rg 不支持该正则时）。

    exts 为 None 时用 DEFAULT_EXTS；--exts 参数在此生效（rg 模式忽略
    后缀白名单，与 --help 描述一致）。
    排除规则与 rg 模式对齐（EXCLUDED_DIRS + 隐藏路径）。已知残余差异：
    rg 默认尊重 .gitignore 而此处不解析——被 gitignore 的生成目录里若
    真有埋点调用，rg 模式会漏掉；此类场景少见，靠第 6 步版本比对兜底。
    """
    exts = exts if exts is not None else DEFAULT_EXTS
    out: list[tuple[str, int, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in exts:
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if _is_excluded(rel_parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in compiled.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            out.append((str(path.relative_to(root)), lineno, m.group(0)))
    return out


def build_exclusions(sensors_path: Path | None, root: Path) -> list[tuple[str, int, int]]:
    """从 sensors.json 提取『定义型』入口的函数体范围，作为排除区间。

    wrapper / hook 类入口的 location 指向函数定义本身，其正则会把定义行
    及函数体内部的转发调用（如 wrapper 内的 sensors.track）一并命中——
    这些属于封装实现，不是业务调用点，应整段排除。
    返回 [(file, body_start_line, body_end_line)]，1-indexed 闭区间。
    若无法解析出函数体，退化为排除单行（start_line, start_line）。
    """
    if not sensors_path:
        return []
    if not sensors_path.exists():
        # 静默降级会让 wrapper 内部的转发调用被误判为业务调用点，
        # 必须留痕提示用户用 --sensors 指明路径
        if LOG:
            LOG.warning(
                "未找到 sensors.json（%s）：无法排除 wrapper/hook 函数体，"
                "封装实现内部的命中会被当作业务调用点（误报）。"
                "请用 --sensors 指明路径", sensors_path)
        return []
    import json as _json
    data = _json.loads(sensors_path.read_text(encoding="utf-8"))
    ranges: list[tuple[str, int, int]] = []
    for p in data.get("paradigms", []):
        for ef in p.get("entry_functions", []):
            if ef.get("kind") not in ("wrapper", "hook"):
                continue
            loc = ef.get("location", {})
            file = loc.get("file")
            start = loc.get("start_line")
            if not (file and start):
                continue
            end = loc.get("end_line") or compute_body_end(root / file, int(start))
            ranges.append((file, int(start), int(end) if end else int(start)))
    return ranges


def compute_body_end(path: Path, sig_line: int) -> int | None:
    """从签名行起找函数体的 '{'，按花括号平衡算到结束行（1-indexed）。

    只在括号深度为 0 时（即参数列表之外）才统计花括号，避免把默认参数
    里的 `{}`、泛型 `Record<...>` 误判为函数体。跳过字符串、`//` 行注释
    与 `/* ... */` 块注释（可跨行）内的括号。找不到函数体 '{' 返回 None。
    已知局限：不处理 Python `#` 行注释（Python 无花括号函数体，影响有限）。
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    lines = text.split("\n")
    depth = 0          # 花括号深度
    paren = 0          # 圆括号深度（参数列表）
    seen_open = False
    quote: str | None = None
    block = False      # 是否处于 /* ... */ 块注释内（跨行保持）
    i = sig_line - 1
    col = 0
    while i < len(lines):
        row = lines[i]
        while col < len(row):
            ch = row[col]
            nxt = row[col + 1] if col + 1 < len(row) else ""
            if block:
                if ch == "*" and nxt == "/":
                    block = False
                    col += 1  # 连同 '/' 一起跳过
            elif quote:
                if ch == "\\":
                    col += 1
                elif ch == quote:
                    quote = None
            elif ch in ("'", '"', "`"):
                quote = ch
            elif ch == "/" and nxt == "/":
                break  # 行注释：余下本行跳过
            elif ch == "/" and nxt == "*":
                block = True
                col += 1  # 连同 '*' 一起跳过
            elif ch == "(":
                paren += 1
            elif ch == ")":
                if paren > 0:
                    paren -= 1
            elif paren == 0:
                if ch == "{":
                    depth += 1
                    seen_open = True
                elif ch == "}":
                    if depth > 0:
                        depth -= 1
                        if seen_open and depth == 0:
                            return i + 1
            col += 1
        col = 0
        i += 1
    return None


def in_exclusions(file: str, lineno: int, ranges: list[tuple[str, int, int]]) -> bool:
    for f, s, e in ranges:
        if f == file and s <= lineno <= e:
            return True
    return False


def compute_end_line(lines: list[str], start_line: int, start_col: int) -> int:
    """从 (start_line 行的 start_col 列) 起扫描括号平衡，确定调用语句结束行。

    覆盖多行函数调用 trackEvent(\n  a,\n  b\n) 的结束位置。
    指令/装饰器等无括号的匹配 → 终止于 start_line。
    跳过字符串、`//` 行注释与 `/* ... */` 块注释（可跨行）内的括号，减少误判。
    已知局限：不处理 Python `#` 行注释（Python 调用行内含 '#' 的情况少见）。
    """
    depth = 0
    seen_open = False
    i = start_line - 1
    col = start_col
    quote: str | None = None
    block = False
    while i < len(lines):
        row = lines[i]
        while col < len(row):
            ch = row[col]
            nxt = row[col + 1] if col + 1 < len(row) else ""
            if block:
                if ch == "*" and nxt == "/":
                    block = False
                    col += 1  # 连同 '/' 一起跳过
            elif quote:
                if ch == "\\":
                    col += 1
                elif ch == quote:
                    quote = None
            elif ch in ("'", '"', "`"):
                quote = ch
            elif ch == "/" and nxt == "/":
                break  # 行注释：余下本行跳过
            elif ch == "/" and nxt == "*":
                block = True
                col += 1  # 连同 '*' 一起跳过
            elif ch == "(":
                depth += 1
                seen_open = True
            elif ch == ")":
                if depth > 0:
                    depth -= 1
                    if seen_open and depth == 0:
                        return i + 1
            col += 1
        col = 0
        i += 1
    return start_line  # 未检测到平衡括号则单行


def build_snippet(lines: list[str], start: int, end: int) -> str:
    """提取 start..end（1-indexed 闭区间）的代码片段，截断到 4000 字符。"""
    a = max(0, start - 1)
    b = min(len(lines), end)
    return "\n".join(lines[a:b])[:4000]


def main() -> int:
    ap = argparse.ArgumentParser(description="按 entry.txt 正则定位埋点调用点 -> call_sites.json")
    ap.add_argument("--entry", default=str(ARTIFACT_DIR / "entry.txt"),
                    help="人工复核后的 entry.txt（默认: .agent/sensors-analyze/entry.txt）")
    ap.add_argument("--root", required=True, help="代码库根目录")
    ap.add_argument("--out", default=str(ARTIFACT_DIR / "call_sites.json"),
                    help="输出文件（默认: .agent/sensors-analyze/call_sites.json）")
    ap.add_argument("--exts", default=",".join(sorted(DEFAULT_EXTS)),
                    help="纯 Python 回退模式下的后缀白名单（逗号分隔），rg 模式忽略")
    ap.add_argument("--sensors", default=str(ARTIFACT_DIR / "sensors.json"),
                    help="sensors.json 路径；用于排除 wrapper/hook 入口自身的定义行"
                         "（默认: .agent/sensors-analyze/sensors.json）")
    ap.add_argument("--log", default=None, help="日志文件路径（默认: <out>.log）")
    args = ap.parse_args()

    global LOG
    out = Path(args.out)
    log_path = Path(args.log) if args.log else out.with_suffix(".log")
    LOG = setup_logging("locate_call_sites", log_path)

    exit_code = 0
    n_sites = 0

    try:
        entry = Path(args.entry)
        if not entry.exists():
            LOG.error("找不到 %s，请先运行 extract_entries.py 并人工复核", entry)
            exit_code = 1
            return exit_code

        if not check_confirmed(entry):
            LOG.error("entry.txt 尚未人工确认（缺少 '# CONFIRMED' 标记）")
            exit_code = 2
            return exit_code

        root = Path(args.root).resolve()
        if not root.is_dir():
            LOG.error("代码库根不存在: %s", root)
            exit_code = 1
            return exit_code

        patterns = read_patterns(entry)
        if not patterns:
            LOG.error("entry.txt 中没有有效正则")
            exit_code = 1
            return exit_code

        exclusions = build_exclusions(Path(args.sensors), root)
        if exclusions:
            LOG.info("排除 %d 个 wrapper/hook 函数体区间（封装实现，非业务调用点）", len(exclusions))

        have_rg = check_rg_available()
        compiled = {p: re.compile(p) for p in patterns}
        exts = {e.strip() for e in args.exts.split(",") if e.strip()}
        LOG.info("搜索方式: %s；正则数: %d", 'ripgrep' if have_rg else 'python fallback', len(patterns))

        # (file, start_line) -> {patterns:set, matched_text, end_line}
        sites: dict[tuple[str, int], dict] = {}
        # 文件内容缓存：避免同一文件被多个 pattern 重复读取
        file_content_cache: dict[str, str] = {}

        for pat in patterns:
            if have_rg:
                hits, rg_ok = rg_search(pat, root)
                if not rg_ok:
                    LOG.warning("rg 不支持正则 %r（lookahead/反向引用等 Rust regex 语法限制），该正则回退纯 Python 搜索", pat)
                    hits = py_search(pat, root, compiled[pat], exts)
            else:
                hits = py_search(pat, root, compiled[pat], exts)
            for file, lineno, matched in hits:
                if in_exclusions(file, lineno, exclusions):
                    continue  # 落在 wrapper/hook 函数体内，属封装实现，非业务调用点
                key = (file, lineno)
                if key not in sites:
                    if file not in file_content_cache:
                        try:
                            file_content_cache[file] = (root / file).read_text(encoding="utf-8", errors="ignore")
                        except Exception:
                            file_content_cache[file] = ""
                    content = file_content_cache[file]
                    lines_list = content.split("\n")
                    # 计算 matched 在该行的起始列（用于括号扫描）
                    m = compiled[pat].search(lines_list[lineno - 1]) if 0 <= lineno - 1 < len(lines_list) else None
                    start_col = m.start() if m else 0
                    end_line = compute_end_line(lines_list, lineno, start_col) if content else lineno
                    sites[key] = {
                        "file": file,
                        "start_line": lineno,
                        "end_line": end_line,
                        "patterns": [],
                        "snippet": build_snippet(lines_list, lineno, end_line),
                    }
                sites[key]["patterns"].append(pat)

        result = []
        for i, ((file, sl), v) in enumerate(sorted(sites.items(), key=lambda kv: (kv[0][0], kv[0][1])), 1):
            result.append({
                "id": i,
                "file": file,
                "start_line": v["start_line"],
                "end_line": v["end_line"],
                "location": f"{file}:{v['start_line']}-{v['end_line']}",
                # 与 sensors.csv 的 file 列完全同格式（逗号分隔），
                # 第 4 步可直接取用，避免 LLM 手工做 连字符→逗号 转换出错
                # （注意写入 CSV 时该值含逗号，仍需整体加双引号）
                "file_csv": f"{file}:{v['start_line']},{v['end_line']}",
                "patterns": v["patterns"],
                "snippet": v["snippet"],
            })

        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        n_sites = len(result)
        LOG.info("定位到 %d 处调用点，写入 %s", n_sites, out)

    except Exception as exc:
        LOG.exception("执行失败: %s", exc)
        exit_code = 1

    finally:
        if exit_code == 2:
            next_step(f"entry.txt 尚未人工确认，请复核后添加 '# CONFIRMED' 再重试")
        elif exit_code != 0:
            next_step(f"执行失败，请查看日志: {log_path}")
        elif n_sites == 0:
            next_step(
                f"未定位到任何调用点。大概率是 {Path(args.entry)} 的正则与实际调用形式不匹配，"
                f"请回到第 2 步复核修正；少数情况是代码用动态调用（如 sensors[name]()），"
                f"需在 entry.txt 补充动态调用的正则"
            )
        else:
            next_step(f"定位到 {n_sites} 处调用点。请进入第 4 步：LLM 逐条读取 {out}，追加写入 sensors.csv")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
