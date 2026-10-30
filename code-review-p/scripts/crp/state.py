"""状态存储(StateStore)与运行目录解析。

运行目录位于被审仓库之外(只读纪律 + 不污染 Phase 0 自身依赖的 ?? 检测):
    <state-home>/code-review-p/<repo-slug>/<run-id>/
        state.json / state.json.lock / in/ / work/ / out/ / logs/

state home 解析顺序: CRP_HOME > (Windows: %LOCALAPPDATA%) >
XDG_STATE_HOME > ~/.local/state。~/.cache 语义为"可随时清除", 断点续跑
状态不能放那(XDG state 才是可恢复状态的归宿)。

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
    """状态根目录: CRP_HOME > (nt: %LOCALAPPDATA%) > XDG_STATE_HOME > ~/.local/state。"""
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
    """创建新运行目录(含 in/work/out/logs 子目录), 返回其绝对路径。"""
    rid = run_id or new_run_id()
    run_dir = repo_home(cwd) / rid
    for sub in (run_dir, run_dir / config.IN_SUBDIR, run_dir / config.WORK_SUBDIR,
                run_dir / config.OUT_SUBDIR, run_dir / config.LOGS_SUBDIR):
        sub.mkdir(parents=True, exist_ok=True)
    return run_dir


def list_runs(cwd: str | Path) -> list[dict]:
    """列出 cwd 所在仓库的全部运行(新->旧), 无独立索引文件, 直接扫 state.json。

    损坏的 state.json 以 {run_id(目录名), corrupt: true} 出现, 不中断列举。
    """
    home = repo_home(cwd)
    runs: list[dict] = []
    if not home.is_dir():
        return runs
    for run_dir in sorted(home.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        state_file = run_dir / config.STATE_FILENAME
        if not state_file.is_file():
            continue
        entry: dict = {"run_id": run_dir.name, "run_dir": str(run_dir)}
        try:
            with open(state_file, encoding="utf-8") as fp:
                data = json.load(fp)
            entry.update({
                "mode": data.get("mode", "?"),
                "phase": data.get("phase", "?"),
                "target": data.get("target"),
                "done": data.get("phase") in ("done", "aborted"),
                "updated_at": data.get("updated_at", ""),
            })
        except (OSError, ValueError):
            entry["corrupt"] = True
            entry["done"] = False
        runs.append(entry)
    return runs


def resolve_run_dir(explicit: Optional[str], cwd: str | Path) -> Optional[Path]:
    """运行目录解析: 显式 --run-dir 优先; 否则取 cwd 最新未完成运行。"""
    if explicit:
        return Path(explicit).resolve()
    for entry in list_runs(cwd):
        if not entry.get("done") and not entry.get("corrupt"):
            return Path(entry["run_dir"])
    return None


# --------------------------------------------------------------------------- #
# schema 迁移管道
# --------------------------------------------------------------------------- #
# 每个迁移函数接收 raw dict 并 in-place 修改, 将 schema 从 v(N-1) 升级到 vN。
# 新增结构变更时: ① 递增 config.STATE_SCHEMA_VERSION  ② 追加迁移函数。
_MIGRATIONS: dict[int, callable] = {
    # 示例(未来使用):
    # 2: _migrate_v1_to_v2,
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

    def work_dir(self) -> Path:
        return self.run_dir / config.WORK_SUBDIR

    def out_dir(self) -> Path:
        return self.run_dir / config.OUT_SUBDIR

    def ensure_layout(self) -> None:
        """确保 in/work/out/logs 子目录存在。"""
        for sub in (self.in_dir(), self.work_dir(), self.out_dir(),
                    self.run_dir / config.LOGS_SUBDIR):
            sub.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def locked(self):
        """获取独占文件锁(跨进程), 保护 load → 修改 → save 的原子性。

        使用独立的 .lock 文件, 避免与 state.json 的 os.replace 替换冲突。
        POSIX 使用 fcntl.flock, Windows 使用 msvcrt.locking。
        锁文件常驻不删(unlink 与后来者 open 新 inode 存在竞态, 会破坏互斥);
        仅当超 24 小时未刷新且仍被进程持有时提示疑似挂起。
        """
        lock_path = self.path.with_name(config.STATE_FILENAME + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

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

        fp = open(lock_path, "a", encoding="utf-8")
        try:
            _lock_file(fp)
            try:
                os.utime(lock_path)
            except OSError:
                pass
            yield
        finally:
            try:
                _unlock_file(fp)
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
        except ValueError as exc:
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
