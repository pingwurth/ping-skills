"""确定性仓库扫描 → work/inventory.json。

移植自 repowiki/scripts/scan_repo.py(578 行, 比 qwiki 的扫描器强: manifest×9 /
命令分类 / 入口打分 / churn top50 / D20 AI 目录排除 / README 摘录 / tree_text)。
附加: CI 探测(qwiki; 供 getting_started 信号)与 .gitkeep 可见性(qwiki bug17:
占位目录检测需要看到标记文件)。

只读, 永不执行仓库代码; 唯一子进程 git。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from collections import Counter

from . import config
from .config import Ctx, now_iso
from .util import posix, git

IGNORE_DIRS = set(config.DEFAULT_IGNORES) | {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".vs",
    # D20: AI 工具/助手配置目录不是产品源码 —— yudao 验收实跑曾把
    # .opencode/skills(103 md / 24k 行)当成了一个模块
    ".opencode", ".qwen", ".codex", ".claude", ".claude-work", ".agents", ".agent",
    ".ua", ".repowise", ".codegraph", ".qoder", ".gitee", ".image", ".mcp", "-",
    "node_modules", "bower_components", "jspm_packages",
    ".venv", "venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".nox", ".eggs", "*.egg-info",
    "dist", "build", "out", "target", "bin", "obj", "coverage", ".nyc_output",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache", ".cache",
    ".gradle", ".mvn", "vendor", "third_party", "third-party",
    "gen", "generated", "__generated__", "autogen", "auto-generated",
    "scratch", "tmp", "temp",
    ".worktrees", ".mimocode", ".cursor", ".windsurf", ".continue", ".aider",
    ".codeium", ".gemini", ".specstory",
    "openwiki", ".terraform", ".serverless", ".docusaurus",
    ".wiki", ".pwiki",
}
# P42: 以 config.DEFAULT_IGNORES 为基(曾重打 ~27 项且漏了 Pods/DerivedData
# —— inventory 的 walk 只认本集合, 而 expand_scope 的快照跟踪另认
# DEFAULT_IGNORES: iOS 仓库的 Pods 进了 inventory 却永不标 stale)。

# glob 条目(*.egg-info 等)单独筛出: os.walk 过滤是精确名匹配,
# 混在集合里曾永不命中(pip install -e . 的 pkg/foo.egg-info 整棵被扫)
_IGNORE_GLOBS = tuple(p for p in IGNORE_DIRS if any(c in p for c in "*?"))


def _dir_ignored(name: str) -> bool:
    if name in IGNORE_DIRS:
        return True
    return any(fnmatch.fnmatch(name, g) for g in _IGNORE_GLOBS)

LANG_BY_EXT = {
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".pyi": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".cs": "csharp", ".fs": "fsharp", ".vb": "vbnet",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".swift": "swift", ".m": "objective-c", ".mm": "objective-c",
    ".rb": "ruby", ".php": "php", ".pl": "perl", ".lua": "lua", ".dart": "dart",
    ".ex": "elixir", ".exs": "elixir", ".erl": "erlang", ".hs": "haskell", ".clj": "clojure",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".fish": "shell", ".ps1": "powershell",
    ".sql": "sql", ".graphql": "graphql", ".gql": "graphql", ".proto": "protobuf",
    ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
    ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
    ".html": "html", ".htm": "html", ".xml": "xml", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".md": "markdown", ".mdx": "mdx", ".rst": "rst", ".txt": "text",
    ".dockerfile": "docker", ".tf": "terraform", ".hcl": "hcl",
}

BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".psd",
    ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".a", ".o", ".obj", ".class", ".pyc", ".pyo",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".wav", ".flac",
    ".sqlite", ".db", ".bin", ".dat", ".pkl", ".npy", ".npz", ".onnx", ".pt", ".pth", ".safetensors",
}

MANIFESTS = {
    "package.json", "pnpm-workspace.yaml", "pnpm-workspace.yml", "yarn.lock", "package-lock.json",
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "uv.lock", "poetry.lock",
    "go.mod", "go.sum", "go.work", "go.work.sum",
    "Cargo.toml", "Cargo.lock",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties",
    "pom.xml", "mvnw", "gradlew",
    "Makefile", "makefile", "GNUmakefile", "justfile", "Taskfile.yml", "Taskfile.yaml",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml",
    "Chart.yaml", "buf.yaml", "buf.gen.yaml", "turbo.json", "nx.json", "lerna.json",
    "tsconfig.json", "vitest.config.ts", "jest.config.js", "vite.config.ts", "next.config.js", "next.config.mjs",
}

MAX_TEXT_BYTES = 1_000_000

# D23 引用行数缓存: (path, size, mtime) → 行数。同一文件在一次进程里被
# submit / LINING / publish / --fix 反复计数, 曾一轮 135-job 整读 ~5400 次。
_CITE_LINE_CACHE: dict = {}


def cite_lines(path: str) -> int:
    """D23 口径的文件行数(Read 工具口径, 供引用校验/钳位)。

    与 count_lines 的区别: count_lines 是清单统计(超大文件 -1 计入
    skipped_large); cite_lines 全量分块计数, 永不返回 -1 —— 曾按
    MAX_TEXT_BYTES 截断计数, >1MB 的文件只数到前 1MB(3.2MB/40 万行报
    12.5 万行), 引用大文件后段的正确行号被误判"行范围越界"硬错,
    submit/LINING 永久阻塞且钳位对着假行数。"""
    try:
        st = os.stat(path)
        key = (path, st.st_size, int(st.st_mtime * 1000))
    except OSError:
        return 0
    hit = _CITE_LINE_CACHE.get(key)
    if hit is not None:
        return hit
    # r7 #61: 分块计数走 _count_lines_chunked 共享实现(此前是一份逐字
    # 手抄, CRLF/块大小等口径修正只会落进一边 —— 与 docstring 的"共用"
    # 声明不符)
    n = _count_lines_chunked(path)
    _CITE_LINE_CACHE[key] = n
    return n

# D4: 生成物后缀(与 corpus.is_generated 同源; 扫描层先过滤一遍)
GENERATED_SUFFIXES = (
    ".pb.go", ".pb.ts", ".pb.js", ".pb.swift", "_pb2.py", "_pb2_grpc.py",
    ".generated.ts", ".generated.tsx", ".generated.js", ".gen.ts", ".gen.tsx",
    ".g.cs", ".designer.cs", "_pb.ts", "_pb.js", "_pb.d.ts",
)

ENTRY_BASENAMES = {
    "main.ts", "main.tsx", "main.js", "main.jsx", "index.ts", "index.tsx", "index.js",
    "app.ts", "app.tsx", "app.js", "server.ts", "server.js", "bootstrap.ts",
    "main.py", "app.py", "server.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py",
    "main.go", "main.rs", "lib.rs", "Program.cs", "main.c", "main.cpp",
    "Application.java", "Main.java", "Application.kt", "Main.kt",
    "page.tsx", "layout.tsx", "route.ts", "route.tsx", "page.ts", "layout.ts",
}

ENTRY_PATH_HINTS = ("/src/main/", "/cmd/", "/bin/", "/app/", "/server/", "/api/", "/routes/", "/pages/", "/views/")

README_CANDIDATES = ("README.md", "readme.md", "README.rst", "README", "README.txt", "docs/README.md")


def read_text(path: str, limit: int = 200_000) -> str:
    """唯一实现见 corpus.read_text(两份逐字拷贝曾各自漂移); 这里只差默认 limit。"""
    from .corpus import read_text as _corpus_read_text
    return _corpus_read_text(path, limit)


def _count_lines_chunked(path: str) -> int:
    """分块计数(1 MiB)—— count_lines 与 cite_lines 共用(P75: Python 行
    迭代是 scan 的主导 CPU 成本, 实测 7.8× 差距; 末行无换行补 1 同口径)。"""
    n, tail = 0, b""
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                n += chunk.count(b"\n")
                tail = chunk[-1:]
    except OSError:
        return 0
    if tail and tail != b"\n":
        n += 1
    return n


def count_lines(path: str) -> int:
    """D23 口径: 返回真实行数(供引用钳位); 超大文件返回 -1 计入 skipped_large。"""
    try:
        if os.path.getsize(path) > MAX_TEXT_BYTES:
            return -1
        return _count_lines_chunked(path)
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# manifest 解析(×9; 全部行/正则, 从不执行仓库代码)
# --------------------------------------------------------------------------- #

def parse_package_json(path: str) -> dict:
    try:
        data = json.loads(read_text(path, 2_000_000))
    except (ValueError, OSError):
        return {}
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        v = data.get(key)
        if isinstance(v, dict):
            deps.update({k: str(x) for k, x in v.items()})
    ws = data.get("workspaces")
    if isinstance(ws, dict):
        ws = ws.get("packages")
    return {
        "name": data.get("name"),
        "version": data.get("version"),
        "private": data.get("private"),
        "scripts": {k: str(v) for k, v in (data.get("scripts") or {}).items()},
        "dependencies": deps,
        "workspaces": ws if isinstance(ws, list) else [],
    }


def parse_pnpm_workspace(path: str) -> dict:
    text = read_text(path)
    patterns = []
    in_block = False
    for line in text.splitlines():
        if re.match(r"^\s*packages\s*:", line):
            in_block = True
            continue
        if in_block:
            m = re.match(r"^\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*(?:#.*)?$", line)
            if m:
                patterns.append(m.group(1).strip())
            elif line.strip() and not line.strip().startswith("#"):
                in_block = False
    return {"packages": patterns}


def parse_pyproject(path: str) -> dict:
    text = read_text(path)
    out = {"name": None, "version": None, "dependencies": [], "workspace_members": []}
    m = re.search(r'^\s*name\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if m:
        out["name"] = m.group(1)
    m = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if m:
        out["version"] = m.group(1)
    section = None
    in_deps_array = False
    for line in text.splitlines():
        s = line.strip()
        if in_deps_array:
            # P16: PEP 621 dependencies 数组可跨行 —— 逐项收引号项
            m2 = re.match(r'^["\']([^"\'#]+?)["\']\s*,?\s*(#.*)?$', s)
            if m2:
                dep = re.split(r"[<>=!~\[;\s]", m2.group(1).strip(), 1)[0]
                if dep:
                    out["dependencies"].append(dep)
                continue
            # 末项带闭合括号的合法 TOML(`"bar"]`)既不是纯项也不以 ']'
            # 开头 —— 项曾整个被丢且数组永不收口, in_deps_array 吞掉文件
            # 余下全部内容(整个 [tool.poetry.dependencies] 节)
            m3 = re.match(r'^["\']([^"\'#]+?)["\']\s*\]\s*(#.*)?$', s)
            if m3:
                out["dependencies"].append(m3.group(1).strip())
                in_deps_array = False
                continue
            if s.startswith("]"):
                in_deps_array = False
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section == "[project]" and re.match(r"^dependencies\s*=\s*\[", s):
            # 真依赖只在 PEP 621 数组里 —— 曾把 [project] 的每个标量键
            # (name/version/requires-python/…)全当依赖, 真依赖整批丢失
            inline = s.split("=", 1)[1]
            for m2 in re.finditer(r'["\']([^"\'#]+?)["\']', inline):
                # r7 #34: 剥版本说明符存裸名(poetry 分支同口径) ——
                # 'sqlalchemy>=2.0' 曾原样入库, themes 的依赖卡门
                # (ARCH_SHAPING_DEPS/basename 词干)永远匹配不到
                dep = re.split(r"[<>=!~\[;\s]", m2.group(1).strip(), 1)[0]
                if dep:
                    out["dependencies"].append(dep)
            # r7 #37: 判收口前先剥行尾注释 —— '["foo"]  # pinned' 曾因
            # endswith 检查失配永不收口, in_array 吞掉后续全部段
            if not inline.split("#", 1)[0].rstrip().endswith("]"):
                in_deps_array = True
            continue
        # r7 #36: [tool.uv] 不再当依赖来源(段内存的是 index-url 等
        # 配置项 —— 曾混出 'index-url' 假依赖卡); uv 的真依赖走 [project]
        if section == "[tool.poetry.dependencies]" \
                and re.match(r'^[A-Za-z0-9_.-]+\s*=\s*["\']', s):
            out["dependencies"].append(s.split("=", 1)[0].strip())
        if section == "[tool.uv.workspace]" and s.startswith("- "):
            out["workspace_members"].append(s[2:].strip().strip("'\""))
    seen = set()
    out["dependencies"] = [d for d in out["dependencies"]
                           if not (d in seen or seen.add(d))]
    return out


def parse_go_work(path: str) -> dict:
    text = read_text(path)
    uses = []
    in_block = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("use ("):
            in_block = True
            continue
        if in_block and s == ")":
            in_block = False
            continue
        if in_block and s:
            uses.append(s.strip("'\""))
        elif s.startswith("use "):
            uses.append(s[4:].strip().strip("'\""))
    return {"use": uses}


def parse_go_mod(path: str) -> dict:
    text = read_text(path)
    m = re.search(r"^module\s+(\S+)", text, re.M)
    return {"module": m.group(1) if m else None}


def parse_cargo(path: str) -> dict:
    text = read_text(path)
    out = {"name": None, "workspace_members": []}
    section = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
            continue
        if section == "[package]":
            m = re.match(r'^name\s*=\s*["\']([^"\']+)["\']', s)
            if m:
                out["name"] = m.group(1)
        if section == "[workspace]" and s.startswith("members"):
            out["workspace_members"] = re.findall(r'["\']([^"\']+)["\']', s)
    return out


def parse_gradle_settings(path: str) -> dict:
    text = read_text(path)
    # r7 #35: 按语句迭代再收全部引号项 —— findall 的字面 'include' 前缀
    # 从上一匹配末尾续扫, 同一语句的其余子项目全部丢弃
    includes = []
    for m in re.finditer(r"include\s*\(?([^)\n]*)", text):
        includes += re.findall(r'["\']([^"\']+)["\']', m.group(1))
    return {"includes": [i.lstrip(":") for i in includes]}


def parse_pom(path: str) -> dict:
    text = read_text(path)
    # r7 #33: 先剥 <parent> 块再取 artifactId —— 子 pom 的第一个
    # artifactId 在 parent 块内, 每个子模块的清单名都曾是父 pom 的
    # artifactId(兄弟依赖解析成 None/错模块)
    body = re.sub(r"(?s)<parent>.*?</parent>", "", text)
    return {
        "artifact": (re.search(r"<artifactId>([^<]+)</artifactId>", body)
                     or [None, None])[1],
        "modules": re.findall(r"<module>([^<]+)</module>", text),
    }


def parse_manifest(path: str) -> dict:
    name = os.path.basename(path)
    if name == "package.json":
        d = parse_package_json(path)
        d["type"] = "npm"
    elif name in ("pnpm-workspace.yaml", "pnpm-workspace.yml"):
        d = parse_pnpm_workspace(path)
        d["type"] = "pnpm-workspace"
    elif name == "pyproject.toml":
        d = parse_pyproject(path)
        d["type"] = "python"
    elif name == "go.mod":
        d = parse_go_mod(path)
        d["type"] = "go"
    elif name == "go.work":
        d = parse_go_work(path)
        d["type"] = "go-work"
    elif name == "Cargo.toml":
        d = parse_cargo(path)
        d["type"] = "cargo"
    elif name in ("settings.gradle", "settings.gradle.kts"):
        d = parse_gradle_settings(path)
        d["type"] = "gradle"
    elif name == "pom.xml":
        d = parse_pom(path)
        d["type"] = "maven"
    elif name in ("build.gradle", "build.gradle.kts"):
        d = {"type": "gradle-build"}
    elif name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                  "compose.yml", "compose.yaml", "Chart.yaml"):
        d = {"type": "deploy"}
    else:
        d = {"type": "other"}
    d["path"] = path
    return d


def _cmd_category(low: str) -> str:
    """scripts 键 / Makefile 目标共用的分类梯(P69) —— 两份手抄的关键字集
    曾各自漂移(scripts 认 serve/watch 不认 run, make 认 run/fmt/setup 不
    认 serve/watch/typecheck), 只加一边的改动会静默错分另一面。"""
    if "test" in low:
        return "test"
    if "lint" in low or "typecheck" in low or "check" in low or "fmt" in low:
        return "lint"
    if "build" in low or "compile" in low:
        return "build"
    if "dev" in low or "run" in low or "start" in low \
            or low in ("serve", "watch"):
        return "dev"
    if "install" in low or "setup" in low:
        return "install"
    return "other"


def detect_commands(manifests: list, repo: str) -> dict:
    commands = {"build": [], "test": [], "lint": [], "dev": [], "install": [], "other": []}
    for m in manifests:
        path = m["path"]
        scripts = m.get("scripts") or {}
        rel_path = path if not os.path.isabs(path) else os.path.relpath(path, repo)
        for key, val in scripts.items():
            entry = f"{rel_path}#scripts.{key}: {val}"
            commands[_cmd_category(key.lower())].append(entry)
        if os.path.basename(path) in ("Makefile", "makefile", "GNUmakefile"):
            # manifest["path"] 已被改写为 repo 相对路径 —— 读取必须并回
            # repo 解析(曾按进程 CWD 打开, 从别的目录扫描读到错误的
            # Makefile, 无 Makefile 的 CWD 则静默丢掉全部构建目标)
            full = path if os.path.isabs(path) else os.path.join(repo, path)
            text = read_text(full, 400_000)
            for target in re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", text, re.M):
                if target.startswith("."):
                    continue
                commands[_cmd_category(target.lower())].append(f"make {target}")
        low_path = path.lower()
        if low_path.endswith("go.mod"):
            commands["test"].append("go test ./...")
            commands["build"].append("go build ./...")
        if low_path.endswith("cargo.toml"):
            commands["test"].append("cargo test")
            commands["build"].append("cargo build")
        if low_path.endswith("build.gradle") or low_path.endswith("build.gradle.kts"):
            commands["test"].append("./gradlew test")
            commands["build"].append("./gradlew build")
        if low_path.endswith("pom.xml"):
            commands["test"].append("mvn test")
            commands["build"].append("mvn package")
    for k in commands:
        commands[k] = sorted(dict.fromkeys(commands[k]))[:40]
    return commands


def score_entry(rel: str) -> int:
    base = os.path.basename(rel)
    score = 0
    if base in ENTRY_BASENAMES:
        score += 50
    if any(h in "/" + rel for h in ENTRY_PATH_HINTS):
        score += 20
    if rel.count("/") <= 2:
        score += 10
    if any(x in rel.lower() for x in ("test", "spec", "mock", "fixture", "example")):
        score -= 30
    return score


def build_tree(files: list) -> dict:
    root = {"name": "", "dirs": {}, "files": []}
    for f in files:
        parts = f["path"].split("/")
        node = root
        for part in parts[:-1]:
            node = node["dirs"].setdefault(part, {"name": part, "dirs": {}, "files": []})
        node["files"].append(f)

    def totals(node):
        lines = sum(max(0, f.get("lines") or 0) for f in node["files"])
        count = len(node["files"])
        for child in node["dirs"].values():
            c, l = totals(child)
            count += c
            lines += l
        node["_files"] = count
        node["_lines"] = lines
        return count, lines

    totals(root)
    return root


def render_tree(root: dict, max_lines: int = 4000, max_files_per_dir: int = 30) -> str:
    out = [".\n"]

    def walk(node: dict, prefix: str):
        if len(out) >= max_lines:
            return
        dirs = sorted(node["dirs"].values(), key=lambda n: n["name"].lower())
        files = sorted(node["files"], key=lambda f: f["path"].lower())
        entries = []
        for d in dirs:
            entries.append(("dir", d))
        shown = files[:max_files_per_dir]
        for f in shown:
            entries.append(("file", f))
        if len(files) > max_files_per_dir:
            entries.append(("more", len(files) - max_files_per_dir))
        for idx, (kind, item) in enumerate(entries):
            if len(out) >= max_lines:
                break
            last = idx == len(entries) - 1
            branch = "└── " if last else "├── "
            nxt = "    " if last else "│   "
            if kind == "dir":
                out.append(f"{prefix}{branch}{item['name']}/\n")
                walk(item, prefix + nxt)
            elif kind == "file":
                out.append(f"{prefix}{branch}{os.path.basename(item['path'])}\n")
            else:
                out.append(f"{prefix}{branch}... {item} more files\n")

    walk(root, "")
    if len(out) >= max_lines:
        out.append("... tree truncated\n")
    return "".join(out)


def scan_repo(repo: str, max_files: int = 50000, extra_ignore=()) -> dict:
    """核心扫描(纯函数; cmd_scan 只做 IO 包装)。

    extra_ignore 是 repo 相对路径条目('src/generated' 只剪那一处,
    顶层裸名等价于顶层路径)。符号链接一律跳过并计数: 目录链接不追
    (os.walk 默认), 文件链接也不收 —— 曾文件链接入册而目录链接隐形,
    同一内容双计/外链内容混入且可能逃出 repo。"""
    extra_ignore = {x.strip("/") for x in extra_ignore if x}

    def _rel_ignored(dirpath: str) -> bool:
        rel = posix(os.path.relpath(dirpath, repo))
        if rel == ".":
            return False
        return any(rel == it or rel.startswith(it + "/") for it in extra_ignore)

    files: list = []
    manifests: list = []
    languages: Counter = Counter()
    lang_lines: Counter = Counter()
    total_lines = 0
    skipped_large = 0
    skipped_symlinks = 0

    for root, dirs, names in os.walk(repo):
        dirs[:] = sorted(
            d for d in dirs
            if not _dir_ignored(d)
            and not os.path.islink(os.path.join(root, d))
        )
        if _rel_ignored(root):
            dirs[:] = []
            continue
        for name in sorted(names):
            if len(files) >= max_files:
                # 内层也要断: 单个平铺大目录(8 万文件的产物堆)曾把上限
                # 整个绕过, 全量行计数 + manifest 解析
                break
            full = os.path.join(root, name)
            if os.path.islink(full):
                skipped_symlinks += 1
                continue
            rel = posix(os.path.relpath(full, repo))
            ext = os.path.splitext(name)[1].lower()
            if name in MANIFESTS or name.startswith("requirements") and ext == ".txt":
                try:
                    manifest = parse_manifest(full)
                    manifest["path"] = rel
                    manifests.append(manifest)
                except Exception as exc:  # noqa: BLE001 — 单文件失败不拖垮清单
                    manifests.append({"type": "error", "path": rel, "error": str(exc)})
            if name.endswith(GENERATED_SUFFIXES):
                continue
            if ext in BINARY_EXT:
                languages["binary"] += 1
                continue
            lang = LANG_BY_EXT.get(ext)
            if not lang and name in ("Dockerfile",):
                lang = "docker"
            if not lang:
                if name in (".gitignore", ".gitattributes", ".editorconfig",
                            ".env", ".env.example"):
                    lang = "config"
                elif name == ".gitkeep":
                    # qwiki bug17: 占位目录检测需要看到标记文件
                    lang = "marker"
                else:
                    continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            lines = count_lines(full)
            if lines < 0:
                skipped_large += 1
                lines = 0
            files.append({"path": rel, "lang": lang, "lines": lines, "bytes": size})
            languages[lang] += 1
            lang_lines[lang] += lines
            total_lines += lines

    workspace = {"pnpm": [], "npm": [], "go_work": [], "cargo": [], "gradle": [],
                 "uv": [], "maven": []}
    for m in manifests:
        t = m.get("type")
        if t == "pnpm-workspace":
            workspace["pnpm"].extend(m.get("packages") or [])
        elif t == "npm":
            workspace["npm"].extend(m.get("workspaces") or [])
        elif t == "go-work":
            workspace["go_work"].extend(m.get("use") or [])
        elif t == "cargo":
            workspace["cargo"].extend(m.get("workspace_members") or [])
        elif t == "gradle":
            workspace["gradle"].extend(m.get("includes") or [])
        elif t == "python":
            workspace["uv"].extend(m.get("workspace_members") or [])
        elif t == "maven":
            workspace["maven"].extend(m.get("modules") or [])

    entry_points = []
    for f in files:
        s = score_entry(f["path"])
        if s >= 50:
            entry_points.append({"path": f["path"], "score": s, "lang": f["lang"]})
    entry_points.sort(key=lambda x: (-x["score"], x["path"]))
    entry_points = entry_points[:80]

    tree_text = render_tree(build_tree(files))

    readme = None
    for candidate in README_CANDIDATES:
        p = os.path.join(repo, candidate)
        if os.path.isfile(p):
            readme = {"path": candidate, "excerpt": read_text(p, 6000)}
            break

    # c049: 六个互不依赖的顺序子进程并成一个 log -1(三字段) + 两个单查
    # —— 每次 scan 少付 3 次进程 spawn(Windows/NFS 下每次 50-100ms)
    _head3 = git(repo, "log", "-1", "--pretty=%H%n%h%n%cI").splitlines()
    _head3 += [""] * (3 - len(_head3))
    git_info = {
        "head": _head3[0],
        "short_head": _head3[1],
        "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit_count": git(repo, "rev-list", "--count", "HEAD"),
        "last_commit": _head3[2],
    }
    churn = Counter()
    log = git(repo, "log", "-n", "2000", "--name-only", "--pretty=format:")
    for line in log.splitlines():
        line = line.strip()
        if line:
            churn[line] += 1
    git_info["top_churn"] = [{"path": p, "commits": c} for p, c in churn.most_common(50)]

    ci = []
    for candidate, label in ((".github/workflows", "github-actions"),
                             (".gitlab-ci.yml", "gitlab-ci"), ("Jenkinsfile", "jenkins"),
                             (".circleci", "circleci"),
                             ("azure-pipelines.yml", "azure-pipelines"),
                             (".travis.yml", "travis")):
        if os.path.exists(os.path.join(repo, candidate)):
            ci.append(label)

    return {
        "schema_version": 2,
        "repo": repo,
        "repo_name": os.path.basename(repo.rstrip("/")) or "repo",
        "generated_at": now_iso(),
        "stats": {
            "files": len(files),
            "lines": total_lines,
            "bytes": sum(f["bytes"] for f in files),
            "skipped_large": skipped_large,
            "skipped_symlinks": skipped_symlinks,
        },
        "languages": [
            {"lang": k, "files": languages[k], "lines": lang_lines[k]}
            for k in sorted(languages, key=lambda x: (-lang_lines.get(x, 0), x))
        ],
        "workspace": workspace,
        "manifests": manifests,
        "commands": detect_commands(manifests, repo),
        "entry_points": entry_points,
        "git": git_info,
        "ci": ci,
        "readme": readme,
        "tree_text": tree_text,
        "files": files,
    }


def inventory_path(ctx: Ctx) -> str:
    return os.path.join(ctx.root, "work", "inventory.json")


# 展开缓存(r4 #73/#74): 进程级 (repo, scope) → 文件集。CLI 单命令单进程,
# 一次 update/finish 内同一目录被走 3-5 遍(snapshot 基线 + mod_files +
# check_item 的 srcs/old_srcs)—— 10k 文件仓库每次数万次冗余 stat。
# 进程内 FS 不变, 缓存安全; 测试进程多仓库共存, 键带 repo。
_SCOPE_CACHE: dict = {}


def expand_scope(repo: str, entries: dict, seen: set, scope: str) -> None:
    """把一条 scope(目录/文件/glob)展开成相对文件路径, 填进 entries。

    移植 qwiki _expand_scope: 目录尾 '/' 递归; '**'/'*' 前缀截成目录前缀;
    忽略 DEFAULT_IGNORES 与二进制。modules.py(seed_depends_on)与
    snapshot.py(快照跟踪集)共用。c049 同族: scope 来自计划(手改面),
    越界(绝对/../)整条丢弃 —— 展开绝不能走出 repo。"""
    for rel in _expand_scope_uncached(repo, scope):
        if rel not in seen:
            seen.add(rel)
            entries[rel] = True


def _expand_scope_uncached(repo: str, scope) -> tuple:
    from .util import join_confined
    s = str(scope).strip().rstrip("/")
    if not s:
        return ()
    # 尾斜杠在入口统一归一: 'src/main.py/'(手滑)曾带着斜杠进目录分支而
    # isdir 为假 —— 空集被缓存, 且缓存键与 'src/main.py' 共享, 同进程
    # 内后来无斜杠的查询也被污染成空(校验还放行: _check_repo_path 正是
    # rstrip 后 exists) —— 文件被大改后 update 永久静默陈旧
    key = (repo, s)
    hit = _SCOPE_CACHE.get(key)
    if hit is not None:
        return hit
    out: set = set()
    if join_confined(repo, s) is None:
        _SCOPE_CACHE[key] = ()
        return ()
    # 文件系统操作沿用调用方 repo 形态的 join(repo 可能本身是符号链接,
    # realpath 形态会让 relpath 算出 ../.. 串)
    base = os.path.join(repo, s)
    # 目录判定看文件系统而不是尾斜杠: 手编 scope 'src'(无 '/')曾静默落进
    # 文件分支不展开 —— 快照基线与 update 变更集双双跟踪 0 文件,
    # scope-only 页永远报 no-op。调用方(expand_dirs)各自的 s+'/' 补丁
    # 从此多余但无害。
    if os.path.isdir(base):
        for root, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs
                             if d not in config.DEFAULT_IGNORES
                             and not _dir_ignored(d)
                             and not os.path.islink(os.path.join(root, d)))
            for name in sorted(names):
                full = os.path.join(root, name)
                # 文档化的契约"忽略 DEFAULT_IGNORES 与二进制": BINARY_EXT
                # 曾没应用 —— logo.png/sqlite 入跟踪集, 一次资产重导出就把
                # 整个模块的页面排队全量重写。符号链接同理不入集(防逃逸)。
                if os.path.splitext(name)[1].lower() in BINARY_EXT:
                    continue
                if name.endswith(GENERATED_SUFFIXES):
                    continue
                if os.path.islink(full):
                    continue
                out.add(posix(os.path.relpath(full, repo)))
        _SCOPE_CACHE[key] = frozenset(out)
        return _SCOPE_CACHE[key]
    # glob 前缀(** / *)截断到最近的稳定目录
    if "*" in s:
        cut = s.split("*")[0].rstrip("/")
        if cut:
            merged = _expand_scope_uncached(repo, cut + "/")
            _SCOPE_CACHE[key] = merged
            return merged
        _SCOPE_CACHE[key] = ()
        return ()
    if os.path.isfile(base) and not os.path.islink(base):
        out.add(posix(os.path.relpath(base, repo)))
    _SCOPE_CACHE[key] = frozenset(out)
    return _SCOPE_CACHE[key]



def load_inventory(ctx: Ctx) -> dict:
    path = inventory_path(ctx)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            inv = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(inv, dict):
        return {}
    # 清单携带的 repo 必须与当前仓库一致: 克隆被移动/.pwiki 从备份恢复后,
    # 旧路径清单曾照常喂给 decompose —— 标题降级、workspace 成员丢失、
    # corpus 按旧路径全空, 零警告 exit 0; 复制场景更是持续分析过时副本。
    # 对不上视为无效 → 调用方触发 _autoscan(幂等, 只多一次重扫)。
    # abspath 归一(不比 realpath: 对已移动的旧路径无意义, 且符号链接形态
    # 的 repo 误判代价只是一次重扫)
    inv_repo = inv.get("repo")
    if isinstance(inv_repo, str) and inv_repo \
            and os.path.abspath(inv_repo) != os.path.abspath(ctx.repo):
        return {}
    return inv


def write_inventory(ctx: Ctx, inv: dict) -> str:
    """inventory 落盘的唯一实现(cmd_scan 与 modules._autoscan 共用, c069:
    两处各写一份 makedirs+json.dump 曾漂移出格式分叉)。"""
    from .util import atomic_write   # P56: 中断不留半份清单
    out_path = inventory_path(ctx)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    atomic_write(out_path, json.dumps(inv, ensure_ascii=False, indent=2))
    return out_path


def cmd_scan(args) -> int:
    ctx = Ctx(args)   # 不 load(): scan 不需要 DB, init 之前也能跑
    inv = scan_repo(ctx.repo, max_files=args.max_files,
                    extra_ignore=getattr(args, "ignore", ()) or ())
    out_path = write_inventory(ctx, inv)

    summary = (f"repo: {inv['repo']}\n"
               f"files: {inv['stats']['files']}  lines: {inv['stats']['lines']}  "
               f"languages: {len(inv['languages'])}\n"
               "top languages: " + ", ".join(
                   f"{x['lang']}({x['files']})" for x in inv["languages"][:6]) + "\n"
               f"manifests: {len(inv['manifests'])}  entries: {len(inv['entry_points'])}"
               f"  git: {inv['git']['short_head'] or 'n/a'}\n"
               f"wrote: {out_path}")
    print(summary[:4000])
    return config.EXIT_OK
