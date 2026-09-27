#!/usr/bin/env python3
"""批量终验与最终报告(batch_finish 阶段)。

工作流位置:
    所有类 done/skipped/failed/unmet/unverified 后, 由主流程调用本脚本进行批量终验。
    一次全量 mvn(涉及模块) → 多模块聚合解析 → 对每个活跃类(done/recheck/unverified)
    刷新覆盖率与 surefire → 达标且测试绿 → 确认 done;
    有补测空间 → recheck 重入队; 否则 → unmet 未达标终态;
    环境不可信(surefire 报告缺失/无法解析/目录清理失败) → unverified
    待复核(非终态, 重跑本脚本再验)。
    全部类均为终态时跳过 mvn, 直接出最终报告。

流程:
    全部类均为终态(skipped/unmet/failed) → 不做无谓的全量 mvn, 直接出最终报告;
    否则: 清理旧报告 → 一次全量 mvn → 多模块聚合 jacoco/surefire
    (沿用各类 state.json 的 coverage_excludes, 与迭代期口径一致)
    → 对每个活跃类(done/recheck/unverified): 刷新类级覆盖率与测试,
      达标且测试绿 → 确认 done;
      仍有可补测方法且类级预算未耗尽 → 标记 recheck 重新入队(类 state.json 按
      final-check 语义刷新); 无补测空间(方法全部 done/skipped 或类级预算耗尽) →
      标记 unmet 未达标终态(不再重验, 防止零进展空转);
      环境不可信(surefire 报告缺失/无法解析/目录清理失败)且无补测空间 →
      标记 unverified 待复核(非终态: 环境修复后重跑本脚本重新终验);
      终态类(skipped/unmet/failed)不参与复核, 只进最终报告
    → 有 recheck → run_script batch_next; 全部确认 → finish 携带批量最终报告

输入:
    --project-root(必填) [--jacoco-version V](默认沿用活跃类 state 记录)
    [--skip-mvn](跳过 mvn, 用于 mock 测试)

输出(NEXT_STEP):
    有 recheck → run_script batch_next.py(重入队摘要);
    全部确认 → finish(批量最终报告, 含未达标清单, LLM 逐字转述)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _path_setup  # noqa: F401

from jaut import config, jacoco, maven, report, surefire, testcollect  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.lockutil import batch_lock  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import BatchState, Decision, MethodStatus, Route, State  # noqa: E402
from jaut.state import (StateStore, default_workdir, load_batch_state,  # noqa: E402
                        save_batch_state)

SCRIPT_NAME = "batch_finish"
# 终态: 不复核覆盖率/测试, 只进最终报告(skipped=跳过, unmet=未达标, failed=失败)
_TERMINAL_STATUSES = ("skipped", "unmet", "failed")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--jacoco-version", default=None,
                        help="终验挂载的 JaCoCo 版本(默认沿用活跃类 state.json"
                             " 记录的版本, 与基线一致)")
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
        batch_state = load_batch_state(batch_state_path)
        if batch_state is None:
            # is_file 检查已通过, 此处 None 即文件损坏(非法 JSON/结构)
            raise StepError.state_error(
                f"batch_state.json 已损坏(无法解析): {batch_state_path}",
                question="请检查文件是否被外部修改; 如需重新开始请删除该文件后"
                          "重跑 batch_init.py")

        # 终验对象: 非终态类(正常情况下只有 done/recheck/unverified)。
        # skipped/unmet/failed 为终态, 不参与复核 mvn 与覆盖率解析, 只进最终报告;
        # unverified(环境不可信待复核)为非终态, 重跑本脚本时会被重新终验。
        active_entries = [c for c in batch_state.classes
                          if c.status not in _TERMINAL_STATUSES]
        modules = sorted({c.module for c in active_entries}) or ["."]
        logger.info(f"批量终验: {len(active_entries)} 个活跃类, 模块 {modules}")

        # 无活跃类(全部 skipped/unmet/failed): 不做无谓的全量 mvn 与解析, 直接出最终报告
        if not active_entries:
            logger.info("无活跃类(全部 skipped/unmet/failed), 跳过 mvn 与覆盖率解析")
            return _finish_all(batch_state, batch_workdir, logger, [])

        # 类 state.json: 终验要沿用迭代期的覆盖率排除模式(init 写入的
        # coverage_excludes)与 JaCoCo 版本(batch_init --jacoco-version 写入),
        # 否则 Lombok/MapStruct 等排除配置被静默忽略终验覆盖率会回落,
        # 且基线与终验挂载不同版本插件
        class_states: dict[str, State | None] = {}
        for entry in batch_state.classes:
            if entry.status in _TERMINAL_STATUSES:
                continue
            class_states[entry.fqcn] = StateStore(Path(entry.workdir)).load()
        merged_excludes: list[str] = []
        for st in class_states.values():
            for pattern in (st.coverage_excludes if st is not None else []):
                if pattern not in merged_excludes:
                    merged_excludes.append(pattern)

        # 终验挂载版本: 显式 --jacoco-version 优先; 未传时沿用活跃类
        # state.json 记录的版本(旧行为硬编码默认值, 与基线不一致)
        if args.jacoco_version:
            jacoco_version = args.jacoco_version
        else:
            versions = {st.jacoco_version for st in class_states.values()
                        if st is not None and st.jacoco_version}
            jacoco_version = (next(iter(versions)) if versions
                              else config.DEFAULT_JACOCO_VERSION)
        logger.info(f"终验 JaCoCo 版本: {jacoco_version}")

        if not args.skip_mvn:
            # 清理旧报告 → 按需 mvn
            maven.clean_jacoco_dirs(project_root, modules)
            uncleaned = maven.clean_surefire_dirs(project_root, modules)
            if uncleaned:
                logger.warning(f"surefire 目录清理失败: {uncleaned}")

            # 收集测试类列表(按需执行): 无任何已存在测试类的活跃类创建桩
            # 测试类(与基线同语义), 保证 -Dtest 恒非空、终验能产出真实覆盖率
            # (否则子代理未写测试的类会因零测试无报告而判 unverified 空转)
            test_classes, stub_files = testcollect.collect_test_classes_with_stub(
                project_root, [(e.module, e.fqcn) for e in active_entries], logger)
            logger.info(f"涉及测试类: {test_classes}")

            mvn_log = str(batch_workdir / "batch-mvn.log")
            try:
                mvn_cmd = maven.build_batch_mvn_cmd(
                    project_root, modules=modules, test_classes=test_classes,
                    jacoco_version=jacoco_version)
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
            except StepError:
                # 失败回滚桩测试文件, 避免在用户项目中留下意外文件
                testcollect.remove_stub_files(stub_files, logger,
                                               reason="批量终验失败, 回滚桩测试")
                raise

            # 终验 mvn 完成, 清理桩测试文件(临时占位, 不留在用户项目)
            testcollect.remove_stub_files(stub_files, logger,
                                           reason="批量终验完成, 清理桩测试")
        else:
            uncleaned = []
            mvn_log = str(batch_workdir / "batch-mvn.log")

        # 多模块聚合解析
        parsed_all = jacoco.aggregate_jacoco_xml(project_root, modules,
                                                 excludes=merged_excludes)
        test = surefire.aggregate_surefire_reports(project_root, modules)
        tests_green = test.is_green(bool(uncleaned))

        # 对每个活跃类: 刷新覆盖率与测试(终态类只入报告)
        recheck_list: list[str] = []
        class_results: list[dict] = []

        for entry in batch_state.classes:
            if entry.status in _TERMINAL_STATUSES:
                class_results.append({"fqcn": entry.fqcn,
                                       "final_rate": entry.final_rate})
                continue

            class_state = class_states[entry.fqcn]
            class_excludes = (class_state.coverage_excludes
                              if class_state is not None else [])
            class_cov = jacoco.aggregate_class_coverage(
                project_root, modules, entry.fqcn,
                parsed_xml=parsed_all, excludes=class_excludes)
            final_rate = class_cov.rate

            # 按类判定自身是否达标: 覆盖率达标 + 结果可信 + (整批绿 或 失败用例均不在本类)。
            # tests_green/report_found 是整批聚合值, 别的类红灯不得把本类打成终态(unmet
            # 不可复活), 因此先按失败用例归属做归因。结果可信须覆盖 is_green 的全部
            # fail-closed 信号(报告缺失/无法解析/目录清理失败): 漏掉任一都会 fail-open。
            test_simple = (class_state.test_class_simple
                           if class_state is not None and class_state.test_class_simple
                           else f"{entry.simple_name}Test")
            # 失败归因须覆盖本类全部测试类命名变体: 终验 mvn 执行
            # testcollect.find_test_classes 收集的 Test/Tests/IT/TestCase/
            # Test 前缀 5 种命名变体, 只按 test_simple 匹配会把 FooTests/
            # FooIT 的失败误判为"其他类的失败", 无辜类被假标 done(达标)
            own_simples = {t.rsplit(".", 1)[-1]
                           for t in testcollect.find_test_classes(
                               project_root, entry.module, entry.fqcn)}
            own_simples.add(test_simple)
            own_failures = [fc for simple in sorted(own_simples)
                            for fc in test.failures_in_class(simple)]
            # 不可解析的报告不产出 failed_cases, 若漏判会被 (tests_green or
            # not own_failures) 当成"失败均在其他类"而假标 done; 故不可信信号与
            # TestResult.is_green 同口径: 报告缺失/无法解析/目录清理失败任一命中
            env_uncertain = ((not test.report_found) or bool(uncleaned)
                             or test.parse_errors > 0)
            own_ok = (final_rate >= batch_state.threshold and not env_uncertain
                      and (tests_green or not own_failures))

            class_workdir = Path(entry.workdir)
            class_store = StateStore(class_workdir)
            if class_state is not None:
                class_state.class_coverage = class_cov
                class_state.final_checked = own_ok
                if own_ok and tests_green:
                    class_state.final_test_summary = {
                        "tests": test.tests, "failures": test.failures,
                        "errors": test.errors, "skipped": test.skipped}

            # 判定: 本类达标 → 确认 done; 有补测空间 → recheck; 否则 → 未达标终态(unmet)
            if own_ok:
                entry.status = "done"
                entry.final_rate = final_rate
                if tests_green:
                    logger.info(f"类 {entry.fqcn} 终验通过: {final_rate:.1f}%")
                else:
                    logger.warning(
                        f"类 {entry.fqcn} 自身达标({final_rate:.1f}%), "
                        f"失败用例均在其他类: {'; '.join(test.fail_lines()) or '无'}")
            else:
                # 类 state.json 按 final-check 语义刷新: 打回覆盖率低于门槛的已完成方法;
                # 类级预算已耗尽时改跳剩余 pending(与 verify_coverage 预检查同语义);
                # 环境不可信(surefire 报告缺失/无法解析/目录清理失败)时方法状态一律不改, 留待重验
                budget_exhausted = (class_state is not None
                                    and class_state.class_budget_exhausted)
                if class_state is not None:
                    if budget_exhausted:
                        class_state.skip_pending_methods(
                            class_state.class_budget_skip_reason)
                    elif not env_uncertain:
                        for m in class_state.methods:
                            if (m.status == MethodStatus.DONE and not m.is_abstract
                                    and m.rate < batch_state.threshold):
                                m.status = MethodStatus.PENDING
                                m.reset_trajectory()

                reason_parts = []
                if final_rate < batch_state.threshold:
                    reason_parts.append(f"覆盖率 {final_rate:.1f}% < {batch_state.threshold}%")
                if env_uncertain:
                    reason_parts.append("测试结果不可信(surefire 报告缺失/无法解析或目录清理失败)")
                elif not tests_green:
                    scope = "本类" if own_failures else "其他类"
                    reason_parts.append(
                        f"{scope}测试不绿(F={test.failures}, E={test.errors})")
                reason = ", ".join(reason_parts)

                has_pending = (class_state is not None
                               and any(m.status == MethodStatus.PENDING
                                       for m in class_state.methods))
                if has_pending:
                    # 仍有可补测方法 → 重入队重验
                    entry.status = "recheck"
                    entry.final_rate = final_rate
                    logger.warning(f"类 {entry.fqcn} 终验未通过, 重入队重验: {reason}")
                    recheck_list.append(entry.fqcn)
                else:
                    # 无补测空间 → 终态化; 但环境不可信时改置 unverified 非终态:
                    # unmet 不可复活, 会把"环境修好后即可确认"的类永久记成未达标;
                    # unverified 不在 _TERMINAL_STATUSES, 重跑本脚本会重新终验
                    entry.final_rate = final_rate
                    if env_uncertain:
                        entry.status = "unverified"
                        entry.skip_reason = f"待环境复核: {reason}"
                        logger.warning(
                            f"类 {entry.fqcn} 终验环境不可信, 待复核"
                            f"(环境修复后重跑 batch_finish): {reason}")
                    else:
                        if budget_exhausted:
                            cause = "类级预算已耗尽"
                        else:
                            cause = "无可补测方法(方法均已跳过或完成)"
                        entry.status = "unmet"
                        entry.skip_reason = f"未达标终态: {reason}; {cause}"
                        logger.warning(
                            f"类 {entry.fqcn} 终验未通过且{cause}, 未达标收尾: {reason}")

            if class_state is not None:
                with class_store.locked():
                    class_store.save(class_state)

            class_results.append({"fqcn": entry.fqcn, "final_rate": final_rate,
                                   "test_green": own_ok})

        save_batch_state(batch_state_path, batch_state)

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
        return _finish_all(batch_state, batch_workdir, logger, class_results)


def _finish_all(batch_state: BatchState, batch_workdir: Path, logger,
                class_results: list[dict]) -> tuple[Decision, EmitContext]:
    """全部类已处理完毕 → finish 并携带批量最终报告。

    Args:
        batch_state: 批量总态(各类状态已定稿)。
        batch_workdir: 批量 workdir。
        logger: 文件日志器。
        class_results: 活跃类的终验结果(无活跃类时为空列表, 报告直接取各类 final_rate)。
    """
    finish_report = report.render_batch_finish_report(batch_state, class_results)
    done_count = sum(1 for c in batch_state.classes if c.status == "done")
    unmet_count = sum(1 for c in batch_state.classes if c.status == "unmet")
    skipped_count = sum(1 for c in batch_state.classes if c.status == "skipped")
    failed_count = sum(1 for c in batch_state.classes if c.status == "failed")
    unverified_count = sum(1 for c in batch_state.classes
                           if c.status == "unverified")
    summary = (f"批量终验完成: 达标 {done_count}, 未达标 {unmet_count}, "
               f"跳过 {skipped_count}, 失败 {failed_count}, "
               f"待环境复核 {unverified_count}")
    logger.info(summary)
    decision = Decision(
        status="success", exit_code=config.EXIT_OK,
        summary=summary,
        route=Route.FINISH, reason="批量终验完成, 输出最终报告",
        report=finish_report)
    return decision, EmitContext(workdir=batch_workdir,
                                  scripts_dir=Path(__file__).resolve().parent)


def main(argv: list[str] | None = None) -> int:
    return run_cli(SCRIPT_NAME, add_arguments, handler, argv)


if __name__ == "__main__":
    sys.exit(main())
