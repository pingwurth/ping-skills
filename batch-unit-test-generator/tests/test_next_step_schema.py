"""next-step.schema.json 结构契约: F5 finish→report 条件与 F3 WORK_DIR 描述。"""

from __future__ import annotations

import json
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "protocol" / "next-step.schema.json"
# java skill 的合法裸 finish 脚本排除表(见 SKILL.md §8)
_EXPECTED_EXCLUSIONS = ["select_worktree.py", "batch_diff.py", "batch_init.py"]


def _load() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def test_root_alof_finish_requires_report_outside_exclusions():
    schema = _load()
    root_alof = schema["allOf"]
    assert len(root_alof) == 1
    cond = root_alof[0]
    assert cond["if"]["properties"]["script"]["not"]["enum"] == _EXPECTED_EXCLUSIONS
    assert cond["if"]["properties"]["next_step"]["properties"]["type"]["const"] == "finish"
    assert cond["then"]["properties"]["next_step"]["required"] == ["report"]


def test_resume_params_description_mentions_work_dir_positional():
    schema = _load()
    params = schema["properties"]["next_step"]["properties"]["resume"]["items"][
        "properties"]["params"]
    assert "WORK_DIR" in params["description"]
    assert "select_worktree" in params["description"]


def test_report_description_documents_exclusions():
    schema = _load()
    report = schema["properties"]["next_step"]["properties"]["report"]
    assert "select_worktree" in report["description"]


def test_abort_type_and_message_contract():
    """abort 类型契约: type 枚举含 abort, abort 负载必须携带 message。"""
    import pytest
    schema = _load()
    type_enum = schema["properties"]["next_step"]["properties"]["type"]["enum"]
    assert "abort" in type_enum
    message = schema["properties"]["next_step"]["properties"]["message"]
    assert "终止" in message["description"]

    jsonschema = pytest.importorskip("jsonschema")
    base = {
        "protocol_version": "1.0",
        "script": "select_worktree.py",
        "status": "failed",
        "exit_code": 2,
        "summary": "codegraph MCP 未连接",
        "artifacts": [],
    }
    ok = dict(base)
    ok["next_step"] = {"type": "abort", "reason": "r", "message": "请执行 codegraph install"}
    jsonschema.validate(ok, schema)

    missing_message = dict(base)
    missing_message["next_step"] = {"type": "abort", "reason": "r"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_message, schema)


def test_abort_message_minlength_and_forbids_resume():
    """abort 契约: message 禁止空串(minLength>=1), 且不得携带 resume(含空数组)。"""
    schema = _load()
    message = schema["properties"]["next_step"]["properties"]["message"]
    assert message.get("minLength", 0) >= 1
    alts = schema["properties"]["next_step"]["allOf"]
    abort_conds = [c for c in alts
                   if c["if"]["properties"]["type"].get("const") == "abort"]
    assert len(abort_conds) == 1
    then = abort_conds[0]["then"]
    assert then["required"] == ["message"]
    assert then["not"]["required"] == ["resume"]


def test_abort_jsonschema_rejects_empty_message_and_resume():
    """若安装了 jsonschema: 空 message / abort 带 resume 均校验失败。"""
    import pytest
    jsonschema = pytest.importorskip("jsonschema")
    schema = _load()
    base = {
        "protocol_version": "1.0",
        "script": "select_worktree.py",
        "status": "failed",
        "exit_code": 2,
        "summary": "s",
        "artifacts": [],
    }

    ok = dict(base)
    ok["next_step"] = {"type": "abort", "reason": "r", "message": "请执行 codegraph install"}
    jsonschema.validate(ok, schema)

    empty_message = dict(base)
    empty_message["next_step"] = {"type": "abort", "reason": "r", "message": ""}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(empty_message, schema)

    with_resume = dict(base)
    with_resume["next_step"] = {"type": "abort", "reason": "r", "message": "m", "resume": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(with_resume, schema)


def test_optional_jsonschema_payload_validation():
    """若安装了 jsonschema, 用代表性 payload 验证条件分支。"""
    import pytest
    jsonschema = pytest.importorskip("jsonschema")
    schema = _load()

    bare_finish = {
        "protocol_version": "1.0",
        "script": "select_worktree.py",
        "status": "success",
        "exit_code": 0,
        "summary": "ok",
        "artifacts": [],
        "next_step": {"type": "finish", "reason": "done"},
    }
    jsonschema.validate(bare_finish, schema)

    missing_report = dict(bare_finish)
    missing_report["script"] = "make_plan.py"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing_report, schema)

    with_report = dict(missing_report)
    with_report["next_step"] = {"type": "finish", "reason": "done", "report": "…"}
    jsonschema.validate(with_report, schema)
