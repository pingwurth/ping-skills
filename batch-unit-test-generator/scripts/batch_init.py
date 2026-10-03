#!/usr/bin/env python3
"""批量基线(batch_init 阶段, 整个批次最重的一步, mvn 仅此一次)。

工作流位置:
    batch_diff 确认门禁通过后, 以 --project-root <worktree> 运行本脚本,
    一次 install + 一次全量 mvn 产出所有候选类的基线 state.json 与
    batch_state.json, 后续由 batch_next 逐类委派子代理。

流程:
    读取候选清单 → 记录 git 基线一次 → install 一次 → 清理旧报告
    → 全量 mvn 一次 → 多模块聚合 jacoco/surefire
    → 对每个确认类生成 state.json(batch_mode=True, 独立 workdir)
    → 基线后自动剔除无待补方法类 → 排序 → 生成 batch_state.json
    → 输出计划摘要 + 首个认领由 batch_next 承接

输入:
    --project-root(必填) --classes(all|top:N|FQCN,...) --threshold(默认 80)
    [--target] [--coverage-exclude PATTERN ...] [--jacoco-version V]

输出(NEXT_STEP):
    成功 → run_script batch_next.py(首个认领);
    mvn/环境失败 → ask_user(exit 2);
    无待补类 → finish。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import _path_setup  # noqa: F401

from jaut import config, decisions, gitops, jacoco, javasrc, maven, report, surefire, testcollect  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import (  # noqa: E402
    BatchClassEntry, BatchState, ClassCoverage, Decision, MethodCoverage,
    MethodStatus, Route, State,
)
from jaut.state import StateStore, default_workdir, save_batch_state  # noqa: E402

SCRIPT_NAME = "batch_init"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--classes", default="all",
                        help="范围: all | top:N | FQCN,FQCN,...")
    parser.add_argument("--threshold", type=float, default=config.DEFAULT_THRESHOLD)
    parser.add_argument("--target", default="auto")
    parser.add_argument("--coverage-exclude", dest="coverage_exclude",
                        action="append", default=None, metavar="PATTERN")
    parser.add_argument("--jacoco-version", default=config.DEFAULT_JACOCO_VERSION)
    parser.add_argument("--workdir", default=None)


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        raise StepError.exec_error(f"项目根目录不存在: {project_root}")

    batch_workdir = (Path(args.workdir).resolve() if args.workdir
                     else default_workdir(project_root))
    batch_workdir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(SCRIPT_NAME, batch_workdir)

    # 读取候选清单
    candidates_file = batch_workdir / "batch_candidates.json"
    if not candidates_file.is_file():
        raise StepError.state_error(
            f"候选清单不存在: {candidates_file}",
            question="请先运行 batch_diff.py 生成候选清单")
    with open(candidates_file, encoding="utf-8") as f:
        candidates_data = json.load(f)
    target_branch = candidates_data.get("target_branch", args.target)
    all_candidates = candidates_data.get("candidates", [])

    # 筛选范围
    selected = _select_classes(all_candidates, args.classes)
    if not selected:
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary="选定范围为空, 无需批量补测",
            route=Route.FINISH, reason="无候选类")
        return decision, EmitContext(workdir=batch_workdir)

    logger.info(f"批量基线: {len(selected)} 个类, 门槛 {args.threshold}%")

    # 批量状态防重
    batch_state_path = batch_workdir / config.BATCH_STATE_FILENAME
    if batch_state_path.is_file():
        raise StepError.state_error(
            f"batch_state.json 已存在: {batch_state_path}",
            question="续跑请使用 batch_next.py; 如需重新开始请先删除 batch_state.json")

    # 涉及模块集合
    modules = sorted({c["module"] for c in selected})
    logger.info(f"涉及模块: {modules}")

    # 记录 git 基线与 init 时已存在的主源码文件快照(共享给各类 state)
    baseline = gitops.record_git_baseline(project_root)
    main_source_files = gitops.record_git_main_source_files(project_root)
    branch = gitops.current_branch(project_root)

    # 覆盖率排除模式(显式 + Lombok 启发式)
    coverage_excludes = _build_coverage_excludes(selected, args, project_root)
    if coverage_excludes:
        logger.info(f"覆盖率排除模式: {coverage_excludes}")

    # 多模块 install 一次
    if maven.is_multi_module(project_root):
        install_log = str(batch_workdir / "install.log")
        install_cmd = maven.build_batch_install_cmd(
            project_root, modules=modules,
            jacoco_version=args.jacoco_version)
        if install_cmd is None:
            raise StepError.exec_error("未找到 mvn", question="请安装 mvn 或加入 PATH")
        logger.info(f"执行批量 install: {' '.join(install_cmd)}")
        install_result = maven.run_mvn(install_cmd, cwd=str(project_root),
                                        log_file=install_log)
        if not install_result.ok:
            raise StepError.exec_error(
                "依赖模块安装失败, 详见 install.log",
                question=f"依赖模块安装失败, 详见 {install_log}",
                artifacts=[{"path": install_log, "kind": "mvn_log"}])
        logger.info("依赖模块安装完成")

    # 清理旧报告 → 按需 mvn 一次
    maven.clean_jacoco_dirs(project_root, modules)
    uncleaned = maven.clean_surefire_dirs(project_root, modules)
    if uncleaned:
        logger.warning(f"surefire 目录清理失败: {uncleaned}")

    # 收集测试类列表(按需执行): 无任何已存在测试类的目标类创建桩测试类,
    # 保证 -Dtest 恒非空(空列表会退化为模块全量测试)且零测试模块也能产出报告
    test_classes, stub_files = testcollect.collect_test_classes_with_stub(
        project_root, [(c["module"], c["fqcn"]) for c in selected], logger)
    logger.info(f"涉及测试类: {test_classes}")

    mvn_log = str(batch_workdir / config.MVN_LOG_FILENAME)
    try:
        mvn_cmd = maven.build_batch_mvn_cmd(
            project_root, modules=modules, test_classes=test_classes,
            jacoco_version=args.jacoco_version)
        if mvn_cmd is None:
            raise StepError.exec_error("未找到 mvn", question="请安装 mvn 或加入 PATH")
        logger.info(f"执行按需 mvn: {' '.join(mvn_cmd)}")
        mvn_result = maven.run_mvn(mvn_cmd, cwd=str(project_root), log_file=mvn_log)
        if not mvn_result.ok:
            raise StepError.exec_error(
                "mvn 执行失败, 详见 mvn.log",
                question=f"mvn 执行失败, 详见 {mvn_log}",
                artifacts=[{"path": mvn_log, "kind": "mvn_log"}])

        # 多模块聚合解析
        parsed_all = jacoco.aggregate_jacoco_xml(project_root, modules,
                                                 excludes=coverage_excludes)
        if not parsed_all:
            # fail-closed: mvn "成功"但一个模块都没产出 jacoco 报告(典型:
            # 零测试模块无 surefire fork → 无 jacoco.exec → report 静默跳过)。
            # 旧行为会把全部类剔为"无覆盖率数据"后 exit 0 假 finish, 用户以为
            # 无事发生实际一个测试都没生成
            raise StepError.exec_error(
                "所有涉及模块均未产出 jacoco 覆盖率报告"
                "(可能没有任何测试被实际执行, 或全部类被排除模式命中)",
                question=f"未产出任何覆盖率报告, 详见 {mvn_log}",
                artifacts=[{"path": mvn_log, "kind": "mvn_log"}])
    except StepError:
        # 失败回滚桩测试文件, 避免在用户项目中留下意外文件
        testcollect.remove_stub_files(stub_files, logger,
                                       reason="批量基线失败, 回滚桩测试")
        raise

    # 基线成功, 清理桩测试文件(与单类模式一致; 真实测试由子代理补充)
    testcollect.remove_stub_files(stub_files, logger,
                                   reason="批量基线完成, 清理桩测试")
    test = surefire.aggregate_surefire_reports(project_root, modules)
    if not test.report_found:
        logger.warning("mvn 成功但未产出 surefire-reports(fail-closed)")
    if test.failures or test.errors:
        logger.warning(f"初始测试存在失败: Failures {test.failures}, Errors {test.errors}")
    for fc in test.failed_cases:
        logger.info(f"[TEST-FAIL] {fc.summary_line()}")

    initial_tests = {"tests": test.tests, "failures": test.failures,
                     "errors": test.errors}

    # 为每个确认类生成 state.json
    classes_subdir = batch_workdir / config.CLASSES_SUBDIR
    entries: list[BatchClassEntry] = []
    excluded_no_pending: list[str] = []

    for c in selected:
        fqcn = c["fqcn"]
        simple = fqcn.rsplit(".", 1)[-1]
        parsed = parsed_all.get(fqcn, [])
        if not parsed:
            logger.warning(f"jacoco.xml 中未找到 {fqcn} 的覆盖率数据, 跳过")
            excluded_no_pending.append(f"{fqcn}(无覆盖率数据)")
            continue

        # 类级覆盖率
        class_cov = jacoco.aggregate_class_coverage(
            project_root, modules, fqcn,
            parsed_xml=parsed_all, excludes=coverage_excludes)

        # 构建方法表
        method_entries = _build_method_entries(parsed, args.threshold)
        pending = [m for m in method_entries if m.status == MethodStatus.PENDING]
        if not pending:
            logger.info(f"类 {fqcn} 无待补方法(全 done/abstract), 剔除")
            excluded_no_pending.append(f"{fqcn}(无待补方法)")
            continue

        # 同名简单类冲突: simple -> FQCN(下划线) -> FQCN_2 -> FQCN_3 -> ...
        workdir_name = simple
        class_workdir = classes_subdir / workdir_name
        idx = 0
        while class_workdir.exists() and (class_workdir / config.STATE_FILENAME).exists():
            idx += 1
            if idx == 1:
                workdir_name = fqcn.replace(".", "_")
            else:
                workdir_name = f"{fqcn.replace('.', '_')}_{idx}"
            class_workdir = classes_subdir / workdir_name

        # 大类检测
        is_large_class = (len(pending) >= config.LARGE_CLASS_METHOD_THRESHOLD
                          or c.get("diff_add", 0) >= config.LARGE_CLASS_DIFF_THRESHOLD)

        class_workdir.mkdir(parents=True, exist_ok=True)
        class_mvn_log = str(class_workdir / config.MVN_LOG_FILENAME)

        # 组装 state.json
        state = State(
            project_root=str(project_root),
            workdir=str(class_workdir),
            threshold=args.threshold,
            target_class=fqcn,
            source_file=c.get("source_file", ""),
            module=c["module"],
            jacoco_version=args.jacoco_version,
            coverage_excludes=coverage_excludes,
            mvn_log=class_mvn_log,
            git_baseline=baseline,
            git_main_source_files=main_source_files,
            class_coverage=class_cov,
            methods=method_entries,
            coverage_history=[{"phase": "init", "class_rate": round(class_cov.rate, 2)}],
            worktree_branch=branch,
            batch_mode=True,
            exploration_mode=is_large_class,
        )
        store = StateStore(class_workdir)
        with store.locked():
            store.save(state)
            store.write_coverage({"target_class": fqcn, "class": class_cov.to_dict(),
                                   "methods": [m.to_dict() for m in method_entries]})

        entry = BatchClassEntry(
            fqcn=fqcn,
            simple_name=simple,
            source_file=c.get("source_file", ""),
            module=c["module"],
            workdir=str(class_workdir),
            status="pending",
            diff_add=c.get("diff_add", 0),
            diff_del=c.get("diff_del", 0),
            baseline_rate=class_cov.rate,
            has_existing_tests=c.get("has_existing_tests", False),
            method_count=len(pending),
        )

        # 大类拆分: pending 方法数超阈值时自动分组
        if len(pending) >= config.LARGE_CLASS_METHOD_THRESHOLD:
            pending_names = [m.key.name for m in pending]
            groups = _split_method_groups(pending_names, config.METHOD_GROUP_SIZE)
            entry.method_groups = groups
            logger.info(
                f"类 {fqcn}: 大类拆分 {len(pending)} 个方法 → {len(groups)} 组 "
                f"(每组≤{config.METHOD_GROUP_SIZE})")

        entries.append(entry)
        logger.info(f"类 {fqcn}: 基线 {class_cov.rate:.1f}%, 待补方法 {len(pending)} 个")

    if not entries:
        summary_parts = ["批量基线完成: 所有类均无待补方法"]
        if excluded_no_pending:
            summary_parts.append(f"剔除: {'; '.join(excluded_no_pending)}")
        decision = Decision(
            status="success", exit_code=config.EXIT_OK,
            summary="; ".join(summary_parts),
            route=Route.FINISH, reason="无待补类")
        return decision, EmitContext(workdir=batch_workdir)

    # 排序(默认 module-coverage: 模块为外键, 模块内基线覆盖率升序、并列差异行数降序)
    entries.sort(key=lambda e: (e.module, e.baseline_rate,
                                -(e.diff_add + e.diff_del)))

    # 生成 batch_state.json
    batch_state = BatchState(
        schema_version=1,
        project_root=str(project_root),
        target_branch=target_branch,
        diff_mode=candidates_data.get("diff_mode", "three"),
        threshold=args.threshold,
        sort_policy=candidates_data.get("sort_policy", "module-coverage"),
        created_at=datetime.now(timezone.utc).isoformat(),
        classes=entries,
        initial_tests=initial_tests,
    )
    save_batch_state(batch_state_path, batch_state)

    # 计划摘要
    pending_count = len(entries)
    rates = [e.baseline_rate for e in entries]
    avg_rate = sum(rates) / len(rates) if rates else 0.0
    summary = (f"批量基线完成: {pending_count} 个类待补, "
               f"平均基线覆盖率 {avg_rate:.1f}%, "
               f"初始测试 Tests {test.tests} Failures {test.failures} Errors {test.errors}")
    if excluded_no_pending:
        summary += f"\n剔除类: {'; '.join(excluded_no_pending)}"

    logger.info(summary)

    # 首个认领由 batch_next 承接
    next_script = str(Path(__file__).resolve().parent / "batch_next.py")
    decision = Decision(
        status="success", exit_code=config.EXIT_OK,
        summary=summary,
        route=Route.BATCH_NEXT, reason="批量基线完成, 进入认领委派",
        artifacts=[{"path": mvn_log, "kind": "mvn_log"},
                   {"path": str(batch_state_path), "kind": "batch_state"}])
    ectx = EmitContext(workdir=batch_workdir, state=None,
                       scripts_dir=Path(__file__).resolve().parent)
    return decision, ectx


def _select_classes(candidates: list[dict], selector: str) -> list[dict]:
    """按 --classes 参数筛选候选类(all/top:N/FQCN列表)。

    top:N 的 N 非正整数或 FQCN 拼错时报 state_error, 不静默空选
    (旧行为会退出 0 假 finish "选定范围为空", 误导用户与 LLM)。
    """
    if selector == "all":
        return list(candidates)
    if selector.startswith("top:"):
        try:
            n = int(selector[4:])
        except ValueError:
            raise StepError.state_error(f"--classes top:N 格式非法: {selector}")
        if n <= 0:
            raise StepError.state_error(
                f"--classes top:N 的 N 必须为正整数: {selector}")
        return candidates[:n]
    fqcn_list = [s.strip() for s in selector.split(",") if s.strip()]
    available = {c["fqcn"] for c in candidates}
    missing = [f for f in fqcn_list if f not in available]
    if missing:
        raise StepError.state_error(
            f"--classes 中的 FQCN 不在候选清单中: {', '.join(missing)}",
            question="请核对 FQCN 拼写。候选清单:\n"
                      + "\n".join(f"  - {c['fqcn']}" for c in candidates))
    return [c for c in candidates if c["fqcn"] in fqcn_list]


def _build_coverage_excludes(selected: list[dict], args, project_root: Path) -> list[str]:
    """覆盖率排除模式: 显式参数 + Lombok/MapStruct 启发式。

    启发式按候选类逐类生成精确字面量。旧版全局通配(*$Builder/*$MapperImpl)
    从未命中过真实生成类: Lombok @Builder 生成内部类 <FQCN>$<Simple>Builder
    (结尾是 SimpleBuilder, 不以 $Builder 结尾), MapStruct @Mapper 生成同包
    顶层类 <包>.<Simple>Impl(无 $) —— 两者均与通配形态不符; 反而会误伤手写
    内部类如 com.foo.Foo$Builder。

    @Value/@Data 不生成新类(仅变换原类字节码), 不再排除; 方法级 @Builder
    生成的内部类名与类级不同, 需 --coverage-exclude 显式排除(更稳妥的做法
    见 references/UnitTestRules.md: 配置 lombok.addLombokGeneratedAnnotation)。
    """
    excludes = []
    if args.coverage_exclude:
        excludes = [p.strip() for p in args.coverage_exclude if p.strip()]

    for c in selected:
        source_path = project_root / c.get("source_file", "")
        if not source_path.is_file():
            continue
        try:
            content = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fqcn = c["fqcn"]
        pkg, _, simple = fqcn.rpartition(".")
        # 命中多个注解时全部追加(旧版 break 会吞掉后续模式)
        generated = {
            # Lombok @Builder/@SuperBuilder 在类上生成内部类 <Simple>Builder;
            # @SuperBuilder 另有 <Simple>BuilderImpl
            "@Builder": (f"{fqcn}${simple}Builder",),
            "@SuperBuilder": (f"{fqcn}${simple}Builder",
                              f"{fqcn}${simple}BuilderImpl"),
            # MapStruct 生成同包顶层实现类(mapper 接口通常已被差异过滤剔除,
            # 类上 @Mapper 的场景在此覆盖)
            "@Mapper": (f"{pkg}.{simple}Impl" if pkg else f"{simple}Impl",),
        }
        for annotation, patterns in generated.items():
            if annotation in content:
                for pattern in patterns:
                    if pattern not in excludes:
                        excludes.append(pattern)
    return excludes


def _build_method_entries(parsed: list[MethodCoverage],
                          threshold: float) -> list[MethodCoverage]:
    """由解析出的方法覆盖率构建方法表(基线模式: 抽象/达标→done, 否则→pending)。"""
    entries: list[MethodCoverage] = []
    for m in parsed:
        if m.is_abstract or m.rate >= threshold:
            entry = MethodCoverage(key=m.key, covered=m.covered, missed=m.missed,
                                    status=MethodStatus.DONE, initial_rate=m.rate)
        else:
            entry = MethodCoverage(key=m.key, covered=m.covered, missed=m.missed,
                                    status=MethodStatus.PENDING, initial_rate=m.rate)
        entries.append(entry)
    return entries


def _split_method_groups(method_names: list[str], group_size: int) -> list[list[str]]:
    """将方法名列表按 group_size 切分为多个组。"""
    if group_size <= 0:
        raise ValueError(f"group_size must be positive, got {group_size}")
    return [method_names[i:i + group_size]
            for i in range(0, len(method_names), group_size)]


def main(argv: list[str] | None = None) -> int:
    return run_cli(SCRIPT_NAME, add_arguments, handler, argv)


if __name__ == "__main__":
    sys.exit(main())
