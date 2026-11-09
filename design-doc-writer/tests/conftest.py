"""pytest 配置：scripts/ 入 sys.path；提供临时目标库与 dd.py 运行器。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DD = SCRIPTS_DIR / "dd.py"

# 章节标题单源派生自 dd.py MANIFEST_DEFAULTS（模板同文由 test_templates 双向锁），
# 不再手抄第三份副本
import dd as _dd  # noqa: E402

ANALYSIS_H2S = [t for d, _s, t, _e in _dd.MANIFEST_DEFAULTS if d == "analysis"]
DESIGN_H2S = [t for d, _s, t, _e in _dd.MANIFEST_DEFAULTS if d == "design"]


@pytest.fixture()
def repo(tmp_path):
    """临时目标库：带源码文件与 .gitignore（忽略 .agent）。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('x')\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agent\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def dd(repo):
    """子进程运行 dd.py（隔离 stdout/退出码/日志全局态），返回 (code, stdout)。"""
    def _run(*args, cwd=None):
        p = subprocess.run(
            [sys.executable, str(DD), *[str(a) for a in args]],
            cwd=str(cwd or repo), capture_output=True, text=True,
        )
        return p.returncode, p.stdout
    return _run


@pytest.fixture()
def booted(dd, repo):
    """init 完成（phase=intake），返回 repo。"""
    code, out = dd("init", "--feature", "order-export")
    assert code == 0, out
    return repo


@pytest.fixture()
def grilling(dd, booted):
    """到 grilling 阶段并加两问一依赖：Q001 无依赖、Q002 依赖 Q001。返回 repo。"""
    assert dd("phase", "analyzing")[0] == 0
    code, out = dd("q-add", "--dimension", "scope", "--title", "范围边界？",
                   "--body", "本期做哪些", "--recommendation", "推荐最小集")
    assert code == 0, out
    code, out = dd("q-add", "--dimension", "technical", "--title", "同步还是异步？",
                   "--body", "数据量大", "--recommendation", "推荐异步",
                   "--depends-on", "Q001")
    assert code == 0, out
    return booted


# ---------- 跨文件共享的低层工具 ----------


def read_ledger(repo: Path) -> dict:
    return json.loads(
        (repo / ".agent" / "design-doc-writer" / "ledger.json").read_text(encoding="utf-8"))


def write_ledger(repo: Path, data: dict) -> None:
    (repo / ".agent" / "design-doc-writer" / "ledger.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_doc(repo: Path, text: str, analysis: bool = True) -> Path:
    led = read_ledger(repo)
    rel = led["docs"]["analysis" if analysis else "design"]["path"]
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def make_analysis(status: str = "Draft", deferred: list[str] | None = None,
                  adr_refs: list[str] | None = None, extra: str = "") -> str:
    """一份能过门禁的需求分析文档（六节非空 + 状态行）。"""
    body = "\n\n".join(f"## {h}\n\n内容：{h}的实质内容。" for h in ANALYSIS_H2S)
    tail = ""
    if deferred:
        tail += "\n\n" + "\n".join(f"- [延期:{q}] 影响面说明" for q in deferred)
    if adr_refs:
        tail += "\n\n" + "\n".join(
            f"| {q} | 问题 | 决策 | 依据 | {a} |" for q, a in adr_refs)
    if extra:
        tail += "\n\n" + extra
    return f"# 特性 需求分析\n\n状态: {status}\n\n{body}{tail}\n"


def make_design(status: str = "Draft", extra: str = "") -> str:
    """一份能过门禁的详细设计文档（默认 shape=api：ui/cache/release 停用不校验）。"""
    body = "\n\n".join(f"## {h}\n\n内容：{h}的实质内容。" for h in DESIGN_H2S)
    mermaid = ("\n```mermaid\nflowchart TD\n    A[开始] --> B[结束]\n```\n")
    return f"# 特性 详细设计\n\n状态: {status}\n\n{body}\n{mermaid}\n{extra}"
