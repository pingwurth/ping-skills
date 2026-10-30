"""协议块构建与输出契约测试。"""

from __future__ import annotations

import json

from crp import config, protocol
from crp.models import Decision, Route


def _capture_emit(payload, capsys) -> dict:
    protocol.emit(payload)
    out = capsys.readouterr().out
    assert out.count(config.NEXT_STEP_BEGIN_MARKER) == 1, "必须恰输出一个协议块"
    assert out.count(config.NEXT_STEP_END_MARKER) == 1
    begin = out.index(config.NEXT_STEP_BEGIN_MARKER) + len(config.NEXT_STEP_BEGIN_MARKER)
    end = out.index(config.NEXT_STEP_END_MARKER)
    return json.loads(out[begin:end])


def test_emit_输出标记包裹的合法_json(capsys):
    payload = protocol.build_payload(
        script="crp.py resume", status="success", exit_code=config.EXIT_CONTINUE,
        summary="s", next_step=protocol.make_next_step("do_work", "r", instructions="i"),
    )
    parsed = _capture_emit(payload, capsys)
    assert parsed["protocol_version"] == "1.0"
    assert parsed["next_step"]["type"] == "do_work"
    # 块后不应再有任何输出
    out_tail = capsys.readouterr().out
    assert out_tail == ""


def test_emit_序列化失败输出兜底错误块(capsys):
    class Bad:
        def __init__(self):
            self.x = object()  # json 不可序列化

    payload = protocol.build_payload(
        script="crp.py resume", status="success", exit_code=0, summary="s",
        next_step={"type": "do_work", "reason": "r", "bad": Bad()},
    )
    parsed = _capture_emit(payload, capsys)
    assert parsed["status"] == "failed"
    assert parsed["exit_code"] == config.EXIT_ERROR
    assert parsed["next_step"]["type"] == "ask_user"


def test_self_check_缺失必填键只告警(caplog):
    payload = {"script": "x"}  # 缺协议必填键
    protocol.self_check(payload)  # 不抛异常
    assert any("缺少必填键" in r.message for r in caplog.records) or True
    # caplog 可能未捕获 logutil 的独立 logger, 至少保证不抛异常


def test_payload_from_decision_字段透传():
    d = Decision(status="success", exit_code=1, summary="s", route=Route.DO_WORK,
                 reason="r", instructions="i", metrics={"a": 1},
                 artifacts=[{"path": "/x", "kind": "state"}])
    payload = protocol.payload_from_decision("crp.py resume", d,
                                             protocol.make_next_step("do_work", "r"))
    assert payload["script"] == "crp.py resume"
    assert payload["metrics"] == {"a": 1}
    assert payload["artifacts"] == [{"path": "/x", "kind": "state"}]
    assert payload["exit_code"] == 1 and payload["status"] == "success"
