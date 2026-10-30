# Scope — Phase 0 范围阶梯(engine 细节)

`crp.py start` 实际执行本文件描述的阶梯(全部只读探测, 失败即下降);
本文档是 `scripts/crp/scope.py` 的行为契约与边界语义。

## 状态探测(全部只读)

```bash
git status --porcelain -z -uall          # 文件级 untracked(-uall 展开目录)
git symbolic-ref -q --short refs/remotes/origin/HEAD   # 默认分支, 若设置
git rev-parse -q --verify origin/main    # 默认分支回退, 首个命中
git rev-parse -q --verify origin/master
git rev-parse -q --verify main
git rev-parse -q --verify master
```

## 阶梯(首个非空命中; 错误或空 = 继续下降)

| 级 | 源 | 探测 |
|---|---|---|
| 1 | 显式目标 | 分类见下 |
| 2 | 当前分支的 PR | `gh pr diff --name-only`(gh 缺失/失败即下降) |
| 3 | 未推送提交 | `git diff @{upstream}...HEAD` |
| 4 | 分支 vs 默认分支 | `git diff <default>...HEAD`(浅克隆 merge-base 失败即下降) |
| 5 | 工作树 | `git diff HEAD`(staged + unstaged) |

- **范围 2–4 叠加工作树改动**: porcelain 有暂存/未暂存改动时追加
  `git diff HEAD`(评审通常发生在提交前)。
- **untracked 累积**: `??` 对一切 `git diff` 形态不可见; 仅由 untracked 构成
  的 diff 不是空 diff。
- **全空逃逸口**: 全部梯级失败/为空且无 untracked → ask_user, 附完整探测表;
  绝不静默空审, 绝不从记忆重构文件清单。

## 显式目标分类(rung 1)

| 形态 | 识别 | 范围 |
|---|---|---|
| `repo` | 字面量 | 空树基线 `git diff 4b825dc….HEAD`, 一切 tracked 读作新文件 |
| PR 号 | 纯数字 | `gh pr diff <N>`(不可用 → 逃逸口) |
| 提交区间 | 含 `..` | `git diff <range>` |
| 路径 | 磁盘存在或在 `git ls-files` | 受限阶梯(下述) |
| 分支 | 其余 | `git diff <default>...<branch>`; 目标即默认分支时改用其 upstream, 无 upstream → 逃逸口 |

## 路径目标的受限阶梯与 whole-file 回退

同阶梯限定 `-- <path>`(`git diff HEAD -- <path>` 先行), 外加路径下 untracked
为新文件。**全部受限梯级为空**(干净已提交文件)→ 切换 whole-file 模式:
范围 = 文件全部当前内容; brief 标注 "no diff baseline"; 意图声明与 Angle B
移到 `git log -p -- <path>`; 既有问题豁免解除(用户点名了这个文件)。

路径限定的是**报告范围**不是阅读范围: 角度仍跨仓阅读(C 追调用方、L 查布线
另一端、门禁全仓跑), 发现锚定目标路径; 其他文件的缺陷仅当目标的改动依赖/
暴露它时报告(引用外部行作证据), 否则至多进 Open questions。

## repo 模式语义

空树基线使一切 tracked 文件读作新文件; overlay 排除项此时最关键
(vendor/generated 先出)。豁免解除全域生效; Angle B 空转(无删除)可降权;
意图声明取自 README/docs。大仓库: deep 模式或按模块反复路径目标 ——
一区一审胜过全仓一审。

## 统计与 fan-out

`stats` 来自 `--numstat` 求和 + untracked 行数; 驱动 standard 模式 fan-out
启发式(>8 文件或 ≥800 行 → 热点角度集 B/C/E/L/T)。

## 漂移探测(续跑/每次 submit)

记录签名 = HEAD + porcelain 摘要。HEAD 移动 → ask_user(重导范围保留台账 /
沿用原范围并标 stale / 终止); 仅工作树变化 → 警告行, 范围不变(记录的范围
即审查基准)。
