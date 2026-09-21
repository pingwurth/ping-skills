"""状态存储(StateStore): state.json 的原子读写与默认 workdir 解析。

state.json 必须原子写入(临时文件 + os.replace + fsync), 避免半截状态破坏断点续跑。
加载委托 models.State.from_dict, 对缺失键填默认值以兼容旧版本状态文件。
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
from .models import State

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fp) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(fp) -> None:
        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(fp) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)

    def _unlock_file(fp) -> None:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


def default_workdir(project_root: str | Path) -> Path:
    """默认工作目录: <project-root>/.agent/java-unit-test-generator。"""
    return Path(project_root).joinpath(*config.WORKDIR_PARTS)


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
        """
        lock_path = self.path.with_name(config.STATE_FILENAME + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 检测残留锁文件(超过 24 小时)
        if lock_path.exists():
            try:
                import time
                lock_age = time.time() - lock_path.stat().st_mtime
                if lock_age > 24 * 3600:  # 24 小时
                    module_logger().warning(
                        f"检测到残留锁文件 {lock_path} (存在 {lock_age/3600:.1f} 小时), "
                        f"如果确认没有其他进程在运行, 可手动删除该文件"
                    )
            except (OSError, ImportError):
                pass
        
        fp = open(lock_path, "a", encoding="utf-8")
        try:
            _lock_file(fp)
            yield
        finally:
            try:
                _unlock_file(fp)
            finally:
                fp.close()
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass

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
