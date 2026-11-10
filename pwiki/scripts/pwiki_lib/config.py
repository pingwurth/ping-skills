"""pwiki 全局常量(单一事实源)。

所有阈值/上限/路径名/协议枚举集中于此, 其余模块只读引用, 禁止散落字面量。
行为准绳为 SKILL.md; 冲突时权威链: Schema > references > SKILL.md > LLM。

移植来源:
  - qwiki/scripts/qwiki.py:45-207 (状态模型/article 模型/目录上限/忽略表)
  - repowiki/scripts/plan_wiki.py PROFILES (target_min)
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

SKILL_NAME = "pwiki"

VERSION = "0.1.0"
DB_NAME = "wiki.db"
PLAN_NAME = "wiki_plan.yaml"
CONFIG_NAME = "config.json"
DIGEST_NAME = "repo-notes.md"
# 仓库级覆盖文件(标题映射等试点抽取物), 放在被分析仓库根, 不放 root
REPO_OVERRIDES_NAME = "pwiki.yaml"

# --------------------------------------------------------------------------- #
# 退出码契约(与 qwiki/crp 一致; install 后 CI 可依赖)
# --------------------------------------------------------------------------- #
EXIT_OK = 0        # 成功
EXIT_ENV = 1       # 环境错误(repo 不存在等)
EXIT_GATE = 2      # 门禁拒绝(validate 错误 / submit 拒绝 / finish 失败)
EXIT_PRECOND = 3   # 前置条件不满足(无活动 run / claim token 失配 / 未实现)

# --------------------------------------------------------------------------- #
# 风格档位(用户拍板: 双风格; 默认 deep)
# --------------------------------------------------------------------------- #
STYLES = ("concise", "deep")
DEFAULT_STYLE = "deep"
# 导出风格沿用 qwiki: legacy = 注入 <cite>/## 目录(Qoder 兼容); current = 原样
EXPORT_STYLES = ("legacy", "current")
# 默认 root: <repo>/.pwiki (--root .qoder/repowiki 可得 Qoder-IDE 可导入布局)
DEFAULT_ROOT_DIRNAME = ".pwiki"

# --------------------------------------------------------------------------- #
# 状态模型(evidence: RepoWikiService status enum in workbench.desktop.main.js)
# --------------------------------------------------------------------------- #
# 活动态集合: db.active_run 的 SQL IN 从这里派生(曾在 SQL 里另写一份, 两处
# 悄悄漂移)。DOCUMENT_FAILED 刻意留在活动语义里: lining 失败可修复后重新
# finish(qwiki 实测教训: 其余终态直接结束 run, 唯它可续)。
ACTIVE_RUN_STATES = ("WAITING", "INITIALIZING", "DIRECTORY_GENERATING",
                     "DOCUMENT_GENERATING", "LINING", "PAUSED", "DOCUMENT_FAILED")
TERMINAL_STATES = {"COMPLETED", "PARTLY_COMPLETED", "INITIALIZING_FAILED",
                   "DIRECTORY_FAILED", "DOCUMENT_FAILED", "NOT_SOURCE_CONTROL_FAILED",
                   "NO_COMMIT_HISTORY_FAILED", "CANCELLED"}
# 可取消态 = 活动态减 LINING(P8): finish 的 LINING/发布窗口内, 一切取消类
# 操作(begin/cancel/regenerate --force, pause)先被 c037 家族守卫挡住; 取消
# 写本身也带该集合前置 —— 守卫检查与 UPDATE 之间被 finish 抢先置 LINING
# 时, 守卫式 UPDATE 落空并如实报错, 不把发布中的 run 拖去 CANCELLED
CANCELABLE_RUN_STATES = tuple(s for s in ACTIVE_RUN_STATES if s != "LINING")
# catalog item status (evidence: Jmo() normalizer — pending/waiting/processing/completed/failed)
PAGE_STATES = ["pending", "processing", "completed", "failed", "skipped"]

# --------------------------------------------------------------------------- #
# profile 预算(qwiki PROFILE_BUDGETS + repowiki PROFILES.target_min 合并)
# --------------------------------------------------------------------------- #
PROFILE_BUDGETS = {"lite": 50, "standard": 135, "full": 180}
PROFILE_TARGETS = {"lite": 36, "standard": 105, "full": 150}
PROFILE_NAMES = ("lite", "standard", "full")

# --------------------------------------------------------------------------- #
# v0.4.0 catalogue/article 模型(evidence: Qoder go:embed prompts —
# wiki_plan_article_types @54350151, decision_tree_top @54520128,
# bad_outline @54418445)
# --------------------------------------------------------------------------- #
ARTICLE_TYPES = ["overview", "getting_started", "domain", "deep_dive",
                 "developer_guide"]
LEAF_TYPES = {"getting_started", "deep_dive", "developer_guide"}
# Qoder 每类型的结构用途清单(system prompt @55355200); 作者自拟本地化标题,
# 这里只约束"最少节数"(两种风格共用的形状门)。
ARTICLE_TYPE_SECTIONS = {
    "overview": 4,          # intro / architecture / capabilities / module map
    "getting_started": 4,   # prereq / setup / first run / workflows
    "domain": 5,            # intro / subdomain map / flows / decisions / children
    "deep_dive": 5,         # intro / architecture / components / data flow / files
    "developer_guide": 4,   # layout / build&test / conventions / common tasks
}

# 树形上限: caps 来自 Qoder Config; 观测输出 roots 9-17 / total 40-131 /
# children<=7 / depth<=3 (引擎允许 4)。
LIMIT_DEFAULTS = {
    "max_depth": 4,
    "max_top_articles": 20,
    "max_sub_per_parent": 8,
    "min_roots": 6,
    # 实测下限: Qoder 最小观测 wiki 40 篇/14 根; 25 篇样本里小仓下限 25。
    "min_total_articles": {"lite": 15, "standard": 40, "full": 60},
    "max_article_words": 3000,
}

# bad_outline 等价物 — validate 报告的结构反模式(泄漏分层的标题)。
ANTI_PATTERN_LEAKY_TITLE = ("controller", "repository", "service layer",
                            "dao", "mapper", "util", "utils", "helper",
                            "controller层", "仓储层", "工具类")

CARD_KINDS = ["business_term", "external_dependency", "configuration_system",
              "build_system", "dependency_management", "frontend_style",
              "logging_system", "error_handling"]


def is_card_kind(kind) -> bool:
    """卡片判定单一入口: plan 侧卡片带 kind=<卡片种类>(CARD_KINDS 之一),
    引擎侧 job/page 行带字面量 'card' —— 两形态都算卡片。
    全库判定一律走这里, 禁止裸 == 'card' 比较(曾致 update 误路由/卡片误判)。"""
    k = str(kind or "")
    return k == "card" or k in CARD_KINDS
# 体系类卡片: Qoder 观测标题是「体系名+构成要素」事实短语; business_term 与
# external_dependency 有各自的命名约定。
SYSTEM_CARD_KINDS = {"configuration_system", "build_system",
                     "dependency_management", "frontend_style",
                     "logging_system", "error_handling"}

# D15 宽引用带(80-120)的主题包 key(themes.build_packs 定义这些包;
# 新增/改名聚合类包时要同步这里 —— styles.cite_band_for 消费)
WIDE_CITE_PACK_KEYS = {"architecture", "ai", "api", "microservices"}

# --------------------------------------------------------------------------- #
# 扫描/模块分解共用的基础表(scan.py/modules.py 扩展各自专属表)
# --------------------------------------------------------------------------- #
DEFAULT_IGNORES = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", "out",
    "target", "venv", ".venv", "env", "__pycache__", ".idea", ".vscode",
    ".qoder", ".wiki", ".pwiki", ".next", ".nuxt", "coverage", ".cache",
    ".gradle", "Pods", "DerivedData", ".terraform", ".tox", ".mypy_cache",
    ".pytest_cache",
}
# 布局目录不是子域 —— modules.py 的 NOISE_DIRS 承担该职责(单一定义点)。
SOURCE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".java",
    ".kt", ".kts", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cc", ".hh",
    ".cs", ".rb", ".php", ".swift", ".m", ".mm", ".scala", ".sh", ".bash",
    ".zsh", ".lua", ".sql", ".proto", ".graphql", ".vue", ".svelte", ".astro",
}
DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
# 显示名/词干剥除用扩展名(单一事实源, 长度倒序防 '.ts' 误吃 '.mts'):
# themes.humanize 与 corpus.stem_of 曾各持一份手抄元组, 双双漂移 ——
# utils.mjs 人类化成 'Utils.mjs', 与 utils.js 撕成两页
STRIP_EXTS = tuple(sorted(SOURCE_EXTS, key=len, reverse=True))

# --------------------------------------------------------------------------- #
# 引用/文件名/mermaid 契约(gates.py 消费)
# --------------------------------------------------------------------------- #
# 路径组允许空格(仓库文件名可含空格, 曾 [^)\s#]+ 使带空格的引用对全部
# 门禁不可见); %20 形态由 gates.extract_citations 统一解码 —— 两种写法
# 都能合法引用, 发射侧统一 %20(与 markdown 渲染器兼容)。
CITE_RE = re.compile(
    r"\[(?P<label>[^\]]*)\]\(file://(?P<path>[^)#]+?)"
    r"(?:#L(?P<a>\d+)(?:-L?(?P<b>\d+))?)?\)"
)
ILLEGAL_FN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# c034: 计划条目 id 的路径安全规则 —— id 会进文件路径(孤儿稿/发布派生),
# 只查非空时含 / 或 ../ 的手编 id 可把写落点挪到 .pwiki 之外。只禁路径
# 分隔符/穿越段/前导点(中文 id 合法 —— 本工具 zh 优先, id 常为拼音/中文)
ID_RE_UNSAFE = re.compile(r"[/\\]|\.\.|^\.")
MERMAID_FIRST = re.compile(
    r"^(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram|stateDiagram-v2|"
    r"erDiagram|journey|gantt|pie|quadrantChart|requirementDiagram|gitGraph|"
    r"mindmap|timeline|sankey-beta|xychart-beta|block-beta|architecture-beta|"
    r"C4Context|C4Container|C4Component|C4Dynamic|C4Deployment|packet|rackDiagram)\b",
    re.I)

# --------------------------------------------------------------------------- #
# 并发认领(租约)
# --------------------------------------------------------------------------- #
DEFAULT_LEASE_MINUTES = 30          # LLM writer 慢, 默认宽租约
ORPHAN_SUBMIT_DIR = "orphan-submits"  # claim token 失配时的孤儿稿保存目录


# ISO 时间戳格式串(now_iso 与 iso_plus_minutes 共用 —— claim_jobs 按字符串
# 比较租约, 两处格式曾是复制粘贴, 漂移即"永不过期/永久过期")
_ISO_FMT = "%Y-%m-%dT%H:%M:%S."


def now_iso() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime(_ISO_FMT) + f"{dt.microsecond // 1000:03d}Z"


def iso_plus_minutes(base: str, minutes: int) -> str:
    dt = datetime.strptime(base, _ISO_FMT + "%fZ").replace(tzinfo=timezone.utc)
    end = dt + timedelta(minutes=minutes)
    return end.strftime(_ISO_FMT) + f"{end.microsecond // 1000:03d}Z"


def sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# 工作区解析(移植 qwiki Ctx; root 默认 <repo>/.pwiki)
# --------------------------------------------------------------------------- #

class Ctx:
    def __init__(self, args):
        from .util import die, git, info  # 局部导入避免环
        self._die, self._git, self._info = die, git, info
        self.repo = os.path.abspath(getattr(args, "repo", ".") or ".")
        if not os.path.isdir(self.repo):
            die(f"repo path not found: {self.repo}", EXIT_ENV)
        cfg_root = getattr(args, "root", None)
        if cfg_root:
            # P38: 显式相对 --root 一律锚定 repo(与默认根同基准) —— 曾按
            # 进程 CWD abspath, 同一个 '--root .pwiki' 键入与省略指向两棵
            # 不同的树, 状态静默分裂到两个 wiki.db
            _rel = cfg_root if os.path.isabs(cfg_root) \
                else os.path.join(self.repo, cfg_root)
            self.root = os.path.abspath(_rel)
        else:
            # 默认 <repo>/.pwiki; --root .qoder/repowiki 得 Qoder-IDE 兼容布局
            self.root = os.path.join(self.repo, DEFAULT_ROOT_DIRNAME)
        self.locale = getattr(args, "locale", None)
        self.db = None
        self.config = {}
        # 进程内缓存: branch/commit/partition 每次访问都 fork git / 写 DB,
        # 一个 135-job 轮次曾付出 ~400 次多余子进程
        self._branch = None
        self._commit = None
        self._partition = None
        self._file_hash_cache = {}

    def load(self, create: bool = True) -> "Ctx":
        if not create and not os.path.isfile(os.path.join(self.root, DB_NAME)):
            # P44: 只读命令不再顺手物化工作区 —— search/pages/read 在未
            # init 的仓库上试探, 曾把 wiki.db + partition 行写进分析目标
            self._die(f"工作区未初始化({self.root} 下无 {DB_NAME}) — "
                      "先 `pwiki init`(只读命令不创建工作区)", EXIT_PRECOND)
        cfg_path = os.path.join(self.root, CONFIG_NAME)
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, encoding="utf-8") as fh:
                    import json
                    self.config = json.load(fh)
                # 合法 JSON 但顶层不是映射(手改成 "deep"/数组): 曾放行到
                # 第一个 .get() 再裸 AttributeError —— c044 同款可诊断路径
                if not isinstance(self.config, dict):
                    self._info(f"warning: config.json 顶层必须是映射"
                               f"(got {type(self.config).__name__}) — "
                               f"本次按默认配置运行, 修好 {cfg_path} 后恢复")
                    self.config = {}
            except (OSError, ValueError) as e:
                # c044: 手改 config.json 的语法错此前静默重置 style/profile/
                # locale 等全部设置 —— 至少在 stderr 留下可诊断的一行
                self._info(f"warning: config.json 解析失败({e}) — "
                           f"本次按默认配置运行, 修好 {cfg_path} 后恢复")
                self.config = {}
        stored_repo = self.config.get("repo")
        if stored_repo and not os.path.isdir(stored_repo):
            self._info(f"note: configured repo path missing ({stored_repo}); "
                       f"using {self.repo}")
        if self.locale is None:
            # r7 #24: 显式 JSON null 走默认 —— config.get 的默认只管缺键,
            # locale=None 曾让 partition 的 SELECT WHERE locale=NULL 永不
            # 命中、INSERT OR IGNORE 被 NOT NULL 静默吸收返回 None, 首个
            # 消费者裸崩
            loc = self.config.get("locale")
            self.locale = loc if isinstance(loc, str) and loc else "zh"
        from .db import WikiDB, SchemaVersionError   # 惰性导入(db 导入本模块, 避免环)
        from .util import die                        # 局部导入避免环(同 __init__)
        try:
            self.db = WikiDB(self.root)
        except SchemaVersionError as e:
            # F47: 开库版本门禁 —— 前置失败给一行干净报错, 不留裸 traceback
            die(str(e), EXIT_PRECOND)
        return self

    @property
    def plan_path(self) -> str:
        return os.path.join(self.root, PLAN_NAME)

    @property
    def digest_path(self) -> str:
        return os.path.join(self.root, "work", DIGEST_NAME)

    @property
    def overrides_path(self) -> str:
        """仓库级覆盖(试点标题映射等), 放被分析仓库根。"""
        return os.path.join(self.repo, REPO_OVERRIDES_NAME)

    def file_sha(self, full_path: str) -> str:
        """进程内共享的文件哈希缓存((path,size,mtime) 键)—— publish 的
        generation-state 与 snapshot 的跟踪集曾对同一批文件各哈希一遍。"""
        try:
            st = os.stat(full_path)
            key = (full_path, st.st_size, int(st.st_mtime * 1000))
        except OSError:
            return ""
        hit = self._file_hash_cache.get(key)
        if hit is not None:
            return hit
        from .util import sha256_file
        h = sha256_file(full_path)
        self._file_hash_cache[key] = h
        return h

    @property
    def style(self) -> str:
        """风格档位: config 镜像, 以 plan 为准(planner 校验时警告失配)。"""
        return self.config.get("style", DEFAULT_STYLE)

    @property
    def branch(self) -> str:
        # r7 #13: git 失败的回退 '-' 不缓存 —— 裸目录(受支持路径,
        # --require-git 才是选通开关)首跑曾把全部 wiki 状态写进 ('-',
        # locale) 分区, git init + commit 后 branch 解析成真名 → 不同分区
        # id, 整个 wiki 静默消失。失败时每次重试(git rev-parse 足够便宜),
        # 成功才落缓存
        if self._branch is None:
            b = self._git(self.repo, "rev-parse", "--abbrev-ref", "HEAD")
            if b:
                self._branch = b
            return b or "-"
        return self._branch

    @property
    def commit(self) -> str:
        # 同 r7 #13: 失败回退 '' 不缓存(baseline_commit 曾因此永久为空)
        if self._commit is None:
            c = self._git(self.repo, "rev-parse", "HEAD")
            if c:
                self._commit = c
            return c or ""
        return self._commit

    def partition(self):
        if self._partition is None:
            self._partition = self.db.partition(self.branch, self.locale)
        return self._partition

    def content_dir(self) -> str:
        return os.path.join(self.root, self.locale, "content")

    def knowledge_dir(self) -> str:
        return os.path.join(self.root, "knowledge", self.locale)

    def meta_dir(self) -> str:
        return os.path.join(self.root, self.locale, "meta")

    def ensure_publish_tree(self) -> None:
        """发布根目录骨架落位(P1): init/begin/update 早期创建 content/
        knowledge/meta 三目录 —— D19 守卫只对"骨架之外的多级缺失"(根
        漂移/typo)触发。曾无任何生命周期步骤创建 <locale>/content, 全新
        workspace 的首次 finish 必须用未写入文档的 --yes, 否则退出码 3
        卡 LINING。幂等; 只读命令不调用(不物化 workspace)。"""
        for d in (self.content_dir(), self.knowledge_dir(), self.meta_dir()):
            os.makedirs(d, exist_ok=True)
