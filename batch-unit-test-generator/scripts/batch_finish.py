#!/usr/bin/env python3
"""批量终验与最终报告(batch_finish 阶段)。

工作流位置:
    所有类 done/skipped/failed 后, 由主流程调用本脚本进行批量终验。
    一次全量 mvn(涉及模块) → 多模块聚合解析 → 对每个非 skipped 类
    刷新覆盖率与 surefire → 达标且测试绿 → 确认 done; 否则 → recheck 重入队。

流程:
    清理旧报告 → 一次全量 mvn → 多模块聚合 jacoco/surefire
    → 对每个非 skipped 类: 刷新类级覆盖率与测试, 达标且测试绿 → 确认 done;
      覆盖率回落/测试不绿 → 标记 recheck 重新入队(类 state.json 按 final-check 语义刷新)
    → 有 recheck → run_script batch_next; 全部确认 → finish 携带批量最终报告

输入:
    --project-root(必填) [--skip-mvn](跳过 mvn, 用于 mock 测试)

输出(NEXT_STEP):
    有 recheck → run_script batch_next.py(重入队摘要);
    全部确认 → finish(批量最终报告, LLM 逐字转述)。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import _path_setup  # noqa: F401

from jaut import config, jacoco, maven, report, surefire  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.lockutil import batch_lock  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import BatchState, Decision, MethodStatus, Route  # noqa: E402
from jaut.state import StateStore, default_workdir  # noqa: E402

SCRIPT_NAME = "batch_finish"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--skip-mvn", action="store_true",
                        help="跳过 mvn 执行(用于测试)")
    parser.add_argument("--workdir", default=None)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    project_root = Path(args.project_root).resolve()
    batch_workdir = (Path(args.workdir).resolve() if args.workdir
                     else default_workdir(project_root))
    batch_workdir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(SCRIPT_NAME, batch_workdir)

    batch_state_path = batch_workdir / config.BATCH_STATE_FILENAME
    if not batch_state_path.is_file():
        raise StepError.state_error(
            f"batch_state.json 不存在: {batch_state_path}",
            question="请先运行 batch_init.py")

    with batch_lock(batch_state_path):
        with open(batch_state_path, encoding="utf-8") as f:
            batch_data = json.load(f)
        batch_state = BatchState.from_dict(batch_data)

        # 涉及模块集合(非 skipped 类)
        active_entries = [c for c in batch_state.classes
                          if c.status in ("done", "recheck")]
        modules = sorted({c.module for c in active_entries}) or ["."]
        logger.info(f"批量终验: {len(active_entries)} 个活跃类, 模块 {modules}")

        if not args.skip_mvn:
            # 清理旧报告 → 按需 mvn
            maven.clean_jacoco_dirs(project_root, modules)
            uncleaned = maven.clean_surefire_dirs(project_root, modules)
            if uncleaned:
                logger.warning(f"surefire 目录清理失败: {uncleaned}")

            # 收集测试类列表(按需执行)
            test_classes = _collect_test_classes(project_root, active_entries)
            logger.info(f"涉及测试类: {test_classes}")

            mvn_log = str(batch_workdir / "batch-mvn.log")
            mvn_cmd = maven.build_batch_mvn_cmd(
                project_root, modules=modules, test_classes=test_classes,
                jacoco_version=config.DEFAULT_JACOCO_VERSION)
            if mvn_cmd is None:
                raise StepError.exec_error("未找到 mvn", question="请安装 mvn")
            logger.info(f"执行按需终验 mvn: {' '.join(mvn_cmd)}")
            mvn_result = maven.run_mvn(mvn_cmd, cwd=str(project_root),
                                        log_file=mvn_log)
            if not mvn_result.ok:
                raise StepError.exec_error(
                    "mvn 执行失败, 详见 batch-mvn.log",
                    question=f"mvn 执行失败, 详见 {mvn_log}",
                    artifacts=[{"path": mvn_log, "kind": "mvn_log"}])
        else:
            uncleaned = []
            mvn_log = str(batch_workdir / "batch-mvn.log")

        # 多模块聚合解析
        parsed_all = jacoco.aggregate_jacoco_xml(project_root, modules)
        test = surefire.aggregate_surefire_reports(project_root, modules)
        tests_green = test.is_green(bool(uncleaned))

        # 对每个非 skipped 类: 刷新覆盖率与测试
        recheck_list: list[str] = []
        class_results: list[dict] = []

        for entry in batch_state.classes:
            if entry.status == "skipped":
                class_results.append({"fqcn": entry.fqcn,
                                       "final_rate": entry.final_rate})
                continue

            class_cov = jacoco.aggregate_class_coverage(
                project_root, modules, entry.fqcn,
                parsed_xml=parsed_all, excludes=[])
            final_rate = class_cov.rate

            # 刷新类 state.json
            class_workdir = Path(entry.workdir)
            class_store = StateStore(class_workdir)
            class_state = class_store.load()

            if class_state is not None:
                class_state.class_coverage = class_cov
                class_state.final_checked = True
                if tests_green:
                    class_state.final_test_summary = {
                        "tests": test.tests, "failures": test.failures,
                        "errors": test.errors, "skipped": test.skipped}
                with class_store.locked():
                    class_store.save(class_state)

            # 判定: 达标且测试绿 → 确认 done; 否则 → recheck
            met = (final_rate >= batch_state.threshold and tests_green
                   and test.report_found)
            if met:
                entry.status = "done"
                entry.final_rate = final_rate
                logger.info(f"类 {entry.fqcn} 终验通过: {final_rate:.1f}%")
            else:
                entry.status = "recheck"
                entry.final_rate = final_rate
                reason_parts = []
                if final_rate < batch_state.threshold:
                    reason_parts.append(f"覆盖率 {final_rate:.1f}% < {batch_state.threshold}%")
                if not tests_green:
                    reason_parts.append(f"测试不绿(F={test.failures}, E={test.errors})")
                logger.warning(f"类 {entry.fqcn} 终验未通过: {', '.join(reason_parts)}")
                recheck_list.append(entry.fqcn)

                # 打回 pending 方法(类 state.json 按 final-check 语义刷新)
                if class_state is not None:
                    for m in class_state.methods:
                        if (m.status == MethodStatus.DONE and not m.is_abstract
                                and m.rate < batch_state.threshold):
                            m.status = MethodStatus.PENDING
                            m.reset_trajectory()
                    class_state.final_checked = False
                    with class_store.locked():
                        class_store.save(class_state)

            class_results.append({"fqcn": entry.fqcn, "final_rate": final_rate,
                                   "test_green": met})

        _save_batch_state(batch_state_path, batch_state)

        if recheck_list:
            # 有 recheck → run_script batch_next
            summary = (f"批量终验完成: {len(recheck_list)} 个类需重验: "
                       f"{', '.join(recheck_list)}")
            logger.info(summary)
            decision = Decision(
                status="success", exit_code=config.EXIT_CONTINUE,
                summary=summary,
                route=Route.BATCH_NEXT, reason="有类需重验, 重新认领委派")
            return decision, EmitContext(workdir=batch_workdir,
                                          scripts_dir=Path(__file__).resolve().parent)

        # 全部确认 → finish 携带批量最终报告
        finish_report = report.render_batch_finish_report(batch_state, class_results)
        done_count = sum(1 for c in batch_state.classes if c.status == "done")
        skipped_count = sum(1 for c in batch_state.classes if c.status == "skipped")
        failed_count = sum(1 for c in batch_state.classes if c.status == "failed")
        summary = (f"批量终验完成: 达标 {done_count}, 跳过 {skipped_count}, "
                   f"失败 {failed_count}")
        logger.info(summary)
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary=summary,
            route=Route.FINISH, reason="批量终验全部通过, 任务完成",
            report=finish_report)
        return decision, EmitContext(workdir=batch_workdir,
                                      scripts_dir=Path(__file__).resolve().parent)


def _save_batch_state(path: Path, batch_state: BatchState) -> None:
    """原子写入 batch_state.json。"""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(batch_state.to_dict(), f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _find_test_classes(project_root: Path, module: str, target_class: str) -> list[str]:
    """查找目标类对应的测试类(按常见命名规则搜索)。

    搜索策略:
       1. {SimpleClassName}Test.java
    2. {SimpleClassName}Tests.java
    3. {SimpleClassName}IT.java (集成测试)
    """
    simple_name = target_class.rsplit(".", 1)[-1] if "." in target_class else target_class
    test_dir = project_root / module / "src" / "test" / "java"
    if not test_dir.is_dir():
        return []

    # 计算包路径
    package_path = target_class.rsplit(".", 1)[0].replace(".", "/") if "." in target_class else ""
    search_dir = test_dir / package_path if package_path else test_dir

    test_classes = []
    for suffix in ["Test", "Tests", "IT"]:
        test_file = search_dir / f"{simple_name}{suffix}.java"
        if test_file.is_file():
            # 转换为 FQCN
            test_fqcn = f"{target_class.rsplit('.', 1)[0]}.{simple_name}{suffix}" if "." in target_class else f"{simple_name}{suffix}"
            test_classes.append(test_fqcn)

    return test_classes


def _collect_test_classes(project_root: Path, entries: list) -> list[str]:
    """收集所有活跃类对应的测试类列表。"""
    all_test_classes = []
    for entry in entries:
        test_classes = _find_test_classes(project_root, entry.module, entry.fqcn)
        all_test_classes.extend(test_classes)
    return list(set(all_test_classes))  # 去重


def main(argv: list[str] | None = None) -> int:
    return run_cli(SCRIPT_NAME, add_arguments, handler, argv)


if __name__ == "__main__":
    sys.exit(main())
