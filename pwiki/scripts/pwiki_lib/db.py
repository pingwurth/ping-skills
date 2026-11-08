"""数据库层(schema 以 Qoder wiki.v1.db 为蓝本, 本地 CLI 适配)。

移植自 qwiki/scripts/qwiki.py:497-912 并扩展:
  - schema v3: page_job/page 增风格与规划字段; module 增类型/骨架/证据;
    run 增 style; 新表 glossary(活术语) 与 reconcile(D7/D22 队列)。
  - 并发认领(头条修复, 取代 qwiki 的"reset all processing"协作约定):
    claim_jobs() 单 BEGIN IMMEDIATE 事务内先回收过期租约再原子认领,
    kind_rank/depth 在入队时预计算, 认领排序完全在 SQL 内完成。

事务模型: 连接 isolation_level=None(全自动提交); 单语句写天然原子,
多语句原子块(认领/成组发布)用显式 BEGIN IMMEDIATE ... COMMIT。
qwiki 风格的 conn.commit() 调用保留为无害空操作。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid

from . import config
from .config import now_iso
from .util import as_int

SCHEMA_VERSION = "v3"

# F47: 可隐式迁移的老库版本集合 —— SCHEMA 全程 CREATE TABLE IF NOT
# EXISTS + 附加列默认值, 老库 executescript 后即等效新库, 无数据改写
# (pwiki 史上只发过 v3 一个版本; 未来 bump SCHEMA_VERSION 且仍是无损
# 变更时, 把老版本加进本集合, 有损变更则要写真正的迁移步骤)。
IMPLICIT_SCHEMA_VERSIONS = frozenset({"v3"})


class SchemaVersionError(RuntimeError):
    """开库时 schema_version 不被本版本支持(F47 开库门禁)。"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS db_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS partition (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  branch                  TEXT NOT NULL,
  locale                  TEXT NOT NULL,
  published_snapshot_id   TEXT NOT NULL DEFAULT '',
  baseline_commit         TEXT NOT NULL DEFAULT '',
  baseline_plan_hash      TEXT NOT NULL DEFAULT '',
  baseline_modules_hash   TEXT NOT NULL DEFAULT '',
  baseline_run_id         TEXT NOT NULL DEFAULT '',
  created_at              TEXT NOT NULL,
  last_accessed_at        TEXT NOT NULL,
  UNIQUE(branch, locale)
);

CREATE TABLE IF NOT EXISTS run (
  id               TEXT PRIMARY KEY,
  partition_id     INTEGER NOT NULL,
  kind             TEXT NOT NULL,             -- generate | update | regenerate
  operation        TEXT NOT NULL DEFAULT '',  -- start | resume | retry | increment
  generation_type  TEXT NOT NULL DEFAULT 'FULL',  -- FULL | INCREMENT
  state            TEXT NOT NULL,
  stage            TEXT NOT NULL DEFAULT '',
  plan_hash        TEXT NOT NULL DEFAULT '',
  error            TEXT NOT NULL DEFAULT '',
  style            TEXT NOT NULL DEFAULT '',
  started_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  finished_at      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_log (
  run_id  TEXT NOT NULL,
  ts      TEXT NOT NULL,
  event   TEXT NOT NULL,
  detail  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS run_log_run ON run_log(run_id, ts);

CREATE TABLE IF NOT EXISTS page_job (
  run_id           TEXT NOT NULL,
  page_id          TEXT NOT NULL,
  title            TEXT NOT NULL,
  parent_id        TEXT NOT NULL DEFAULT '',
  ordinal          INTEGER NOT NULL DEFAULT 0,
  kind             TEXT NOT NULL DEFAULT 'page',   -- page | module_sheet | card
  module           TEXT NOT NULL DEFAULT '',
  sheet            TEXT NOT NULL DEFAULT '',
  card_kind        TEXT NOT NULL DEFAULT '',
  path             TEXT NOT NULL DEFAULT '',
  prompt           TEXT NOT NULL DEFAULT '',
  source_files     TEXT NOT NULL DEFAULT '[]',
  article_type     TEXT NOT NULL DEFAULT '',
  slug             TEXT NOT NULL DEFAULT '',
  objective        TEXT NOT NULL DEFAULT '',
  modules          TEXT NOT NULL DEFAULT '[]',
  summary          TEXT NOT NULL DEFAULT '',
  key_topics       TEXT NOT NULL DEFAULT '[]',
  state            TEXT NOT NULL DEFAULT 'pending',
  attempts         INTEGER NOT NULL DEFAULT 0,
  last_error       TEXT NOT NULL DEFAULT '',
  content          TEXT NOT NULL DEFAULT '',
  content_hash     TEXT NOT NULL DEFAULT '',
  notes            TEXT NOT NULL DEFAULT '',
  submitted_at     TEXT NOT NULL DEFAULT '',
  -- pwiki v3: 风格盖章 + 规划字段 + 并发认领
  style            TEXT NOT NULL DEFAULT '',
  scope            TEXT NOT NULL DEFAULT '[]',
  required_sections TEXT NOT NULL DEFAULT '[]',
  title_check      INTEGER NOT NULL DEFAULT 0,
  pack_key         TEXT NOT NULL DEFAULT '',
  report           TEXT NOT NULL DEFAULT '',
  kind_rank        INTEGER NOT NULL DEFAULT 2,   -- module_sheet=0 card=1 page=2
  depth            INTEGER NOT NULL DEFAULT 0,   -- 页树深度(入队时预计算)
  claim_token      TEXT NOT NULL DEFAULT '',
  claimed_at       TEXT NOT NULL DEFAULT '',
  lease_expires_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(run_id, page_id)
);
CREATE INDEX IF NOT EXISTS page_job_claim
  ON page_job(run_id, state, kind_rank, depth, ordinal, page_id);

CREATE TABLE IF NOT EXISTS page (
  partition_id  INTEGER NOT NULL,
  page_id       TEXT NOT NULL,
  title         TEXT NOT NULL,
  parent_id     TEXT NOT NULL DEFAULT '',
  ordinal       INTEGER NOT NULL DEFAULT 0,
  kind          TEXT NOT NULL DEFAULT 'page',
  module        TEXT NOT NULL DEFAULT '',
  sheet         TEXT NOT NULL DEFAULT '',
  card_kind     TEXT NOT NULL DEFAULT '',
  path          TEXT NOT NULL DEFAULT '',
  prompt        TEXT NOT NULL DEFAULT '',
  source_files  TEXT NOT NULL DEFAULT '[]',
  article_type  TEXT NOT NULL DEFAULT '',
  slug          TEXT NOT NULL DEFAULT '',
  objective     TEXT NOT NULL DEFAULT '',
  modules       TEXT NOT NULL DEFAULT '[]',
  summary       TEXT NOT NULL DEFAULT '',
  key_topics    TEXT NOT NULL DEFAULT '[]',
  content       TEXT NOT NULL DEFAULT '',
  content_hash  TEXT NOT NULL DEFAULT '',
  run_id        TEXT NOT NULL DEFAULT '',
  updated_at    TEXT NOT NULL,
  -- pwiki v3
  style         TEXT NOT NULL DEFAULT '',
  scope         TEXT NOT NULL DEFAULT '[]',
  required_sections TEXT NOT NULL DEFAULT '[]',
  title_check   INTEGER NOT NULL DEFAULT 0,
  pack_key      TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(partition_id, page_id)
);

CREATE TABLE IF NOT EXISTS module (
  partition_id  INTEGER NOT NULL,
  module_id     TEXT NOT NULL,
  title         TEXT NOT NULL,
  dir           TEXT NOT NULL DEFAULT '',
  scope         TEXT NOT NULL DEFAULT '[]',
  depends_on    TEXT NOT NULL DEFAULT '[]',
  related_to    TEXT NOT NULL DEFAULT '[]',
  parent_id     TEXT NOT NULL DEFAULT '',
  path          TEXT NOT NULL DEFAULT '',
  notes         TEXT NOT NULL DEFAULT '',
  -- pwiki v3: D8 骨架/类型 + repowiki 证据
  module_type   TEXT NOT NULL DEFAULT 'real',   -- real | group | container | skeleton
  skeleton      INTEGER NOT NULL DEFAULT 0,
  evidence      TEXT NOT NULL DEFAULT '{}',
  updated_at    TEXT NOT NULL,
  PRIMARY KEY(partition_id, module_id)
);

CREATE TABLE IF NOT EXISTS file_hash (
  partition_id INTEGER NOT NULL,
  path         TEXT NOT NULL,
  sha256       TEXT NOT NULL,
  size         INTEGER NOT NULL DEFAULT 0,
  mtime        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(partition_id, path)
);

-- pwiki v3: 活术语表(submit --key-topics 累积, digest 从这里再生 —— 修
-- repowiki 静态 digest: 后到的 writer 也能看到先到 writer 的术语)
CREATE TABLE IF NOT EXISTS glossary (
  partition_id  INTEGER NOT NULL,
  term          TEXT NOT NULL,
  definition    TEXT NOT NULL DEFAULT '',
  source_page_id TEXT NOT NULL DEFAULT '',
  updated_at    TEXT NOT NULL,
  PRIMARY KEY(partition_id, term)
) WITHOUT ROWID;

-- pwiki v3: D7/D22 和解队列(writer 报告 → 结构化待办, 主 agent 决策)
CREATE TABLE IF NOT EXISTS reconcile (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  partition_id INTEGER NOT NULL,
  run_id       TEXT NOT NULL DEFAULT '',
  kind         TEXT NOT NULL,          -- title_mismatch | card_conflict
  payload      TEXT NOT NULL DEFAULT '{}',
  state        TEXT NOT NULL DEFAULT 'pending',  -- pending | applied | rejected
  created_at   TEXT NOT NULL,
  resolved_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS reconcile_pending ON reconcile(partition_id, state);
"""

# 并发认领的排序键(knowledge-first + 自底向上; 与 qwiki next_pending 一致)
KIND_RANK = {"module_sheet": 0, "card": 1, "page": 2}

# page_job 的重列(content/report 每行 10-30KB markdown): 状态机/计数类
# 消费方用 jobs_meta, 只有用稿体的路径(finish LINING/publish)才 jobs()
JOB_META_COLS = ("run_id,page_id,title,parent_id,ordinal,kind,module,sheet,"
                 "card_kind,path,prompt,source_files,article_type,slug,"
                 "objective,modules,summary,key_topics,state,attempts,"
                 "last_error,notes,submitted_at,style,scope,"
                 "required_sections,title_check,pack_key,kind_rank,depth,"
                 "claim_token,claimed_at,lease_expires_at")

# active_run 的活动态集合从 config 派生(单一事实源; 曾在 SQL 里硬编码漂移)
_ACTIVE_RUN_IN = ",".join(f"'{s}'" for s in config.ACTIVE_RUN_STATES)


class WikiDB:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.path = os.path.join(self.root, config.DB_NAME)
        os.makedirs(self.root, exist_ok=True)
        need_init = not os.path.isfile(self.path)
        self.conn = sqlite3.connect(self.path, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.isolation_level = None   # 自动提交; 原子块显式 BEGIN IMMEDIATE
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        # 与 connect(timeout=10.0) 同刻度: 5000 曾把 10s 等锁压回 5s ——
        # 两行自相矛盾, 并发 writer 在 finish 的多语句事务下提前
        # 'database is locked'
        self.conn.execute("PRAGMA busy_timeout=10000")
        # 嵌套事务深度(0=无事务): 见 begin() 的嵌套感知说明
        self._txn_depth = 0
        self.conn.executescript(SCHEMA)
        if need_init:
            self.meta_set("schema_version", SCHEMA_VERSION)
            self.meta_set("generator_version", config.VERSION)
        else:
            # F47: schema_version 曾是只写簿记(打完戳零人读, 暗示着不存在
            # 的迁移门)—— 接成真开库校验
            self._check_schema_version()

    def _check_schema_version(self) -> None:
        """F47: 已有库的 schema_version 开库门禁。

        相等 → 放行; ∈ 可隐式迁移集合(或无戳 —— db_meta 引入前的老库)
        → 放行并把戳/生成器版本带到当前(隐式迁移已由 executescript 完成);
        未来/未知版本(本工具更老, 库由更新版本写过)→ 明确报错, 不静默
        误读别代 schema。"""
        stamp = self.meta_get("schema_version")
        if stamp == SCHEMA_VERSION:
            return
        if not stamp or stamp in IMPLICIT_SCHEMA_VERSIONS:
            self.meta_set("schema_version", SCHEMA_VERSION)
            self.meta_set("generator_version", config.VERSION)
            return
        raise SchemaVersionError(
            f"{self.path} 的 schema_version={stamp!r} 不被本版本支持"
            f"(当前 {SCHEMA_VERSION}, 可隐式迁移: "
            f"{'/'.join(sorted(IMPLICIT_SCHEMA_VERSIONS))}) — "
            "该库可能由更新版本的 pwiki 写入, 请用对应版本打开")

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # -- 显式事务助手 -------------------------------------------------------
    # 嵌套感知(F36): 外层 BEGIN IMMEDIATE, 内层 SAVEPOINT —— publish_run
    # 把 kept/completed 重发布 + 下架 + 模块同步包进单事务, 其间调用的
    # unpublish_pages/publish_modules 等自身也管事务(它们独立调用时行为
    # 不变)。嵌套 rollback 一律整笔回滚并清零深度: 既有调用点 rollback 后
    # 必然向上抛, 外层 except 再 rollback 是幂等 no-op。
    def begin(self) -> None:
        self._txn_depth += 1
        if self._txn_depth == 1:
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT pwiki_sp{self._txn_depth}")

    def commit(self) -> None:
        if not self.conn.in_transaction:
            # 事务已不在(底层错误自动回滚/裸 ROLLBACK)—— 深度随之清零
            self._txn_depth = 0
            return
        if self._txn_depth > 1:
            self.conn.execute(f"RELEASE SAVEPOINT pwiki_sp{self._txn_depth}")
            self._txn_depth -= 1
            return
        self._txn_depth = 0
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        self._txn_depth = 0
        if self.conn.in_transaction:
            self.conn.execute("ROLLBACK")

    # -- meta ---------------------------------------------------------------
    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO db_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def meta_get(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM db_meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # -- partition ----------------------------------------------------------
    def partition(self, branch: str, locale: str, create: bool = True):
        row = self.conn.execute(
            "SELECT * FROM partition WHERE branch=? AND locale=?",
            (branch, locale)).fetchone()
        if row:
            self.conn.execute("UPDATE partition SET last_accessed_at=? WHERE id=?",
                              (now_iso(), row["id"]))
            return row
        if not create:
            return None
        # 并发首建: OR IGNORE 吸收竞态(两个首跑 begin 曾有一个裸崩
        # IntegrityError), 输家统一回读到赢家的行
        self.conn.execute(
            "INSERT OR IGNORE INTO partition(branch,locale,created_at,"
            "last_accessed_at) VALUES(?,?,?,?)",
            (branch, locale, now_iso(), now_iso()))
        return self.conn.execute(
            "SELECT * FROM partition WHERE branch=? AND locale=?",
            (branch, locale)).fetchone()

    def set_baseline(self, pid: int, snapshot_id: str, commit: str, plan_hash: str,
                     modules_hash: str, run_id: str) -> None:
        self.conn.execute(
            "UPDATE partition SET published_snapshot_id=?, baseline_commit=?, "
            "baseline_plan_hash=?, "
            "baseline_modules_hash=?, baseline_run_id=?, last_accessed_at=? "
            "WHERE id=?",
            (snapshot_id, commit, plan_hash, modules_hash, run_id,
             now_iso(), pid))

    # -- runs ---------------------------------------------------------------
    def _insert_run(self, pid: int, run_id: str, kind: str, operation: str,
                    generation_type: str, state: str, commit: str,
                    plan_hash: str, config_: dict, style: str) -> None:
        """run INSERT + run_log 的单一实现(create_run 与守卫式孪生共用
        —— 两份逐字拷贝曾各自漂移, schema 改一处漏一处测试仍绿)。
        commit/config_ 形参保留(调用方契约稳定), 不再入库 ——
        run.target_commit/config_json 是只写无人读的死列(r4 #68)。"""
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO run(id,partition_id,kind,operation,generation_type,state,"
            "stage,plan_hash,style,started_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, pid, kind, operation, generation_type, state, "init",
             plan_hash, style, ts, ts))
        self.conn.execute(
            "INSERT INTO run_log(run_id,ts,event,detail) VALUES(?,?,?,?)",
            (run_id, ts, "run_created",
             f"kind={kind} operation={operation} type={generation_type} style={style}"))

    def create_run(self, pid: int, kind: str, operation: str, generation_type: str,
                   state: str, commit: str, plan_hash: str, config_: dict,
                   style: str = "") -> str:
        """无守卫直建(测试/单进程确定性路径用)。产品调用方全部走
        create_run_guarded(c036: 检查+插入同事务, 并发 begin 不叠 run)。"""
        run_id = uuid.uuid4().hex[:12]
        self.begin()
        try:
            self._insert_run(pid, run_id, kind, operation, generation_type,
                             state, commit, plan_hash, config_, style)
            self.commit()
        except Exception:
            self.rollback()
            raise
        return run_id

    def create_run_guarded(self, pid: int, kind: str, operation: str,
                           generation_type: str, state: str, commit: str,
                           plan_hash: str, config_: dict,
                           style: str = "") -> tuple:
        """并发安全建 run: 单 BEGIN IMMEDIATE 内先查活动 run 再插入。

        c036 回修: active_run 检查与 create_run 曾分属两个事务 —— 两个并发
        begin 都看到"无活动 run"后各建一个, 留下双活动 run(40 次并发试验
        35 次命中), 僵尸 run 经"续用"复活整轮双倍生成。写锁串行化后,
        输家拿到赢家刚建的 run。返回 (run_id, None) 或 (None, active_row)。
        regenerate/reconcile 等显式取消旧 run 后的重建同样走这里 ——
        若在取消与重建之间有并发 begin 插入, 如实返回而不是叠 run。"""
        run_id = uuid.uuid4().hex[:12]
        self.begin()
        try:
            rows = self.conn.execute(
                f"SELECT * FROM run WHERE partition_id=? AND state IN "
                f"({_ACTIVE_RUN_IN}) ORDER BY started_at DESC",
                (pid,)).fetchall()
            if rows:
                self.rollback()
                return None, rows[0]
            self._insert_run(pid, run_id, kind, operation, generation_type,
                             state, commit, plan_hash, config_, style)
            self.commit()
        except Exception:
            self.rollback()
            raise
        return run_id, None

    def update_run(self, run_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE run SET {sets} WHERE id=?",
                          (*fields.values(), run_id))

    def update_run_guarded(self, run_id: str, expected_states,
                           **fields) -> bool:
        """带状态前置条件的 run 转移(c022): 仅当 run 当前处于 expected_states
        之一才应用 fields; 返回是否生效(rowcount=0 = 状态已被并发方改走)。

        finish 的 LINING 置位曾是无守卫 UPDATE —— 与并发 cancel/regenerate
        --force 的 CANCELLED 写互相盲覆盖, 把已取消的 run 复活成 LINING
        (LINING ∈ 活动态) → 双活动 run, 僵尸 run 永卡 LINING。"""
        if not fields:
            return True
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        in_ph = ",".join("?" * len(expected_states))
        cur = self.conn.execute(
            f"UPDATE run SET {sets} WHERE id=? AND state IN ({in_ph})",
            (*fields.values(), run_id, *expected_states))
        return cur.rowcount > 0

    def active_run(self, pid: int):
        # 活动态集合 = config.ACTIVE_RUN_STATES(DOCUMENT_FAILED 刻意留在可
        # 恢复集合: 修复被标记项 → 重新 submit → finish 重新 lining)。
        rows = self.conn.execute(
            f"SELECT * FROM run WHERE partition_id=? AND state IN "
            f"({_ACTIVE_RUN_IN}) ORDER BY started_at DESC",
            (pid,)).fetchall()
        return rows[0] if rows else None

    def last_run(self, pid: int):
        rows = self.conn.execute(
            "SELECT * FROM run WHERE partition_id=? ORDER BY started_at DESC LIMIT 1",
            (pid,)).fetchall()
        return rows[0] if rows else None

    def skipped_page_ids(self, pid: int) -> set:
        """全部分区 run 里被显式 skip 的 page_id(begin --missing 的跳过集)。

        只看 last_run 曾把 update/regenerate 产生的 INCREMENT run 之后被跳过
        的页面复活重排 —— 跳过是用户决定, 任何后续 run 都不该翻案。"""
        rows = self.conn.execute(
            "SELECT DISTINCT j.page_id FROM page_job j "
            "JOIN run r ON j.run_id=r.id "
            "WHERE r.partition_id=? AND j.state='skipped'", (pid,)).fetchall()
        return {r["page_id"] for r in rows}

    def log(self, run_id: str, event: str, detail: str = "") -> None:
        # 单 CLI 进程 = 单日志行; 自动提交模式下逐条落盘, 进程中途死亡不丢审计。
        self.conn.execute("INSERT INTO run_log(run_id,ts,event,detail) VALUES(?,?,?,?)",
                          (run_id, now_iso(), event, detail))

    # -- page jobs ----------------------------------------------------------
    def queue_jobs(self, run_id: str, items: list, style: str = "") -> int:
        """成队入列: 预计算 kind_rank/depth(knowledge-first, 页自底向上),
        使认领排序完全在 SQL 内完成。单事务保证 run 队列原子成型。

        卡片规范化在这一个咽喉点完成(plan 卡 kind=<种类> → 行 kind='card'
        + card_kind + ordinal 950)—— 该字典字面量曾四处分拷, snapshot 的
        那份漏了 ordinal、自己重推 card_kind, 漂移出过伪页面事故。"""
        parent_of = {it["id"]: it.get("parent", "") for it in items}
        id_set = set(parent_of)

        def depth(it: dict) -> int:
            d, cur, guard = 0, it.get("parent", ""), 0
            while cur and cur in id_set and guard < 12:
                cur = parent_of.get(cur, "")
                d += 1
                guard += 1
            return d

        self.begin()
        try:
            for it in items:
                it = dict(it)
                kind = it.get("kind", "page")
                if config.is_card_kind(kind):
                    it.setdefault("card_kind", kind)
                    # 显式 None(yaml `ordinal:` 空值)不被 setdefault 替换,
                    # 曾以 None 入库后在多个 int() 位点裸崩 —— 归一到 950
                    it["ordinal"] = as_int(it.get("ordinal"), 950)
                    it["kind"] = "card"
                    kind = "card"
                self._insert_job(run_id, it, kind,
                                 KIND_RANK.get(kind, 3), depth(it), style)
            self.commit()
        except Exception:
            self.rollback()
            raise
        return len(items)

    def _insert_job(self, run_id: str, item: dict, kind: str,
                    kind_rank: int, depth: int, style: str) -> None:
        # 列序(24): run_id..modules(16 ?) + state('pending') + style..depth(7 ?)
        self.conn.execute(
            "INSERT OR REPLACE INTO page_job("
            "run_id,page_id,title,parent_id,ordinal,kind,module,sheet,card_kind,"
            "path,prompt,source_files,article_type,slug,objective,modules,"
            "state,style,scope,required_sections,title_check,pack_key,"
            "kind_rank,depth) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?)",
            (run_id, item["id"], item.get("title", item["id"]),
             item.get("parent", ""), as_int(item.get("ordinal"), 0),
             kind, item.get("module", ""), item.get("sheet", ""),
             item.get("card_kind", ""), item.get("path", ""),
             item.get("prompt", ""),
             # `source_files: null` 等空值曾入库为 'null'(truthy 串), 绕过
             # 消费方全部 `json.loads(x or '[]')` 守卫, next/submit 裸
             # join/迭代即崩 —— 入库前归一 []
             json.dumps(item.get("source_files") or [], ensure_ascii=False),
             item.get("type", ""), item.get("slug", ""),
             item.get("objective", ""),
             json.dumps(item.get("modules") or [], ensure_ascii=False),
             style or item.get("style", ""),
             json.dumps(item.get("scope") or [], ensure_ascii=False),
             json.dumps(item.get("required_sections") or [],
                        ensure_ascii=False),
             1 if item.get("title_check") else 0,
             item.get("pack_key", ""), kind_rank, depth))

    def jobs(self, run_id: str):
        return self.conn.execute(
            "SELECT * FROM page_job WHERE run_id=? "
            "ORDER BY CASE parent_id WHEN '' THEN 0 ELSE 1 END, ordinal, page_id",
            (run_id,)).fetchall()

    def jobs_meta(self, run_id: str):
        """同 jobs() 但不拖 content/report(next/begin --missing 等元数据
        消费方; 一个 135-job run 的逐个 next 曾白读数百 MB 稿体)。"""
        return self.conn.execute(
            f"SELECT {JOB_META_COLS} FROM page_job WHERE run_id=? "
            "ORDER BY CASE parent_id WHEN '' THEN 0 ELSE 1 END, ordinal, page_id",
            (run_id,)).fetchall()

    def job(self, run_id: str, page_id: str):
        return self.conn.execute(
            "SELECT * FROM page_job WHERE run_id=? AND page_id=?",
            (run_id, page_id)).fetchone()

    # -- 并发认领(头条修复) ---------------------------------------------------
    def claim_jobs(self, run_id: str, count: int = 1,
                   lease_minutes: int = config.DEFAULT_LEASE_MINUTES) -> list:
        """原子认领至多 count 个 pending job。

        单 BEGIN IMMEDIATE 事务: (1) 先回收过期租约(死 writer 复活, 只在此处
        回收, 永不定时触发); (2) UPDATE ... WHERE rowid=(SELECT ... LIMIT 1)
        AND state='pending' 逐个认领 —— select-then-update 由构造保证原子。
        并发 next 串行化在写锁上: 第二个调用看到第一个的 processing 行,
        认领下一个 pending —— 不重发、不互踩(qwiki 协作约定的根治)。"""
        now = now_iso()
        expires = config.iso_plus_minutes(now, lease_minutes)
        claimed = []
        self.begin()
        try:
            self.conn.execute(
                "UPDATE page_job SET state='pending', claim_token='', "
                "last_error='lease_expired_reclaimed' "
                "WHERE run_id=? AND state='processing' AND lease_expires_at!='' "
                "AND lease_expires_at<?", (run_id, now))
            for _ in range(max(1, count)):
                token = uuid.uuid4().hex
                cur = self.conn.execute(
                    "UPDATE page_job SET state='processing', attempts=attempts+1, "
                    "claim_token=?, claimed_at=?, lease_expires_at=? "
                    "WHERE rowid=(SELECT rowid FROM page_job "
                    "             WHERE run_id=? AND state='pending' "
                    "             ORDER BY kind_rank, depth DESC, ordinal, page_id "
                    "             LIMIT 1) AND state='pending'",
                    (token, now, expires, run_id))
                if cur.rowcount == 0:
                    break
                row = self.conn.execute(
                    "SELECT * FROM page_job WHERE run_id=? AND claim_token=?",
                    (run_id, token)).fetchone()
                claimed.append(row)
            self.commit()
        except Exception:
            self.rollback()
            raise
        return claimed

    def match_claim(self, run_id: str, page_id: str, token: str) -> bool:
        row = self.job(run_id, page_id)
        return bool(row) and row["state"] == "processing" \
            and row["claim_token"] == token

    def leased_jobs(self, run_id: str):
        # 消费方(status/pause/cancel/regenerate)只读 page_id 与租约时间
        # —— SELECT * 曾为每个在写行拖 10-30KB 稿体
        return self.conn.execute(
            "SELECT page_id, lease_expires_at FROM page_job "
            "WHERE run_id=? AND state='processing' AND claim_token!='' "
            "ORDER BY claimed_at", (run_id,)).fetchall()

    def release_all_claims(self, run_id: str) -> int:
        """cancel 用: 释放所有认领(回 pending)。"""
        cur = self.conn.execute(
            "UPDATE page_job SET state='pending', claim_token='', claimed_at='', "
            "lease_expires_at='' WHERE run_id=? AND state='processing'",
            (run_id,))
        return cur.rowcount

    def set_job_state(self, run_id: str, page_id: str, state: str,
                      error: str = "", content: str = "", notes: str = "",
                      claim_token: str = "") -> bool:
        """更新 job 状态。给出 claim_token 时必须匹配当前认领(不匹配 →
        False, 调用方走孤儿稿路径); attempts 只在 claim_jobs 里递增,
        这里不再加(qwiki 在 processing 转移时加, 我们把认领即视为一次尝试)。"""
        if claim_token:
            if not self.match_claim(run_id, page_id, claim_token):
                return False
            where = "run_id=? AND page_id=? AND claim_token=?"
            params = [run_id, page_id, claim_token]
        else:
            where = "run_id=? AND page_id=?"
            params = [run_id, page_id]
        cur = self.conn.execute(
            f"UPDATE page_job SET state=?, last_error=?, "
            f"content=CASE WHEN ?!='' THEN ? ELSE content END, "
            f"content_hash=CASE WHEN ?!='' THEN ? ELSE content_hash END, "
            f"notes=CASE WHEN ?!='' THEN ? ELSE notes END, "
            f"submitted_at=CASE WHEN ? IN ('completed','failed','skipped') THEN ? "
            f"ELSE submitted_at END, claim_token='', claimed_at='', "
            f"lease_expires_at='' WHERE {where}",
            (state, error, content, content,
             content, config.sha256_bytes(content.encode()) if content else "",
             notes, notes, state, now_iso(), *params))
        if cur.rowcount == 0:
            # claim 分支: 令牌已在 WHERE 里, rowcount==0 = 在 match_claim 与
            # UPDATE 之间被换走(租约回收/重认领竞态) —— 如实报失配, 调用方
            # 走孤儿稿路径, 稿件不静默丢。no-token 分支: 0 行 = job 不存在
            # —— 不再谎报成功(c022/c042: 幽灵 id 曾 exit 0 还污染 run_log)
            return False
        return True

    def set_job_report(self, run_id: str, page_id: str, report: dict) -> None:
        self.conn.execute(
            "UPDATE page_job SET report=? WHERE run_id=? AND page_id=?",
            (json.dumps(report, ensure_ascii=False), run_id, page_id))

    def complete_job_guarded(self, run_id: str, page_id: str, content: str,
                             claim_token: str = "") -> str:
        """submit 终写: 单 BEGIN IMMEDIATE 内核 run 状态再写 completed。

        finish 的 LINING 置位与 jobs() 快照之间曾是无守卫窗口 —— 迟到的
        submit 在快照后把 job 翻成 completed, finish 拿旧快照发布并报 run
        完成, 该页从未落盘(稿在 DB, published_page 返回 None)。事务使
        "检查+终写"与 finish 的 UPDATE run 串行化: 要么先于 LINING 提交
        (快照看得到), 要么在此看到 LINING/终态而拒收(调用方走孤儿稿)。
        返回 ok | busy | token_lost。"""
        self.begin()
        try:
            row = self.conn.execute(
                "SELECT state FROM run WHERE id=?", (run_id,)).fetchone()
            if row is not None and str(row["state"]) in (
                    "LINING", "COMPLETED", "PARTLY_COMPLETED"):
                self.rollback()
                return "busy"
            if not claim_token:
                # c002: 无 token 路径(单写便利/租约过期)的 job 级守卫 ——
                # re-read 与终写之间落进的新认领(state=processing 且持新
                # token)不得被无条件 completed + 清租约顶掉(新 writer 的
                # 合法工作会沦为孤儿稿)。有活租约在 → token_lost 走孤儿。
                job = self.conn.execute(
                    "SELECT state, claim_token FROM page_job "
                    "WHERE run_id=? AND page_id=?",
                    (run_id, page_id)).fetchone()
                if job is not None and str(job["state"]) == "processing" \
                        and str(job["claim_token"] or ""):
                    self.rollback()
                    return "token_lost"
            ok = self.set_job_state(run_id, page_id, "completed",
                                    content=content, claim_token=claim_token)
            self.commit()
            return "ok" if ok else "token_lost"
        except Exception:
            self.rollback()
            raise

    def pending_counts(self, run_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) AS n FROM page_job WHERE run_id=? "
            "GROUP BY state", (run_id,)).fetchall()
        counts = {s: 0 for s in config.PAGE_STATES}
        for r in rows:
            counts[r["state"]] = r["n"]
        return counts

    # -- published pages ------------------------------------------------------
    def publish_page(self, pid: int, job_row, content: str, run_id: str) -> None:
        """把一个 completed job 发布进 page 表(v3 全字段)。"""
        cols = ("partition_id,page_id,title,parent_id,ordinal,kind,module,sheet,"
                "card_kind,path,prompt,source_files,article_type,slug,objective,"
                "modules,summary,key_topics,content,content_hash,run_id,updated_at,"
                "style,scope,required_sections,title_check,pack_key")
        self.conn.execute(
            f"INSERT OR REPLACE INTO page({cols}) "
            f"VALUES({','.join('?' * len(cols.split(',')))})",
            (pid, job_row["page_id"], job_row["title"], job_row["parent_id"],
             job_row["ordinal"], job_row["kind"], job_row["module"],
             job_row["sheet"], job_row["card_kind"], job_row["path"],
             job_row["prompt"], job_row["source_files"], job_row["article_type"],
             job_row["slug"], job_row["objective"], job_row["modules"],
             job_row["summary"], job_row["key_topics"], content,
             config.sha256_bytes(content.encode()), run_id, now_iso(),
             job_row["style"], job_row["scope"], job_row["required_sections"],
             job_row["title_check"], job_row["pack_key"]))

    def published_pages(self, pid: int):
        return self.conn.execute(
            "SELECT * FROM page WHERE partition_id=? "
            "ORDER BY CASE parent_id WHEN '' THEN 0 ELSE 1 END, ordinal, page_id",
            (pid,)).fetchall()

    def published_pages_in(self, pid: int, page_ids: list) -> list:
        """按 id 集合取发布行(c050: 改名级联曾对每个后代各发一条 SELECT,
        顶层页改名 = 数百次顺序查询 + 逐行拖整页稿体)。"""
        ids = [str(i) for i in page_ids if i]
        if not ids:
            return []
        ph = ",".join("?" * len(ids))
        return self.conn.execute(
            f"SELECT * FROM page WHERE partition_id=? AND page_id IN ({ph})",
            (pid, *ids)).fetchall()

    def set_page_style(self, pid: int, page_id: str, style: str) -> None:
        """重盖章已发布行的 style(c005: update 的 style-drift 对过检行收敛)。"""
        self.conn.execute(
            "UPDATE page SET style=? WHERE partition_id=? AND page_id=?",
            (style, pid, page_id))

    def published_paths(self, pid: int) -> set:
        """只要路径集合(手工 write 的链接改写等轻消费方 —— 全列 SELECT *
        曾为每个文件名拖出整库稿体)。"""
        rows = self.conn.execute(
            "SELECT path FROM page WHERE partition_id=?", (pid,)).fetchall()
        return {r["path"] for r in rows}

    def published_ids(self, pid: int) -> set:
        """只要 id 集合(begin --missing 的已发布判定 —— published_pages
        曾为建一个 id 集合拖出整库稿体)。"""
        rows = self.conn.execute(
            "SELECT page_id FROM page WHERE partition_id=?", (pid,)).fetchall()
        return {r["page_id"] for r in rows}

    def published_kind_counts(self, pid: int) -> dict:
        rows = self.conn.execute(
            "SELECT kind, COUNT(*) AS n FROM page WHERE partition_id=? "
            "GROUP BY kind", (pid,)).fetchall()
        return {r["kind"]: r["n"] for r in rows}

    def published_page(self, pid: int, page_id: str):
        return self.conn.execute(
            "SELECT * FROM page WHERE partition_id=? AND page_id=?",
            (pid, page_id)).fetchone()

    def save_page_content(self, pid: int, page_id: str, content: str) -> None:
        """page.content 落库唯一入口(哈希/时间戳封装)—— write/export/reconcile
        三处曾各自复制粘贴裸 UPDATE, 漏改一处即产生 content_hash 永久漂移。"""
        self.conn.execute(
            "UPDATE page SET content=?, content_hash=?, updated_at=? "
            "WHERE partition_id=? AND page_id=?",
            (content, config.sha256_bytes(content.encode()), now_iso(),
             pid, page_id))

    def unpublish_pages(self, pid: int, keep_ids: list) -> None:
        self.begin()
        try:
            if not keep_ids:
                self.conn.execute("DELETE FROM page WHERE partition_id=?", (pid,))
            else:
                marks = ",".join("?" for _ in keep_ids)
                self.conn.execute(
                    f"DELETE FROM page WHERE partition_id=? AND page_id NOT IN ({marks})",
                    (pid, *keep_ids))
            self.commit()
        except Exception:
            self.rollback()
            raise

    def unpublish_page(self, pid: int, page_id: str) -> None:
        """删单行(cmd_delete 用)。注意 unpublish_pages 的入参是**保留**
        名单 —— 单页删除曾误传 [page_id], 语义反转为"删掉其余全部行",
        一次 delete 只剩被删的那行在库里。"""
        self.conn.execute(
            "DELETE FROM page WHERE partition_id=? AND page_id=?",
            (pid, page_id))

    # -- modules ---------------------------------------------------------------
    def publish_modules(self, pid: int, modules: list) -> None:
        self.begin()
        try:
            self.conn.execute("DELETE FROM module WHERE partition_id=?", (pid,))
            ts = now_iso()
            for m in modules:
                self.conn.execute(
                    "INSERT INTO module(partition_id,module_id,title,dir,scope,"
                    "depends_on,related_to,parent_id,path,notes,module_type,skeleton,"
                    "evidence,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, m.get("id", ""), m.get("title", ""), m.get("dir", ""),
                     json.dumps(m.get("scope", []), ensure_ascii=False),
                     json.dumps(m.get("depends_on", []), ensure_ascii=False),
                     json.dumps(m.get("related_to", []), ensure_ascii=False),
                     m.get("parent", ""), m.get("path", m.get("dir", "")),
                     m.get("notes", ""), m.get("module_type", "real"),
                     1 if m.get("skeleton") else 0,
                     json.dumps(m.get("evidence", {}), ensure_ascii=False), ts))
            self.commit()
        except Exception:
            self.rollback()
            raise

    def modules(self, pid: int) -> list:
        rows = self.conn.execute(
            "SELECT * FROM module WHERE partition_id=? ORDER BY module_id",
            (pid,)).fetchall()
        return [dict(r) for r in rows]

    # -- file hashes ----------------------------------------------------------
    def upsert_file_hashes(self, pid: int, entries: dict) -> None:
        """只写给出的行(手工 write 刷基线用)—— 全表删写曾让单页修正
        产生 10k 行 churn, 快照基线本身不动。"""
        if not entries:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO file_hash(partition_id,path,sha256,size,mtime) "
            "VALUES(?,?,?,?,?)",
            [(pid, p, e["sha256"], e["size"], e["mtime"])
             for p, e in entries.items()])

    def delete_file_hashes(self, pid: int, paths) -> None:
        paths = list(paths)
        if not paths:
            return
        marks = ",".join("?" for _ in paths)
        self.conn.execute(
            f"DELETE FROM file_hash WHERE partition_id=? AND path IN ({marks})",
            (pid, *paths))

    def file_hashes(self, pid: int) -> dict:
        rows = self.conn.execute(
            "SELECT path,sha256,size,mtime FROM file_hash WHERE partition_id=?",
            (pid,)).fetchall()
        return {r["path"]: {"sha256": r["sha256"], "size": r["size"],
                            "mtime": r["mtime"]} for r in rows}

    # -- glossary(活术语表) ----------------------------------------------------
    def glossary_add_terms(self, pid: int, terms: list, source_page_id: str = "") -> None:
        for term in terms:
            t = str(term).strip()
            if not t:
                continue
            self.conn.execute(
                "INSERT INTO glossary(partition_id,term,definition,source_page_id,"
                "updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(partition_id,term) DO UPDATE SET "
                "source_page_id=excluded.source_page_id, updated_at=excluded.updated_at",
                (pid, t, "", source_page_id, now_iso()))

    def glossary_all(self, pid: int) -> list:
        rows = self.conn.execute(
            "SELECT term,definition,source_page_id FROM glossary "
            "WHERE partition_id=? ORDER BY term", (pid,)).fetchall()
        return [dict(r) for r in rows]

    # -- reconcile(D7/D22 队列) -------------------------------------------------
    def reconcile_add(self, pid: int, kind: str, payload: dict,
                      run_id: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO reconcile(partition_id,run_id,kind,payload,state,created_at) "
            "VALUES(?,?,?,?, 'pending', ?)",
            (pid, run_id, kind, json.dumps(payload, ensure_ascii=False), now_iso()))
        return cur.lastrowid

    def reconcile_pending(self, pid: int) -> list:
        rows = self.conn.execute(
            "SELECT * FROM reconcile WHERE partition_id=? AND state='pending' "
            "ORDER BY id", (pid,)).fetchall()
        return [dict(r) for r in rows]

    def reconcile_resolve(self, rid: int, state: str) -> None:
        self.conn.execute(
            "UPDATE reconcile SET state=?, resolved_at=? WHERE id=?",
            (state, now_iso(), rid))
