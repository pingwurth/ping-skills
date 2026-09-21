"""NEXT_STEP 协议: 负载构建、结构自检与 stdout 输出。

协议块是脚本与调用方(LLM/编排器)之间的唯一契约, Schema 以
protocol/next-step.schema.json 为准。运行期不依赖第三方 jsonschema,
仅在 emit 前做轻量结构自检(必填键 + 枚举值), 不符记 warning 不阻断。
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import config
from .logutil import module_logger
from .models import Decision

# 负载必填键与类型自检规则
_REQUIRED_KEYS = ("protocol_version", "script", "status", "exit_code",
                  "summary", "artifacts", "next_step")


def make_next_step(step_type: str, reason: str, **extra: Any) -> dict:
    """构建 next_step 对象; extra 按类型携带 script/params、instructions、
    question、deliverables 等。"""
    return {"type": step_type, "reason": reason, **extra}


def build_payload(script: str, status: str, exit_code: int, summary: str,
                  next_step: dict, artifacts: Optional[list] = None,
                  metrics: Optional[dict] = None) -> dict:
    """构建 NEXT_STEP 负载, 统一字段顺序与缺省值。"""
    payload: dict[str, Any] = {
        "protocol_version": config.NEXT_STEP_PROTOCOL_VERSION,
        "script": script,
        "status": status,
        "exit_code": exit_code,
        "summary": summary,
        "artifacts": artifacts or [],
    }
    if metrics:
        payload["metrics"] = metrics
    payload["next_step"] = next_step
    return payload


def payload_from_decision(script_file: str, decision: Decision, next_step: dict) -> dict:
    """由 Decision + 已解析的 next_step 组装协议负载。"""
    return build_payload(
        script=script_file,
        status=decision.status,
        exit_code=decision.exit_code,
        summary=decision.summary,
        next_step=next_step,
        artifacts=decision.artifacts,
        metrics=decision.metrics,
    )


def self_check(payload: dict) -> None:
    """轻量结构自检: 校验必填键与枚举取值, 不符仅告警(不抛异常)。"""
    log = module_logger()
    for key in _REQUIRED_KEYS:
        if key not in payload:
            log.warning(f"NEXT_STEP 负载缺少必填键: {key}")
    status = payload.get("status")
    if status is not None and status not in config.NEXT_STEP_STATUSES:
        log.warning(f"NEXT_STEP status 非法: {status}(允许 {config.NEXT_STEP_STATUSES})")
    next_step = payload.get("next_step") or {}
    step_type = next_step.get("type")
    if step_type not in config.NEXT_STEP_TYPES:
        log.warning(f"NEXT_STEP next_step.type 非法: {step_type}(允许 {config.NEXT_STEP_TYPES})")


def emit(payload: dict) -> None:
    """在 stdout 末尾输出标记包裹的协议 JSON 块; 输出后不应再打印任何内容。"""
    self_check(payload)
    try:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        module_logger().warning(f"NEXT_STEP 负载序列化失败: {exc}")
        # P3-5 修复：输出错误协议块而非静默返回，避免调用方无限等待
        script = payload.get("script", "<unknown>")
        error_payload = {
            "protocol_version": config.NEXT_STEP_PROTOCOL_VERSION,
            "script": script,
            "status": "failed",
            "exit_code": config.EXIT_ERROR,
            "summary": f"协议块序列化失败: {exc}",
            "artifacts": [],
            "next_step": {
                "type": "ask_user",
                "reason": f"脚本 {script} 序列化协议块时出错，请检查日志",
                "question": f"协议块序列化失败: {exc}\n是否继续?"
            }
        }
        try:
            body = json.dumps(error_payload, ensure_ascii=False, indent=2)
        except Exception:
            # 兜底：连错误协议都无法序列化，输出最小 JSON
            body = '{"status":"failed","exit_code":2,"summary":"protocol emit failed"}'
    print()
    print(config.NEXT_STEP_BEGIN_MARKER)
    print(body)
    print(config.NEXT_STEP_END_MARKER)
