# 并行写作与 writer 报告(parallel-writing.md)

engine 的认领是事务级的: `pwiki next` 原子发放 job(带 claim 令牌),
并行 writer 不会互踩; 令牌随 `--claim` 交回, 失配即孤儿稿保存(exit 3,
稿件不丢)。

## 单写循环(默认)

```
pwiki next                       # 取一个 job(提示: --claim 令牌)
# …读 sources/digest/knowledge, 写页面文件…
pwiki submit <id> --file PAGE.md --summary "…" --key-topics "a,b" \
    [--claim <token>] [--report report.json]
```

## 并行 fan-out(大计划)

每个 writer 子代理拿到:
1. ONE job 载荷(`pwiki next --json` 输出; 批量用 `--batch N`)
2. digest 路径 + 该 job 的模块 knowledge 文件(载荷里有 knowledge/context 行)
3. `references/writing-guide.md` + `references/validation.md`
   (+ deep 风格加 `templates/page.md`)
4. 读声明源 + 至多 3 个额外文件的权限

批量取件 → 每子代理独立 `submit --claim`(引擎串行化写锁, 交错提交安全;
仍建议错峰 1-2 秒)。job 调度是 knowledge-first + 自底向上: **先发叶子**,
父页批次放最后(它们聚合子页 summary)。≤20 项单线程也够。

子代理完成一个 job 后可以继续 `next` 取下一个, 直到引擎报"全部终态"。

## writer 报告(--report, 结构化反馈通道)

写页时发现的问题走 JSON 报告(不改动计划/不自行改名):

```json
{
  "files_read_beyond_sources": ["src/extra.py"],
  "uncertainties": [
    {"claim": "未发现重试逻辑", "verification": "ls services/*/retry* ; grep -r retry services/ → 无输出"}
  ],
  "title_mismatch": [
    {"page_id": "pack-core-state", "proposed": "会话状态机",
     "evidence": "grep -rn 'state machine' src/core → state/session.py:12"}
  ],
  "card_conflict": [
    {"card": "错误处理体系", "card_line": "统一重试三次",
     "evidence": "services/user/m0.py:1-5 显示无重试"}
  ]
}
```

- **D14**: 每条「未发现」类断言必须附 verification(命令+输出)。
- **D22**: `title_check: true` 的页 —— 验证标题概念在本仓库存在; 不存在
  时照实写并在报告里提 title_mismatch; **writer 永不自行改名**。
- **D7**: 与知识卡冲突时正文以行级证据为准, 冲突进 card_conflict;
  主代理用 `pwiki reconcile --all` 落地(改名零 LLM; 卡片重写排队)。

## 失败与跳过

- `pwiki fail <id> --error "原因"` / `pwiki skip <id> --reason "原因"` ——
  如实退出, 不要硬写。被拒的 submit 自动回 pending 并保留 checkpoint,
  修稿重交即可(attempts 会涨; 连续被拒先看 last_error 再写, 不要空转重试)。
- 租约默认 30 分钟(config `lease_minutes`), **submit 不顺延**。一次认领
  一个 job 应在租约内交稿; 超时的租约由下一次 `next` 回收, job 可能已被
  兄弟 writer 重认领 —— 此时旧令牌 submit 走孤儿稿路径, 稿件在
  `work/orphan-submits/`, 取新令牌重交即可。
- **--claim 契约**: 首次认领(attempts=1)的 submit 可省 `--claim`(单写
  便利); job 被重新认领过(attempts>1)后省略即拒(防止过期 writer 顶掉
  在写 writer)。重交/fail/skip 一律带上你最近一次 `next` 输出的令牌。
  完成态的 job 不接受 fail/skip 撤销(除非 `--force`)。

## 主代理收尾

全部终态后 `pwiki finish`(LINING 按风格硬门禁 → 发布 → 快照);
`reconcile --all` 清报告队列; 报告: 各 kind 页数、failed/skipped、lining
结果、发布路径、快照 id、`pwiki search` 用法。
