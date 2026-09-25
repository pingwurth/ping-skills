# 子代理委派与失控防护完整契约

## 1. 委派要件（batch_next 输出）

`batch_next.py` 认领类后输出 `ask_user` 型 NEXT_STEP，`question` 字段携带委派要件：

- **worktree 绝对路径**（项目根，`--project-root` 值）
- **目标类 FQCN**
- **类 workdir 绝对路径**（`<worktree>/.agent/batch-unit-test-generator/classes/<类名>/`）
- **scripts 目录绝对路径**
- **门槛**（百分比）
- **类级剩余预算**（有效预算 - `rounds_used`；有效预算 = `BATCH_CLASS_ROUND_BUDGET`，大类探索模式翻倍）
- **认领序号**（`attempts`）

## 2. 子代理启动命令

子代理从以下命令开始：

```
python <scripts_dir>/make_plan.py --workdir <类workdir>
```

`state.json` 已由 `batch_init` 生成（含 `batch_mode=True`），`make_plan` 会自动恢复游标。

## 3. 预算与跳过语义

### 3.1 类内自动跳过（子代理无感）

`decide_after_verify` 中四处触发点（单方法预算耗尽 / 全局预算耗尽 / 连续测试失败 / 连续无提升）
和 `validate_rules` 的连续违规触发点，一律：

- 方法状态写 `skipped`，原因写入 `state.json` 的 `methods[].skip_reason`；
- 全局预算耗尽时同时跳过全部剩余 pending 方法（`skip_all_pending`）；
- 返回 `MAKE_PLAN`，由子代理继续驱动队列（下一方法，或队列空时输出类内完成报告）。

过程中**不产生 `ask_user`**，子代理无需向用户提问。

### 3.2 类级预算

- 有效预算 = `batch_class_round_budget()`（默认 30，环境变量 `JAVA_UT_BATCH_CLASS_ROUND_BUDGET`
  可覆盖）；大类探索模式 × `EXPLORATION_MODE_MULTIPLIER`（默认 2）；再加上 `--unskip` 追加的
  类级窗口（`state.json` 的 `class_round_bonus`）。口径实现在 `State.class_round_budget`。
- `verify_coverage.py` 在执行 mvn **之前**预检查 `state.class_budget_exhausted`：耗尽则跳过全部
  未达标方法并回 `make_plan`，不再浪费一轮全量构建。
- 主流程侧仅在子代理**异常中断**（mvn 环境/依赖失败 exit 2、用户中止）时才保留 `ask_user` 兜底，
  用户决策后运行 `make_plan.py --grant-rounds/--skip-current/--set-threshold/--unskip` 落地再重新委派。

## 4. 大类方法组拆分

### 4.1 触发条件

当类满足以下任一条件时，`batch_init` 自动将其标记为"大类"并拆分方法组：

- `diff_add >= 300`（差异新增行数阈值）
- `pending 方法数 >= 30`（方法数阈值）

### 4.2 拆分策略

- pending 方法按顺序切分为每组 ≤ 10 个方法的方法组
- 方法组信息写入 `batch_state.json` 的 `BatchClassEntry.method_groups`
- `state.json` 标记 `exploration_mode=True`（预算翻倍）

### 4.3 委派流程

1. `batch_next` 认领大类时，从当前组（`current_group`）取方法名列表
2. 委派命令附带 `--method-group NAME1,NAME2,...` 参数
3. `make_plan` 将组外 pending 方法标记为 skipped，仅处理组内方法
4. 子代理正常执行类内迭代循环（预算翻倍：`BATCH_CLASS_ROUND_BUDGET × 2`）
5. 当前组完成后，`batch_update` 调用 `advance_group()` 推进到下一组（仅当剩余组仍有可调度方法；类级预算耗尽后剩余组为零工作，不再逐组空转委派）
6. 类状态回到 `pending`，`batch_next` 再次认领并输出下一组的委派要件
7. 所有组完成后（或剩余组已无可调度方法），类标记为 `done`

### 4.4 子代理无感

子代理看到的委派要件与普通类一致，只是：
- `make_plan` 命令多了 `--method-group` 参数
- 委派要件中显示方法组进度（第 N/M 组）
- 预算翻倍（探索模式）

## 5. 失控防护

### 5.1 命令白名单

子代理仅允许执行：

- `make_plan.py`、`build_prompt.py`、`validate_rules.py`、`verify_coverage.py`
- `on_complete` 指定的验证命令

### 5.2 禁止清单

- `select_worktree.py`、`init_coverage.py`
- `batch_diff.py`、`batch_init.py`、`batch_next.py`、`batch_update.py`、`batch_finish.py`
- 裸 mvn（覆盖率验证只经 `verify_coverage.py`）
- 任何 git 写操作
- 修改 `src/main/java` 下任何文件
- 自行开启下一类或再认领

### 5.3 交付报告

子代理退出前必须输出交付报告：

- 类 FQCN、轮次、覆盖率 before→after、达标状态
- 升级请求: <无 / 具体原因与建议；异常中断时注明；跳过的方法见类内完成报告>
- 类内 finish 报告逐字转述（如 finish 路由携带了 report）

## 6. 断点续跑

- **子代理中断**：`batch_next` 检测到 `in_progress` 类，直接重新输出委派要件。子代理从 `make_plan` 续跑（state.json 存在则恢复游标）。
- **僵死复位**：`batch_next --reset-claim <FQCN>` 将 `in_progress` 复位为 `pending`。

## 7. 同名简单类 workdir 冲突

冲突时以 FQCN 点换下划线作为目录名（如 `com.foo.Bar` → `com_foo_Bar`）。
