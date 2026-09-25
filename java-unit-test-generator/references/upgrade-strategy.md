# 不收敛与预算策略详细说明

本文件包含 java-unit-test-generator 技能的预算控制与自动跳过细节，SKILL.md 主文档仅保留概述。

---

## 1. 自动跳过条件

同一方法出现以下任一情况时，**自动跳过该方法**（不再询问用户）：

```text
单方法迭代达上限(默认 8 轮, 含追加窗口)
全局迭代达上限(默认 30 轮, 含追加窗口)
连续 3 轮无有效覆盖率提升
连续 3 轮测试失败
validate_rules 连续 5 轮规范违规未通过
```

跳过动作由脚本落地：`methods[].status = skipped` 且写入 `methods[].skip_reason`（人类可读的原因，
如 `单方法迭代已达上限 8 轮仍未达标(当前 50.0%)`），随后路由 `make_plan.py` 推进队列中的下一个
未达标方法。

---

## 2. 预算控制

默认预算（可通过环境变量覆盖）：

```text
单方法最多 8 轮（JAVA_UT_METHOD_ROUND_BUDGET）
全局最多 30 轮（JAVA_UT_GLOBAL_ROUND_BUDGET）
```

另有两个追加窗口字段，仅作为预算上限的加项（`state.json` 中持久化）：

```text
method_round_bonus   单方法预算追加（切换方法时清零）
global_round_bonus   全局预算追加（终验延续）
```

有效上限 = 默认上限 + 对应 bonus。这些窗口由 `make_plan.py --grant-rounds N` 追加
（`_grant_rounds`：单方法/全局各 +N 并复位当前方法轨迹），或由 `--unskip` 恢复被跳过方法时
追加一个全局窗口（见 §6），可供人工在外部干预时使用；
技能的自动流程不再依赖"先问用户再追加"的交互。

---

## 3. 跳过语义

```text
当前方法 -> status=skipped + skip_reason
队列仍有未达标方法 -> make_plan.py 选下一个方法继续
队列已空(方法全部 done/skipped) -> 仍重跑一次终验, 实测后再定达标/未达标
```

- **不跳过生产代码**：跳过只作用于目标方法，已生成的测试代码保留。
- **终验照跑**：队列空后一律进入 `init_coverage.py --final-check` 实测，达标与否只由
  `decisions.init_is_met` 判定（单一事实源）。不用迭代期的覆盖率快照预判达标；仅当
  口径一致的既往终验结论存在时（`make_plan._final_check_still_valid`）才可直接收尾，
  否则重走终验实测（代价是多跑一次 mvn 实测）。
- **method 模式**：达标看目标方法本身。目标方法被跳过（未覆盖）时即使队列已空也判未达标，
  不会因"无 pending"而假 PASS（SKILL §7）。
- **收尾报告**：`最终状态: 未达标`，并在"方法"清单中逐条给出 `(跳过: <原因>)`，便于事后
  定位哪一类场景无法自动补测。

---

## 4. 规范校验(validate_rules)

- 违规修复轮不计入迭代轮次；
- 连续 5 轮未通过即自动跳过当前方法（跳过后计数清零，重新计满 5 轮才会再次跳过）；
- state 中无 `current_method` 可落地跳过时按状态错误中止（ask_user, exit 3），不假报"已跳过"；
- 禁止 `write_code ↔ validate_rules` 无限循环。

---

## 5. 终验(Final Check)的终止条件

终验阶段不再询问用户，出现以下情况直接收尾（未达标/未复核）并携带收尾报告：

```text
终验连续 FINAL_CHECK_FAIL_STREAK_LIMIT(2) 轮测试不绿且已无待修复方法
  纯环境不可信(surefire 报告缺失/无法解析/目录清理失败且无失败用例)
  -> 按未复核收尾, 报告 `最终状态: 未复核(测试结果不可信)`, 环境修复后可重跑复核
终验失败用例包含非目标类(技能无权修复)
无可补测方法(方法已完成或被跳过)但类级覆盖率未达标
```

失败用例全在目标测试类时，仍走原有的自动修复路径（`write_code` -> `make_plan.py` 重验）。

---

## 6. 恢复机制

**不得手改 `state.json`**，一律用 `make_plan.py` 的参数落地：

```text
python scripts/make_plan.py --workdir <workdir> --skip-current         # 手动跳过当前方法
python scripts/make_plan.py --workdir <workdir> --grant-rounds N       # 追加 N 轮预算窗口
python scripts/make_plan.py --workdir <workdir> --set-threshold N      # 调整覆盖率门槛
python scripts/make_plan.py --workdir <workdir> --unskip name1,name2   # 恢复被跳过的方法
```

被自动跳过（`status=skipped` 且带 `skip_reason`）的方法**只能用 `--unskip` 恢复**：
`--grant-rounds` 只加预算窗口、`--set-threshold` 只在 done↔pending 间重分类，都不会复活
skipped 方法（`pending_methods()` 不含 skipped）。

`--unskip` 的动作：

```text
校验: 方法名必须存在、status=skipped 且 skip_reason 非空(范围外方法不可恢复)
      方法名匹配全部同名重载(重载共享 --method 范围), 一次恢复所有带原因的 skipped 重载
落地: status=pending, 清空 skip_reason, 复位该方法的轮次/测试轨迹
预算: 全局窗口 +GLOBAL_ROUND_BUDGET(跳过原因多为预算耗尽, 不追加则下一轮立刻再次跳过)
      当前方法即被恢复方法时, 就地清零 iteration 与 method_round_bonus(游标不切换)
```

可与 `--grant-rounds N` 组合使用（如 `--unskip doStuff --grant-rounds 8`，再多给 8 轮）；
`--skip-current` / `--set-threshold` 不能与其他恢复参数同用。

调整门槛后，`_reclassify_by_threshold` 会按新门槛重评估方法状态（低于新门槛的 done 方法打回
pending 并清轨迹，已达新门槛的 pending 方法晋升 done）。