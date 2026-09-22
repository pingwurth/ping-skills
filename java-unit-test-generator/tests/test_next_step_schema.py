"""next-step.schema.json 结构契约: F5 finish→report 条件与 F3 WORK_DIR 描述。"""

from __future__ import annotations

import json
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "protocol" / "next-step.schema.json"
# java skill 的合法裸 finish 脚本排除表(见 SKILL.md §8)
_EXPECTED_EXCLUSIONS = ["select_worktree.py"]


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
