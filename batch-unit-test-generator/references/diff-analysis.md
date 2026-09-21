# 差异过滤与排序规则细节

## 1. 差异文件采集

- `git diff --name-only --diff-filter=ACMR target...HEAD`（三点 merge-base），auto 模式回退两点 `target..HEAD`。
- 每文件 `git diff --numstat` 取增删行数（`-` 视为 0）。
- diff base 固化进 `batch_state.json`，执行期不刷新。

## 2. 过滤链

按顺序执行：

1. **仅 src/main/java**：路径含 `/src/main/java/` 或以 `src/main/java/` 开头。
2. **剔除测试类**：`/src/test/` 路径下的文件；类名以 `Test` 开头（后跟大写字母）或以 `Test`/`Tests`/`TestCase` 结尾。
3. **剔除 package-info / module-info**：`package-info.java`、`module-info.java`。
4. **pom jacoco excludes 过滤**：解析项目所有 `pom.xml` 中 `jacoco-maven-plugin` 的 `configuration/excludes/exclude`，按 ant 风格语义匹配（`**` 匹配零或多目录段，`*` 不跨目录；移植自 `diff-coverage-unit-test/scripts/diff_coverage.py` 的 `matches_jacoco_pattern`）。
5. **剔除接口/枚举/注解**：源码启发式（净化注释/字符串后扫描 `interface|enum|@interface` 声明）。
6. **用户 --exclude-glob 过滤**：fnmatch 匹配。

## 3. 源路径 → FQCN

复用 `jaut/javasrc.py` 的 `fqcn_from_source_path`：从 `src/main/java` 之后的相对路径反推 FQCN。

## 4. 模块定位

复用 `jaut/maven.find_module_for_source`：从源文件向上找最近含 `pom.xml` 的目录，根模块返回 `.`。

## 5. 已有测试类探测

扫描 `src/test/java` 同包目录下 `*Test.java` / `*Tests.java` / `*TestCase.java` / `Test*.java`。

## 6. 排序策略

排序分两阶段执行，各有不同数据可用：

### 6.1 batch_diff 阶段（预排序，无 jacoco 数据）

此阶段仅有 git diff 数据（增删行数），排序用于候选清单展示：

- **module-coverage**（默认）：模块聚类 + 差异行数（增+删）降序。
- **coverage**：按 FQCN 字母序（占位排序，实际覆盖率尚不可用）。
- **diff-size**：按差异行数（增+删）降序。

### 6.2 batch_init 阶段（终排序，有 jacoco 数据）

`batch_init` 运行 jacoco 后按以下规则对确认类排序，决定类级委派顺序：

- **module-coverage**：模块为外键，模块内基线覆盖率升序（优先处理低覆盖类），并列按差异行数降序。

## 7. 大类检测与方法组拆分

### 7.1 检测目的

大型类（方法数多或差异行数大）若一次性委派给单个子代理，容易超出迭代预算或难以在单轮内完成。检测大类后按方法组分批委派，每组独立执行，降低单次任务复杂度。

### 7.2 检测阈值

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LARGE_CLASS_DIFF_THRESHOLD` | 300 | `diff_add` 行数 ≥ 此值视为大类 |
| `LARGE_CLASS_METHOD_THRESHOLD` | 30 | 方法数 ≥ 此值视为大类 |
| `METHOD_GROUP_SIZE` | 10 | 每组方法数上限 |

满足任一条件即标记为大类。

### 7.3 两阶段检测机制

大类检测分两个阶段，使用不同的方法数口径：

1. **batch_diff 预筛（源码方法数）**：`_count_methods` 通过正则启发式统计源文件中 `public`/`protected` 方法数（排除构造器和抽象方法）。此阶段尚无 jacoco 数据，源码方法数作为**预筛阈值**，候选清单中标记 `[大类]` 并显示 `N方法(源码)`。
2. **batch_init 确认（pending 方法数）**：运行 jacoco 后获取报告中实际的 pending 方法数，以此作为**最终判定依据**。若 pending 方法数 ≥ `LARGE_CLASS_METHOD_THRESHOLD` 或 `diff_add` ≥ `LARGE_CLASS_DIFF_THRESHOLD`，则执行方法组拆分。

两阶段口径差异说明：源码方法数包含所有可见方法（含已有测试覆盖的方法），而 pending 方法数仅包含 jacoco 报告中尚未覆盖的方法，后者通常更小。

### 7.4 方法组拆分流程

`batch_init` 确认大类后的拆分策略：

- pending 方法按顺序切分为每组 ≤ `METHOD_GROUP_SIZE` 个方法的方法组
- 方法组信息写入 `BatchClassEntry.method_groups`
- `state.json` 标记 `exploration_mode=True`（预算翻倍：`BATCH_CLASS_ROUND_BUDGET × EXPLORATION_MODE_MULTIPLIER`）

完整委派流程（`batch_next` → `make_plan` → `batch_update` → `advance_group`）见 [delegation.md §4](./delegation.md)。
