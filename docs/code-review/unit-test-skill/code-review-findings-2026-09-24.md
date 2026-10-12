# 代码审查问题记录（2026-09-24）

对工作区未提交改动（java/batch unit-test skill 的预算自动跳过改造）的 `/code-review` 结果，共 7 条发现，按严重程度排序。

修复进度：全部 7 条已修复（2026-09-24，含回归测试）。

## 1. own_ok 失败归属只匹配 `{Simple}Test`，产生假 PASS【已修复】

修复：失败归因改为覆盖本类全部测试类命名变体（`_find_test_classes` 收集的 Test/Tests/IT 简名集合 ∪ `test_class_simple`），见 `batch_finish.py` 的 `own_simples`；回归测试 `test_handler_unmet_when_own_tests_fail_with_nonstandard_name`。

- 位置：`batch-unit-test-generator/scripts/batch_finish.py:163`
- 类型：正确性
- 问题：`own_ok` 的失败归属只按 `{Simple}Test` 匹配，类自身非标准命名的测试类（如 `FooTests`）的失败会被误判为"其他类的失败"，导致类被标 `done`（达标）的假 PASS。
- 场景：类 `com.pkg.FooService` 已有既有测试类 `FooTests.java`（含失败用例）。终验 mvn 由 `_collect_test_classes` 收集 `Test`/`Tests`/`IT` 三个后缀，因此 `FooTests` 会被执行并出现在 `test.failed_cases` 里（`class_name=com.pkg.FooTests`）；但 `test_simple = class_state.test_class_simple`（由 make_plan 固定为 `FooServiceTest`）→ `test.failures_in_class('FooServiceTest')` 返回空 → `own_failures` 为空 → `own_ok = 覆盖率达标 and not env_uncertain` → `entry.status='done'`，最终报告把该类列入"达标"清单。日志还同时打印"失败用例均在其他类"与本类失败清单（185 行），自相矛盾。
- 修复方向：失败归属应按 `_collect_test_classes` 收集的全部测试类后缀（或本类对应的全部测试类名）匹配，而非仅 `test_class_simple`。

## 2. env_uncertain 直接置 unmet 终态，重跑也不复核【已修复】

修复：新增 `unverified` 非终态（不在 `_TERMINAL_STATUSES`）——环境不可信且无补测空间时置该状态并进"待环境复核"报告分节；环境修复后重跑 `batch_finish.py` 会重新终验（达标则确认 done）。同步更新 `models.py` 的 `_BATCH_CLASS_STATUSES`/`all_done` 文档、`report.py` 的 `render_batch_finish_report`、SKILL.md §0.2 第 7 步/§6/§7；回归测试 `test_handler_unverified_when_environment_uncertain_without_pending`、`test_handler_unverified_class_is_rechecked_after_env_fixed`、`test_render_batch_finish_report_unverified`。

- 位置：`batch-unit-test-generator/scripts/batch_finish.py:231`
- 类型：正确性
- 问题：环境不可信（`env_uncertain`）且类内无 pending 时直接把类置为 `unmet` 终态，环境修好后重跑 `batch_finish.py` 也不会再复核，该类被永久记成未达标。
- 场景：终验 mvn 期间 surefire 目录清理失败（IDE/进程占用）或报告缺失 → `uncleaned`/`report_found` 触发 `env_uncertain` → 类内方法都已 done（无 pending）→ 走 else 分支置 `entry.status='unmet'`，`skip_reason='...测试结果不可信...; 环境问题导致无法确认测试结果'`。用户关闭占用进程后再次执行 batch_finish：`_TERMINAL_STATUSES`（skipped/unmet/failed）已把该类排除出 `active_entries`，无活跃/部分活跃时都不再复核它，最终报告永久把可能已达标（覆盖率达标）的类列为"未达标"，且没有任何脚本参数能把它复位（仅 `batch_next --reset-claim` 处理 in_progress）。
- 修复方向：`env_uncertain` 应是可复核的暂态而非终态——例如单独的 `unverified` 状态，或 batch_finish 对 `unmet` + `skip_reason` 含"环境问题"的类允许重跑复核 / 提供 `--recheck` 参数。

## 3. 类级预算耗尽后剩余方法组仍逐组委派，零工作白耗子代理【已修复】

修复：`batch_update.py` 新增 `_remaining_groups_have_work()`——类级预算耗尽直接判无工作（复活的方法会被 verify_coverage 预检查立即再跳过）；否则看剩余组是否有 PENDING 或仅组过滤跳过的 SKIPPED（无 `skip_reason`，make_plan 会复活）。无工作时不再推进组索引，直接落账 `done` 进终验。`--unskip` 追加预算后 `class_budget_exhausted` 自然转假，仍可续跑。同步 `references/delegation.md` §4.3；回归测试见 `tests/test_batch.py` 的 `_remaining_groups_have_work` 系列与两个 handler 级测试。

- 位置：`batch-unit-test-generator/scripts/batch_update.py:129`
- 类型：效率
- 问题：类级预算耗尽后 pending 已被全部跳过，但方法组机制仍逐组认领，每个剩余组白白消耗一次完整子代理委派。
- 场景：大类拆成 6 组，类级预算在第 3 组耗尽：verify_coverage 预检查把全部 pending（跨所有组）置 skipped → make_plan 队列空立即 FINISH 类内完成报告 → batch_update 看到 pending 为空、`has_method_groups` 为真 → `advance_group()` 返回还有后续组 → `entry.status='pending'` → batch_next 再委派组 4/5/6。而 `make_plan._apply_method_group` 只复活 `skip_reason` 为空的方法，这些带"类级预算耗尽"原因的方法不会被复活，于是每个剩余组都是一次零工作的子代理启动（make_plan 立刻 finish），只是为了让组索引走完。
- 修复方向：batch_update 检测到"pending 全空且全部因类级预算耗尽被跳过"时应直接终结该类（走 unmet/skip_all_pending 路径），不再推进组索引。

## 4. met=False 收尾报告的测试计数永远显示"未记录"【已修复】

修复：两个 skill 的 `init_coverage.py` 把 `final_test_summary` 落盘条件从 `if met` 改为 `if met or args.final_check`——终验轮无论达标与否都写盘，未达标收尾报告如实显示本轮测试数字；两侧 `protocol/state.schema.json` 的字段描述同步更新。回归测试 `test_handler_unmet_final_check_persists_test_summary`（两个 skill 各一份）。

- 位置：`java-unit-test-generator/scripts/init_coverage.py:316`
- 类型：正确性
- 问题：未达标收尾报告里测试计数永远显示"未记录"：`final_test_summary` 只在 `met=True` 时写盘，而新增的 `met=False` 路径同样渲染该字段。
- 场景：终验有方法被跳过/覆盖率不达标 → `met=False` → `state.final_test_summary` 保持 None（且在本次 `_assemble_state` 中不会从 prev_state 继承），但紧接着 `report.render_finish_report(state, met=False, note=...)` 用同一 state 渲染。报告"测试:"段固定输出 `Tests run: 未记录 / Failures: 未记录 / Errors: 未记录`，而同一次的 note 来自刚解析的 surefire（"说明: 终验测试未通过(Failures 3, Errors 1)"）。用户拿到的未达标收尾报告不含任何测试数字。
- 修复方向：`final_test_summary` 在 met=False 时也写盘（终验的 surefire 数据两种结果下都已解析，只是没存）。
- 备注：两个 skill 的引擎已分叉（见根 CLAUDE.md），batch 侧同类逻辑需对照检查。

## 5. _infer_escalation_reason 复刻 decisions 判定，且轮次上限不含 bonus【已修复】

修复：`_infer_escalation_reason` 改为复用 `jaut/decisions.py` 的 `test_failure_streak` / `no_improvement` 原语（消除逐行复刻），方法轮次上限改用 `METHOD_ROUND_BUDGET + method_round_bonus`（与 `decide_after_verify` 同口径）；回归测试 `test_infer_escalation_reason_method_round_budget_includes_bonus`。

- 位置：`batch-unit-test-generator/scripts/batch_update.py:244`
- 类型：代码质量 / 口径不一致
- 问题：`_infer_escalation_reason` 复刻了 `decisions.test_failure_streak` / `no_improvement` 的判定，且方法轮次上限仍用不含 bonus 的常量，与真实跳过口径不一致。
- 场景：① 用户执行 `make_plan.py --unskip doStuff --grant-rounds 8` 后，单方法真实上限是 `METHOD_ROUND_BUDGET + method_round_bonus = 16` 轮，但该函数用 `len(method.round_rates) >= 8` 判定并输出"达单方法上限 8 轮"，中断原因转述给用户的数字是错的（同一函数里类级预算已改用有效的 `class_round_budget`，口径不一致）；② 第 3/4 条判定与 `jaut/decisions.py` 的 `test_failure_streak` / `no_improvement` 逐行重复，任一侧调整阈值语义（如 `NO_IMPROVEMENT_NEAR_THRESHOLD_FACTOR`）都会漏改另一侧。
- 修复方向：让 `_infer_escalation_reason` 复用 decisions 模块的判定函数；方法轮次上限用 `METHOD_ROUND_BUDGET + method_round_bonus`。

## 6. escalations 无递增路径，"总升级次数"恒为 0（死状态）【已修复】

修复：采用"计数"方案——`batch_update` 落账时把类内自动跳过（带 `skip_reason` 的终态跳过，组过滤跳过不计）的方法数写入 `entry.escalations`（历史字段名保留，state 兼容），报告行改为"总自动跳过方法数"（原"总升级次数"）；`models.py` 字段注释与 SKILL.md §7 报告内容清单同步。回归测试 `test_batch_update_counts_auto_skipped_methods`、`test_render_batch_finish_report`（标签断言更新）。

- 位置：`batch-unit-test-generator/scripts/jaut/report.py:192`
- 类型：正确性（报告口径）
- 问题：escalations 在本次改造移除升级穿透后已无任何递增路径，最终报告的"总升级次数"恒为 0。
- 场景：全部脚本里 `entry.escalations` 只有三处置 0（`batch_update.py:134/153/162`），没有任何 `+= 1`；于是 `render_batch_finish_report` 的 `总升级次数: {total_escalations}` 永远输出 0，与实际发生的自动跳过次数无关（自动跳过只写 `methods[].skip_reason`）。用户会误以为整批没有任何降级/跳过；只有测试手工构造 `escalations=1/2` 才非零。
- 修复方向：要么在自动跳过时同步 `escalations += 1`（保留该统计），要么从报告和 state schema 中移除该字段（承认它是遗留死状态）。

## 7. 队列空时 render_finish_report 默认 met=True，与 met=False 收尾口径相反【已修复】

修复：两个 skill 的 `make_plan.py` 新增 `_final_check_still_valid(state)`——队列空时复核持久化的终验结论是否仍与当前口径一致（class 模式看类级覆盖率 ≥ 门槛；method 模式看目标方法同名重载全部 done 且达标，与 `init_is_met` 口径对齐），漂移（调门槛/终验后回落）时按未终验处理：单类模式重走 final-check 实测、批量模式交 batch_finish 统一终验，不再用陈旧结论渲染 PASS 收尾报告。回归测试 `test_handler_stale_final_check_reruns_final_check`、`test_handler_stale_final_check_method_mode_uses_target_scope`（两个 skill 各一份）；java SKILL.md §7 补充终验结论复核语义。

- 位置：`java-unit-test-generator/scripts/make_plan.py:332`（batch 侧同一模式在 `batch-unit-test-generator/scripts/make_plan.py:374`）
- 类型：正确性（报告口径）
- 问题：队列空时用 `render_finish_report(state)` 的默认 `met=True` 渲染收尾报告，空队列 + `final_checked=True` 时无法表达未达标，会输出"最终状态: PASS"。
- 场景：`state.final_checked` 为 True 且队列已空时，`_advance_cursor` 渲染 report 走默认参数 → 报告固定写"最终状态: PASS"，不再有 note。一旦该状态与实测口径不符（例如 `final_checked` 由上一轮 PASS 持久化、随后经 `--set-threshold`/`--unskip` 或批量 batch_finish 回写使覆盖率回落而队列仍空），收尾报告与 decisions 的未达标收尾（`met=False`）会给出相反结论。本次改造刚为 init_coverage 统一了 met/note 口径，这两处调用点仍未纳入同一事实源。
- 修复方向：空队列渲染收尾报告时按实际达标状态传 `met`（例如根据 state 的覆盖率/阈值计算或读取持久化的终验结论）。

---

## 修复时的注意事项

- 两个 unit-test skill 的 `jaut` 引擎是**复制后分叉**的两份（根 CLAUDE.md）：涉及共享逻辑的修复（尤其 #4、#5、#7）需 diff 两侧并跑两套测试 `python3 -m pytest tests/`。
- 预算耗尽已改为自动跳过 + `skip_reason`（用户确认的行为，勿回退为 ask_user）。
