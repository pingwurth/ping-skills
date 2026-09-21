"""common.failures 失败记录分片机制 + common.dictionary 词表缓存单测。"""

from __future__ import annotations

import os

from common import failures
from common.dictionary import load_word_set
from common.paths import FAILED_FILES_LIST


# ---------------------------------------------------------------------------
# FailureRecorder 写分片
# ---------------------------------------------------------------------------

class TestFailureRecorder:
    def test_flush_writes_shard(self, tmp_path):
        recorder = failures.FailureRecorder("log_scan", tmp_path)
        recorder.record("a/B.java", "cannot read")
        recorder.record("c/D.java", "boom")
        recorder.flush()

        shard = failures.failure_shard(tmp_path, "log_scan")
        assert shard.name == "failed-files.log_scan.part"
        lines = shard.read_text(encoding="utf-8").splitlines()
        assert lines == [
            "log_scan\ta/B.java\tcannot read",
            "log_scan\tc/D.java\tboom",
        ]

    def test_flush_without_records_removes_stale_shard(self, tmp_path):
        shard = failures.failure_shard(tmp_path, "log_scan")
        shard.write_text("log_scan\told.java\tstale\n", encoding="utf-8")
        # 无记录时 flush 清理旧分片（幂等）
        failures.FailureRecorder("log_scan", tmp_path).flush()
        assert not shard.exists()

    def test_reason_sanitized_to_single_line(self, tmp_path):
        recorder = failures.FailureRecorder("s", tmp_path)
        recorder.record("X.java", "multi\nline\treason  here")
        recorder.flush()
        line = failures.failure_shard(tmp_path, "s").read_text(encoding="utf-8").rstrip("\n")
        # 换行/制表符被压缩为空格，行格式恰好两个制表符
        assert line == "s\tX.java\tmulti line reason here"
        assert line.count("\t") == 2

    def test_reason_truncated_to_200_chars(self, tmp_path):
        recorder = failures.FailureRecorder("s", tmp_path)
        recorder.record("X.java", "y" * 500)
        recorder.flush()
        line = failures.failure_shard(tmp_path, "s").read_text(encoding="utf-8").rstrip("\n")
        assert line.split("\t")[2] == "y" * 200


# ---------------------------------------------------------------------------
# merge_failure_shards 合并与行级完整性
# ---------------------------------------------------------------------------

class TestMergeFailureShards:
    def test_merge_combines_all_shards(self, tmp_path):
        r1 = failures.FailureRecorder("alpha", tmp_path)
        r1.record("A.java", "e1")
        r1.flush()
        r2 = failures.FailureRecorder("beta", tmp_path)
        r2.record("B.java", "e2")
        r2.flush()

        lines = failures.merge_failure_shards(tmp_path)
        assert lines == ["alpha\tA.java\te1", "beta\tB.java\te2"]

        merged = (tmp_path / FAILED_FILES_LIST).read_text(encoding="utf-8")
        assert merged == "alpha\tA.java\te1\nbeta\tB.java\te2\n"

    def test_merge_drops_torn_lines(self, tmp_path):
        # 行级完整性：不足两个制表符的损坏行被丢弃
        shard = failures.failure_shard(tmp_path, "broken")
        shard.write_text(
            "broken\tok.java\treason\n"
            "torn-line-without-tabs\n"
            "only\tone-tab\n"
            "\n",
            encoding="utf-8",
        )
        lines = failures.merge_failure_shards(tmp_path)
        assert lines == ["broken\tok.java\treason"]

    def test_merge_empty_dir_writes_empty_list(self, tmp_path):
        assert failures.merge_failure_shards(tmp_path) == []
        assert (tmp_path / FAILED_FILES_LIST).read_text(encoding="utf-8") == ""

    def test_merge_ignores_final_list_itself(self, tmp_path):
        # failed-files.list 不匹配 *.part 通配，不会被再次并入
        (tmp_path / FAILED_FILES_LIST).write_text("x\ty\tz\n", encoding="utf-8")
        assert failures.merge_failure_shards(tmp_path) == []


# ---------------------------------------------------------------------------
# clear_failure_shards 清理
# ---------------------------------------------------------------------------

class TestClearFailureShards:
    def test_clear_removes_shards_and_summary(self, tmp_path):
        failures.failure_shard(tmp_path, "a").write_text("a\tf\tr\n", encoding="utf-8")
        failures.failure_shard(tmp_path, "b").write_text("b\tf\tr\n", encoding="utf-8")
        (tmp_path / FAILED_FILES_LIST).write_text("a\tf\tr\n", encoding="utf-8")

        failures.clear_failure_shards(tmp_path)

        assert list(tmp_path.glob("failed-files.*.part")) == []
        assert not (tmp_path / FAILED_FILES_LIST).exists()

    def test_clear_keeps_unrelated_files(self, tmp_path):
        other = tmp_path / "log-print-ok.list"
        other.write_text("keep me\n", encoding="utf-8")
        failures.clear_failure_shards(tmp_path)
        assert other.exists()


# ---------------------------------------------------------------------------
# 词表 mtime 缓存（common.dictionary.load_word_set）
# ---------------------------------------------------------------------------

class TestWordSetCache:
    def test_second_load_hits_cache(self, tmp_path):
        f = tmp_path / "words.txt"
        f.write_text("apple\nbanana\n", encoding="utf-8")
        first = load_word_set(f)
        second = load_word_set(f)
        # 缓存命中返回同一共享集合对象
        assert second is first
        assert first == {"apple", "banana"}

    def test_mtime_change_invalidates_cache(self, tmp_path):
        f = tmp_path / "words.txt"
        f.write_text("apple\n", encoding="utf-8")
        first = load_word_set(f)
        assert first == {"apple"}

        f.write_text("cherry\n", encoding="utf-8")
        # 显式设置不同 mtime，规避文件系统时间戳精度问题
        stat = f.stat()
        os.utime(f, (stat.st_atime, stat.st_mtime + 10))

        second = load_word_set(f)
        assert second == {"cherry"}
        assert second is not first

    def test_same_mtime_returns_cached_content(self, tmp_path):
        f = tmp_path / "words.txt"
        f.write_text("apple\n", encoding="utf-8")
        stat = f.stat()
        load_word_set(f)
        # 内容变化但 mtime 被还原：命中缓存返回旧集合（设计行为）
        f.write_text("changed\n", encoding="utf-8")
        os.utime(f, (stat.st_atime, stat.st_mtime))
        assert load_word_set(f) == {"apple"}
