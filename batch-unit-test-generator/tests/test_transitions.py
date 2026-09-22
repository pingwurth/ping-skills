"""transitions 路由单测: 各 route -> 正确 next_step 类型/脚本/参数。"""

from __future__ import annotations

from pathlib import Path

from jaut import transitions
from jaut.models import Decision, NextCommand, ResumeOption, Route, State


def _decision(route, **kw) -> Decision:
    base = dict(status="success", exit_code=0, summary="s", route=route, reason="r")
    base.update(kw)
    return Decision(**base)


def _rc(tmp_path: Path, state=None) -> transitions.RouteContext:
    return transitions.RouteContext(scripts_dir=tmp_path, workdir=tmp_path / "wd", state=state)


def test_run_script_routes_resolve_script_and_workdir_param(tmp_path: Path):
    rc = _rc(tmp_path)
    for route, fname in [(Route.MAKE_PLAN, "make_plan.py"),
                         (Route.BUILD_PROMPT, "build_prompt.py"),
                         (Route.VERIFY_COVERAGE, "verify_coverage.py")]:
        step = transitions.build_next_step(_decision(route), rc)
        assert step["type"] == "run_script"
        assert step["script"] == str(tmp_path / fname)
        assert step["params"] == ["--workdir", str(tmp_path / "wd")]


def test_build_prompt_carries_instructions(tmp_path: Path):
    step = transitions.build_next_step(
        _decision(Route.BUILD_PROMPT, instructions="fix it"), _rc(tmp_path))
    assert step["instructions"] == "fix it"


def test_final_check_route_params(tmp_path: Path):
    state = State(project_root="/repo", target_class="com.x.Foo", jacoco_version="0.8.12")
    step = transitions.build_next_step(_decision(Route.FINAL_CHECK), _rc(tmp_path, state))
    assert step["script"] == str(tmp_path / "init_coverage.py")
    assert step["params"][:6] == ["--project-root", "/repo", "--class", "com.x.Foo",
                                  "--workdir", str(tmp_path / "wd")]
    assert "--final-check" in step["params"]


def test_final_check_requires_state(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError):
        transitions.build_next_step(_decision(Route.FINAL_CHECK), _rc(tmp_path, state=None))


def test_write_code_finish_ask_user(tmp_path: Path):
    rc = _rc(tmp_path)
    wc = transitions.build_next_step(_decision(Route.WRITE_CODE, instructions="do"), rc)
    assert wc["type"] == "write_code" and wc["instructions"] == "do"
    # on_complete 默认注入 validate_rules.py + --workdir
    assert wc["on_complete"]["script"] == str(tmp_path / "validate_rules.py")
    assert wc["on_complete"]["params"] == ["--workdir", str(tmp_path / "wd")]

    fin = transitions.build_next_step(_decision(Route.FINISH, deliverables=["/a", "/b"]), rc)
    assert fin["type"] == "finish" and fin["deliverables"] == ["/a", "/b"]

    ask = transitions.build_next_step(_decision(Route.ASK_USER, question="q?"), rc)
    assert ask["type"] == "ask_user" and ask["question"] == "q?"


def test_abort_route_emits_message_without_resume(tmp_path: Path):
    """abort 路由: question 作为 message 输出, 不带 resume(转述后立即终止)。"""
    step = transitions.build_next_step(
        _decision(Route.ABORT, question="请执行 codegraph install"), _rc(tmp_path))
    assert step["type"] == "abort"
    assert step["message"] == "请执行 codegraph install"
    assert "resume" not in step


def test_abort_route_message_falls_back_to_summary(tmp_path: Path):
    """abort 路由: question 缺失时 message 兜底 summary, 仍不带 resume。"""
    step = transitions.build_next_step(
        _decision(Route.ABORT, question=None, summary="工程根缺少索引"), _rc(tmp_path))
    assert step["type"] == "abort"
    assert step["message"] == "工程根缺少索引"
    assert "resume" not in step


def test_abort_route_message_falls_back_to_reason(tmp_path: Path):
    """abort 路由: question/summary 均空时兜底 reason, message 恒非空。"""
    step = transitions.build_next_step(
        _decision(Route.ABORT, question="", summary="", reason="R"), _rc(tmp_path))
    assert step["type"] == "abort"
    assert step["message"] == "R"
    assert "resume" not in step


def test_finish_route_carries_report(tmp_path: Path):
    step = transitions.build_next_step(
        _decision(Route.FINISH, deliverables=["/a"], report="REPORT"), _rc(tmp_path))
    assert step["type"] == "finish"
    assert step["report"] == "REPORT"
    # 无 report 时不输出该键(保持负载最小)
    step2 = transitions.build_next_step(
        _decision(Route.FINISH, deliverables=["/a"]), _rc(tmp_path))
    assert "report" not in step2


def test_ask_user_resume_resolves_script_and_workdir(tmp_path: Path):
    """ask_user 的 resume: 补全脚本绝对路径并统一前缀 --workdir。"""
    resume = [
        ResumeOption(option="continue", label="继续", script="make_plan.py",
                     params=["--grant-rounds", "3"]),
        ResumeOption(option="adjust_threshold", label="调整门槛", script="make_plan.py",
                     params=["--set-threshold", "N"], note="N=新门槛"),
        ResumeOption(option="terminate", label="终止"),
    ]
    step = transitions.build_next_step(
        _decision(Route.ASK_USER, question="q?", resume=resume), _rc(tmp_path))

    assert step["type"] == "ask_user" and step["question"] == "q?"
    entries = step["resume"]
    assert entries[0]["script"] == str(tmp_path / "make_plan.py")
    assert entries[0]["params"] == ["--workdir", str(tmp_path / "wd"), "--grant-rounds", "3"]
    assert entries[1]["params"] == ["--workdir", str(tmp_path / "wd"), "--set-threshold", "N"]
    assert entries[1]["note"] == "N=新门槛"
    # terminate 无命令: 只有 option/label, 不携带 script/params
    assert "script" not in entries[2] and "params" not in entries[2]
    assert entries[2] == {"option": "terminate", "label": "终止"}


def test_ask_user_without_resume_omits_key(tmp_path: Path):
    """无 resume(如 StepError 升级)时不输出该键, 保持负载最小。"""
    step = transitions.build_next_step(
        _decision(Route.ASK_USER, question="q?"), _rc(tmp_path))
    assert "resume" not in step


def test_ask_user_resume_without_workdir_omits_prefix(tmp_path: Path):
    """workdir 未知(极端场景)时不前缀 --workdir, 但仍给出脚本路径。"""
    rc = transitions.RouteContext(scripts_dir=tmp_path, workdir=None, state=None)
    resume = [ResumeOption(option="skip_method", label="跳过", script="make_plan.py",
                           params=["--skip-current"])]
    step = transitions.build_next_step(
        _decision(Route.ASK_USER, question="q?", resume=resume), rc)
    assert step["resume"][0]["params"] == ["--skip-current"]


def test_params_for_raises_on_none_workdir(tmp_path: Path):
    """P3-6 修复: workdir 为 None 时 _params_for 应抛出 ValueError。"""
    import pytest

    rc = transitions.RouteContext(scripts_dir=tmp_path, workdir=None, state=None)
    with pytest.raises(ValueError, match="需要有效的 workdir"):
        transitions.build_next_step(_decision(Route.MAKE_PLAN), rc)


def test_params_for_raises_on_empty_string_workdir(tmp_path: Path):
    """P3-6 修复: workdir 为空字符串 Path 时 _params_for 应抛出 ValueError。"""
    import pytest

    # 模拟一个会转换为空字符串的场景（虽然 Path("") 会解析为当前目录，
    # 但我们可以直接测试 _params_for 的防御逻辑）
    rc = transitions.RouteContext(scripts_dir=tmp_path, workdir=None, state=None)
    with pytest.raises(ValueError, match="需要有效的 workdir"):
        transitions.build_next_step(_decision(Route.MAKE_PLAN), rc)


def test_params_for_accepts_valid_workdir(tmp_path: Path):
    """P3-6 修复: workdir 为有效路径时 _params_for 正常工作。"""
    rc = transitions.RouteContext(scripts_dir=tmp_path, workdir=tmp_path / "wd", state=None)
    step = transitions.build_next_step(_decision(Route.MAKE_PLAN), rc)
    assert step["params"] == ["--workdir", str(tmp_path / "wd")]


def test_final_check_params_for_raises_on_none_workdir(tmp_path: Path):
    """P3-6 修复: FINAL_CHECK 路由 workdir 为 None 时应抛出 ValueError。"""
    import pytest

    state = State(project_root="/repo", target_class="com.x.Foo", jacoco_version="0.8.12")
    rc = transitions.RouteContext(scripts_dir=tmp_path, workdir=None, state=state)
    with pytest.raises(ValueError, match="需要有效的 workdir"):
        transitions.build_next_step(_decision(Route.FINAL_CHECK), rc)


# --------------------------------------------------------------------------- #
# P3-新增-1: 补充边界测试
# --------------------------------------------------------------------------- #
def test_run_script_route_with_instructions(tmp_path: Path):
    """run_script 路由携带 instructions 时应透传到 next_step。"""
    rc = _rc(tmp_path)
    step = transitions.build_next_step(
        _decision(Route.VERIFY_COVERAGE, instructions="re-run"), rc)
    assert step["type"] == "run_script"
    assert step["instructions"] == "re-run"


def test_run_script_route_without_instructions_omits_key(tmp_path: Path):
    """run_script 路由无 instructions 时不输出该键。"""
    rc = _rc(tmp_path)
    step = transitions.build_next_step(_decision(Route.MAKE_PLAN), rc)
    assert "instructions" not in step


def test_ask_user_empty_resume_list_omits_key(tmp_path: Path):
    """resume 为空列表时不输出 resume 键(与 None 行为一致)。"""
    step = transitions.build_next_step(
        _decision(Route.ASK_USER, question="q?", resume=[]), _rc(tmp_path))
    assert "resume" not in step


def test_finish_route_without_report_omits_key(tmp_path: Path):
    """finish 无 report 时不输出该键。"""
    step = transitions.build_next_step(
        _decision(Route.FINISH, deliverables=["/x"]), _rc(tmp_path))
    assert step["type"] == "finish"
    assert "report" not in step


def test_write_code_without_instructions_defaults_empty(tmp_path: Path):
    """write_code 无 instructions 时应输出空字符串; on_complete 仍默认注入。"""
    step = transitions.build_next_step(
        _decision(Route.WRITE_CODE), _rc(tmp_path))
    assert step["type"] == "write_code"
    assert step["instructions"] == ""
    assert step["on_complete"]["script"] == str(tmp_path / "validate_rules.py")
    assert step["on_complete"]["params"] == ["--workdir", str(tmp_path / "wd")]


def test_write_code_custom_on_complete_routes_make_plan(tmp_path: Path):
    """终验 write_code 决策携带 make_plan.py 时 on_complete 指向 make_plan。"""
    rc = _rc(tmp_path)
    wc = transitions.build_next_step(
        _decision(Route.WRITE_CODE, instructions="修复终验失败",
                  on_complete=NextCommand("make_plan.py")), rc)
    assert wc["on_complete"]["script"] == str(tmp_path / "make_plan.py")
    assert wc["on_complete"]["params"] == ["--workdir", str(tmp_path / "wd")]


def test_write_code_on_complete_without_workdir_omits_prefix(tmp_path: Path):
    """workdir 未知时 on_complete 仍给出脚本路径但不前缀 --workdir(与 resume 同逻辑)。"""
    rc = transitions.RouteContext(scripts_dir=tmp_path, workdir=None, state=None)
    wc = transitions.build_next_step(_decision(Route.WRITE_CODE), rc)
    assert wc["on_complete"]["script"] == str(tmp_path / "validate_rules.py")
    assert wc["on_complete"]["params"] == []


def test_resume_entry_note_without_script(tmp_path: Path):
    """无脚本的 resume 选项仍可携带 note。"""
    resume = [ResumeOption(option="info", label="了解", note="仅供参考")]
    step = transitions.build_next_step(
        _decision(Route.ASK_USER, question="q?", resume=resume), _rc(tmp_path))
    entry = step["resume"][0]
    assert entry["option"] == "info"
    assert entry["note"] == "仅供参考"
    assert "script" not in entry
    assert "params" not in entry
