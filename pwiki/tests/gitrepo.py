"""测试 fixture 构造器: git init + 可配文件树, 行数已知便于精确引用。

用法:
    repo = GitRepo(tmp_path)
    repo.write("src/main.py", "print('hi')\\n")     # 自动 add
    repo.write("README.md", ...)
    repo.commit("init")
"""

from __future__ import annotations

import os
import subprocess


class GitRepo:
    def __init__(self, base, initial_branch="main"):
        self.path = str(base)
        os.makedirs(self.path, exist_ok=True)
        self.run("init", "-b", initial_branch)
        self.run("config", "user.email", "test@example.com")
        self.run("config", "user.name", "Test")

    def run(self, *args, check=True):
        p = subprocess.run(["git", "-C", self.path, *args],
                           capture_output=True, text=True)
        if check and p.returncode != 0:
            raise RuntimeError(f"git {args} failed: {p.stderr}")
        return p.stdout.strip()

    def write(self, rel: str, content: str):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full) or self.path, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.run("add", "--", rel, check=False)

    def commit(self, msg="wip"):
        self.run("add", "-A", check=False)
        env = dict(os.environ, GIT_AUTHOR_DATE="2026-01-01T00:00:00",
                   GIT_COMMITTER_DATE="2026-01-01T00:00:00")
        subprocess.run(["git", "-C", self.path, "commit", "-m", msg, "--allow-empty"],
                       capture_output=True, env=env)
        return self.run("rev-parse", "HEAD")
