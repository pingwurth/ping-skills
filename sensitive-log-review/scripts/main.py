#!/usr/bin/env python3
"""敏感信息日志审查主脚本（编排层）。

执行流程（P1-6 四链路并行，目标全流程 <60s）:
  链路A: 步骤1 扫描日志输出     -> 步骤2 变更日志检查
  链路B: 步骤3 @ToString 注解检查
  链路C: 步骤4 字段提取审计     -> 步骤5 敏感词变更检查
  链路D: 步骤6 POJO 注释检查    -> 步骤7 深度敏感性分析
  汇合后: 步骤8 生成 HTML 报告

扫描范围:
  默认 --changed-only：仅扫描相对目标分支变更的 Java 文件；
  --full-scan 显式回退全量扫描。

退出码（P0-1 门禁契约）:
  0: 通过（--fail-on 指定的计数项全部为 0）
  1: 触发门禁（--fail-on 任一计数项 > 0，默认 violation,sensitive）
  2: 执行错误（关键步骤 1/2/4/5/7/8 失败；步骤 3/6 仅警告）

可选门禁计数项: violation, sensitive, unqualified, tostring, tostring_sensitive,
               analyze, miss_comments, low
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.dictionary import (  # noqa: E402
    BLACKLIST_FILE,
    CORE_FILE,
    EN_US_DICT_FILE,
    EN_US_WHITELIST_FILE,
    EXTENDED_FILE,
    WHITELIST_FILE,
)
from common.errors import ErrorCode, print_error  # noqa: E402
from common.failures import clear_failure_shards, merge_failure_shards  # noqa: E402
from common.git_utils import get_changed_files, resolve_repo, validate_ref  # noqa: E402
from common.paths import (  # noqa: E402
    ANALYZE_RESULT,
    FAILED_FILES_LIST,
    HTML_REPORT,
    LOG_OK,
    LOG_VIOLATION,
    MAYBE_SENSITIVE_FIELDS,
    POJO_CHANGED,
    POJO_COMMENTS,
    SENSITIVE_RESULTS,
    TOSTRING_MISSING,
    TOSTRING_SENSITIVE_VIOLATIONS,
    UNQUALIFIED_FIELDS,
    UNQUALIFIED_RESULTS,
    artifact,
    get_output_dir,
)
from common.pojo_config import is_pojo_file, load_pojo_config  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
POJO_CONFIG_FILE = SCRIPT_DIR / "rules" / "pojo.config"
RULES_DIR = SCRIPT_DIR / "rules"
CHECKSTYLE_JAR = SCRIPT_DIR / "checkstyle" / "checkstyle-13.7.0-all.jar"

SUMMARY_RE = re.compile(r"^SUMMARY:\s*(?P<body>.+)$", re.MULTILINE)

# 门禁可选计数项
GATE_KEYS = ("violation", "sensitive", "unqualified", "tostring", "tostring_sensitive", "analyze", "miss_comments", "low")


class Context:
    """流水线共享上下文。"""

    def __init__(self, root: Path, branch: str, out_dir: Path, verbose: bool,
                 changed_only: bool):
        self.root = root
        self.branch = branch
        self.out_dir = out_dir
        self.verbose = verbose
        self.changed_only = changed_only
        self.changed_java_manifest = out_dir / "changed-java.files"
        self.changed_pojo_manifest = out_dir / "changed-pojo.files"


class StepResult:
    """单步骤执行结果。"""

    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.error: str | None = None
        self.summary: dict[str, int] = {}

    def fail(self, message: str) -> "StepResult":
        self.ok = False
        self.error = message
        return self


def run_script(cmd: list[str], verbose: bool, description: str) -> tuple[int, str, str]:
    """运行子脚本，返回 (返回码, stdout, stderr)。"""
    if verbose:
        print(f"\n{'=' * 60}\n执行: {description}\n命令: {' '.join(cmd)}\n{'=' * 60}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return 2, "", f"找不到命令: {cmd[0]}"
    except Exception as exc:  # noqa: BLE001 - 编排层兜底
        return 2, "", str(exc)

    if verbose:
        if result.stdout:
            print(f"\n[标准输出]\n{result.stdout}")
        if result.stderr:
            print(f"\n[标准错误]\n{result.stderr}")
    return result.returncode, result.stdout, result.stderr


def parse_summary(stdout: str) -> dict[str, int]:
    """解析子脚本 stdout 中的 SUMMARY 行为键值计数。"""
    match = SUMMARY_RE.search(stdout)
    if not match:
        return {}
    counts: dict[str, int] = {}
    for token in match.group("body").split():
        if "=" in token:
            key, _, value = token.partition("=")
            if value.isdigit():
                counts[key] = int(value)
    return counts


def count_lines(file_path: Path, exclude_prefix: str | None = None) -> int:
    """统计产物文件非空行数，文件不存在返回 0。

    exclude_prefix: 排除以该前缀开头的行（如 analyze-sensitize.result 中
    7b LLM 语义复核追加的 [LLM] 行不计入门禁统计，仅供人工参考）。
    """
    if not file_path.is_file():
        return 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return sum(
                1 for line in f
                if line.strip() and not (exclude_prefix and line.startswith(exclude_prefix))
            )
    except OSError:
        return 0


def count_processed_files(ctx: Context) -> int:
    """估算已处理文件总数（失败率告警的分母）。

    changed-only 模式用变更清单行数；全量扫描统计根目录下 Java 文件数。
    """
    if ctx.changed_only:
        return count_lines(ctx.changed_java_manifest)
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(ctx.root):
        total += sum(1 for fn in filenames if fn.endswith(".java"))
    return total


def base_cmd(script_name: str, ctx: Context) -> list[str]:
    """构造子脚本公共命令前缀（脚本路径 + 输出目录）。"""
    return [sys.executable, str(SCRIPT_DIR / script_name), "-o", str(ctx.out_dir)]


# ---------------------------------------------------------------------------
# 步骤实现
# ---------------------------------------------------------------------------

def step1_scan_logs(ctx: Context) -> StepResult:
    result = StepResult("步骤1: 扫描日志输出")
    cmd = base_cmd("java_log_scanner.py", ctx) + [str(ctx.root)]
    if ctx.changed_only:
        cmd += ["--files", str(ctx.changed_java_manifest)]
    rc, _stdout, stderr = run_script(cmd, ctx.verbose, result.name)
    if rc != 0:
        return result.fail(f"日志扫描失败(rc={rc}): {stderr.strip()[:200]}")
    return result


def step2_check_log_print(ctx: Context) -> StepResult:
    result = StepResult("步骤2: 检查变更文件日志")
    cmd = base_cmd("check_log_print.py", ctx) + ["--repo", str(ctx.root), "-b", ctx.branch]
    if ctx.verbose:
        cmd.append("-v")
    rc, stdout, stderr = run_script(cmd, ctx.verbose, result.name)
    result.summary = parse_summary(stdout)
    if rc == 2:
        return result.fail(f"变更日志检查执行错误: {stderr.strip()[:200]}")
    return result


def step3_check_tostring(ctx: Context) -> StepResult:
    result = StepResult("步骤3: 检查@ToString注解")
    cmd = base_cmd("check_tostring_annotation.py", ctx) + [str(ctx.root)]
    if ctx.changed_only:
        cmd += ["--files", str(ctx.changed_pojo_manifest)]
    rc, _stdout, stderr = run_script(cmd, ctx.verbose, result.name)
    if rc != 0:
        return result.fail(f"@ToString检查失败(rc={rc}): {stderr.strip()[:200]}")
    result.summary = {
        "tostring": count_lines(artifact(ctx.out_dir, TOSTRING_MISSING)),
        "tostring_sensitive": count_lines(artifact(ctx.out_dir, TOSTRING_SENSITIVE_VIOLATIONS)),
    }
    return result


def step4_extract_fields(ctx: Context) -> StepResult:
    result = StepResult("步骤4: 提取Java字段")
    cmd = base_cmd("extract_java_fields.py", ctx) + [str(ctx.root)]
    if ctx.changed_only:
        cmd += ["--files", str(ctx.changed_pojo_manifest)]
    rc, _stdout, stderr = run_script(cmd, ctx.verbose, result.name)
    if rc != 0:
        return result.fail(f"字段提取失败(rc={rc}): {stderr.strip()[:200]}")
    return result


def step5_check_sensitive_word(ctx: Context) -> StepResult:
    result = StepResult("步骤5: 检查敏感词")
    cmd = base_cmd("check_sensitive_word.py", ctx) + ["--repo", str(ctx.root), "-b", ctx.branch]
    if ctx.verbose:
        cmd.append("-v")
    rc, stdout, stderr = run_script(cmd, ctx.verbose, result.name)
    result.summary = parse_summary(stdout)
    if rc == 2:
        return result.fail(f"敏感词检查执行错误: {stderr.strip()[:200]}")
    return result


def step6_check_pojo_comments(ctx: Context) -> StepResult:
    result = StepResult("步骤6: 检查POJO注释")
    cmd = base_cmd("check_pojo_comments.py", ctx) + ["--repo", str(ctx.root), "-b", ctx.branch]
    if ctx.verbose:
        cmd.append("-v")
    rc, stdout, stderr = run_script(cmd, ctx.verbose, result.name)
    result.summary = parse_summary(stdout)
    if rc == 2:
        return result.fail(f"POJO注释检查执行错误: {stderr.strip()[:200]}")
    return result


def step7_analyze_fields(ctx: Context) -> StepResult:
    result = StepResult("步骤7: 深度敏感性分析")
    cmd = base_cmd("analyze_sensitive_fields.py", ctx)
    if ctx.verbose:
        cmd.append("-v")
    rc, _stdout, stderr = run_script(cmd, ctx.verbose, result.name)
    if rc != 0:
        return result.fail(f"深度分析失败(rc={rc}): {stderr.strip()[:200]}")
    result.summary = {"analyze": count_lines(artifact(ctx.out_dir, ANALYZE_RESULT), exclude_prefix="[LLM]")}
    return result


# ---------------------------------------------------------------------------
# 并行链路（P1-6）
# ---------------------------------------------------------------------------

def group_a(ctx: Context) -> list[StepResult]:
    step1 = step1_scan_logs(ctx)
    if not step1.ok:
        return [step1]
    return [step1, step2_check_log_print(ctx)]


def group_b(ctx: Context) -> list[StepResult]:
    return [step3_check_tostring(ctx)]


def group_c(ctx: Context) -> list[StepResult]:
    step4 = step4_extract_fields(ctx)
    if not step4.ok:
        return [step4]
    return [step4, step5_check_sensitive_word(ctx)]


def group_d(ctx: Context) -> list[StepResult]:
    step6 = step6_check_pojo_comments(ctx)
    step7 = step7_analyze_fields(ctx)
    return [step6, step7]


def prepare_changed_manifests(ctx: Context) -> bool:
    """计算变更文件清单（--changed-only 模式），失败返回 False。"""
    try:
        changed = get_changed_files(ctx.root, ctx.branch)
    except Exception as exc:  # noqa: BLE001
        print(f"错误: 获取变更文件失败: {exc}", file=sys.stderr)
        return False

    java_files = [f for f in changed if f.endswith(".java")]
    directories, suffixes = load_pojo_config(POJO_CONFIG_FILE)
    pojo_files: list[str] = []
    for relative in java_files:
        file_path = (ctx.root / relative).resolve()
        if file_path.is_file() and is_pojo_file(file_path, ctx.root, directories, suffixes):
            pojo_files.append(str(file_path))

    abs_java = [str((ctx.root / f).resolve()) for f in java_files if (ctx.root / f).is_file()]
    ctx.changed_java_manifest.write_text(
        "\n".join(abs_java) + ("\n" if abs_java else ""), encoding="utf-8")
    ctx.changed_pojo_manifest.write_text(
        "\n".join(pojo_files) + ("\n" if pojo_files else ""), encoding="utf-8")
    print(f"变更文件: java={len(abs_java)} 个, 其中 POJO={len(pojo_files)} 个")
    return True


# ---------------------------------------------------------------------------
# HTML 报告（P1-5 escape + P2-7 details 折叠）
# ---------------------------------------------------------------------------

def read_artifact_lines(file_path: Path) -> list[str]:
    """读取产物文件全部非空行。"""
    if not file_path.is_file():
        return []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return [line.rstrip("\n") for line in f if line.strip()]
    except OSError:
        return []


def render_pre(lines: list[str], preview: int = 10) -> str:
    """渲染转义后的 <pre> 预览 + <details> 完整清单（P2-7）。"""
    if not lines:
        return ""
    escaped = [escape(line) for line in lines]
    head = "<pre>" + "\n".join(escaped[:preview]) + "</pre>"
    if len(lines) <= preview:
        return head
    full = "<pre>" + "\n".join(escaped) + "</pre>"
    return (head
            + f'<details><summary>查看全部 {len(lines)} 条</summary>{full}</details>')


def generate_html_report(ctx: Context, counts: dict[str, int],
                         gate_triggered: dict[str, int], duration: float,
                         failed_lines: list[str] | None = None) -> StepResult:
    result = StepResult("步骤8: 生成HTML报告")
    out = ctx.out_dir
    failed_lines = failed_lines or []

    data = {
        "log_ok": read_artifact_lines(artifact(out, LOG_OK)),
        "log_violation": read_artifact_lines(artifact(out, LOG_VIOLATION)),
        "tostring_missing": read_artifact_lines(artifact(out, TOSTRING_MISSING)),
        "tostring_sensitive_violations": read_artifact_lines(artifact(out, TOSTRING_SENSITIVE_VIOLATIONS)),
        "sensitive": read_artifact_lines(artifact(out, SENSITIVE_RESULTS)),
        "unqualified": read_artifact_lines(artifact(out, UNQUALIFIED_RESULTS)),
        "pojo_changed": read_artifact_lines(artifact(out, POJO_CHANGED)),
        "pojo_comments": read_artifact_lines(artifact(out, POJO_COMMENTS)),
        "analyze_result": read_artifact_lines(artifact(out, ANALYZE_RESULT)),
        "unqualified_fields": read_artifact_lines(artifact(out, UNQUALIFIED_FIELDS)),
        "maybe_sensitive_fields": read_artifact_lines(artifact(out, MAYBE_SENSITIVE_FIELDS)),
    }

    gate_text = "通过" if not gate_triggered else "触发: " + ", ".join(
        f"{key}={value}" for key, value in gate_triggered.items())
    scan_mode = "仅变更文件" if ctx.changed_only else "全量扫描"

    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>敏感信息日志审查报告</title>
    <style>
        body {{ font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
               line-height: 1.6; color: #333; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .container {{ background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,.1); }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 30px; }}
        .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 15px; }}
        .summary-item {{ background: #fff; padding: 15px; border-radius: 5px; text-align: center;
                         box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
        .summary-item .count {{ font-size: 32px; font-weight: bold; color: #3498db; }}
        .summary-item .label {{ font-size: 14px; color: #7f8c8d; }}
        .summary-item.warning .count {{ color: #e74c3c; }}
        .summary-item.success .count {{ color: #2ecc71; }}
        .meta {{ background: #ecf0f1; padding: 15px 20px; border-radius: 5px; margin: 20px 0; font-size: 14px; }}
        .meta code {{ background: #dfe6e9; padding: 1px 6px; border-radius: 3px; }}
        .section {{ margin: 30px 0; padding: 20px; background: #fafafa; border-radius: 5px; border-left: 4px solid #3498db; }}
        .section.warning {{ border-left-color: #e74c3c; }}
        pre {{ background: #2c3e50; color: #ecf0f1; padding: 15px; border-radius: 5px;
              overflow-x: auto; font-size: 13px; white-space: pre-wrap; word-break: break-all; }}
        details {{ margin-top: 8px; }}
        details summary {{ cursor: pointer; color: #3498db; }}
        .gate-pass {{ color: #2ecc71; font-weight: bold; }}
        .gate-fail {{ color: #e74c3c; font-weight: bold; }}
        .timestamp, .footer {{ color: #7f8c8d; font-size: 14px; }}
        .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>敏感信息日志审查报告</h1>
        <p class="timestamp">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>

        <div class="meta">
            <p>项目根目录: <code>{escape(str(ctx.root))}</code></p>
            <p>目标分支: <code>{escape(ctx.branch)}</code> ｜ 扫描模式: {scan_mode} ｜ 耗时: {duration:.1f}s</p>
            <p>产物目录: <code>{escape(str(out))}</code></p>
            <p>门禁结果: <span class="{'gate-fail' if gate_triggered else 'gate-pass'}">{escape(gate_text)}</span></p>
        </div>

        <div class="summary-grid">
            <div class="summary-item success"><div class="count">{len(data['log_ok'])}</div><div class="label">合规日志</div></div>
            <div class="summary-item warning"><div class="count">{counts.get('violation', 0)}</div><div class="label">违规日志(变更)</div></div>
            <div class="summary-item warning"><div class="count">{counts.get('tostring', 0)}</div><div class="label">@ToString缺失</div></div>
            <div class="summary-item warning"><div class="count">{len(data['tostring_sensitive_violations'])}</div><div class="label">@ToString敏感词</div></div>
            <div class="summary-item warning"><div class="count">{counts.get('sensitive', 0)}</div><div class="label">敏感字段(变更)</div></div>
            <div class="summary-item"><div class="count">{counts.get('unqualified', 0)}</div><div class="label">不规范字段(变更)</div></div>
            <div class="summary-item warning"><div class="count">{counts.get('analyze', 0)}</div><div class="label">深度分析命中</div></div>
            <div class="summary-item"><div class="count">{counts.get('low', 0)}</div><div class="label">低置信提示</div></div>
        </div>

        <div class="section warning">
            <h2>日志违规（变更文件）</h2>
            <p>变更文件中发现 <strong>{counts.get('violation', 0)}</strong> 条违规日志（清单全量 {len(data['log_violation'])} 条）。</p>
            {render_pre(data['log_violation'])}
        </div>

        <div class="section warning">
            <h2>@ToString 注解缺失</h2>
            <p>发现 <strong>{len(data['tostring_missing'])}</strong> 个类缺少 @ToString(of = {{...}}) 注解。</p>
            {render_pre(data['tostring_missing'])}
        </div>

        <div class="section warning">
            <h2>@ToString 敏感词违规</h2>
            <p>发现 <strong>{len(data['tostring_sensitive_violations'])}</strong> 个 @ToString(of = {{...}}) 注解中的字段命中敏感词，存在敏感数据泄露风险。</p>
            {render_pre(data['tostring_sensitive_violations'])}
        </div>

        <div class="section warning">
            <h2>敏感字段（变更文件）</h2>
            <p>变更文件中发现 <strong>{counts.get('sensitive', 0)}</strong> 个高置信敏感字段（含低置信共 {len(data['sensitive'])} 条；全量审计 {len(data['maybe_sensitive_fields'])} 条）。</p>
            {render_pre(data['sensitive'])}
        </div>

        <div class="section">
            <h2>字段命名规范</h2>
            <p>变更文件中发现 <strong>{counts.get('unqualified', 0)}</strong> 个不规范字段；全量审计 {len(data['unqualified_fields'])} 条。</p>
            {render_pre(data['unqualified'])}
        </div>

        <div class="section">
            <h2>POJO 字段注释检查</h2>
            <p>检查了 <strong>{len(data['pojo_changed'])}</strong> 个变更的 POJO 文件，发现 <strong>{counts.get('miss_comments', 0)}</strong> 处注释问题。</p>
            {render_pre(data['pojo_comments'])}
        </div>

        <div class="section warning">
            <h2>深度敏感性分析</h2>
            <p>基于 JR/T 0171-2020 敏感数据分类规则，发现 <strong>{counts.get('analyze', 0)}</strong> 个可能的敏感字段。</p>
            {render_pre(data['analyze_result'])}
        </div>

        <div class="section">
            <h2>失败文件</h2>
            <p>读取/解析失败的文件共 <strong>{len(failed_lines)}</strong> 个（不计入门禁统计，可用 --strict-mode 拦截）。</p>
            <details><summary>查看失败清单 {len(failed_lines)} 条</summary><pre>{escape(chr(10).join(failed_lines)) if failed_lines else '(无)'}</pre></details>
        </div>

        <div class="section">
            <h2>审查建议</h2>
            <ul>
                <li>检查并修复所有日志违规问题，避免输出敏感信息</li>
                <li>为 POJO 类添加 @ToString(of = {{...}}) 注解，控制 toString 输出字段</li>
                <li>确保敏感字段有完整的文档注释</li>
                <li>对敏感数据进行脱敏处理后再输出到日志</li>
            </ul>
        </div>

        <div class="footer">
            <p>报告由敏感信息日志审查工具生成 | 基于 JR/T 0171-2020 银行金融业数据安全标准</p>
        </div>
    </div>
</body>
</html>"""

    report_file = artifact(out, HTML_REPORT)
    try:
        report_file.write_text(html_content, encoding="utf-8")
    except OSError as exc:
        return result.fail(f"生成HTML报告失败: {exc}")
    print(f"HTML报告已生成: {report_file}")
    return result


def print_result_table(out_dir: Path) -> None:
    """打印产物文件清单。"""
    names = [
        ("日志合规清单", LOG_OK),
        ("日志违规清单", LOG_VIOLATION),
        ("@ToString缺失清单", TOSTRING_MISSING),
        ("@ToString敏感词违规", TOSTRING_SENSITIVE_VIOLATIONS),
        ("敏感字段清单", SENSITIVE_RESULTS),
        ("不规范字段清单", UNQUALIFIED_RESULTS),
        ("POJO变更清单", POJO_CHANGED),
        ("POJO注释问题", POJO_COMMENTS),
        ("深度分析结果", ANALYZE_RESULT),
        ("失败文件清单", FAILED_FILES_LIST),
        ("HTML报告", HTML_REPORT),
    ]
    print("\n" + "=" * 80)
    print("审查结果文件清单")
    print("=" * 80)
    for file_type, name in names:
        path = artifact(out_dir, name)
        status = "OK " if path.exists() else "缺失"
        print(f"{file_type:<22} | {status} | {path}")
    print("=" * 80)


# ---------------------------------------------------------------------------
# --doctor 环境诊断（只做检查不执行审查）
# ---------------------------------------------------------------------------

# 诊断涉及的 4 个规则文件与 7 个词典文件
DOCTOR_RULE_FILES = ("log-scanner.json", "pojo.config",
                     "sensitive-field-rules.json", "sensitive-data-classification.md")
DOCTOR_DICT_FILES = (CORE_FILE, EXTENDED_FILE, BLACKLIST_FILE, WHITELIST_FILE,
                     EN_US_DICT_FILE, EN_US_WHITELIST_FILE)


def _has_version_header(file_path: Path) -> bool:
    """检查文件头部（前 10 行）是否含 # version: 版本头。"""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for _ in range(10):
                line = f.readline()
                if not line:
                    break
                if line.lstrip().startswith("# version:"):
                    return True
    except OSError:
        pass
    return False


def _doctor_check_tool(record, name: str, version_args: list[str]) -> bool:
    """检查外部命令可用性与版本，返回是否可用。"""
    tool = shutil.which(name)
    if not tool:
        record("FAIL", f"{name} 可用性", f"PATH 中找不到 {name} 命令")
        return False
    try:
        r = subprocess.run([name, *version_args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        # java -version 输出在 stderr，git --version 输出在 stdout
        first_line = ((r.stdout or r.stderr).strip().splitlines() or [""])[0]
        # 退出码非 0 时命令实际不可用（如 JRE 损坏），不能仅凭有输出就记 OK
        if r.returncode != 0:
            record("FAIL", f"{name} 可用性",
                   f"{first_line or '命令退出码非 0'} (exit={r.returncode})")
            return False
        record("OK", f"{name} 可用性", first_line)
        return True
    except Exception as exc:  # noqa: BLE001 - 诊断层兜底
        record("FAIL", f"{name} 可用性", f"执行 {name} 版本检查失败: {exc}")
        return False


def run_doctor(args: argparse.Namespace) -> int:
    """逐项诊断运行环境，全部通过返回 0，任一失败返回 2（WARN 不计入失败）。"""
    checks: list[tuple[str, str, str]] = []

    def record(status: str, item: str, detail: str = "") -> None:
        checks.append((status, item, detail))

    # 1. Python 版本
    py_ok = sys.version_info >= (3, 10)
    record("OK" if py_ok else "FAIL", "Python 版本 >= 3.10",
           platform.python_version() + ("" if py_ok else "（请升级 Python）"))

    # 2. java 可用性（仅影响步骤6 POJO 注释检查）
    _doctor_check_tool(record, "java", ["-version"])

    # 3. git 可用性 + -r 目录是否为 git 仓库
    git_ok = _doctor_check_tool(record, "git", ["--version"])
    root = Path(args.root or ".").resolve()
    if not root.is_dir():
        record("FAIL", "-r 目录为 git 仓库", f"目录不存在: {root}")
    elif git_ok:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            record("OK", "-r 目录为 git 仓库", str(root))
        else:
            record("FAIL", "-r 目录为 git 仓库", f"{root}（git rev-parse 失败，参考 E001）")
    else:
        record("FAIL", "-r 目录为 git 仓库", "git 不可用，无法验证")

    # 4. checkstyle jar 存在性与 SHA256 校验
    if not CHECKSTYLE_JAR.is_file():
        record("FAIL", "checkstyle jar 存在", str(CHECKSTYLE_JAR))
    else:
        record("OK", "checkstyle jar 存在", CHECKSTYLE_JAR.name)
        baseline = CHECKSTYLE_JAR.with_suffix(CHECKSTYLE_JAR.suffix + ".sha256")
        if not baseline.is_file():
            record("WARN", "checkstyle jar SHA256 校验", f"基线文件缺失: {baseline}")
        else:
            # 基线文件存在但为空/仅空白时 split() 为空列表，需明确报 FAIL 而非抛 IndexError
            tokens = baseline.read_text(encoding="utf-8").split()
            if not tokens:
                record("FAIL", "checkstyle jar SHA256 校验",
                       f"基线文件为空: {baseline}（请写入形如 '<sha256>  <file>' 的内容）")
            else:
                expected = tokens[0].strip().lower()
                actual = hashlib.sha256(CHECKSTYLE_JAR.read_bytes()).hexdigest()
                if actual == expected:
                    record("OK", "checkstyle jar SHA256 校验", "与基线一致")
                else:
                    record("FAIL", "checkstyle jar SHA256 校验",
                           f"期望={expected} 实际={actual}（参考 E007）")

    # 5. 4 个规则文件存在且可解析
    for name in DOCTOR_RULE_FILES:
        rule_path = RULES_DIR / name
        if not rule_path.is_file():
            record("FAIL", f"规则文件 {name}", f"文件不存在: {rule_path}")
            continue
        try:
            if name.endswith(".json"):
                json.loads(rule_path.read_text(encoding="utf-8"))
            elif name == "pojo.config":
                load_pojo_config(rule_path)
            elif not rule_path.read_text(encoding="utf-8", errors="replace").strip():
                record("FAIL", f"规则文件 {name}", "文件内容为空")
                continue
            record("OK", f"规则文件 {name}", "存在且可解析")
        except json.JSONDecodeError as exc:
            record("FAIL", f"规则文件 {name}",
                   f"JSON 损坏: {exc.msg} (行 {exc.lineno} 列 {exc.colno})（参考 E004）")
        except Exception as exc:  # noqa: BLE001 - 诊断层兜底
            record("FAIL", f"规则文件 {name}", f"解析失败: {exc}")

    # 6. 7 个词典文件存在、非空、版本头（版本头缺失仅告警）
    for dict_path in DOCTOR_DICT_FILES:
        if not dict_path.is_file():
            record("FAIL", f"词典文件 {dict_path.name}", f"文件不存在: {dict_path}")
        elif dict_path.stat().st_size == 0:
            record("FAIL", f"词典文件 {dict_path.name}", "文件为空")
        elif not _has_version_header(dict_path):
            record("WARN", f"词典文件 {dict_path.name}",
                   "缺少 # version: 版本头（建议补充，见词典维护规范）")
        else:
            record("OK", f"词典文件 {dict_path.name}", "存在、非空、含版本头")

    # 7. 输出目录可写性
    try:
        out_dir = get_output_dir(args.output_dir, args.run_id)
        probe = out_dir / ".doctor-write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink(missing_ok=True)
        record("OK", "输出目录可写", str(out_dir))
    except ValueError as exc:
        record("FAIL", "输出目录可写", f"run-id 非法: {exc}（参考 E005）")
    except OSError as exc:
        record("FAIL", "输出目录可写", f"{exc}（参考 E006）")

    # 8. PYTHONIOENCODING（Windows 下非 utf-8 时提示，不计入失败）
    io_encoding = os.environ.get("PYTHONIOENCODING", "")
    if io_encoding.lower().replace("-", "") == "utf8":
        record("OK", "PYTHONIOENCODING", io_encoding)
    elif os.name == "nt":
        record("WARN", "PYTHONIOENCODING",
               f"当前值为 {io_encoding or '(未设置)'}，建议 PowerShell 执行 "
               "$env:PYTHONIOENCODING=\"utf-8\" 避免中文乱码")
    else:
        record("OK", "PYTHONIOENCODING", io_encoding or "(未设置，非 Windows 影响较小)")

    # 汇总输出（不用 emoji，纯文本标记）
    print("=" * 70)
    print("环境诊断（--doctor，只做检查不执行审查）")
    print("=" * 70)
    for status, item, detail in checks:
        mark = {"OK": "[OK]  ", "FAIL": "[FAIL]", "WARN": "[WARN]"}[status]
        print(f"{mark} {item}" + (f": {detail}" if detail else ""))
    fail_count = sum(1 for status, _, _ in checks if status == "FAIL")
    warn_count = sum(1 for status, _, _ in checks if status == "WARN")
    print("=" * 70)
    print(f"诊断完成: 共 {len(checks)} 项，失败 {fail_count} 项，警告 {warn_count} 项")
    return 2 if fail_count else 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="敏感信息日志审查工具 - 审查变更代码中可能的敏感信息泄露",
    )
    parser.add_argument("-b", "--branch", type=str, default="master",
                        help="目标分支名称（默认: master）")
    parser.add_argument("-r", "--root", type=str, default=".",
                        help="项目根目录 / git 仓库目录（默认: 当前目录）")
    parser.add_argument("-o", "--output-dir", type=str, default=None,
                        help="审查产物输出目录（默认: 环境变量 SLR_OUTPUT_DIR 或系统临时目录）")
    parser.add_argument("--run-id", type=str, default=None,
                        help="并行运行隔离子目录名（CI 场景）")
    parser.add_argument("--fail-on", type=str, default="violation,sensitive",
                        help="门禁计数项（逗号分隔，可选: " + ",".join(GATE_KEYS) + "；空串关闭门禁）")
    parser.add_argument("--changed-only", dest="changed_only", action="store_true", default=True,
                        help="仅扫描变更文件（默认开启）")
    parser.add_argument("--full-scan", dest="changed_only", action="store_false",
                        help="全量扫描（覆盖 --changed-only）")
    parser.add_argument("--strict-mode", action="store_true", default=False,
                        help="严格模式: 存在读取/解析失败文件（failedFiles > 0）时以退出码 2 结束（默认关闭，仅统计与展示）")
    parser.add_argument("--doctor", action="store_true", default=False,
                        help="环境诊断模式: 只检查运行环境不执行审查，全部通过退出码 0，任一失败退出码 2；与其他执行参数同时传入时 doctor 优先")
    parser.add_argument("-v", "--verbose", action="store_true", default=False,
                        help="启用详细输出模式")
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    started = time.monotonic()

    # --doctor 优先：只做环境诊断，不执行审查流水线
    if args.doctor:
        return run_doctor(args)

    root = resolve_repo(args.root)
    try:
        branch = validate_ref(args.branch)
    except ValueError as exc:
        # 结构化错误提示（E003），退出码契约保持 2 不变
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

    ctx = Context(root, branch, out_dir, args.verbose, args.changed_only)

    print("=" * 80)
    print("敏感信息日志审查工具")
    print("=" * 80)
    print(f"项目根目录: {root}")
    print(f"目标分支:   {branch}")
    print(f"产物目录:   {out_dir}")
    print(f"扫描模式:   {'仅变更文件' if ctx.changed_only else '全量扫描'}")
    print(f"门禁项:     {args.fail_on or '(已关闭)'}")
    print("=" * 80)

    # 变更清单准备（--changed-only）
    if ctx.changed_only and not prepare_changed_manifests(ctx):
        return 2

    # 清理上一轮遗留的失败记录分片，避免旧记录混入本次汇总
    clear_failure_shards(out_dir)

    # 四链路并行执行（P1-6）
    all_results: list[StepResult] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(group, ctx) for group in (group_a, group_b, group_c, group_d)]
        for future in futures:
            try:
                all_results.extend(future.result())
            except Exception as exc:  # noqa: BLE001 - 编排层兜底
                all_results.append(StepResult("并行链路").fail(f"未预期异常: {exc}"))

    for step in all_results:
        mark = "OK" if step.ok else "!!"
        print(f"[{mark}] {step.name}" + (f" - {step.error}" if step.error else ""))

    # 汇总计数
    by_name = {step.name: step for step in all_results}
    counts: dict[str, int] = {
        "violation": by_name.get("步骤2: 检查变更文件日志", StepResult("")).summary.get("violation", 0),
        "sensitive": by_name.get("步骤5: 检查敏感词", StepResult("")).summary.get("sensitive", 0),
        "unqualified": by_name.get("步骤5: 检查敏感词", StepResult("")).summary.get("unqualified", 0),
        "tostring": by_name.get("步骤3: 检查@ToString注解", StepResult("")).summary.get("tostring", 0),
        "tostring_sensitive": by_name.get("步骤3: 检查@ToString注解", StepResult("")).summary.get("tostring_sensitive", 0),
        "analyze": by_name.get("步骤7: 深度敏感性分析", StepResult("")).summary.get("analyze", 0),
        "miss_comments": by_name.get("步骤6: 检查POJO注释", StepResult("")).summary.get("miss_comments", 0),
        "low": (by_name.get("步骤2: 检查变更文件日志", StepResult("")).summary.get("low", 0)
                + by_name.get("步骤5: 检查敏感词", StepResult("")).summary.get("low", 0)),
    }

    # 关键步骤失败 -> 退出码 2（步骤3/6 仅警告）
    warn_only = {"步骤3: 检查@ToString注解", "步骤6: 检查POJO注释"}
    critical_errors = [s for s in all_results if not s.ok and s.name not in warn_only]

    # 门禁判定（P0-1）
    fail_on = {key.strip() for key in args.fail_on.split(",") if key.strip()}
    unknown_keys = fail_on - set(GATE_KEYS)
    if unknown_keys:
        print(f"错误: 未知门禁项: {','.join(sorted(unknown_keys))}（可选: {','.join(GATE_KEYS)}）",
              file=sys.stderr)
        return 2
    gate_triggered = {key: counts.get(key, 0) for key in fail_on if counts.get(key, 0) > 0}

    # 失败文件汇总（各步骤分片 -> failed-files.list，不进入 --fail-on 门禁口径）
    failed_lines = merge_failure_shards(out_dir)
    failed_count = len(failed_lines)

    duration = time.monotonic() - started

    # 步骤8: 报告（尽力而为）
    report = generate_html_report(ctx, counts, gate_triggered, duration, failed_lines)
    if not report.ok:
        critical_errors.append(report)

    print_result_table(out_dir)

    print(f"\n计数: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"failedFiles: {failed_count}")
    if failed_count > 0:
        total_processed = count_processed_files(ctx)
        # 失败文件数超过已处理文件总数 10% 时醒目告警
        if failed_count > total_processed * 0.1:
            print("!" * 80)
            print(f"警告: 失败文件数 {failed_count} 超过已处理文件总数 {total_processed} 的 10%，"
                  f"检查结果可能不完整，请排查 {artifact(out_dir, FAILED_FILES_LIST)}")
            print("!" * 80)
    print(f"总耗时: {duration:.1f}s")

    if critical_errors:
        print(f"\n执行错误: {'; '.join(s.name for s in critical_errors)}", file=sys.stderr)
        return 2
    # 严格模式：存在失败文件即视为执行错误（退出码 2），默认关闭时仅统计展示
    if args.strict_mode and failed_count > 0:
        print(f"\n严格模式: 存在 {failed_count} 个读取/解析失败文件:", file=sys.stderr)
        for line in failed_lines:
            print(f"  {line}", file=sys.stderr)
        return 2
    if gate_triggered:
        print(f"\n门禁触发: " + ", ".join(f"{k}={v}" for k, v in gate_triggered.items()))
        return 1
    print("\n审查完成: 门禁通过")
    return 0


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
