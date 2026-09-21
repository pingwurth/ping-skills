# 升级策略详细说明

本文件包含 java-unit-test-generator 技能的升级策略和预算控制细节，SKILL.md 主文档仅保留概述。

---

## 1. 不收敛升级条件

同一方法出现以下情况时，升级为 `ask_user`：

```text
连续 3 轮无有效覆盖率提升
```

或：

```text
连续 3 轮测试失败
```

---

## 2. 预算控制

默认预算（可通过环境变量覆盖）：

```text
单方法最多 8 轮（JAVA_UT_METHOD_ROUND_BUDGET）
全局最多 30 轮（JAVA_UT_GLOBAL_ROUND_BUDGET）
```

超过预算时，升级为 `ask_user`。

---

## 3. 用户决策选项

当升级为 `ask_user` 时，用户可以：

```text
继续
跳过方法
调整门槛
终止
```

恢复命令以 `next_step.resume` 为准，用户答复后**逐字执行对应命令**，禁止手改 `state.json`。

---

## 4. 规范校验升级

`validate_rules` 连续 5 轮规范违规未通过时，升级为 `ask_user`：

```text
→ ask_user
```

用户可以：

```text
继续修复
跳过方法
终止
```

恢复命令以 `next_step.resume` 为准。

---

## 5. 迭代轮次说明

- 违规修复轮不计入迭代轮次
- 连续 5 轮未通过即升级 `ask_user`（升级后计数清零，重新计满 5 轮才会再次升级）
- 禁止 `write_code ↔ validate_rules` 无限循环

---

## 6. 策略参数说明

> 以下策略参数为维护者规格，执行时无需记忆，升级由脚本判定并在 `resume` 中给出命令。

---

## 7. 恢复机制

用户答复后，根据 `next_step.resume` 中的选项执行对应命令：

1. **继续**：按 `resume` 中的 `script` + `params` 执行
2. **跳过方法**：标记当前方法为 `skipped`，继续下一个方法
3. **调整门槛**：使用新的阈值重新评估
4. **终止**：结束技能执行

禁止手改 `state.json` 恢复流程。
