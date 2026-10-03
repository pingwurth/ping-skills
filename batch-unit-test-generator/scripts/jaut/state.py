"""状态存储(StateStore): state.json 的原子读写与默认 workdir 解析。

state.json 必须原子写入(临时文件 + os.replace + fsync), 避免半截状态破坏断点续跑。
加载委托 models.State.from_dict, 对缺失键填默认值以兼容旧版本状态文件。
批量侧另有 batch_state.json 的共享读写(save/load_batch_state), 供
batch_init/next/update/finish 四入口复用。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

from . import config
from .logutil import module_logger
from .models import BatchState, State

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


def default_workdir(project_root: str | Path) -> Path:
    """默认工作目录: <project-root>/.agent/batch-unit-test-generator。"""
    return Path(project_root).joinpath(*config.WORKDIR_PARTS)


def save_batch_state(path: Path, batch_state: BatchState) -> None:
    """原子写入 batch_state.json(batch_init/next/update/finish 共用)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(batch_state.to_dict(), f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_batch_state(path: Path) -> Optional[BatchState]:
    """读取 batch_state.json; 缺失或损坏返回 None(损坏细节进日志)。

    与 StateStore.load 的降级语义一致; 区别是批量入口(next/update/finish)
    对 None 抛 StepError.state_error 提示用户处置, 不让裸异常穿透 traceback。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        module_logger().warning(f"batch_state.json 读取失败, 视为缺失: {path} ({exc})")
        return None
    if not isinstance(data, dict):
        module_logger().warning(f"batch_state.json 结构非法(非对象): {path}")
        return None
    try:
        return BatchState.from_dict(data)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        module_logger().warning(f"batch_state.json 字段结构非法: {path} ({exc})")
        return None


# --------------------------------------------------------------------------- #
# schema 迁移管道
# --------------------------------------------------------------------------- #
# 每个迁移函数接收 raw dict 并 in-place 修改, 将 schema 从 v(N-1) 升级到 vN。
# 新增结构变更时: ① 递增 config.STATE_SCHEMA_VERSION  ② 追加迁移函数。
_MIGRATIONS: dict[int, callable] = {
    # 示例(未来使用):
    # 2: _migrate_v1_to_v2,   # v1 -> v2: 重命名 foo 为 bar
    # 3: _migrate_v2_to_v3,   # v2 -> v3: 拆分字段等
}


def _migrate(data: dict) -> dict:
    """将 raw state dict 从旧 schema_version 顺序迁移到当前版本。

    每次升级一步(v -> v+1), 便于独立测试每个迁移函数。
    旧 state.json 无 schema_version 字段时视为 v1。
    """
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
    """围绕单个 workdir 的状态读写。"""

    def __init__(self, workdir: str | Path) -> None:
        self.workdir = Path(workdir)

    @property
    def path(self) -> Path:
        return self.workdir / config.STATE_FILENAME

    def coverage_path(self) -> Path:
        return self.workdir / config.COVERAGE_FILENAME

    def mvn_log_path(self) -> Path:
        return self.workdir / config.MVN_LOG_FILENAME

    @contextlib.contextmanager
    def locked(self):
        """获取独占文件锁(跨进程), 保护 load → 修改 → save 的原子性。

        使用独立的 .lock 文件, 避免与 state.json 的 os.replace 替换冲突。
        POSIX 使用 fcntl.flock, Windows 使用 msvcrt.locking。

        锁文件常驻不删: unlock 后 unlink 与后来者 open 新 inode 存在竞态
        (旧 inode 上阻塞的等待方与新 inode 上立即获锁的后来者会同时"持锁"),
        会破坏互斥; 常驻锁空闲属正常设计, 仅当超 24 小时未刷新且仍被进程
        持有时提示疑似挂起(获锁时刷新 mtime)。
        """
        lock_path = self.path.with_name(config.STATE_FILENAME + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        # 陈旧锁提示: 常驻锁文件空闲(mtime 变旧)属正常设计, 不告警; 仅当超过
        # 24 小时未刷新且仍被进程持有时提示疑似挂起(删除锁文件无法释放 flock,
        # 且会重新引入 inode 竞态, 消息中不再建议手动删除)。
        if lock_path.exists():
            try:
                import time
                lock_age = time.time() - lock_path.stat().st_mtime
                if lock_age > 24 * 3600 and _lock_held(lock_path):  # 24 小时
                    module_logger().warning(
                        f"锁文件 {lock_path} 超过 24 小时未刷新且仍被进程持有 "
                        f"(疑似挂起进程); 锁会在持锁进程退出后自动释放, 本进程将等待获锁"
                    )
            except (OSError, ImportError):
                pass

        fp = open(lock_path, "a", encoding="utf-8")
        try:
            _lock_file(fp)
            # 获锁后刷新 mtime: 陈旧提示以"最近一次获锁时间"为准
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
                # 不 unlink: unlock 后删除与后来者 open 新 inode 存在竞态, 会破坏互斥

    def load(self) -> Optional[State]:
        """读取并反序列化状态; 文件缺失或损坏返回 None(由调用方按状态错误流转)。"""
        path = self.path
        if not path.is_file():
            return None
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, ValueError) as exc:
            module_logger().warning(f"state.json 读取失败, 视为缺失: {path} ({exc})")
            return None
        if not isinstance(data, dict):
            module_logger().warning(f"state.json 结构非法(非对象): {path}")
            return None
        state = State.from_dict(_migrate(data))
        return state

    def save(self, state: State) -> None:
        """原子写入状态。"""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(config.STATE_FILENAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(state.to_dict(), fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)

    def write_coverage(self, payload: dict) -> Path:
        """写出 coverage.json 快照, 返回其路径。"""
        path = self.coverage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        return path
