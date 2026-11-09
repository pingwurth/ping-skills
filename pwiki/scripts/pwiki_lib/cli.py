"""pwiki CLI: argparse 树 + 派发。

命令所有权表(派发按此惰性导入, 各阶段落地对应模块即生效, cli.py 不再改动):

  engine.py    init begin next submit fail skip regenerate pause resume cancel
               status finish
  scan.py      scan
  modules.py   modules
  planner.py   plan validate(plan gate)
  digest.py    digest
  gates.py     validate --content (发布树验证器)
  snapshot.py  update
  publish.py   pages read write delete export
  search.py    search
  reconcile.py reconcile
  qoderplan.py qoder-plan

所有子命令接受 --repo/--root/--locale 且允许出现在子命令之前或之后
(qwiki 实战 bug9: 主/子解析器互相覆盖 → argparse.SUPPRESS 双注册)。
"""

from __future__ import annotations

import argparse
import sys

from . import config

# command -> (module_name, function_name)
COMMAND_OWNERS = {
    "init": ("engine", "cmd_init"),
    "scan": ("scan", "cmd_scan"),
    "modules": ("modules", "cmd_modules"),
    "plan": ("planner", "cmd_plan"),
    "digest": ("digest", "cmd_digest"),
    "validate": ("planner", "cmd_validate"),
    "begin": ("engine", "cmd_begin"),
    "next": ("engine", "cmd_next"),
    "submit": ("engine", "cmd_submit"),
    "fail": ("engine", "cmd_fail"),
    "skip": ("engine", "cmd_skip"),
    "regenerate": ("engine", "cmd_regenerate"),
    "pause": ("engine", "cmd_pause"),
    "resume": ("engine", "cmd_resume"),
    "cancel": ("engine", "cmd_cancel"),
    "status": ("engine", "cmd_status"),
    "finish": ("engine", "cmd_finish"),
    "reconcile": ("reconcile", "cmd_reconcile"),
    "update": ("snapshot", "cmd_update"),
    "pages": ("publish", "cmd_pages"),
    "read": ("publish", "cmd_read"),
    "write": ("publish", "cmd_write"),
    "delete": ("publish", "cmd_delete"),
    "search": ("search", "cmd_search"),
    "export": ("publish", "cmd_export"),
    "qoder-plan": ("qoderplan", "cmd_qoder_plan"),
}


def _add_common(parser: argparse.ArgumentParser) -> None:
    """--repo/--root/--locale 双注册(qwiki bug9 修复模式)。

    SUPPRESS 默认值: 主解析器(子命令之前)给的值不被子解析器默认值覆盖
    (argparse 陷阱; ping 实跑 4 个并行 writer 全踩过参数顺序坑)。
    Ctx 用 getattr 兜底 ".", None, None。"""
    parser.add_argument("--repo", default=argparse.SUPPRESS,
                        help="目标仓库路径(默认 当前目录)")
    parser.add_argument("--root", default=argparse.SUPPRESS,
                        help=f"wiki 工作区根(默认 <repo>/{config.DEFAULT_ROOT_DIRNAME}; "
                             f".qoder/repowiki 可得 Qoder-IDE 可导入布局)")
    parser.add_argument("--locale", default=argparse.SUPPRESS,
                        help="内容语言(默认 zh)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pwiki",
        description="pwiki — 统一仓库 wiki 引擎(融合 qwiki/repowiki/wiki-plan-gen)",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {config.VERSION}")
    _add_common(parser)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def sp(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        _add_common(p)
        return p

    p = sp("init", "初始化工作区(config.json + wiki.db); 重跑=合并保留手工键")
    # default=None(P35): 省略 = 沿用现有 config.json 的值, 不再用默认值
    # 覆盖手工编辑; 首次 init 时 cmd_init 落默认
    p.add_argument("--profile", choices=config.PROFILE_NAMES, default=None)
    p.add_argument("--style", choices=config.STYLES, default=None)
    p.add_argument("--export-style", choices=config.EXPORT_STYLES, default=None)

    p = sp("scan", "确定性仓库扫描 → work/inventory.json")
    p.add_argument("--max-files", type=int, default=50000)
    p.add_argument("--ignore", action="append", default=[],
                   help="额外剪除的 repo 相对目录(可多次; 'src/generated' "
                        "只剪该处, 顶层裸名等价于顶层路径)")

    p = sp("modules", "模块分解 + D1 覆盖断言(写注册表进 plan+DB)")
    p.add_argument("--reset", action="store_true",
                   help="忽略 plan 现有 modules:, 重新推导(默认从 plan 起步)")

    p = sp("plan", "计划生成/更新(三路由: --seed | --themes | --layers)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--seed", action="store_true", help="qwiki 确定性种子目录")
    g.add_argument("--themes", action="store_true", help="repowiki 主题包规划")
    p.add_argument("--layers", choices=["init", "apply", "commit", "status"],
                   help="Qoder 分层递归规划器")
    p.add_argument("--of", default=None, help="layers apply: 指定父 slug")
    p.add_argument("--file", default=None, help="layers apply: <result> JSON 文件")
    p.add_argument("--sheets", choices=["none", "lite", "standard", "full"],
                   help="展开模块 sheet 页")
    p.add_argument("--deps", action="store_true", help="从 import 语句播种 depends_on")
    p.add_argument("--aliases", default=None, help="别名规则文件(缺省用 plan aliases:)")
    p.add_argument("--review", action="store_true",
                   help="再生 work/plan.md(主题包人工评审门)")
    p.add_argument("--reset", action="store_true",
                   help="允许覆盖手工编辑(默认绝不静默覆盖)")

    p = sp("digest", "共享摘要 work/repo-notes.md(活术语表从 DB 合并)")
    p.add_argument("--extra", action="append", default=[],
                   help="附加文件(D11: digest 会覆写自身输出, 前期笔记走这里)")

    p = sp("validate", "门禁: 默认 plan 校验(挡 begin); --content 发布树验证")
    p.add_argument("--warn-missing", action="store_true")
    p.add_argument("--content", action="store_true", help="发布树验证器(repowiki 模式)")
    p.add_argument("--fix", action="store_true",
                   help="机械修复(D18 章节来源/D23 钳位; 需配合 --content)")
    p.add_argument("--lenient", action="store_true",
                   help="错误降为警告显示(仅报告口径; begin 门禁不变)")

    p = sp("begin", "开跑(generate/FULL): 建 run + knowledge-first job 队列")
    p.add_argument("--force", action="store_true")
    p.add_argument("--missing", action="store_true",
                   help="恢复跑: 只排队未发布且未跳过的项")
    p.add_argument("--require-git", action="store_true")

    p = sp("next", "原子认领下一个 job(并行安全; --batch N 批量认领)")
    p.add_argument("--batch", type=int, default=1)
    p.add_argument("--json", action="store_true")

    p = sp("submit", "提交一页(门禁按 job 盖章的风格执行; 拒绝→pending 保稿)")
    p.add_argument("page_id")
    p.add_argument("--file", required=True, help="页面文件(- 读 stdin)")
    p.add_argument("--claim", default=None,
                   help="认领令牌(来自 next 输出; 并行 writer 必传 —— "
                        "失配即租约被回收, 走孤儿稿路径)")
    p.add_argument("--summary", default="")
    p.add_argument("--key-topics", default="")
    p.add_argument("--report", default=None,
                   help="writer 报告 JSON(D22 标题勘误/D7 卡片冲突/D14 负面断言)")
    p.add_argument("--lenient", action="store_true")
    p.add_argument("--force", action="store_true")

    p = sp("reconcile", "应用 writer 报告的和解项(D22 改名零 LLM 移动/D7 卡片重生成)")
    p.add_argument("--apply", type=int, default=None,
                   help="应用指定 reconcile id")
    p.add_argument("--all", action="store_true", help="应用全部 pending 项")
    p.add_argument("--dry-run", action="store_true")

    p = sp("fail", "如实标记失败")
    p.add_argument("page_id")
    p.add_argument("--error", required=True)
    p.add_argument("--claim", default=None,
                   help="并行 writer 断言自己的租约令牌(失配拒绝)")
    p.add_argument("--force", action="store_true",
                   help="确认撤销 completed 状态(默认拒绝)")

    p = sp("skip", "如实跳过")
    p.add_argument("page_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--claim", default=None,
                   help="并行 writer 断言自己的租约令牌(失配拒绝)")
    p.add_argument("--force", action="store_true",
                   help="确认撤销 completed 状态(默认拒绝)")

    p = sp("regenerate", "单项重做(新 run kind=regenerate)")
    p.add_argument("page_id")
    p.add_argument("--force", action="store_true",
                   help="有 writer 持租约时也强制重建(其稿件会走孤儿稿)")

    sp("pause", "暂停(run 阻塞 next; 有租约时拒绝)")
    sp("resume", "恢复")
    p = sp("cancel", "取消(释放所有认领)")
    p.add_argument("--force", action="store_true",
                   help="有 writer 持租约时也强制取消(其稿件会走孤儿稿)")

    p = sp("status", "运行状态/进度(Qoder 公式)/认领与租约视图")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")

    p = sp("finish", "LINING(按风格硬门禁)→发布→快照")
    p.add_argument("--lenient", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="同 --lenient: LINING 错误强过(submit 的拒绝提示所指)")
    p.add_argument("--prune", action="store_true",
                   help="全量清扫计划外孤儿 .md(默认只清理引擎自己移动/下架的旧路径, 手放笔记保留)")
    p.add_argument("--yes", action="store_true", help="跳过 D19 新根目录守卫")

    p = sp("update", "增量: 快照哈希/模块/prompt/目录/风格漂移 → INCREMENT")
    p.add_argument("--from-commit", default=None)

    p = sp("pages", "已发布条目清单(list_wiki)")
    p.add_argument("--tree", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sp("read", "读一条(read_wiki)")
    p.add_argument("page_id")
    p.add_argument("--meta", action="store_true")

    p = sp("write", "直接发布修正(edit_wiki; 同门禁, 刷新哈希基线)")
    p.add_argument("page_id")
    p.add_argument("--file", required=True)
    p.add_argument("--lenient", action="store_true")
    p.add_argument("--force", action="store_true")

    p = sp("delete", "删除条目/全库(delete_wiki / deleteIndex)")
    p.add_argument("page_id", nargs="?", default=None)
    p.add_argument("--all", action="store_true")

    p = sp("search", "BM25 检索已发布 wiki")
    p.add_argument("query")
    p.add_argument("-n", "--top", type=int, default=5)
    p.add_argument("--json", action="store_true")

    p = sp("export", "DB↔文件同步(db_to_file 自愈 / file_to_db 吸收)")
    p.add_argument("--direction", choices=["db_to_file", "file_to_db"],
                   required=True)
    p.add_argument("--force", action="store_true")

    p = sp("qoder-plan", "Qoder IDE wiki_plan.yaml 生成/校验(wiki-plan-gen 契约)")
    p.add_argument("--emit", default=None,
                   help="从 plan v3 生成到该路径(默认 .qoder/repowiki/wiki_plan.yaml)")
    p.add_argument("--lint", default=None, help="lint 现有文件(上限/静默修复预检)")

    return parser


def _resolve_handler(command: str):
    """按所有权表惰性导入; 模块未落地 → None(调用处给诚实的 P3 提示)。

    只把"命令所属模块本身缺失"当未落地 —— 模块**内部**的 ImportError
    (拼错的内部导入/半装环境)原样抛: 曾一律吞掉, 真实导入错误被伪装成
    "命令尚未实现"exit 3(engine.py 同款教训: 不可达的 ImportError 守卫
    只会把真实导入错误伪装成未落地)。"""
    owner = COMMAND_OWNERS.get(command)
    if not owner:
        return None
    module_name, func_name = owner
    full = f"{__package__}.{module_name}"
    try:
        module = __import__(full, fromlist=[func_name])
    except ImportError as e:
        if getattr(e, "name", None) and e.name not in (full, module_name):
            raise   # 模块内部 ImportError —— 不是"未落地"
        return None
    return getattr(module, func_name, None)


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse 用法错误自带 exit 2, 与文档契约 EXIT_GATE(2)=内容门禁
        # 相撞 —— 编排方按 2 会把 `--fil` 这类 typo 当内容缺陷重试/升级。
        # 用法错误是前置不满足, 归 3; --help 的 0 原样放行
        if e.code == 2:
            sys.exit(config.EXIT_PRECOND)
        raise
    if not args.command:
        parser.print_help()
        return config.EXIT_OK
    handler = _resolve_handler(args.command)
    if handler is None:
        print(f"pwiki: 命令 '{args.command}' 尚未实现(按阶段落地, 见 SKILL.md)",
              file=sys.stderr)
        return config.EXIT_PRECOND
    return handler(args)
