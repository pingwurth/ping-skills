# 工作流详细说明

本文件包含 java-unit-test-generator 技能的详细命令和参数说明，SKILL.md 主文档仅保留概述。

---

## 1. 工作树确认（select_worktree.py）

### 命令格式

```text
python scripts/select_worktree.py <当前工作目录>                        # 首次: 请求选择或请求确认新建
python scripts/select_worktree.py <当前工作目录> --list                  # 仅列出已存在的 worktree
python scripts/select_worktree.py <当前工作目录> --choice N              # 选择第 N 个已有 worktree
python scripts/select_worktree.py <当前工作目录> --new [名称]            # 新建
python scripts/select_worktree.py <当前工作目录> --new [名称] --base <ref>  # 基于指定分支/标签/commit 新建
python scripts/select_worktree.py <当前工作目录> --new [名称] --force        # 跳过未提交变更确认，强制新建
```

### 行为说明

- 无已有 worktree → 输出 `ask_user` 请求用户确认（选项：默认名新建 / 自定义名新建 / 取消），用户确认后调用方带 `--new --force` 重跑
- 有已有 worktree → 输出 `ask_user` 列出候选，用户以 `--choice N` 选择其一，或以 `--new [名称]` 新建
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
- `--base <ref>`：新建基于指定分支/标签/commit 的工作树（而非当前 HEAD）
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
    "type": "run_script|write_code|ask_user|finish"
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

> **退出码 1 是正常流转信号，不是脚本失败。** Bash 工具对非零退出码会红字报错，这不构成失败判定。禁止因非零退出码而中止、盲目重试或向用户报告"脚本出错"。一切以协议块的 `status + next_step` 为准。`status="failed"`（exit 2）同样按协议块路由处理（通常 `ask_user`），禁止盲目重试。

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
