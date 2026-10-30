"""临时 git 仓库夹具构建器(Phase 0 阶梯矩阵测试用)。

全部走文件系统 + git CLI, 不 mock —— 阶梯行为必须对真实 git 成立。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class RepoBuilder:
    def __init__(self, root: Path):
        self.root = root

    # ------------------------------------------------------------------ #
    @classmethod
    def init(cls, tmp: Path, name: str = "repo", default_branch: str = "main") -> "RepoBuilder":
        root = tmp / name
        root.mkdir(parents=True)
        cls._run(root, ["init", "-b", default_branch])
        return cls(root)

    @staticmethod
    def _run(cwd: Path, args: list[str], check: bool = True) -> str:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args} 失败: {proc.stderr}")
        return proc.stdout.strip()

    # ------------------------------------------------------------------ #
    def commit(self, files: dict[str, str], msg: str) -> str:
        for rel, content in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        self._run(self.root, ["add", "-A"])
        self._run(self.root, ["-c", "user.email=t@example.com", "-c", "user.name=t",
                              "commit", "-m", msg])
        return self._run(self.root, ["rev-parse", "HEAD"])

    def branch(self, name: str) -> None:
        self._run(self.root, ["checkout", "-b", name])

    def checkout(self, name: str) -> None:
        self._run(self.root, ["checkout", name])

    def edit(self, files: dict[str, str]) -> None:
        """写文件不提交(工作树改动)。"""
        for rel, content in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    def stage(self, rel: str) -> None:
        self._run(self.root, ["add", rel])

    def untracked(self, files: dict[str, str]) -> None:
        self.edit(files)

    def head(self) -> str:
        return self._run(self.root, ["rev-parse", "HEAD"])

    def clone(self, tmp: Path, name: str = "clone", depth: int | None = None) -> "RepoBuilder":
        """克隆本仓库(产生 origin 远端与 upstream 跟踪)。"""
        dst = tmp / name
        args = ["clone", str(self.root), str(dst)]
        if depth:
            args = ["clone", "--depth", str(depth), str(self.root), str(dst)]
        subprocess.run(["git", *args], capture_output=True, text=True, check=True)
        self._run(dst, ["config", "user.email", "t@example.com"])
        self._run(dst, ["config", "user.name", "t"])
        return RepoBuilder(dst)
