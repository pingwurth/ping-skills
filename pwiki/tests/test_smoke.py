"""P0 冒烟: 入口可跑、CLI 面完整、所有权表与解析器一致。"""

from __future__ import annotations

import pytest

from pwiki_lib import cli, config


def _run(argv, capsys):
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def test_version(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["--version"])
    assert ei.value.code == 0
    assert config.VERSION in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    code, out, _ = _run([], capsys)
    assert code == config.EXIT_OK
    assert "pwiki" in out


def test_all_owner_commands_registered():
    """所有权表的每个命令都在解析器里注册(qwiki 全 23 命令 + 新增)。"""
    out = cli.build_parser().format_help()
    for command in cli.COMMAND_OWNERS:
        assert command in out, f"命令 {command} 未注册"


def test_command_ownership_map_complete():
    """所有权表的每个 (module, function) 都真实存在(P0-P6 全落地的回归)。"""
    import importlib
    for command, (module_name, func_name) in cli.COMMAND_OWNERS.items():
        module = importlib.import_module(f"pwiki_lib.{module_name}")
        handler = getattr(module, func_name, None)
        assert callable(handler), f"{command} → {module_name}.{func_name} 缺失"


def test_common_flags_both_positions(capsys):
    """qwiki bug9 回归: 全局旗标在子命令前/后都生效(SUPPRESS 模式)。"""
    parser = cli.build_parser()
    a1 = parser.parse_args(["--repo", "/tmp/a", "scan"])
    a2 = parser.parse_args(["scan", "--repo", "/tmp/a"])
    assert a1.repo == "/tmp/a" and a2.repo == "/tmp/a"
    # 缺省时不出现属性(SUPPRESS), 由 Ctx getattr 兜底
    a3 = parser.parse_args(["scan"])
    assert not hasattr(a3, "repo")


def test_command_surface_covers_predecessors():
    """覆盖三前身的功能面(qwiki 23 命令 + repowiki 阶段 + wiki-plan-gen)。"""
    commands = set(cli.COMMAND_OWNERS)
    # qwiki 全部命令
    for cmd in ["init", "scan", "plan", "digest", "validate", "begin", "next",
                "submit", "fail", "skip", "regenerate", "pause", "resume",
                "cancel", "status", "finish", "update", "pages", "read",
                "write", "delete", "search", "export"]:
        assert cmd in commands, f"qwiki 命令 {cmd} 缺失"
    # repowiki 阶段落位(modules→detect_modules, plan --themes, validate --content)
    assert "modules" in commands
    # wiki-plan-gen
    assert "qoder-plan" in commands
    # 新增并发/和解
    assert "reconcile" in commands
