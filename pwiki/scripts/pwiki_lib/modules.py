"""模块分解: detect_modules(D1 覆盖断言) + 注册表转换 + depends_on 播种。

移植:
  - repowiki/scripts/detect_modules.py 全量(容器细分/生态分组/pick_source
    打分/D1 覆盖断言/骨架标记 D8)
  - qwiki seed_depends_on(import 语句启发式; yudao 硬编码规则 → plan 级
    aliases: 数据化)

输出 = plan v3 的 modules: 注册表(由 cmd_modules 写入 plan + DB)。
"""

from __future__ import annotations

import fnmatch
import glob
import json
import os
import re

from . import config
from .config import Ctx
from .scan import expand_scope, load_inventory, read_text
from . import util as _u
from .util import info

NOISE_DIRS = {
    "tests", "test", "__tests__", "spec", "specs", "fixtures", "fixture", "mocks",
    "assets", "public", "static", "styles", "images", "img", "icons", "fonts",
    "dist", "build", "out", "coverage", "node_modules", "vendor", "examples",
    "scratch", "tmp", "temp", "playground",
    "scripts", "bin", "obj", "target", ".next", ".turbo", "__pycache__",
}

CONTAINERS = {"apps", "services", "packages", "libs", "modules", "crates",
              "internal", "cmd", "tools", "infra", "proto"}

TITLE_MAP = {
    "client": "客户端", "server": "服务端", "docs": "文档", "doc": "文档",
    "infra": "基础设施", "infrastructure": "基础设施",
    "packages": "共享包集合", "package": "共享包", "proto": "契约定义",
    "tools": "工程工具", "scripts": "脚本", "apps": "应用集合",
    "page-prototype": "原型页面",
    "page_prototype": "原型页面", "src-tauri": "Tauri 桌面后端", "src": "源码",
    "desktop": "桌面应用", "web": "Web 应用", "mobile": "移动端",
    "api": "API 层", "core": "核心逻辑", "platform": "平台适配层",
    "services_layer": "服务层", "stores": "状态存储层", "types": "类型定义",
    # c077: 原第二张硬编码表(fixed)的独有口径并进来 —— 同一概念一张表
    # (services 取后者"后端服务", 与旧 fixed 表的全路径口径一致)
    "services": "后端服务",
    "components": "组件层", "app": "应用入口", "lib": "通用库", "i18n": "国际化",
}

ECO_TITLE = {
    "java": "Java 服务集合", "go": "Go 服务集合", "python": "Python 服务集合",
    "npm": "TypeScript 服务集合", "rust": "Rust 模块集合", "docs": "文档集合",
}

ECO_EXT = {
    "java": {".java", ".kt", ".kts"},
    "go": {".go"},
    "python": {".py", ".pyi"},
    "npm": {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"},
    "rust": {".rs"},
}


posix = _u.posix     # 路径 posix 形态(单一实现, 与 util 同源)
dirname = _u.dirname  # 父目录(单一实现; 两套手写版曾分组不一致)


def depth(p: str) -> int:
    return 0 if not p else p.count("/") + 1


def humanize(name: str) -> str:
    """路径/目录名 → 人类标题的单一实现(themes 经薄封装复用)。

    取 basename → TITLE_MAP 术语表 → 剥 STRIP_EXTS 扩展名(长度倒序,
    '.ts' 不误吃 '.mts')→ 连字符/下划线分词首字母大写。两份同名手抄曾
    各自漂移: 这份认表不剥扩展名('main.py'→'Main.py'), themes 那份剥
    扩展名不认表('client' 两路两题)。"""
    base = posix(str(name)).split("/")[-1]
    if base in TITLE_MAP:
        return TITLE_MAP[base]
    for ext in config.STRIP_EXTS:
        if base.endswith(ext):
            base = base[:-len(ext)]
            break
    words = re.split(r"[-_]+", base)
    return " ".join(w if len(w) <= 3 else w[:1].upper() + w[1:]
                    for w in words if w)


def readme_title(repo: str, rel_dir: str):
    for name in ("README.md", "readme.md", "README.rst", "README"):
        p = os.path.join(repo, rel_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                for _ in range(20):
                    line = fh.readline()
                    if not line:
                        break
                    m = re.match(r"^\s*#\s+(.+?)\s*$", line)
                    if m and 2 <= len(m.group(1)) <= 48:
                        return m.group(1)
        except OSError:
            pass
    return None


# --------------------------------------------------------------------------- #
# 核心分解(纯函数; repowiki detect_modules.main 的主体)
# --------------------------------------------------------------------------- #

def decompose(inv: dict, max_modules: int = 40, min_files: int = 4,
              sub_min_files: int = 8, subdivide: bool = True) -> dict:
    """确定性分解。返回 {modules: dict[path->entry], order, absorbed, remaining}。
    remaining 非空 ⇒ 调用方必须失败(D1)。"""
    repo = inv["repo"]
    files = inv["files"]
    manifests = inv.get("manifests", [])
    workspace = inv.get("workspace", {})

    file_paths = {f["path"] for f in files}
    dir_files: dict = {}
    for f in files:
        d = dirname(f["path"])
        dir_files.setdefault(d, []).append(f)
        parts = d.split("/") if d else []
        for i in range(1, len(parts)):
            dir_files.setdefault("/".join(parts[:i]), []).append(f)

    def direct_files(d: str) -> list:
        return dir_files.get(d, [])

    def stats(d: str):
        fs = direct_files(d)
        return len(fs), sum(max(0, f.get("lines") or 0) for f in fs)

    def eco_of(d: str) -> str:
        exts = {os.path.splitext(f["path"])[1].lower() for f in direct_files(d)}
        best, best_n = "other", 0
        for eco, wanted in ECO_EXT.items():
            n = len(exts & wanted)
            if n > best_n:
                best, best_n = eco, n
        return best

    manifest_dirs = {dirname(m["path"]) for m in manifests if dirname(m["path"])}

    workspace_dirs: set = set()

    def add_dir(rel: str):
        from .util import join_confined
        rel = rel.strip().strip("/")
        rel = rel[2:] if rel.startswith("./") else rel
        if not rel:
            return
        # c049 同族: workspace 文件内容(仓库作者可控)不得把模块目录
        # 指到 repo 之外
        if join_confined(repo, rel) is None:
            return
        full = os.path.join(repo, rel)
        if os.path.isdir(full):
            workspace_dirs.add(posix(os.path.relpath(full, repo)))

    for pattern in workspace.get("pnpm", []) + workspace.get("npm", []):
        if not pattern or pattern.startswith("!"):
            continue
        if any(ch in pattern for ch in "*?["):
            for x in glob.glob(os.path.join(repo, pattern)):
                if os.path.isdir(x):
                    add_dir(os.path.relpath(x, repo))
        else:
            add_dir(pattern)
    for rel in workspace.get("go_work", []) + workspace.get("cargo", []) + \
            workspace.get("uv", []):
        add_dir(rel)
    for inc in workspace.get("gradle", []):
        inc = inc.lstrip(":").replace(":", "/")
        for cand in (inc, f"services/{inc}", f"apps/{inc}", f"modules/{inc}"):
            add_dir(cand)
    for rel in workspace.get("maven", []):
        add_dir(rel)

    top_dirs = sorted({p.split("/")[0] for p in file_paths if "/" in p})
    cohesive = {d for d in top_dirs if d not in NOISE_DIRS and stats(d)[0] >= min_files}

    leaf = set(manifest_dirs) | workspace_dirs | cohesive
    leaf = {d for d in leaf if d and stats(d)[0] >= 1}
    leaf = {d for d in leaf if depth(d) <= 4}

    # ----- 容器细分 -----
    submodules: set = set()
    if subdivide:
        for d in sorted(leaf, key=lambda x: (depth(x), x)):
            nfiles, nlines = stats(d)
            if nfiles < 30 or nlines < 1200:
                continue
            # P77: 只扫本子树(dir_files 祖先映射已枚举) —— 曾对全仓
            # 文件列表逐个 startswith(30-50 个合格叶子 × 10k 文件)
            direct_subdirs = sorted({
                "/".join(f["path"].split("/")[:depth(d) + 1])
                for f in direct_files(d)
                if f["path"].count("/") > depth(d)
            })
            candidates = []
            for sd in direct_subdirs:
                base = sd.split("/")[-1]
                if base in NOISE_DIRS or sd in leaf:
                    continue
                sfiles, slines = stats(sd)
                code_ratio = len([f for f in direct_files(sd)
                                  if f["lang"] not in ("markdown", "json",
                                                       "yaml", "text")]) / max(1, sfiles)
                if sfiles >= sub_min_files and slines >= 250 and code_ratio >= 0.4:
                    candidates.append((slines, sd))
            candidates.sort(reverse=True)
            if len(candidates) == 1:
                base = candidates[0][1].split("/")[-1]
                share = stats(candidates[0][1])[0] / max(1, nfiles)
                if base in {"src", "lib", "source", "app", "internal", "pkg"} \
                        and share >= 0.65:
                    candidates = []
            for _, sd in candidates[:8]:
                submodules.add(sd)
        leaf |= submodules

    # ----- 容器下兄弟按生态分组合成模块 -----
    children_of: dict = {}
    for d in leaf:
        parent = d.rsplit("/", 1)[0] if "/" in d else ""
        children_of.setdefault(parent, []).append(d)

    synthetic: dict = {}
    remap: dict = {}

    for parent, kids in list(children_of.items()):
        if not parent or len(kids) < 4:
            continue
        top = parent.split("/")[0]
        if top in ("apps",):
            continue
        by_eco: dict = {}
        for k in kids:
            by_eco.setdefault(eco_of(k), []).append(k)
        if len(by_eco) <= 1 and top != "packages":
            continue
        for eco, group in by_eco.items():
            if len(group) < 3 and not (top == "packages" and len(group) >= 2):
                continue
            group_files = sum(stats(g)[0] for g in group)
            if group_files < 10 and top != "packages":
                continue
            key = f"{parent}/_group_{eco}"
            synthetic[key] = {
                "module_path": key,
                "title": ECO_TITLE.get(eco, f"{humanize(parent)} - {eco}"),
                "scope": [g + "/" for g in sorted(group)],
                "source_files": [], "depends_on": [], "related_to": [],
                "synthetic": True, "parent": parent,
                "children": sorted(group), "ecosystem": eco,
            }
            for g in group:
                remap[g] = key

    # ----- 模块条目 -----
    modules: dict = {}

    def module_entry(path: str, synthetic_data=None) -> dict:
        if synthetic_data:
            entry = dict(synthetic_data)
            entry["files"] = sum(stats(s.rstrip("/"))[0] for s in entry["scope"])
            entry["lines"] = sum(stats(s.rstrip("/"))[1] for s in entry["scope"])
            return entry
        nfiles, nlines = stats(path)
        title = humanize(path)
        rt = readme_title(repo, path)
        if rt and len(rt) <= 24 and not any(ch in rt for ch in "·—|/"):
            title = rt
        # c077: 目录名→标准中文标题以 TITLE_MAP 为唯一来源(优先于 readme
        # 标题, 与原硬编码表同序); readme 可读性让位给稳定性
        if path in TITLE_MAP:
            title = TITLE_MAP[path]
        return {
            "module_path": path, "title": title, "scope": [path + "/"],
            "source_files": [], "depends_on": [], "related_to": [],
            "synthetic": False,
            "parent": path.rsplit("/", 1)[0] if "/" in path else "",
            "children": [], "ecosystem": eco_of(path),
            "files": nfiles, "lines": nlines,
        }

    for d in sorted(leaf):
        modules[d] = module_entry(d)
        if d in remap:
            modules[d]["parent"] = remap[d]
    for key, data in synthetic.items():
        modules[key] = module_entry("", data)

    for d in list(modules):        # 中间目录补父模块
        parent = modules[d]["parent"]
        while parent:
            if parent not in modules:
                modules[parent] = module_entry(parent)
            parent = modules[parent]["parent"]

    for d, entry in modules.items():
        entry["children"] = []
    for d, entry in modules.items():
        p = entry.get("parent", "")
        if p in modules:
            modules[p]["children"].append(d)
    for kids in [v["children"] for v in modules.values()]:
        kids.sort(key=lambda x: (-modules[x]["files"], x))

    # root
    top_children = sorted([d for d in modules if "/" not in d],
                          key=lambda x: (-modules[x]["files"], x))
    repo_title = readme_title(repo, "") or inv.get("repo_name", "repo")
    root_title = f"{repo_title} 工作区" if len(top_children) > 1 else repo_title
    root = {
        "module_path": "", "title": root_title, "scope": [],
        "source_files": [], "depends_on": [], "related_to": [],
        "synthetic": False, "parent": None, "children": top_children,
        "ecosystem": "mixed", "files": len(files),
        "lines": sum(max(0, f.get("lines") or 0) for f in files),
    }
    for f in files:
        if f["path"].count("/") == 0 and f["path"] in {
                "README.md", "README.rst", "package.json", "pyproject.toml",
                "go.work", "Cargo.toml", "settings.gradle",
                "settings.gradle.kts", "Makefile", "docker-compose.yml",
                "docker-compose.yaml", "AGENTS.md", "CLAUDE.md"}:
            root["scope"].append(f["path"])
    modules[""] = root
    for d in top_children:
        modules[d]["parent"] = ""

    # ----- pick_source 打分 -----
    entry_paths = {e["path"] for e in inv.get("entry_points", [])}
    manifest_paths = {m["path"] for m in manifests}

    def pick_source(d: str) -> list:
        fs = direct_files(d)
        scored = []
        for f in fs:
            p = f["path"]
            base = os.path.basename(p)
            score = 0
            if p in manifest_paths:
                score += 200
            if base in {".gitignore", ".gitattributes", "LICENSE", "LICENSE.md",
                        "skills-lock.json", "brainstorming.txt",
                        "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
                        "uv.lock", "Cargo.lock", "go.sum"}:
                score -= 300
            if p in entry_paths:
                score += 80
            if base.lower().startswith("readme"):
                score += 70
            if base in ("index.ts", "index.tsx", "index.js", "mod.rs", "lib.rs",
                        "main.go", "main.py", "main.rs", "__init__.py"):
                score += 45
            if any(x in p.lower() for x in ("core/", "domain/", "service",
                                            "model", "schema", "api/", "config",
                                            "engine", "store", "route")):
                score += 22
            if p.count("/") <= 2:
                score += 12
            if 20 <= (f.get("lines") or 0) <= 600:
                score += 10
            if any(x in p.lower() for x in ("test", "spec", "mock", "fixture",
                                            "generated")):
                score -= 35
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [p for _, p in scored[:8]]

    for d, entry in modules.items():
        if d == "":
            entry["source_files"] = pick_source("")
        elif entry.get("synthetic"):
            seen = []
            for s in entry["scope"]:
                for p in pick_source(s.rstrip("/")):
                    if p not in seen:
                        seen.append(p)
                    if len(seen) >= 8:
                        break
                if len(seen) >= 8:
                    break
            entry["source_files"] = seen
        else:
            entry["source_files"] = pick_source(d)

    # ----- 依赖边(manifest 名字匹配) -----
    name_to_module: dict = {}
    for m in manifests:
        d = dirname(m["path"])
        name = m.get("name") or m.get("module") or m.get("artifact")
        if name and d in modules:
            name_to_module[str(name).lower()] = d
            name_to_module.setdefault(str(name).split("/")[-1].lower(), d)

    for d, entry in modules.items():
        if entry.get("synthetic"):
            continue
        deps = set()
        for m in manifests:
            if dirname(m["path"]) != d:
                continue
            raw_deps = m.get("dependencies") or {}
            dep_names = raw_deps.keys() if isinstance(raw_deps, dict) else raw_deps
            for dep in dep_names:
                key = str(dep).lower()
                target = name_to_module.get(key) or \
                    name_to_module.get(key.split("/")[-1])
                if target and target != d:
                    deps.add(target)
        entry["depends_on"] = sorted(deps)
        if not deps:
            siblings = [x for x in modules.get(entry.get("parent", ""),
                                               {}).get("children", []) if x != d]
            entry["related_to"] = [{"path": x} for x in siblings[:3]]

    # ----- 预算裁剪 -----
    if len(modules) > max_modules:
        removable = [d for d, e in modules.items()
                     if d and not e.get("synthetic") and depth(d) >= 2
                     and e.get("files", 0) < 30]
        removable.sort(key=lambda d: (modules[d].get("files", 0), -depth(d)))
        while len(modules) > max_modules and removable:
            d = removable.pop(0)
            p = modules[d].get("parent", "")
            modules.pop(d, None)
            if p in modules:
                modules[p]["children"] = [c for c in modules[p]["children"]
                                          if c != d]
        for d in [x for x, e in modules.items()
                  if x and not e["children"] and not e["source_files"]]:
            p = modules[d].get("parent", "")
            modules.pop(d, None)
            if p in modules:
                modules[p]["children"] = [c for c in modules[p]["children"]
                                          if c != d]

    # ----- D1 覆盖断言: 任何源目录不得被静默丢失 -----
    def parent_of(d: str) -> str:
        return d.rsplit("/", 1)[0] if "/" in d else ""

    def subdivided(d: str) -> bool:
        return bool(modules.get(d, {}).get("children"))

    def compute_covered() -> set:
        covered = set()
        for mp, e in modules.items():
            if mp and "/" not in mp and mp in CONTAINERS and e.get("children"):
                continue   # 被细分的容器: 覆盖由其子模块承担
            covered.add(mp)
            for s in e.get("scope", []):
                covered.add(s.rstrip("/"))
            covered.update(e.get("children", []))
        return covered

    covered = compute_covered()

    candidates: set = set()
    for m in manifests:
        d = dirname(m["path"])
        if d:
            candidates.add(d)
    candidates.update(workspace_dirs)
    for f in files:
        parts = f["path"].split("/")
        if len(parts) > 2 and parts[0] in CONTAINERS:
            candidates.add("/".join(parts[:2]))

    def find_orphans() -> list:
        orphans = []
        for d in sorted(candidates):
            if d in covered or d in NOISE_DIRS:
                continue
            chain, cur = [], d
            while cur:
                chain.append(cur)
                cur = parent_of(cur)
            unc = [x for x in reversed(chain) if x not in covered]
            if not unc:
                continue
            top = unc[0]
            par = parent_of(top)
            if par in covered and not subdivided(par):
                continue
            if par == "" and not modules.get("", {}).get("children"):
                continue
            orphans.append(top)
        return orphans

    orphans = find_orphans()
    absorbed: list = []
    for d in sorted(orphans):
        par = parent_of(d)
        eco = eco_of(d)
        group = None
        for c in modules.get(par, {}).get("children", []):
            g = modules.get(c, {})
            if g.get("synthetic") and g.get("ecosystem") == eco:
                group = g
                break
        entry = module_entry(d)
        if group is not None:
            entry["parent"] = group["module_path"]
            group["scope"] = sorted(set(group["scope"]) | {d + "/"})
            group["children"] = sorted(set(group["children"]) | {d})
            group["files"] = sum(stats(s.rstrip("/"))[0] for s in group["scope"])
            group["lines"] = sum(stats(s.rstrip("/"))[1] for s in group["scope"])
            absorbed.append((d, group["module_path"]))
        else:
            entry["parent"] = par
            if par in modules:
                modules[par]["children"] = sorted(
                    set(modules[par]["children"]) | {d})
            absorbed.append((d, par))
        modules[d] = entry
        covered.add(d)

    # 骨架标记(D8): 几乎没有代码文件的模块是占位
    for mp, e in modules.items():
        if not mp or e.get("synthetic"):
            continue
        code = [f for f in direct_files(mp)
                if f["lang"] not in ("markdown", "json", "yaml", "toml",
                                     "text", "config", "docker", "ini", "marker")]
        e["skeleton"] = len(code) <= 1 and e.get("files", 0) >= 1

    # 终局断言: 同规则重算, 幸存孤儿 ⇒ 失败
    covered = compute_covered()
    remaining = [d for d in find_orphans()]

    order = []

    def walk(key: str):
        order.append(key)
        for c in modules.get(key, {}).get("children", []):
            if c in modules:
                walk(c)

    walk("")
    return {"modules": modules, "order": order,
            "absorbed": absorbed, "remaining": remaining,
            "candidates": sorted(candidates), "covered": covered}


# --------------------------------------------------------------------------- #
# 注册表转换(repowiki 形状 → plan v3 modules)
# --------------------------------------------------------------------------- #

def to_plan_modules(decomposed: dict) -> list:
    """path 键控的分解结果 → plan v3 modules 列表(root id=root, parent 用 id)。"""
    mods = decomposed["modules"]
    # 工作区根(path '')固定占用 id 'root'(全树 parent 锚点)。顶层恰好
    # 名叫 root/ 的目录曾与之共享同一 id —— modules_by_id last-wins 吞并
    # (sheet 路径/摘要表交叉污染), begin 的重复 id 检查再把已写入的 plan
    # 拒之门外。目录 root/ 改发 'root-dir'
    id_of = {path: ("root" if not path else
                    ("root-dir" if path == "root" else path))
             for path in mods}
    out = []
    for path in sorted(mods):
        e = mods[path]
        mid = id_of[path]
        module_type = "group" if e.get("synthetic") else (
            "skeleton" if e.get("skeleton") else (
                "container" if e.get("children") and path in CONTAINERS else "real"))
        related = []
        for r in e.get("related_to") or []:
            rp = r.get("path") if isinstance(r, dict) else r
            if rp in id_of:
                related.append(id_of[rp])
        out.append({
            "id": mid,
            "title": e["title"],
            "dir": (path + "/") if path else "",
            "path": path or "/",
            "parent": "" if mid == "root" else (
                id_of.get(e.get("parent") or "") or "root"),
            "scope": e.get("scope") or [],
            "depends_on": [id_of[d] for d in e.get("depends_on") or []
                           if d in id_of],
            "related_to": related,
            "notes": (f"{e.get('files', 0)} 文件 / {e.get('lines', 0)} 行"
                      f" / {e.get('ecosystem', 'mixed')}"
                      if path else "根级配置、清单与入口文件"),
            "module_type": module_type,
            "skeleton": bool(e.get("skeleton")),
            "evidence": {"files": e.get("files", 0), "lines": e.get("lines", 0),
                         "ecosystem": e.get("ecosystem", "mixed"),
                         "source_files": e.get("source_files", [])[:8]},
        })
    # root 排最前
    out.sort(key=lambda m: (m["id"] != "root",))
    return out


# --------------------------------------------------------------------------- #
# depends_on 播种(qwiki seed_depends_on; yudao 规则 → aliases 数据)
# --------------------------------------------------------------------------- #

def _imported_names(text: str) -> set:
    """尽力而为的 import 名(py/js/ts/go/java)。qwiki bug7: py 的每个点分段
    都是候选; java 取首段。"""
    names = set()
    for m in re.finditer(r"^[ \t]*(?:from|import)[ \t]+([\w.]+)[ \t]+import", text, re.M):
        names.update(m.group(1).split("."))
    for m in re.finditer(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]*,[ \t]*[\w.]+)*)"
                         r"[ \t]*(?:#.*)?$", text, re.M):
        for part in m.group(1).split(","):
            names.update(part.strip().split(" as ")[0].strip().split("."))
    for m in re.finditer(r"\bfrom[ \t]+['\"]([^'\"]+)['\"]", text):
        names.add(m.group(1).split("/")[0].lstrip("."))
    for m in re.finditer(r"\brequire\([ \t]*['\"]([^'\"]+)['\"]", text):
        names.add(m.group(1).split("/")[0].lstrip("."))
    for m in re.finditer(r"^[ \t]*import[ \t]+\"([^\"]+)\"", text, re.M):
        names.add(m.group(1).split("/")[0])
    for m in re.finditer(r"import[ \t]*\(([^)]*)\)", text, re.S):
        for q in re.finditer(r"\"([^\"]+)\"", m.group(1)):
            names.add(q.group(1).split("/")[0])
    for m in re.finditer(r"^[ \t]*import[ \t]+([\w.]+);", text, re.M):
        names.add(m.group(1).split(".")[0])
    return {n.lower() for n in names
            if n and not n.isdigit() and len(n) <= 40}


def _pom_dep_artifacts(text: str) -> set:
    """活跃 <dependency> 块里的 artifactId。qwiki bug6: 先剥 XML 注释,
    被注释掉的依赖不泄漏。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    arts = set()
    for m in re.finditer(r"<dependency>.*?</dependency>", text, re.S):
        a = re.search(r"<artifactId>([\w.-]+)</artifactId>", m.group(0))
        if a:
            arts.add(a.group(1))
    return arts


_ALIAS_SUFFIXES = ("-api", "-server", "-biz")


def seed_depends_on(ctx: Ctx, plan: dict) -> int:
    """用模块 scope 内的证据补缺失的 depends_on: import 语句 + Maven pom
    <dependency>。别名规则来自 plan aliases:(qwiki 的 yudao 硬编码规则现在
    是数据: patterns: ["yudao-spring-boot-starter-*", "yudao-common*"],
    module: yudao-framework)。"""
    modules = plan.get("modules") or []
    if not modules:
        return 0
    ids = {str(m.get("id", "")) for m in modules}
    alias = {}
    for m in modules:
        mid = str(m.get("id", ""))
        dirbase = str(m.get("dir", "")).rstrip("/").split("/")[-1]
        keys = {mid, dirbase}
        for k in list(keys):
            for suf in _ALIAS_SUFFIXES:
                if k.endswith(suf):
                    keys.add(k[: -len(suf)])
        keys.add(str(m.get("title", "")))
        for k in keys:
            k = k.strip().lower().rstrip("/")
            if k and k not in ("", "."):
                alias.setdefault(k, mid)

    # plan 级别名表(fnmatch 模式 → 模块 id)
    alias_rules = []
    for rule in plan.get("aliases") or []:
        target = str(rule.get("module", ""))
        if target in ids:
            alias_rules.append((list(rule.get("patterns") or []), target))

    def resolve(name: str, self_id: str):
        n = name.lower()
        for suf in _ALIAS_SUFFIXES:
            if n.endswith(suf):
                n = n[: -len(suf)]
        if n in alias and alias[n] != self_id:
            return alias[n]
        for patterns, target in alias_rules:
            if target != self_id and any(fnmatch.fnmatch(n, p) for p in patterns):
                return target
        return None

    source_exts = config.SOURCE_EXTS
    mod_files = {}
    for m in modules:
        entries, seen = {}, set()
        for s in m.get("scope") or []:
            expand_scope(ctx.repo, entries, seen, str(s))
        mod_files[m.get("id", "")] = [
            p for p in sorted(entries)
            if os.path.splitext(p)[1].lower() in source_exts
            or os.path.basename(p) == "pom.xml"][:600]
    added = 0
    text_memo: dict = {}   # P74: 重叠 scope 的文件只读一次(曾每模块重读)
    for m in modules:
        if m.get("depends_on"):
            continue
        deps = set()
        for rel in mod_files.get(m.get("id", ""), []):
            from .util import join_confined
            # c049 同族: 跟踪集键经 expand_scope 已限仓内; 旧库/手工数据
            # 兜底 —— 越界键不读
            full = join_confined(ctx.repo, rel)
            if full is None:
                continue
            # 有界读取统一走 corpus.read_text(唯一实现; 这份手抄曾带
            # errors='replace' 与自有上限, 解码口径与主阅读器漂移)
            if full in text_memo:
                text = text_memo[full]
            else:
                text = read_text(full, 200_000)
                text_memo[full] = text
            if not text:
                continue
            names = (_pom_dep_artifacts(text)
                     if os.path.basename(rel) == "pom.xml"
                     else _imported_names(text))
            for n in names:
                target = resolve(n, str(m.get("id", "")))
                if target:
                    deps.add(target)
        if deps:
            m["depends_on"] = sorted(deps)
            added += 1
    return added


# --------------------------------------------------------------------------- #
# cmd_modules
# --------------------------------------------------------------------------- #

def _autoscan(ctx: Ctx) -> dict:
    """inventory 缺失时的确定性补扫(结果与 `pwiki scan` 完全一致)。"""
    from .scan import scan_repo, write_inventory
    inv = scan_repo(ctx.repo)
    write_inventory(ctx, inv)
    info("inventory 缺失 — 已自动补扫(work/inventory.json)")
    return inv


def cmd_modules(args) -> int:
    from .planner import load_plan, save_plan   # 惰性: planner 不反向导入本模块

    ctx = Ctx(args).load()
    inv = load_inventory(ctx)
    if not inv:
        inv = _autoscan(ctx)   # 确定性且廉价; 缺缓存时自动补扫而不是报错赶人
    dec = decompose(inv)
    for d, target in dec["absorbed"]:
        print(f"[coverage] absorbed orphan dir: {d} -> {target}")
    cand = len(dec["candidates"])
    rep = len(set(dec["covered"]) & set(dec["candidates"]))
    print(f"coverage: {rep}/{cand} candidate dirs represented, "
          f"orphans remaining: {len(dec['remaining'])}")
    if dec["remaining"]:
        for d in dec["remaining"]:
            print(f"[coverage] ERROR orphan dir not represented: {d}",
                  file=__import__("sys").stderr)
        return config.EXIT_GATE   # D1: 无法代表 ⇒ 非零退出

    registry = to_plan_modules(dec)
    if os.path.isfile(ctx.plan_path):
        plan = load_plan(ctx)
        if getattr(args, "reset", False):
            # --reset 的承诺(见 cli.py 帮助文本)只是"忽略 plan 现有 modules:,
            # 重新推导" —— 曾在此整建新 plan(pages/cards/aliases/limits 全
            # 清空), 手编计划一次 --reset 即蒸发, 且下次 finish 把旧发布页
            # 当 moved-out 孤儿剪除。其余人工编辑面一律原样保留。
            info("--reset: 仅重导 modules 注册表, 计划其余内容保持不变")
        else:
            old_by_id = {str(m.get("id")): m for m in plan.get("modules") or []}
            # 保留人工编辑过的标题/notes/依赖(与分解结果按 id 合并, 分解赢在结构)
            for m in registry:
                old = old_by_id.get(m["id"])
                if not old:
                    continue
                for field in ("title", "notes", "depends_on", "related_to",
                              "module_type", "skeleton"):
                    if old.get(field) not in (None, "", []):
                        m[field] = old[field]
        plan["modules"] = registry
        plan["schema_version"] = max(3, int(plan.get("schema_version") or 2))
    else:
        plan = {
            "schema_version": 3, "locale": ctx.locale, "style": ctx.style,
            "title": "Repo Wiki", "repo_summary": "", "export_style":
                ctx.config.get("export_style") or "legacy",
            "aliases": [], "modules": registry, "pages": [], "cards": [],
        }
    save_plan(ctx, plan)
    # 注册表快照进 DB(modules_hash 漂移检测的基准)
    pid = ctx.partition()["id"]
    ctx.db.publish_modules(pid, plan.get("modules") or [])

    print(f"modules: {len(registry)} (root + "
          f"{len(registry) - 1})")
    for m in registry[:40]:
        print(f"  {m['title']}  [{m['path']}]  type={m['module_type']}"
              f"  files={m['evidence'].get('files', 0)}")
    info(f"注册表已写入 {ctx.plan_path} 与 DB")
    return config.EXIT_OK
