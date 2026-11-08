# -*- coding: utf-8 -*-
"""batch_diff._get_diff_files 的 -z numstat 解析回归测试(F11, P12 同款根修)。

旧解析以 --name-only 联结 numstat 第三列, 两种静默丢失:
  (a) 重命名的花括号形式 src/{old => new}.java 与 name-only 的新路径
      永不匹配 → +0/-0, 大类丢失 [大类] 标签与双倍探索预算;
  (b) CJK 路径 C 引用形("\\344\\270\\255...")再被 filepath.replace("\\\\","/")
      二次损坏 → 路径不存在, 接口/枚举与方法计数静默归零。
根修: 单条 `git diff --numstat -z` 输出 NUL 分隔的原始字段(重命名行为
`ins<TAB>del<TAB>` 空槽后随 \\0old\\0new), 无伪路径无 C 引用。
"""
from pathlib import Path
from types import SimpleNamespace

import batch_diff  # noqa: E402  — conftest 已把 scripts/ 放上 sys.path

_NUMSTAT_Z_OUT = (
    "10\t2\tsrc/main/java/com/foo/Bar.java\0"
    "200\t0\t\0src/main/java/com/foo/Old.java\0src/main/java/com/foo/New.java\0"
    "5\t5\tsrc/main/java/com/foo/中文.java\0"
    "-\t-\tdocs/binary.png\0"
)


def _install_fake_git(monkeypatch, numstat_stdout=_NUMSTAT_Z_OUT):
    calls = []

    def fake_run_command(cmd, cwd=None, capture=False, timeout=None, **kw):
        calls.append(cmd)
        if "merge-base" in cmd:
            return SimpleNamespace(ok=True, stdout="base123\n", stderr="")
        if "--numstat" in cmd:
            return SimpleNamespace(ok=True, stdout=numstat_stdout, stderr="")
        return SimpleNamespace(ok=True, stdout="", stderr="")

    import jaut.proc as proc
    monkeypatch.setattr(proc, "resolve_tool", lambda name: "git")
    monkeypatch.setattr(proc, "run_command", fake_run_command)
    return calls


def test_get_diff_files_numstat_z_rename_and_cjk(monkeypatch):
    """重命名取新路径并带上真实增删; CJK 路径原样到达; 二进制记 0/0。"""
    calls = _install_fake_git(monkeypatch)
    out = batch_diff._get_diff_files(Path("/w"), "main", "auto")

    by_path = {p: (a, d) for p, a, d in out}
    # 重命名: 新路径承载 200 增行(旧解析下恒 +0/-0)
    assert by_path["src/main/java/com/foo/New.java"] == (200, 0)
    assert "src/main/java/com/foo/Old.java" not in by_path
    # CJK 路径无 C 引用地原样输出(旧解析经 replace 后路径损坏)
    assert by_path["src/main/java/com/foo/中文.java"] == (5, 5)
    assert by_path["src/main/java/com/foo/Bar.java"] == (10, 2)
    # 二进制行 '-' 记 0/0 而非 ValueError 吞行
    assert by_path["docs/binary.png"] == (0, 0)


def test_get_diff_files_numstat_z_command_shape(monkeypatch):
    """命令形: 单条 numstat -z 且带 diff-filter(不再有第二条 name-only)。"""
    calls = _install_fake_git(monkeypatch)
    batch_diff._get_diff_files(Path("/w"), "main", "auto")
    diff_calls = [c for c in calls if "diff" in c]
    assert len(diff_calls) == 1
    assert "--numstat" in diff_calls[0]
    assert "-z" in diff_calls[0]
    assert "--diff-filter=ACMR" in diff_calls[0]
    assert "name-only" not in diff_calls[0]


def test_get_diff_files_empty_diff(monkeypatch):
    """空差异(stdout 全空)返回空列表, 不抛错。"""
    _install_fake_git(monkeypatch, numstat_stdout="")
    assert batch_diff._get_diff_files(Path("/w"), "main", "auto") == []
