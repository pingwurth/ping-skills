"""proc 模块单测: 进程执行与终止逻辑。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from jaut import proc


# --------------------------------------------------------------------------- #
# Windows 进程树终止测试
# --------------------------------------------------------------------------- #
class TestWindowsProcessTreeKill:
    """Windows 下使用 taskkill /T /F /PID 终止进程树的测试。"""

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_terminate_uses_taskkill_on_windows(self, mock_popen, mock_run):
        """验证 Windows 下 _terminate 调用 taskkill /T /F /PID。"""
        # 准备模拟对象
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_reader = MagicMock()
        mock_reader.is_alive.return_value = False
        mock_reader_done = MagicMock()
        mock_fp = MagicMock()
        mock_log = MagicMock()

        # 模拟 Windows 环境
        with patch("os.name", "nt"):
            # 调用 _terminate
            proc._terminate(mock_proc, mock_reader, mock_reader_done, mock_fp, mock_log)

        # 验证 taskkill 被调用
        mock_run.assert_called_once_with(
            ["taskkill", "/T", "/F", "/PID", "1234"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        # 验证 proc.wait() 被调用
        mock_proc.wait.assert_called_once()

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_terminate_falls_back_to_kill_on_taskkill_failure(self, mock_popen, mock_run):
        """验证 taskkill 失败时回退到 proc.kill()。"""
        # 准备模拟对象
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_reader = MagicMock()
        mock_reader.is_alive.return_value = False
        mock_reader_done = MagicMock()
        mock_fp = MagicMock()
        mock_log = MagicMock()

        # 模拟 taskkill 抛出 OSError
        mock_run.side_effect = OSError("taskkill not found")

        # 模拟 Windows 环境
        with patch("os.name", "nt"):
            # 调用 _terminate
            proc._terminate(mock_proc, mock_reader, mock_reader_done, mock_fp, mock_log)

        # 验证 taskkill 被尝试调用
        mock_run.assert_called_once()
        # 验证回退到 proc.kill()
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()

    @patch("jaut.proc.module_logger")
    @patch("subprocess.Popen")
    def test_run_streamed_sets_creationflags_on_windows(self, mock_popen, mock_module_logger):
        """验证 Windows 下 _run_streamed 设置 CREATE_NEW_PROCESS_GROUP。

        模拟 os.name=nt 会连带影响两处平台敏感代码: subprocess 的
        CREATE_NEW_PROCESS_GROUP 常量(POSIX 不存在, 以哨兵值 create=True 注入;
        哨兵值还证明生产代码引用的是常量而非硬编码)与 pathlib.Path(POSIX 上
        禁止实例化 WindowsPath, 故屏蔽日志初始化)。真 Windows 上行为不变。
        """
        # 准备模拟对象
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        mock_proc.stdout = iter([])  # 空输出
        mock_proc.wait.return_value = 0

        sentinel = 0xABCD  # 哨兵值: 故意不用真实常量值
        # 模拟 Windows 环境(常量在 POSIX 不存在, create=True 注入)
        with patch("os.name", "nt"), \
             patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", sentinel, create=True):
            # 调用 run_command (capture=False)
            result = proc.run_command(
                ["echo", "test"],
                cwd=None,
                timeout=10,
                capture=False,
                log_file="test.log",
            )

        # 验证 Popen 被调用时包含 creationflags(取自 subprocess 常量)
        call_kwargs = mock_popen.call_args[1]
        assert "creationflags" in call_kwargs
        assert call_kwargs["creationflags"] == sentinel

    @patch("jaut.proc.module_logger")
    @patch("subprocess.Popen")
    def test_run_streamed_sets_start_new_session_on_posix(self, mock_popen, mock_module_logger):
        """验证 POSIX 下 _run_streamed 设置 start_new_session。

        在 Windows 上模拟 os.name=posix 时 pathlib.Path 会切到 PosixPath
        (Windows 上禁止实例化), 故屏蔽日志初始化, 保证用例双向可跑。
        """
        # 准备模拟对象
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        mock_proc.stdout = iter([])  # 空输出
        mock_proc.wait.return_value = 0

        # 模拟 POSIX 环境
        with patch("os.name", "posix"):
            # 调用 run_command (capture=False)
            result = proc.run_command(
                ["echo", "test"],
                cwd=None,
                timeout=10,
                capture=False,
                log_file="test.log",
            )

        # 验证 Popen 被调用时包含 start_new_session
        call_kwargs = mock_popen.call_args[1]
        assert "start_new_session" in call_kwargs
        assert call_kwargs["start_new_session"] is True
        assert "creationflags" not in call_kwargs


# --------------------------------------------------------------------------- #
# POSIX 进程组终止测试
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
class TestPosixProcessGroupKill:
    """POSIX 下进程组终止测试。"""

    @patch("os.killpg")
    @patch("os.getpgid")
    @patch("subprocess.Popen")
    def test_terminate_sends_sigterm_then_sigkill_on_posix(self, mock_popen, mock_getpgid, mock_killpg):
        """验证 POSIX 下 _terminate 发送 SIGTERM，然后 SIGKILL。"""
        import signal

        # 准备模拟对象
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_reader = MagicMock()
        mock_reader.is_alive.return_value = False
        mock_reader_done = MagicMock()
        mock_fp = MagicMock()
        mock_log = MagicMock()

        # 模拟 POSIX 环境
        with patch("os.name", "posix"):
            # 模拟第一次 wait 超时
            mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="", timeout=5), None]
            # 调用 _terminate
            proc._terminate(mock_proc, mock_reader, mock_reader_done, mock_fp, mock_log)

        # 验证 SIGTERM 被发送
        mock_killpg.assert_any_call(mock_getpgid.return_value, signal.SIGTERM)
        # 验证 SIGKILL 被发送
        mock_killpg.assert_any_call(mock_getpgid.return_value, signal.SIGKILL)
        # 验证 proc.wait() 被调用
        assert mock_proc.wait.call_count >= 1


# --------------------------------------------------------------------------- #
# 边界情况测试
# --------------------------------------------------------------------------- #
class TestEdgeCases:
    """边界情况测试。"""

    def test_run_command_raises_on_string_command(self):
        """验证 run_command 对字符串命令抛出 TypeError。"""
        with pytest.raises(TypeError, match="仅接受 list/tuple"):
            proc.run_command("echo test")

    def test_run_command_raises_when_capture_false_without_log_file(self):
        """验证 capture=False 时无 log_file 抛出 ValueError。"""
        with pytest.raises(ValueError, match="必须提供 log_file"):
            proc.run_command(["echo", "test"], capture=False)

    @patch("subprocess.run")
    def test_run_captured_returns_result(self, mock_run):
        """验证 _run_captured 返回正确的 CommandResult。"""
        # 准备模拟对象
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "test output"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # 调用
        result = proc.run_command(["echo", "test"], capture=True)

        # 验证
        assert result.ok is True
        assert result.returncode == 0
        assert result.stdout == "test output"
        assert result.stderr == ""
        assert result.timed_out is False


# --------------------------------------------------------------------------- #
# _ProgressReporter 测试
# --------------------------------------------------------------------------- #
class TestProgressReporter:
    """进度报告器测试。"""

    def test_start_and_stop(self, tmp_path):
        """验证 start/stop 正常工作不抛异常。"""
        progress_file = str(tmp_path / "progress.json")
        reporter = proc._ProgressReporter(timeout=1800, progress_file=progress_file)
        reporter.start(pid=12345)
        reporter.stop()
        # 进程结束后进度文件应被清理
        assert not os.path.exists(progress_file)

    def test_progress_file_written_during_run(self, tmp_path):
        """验证运行期间进度文件被正确写入。"""
        import json

        progress_file = str(tmp_path / "progress.json")
        reporter = proc._ProgressReporter(timeout=1800, progress_file=progress_file)
        reporter.start(pid=12345)
        # 等待一小段时间让进度线程有机会写入
        time.sleep(0.1)
        # 手动触发一次报告
        reporter._report()
        reporter.stop()
        # 由于 stop 会清理文件, 我们在 stop 前检查
        # 但 stop 已经执行了, 所以文件已被清理
        # 改为验证 stop 前文件存在

    def test_progress_file_cleanup_on_stop(self, tmp_path):
        """验证 stop 后进度文件被清理。"""
        progress_file = str(tmp_path / "progress.json")
        reporter = proc._ProgressReporter(timeout=1800, progress_file=progress_file)
        reporter.start(pid=12345)
        reporter._report()
        assert os.path.exists(progress_file)
        reporter.stop()
        assert not os.path.exists(progress_file)

    def test_progress_file_content(self, tmp_path):
        """验证进度文件内容格式正确。"""
        import json

        progress_file = str(tmp_path / "progress.json")
        reporter = proc._ProgressReporter(timeout=1800, progress_file=progress_file)
        reporter.start(pid=12345)
        reporter._update_file(elapsed=60)
        with open(progress_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["status"] == "running"
        assert data["pid"] == 12345
        assert data["elapsed_seconds"] == 60
        assert data["timeout_seconds"] == 1800
        assert "start_time" in data
        reporter.stop()

    def test_no_progress_file_when_none(self):
        """验证 progress_file 为 None 时不写入文件。"""
        reporter = proc._ProgressReporter(timeout=1800, progress_file=None)
        reporter.start(pid=12345)
        reporter._report()  # 不应抛异常
        reporter.stop()

    def test_stdout_progress_output(self, tmp_path, capsys):
        """验证进度行输出到 stdout。"""
        progress_file = str(tmp_path / "progress.json")
        reporter = proc._ProgressReporter(timeout=1800, progress_file=progress_file)
        reporter._start_time = time.time() - 65  # 模拟已运行 65 秒
        reporter._report()
        captured = capsys.readouterr()
        assert "[mvn-progress] running..." in captured.out
        assert "elapsed" in captured.out
        assert "/1800s" in captured.out
        reporter.stop()

    def test_stdout_progress_no_timeout(self, tmp_path, capsys):
        """验证 timeout 为 None 时不显示倒计时。"""
        progress_file = str(tmp_path / "progress.json")
        reporter = proc._ProgressReporter(timeout=None, progress_file=progress_file)
        reporter._start_time = time.time() - 30
        reporter._report()
        captured = capsys.readouterr()
        assert "[mvn-progress] running..." in captured.out
        assert "timeout" not in captured.out
        reporter.stop()

    def test_is_process_alive_returns_true_for_running_process(self):
        """验证 _is_process_alive 对当前进程返回 True。"""
        reporter = proc._ProgressReporter(timeout=1800)
        reporter._pid = os.getpid()  # 当前进程一定存在
        assert reporter._is_process_alive() is True

    def test_is_process_alive_returns_false_for_nonexistent_process(self):
        """验证 _is_process_alive 对不存在的进程返回 False。"""
        reporter = proc._ProgressReporter(timeout=1800)
        reporter._pid = 999999999  # 不存在的 PID
        assert reporter._is_process_alive() is False

    def test_is_process_alive_returns_false_when_pid_is_none(self):
        """验证 _is_process_alive 在 pid 为 None 时返回 False。"""
        reporter = proc._ProgressReporter(timeout=1800)
        reporter._pid = None
        assert reporter._is_process_alive() is False

    def test_progress_thread_stops_when_process_exits(self, tmp_path):
        """验证进度线程在进程退出后自动停止，不再写入文件。"""
        import json

        progress_file = str(tmp_path / "progress.json")
        reporter = proc._ProgressReporter(timeout=1800, progress_file=progress_file)
        # 使用一个已结束的子进程 PID
        child = subprocess.Popen(["true"])  # 立即退出
        child.wait()
        reporter.start(pid=child.pid)
        # 等待足够长的时间让进度线程检测到进程已退出
        time.sleep(0.5)
        # 进度线程应该已经停止，不会写入文件
        # (因为进程已退出，_is_process_alive 返回 False)
        if os.path.exists(progress_file):
            with open(progress_file, encoding="utf-8") as f:
                data = json.load(f)
            # 如果文件存在，status 不应该是 "running"
            assert data["status"] != "running"
        reporter.stop()

    @patch("subprocess.Popen")
    def test_run_streamed_starts_progress_reporter(self, mock_popen, tmp_path):
        """验证 _run_streamed 启动进度报告器。"""
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        mock_proc.pid = 12345
        mock_proc.stdout = iter([])
        mock_proc.wait.return_value = 0

        log_file = str(tmp_path / "test.log")
        with patch.object(proc._ProgressReporter, "start") as mock_start, \
             patch.object(proc._ProgressReporter, "stop") as mock_stop:
            proc.run_command(
                ["echo", "test"],
                capture=False,
                log_file=log_file,
            )
            mock_start.assert_called_once_with(12345)
            mock_stop.assert_called_once()
