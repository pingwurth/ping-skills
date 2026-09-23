# 工作流详细说明

本文件包含 java-unit-test-generator 技能的详细命令和参数说明，SKILL.md 主文档仅保留概述。

---

## 1. 工作树确认（select_worktree.py）

### 命令格式

```text
python scripts/select_worktree.py <当前工作目录>                        # 首次: 请求选择或请求确认新建
python scripts/select_worktree.py <当前工作目录> --list                  # 列出已存在的 worktree; 无则 run_script 给出新建命令
python scripts/select_worktree.py <当前工作目录> --choice N              # 选择第 N 个已有 worktree
python scripts/select_worktree.py <当前工作目录> --new [名称]            # 新建
python scripts/select_worktree.py <当前工作目录> --new [名称] --base <ref>  # 基于指定分支/标签/commit 新建
python scripts/select_worktree.py <当前工作目录> --new [名称] --force        # 跳过未提交变更确认，强制新建
python scripts/select_worktree.py <当前工作目录> --clear-history [--base <ref>] [--force]  # 预检通过后清理全部历史 worktree 再新建默认名
```

### 行为说明

- `--list` 有已有 worktree → `finish` 列出候选；**无已有 worktree → `run_script`（exit 1）**，`next_step.params` 为完整新建命令 `<当前工作目录> --new <默认名> --force`（含 `--base` 时一并携带），调用方逐字执行即可新建
- 无已有 worktree → 输出 `ask_user` 请求用户确认（选项：默认名新建 / 自定义名新建 / 取消），用户确认后调用方带 `--new --force` 重跑；**不出现**清理历史选项
- 有已有 worktree → 输出 `ask_user` 列出候选（`[1..N]` 选择已有、`[N+1] 新建默认 worktree`、`[N+2] 清理历史工作树并创建新的工作树(历史树未提交内容将被永久删除)`，各选项均附 resume 命令（params 以 `<当前工作目录>` 开头）可逐字执行；`--new [名称]` 自定义新建见 question 指引）
- `--clear-history` → **先预检**（base ref 可解析 / 目标路径 / 派生分支未被容器外检出；预检失败则**历史 worktree 全部保留**），再强制清理容器内全部历史 worktree（`git worktree remove --force`，**历史树未提交内容永久删除、不可恢复；分支保留不删除**）后新建默认名 worktree → `finish`；清理后新建失败的报错会注明「历史 worktree 已被清除, 无法回滚」；历史树为零时幂等跳过清理照常新建；主工作区有未提交变更时先 `ask_user` 确认（`--force` 跳过），确认文案含永久删除警示
- 已有分支 + `--base <ref>` → 先 `git branch -f` 将该空闲分支强制重置到 `<ref>` 再检出（落在派生分支上，非 detached）；分支被容器外检出则预检/创建报错
- resume 的 `params` 以位置参数 `WORK_DIR`（当前工作目录）开头，可逐字执行
- 选定/新建成功后，工作树绝对路径见 NEXT_STEP 的 `deliverables` 与 `artifacts[].kind == "worktree"`

### 后续调用

拿到工作树路径 `<worktree>` 后，后续 `init_coverage.py` 等所有脚本一律以 `--project-root <worktree>` 运行（`--workdir` 相应为 `<worktree>/.agent/java-unit-test-generator`）。

---

## 2. 初始化（init_coverage.py）

### 命令格式

```text
python scripts/init_coverage.py --project-root <worktree> --class <FQCN 或源文件路径>
    [--method <方法名>] [--threshold N] [--workdir <wd>]
    [--jacoco-version V] [--coverage-exclude PATTERN ...]
```

### 参数说明

- `--class`：接受 FQCN（如 `com.example.FooService`）或源文件路径（如 `src/main/java/com/example/FooService.java`）
- `--threshold`：默认 80；终验模式默认沿用 init 轮门槛，传 `--override-threshold` 才生效
- `--coverage-exclude`：排除模式（可重复传），模式为 fnmatch（`*` / `?`），排除模式命中目标类本身会报错

### 自动创建测试桩

测试文件不存在时，`init_coverage` 会自动创建最小化桩测试类（仅类声明，无测试方法），确保 JaCoCo 能产出覆盖率报告。

---

## 3. NEXT_STEP 协议详细格式

### 协议块结构

每个脚本结束时，在 stdout 末尾输出唯一协议块：

```text
:::NEXT_STEP_BEGIN:::
{
  "protocol_version": "1.0",
  "script": "...",
  "status": "success",
  "exit_code": 0,
  "summary": "...",
  "artifacts": [],
  "metrics": {},
  "next_step": {
    "type": "run_script|write_code|ask_user|finish|abort"
  }
}
:::NEXT_STEP_END:::
```

只解析标记块中的 JSON。

### 路由类型

- `run_script`：执行下一个脚本
- `write_code`：LLM 编写/修改测试代码
- `ask_user`：需要用户决策
- `finish`：任务完成或阶段完成
- `abort`：**立即终止本技能**——将 `message` 逐字转述给用户后结束，禁止执行任何后续脚本、禁止重试、禁止代为执行 message 中的修复命令；修复命令**由用户手动执行**，执行完毕后**由用户重新调用本技能**；无 `resume`（不等待用户答复）

### 特殊字段

- `write_code` 路由的 `next_step` 额外携带 `on_complete` 字段（完成后命令）
- `finish` 路由有两种语义：
  - 携带 `report`（`init_coverage` / `make_plan` 发出）→ 任务完成，将 `report` 内容逐字转述给用户
  - 不带 `report`（仅 `select_worktree`）→ 阶段完成，按工作流继续下一步

---

## 4. 退出码说明

```text
0 = 脚本正常完成
1 = 需要继续处理
2 = 脚本执行错误
3 = 状态/协议错误
# 新增退出码需同步更新 protocol/next-step.schema.json 和 config.py
```

### 重要说明

> **退出码 1 是正常流转信号，不是脚本失败。** Bash 工具对非零退出码会红字报错，这不构成失败判定。禁止因非零退出码而中止、盲目重试或向用户报告"脚本出错"。一切以协议块的 `status + next_step` 为准。`status="failed"`（exit 2）同样按协议块路由处理（通常 `ask_user`），禁止盲目重试。**例外（直接终止）**：`next_step.type == "abort"`；或旧版协议块中 `exit_code == 2` 且 `resume` **存在且恰好只含一个 `terminate` 选项**（数组长度 1）——转述 `message`/question 后立即终止本技能，不得执行任何后续脚本、不得重试。**禁止扩大解释**：`exit_code == 2` 且 `type == "ask_user"`、`resume` 为空数组或**缺失**，是**正常提问路径**，须转述 question 并等待用户答复后继续，不得终止。

---

## 5. 协议块缺失兜底

脚本崩溃/超时导致 stdout 无协议块时：

- 读 `<workdir>/logs/<script>.log` 尾部（约 50 行）与 summary
- 向用户报告错误并终止本技能执行
- 禁止自行猜测 `next_step`、禁止手动修改 `state.json` 恢复流程

---

## 6. 过程日志

```text
<workdir>/logs/<script>.log
<workdir>/mvn.log
```

`mvn.log` 可能很大，先用 grep 定位 `[ERROR]`、`BUILD FAILURE`、`FAILURE!` 与测试失败摘要，不要整读文件。

---

## 7. Maven 长时间执行

mvn 执行可能超过 Bash 工具默认超时。脚本内部已实现超时处理和进度监控，脚本完成时会输出 NEXT_STEP 协议块。

> LLM 无需关注超时机制细节，等待协议块输出即可。
