#!/usr/bin/env python3
"""第一步(工作树选定后): 建立覆盖率基线并做门槛判定; 亦承担 §7 全量终验。

工作流位置:
    select_worktree 选定工作树后, 以 --project-root <worktree> 运行本脚本建立基线;
    未达标 -> make_plan 逐方法补测; 达标且测试全绿 -> finish。
    队列清空后由 make_plan 以 --final-check 回调本脚本做全量终验(SKILL.md §7)。

两种模式:
    基线模式(默认)      : 记录 git 基线(规则 d 豁免依据), fast-single-cov.sh 采集覆盖率, 首次生成方法表与状态。
    终验模式(--final-check): 延续既有进度, 仅刷新覆盖率/测试数据并复核完成条件;
                          连续 FINAL_CHECK_FAIL_STREAK_LIMIT 轮不绿且无待修复方法 ->
                          未达标收尾(环境不可信时为未复核收尾, 环境修复后可重跑复核)。

职责边界(本文件只做编排):
    目标定位 -> javasrc; 模块探测/构建/清理/覆盖率采集 -> maven.run_single_cov_with_fallback; 覆盖率解析 -> jacoco;
    测试结果解析 -> surefire; 门槛与升级判定 -> decisions.decide_after_init;
    路由 -> transitions; 协议输出 -> cli/protocol。

输入:
    --project-root(必填) --class(FQCN 或源文件路径, 必填) --method(可选限定单方法)
    --threshold(默认 80) --workdir --jacoco-version --final-check
    --coverage-exclude(可重复, fnmatch 模式, 排除目标类无需测试的内部类;
    终验未传时沿用 init 轮)。

输出(NEXT_STEP):
    达标 -> finish(exit 0); 未达标/测试不绿 -> run_script make_plan(exit 1);
    终验收敛 -> finish(未达标/未复核收尾, exit 0); mvn/报告/环境失败 -> ask_user(exit 2, failed)。
    产物: <workdir>/state.json 与 coverage.json; 过程日志 <workdir>/mvn.log。

关键约束:
    - 每次执行前清理旧 JaCoCo/Surefire 报告, 防止读取历史结果(SKILL.md §3)。
    - 测试是否通过以 surefire 为唯一依据(fail-closed), 不能只看 mvn 退出码。
    - state.json 原子写入, 支持断点续跑。
    - 分支名经 git rev-parse 查询落盘, 达标轮测试汇总落盘(finish 报告数据源)。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _path_setup  # noqa: F401  — 初始化 sys.path 以导入 jaut 包

from jaut import config, decisions, gitops, jacoco, javasrc, maven, report, surefire  # noqa: E402
from jaut.cli import EmitContext, StepError, run_cli  # noqa: E402
from jaut.logutil import setup_logger  # noqa: E402
from jaut.models import ClassCoverage, Decision, MethodCoverage, MethodStatus, State  # noqa: E402
from jaut.state import StateStore, default_workdir  # noqa: E402

SCRIPT_NAME = "init_coverage"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """注册命令行参数。

    --project-root / --class 必填; --method 限定单方法(method 模式);
    --threshold 覆盖率门槛; --final-check 切换为全量终验模式。
    """
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--class", dest="target_class", required=True,
                        help="目标类 FQCN 或源文件路径")
    parser.add_argument("--method", default=None, help="可选: 限定单个方法名")
    parser.add_argument("--threshold", type=float, default=config.DEFAULT_THRESHOLD)
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--jacoco-version", default=config.DEFAULT_JACOCO_VERSION)
    parser.add_argument("--coverage-exclude", dest="coverage_exclude", action="append",
                        default=None, metavar="PATTERN",
                        help="覆盖率排除模式(fnmatch, 可重复; 如 'com.foo.User$Builder' / "
                             "'*$MapperImpl'), 排除目标类无需测试的内部类(生成代码等), "
                             "不进方法表与类级统计; 命中目标类本身则报错; "
                             "终验未传时沿用 init 轮取值")
    parser.add_argument("--final-check", action="store_true",
                        help="全量终验模式(make_plan 队列清空后触发)")
    parser.add_argument("--override-threshold", action="store_true",
                        help="终验模式下仍使用 --threshold 而非沿用 init 时的门槛")


def _ensure_test_file_exists(project_root: Path, module: str, fqcn: str, logger) -> tuple[bool, Path]:
    """检查目标类的测试文件是否存在, 不存在则创建最小化桩测试类。

    桩测试类仅包含类声明(无测试方法), 确保:
        - mvn test-compile 不会因缺少测试源码而跳过
        - JaCoCo 能产出覆盖率报告(所有方法0%覆盖)
        - 后续 make_plan.py 能正常读取 state.json 并生成补测计划

    Args:
        project_root: 项目根目录。
        module: Maven 模块名('.' 表示单模块)。
        fqcn: 目标类全限定名。
        logger: 日志器。

    Returns:
        (bool, Path): (是否创建了桩测试文件, 桩测试文件路径)。
    """
    module_dir = project_root if module == "." else project_root / module
    test_relpath = decisions.test_class_relpath(fqcn)
    test_file = module_dir / test_relpath
    if test_file.is_file():
        return False, test_file

    # 生成最小化桩测试类(无测试方法, 仅保证编译通过)
    # 不导入任何 JUnit 版本, 避免与 JUnit 4/5 冲突
    pkg, _, simple = fqcn.rpartition(".")
    test_simple = simple + "Test"
    pkg_line = f"package {pkg};\n\n" if pkg else ""
    content = (
        f"{pkg_line}"
        f"public class {test_simple} {{\n"
        f"    // 桩测试占位: 确保 JaCoCo 能产出覆盖率报告\n"
        f"    // 后续由 make_plan.py 补充真实测试方法\n"
        f"}}\n"
    )
    test_file.parent.mkdir(parents=True, exist_ok=True)
    with open(test_file, "w", encoding="utf-8") as fp:
        fp.write(content)
    logger.info(f"未找到测试文件, 已创建桩测试类: {test_file}")
    return True, test_file


def handler(args: argparse.Namespace) -> tuple[Decision, EmitContext]:
    """建立/刷新覆盖率基线并做门槛判定, 产出下一步决策。

    步骤:
        1. 校验项目根, 解析 workdir 并建目录, 初始化文件日志;
        2. 解析目标类(FQCN/源文件路径)与所属 Maven 模块;
        3. 解析覆盖率排除模式(终验未传沿用 init 轮, 命中目标类则报错), 记录 git 基线;
        4. 清理旧报告 -> fast-single-cov.sh 采集覆盖率 -> 解析 jacoco 覆盖率(任一步失败即 StepError, 不改状态);
        5. 构建方法表, 组装并原子写入 state 与 coverage.json;
        6. 解析 surefire 结果, 计算 fail-closed 绿灯与达标判定;
        7. 更新 final_checked / final_check_fail_streak, 交 decisions 决策。

    Args:
        args: 已解析的命令行参数。

    Returns:
        (Decision, EmitContext): 决策(finish / make_plan)与路由上下文。

    Raises:
        StepError: 项目根缺失、目标类无法解析、mvn/报告/环境失败(exit 2)。
    """
    project_root = Path(args.project_root).resolve()
    if not project_root.is_dir():
        raise StepError.exec_error(f"项目根目录不存在: {project_root}")
    workdir = Path(args.workdir).resolve() if args.workdir else default_workdir(project_root)
    workdir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(SCRIPT_NAME, workdir)
    store = StateStore(workdir)

    fqcn, source = javasrc.resolve_target(project_root, args.target_class)
    if not fqcn:
        raise StepError.exec_error(f"无法解析目标类: {args.target_class}")
    # P2-6: 源文件前置校验 — 未找到时 fail-fast, 避免模块探测退化为根模块
    if source is None:
        diag = javasrc.diagnose_missing_source(project_root, args.target_class)
        raise StepError.exec_error(
            f"无法定位目标类 {fqcn} 的源文件(在 {project_root} 下未找到对应 .java 文件)",
            question=(
                f"源文件未找到，请检查:\n"
                f"  1. FQCN 拼写是否正确（包名 + 类名）\n"
                f"  2. 源文件是否在 src/main/java 目录下\n"
                f"  3. 如传入的是文件路径，请确认路径存在且为绝对路径或以项目根为基准\n"
                f"诊断: {diag}"
            ),
        )
    logger.info(f"目标类: {fqcn}, 源文件: {source}, 门槛: {args.threshold}, "
                f"final_check={args.final_check}")

    with store.locked():
        prev_state = store.load() if args.final_check else None
        # 排除模式: 显式传入优先; 终验未传时沿用 init 轮取值(与 threshold 同模式)
        if args.coverage_exclude is not None:
            coverage_excludes = [p.strip() for p in args.coverage_exclude if p.strip()]
        else:
            coverage_excludes = list(prev_state.coverage_excludes) if prev_state else []
        if jacoco.is_excluded(fqcn, coverage_excludes):
            raise StepError.state_error(
                f"排除模式命中目标类 {fqcn} 本身, 目标类不可被排除",
                question=f"请从 --coverage-exclude 中移除命中 {fqcn} 的模式")
        if coverage_excludes:
            logger.info(f"覆盖率排除模式: {coverage_excludes}")
        baseline = prev_state.git_baseline if prev_state else gitops.record_git_baseline(project_root)
        # 分支名是运行时事实(git rev-parse), 不可由目录名推导; 终验时优先取实时查询,
        # 查询失败(detached/无 git)回退 init 轮记录, 兼容旧 state.json
        branch = gitops.current_branch(project_root)
        if branch is None and prev_state is not None:
            branch = prev_state.worktree_branch
        module = maven.find_module_for_source(project_root, source) if source else "."
        mvn_log = str(workdir / config.MVN_LOG_FILENAME)
        install_log = str(workdir / "install.log")

        # 多模块项目: 先执行 mvn install -DskipTests 确保依赖模块已安装
        if maven.is_multi_module(project_root):
            logger.info("多模块项目, 先执行 mvn install -DskipTests 确保依赖模块已安装...")
            install_cmd = maven.build_install_cmd(project_root, module=module,
                                                  jacoco_version=args.jacoco_version,
                                                  state=prev_state)
            if install_cmd is None:
                raise StepError.exec_error("未找到 mvn 可执行程序", question="请安装 mvn 或加入 PATH")
            logger.info(f"执行依赖安装: {' '.join(install_cmd)}")
            install_result = maven.run_mvn(install_cmd, cwd=str(project_root), log_file=install_log)
            if not install_result.ok:
                raise StepError.exec_error("依赖模块安装失败, 详见 install.log",
                                           question=f"依赖模块安装失败, 详见 {install_log}",
                                           artifacts=[{"path": install_log, "kind": "mvn_log"}])
            logger.info("依赖模块安装完成")

        # 测试文件不存在时自动创建桩测试类, 确保 JaCoCo 能产出覆盖率报告
        stub_created, stub_file = _ensure_test_file_exists(project_root, module, fqcn, logger)

        try:
            # 清理旧报告 -> fast-single-cov.sh 采集覆盖率 -> 解析覆盖率(失败一律 StepError, 不改状态)
            maven.clean_jacoco_dirs(project_root, [module])
            uncleaned = maven.clean_surefire_dirs(project_root, [module])
            if uncleaned:
                logger.warning(f"surefire 目录清理失败, 本轮测试结果可能含陈旧数据: {uncleaned}")
            # 使用 fast-single-cov.sh 获取单类覆盖率(替代 mvn test jacoco:report)
            # init 阶段不传 no_am, 首次需要编译依赖模块
            test_simple = decisions.test_simple_name(fqcn)
            logger.info(f"执行覆盖率采集: module={module}, class={fqcn}, test={test_simple}")
            result, _fast_cov_log = maven.run_single_cov_with_fallback(
                project_root, module, fqcn, test_simple, mvn_log,
                jacoco_version=args.jacoco_version, state=prev_state)
            if not result.ok:
                raise StepError.exec_error("覆盖率采集失败, 详见 mvn.log",
                                           question=f"覆盖率采集失败, 详见 {mvn_log}",
                                           artifacts=[{"path": mvn_log, "kind": "mvn_log"}])

            xml_path, csv_path = jacoco.report_paths(project_root, module)
            if not xml_path.is_file():
                raise StepError.exec_error(
                    f"未找到覆盖率报告 {xml_path}(mvn 可能未真正成功), 详见 mvn.log",
                    question=f"未找到覆盖率报告 {xml_path}, 详见 {mvn_log}",
                    artifacts=[{"path": mvn_log, "kind": "mvn_log"}])

            parsed_all = jacoco.parse_jacoco_xml(xml_path, excludes=coverage_excludes)
            parsed = parsed_all.get(fqcn, [])
            if not parsed:
                raise StepError.exec_error(
                    f"jacoco.xml 中未找到目标类 {fqcn} 的覆盖率数据(检查类是否被编译/排除)",
                    question=f"jacoco.xml 中未找到目标类 {fqcn} 的覆盖率数据",
                    artifacts=[{"path": mvn_log, "kind": "mvn_log"}])
        except StepError:
            # 失败时回滚桩测试文件, 避免在用户项目中留下意外文件
            if stub_created and stub_file.is_file():
                stub_file.unlink(missing_ok=True)
                logger.info(f"覆盖率采集失败, 已回滚桩测试文件: {stub_file}")
            raise

        class_cov = jacoco.class_coverage(xml_path, csv_path, fqcn, parsed_xml=parsed_all,
                                          excludes=coverage_excludes)
        if args.final_check and not args.override_threshold:
            threshold = prev_state.threshold if prev_state else args.threshold
            if args.threshold != config.DEFAULT_THRESHOLD:
                logger.warning(
                    f"终验模式默认沿用 init 时的门槛({threshold}%), "
                    f"忽略 --threshold={args.threshold}; 如需覆盖请加 --override-threshold")
        else:
            threshold = args.threshold
        method_entries = _build_method_entries(parsed, args.method, prev_state, threshold,
                                               args.final_check)

        # P0-3: --method 拼写错误时所有方法被判 SKIPPED -> scope_met=True -> 零测试假 PASS;
        # 基线模式下强制校验 method_arg 至少命中一个 parsed 方法, 否则 fail-fast 并列出候选名
        if args.method and not args.final_check:
            available_names = sorted({m.key.name for m in parsed})
            if not any(m.key.name == args.method for m in parsed):
                raise StepError.state_error(
                    f"--method '{args.method}' 未匹配到目标类 {fqcn} 的任何方法",
                    question=(
                        f"方法名 '{args.method}' 在 {fqcn} 中未找到。\n"
                        f"候选方法名({len(available_names)} 个):\n"
                        + "\n".join(f"  - {n}" for n in available_names)
                        if available_names
                        else f"{fqcn} 在 jacoco.xml 中无任何方法记录(检查类是否被编译)"
                    ),
                )

        state = _assemble_state(args, project_root, workdir, fqcn, source, module,
                                mvn_log, baseline, threshold, class_cov, method_entries,
                                prev_state, branch, coverage_excludes)

        # Surefire 结果解析 + fail-closed 绿灯判定
        test = surefire.parse_surefire_reports(project_root, module)
        tests_green = test.is_green(bool(uncleaned))
        if not test.report_found:
            logger.warning("mvn 成功但未产出 surefire-reports, 本轮按测试失败计(fail-closed)")
        if test.parse_errors:
            logger.warning(f"{test.parse_errors} 个 surefire 报告无法解析, 按失败计(fail-closed)")
        for fc in test.failed_cases:
            logger.info(f"[TEST-FAIL] {fc.summary_line()}")
        if test.skipped > 0:
            logger.warning(
                f"[SKIPPED] 本轮有 {test.skipped} 个测试被跳过(可能使用了 @Disabled), "
                f"请确认是否有意为之; 被跳过的测试不计入 failures, 但可能导致覆盖率虚高")
        failing = state.pending_methods()
        method_mode = bool(state.target_method)
        # method 模式达标看目标方法本身(SKILL §7): 被跳过(未覆盖)的目标方法不是 pending,
        # 但绝不能算"范围已满足", 否则未覆盖的方法会被判成 PASS
        scope_met = not failing and (not method_mode or _target_method_done(state))
        class_met = class_cov.rate >= threshold
        met = decisions.init_is_met(scope_met, tests_green, class_met, method_mode)

        # 仅终验真正通过(含测试全绿)才持久化 final_checked, 防止未达标时 make_plan 谎报通过
        state.final_checked = bool(args.final_check) and met
        # 达标轮(init 即达标)与终验轮(含未达标收尾)的测试汇总落盘, finish 报告
        # "测试:"段数据源 —— 未达标终验也要如实显示本轮测试数字, 而非"未记录"
        if met or args.final_check:
            state.final_test_summary = {"tests": test.tests, "failures": test.failures,
                                        "errors": test.errors, "skipped": test.skipped}
        if args.final_check:
            if met:
                state.final_check_fail_streak = 0
            elif not tests_green:
                state.final_check_fail_streak += 1
            else:
                state.final_check_fail_streak = 0  # 测试全绿但覆盖率未达标: 重置 streak

        # 成功时清理桩测试文件, 避免污染用户项目
        if stub_created and stub_file.is_file():
            stub_file.unlink(missing_ok=True)
            logger.info(f"覆盖率采集成功, 已清理桩测试文件: {stub_file}")

        store.save(state)
        store.write_coverage({"target_class": fqcn, "class": class_cov.to_dict(),
                              "methods": [m.to_dict() for m in method_entries]})
    logger.info(f"类级覆盖率 {class_cov.rate:.2f}% (门槛 {threshold}%), "
                f"未达标方法 {len(failing)} 个, Tests run {test.tests}, "
                f"Failures {test.failures}, Errors {test.errors}")

    # 环境不可信(报告缺失/无法解析/目录清理失败, 无失败用例): 报告按"未复核"
    # 收尾, 不混同普通测试失败; 环境修复后重跑终验即可复核
    env_uncertain = decisions.env_uncertain(test, bool(uncleaned))
    ctx = decisions.InitContext(
        final_check=bool(args.final_check), method_mode=method_mode,
        class_rate=class_cov.rate, threshold=threshold, failing_count=len(failing),
        scope_met=scope_met, class_met=class_met, test=test, tests_green=tests_green,
        uncleaned=bool(uncleaned), final_check_fail_streak=state.final_check_fail_streak,
        coverage_path=str(store.coverage_path()), state_path=str(store.path),
        mvn_log=mvn_log, test_simple=state.test_class_simple or None,
        report=report.render_finish_report(
            state, met=met,
            note=_unmet_note(met, tests_green, test, len(failing),
                             class_cov.rate, threshold, state.target_method,
                             final_check=bool(args.final_check),
                             env_uncertain=env_uncertain),
            unverified=env_uncertain))
    decision = decisions.decide_after_init(ctx)
    decision = decisions.decide_after_init(ctx)
    return decision, EmitContext(workdir=workdir, state=state)


def _unmet_note(met: bool, tests_green: bool, test, failing_count: int,
                class_rate: float, threshold: float,
                target_method: str | None = None, *,
                final_check: bool = False, env_uncertain: bool = False) -> str:
    """未达标收尾报告的"说明"行(达标时为空串)。

    按成因区分: 环境不可信(未复核) / 测试不绿 / 仍有未达标方法 / method 模式
    目标方法已跳过 / 无补测方法(方法被跳过)导致类级不达标。
    "终验"措辞仅在终验模式(final_check)下使用, 基线轮测试未通过不得误述为终验。
    """
    if met:
        return ""
    if env_uncertain:
        return ("测试结果不可信(surefire 报告缺失/无法解析或目录清理失败), "
                "未能确认达标; 环境修复后可重新终验复核")
    if not tests_green:
        prefix = "终验测试未通过" if final_check else "测试未通过"
        return (f"{prefix}(Failures {test.failures}, Errors {test.errors}), "
                f"未达标方法 {failing_count} 个")
    if failing_count:
        return f"仍有 {failing_count} 个未达标方法"
    if target_method:
        return f"目标方法 {target_method} 已跳过(未达到门槛 {threshold}%), 无补测余地"
    return (f"无可补测方法(已完成或被跳过), 类级覆盖率 {class_rate:.2f}% "
            f"未达门槛 {threshold}%")


def _target_method_done(state: State) -> bool:
    """method 模式: 目标方法是否已达标(done)。

    目标方法被跳过/待测/不存在都算未达标 — 该模式下"范围已满足"必须由目标方法
    自身给出, 不能靠 pending 队列为空推断(SKILL §7)。

    `--method` 按方法名限定范围, 同名重载全部在范围内, 因此必须每个都 done 才算
    达标: 只取首个同名条目会让结果依赖 methods 顺序, 漏掉"一个重载 done、另一个
    重载被跳过"的假 PASS。
    """
    in_scope = [m for m in state.methods if m.key.name == state.target_method]
    return bool(in_scope) and all(m.status == MethodStatus.DONE for m in in_scope)


def _build_method_entries(parsed: list[MethodCoverage], method_arg: str | None,
                          prev_state: State | None, threshold: float,
                          final_check: bool) -> list[MethodCoverage]:
    """由解析出的方法覆盖率构建方法表(唯一键 name+desc)。

    状态判定(逐个方法):
        - 基线模式: --method 范围外 -> skipped; 抽象方法(covered+missed==0)或
          覆盖率 >= threshold -> done; 否则 -> pending。
        - 终验模式: 保留既有状态与轨迹, 仅刷新覆盖率; 覆盖率回落到门槛以下的
          done 方法打回 pending 并清空轨迹(升级判定只基于本次入队后的轮次)。

    Args:
        parsed: jacoco 解析出的目标类方法覆盖率列表。
        method_arg: --method 限定的方法名(None 表示不限定)。
        prev_state: 终验模式下的既有状态(基线模式为 None)。
        threshold: 覆盖率门槛。
        final_check: 是否终验模式。

    Returns:
        带状态的方法条目列表。
    """
    entries: list[MethodCoverage] = []
    for m in parsed:
        in_scope = not method_arg or m.key.name == method_arg
        rate = m.rate
        if final_check and prev_state is not None:
            prev = prev_state.find_method(m.key)
            entry = _carry_prev(prev, m)
            if not in_scope and entry.status != MethodStatus.DONE:
                entry.status = MethodStatus.SKIPPED
            elif entry.status != MethodStatus.DONE and (m.is_abstract or rate >= threshold):
                # 达标晋升: 清空历史跳过原因, 维持 "done => skip_reason 为空" 不变式
                # (报告/方法组复活/--unskip 都按该不变式判定方法是否被跳过)
                entry.status = MethodStatus.DONE
                entry.skip_reason = None
            elif entry.status == MethodStatus.DONE and rate < threshold and not m.is_abstract:
                # 打回 pending: 清除陈旧轨迹, 升级判定只基于本次入队后的轮次
                entry.status = MethodStatus.PENDING
                entry.reset_trajectory()
        else:
            if not in_scope:
                entry = MethodCoverage(key=m.key, covered=m.covered, missed=m.missed,
                                       status=MethodStatus.SKIPPED, initial_rate=rate)
            elif m.is_abstract or rate >= threshold:
                entry = MethodCoverage(key=m.key, covered=m.covered, missed=m.missed,
                                       status=MethodStatus.DONE, initial_rate=rate)
            else:
                entry = MethodCoverage(key=m.key, covered=m.covered, missed=m.missed,
                                       status=MethodStatus.PENDING, initial_rate=rate)
        entries.append(entry)
    return entries


def _carry_prev(prev: MethodCoverage | None, m: MethodCoverage) -> MethodCoverage:
    """终验时保留既有进度(状态/轨迹/基线快照), 仅刷新本轮覆盖率数据。"""
    if prev is None:
        return MethodCoverage(key=m.key, covered=m.covered, missed=m.missed,
                              status=MethodStatus.PENDING)
    entry = MethodCoverage(key=prev.key, covered=m.covered, missed=m.missed,
                           status=prev.status, round_rates=list(prev.round_rates),
                           round_test_results=list(prev.round_test_results),
                           initial_rate=prev.initial_rate,
                           skip_reason=prev.skip_reason)
    return entry


def _assemble_state(args, project_root, workdir, fqcn, source, module, mvn_log,
                    baseline, threshold, class_cov: ClassCoverage,
                    method_entries: list[MethodCoverage], prev_state: State | None,
                    branch: str | None, coverage_excludes: list[str]) -> State:
    """组装本轮 state(终验时延续既有进度与轨迹计数, 并记录分支名)。"""
    phase = "final_check" if args.final_check else "init"
    prev_cov_history = list(prev_state.coverage_history) if prev_state else []
    prev_test_history = list(prev_state.test_history) if prev_state else []
    state = State(
        project_root=str(project_root),
        workdir=str(workdir),
        threshold=threshold,
        target_class=fqcn,
        target_method=args.method or (prev_state.target_method if prev_state else None),
        source_file=str(source) if source else None,
        module=module,
        jacoco_version=args.jacoco_version,
        coverage_excludes=coverage_excludes,
        mvn_log=mvn_log,
        git_baseline=baseline,
        iteration=0,
        global_iteration=(prev_state.global_iteration if (args.final_check and prev_state) else 0),
        # 单方法窗口随新方法周期归零; 全局窗口与规范校验计数跨终验延续
        method_round_bonus=0,
        global_round_bonus=(prev_state.global_round_bonus
                            if (args.final_check and prev_state) else 0),
        validate_fail_streak=(prev_state.validate_fail_streak
                              if (args.final_check and prev_state) else 0),
        current_method=None,
        class_coverage=class_cov,
        methods=method_entries,
        coverage_history=prev_cov_history + [{"phase": phase,
                                              "class_rate": round(class_cov.rate, 2)}],
        test_history=prev_test_history,
        final_checked=False,
        final_check_fail_streak=(prev_state.final_check_fail_streak
                                 if (args.final_check and prev_state) else 0),
        worktree_branch=branch,
    )
    if args.final_check and prev_state is not None:
        state.test_class_file = prev_state.test_class_file
        state.test_class_simple = prev_state.test_class_simple
        state.plan = prev_state.plan
    return state


def main(argv: list[str] | None = None) -> int:
    """脚本入口: 委托 cli.run_cli 统一处理参数解析、异常兜底与协议输出。

    Args:
        argv: 命令行参数(默认取 sys.argv[1:]); 显式传入便于测试。

    Returns:
        进程退出码(取自 Decision.exit_code)。
    """
    return run_cli(SCRIPT_NAME, add_arguments, handler, argv)


if __name__ == "__main__":
    sys.exit(main())
