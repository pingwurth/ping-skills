"""子进程执行(proc)。

统一命令执行入口, 只接受 list/tuple(shell=False), 严禁向 stdout 回显。
与旧实现的关键差异: 不再内嵌 sys.exit 控制流, 一律返回 CommandResult,
由调用方(maven / 入口脚本)决定失败如何流转, 保证纯度高、可测试。

两种模式:
    capture=True  : 收集 stdout 文本返回(适合 git 等短命令)
    capture=False : 输出流式写入 log_file(适合 mvn), 保留单写者不变式与
                    POSIX 进程组两段式终止(SIGTERM -> 宽限 -> SIGKILL)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from . import config
from .logutil import module_logger


@dataclass
class CommandResult:
    """命令执行结果。

    ok         : 进程正常结束且退出码为 0(未超时)
    returncode : 进程退出码(超时/未结束/无法执行为 None)
    stdout     : capture=True 时的标准输出文本, 否则为 None
    stderr     : capture=True 时的标准错误文本, 否则为 None
    timed_out  : 是否因超时被终止
    """

    ok: bool
    returncode: Optional[int] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    timed_out: bool = False

    @property
    def launch_failed(self) -> bool:
        """命令根本无法启动(如可执行程序缺失): 无退出码且非超时。"""
        return self.returncode is None and not self.timed_out


class _ProgressReporter:
    """长时间运行命令的进度报告器。

    定期向 stdout 打印进度行并更新进度文件, 防止主 Agent 误判进程无响应。
    使用守护线程实现, 不阻塞进程退出。
    """

    def __init__(self, timeout: Optional[int], progress_file: Optional[str] = None):
        self._stop = threading.Event()
        self._timeout = timeout
        self._progress_file = progress_file
        self._start_time = time.time()
        self._thread: Optional[threading.Thread] = None
        self._pid: Optional[int] = None

    def start(self, pid: int) -> None:
        """启动进度报告线程。"""
        self._pid = pid
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止进度报告线程并清理进度文件。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._cleanup_file()

    def _run(self) -> None:
        while not self._stop.wait(config.MVN_PROGRESS_INTERVAL_SECONDS):
            if not self._is_process_alive():
                self._report_exited()
                return
            self._report()

    def _is_process_alive(self) -> bool:
        """检查进程是否仍在运行。"""
        if self._pid is None:
            return False
        try:
            os.kill(self._pid, 0)  # 信号 0: 不发送信号, 仅检查进程存在
            return True
        except OSError:
            return False

    def _report(self) -> None:
        elapsed = int(time.time() - self._start_time)
        timeout_str = f"/{self._timeout}s" if self._timeout else ""
        print(f"[mvn-progress] running... {elapsed}s elapsed{timeout_str}", flush=True)
        self._update_file(elapsed)

    def _report_exited(self) -> None:
        """进程已退出, 输出最终状态并更新进度文件。"""
        elapsed = int(time.time() - self._start_time)
        print(f"[mvn-progress] process exited after {elapsed}s", flush=True)
        if not self._progress_file:
            return
        data = {
            "status": "exited",
            "pid": self._pid,
            "elapsed_seconds": elapsed,
            "timeout_seconds": self._timeout,
            "start_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self._start_time)),
        }
        try:
            tmp = self._progress_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._progress_file)
        except OSError:
            pass

    def _update_file(self, elapsed: int) -> None:
        if not self._progress_file:
            return
        data = {
            "status": "running",
            "pid": self._pid,
            "elapsed_seconds": elapsed,
            "timeout_seconds": self._timeout,
            "start_time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self._start_time)),
        }
        try:
            tmp = self._progress_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._progress_file)
        except OSError:
            pass

    def _cleanup_file(self) -> None:
        if not self._progress_file:
            return
        try:
            os.remove(self._progress_file)
        except OSError:
            pass


def run_command(cmd: Sequence[str], *, cwd: Optional[str] = None,
                timeout: Optional[int] = None, capture: bool = True,
                log_file: Optional[str] = None) -> CommandResult:
    """执行命令并返回结果, 绝不抛 SystemExit。

    capture=False 时必须提供 log_file; 日志以 "w" 覆盖写(只留最近一次)。
    """
    if isinstance(cmd, str):
        raise TypeError("run_command 仅接受 list/tuple 命令(shell=False)")
    cmd_list = list(cmd)
    cmd_str = " ".join(cmd_list)
    log = module_logger()
    log.debug(f"> {cmd_str}")

    if capture:
        return _run_captured(cmd_list, cmd_str, cwd, timeout, log)
    if not log_file:
        raise ValueError("capture=False 时必须提供 log_file")
    return _run_streamed(cmd_list, cmd_str, cwd, timeout, log_file, log)


def _run_captured(cmd: list[str], cmd_str: str, cwd: Optional[str],
                  timeout: Optional[int], log) -> CommandResult:
    try:
        proc = subprocess.run(
            cmd, shell=False, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.error(f"命令超时 (>{timeout}s): {cmd_str}")
        return CommandResult(ok=False, returncode=None, stdout=None, timed_out=True)
    except OSError as exc:
        log.error(f"命令无法执行: {cmd_str} ({exc})")
        return CommandResult(ok=False, returncode=None, stdout=None)
    if proc.returncode != 0:
        log.error(f"命令失败 (exit {proc.returncode}): {(proc.stderr or '').strip()}")
    return CommandResult(ok=proc.returncode == 0, returncode=proc.returncode,
                         stdout=proc.stdout or "", stderr=proc.stderr or "")


def _run_streamed(cmd: list[str], cmd_str: str, cwd: Optional[str],
                  timeout: Optional[int], log_file: str, log) -> CommandResult:
    """流式写日志执行。单写者不变式: Popen 之后只有读线程写文件句柄。"""
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    fp = open(log_file, "w", encoding="utf-8", errors="replace")
    proc: Optional[subprocess.Popen] = None
    reader: Optional[threading.Thread] = None
    reader_done = threading.Event()
    # 进度报告器: 定期向 stdout 打印进度, 防止主 Agent 误判进程无响应
    progress_file = os.path.join(log_dir, config.MVN_PROGRESS_FILENAME) if log_dir else None
    # 清理上一次残留的进度文件(脚本被 SIGKILL 或崩溃时 finally 未执行)
    if progress_file:
        try:
            os.remove(progress_file)
        except OSError:
            pass
    progress = _ProgressReporter(timeout, progress_file)
    try:
        fp.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} $ {cmd_str}\n" + "=" * 72 + "\n")
        popen_kwargs = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            proc = subprocess.Popen(cmd, shell=False, **popen_kwargs)
        except OSError as exc:
            log.error(f"命令无法启动: {cmd_str} ({exc})")
            return CommandResult(ok=False, returncode=None, timed_out=False)
        progress.start(proc.pid)

        def _reader() -> None:
            try:
                assert proc is not None and proc.stdout is not None
                for line in proc.stdout:
                    fp.write(line)
                    fp.flush()
            finally:
                reader_done.set()

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        timed_out = _wait_with_timeout(proc, reader_done, timeout, cmd_str, log)
        if timed_out:
            _terminate(proc, reader, reader_done, fp, log)
            return CommandResult(ok=False, returncode=_safe_returncode(proc), timed_out=True)

        # 输出 EOF 后仍不退出: 终止进程组, 避免 start_new_session 造成孤儿进程
        reader.join(timeout=10)
        try:
            proc.wait(timeout=config.POST_EOF_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            log.error(f"命令输出结束后未正常退出, 终止进程组: {cmd_str}")
            _kill_process_group(proc)
            proc.wait()
            return CommandResult(ok=False, returncode=_safe_returncode(proc), timed_out=True)

        if proc.returncode != 0:
            log.error(f"命令失败 (exit {proc.returncode})")
        return CommandResult(ok=proc.returncode == 0, returncode=proc.returncode)
    finally:
        progress.stop()
        if reader is not None:
            reader.join(timeout=10)
        fp.flush()
        fp.close()


def _wait_with_timeout(proc: subprocess.Popen, reader_done: threading.Event,
                       timeout: Optional[int], cmd_str: str, log) -> bool:
    """等待进程结束; 超时返回 True。"""
    try:
        proc.wait(timeout=timeout)
        return False
    except subprocess.TimeoutExpired:
        log.error(f"命令超时 (>{timeout}s): {cmd_str}")
        return True


def _terminate(proc: subprocess.Popen, reader: threading.Thread,
               reader_done: threading.Event, fp, log) -> None:
    """超时终止: POSIX 两段式(SIGTERM -> 宽限 -> SIGKILL), Windows 直接 kill。"""
    if os.name != "nt":
        _signal_process_group(proc, signal.SIGTERM)
        reader_done.wait(config.MVN_KILL_GRACE_SECONDS)
        try:
            proc.wait(timeout=config.MVN_KILL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            proc.wait()
    else:
        # Windows: 使用 taskkill /T /F /PID 终止整个进程树
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            # taskkill 不可用时回退到 proc.kill()
            proc.kill()
        proc.wait()
    reader.join(timeout=10)
    # 保持单写者不变式: 读线程仍存活时不写 [TIMEOUT] 标记
    if reader.is_alive():
        log.warning("读线程未结束, 跳过 [TIMEOUT] 标记写入(保持单写者不变式)")
    else:
        fp.write(f"[TIMEOUT] 命令超时已被终止\n")


def _signal_process_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except OSError:
        try:
            proc.send_signal(sig)
        except (OSError, ValueError):
            pass


def _kill_process_group(proc: subprocess.Popen) -> None:
    if os.name != "nt":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except OSError:
            pass
    proc.kill()


def _safe_returncode(proc: Optional[subprocess.Popen]) -> Optional[int]:
    return proc.returncode if proc is not None else None


def resolve_tool(name: str) -> Optional[str]:
    """在 PATH 中定位可执行程序, 未找到记 error 返回 None。"""
    import shutil

    path = shutil.which(name)
    if path is None:
        module_logger().error(f"未找到可执行程序 {name}, 请先安装或加入 PATH")
    return path


def ensure_parent_dir(path: str | Path) -> None:
    """确保文件父目录存在(供日志/产物写入前调用)。"""
    parent = Path(path).parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
