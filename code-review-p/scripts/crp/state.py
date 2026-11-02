"""状态存储(StateStore)与运行目录解析。

运行目录位于被审仓库之外(只读纪律 + 不污染 Phase 0 自身依赖的 ?? 检测):
    <state-home>/code-review-p/<repo-slug>/<run-id>/
        state.json / state.json.lock / in/ / out/ / logs/

state home 解析顺序: CRP_HOME > (Windows: %LOCALAPPDATA%) >
XDG_STATE_HOME > ~/.local/state。CRP_HOME 直接作为技能状态根(运行在
<CRP_HOME>/<repo-slug>/…, 不追加 code-review-p/ 子目录 —— 它覆盖整个根
而非 state-home 的上层); 其余分支在 state home 之下追加 code-review-p/。
~/.cache 语义为"可随时清除", 断点续跑状态不能放那(XDG state 才是归宿)。

state.json 必须原子写入(临时文件 + os.replace + fsync), 避免半截状态破坏续跑。
加载委托 models.State.from_dict, 对缺失键填默认值以兼容旧版本状态文件。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Optional

from . import config, gitops
from .logutil import module_logger
from .models import State

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fp) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(fp) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)

    def _try_lock_file(fp) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl

    def _lock_file(fp) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)

    def _unlock_file(fp) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)

    def _try_lock_file(fp) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


# 本线程已持有的运行锁(P8 重入守卫; P11/c014 改线程作用域 —— 进程级集合
# 会把"进程内某处持锁"误当"本线程持锁", 嵌入式多线程宿主下静默失去互斥)。
# 键用锁文件 inode(P11/c014: 路径拼写不影互斥), 取不到时回退规范化路径。
import threading

_HELD = threading.local()


def _held_keys() -> set:
    if not hasattr(_HELD, "keys"):
        _HELD.keys = set()
    return _HELD.keys


def _lock_key(lock_path: Path, run_dir: Path):
    try:
        st = lock_path.stat()
        return ("ino", st.st_dev, st.st_ino)
    except OSError:
        return ("path", str(run_dir.resolve()))


def _lock_held(lock_path: Path) -> bool:
    """非阻塞探测锁文件当前是否被进程持有(仅供陈旧锁诊断, 探测后立即释放)。"""
    try:
        fp = open(lock_path, "a", encoding="utf-8")
    except OSError:
        return False
    try:
        try:
            _try_lock_file(fp)
        except OSError:
            return True  # 未能立即获锁, 说明锁已被其他进程持有
        _unlock_file(fp)
        return False
    finally:
        fp.close()


# --------------------------------------------------------------------------- #
# 运行目录解析
# --------------------------------------------------------------------------- #
def state_home() -> Path:
    """状态根目录: CRP_HOME > (nt: %LOCALAPPDATA%) > XDG_STATE_HOME > ~/.local/state。

    CRP_HOME 覆盖整个技能状态根(不追加 code-review-p/); 其余分支追加 —— 与
    SKILL.md §1 的布局描述一致(P8 回修: 原文档两说与代码不一致)。
    """
    env = os.environ.get(config.CRP_HOME_ENV)
    if env:
        return Path(env)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / config.SKILL_NAME
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / config.SKILL_NAME


def repo_slug(cwd: str | Path) -> str:
    """仓库 slug: <顶层目录名>-<abs 顶层路径 sha1 前 8 位>。

    不同 checkout/worktree 得到不同 slug, 并行审查互不碰撞; 非 git 目录
    以 cwd 本身兜底。
    """
    root = gitops.repo_root(cwd) or Path(cwd).resolve()
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:8]
    return f"{root.name}-{digest}"


def new_run_id() -> str:
    """run-id: crp-<YYYYmmdd-HHMMSS>-<4hex>。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rand = secrets.token_hex(config.RUN_ID_RANDOM_HEX // 2)[:config.RUN_ID_RANDOM_HEX]
    return f"{config.RUN_ID_PREFIX}-{stamp}-{rand}"


def repo_home(cwd: str | Path) -> Path:
    """某仓库全部运行的容器目录: <state-home>/<repo-slug>。"""
    return state_home() / repo_slug(cwd)


def create_run_dir(cwd: str | Path, run_id: Optional[str] = None) -> Path:
    """创建新运行目录(含 in/out/logs 子目录), 返回其绝对路径。

    P5 回修: 删除 work/ 子目录 —— 只建不读(gap digest 与验证批次实际内联在
    协议 context 里交付), 每次运行白建一个空目录还误导贡献者找 digest 文件。
    """
    rid = run_id or new_run_id()
    run_dir = repo_home(cwd) / rid
    for sub in (run_dir, run_dir / config.IN_SUBDIR,
                run_dir / config.OUT_SUBDIR, run_dir / config.LOGS_SUBDIR):
        sub.mkdir(parents=True, exist_ok=True)
    return run_dir


def list_runs(cwd: str | Path) -> list[dict]:
    """列出 cwd 所在仓库的全部运行(新->旧), 优先读轻量 meta 边车。

    P6 回修: 原版对每个运行完整 json.load 其多 MB 的 state.json 只为取 5
    个标量 —— 被 runs 命令 / start 未完成检查 / 每次裸 submit 的
    resolve_run_dir 反复调用(10 个 deep 运行后一条 `crp.py runs` 要解析
    30-50MB JSON)。save() 现同步落 meta.json; 旧运行无 meta 回退 state.json。
    损坏以 {run_id(目录名), corrupt: true} 出现, 不中断列举。
    """
    home = repo_home(cwd)
    runs: list[dict] = []
    if not home.is_dir():
        return runs
    for run_dir in sorted(home.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        meta_file = run_dir / config.META_FILENAME
        state_file = run_dir / config.STATE_FILENAME
        if not meta_file.is_file() and not state_file.is_file():
            continue
        entry: dict = {"run_id": run_dir.name, "run_dir": str(run_dir)}
        src = meta_file if meta_file.is_file() else state_file
        try:
            with open(src, encoding="utf-8") as fp:
                data = json.load(fp)
            if not isinstance(data, dict):
                raise ValueError("非对象")
            done = data.get("done")
            if not isinstance(done, bool):
                done = data.get("phase") in ("done", "aborted")
            entry.update({
                "mode": data.get("mode", "?"),
                "phase": data.get("phase", "?"),
                "target": data.get("target"),
                "done": done,
                "updated_at": data.get("updated_at", ""),
            })
        except (OSError, ValueError):
            entry["corrupt"] = True
            entry["done"] = False
        runs.append(entry)
    return runs


def resolve_run_dir(explicit: Optional[str], cwd: str | Path,
                    include_done: bool = False) -> Optional[Path]:
    """运行目录解析: 显式 --run-dir 优先; 否则取 cwd 最新未完成运行。

    include_done=True 时无未完成运行则回落最新已完成运行 —— report/status/
    resume 的裸形式在唯一运行完成后仍可解析(P2 回修: 原版 done 后裸命令全部
    不可达, 'done 后幂等重渲染'面不传 --run-dir 无法触达, 用户被要求重新
    start 才能看到已完成的报告)。
    """
    if explicit:
        return Path(explicit).resolve()
    runs = list_runs(cwd)
    for entry in runs:
        if not entry.get("done") and not entry.get("corrupt"):
            return Path(entry["run_dir"])
    if include_done:
        for entry in runs:
            if not entry.get("corrupt"):
                return Path(entry["run_dir"])
    return None


# --------------------------------------------------------------------------- #
# schema 迁移管道
# --------------------------------------------------------------------------- #
# 每个迁移函数接收 raw dict 并 in-place 修改, 将 schema 从 v(N-1) 升级到 vN。
# 新增结构变更时: ① 递增 config.STATE_SCHEMA_VERSION  ② 追加迁移函数。
def _normalize_state_types(data: dict) -> None:
    """类型归一(常驻, 不依赖 schema 版本)。

    P12(c019): P11 的类型防御只在 v1→v2 迁移里 —— v2 态的畸形类型绕过防御,
    mode_params 在 brief/finalize 时 TypeError → exit-2(端到端实证)。归一移到
    load 常驻路径。P12(c006): 归一不再静默 —— 非法 mode/类型回填记 warning
    (原版静默把 deep 换成 standard 预算且首存后不可检)。
    """
    mode = data.get("mode")
    fixed = None
    if not isinstance(mode, str):
        fixed = mode
        mode = config.DEFAULT_MODE
    elif mode not in config.MODE_PARAMS:
        fixed = mode
        mode = config.DEFAULT_MODE
    if fixed is not None:
        module_logger().warning(
            f"state.json mode 非法({fixed!r})已归一为 {mode}(预算/保留阈值按默认)")
        data["mode"] = mode
    for key in ("sweep_plan", "repo", "cursor", "overlay", "scope", "brief",
                "carrier", "coverage", "accounting", "report", "fix",
                "interrupted"):
        val = data.get(key)
        if not isinstance(val, dict):
            if val is not None:
                module_logger().warning(
                    f"state.json {key} 类型非法({type(val).__name__})已归一为 {{}}")
            data[key] = {}  # 缺失同补(from_dict 本就默认 {}, 形状统一)
    plan = data.get("sweep_plan")
    if isinstance(plan, dict):
        angles = plan.get("angles")
        if not isinstance(angles, list):
            if angles is not None:
                module_logger().warning("state.json sweep_plan.angles 类型非法已归一为 []")
            plan["angles"] = []  # 缺失同置 [](形状统一, 消费者免查键)
    if not isinstance(data.get("candidates"), list):
        data["candidates"] = []
    if not isinstance(data.get("submissions"), list):
        data["submissions"] = []


def _migrate_v1_to_v2(data: dict) -> None:
    """v1→v2: 回填 sweep_plan 的并发/预算键(2026-11 并发治理新增)与 repo.root。

    P8 回修: 这些字段原先靠读点四处 or-回退兼容 —— 为 schema 迁移建的
    _MIGRATIONS 注册表一直空置, 机制被绕开。加载时回填后, decisions/handlers
    读点的 or-回退仅作为内存态 State(未经 load)的防御层。
    """
    # P11(c032): 畸形但合法 JSON 的类型防御 —— 原版 MODE_PARAMS.get(不可哈希
    # mode)/len(非列表 angles) 抛 TypeError 逃出 load 的 ValueError 捕获,
    # "损坏→None→exit-3 自愈"契约退化为 exit-2 内部错误(实证)
    # 类型归一由 load 常驻的 _normalize_state_types 负责(P12/c019)
    mode = data.get("mode")
    params = config.MODE_PARAMS.get(mode) if isinstance(mode, str) else None
    plan = data.get("sweep_plan")
    if not isinstance(plan, dict):
        plan = {}
        data["sweep_plan"] = plan
    if params:
        for key in ("max_rounds", "dry_required", "keep", "cap"):
            plan.setdefault(key, params[key])
    plan.setdefault("confidence_floor", config.CONFIDENCE_FLOOR)
    angles = plan.get("angles")
    plan.setdefault("max_agents",
                    config.agent_budget(len(angles)) if isinstance(angles, list) else 0)
    plan.setdefault("max_concurrent", config.max_concurrent_agents())
    repo = data.get("repo")
    if not isinstance(repo, dict):
        repo = {}
        data["repo"] = repo
    # v1 无 root(子目录启动的旧运行只能降级回 cwd —— 新运行由 _new_state 落真值)
    repo.setdefault("root", repo.get("cwd", ""))


_MIGRATIONS: dict[int, callable] = {
    2: _migrate_v1_to_v2,
}


def _migrate(data: dict) -> dict:
    """将 raw state dict 从旧 schema_version 顺序迁移到当前版本。"""
    current = config.STATE_SCHEMA_VERSION
    ver = int(data.get("schema_version", 1))
    while ver < current:
        next_ver = ver + 1
        migrator = _MIGRATIONS.get(next_ver)
        if migrator is None:
            raise ValueError(
                f"缺少 v{ver} -> v{next_ver} 迁移函数; "
                f"请检查 _MIGRATIONS 注册表是否完整"
            )
        migrator(data)
        data["schema_version"] = next_ver
        ver = next_ver
    return data


class StateStore:
    """围绕单个运行目录的状态读写。"""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)

    @property
    def path(self) -> Path:
        return self.run_dir / config.STATE_FILENAME

    def in_dir(self) -> Path:
        return self.run_dir / config.IN_SUBDIR

    def out_dir(self) -> Path:
        return self.run_dir / config.OUT_SUBDIR

    def ensure_layout(self) -> None:
        """确保 in/out/logs 子目录存在。"""
        for sub in (self.in_dir(), self.out_dir(),
                    self.run_dir / config.LOGS_SUBDIR):
            sub.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def locked(self):
        """获取独占文件锁(跨进程), 保护 load → 修改 → save 的原子性。

        使用独立的 .lock 文件, 避免与 state.json 的 os.replace 替换冲突。
        POSIX 使用 fcntl.flock, Windows 使用 msvcrt.locking。
        锁文件常驻不删(unlink 与后来者 open 新 inode 存在竞态, 会破坏互斥);
        仅当超 24 小时未刷新且仍被进程持有时提示疑似挂起。
        同线程重入(P8 回修, P11 收紧为线程作用域): 已持本运行锁时重入变
        no-op —— 原版对自身 flock 阻塞挂死, 防死锁只靠调用点约定注释。
        """
        lock_path = self.path.with_name(config.STATE_FILENAME + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fp = open(lock_path, "a", encoding="utf-8")
        # 键在 open 之后取(P11): open 前锁文件可能不存在(回退 path 键)而
        # 嵌套调用时已存在(ino 键)—— 两层键形不一致会让重入守卫漏判,
        # 内层对自身 flock 永久阻塞(实测)
        key = _lock_key(lock_path, self.run_dir)
        keys = _held_keys()
        if key in keys:
            fp.close()  # 未获锁, 只释放 fd
            yield
            return

        if lock_path.exists():
            try:
                lock_age = time.time() - lock_path.stat().st_mtime
                if lock_age > 24 * 3600 and _lock_held(lock_path):
                    module_logger().warning(
                        f"锁文件 {lock_path} 超过 24 小时未刷新且仍被进程持有 "
                        f"(疑似挂起进程); 锁会在持锁进程退出后自动释放, 本进程将等待获锁"
                    )
            except OSError:
                pass

        try:
            _lock_file(fp)
            keys.add(key)
            try:
                # P11(c018): discard 覆盖 add 之后的整段(含 utime 的
                # BaseException 窗口)—— 原版键残留后本进程永假持锁
                try:
                    os.utime(lock_path)
                except OSError:
                    pass
                yield
            finally:
                keys.discard(key)
        finally:
            try:
                _unlock_file(fp)
            except OSError as exc:
                # P12(c015): _lock_file 抛出时 fd 从未上锁, Windows 的 LK_UNLCK
                # 对未锁区自身抛错并覆盖原始异常(POSIX 为 no-op)—— 记日志不 raise
                module_logger().warning(f"解锁失败(fd 可能从未上锁, POSIX 为 no-op): {exc}")
            finally:
                fp.close()

    def load(self) -> Optional[State]:
        """读取并反序列化状态; 文件缺失或损坏返回 None(由调用方按状态错误流转)。

        损坏(非法 JSON/非对象)与缺失区分记日志; 二者都返回 None, 绝不静默重建。
        """
        path = self.path
        if not path.is_file():
            return None
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, ValueError) as exc:
            module_logger().warning(f"state.json 读取失败(损坏), 视为缺失: {path} ({exc})")
            return None
        if not isinstance(data, dict):
            module_logger().warning(f"state.json 结构非法(非对象): {path}")
            return None
        try:
            data = _migrate(data)
            _normalize_state_types(data)  # P12(c019): 常驻, v2 态同样归一
        except (ValueError, TypeError) as exc:
            module_logger().warning(f"state.json 迁移失败: {exc}")
            return None
        return State.from_dict(data)

    def save(self, state: State) -> None:
        """原子写入状态(tmp + fsync + os.replace)。"""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(config.STATE_FILENAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(state.to_dict(), fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
        # 轻量 meta 边车(P6): list_runs 只读这份小文件, 不再解析多 MB state.json
        meta = {"run_id": state.run_id, "mode": state.mode, "phase": state.phase,
                "target": state.target, "done": state.is_done,
                "updated_at": state.updated_at}
        meta_path = path.with_name(config.META_FILENAME)
        meta_tmp = path.with_name(config.META_FILENAME + ".tmp")
        try:
            with open(meta_tmp, "w", encoding="utf-8") as fp:
                json.dump(meta, fp, ensure_ascii=False)
                fp.flush()
                os.fsync(fp.fileno())  # P10(c017): 无 fsync 的 replace 掉电可留零字节 meta
            os.replace(meta_tmp, meta_path)
        except OSError:
            # P10 回修(c017): 边车写失败必须删除 —— 陈旧 meta(done=false)会让
            # list_runs 永远把 done 运行当未完成(start 循环追问唯 --force 逃),
            # 删掉即回退 state.json(真相源); done 运行不再 save, 无自愈机会
            try:
                meta_path.unlink(missing_ok=True)
                meta_tmp.unlink(missing_ok=True)
            except OSError as exc:
                # P12(c013): 双失败不再静默 —— 陈旧 meta 若幸存, list_runs 仍会
                # 误读, 至少留下日志线索
                module_logger().warning(f"meta 边车删除失败(陈旧 meta 可能残留): {exc}")
