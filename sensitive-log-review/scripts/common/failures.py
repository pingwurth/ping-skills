"""失败文件记录与汇总（进程安全设计）。

main.py 四链路为线程并行，且每个检查步骤是独立子进程，若多个进程
同时向 failed-files.list 追加写，可能出现行撕裂或相互覆盖。
因此采用"各步骤独立分片"方案：
  1. 每个检查脚本把失败记录覆盖写入自己的分片
     failed-files.<步骤标识>.part（不同步骤文件名不同，天然无竞争；
     覆盖写保证独立 CLI 重复运行幂等，不残留上次的旧记录）；
  2. main.py 在全部步骤结束后调用 merge_failure_shards 合并分片为
     failed-files.list，行格式：{步骤标识}\t{文件路径}\t{原因摘要}。

各脚本独立 CLI 调用时同样写出自己的分片文件，不依赖 main.py。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .paths import FAILED_FILES_LIST, artifact

# 分片文件名模板与匹配模式（不会匹配到 failed-files.list 本身）
_SHARD_TEMPLATE = "failed-files.{step}.part"
_SHARD_GLOB = "failed-files.*.part"


def failure_shard(out_dir: Path, step_id: str) -> Path:
    """返回指定步骤的失败记录分片路径。"""
    return out_dir / _SHARD_TEMPLATE.format(step=step_id)


def _sanitize_reason(reason: object) -> str:
    """原因摘要压缩为单行（去除制表符/换行），避免破坏行格式。"""
    return " ".join(str(reason).split())[:200] or "unknown"


class FailureRecorder:
    """单步骤失败文件收集器：先内存累积，结束时一次性写分片。"""

    def __init__(self, step_id: str, out_dir: Path):
        self.step_id = step_id
        self.out_dir = out_dir
        self.records: list[tuple[str, str]] = []

    def record(self, file_path: object, reason: object) -> None:
        """记录一个失败文件及原因摘要。"""
        self.records.append((str(file_path), _sanitize_reason(reason)))

    def flush(self) -> None:
        """写出分片；无失败记录时清理旧分片（保证幂等）。

        写盘失败仅告警，不影响检查脚本本身的退出码。
        """
        shard = failure_shard(self.out_dir, self.step_id)
        try:
            if not self.records:
                shard.unlink(missing_ok=True)
                return
            with open(shard, "w", encoding="utf-8") as f:
                for path, reason in self.records:
                    f.write(f"{self.step_id}\t{path}\t{reason}\n")
        except OSError as exc:
            print(f"警告: 写入失败记录分片失败 {shard}: {exc}", file=sys.stderr)


def clear_failure_shards(out_dir: Path) -> None:
    """清理上一轮运行遗留的全部分片与汇总文件（main.py 运行前调用）。"""
    for shard in out_dir.glob(_SHARD_GLOB):
        try:
            shard.unlink()
        except OSError:
            pass
    try:
        artifact(out_dir, FAILED_FILES_LIST).unlink(missing_ok=True)
    except OSError:
        pass


def merge_failure_shards(out_dir: Path) -> list[str]:
    """合并全部分片为 failed-files.list，返回全部有效记录行。

    行级完整性校验：仅保留至少含两个制表符的完整行，
    防止个别分片损坏影响汇总。
    """
    lines: list[str] = []
    for shard in sorted(out_dir.glob(_SHARD_GLOB)):
        try:
            content = shard.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            if line.strip() and line.count("\t") >= 2:
                lines.append(line)

    target = artifact(out_dir, FAILED_FILES_LIST)
    try:
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    except OSError:
        pass
    return lines
