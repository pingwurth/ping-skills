# 代码审查问题记录（2026-09-27）

对 java-unit-test-generator 全目录深度审查的结果，共 12 条发现（2 High / 4 Medium / 6 Low），按严重程度排序。

审查手段：逐文件深读 scripts/jaut 18 模块、6 个入口脚本、fast-single-cov.sh、protocol schema、references、SKILL.md，抽查 tests 23 个文件；运行 `python3 -m pytest tests/` → 500 用例全过（2.71s）；与 batch-unit-test-generator 侧逐文件 diff 对照。

复核结论：
- 2026-09-24 七条修复中涉及本 skill 的 #4（final_test_summary 仅 met=True 写盘）、#7（空队列默认 met=True 渲染 PASS）均已落地且带回归测试，无回归；其余五条在 batch 侧，不在本次范围。
- `问题.txt` 前 7 项需求（mvn 不加 -T、--clear-history、.codegraph 检查/拷贝/sync、快速/标准模式、预算耗尽自动跳过+skip_reason）均已正确实现；第 8-10 项未实现，按约定不计为缺陷。

总体结论：无 Critical 问题——subprocess 全部 list 参数无 shell、ElementTree 解析外部 XML 天然防 XXE、路径均由 workdir 派生无用户可控遍历。架构分层（L0-L5 单向依赖）与协议契约在代码/文档/测试三层整体同步。风险集中在两个"边界口径"：快速/标准路径的覆盖率口径（#1）与 Windows 进程探测语义（#2）。

修复进度：2026-09-27 已全部修复（12/12）并同步 batch 侧共性项；java 套件 515 passed（基线 500 + 新增 15 回归）、batch 套件 565 passed，均零回归。

## 1. fast-single-cov.sh 的 JaCoCo agent includes 不匹配内部类，覆盖率被系统性低估【High】【已修复】

- 位置：`java-unit-test-generator/scripts/jacoco/fast-single-cov.sh:126`
- 类型：正确性（覆盖率口径）
- 问题：`ARG_LINE="-javaagent:$JACOCO_AGENT=destfile=$EXEC_FILE,includes=$TARGET_CLASS"` 中 includes 为精确类名匹配（无通配），`com.example.Foo` 不会插桩 `Foo$Bar`、`Foo$1`（匿名类）、`Foo$Builder` 等内部类；报告生成（L162-167）含全部 classfiles 且无 name 过滤 → XML/CSV 中内部类行全部按 0 覆盖计，`jaut/jacoco.py`（L96-97）聚合到外部类 FQCN 后：
  1. 类级覆盖率被低估（内部类行数全部计入 missed）；
  2. 内部类方法进入方法表后永远 0% → `no_improvement` 3 轮 → 被自动跳过且 skip_reason 错误归因（"连续 3 轮无提升"，实际是数据缺失）；
  3. 与降级路径 `mvn test jacoco:report`（maven 插件无 includes 限制、全类插桩）口径不一致，两路径混用时同轮数据漂移。
- 场景：任何目标类含非静态内部类/匿名类/Switch 生成分派类（Java 代码中极常见）。
- 修复方向：includes 追加内部类通配 `:${TARGET_CLASS}\$*`（bash 双引号内 `\$` 为字面 `$`；JaCoCo includes 支持 `:` 分隔多模式与 `*` 通配，`Foo$*` 覆盖全部内部类）。
- 修复结果（2026-09-27）：ARG_LINE 已改为 `includes=$TARGET_CLASS:${TARGET_CLASS}\$*`；用仓内 jacocoagent.jar 实测验证：仅精确类名时 `Foo$Inner` 全 0 覆盖，追加 `$*` 通配后 2/2 covered，且点号/斜杠格式经 agent 归一化等价。bash -n 通过，test_maven.py 增脚本内容断言。

## 2. proc.py 存活探测在 Windows 上向 mvn 进程组投递 CTRL_C，周期性中断构建【High】【已修复】

- 位置：`java-unit-test-generator/scripts/jaut/proc.py:80-88`
- 类型：跨平台正确性
- 问题：`os.kill(self._pid, 0)` 在 POSIX 是无害探测，但在 Windows 上 `sig=0` 恰等于 `CTRL_C_EVENT`，实际执行 `GenerateConsoleCtrlEvent(0, pid)`。子进程以 `CREATE_NEW_PROCESS_GROUP` 启动（proc.py 已显式支持 Windows），其 pid 即进程组 id → 每次"存活探测"都会向整个 mvn 进程组发送 CTRL_C；`_run()`（L90-96）每 `MVN_PROGRESS_INTERVAL_SECONDS`（30s）探测一次，mvn 运行超 30 秒即被 JVM shutdown 中断。
- 场景：Windows + 任一轮 mvn 执行超过 30 秒（几乎必然）。
- 修复方向：Windows 分支改用 `OpenProcess(SYNCHRONIZE) + WaitForSingleObject(0)` 探测，不复用 `os.kill(pid, 0)`。
- 修复结果（2026-09-27）：新增模块级 `_windows_process_alive`（OpenProcess(SYNCHRONIZE)+WaitForSingleObject 零等待探测），`_is_process_alive` 增加 `os.name == "nt"` 分支委托之；test_proc.py 补委托与死亡进程双向测试。

## 3. rules.py 规则 d 违规消息引用不存在的 `--acknowledge` 参数（幽灵参数）【Medium】【已修复】

- 位置：`java-unit-test-generator/scripts/jaut/rules.py:166-167`
- 类型：协议一致性 / 文档-实现同步
- 问题：违规消息建议"请还原或使用 --acknowledge 豁免"，但 validate_rules.py 的 CLI 仅定义 `--test-file` 与公共参数，全仓确认 `--acknowledge` 不存在。LLM 按消息指引带该参数重跑 → argparse 报错 exit 2 且无协议块 → 按 SKILL.md §2 兜底直接终止技能。
- 场景：终验/迭代期检测到"src/main/java 文件内容与基线不一致"（用户在 init 后手改业务代码）。
- 修复方向：删除消息中"或使用 --acknowledge 豁免"（当前无豁免机制，唯一出路是还原文件）。
- 修复结果（2026-09-27）：消息改为仅"请还原"；类 docstring 补第 5 条豁免逻辑；全仓 grep 确认 `--acknowledge` 无残留（仅测试断言与本文档命中）。

## 4. SKILL.md §0.2 第 7 步终验命令缺必填 `--class` 参数【Medium】【已修复】

- 位置：`java-unit-test-generator/SKILL.md:66`
- 类型：协议一致性（AGENTS.md 要求文档与 CLI 同步）
- 问题：todo 清单第 7 步写 `init_coverage.py --project-root <worktree> --final-check`，但 `--class` 为 required；`references/workflow-details.md` §2 与 `transitions.py` FINAL_CHECK 路由的 params 均含 `--class`。LLM 按 §0.2 清单逐字执行 → argparse exit 2 无协议块 → 终止技能（自动路由路径用 next_step.params 不受影响，风险仅在 LLM 自行拼命令时）。
- 修复方向：补 `--class <目标类FQCN>`。
- 修复结果（2026-09-27）：第 7 步补 `--class <目标类FQCN 或源文件>`，并注明多目标类场景需 `--workdir` 指向该类专属目录。

## 5. init_coverage.py 终验模式 state.json 缺失/损坏时静默降级为全新 init，门槛丢失可致假 PASS【Medium】【已修复】

- 位置：`java-unit-test-generator/scripts/init_coverage.py:172`
- 类型：正确性 / 状态机健壮性
- 问题：`prev_state = store.load() if args.final_check else None`——终验时 load() 因文件缺失/损坏返回 None，不报 exit 3 状态错误，而是静默按全新 init 处理：threshold 回退默认 80（丢 init 轮 `--threshold 90` 设定）→ 真实覆盖率 80≤rate<90 时误判达标收尾（假 PASS）；`coverage_excludes`、`target_method`（method 模式静默变 class 模式）、`test_class_file/plan` 全部丢失。
- 场景：终验时 state.json 被外部损坏/误删，或 `--workdir` 传错路径。
- 修复方向：终验模式 state 缺失应 fail-fast（StepError.state_error，exit 3），提示重跑 init 起始轮。
- 修复结果（2026-09-27）：终验 `prev_state` 为 None 时 raise StepError.state_error（exit 3），question 指引检查 --workdir/--project-root 或重跑 init 轮；补 exit 3 回归测试。

## 6. state.py locked() 解锁后 unlink 锁文件，inode 竞态可致互斥失效【Medium】【已修复】

- 位置：`java-unit-test-generator/scripts/jaut/state.py:116-128`
- 类型：并发安全
- 问题：加锁模式为 open(lock) → flock → yield → unlock → close → unlink。竞态窗口：A 持锁期间 B 打开同一 inode 阻塞于 flock；A 解锁后 unlink 路径 → C 以 `open(..., "a")` 创建新 inode 立即获锁 → B（旧 inode）与 C（新 inode）同时"持锁"，互斥被绕过 → `load → 修改 → save` 丢更新。
- 场景：用户手动执行 make_plan.py --unskip/--grant-rounds 干预（upgrade-strategy.md §6 明确鼓励）与 LLM 驱动的脚本同时运行。
- 修复方向：锁文件常驻不删（24h 残留锁检测 warning 已就位，正好配套）。
- 修复结果（2026-09-27）：删除 finally 中的 unlink，获锁后 os.utime 刷新 mtime（残留检测以"最近一次获锁时间"为准），警告文案改为"24 小时未更新"；补锁文件常驻与 mtime 刷新测试。

## 7. verify_coverage.py 降级路径无条件回退完整 mvn，与 init 侧门控不一致【Low】【已修复】

- 位置：`java-unit-test-generator/scripts/verify_coverage.py:103-107`
- 类型：效率 / 行为一致性
- 问题：init 侧失败后先经 `_is_fallbackable_failure` 判定（编译失败/测试类未找到等降级无意义），verify 侧无门控直接降级；fast-single-cov.sh 因编译失败退出时，verify 仍白跑一次完整 `mvn test jacoco:report`（必然同样失败），每轮多耗一个完整 mvn 周期。
- 修复方向：加同样门控，不可降级的失败直接进入编译错误解析分支。
- 修复结果（2026-09-27）：降级前经 `maven._is_fallbackable_failure(mvn_log)` 门控（与 init 侧同口径），不可降级时 logger.error 并落入既有编译/环境错误分支；补门控双向测试（编译失败不降级 / exec 未生成降级）。

## 8. logutil.py：get_logger 死代码；module_logger 兜底日志写系统临时目录，与排障文档脱节【Low】【已修复】

- 位置：`java-unit-test-generator/scripts/jaut/logutil.py:47-49、52-75`
- 类型：代码质量 / 可观测性
- 问题：① get_logger 全仓无调用方，且返回的 logger 名（`java_ut.script`）与 setup_logger 实际创建的（`java_ut.script.workdir`）不匹配——若被调用会静默拿不到 handler；② module_logger() 把兜底 FileHandler 挂到命名空间根并写 `tempfile.gettempdir()/java-unit-test-generator/logs/common.log`——maven.py/state.py/proc.py 等模块级日志实际落在系统临时目录而非 workdir/logs，而 workflow-details.md §5 的协议块缺失兜底指引是"读 `<workdir>/logs/<script>.log` 尾部"，关键报错不在声称的位置。
- 修复方向：删除 get_logger；兜底路径在文档与 stderr 提示中显式化。
- 修复结果（2026-09-27）：删除 get_logger 与未使用的 import os；module_logger 首次建立兜底 handler 时向 stderr 打印实际路径（stdout 仍只留给协议块）；workflow-details.md §6 补兜底日志位置说明。

## 9. fast-single-cov.sh 杂项：死变量、文档化的死参数、无 trap 清理、无 set -euo pipefail【Low】【已修复】

- 位置：`java-unit-test-generator/scripts/jacoco/fast-single-cov.sh:95、129`
- 类型：代码质量 / 健壮性
- 问题：① L95 JSON_FILE 定义后从未使用；② 头部注释声称接收第 4 位置参数"日志文件路径"但脚本忽略，而 maven.py 调用时确实传了 log_file——参数与文档双重冗余；③ MVN_LOG=$(mktemp) 无 trap cleanup EXIT，信号中断时 /tmp 残留；④ 无 set -euo pipefail。注意：batch 侧同名脚本已加 set -euo pipefail + trap 并删除死参数——分叉修复未回流 java 侧。
- 修复方向：对齐 batch 侧的 trap/参数清理；保留 java 侧"测试失败仍产报告"的语义差异，不可整文件盲抄。
- 修复结果（2026-09-27）：set -uo pipefail（刻意不加 -e，保留测试失败仍产报告）+ trap cleanup EXIT + MVN_LOG 预初始化；删 JSON_FILE 死变量与第 4 位置参数文档，maven.py 调用 cmd 不再传 log_file；位置参数取值改 `${1:-}` 与 `${POSITIONAL[@]+...}` 防 set -u 误报；bash -n + 脚本内容断言入测试。

## 10. maven.py pom 解析 endswith("modules") 匹配任意深度元素，profile 的 modules 会被误判【Low】【已修复】

- 位置：`java-unit-test-generator/scripts/jaut/maven.py:200-209、212-229`
- 类型：正确性（边界）
- 问题：`root.iter()` 匹配任意深度的 `<modules>`，Maven profile 可声明自己的 `<modules>` → 单模块项目带 profile-modules 时 is_multi_module 误判（init 阶段多跑一次 mvn install -DskipTests）；get_maven_modules（break 取首个匹配）若 profiles 在 modules 之前会拿到 profile 的模块列表，javasrc.py 据此限定源文件搜索范围，极端情况下源文件解析失败。
- 修复方向：只认根 pom 直接子元素（namespace 无关的 localname 比较）。
- 修复结果（2026-09-27）：新增 `_local_tag()` 取 namespace 无关局部名，is_multi_module/get_maven_modules 只遍历 <project> 根级子元素；补 profile-modules 误判与带 xmlns 根级 modules 双向测试。

## 11. rules.py 规则 d 的删除检测不与基线比对，init 前已删除的文件误报违规【Low】【已修复】

- 位置：`java-unit-test-generator/scripts/jaut/rules.py:168-175`
- 类型：正确性（边界）
- 问题：git_deleted_main_source_paths 把 git status 全部 `D` 项报为违规，而 record_git_baseline 只快照 init 时现存的 src/main/java 文件——init 前已被删除的文件不在基线，无法豁免 → LLM 收到"禁止删除用户已有文件"的违规但并非它删除。
- 场景：从用户既有脏工作区选定/检出 worktree 且该区有历史删除记录。
- 修复方向：删除检测以基线为准——仅当被删路径存在于 git_baseline 时才构成违规。
- 修复结果（2026-09-27）：删除违规改为仅基线内路径（`sorted(p for p in deleted if p in baseline_paths)`）；补基线外删除豁免/基线内删除违规双向测试。

## 12. 测试缺口：若干高价值场景无覆盖【Low】【已修复】

- 位置：`java-unit-test-generator/tests/`
- 类型：测试质量
- 问题：500 用例整体覆盖优秀，但关键缺口：① test_proc.py 的 _is_process_alive 仅 POSIX 路径被测，#2 的 Windows 语义 bug 测不出；② init_coverage final-check + state 缺失路径无测试（修复 #5 后应补 exit 3 断言）；③ fast-single-cov.sh 的 ARG_LINE 生成无断言（#1 类问题 Python 测试无法发现——可把"调用参数含 `$*` 内部类通配"作为 run_fast_single_cov 的 cmd 断言）；④ SKILL.md 代码块命令与 argparse 必填参数一致性无 lint；⑤ locked() 互斥行为无测试。
- 修复方向：结合 #1/#2/#5/#6 的修复补回归测试。
- 修复结果（2026-09-27）：新增 15 个回归测试——test_proc.py Windows 探测×2、test_rules.py 规则 d 提示/删除基线×3、test_state.py 锁常驻/mtime×2、test_maven.py profile-modules/命名空间/bash -n/脚本内容×5、test_init_coverage_handler.py 终验 state 缺失 exit 3×1、test_verify_coverage_handler.py 降级门控双向×2；全套 515 passed。④（SKILL.md 与 argparse 一致性 lint）未实现，属工程化改进，不阻塞。

## 两侧共性（batch-unit-test-generator 同步修复清单）

经 diff/grep 确认 batch 侧同样存在、修复时建议同步的问题：

1. fast-single-cov.sh includes 内部类缺口 → #1（已同步）
2. proc.py os.kill(pid, 0) Windows CTRL_C → #2（已同步）
3. rules.py --acknowledge 幽灵参数（两侧 rules.py diff=0）→ #3（已同步）
4. state.py locked() unlink 竞态 → #6（已同步）
5. 反向：batch 侧 fast-single-cov.sh 的 set -euo pipefail + trap 修复未回流 java 侧 → #9（已回流，java 侧用 set -uo pipefail 保留自身语义）

2026-09-27 实际同步时另确认并同步：#10（maven.py 根级 modules 匹配）、#11（删除检测比对基线）、#5（终验 fail-fast）、#8（logutil 清理，目录名保持 batch-unit-test-generator）；#7 无需同步——batch 侧已有 `run_single_cov_with_fallback` 封装（内含 `_is_fallbackable_failure` 门控与日志备份）；#9 中 JSON_FILE 死变量与 `${POSITIONAL[@]+...}` 加固亦一并同步。同步后 batch 套件 565 passed。

---

## 修复时的注意事项

- 两个 unit-test skill 的 jaut 引擎是复制后分叉的两份（根 CLAUDE.md）：涉及共享逻辑的修复需 diff 两侧并跑两套测试 `python3 -m pytest tests/`。
- java 侧 fast-single-cov.sh 同步 batch 侧 set -euo pipefail/trap 时，必须保留"测试失败仍产报告"的语义（batch 侧 test-compile 与 surefire:test 合并执行、set -e 下 mvn 失败即退出，行为不同，不可整文件盲抄）。
