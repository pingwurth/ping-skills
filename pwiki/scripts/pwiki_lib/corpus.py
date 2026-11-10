"""证据池层: 路径匹配原语、D4 生成物排除、D12 证据分层、D13 兄弟分歧、Corpus。

移植自 repowiki/scripts/plan_wiki.py:62-171(分层与裁剪)、320-495(原语与
Corpus)。主题包定义(P_* 探针)在 themes.py; 本模块只放与主题无关的证据机制。
"""

from __future__ import annotations

import fnmatch
import os

from . import config
from .util import posix

TEXT_LANGS = {
    "typescript", "javascript", "python", "go", "rust", "java", "kotlin", "csharp",
    "c", "cpp", "ruby", "php", "shell", "vue", "svelte", "sql", "protobuf",
    "yaml", "toml", "json", "markdown", "mdx", "rst", "text", "docker", "terraform",
}

DOC_LANGS = {"markdown", "mdx", "rst"}

DEFAULT_EXCLUDE = ["/node_modules/", "/vendor/", "/dist/", "/build/", "/target/",
                   "/coverage/", "/generated/", "/.git/", "/scratch/", "/tmp/",
                   ".pb.", "_pb."]

# D4: 生成物永远不当证据。单一事实源 — Corpus.collect(证据池)、Builder.add
# (source_files)、check gate 三处共用。模式是 fnmatch/子串, 与 path_match 同口径。
GENERATED_PATTERNS = [
    "/generated/", "/src/generated/", "/gen/go/", "/gen/java/", "/gen/python/",
    "/gen/ts/", "rest-contract/src/models", "openapi-generator",
    "/.openapi-generator/",
    "*.pb.go", "*.pb.cc", "*.pb.h", "*_pb2.py", "*_pb2.grpc.py", "*_grpc_pb.py",
    "connect-", ".g.dart", "*.generated.ts", "*.generated.js", "*.generated.go",
    "*.gen.ts",
]

# D12: 证据分层。文档(readme/ADR/markdown)与锁文件只证明主题被"写过文档",
# 不证明被"实现"。主题子项以强证据门控, 弱证据时 declined 而不是造出
# doc-only 页面再随机回填代码。
WEAK_DOC_STEMS = {
    "readme", "changelog", "changes", "history", "license", "licence", "notice",
    "claude", "agents", "contributing", "authors", "maintainers", "security",
    "code_of_conduct", "tutorial", "faq",
}
LOCKFILE_MARKS = (
    ".lock", "-lock.json", "-lock.yaml", "lockfile",
    "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "gemfile.lock",
    "composer.lock", "poetry.lock", "cargo.lock", "go.sum", "bun.lockb",
    "flake.lock",
)


def _strip_doc_ext(base: str) -> str:
    for ext in (".md", ".mdx", ".rst", ".markdown", ".txt"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def is_generated(path: str) -> bool:
    return match_any(path, GENERATED_PATTERNS)


def is_weak_evidence(path: str) -> bool:
    base = os.path.basename(path).lower()
    if _strip_doc_ext(base) in WEAK_DOC_STEMS:
        return True
    if path.lower().endswith((".md", ".mdx", ".rst", ".markdown")):
        return True
    return any(m in base for m in LOCKFILE_MARKS)


def is_never_cite(path: str) -> bool:
    """即使页面允许 markdown, 这些也永远不是好的回填候选。"""
    base = os.path.basename(path).lower()
    if _strip_doc_ext(base) in ("claude", "agents", "changelog", "changes",
                                "license", "notice", "lockfile"):
        return True
    return any(m in base for m in LOCKFILE_MARKS)


# --------------------------------------------------------------------------- #
# D13: 兄弟分歧
# --------------------------------------------------------------------------- #

def trim_sibling_overlap(pages: list, notes: list = None) -> None:
    """D13: 同父兄弟共享 >65% 源会各自复述整个家族。裁掉后到兄弟的共享尾
    (保留其 primary=首个源); 下限 2 源 —— 饥饿比轻度重叠更糟。"""
    for i, a in enumerate(pages):
        sa = set(a.get("source_files") or [])
        if not sa:
            continue
        for b in pages[i + 1:]:
            sb = set(b.get("source_files") or [])
            if not sb:
                continue
            inter = sa & sb
            if not inter:
                continue
            # 0.65: 几乎相同的集合才裁; 独立 primary + 少量共享次源
            # (shell/schema/session) 是正当的家族结构, 不是重复
            if len(inter) / len(sa | sb) <= 0.65:
                continue
            primary = (b.get("source_files") or [""])[0]
            keep = [s for s in b["source_files"] if s == primary or s not in sa]
            if len(keep) < 2:
                # 保 primary + 再一个: 诚实的单点重叠好过单源页
                keep = [primary] + [s for s in b["source_files"]
                                    if s != primary][:1]
            dropped = [s for s in b["source_files"] if s not in keep]
            if not dropped:
                continue
            b["source_files"] = keep
            if len(keep) < 4:
                b["evidence_thin"] = True
            if notes is not None:
                # 审计键用 id/title —— 三个调用点传的都是 assign_paths 之前
                # 的 Builder 页 dict(path 从未赋值), .get("path") 恒为 None,
                # D13 人工评审门只见 "page: null / shared with None"
                notes.append({
                    "page": b.get("id") or b.get("title"),
                    "title": b.get("title"),
                    "code": "sibling/overlap",
                    "detail": "dropped " + ", ".join(dropped[:4]) +
                              " shared with " + str(a.get("id")
                                                     or a.get("title")),
                })


def primary_first(page: dict) -> None:
    """D13: 页面"自己的"文件(stem 出现在其 id 里)排在源列表首位。"""
    src = page.get("source_files") or []
    if len(src) < 2:
        return
    pid = str(page.get("id", "")).lower()
    for idx, s in enumerate(src[1:], start=1):
        stem = stem_of(s).lower()
        if stem and len(stem) > 2 and stem in pid:
            src.insert(0, src.pop(idx))
            page["source_files"] = src
            return


# --------------------------------------------------------------------------- #
# 匹配原语
# --------------------------------------------------------------------------- #

# 路径规范化缓存: path_match 每次调用都 lower/加前导斜杠, 一次 plan --themes
# 要做 ~10M 次 —— 规范化一次复用(文件数 << 次数)
_PATH_NORM_CACHE: dict = {}


def _norm_path(path: str) -> str:
    p = _PATH_NORM_CACHE.get(path)
    if p is None:
        p = posix(os.path.normpath(path)).lower()
        if not p.startswith("/"):
            p = "/" + p
        _PATH_NORM_CACHE[path] = p
    return p


def path_match(path: str, pattern: str) -> bool:
    p = _norm_path(path)
    pat = posix(pattern).lower()
    if "*" in pat or "?" in pat:
        return fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(os.path.basename(p), pat)
    return pat in p


def match_any(path: str, patterns) -> bool:
    for pat in patterns:
        if path_match(path, pat):
            return True
    return False


def read_text(path: str, limit: int = 300_000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def dedupe_files(files: list) -> list:
    seen, out = set(), []
    for f in files:
        if f["path"] in seen:
            continue
        seen.add(f["path"])
        out.append(f)
    return out


def pick_files(files: list, keywords: list, limit: int,
               exclude_tests: bool = True) -> list:
    scored = []
    for f in files:
        p = f["path"]
        low = p.lower()
        score = 1
        for i, kw in enumerate(keywords):
            if kw.lower() in low:
                score += (len(keywords) - i) * 3
        if p.count("/") <= 4:
            score += 2
        lines = f.get("lines") or 0
        if 20 <= lines <= 1200:
            score += 2
        elif lines > 1200:
            score += 1
        if exclude_tests and match_any(low, TEST_PATTERNS):
            score -= 20
        if f.get("lang") in ("markdown", "json", "yaml", "text") \
                and "readme" not in low:
            score -= 3
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for _, p in scored:
        if p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out


TEST_PATTERNS = ["/tests/", "/test/", "/__tests__/", "/spec/", "/specs/",
                 ".test.", ".spec.", "_test.go", "_test.py", "_test.rs",
                 "Test.java", "Test.kt"]


def dirs_of(files: list, limit: int = 8) -> list:
    out = []
    for f in files:
        parts = f["path"].split("/")
        d = "/".join(parts[:-1])
        if d and d not in out:
            out.append(d)
    return out[:limit]


def stem_of(path: str) -> str:
    # 扩展名剥除走 config.STRIP_EXTS 单一事实源(长度倒序): 手抄元组曾与
    # themes.humanize 各自漂移, .mjs/.cpp 等剥不掉
    base = os.path.basename(path)
    for ext in config.STRIP_EXTS:
        if base.endswith(ext):
            return base[:-len(ext)]
    return base


class Corpus:
    """证据池: 模式匹配 + grep 回填 + 文档命中, 全部过 D4/默认排除。"""

    def __init__(self, repo: str, files: list, modules: dict, manifest_paths: set):
        self.repo = repo
        self.files = files
        self.by_path = {f["path"]: f for f in files}
        self.modules = modules          # {module_path: entry} (modules.decompose)
        self.manifest_paths = manifest_paths
        self._text: dict = {}
        # r7 #51: 原文+小写两份缓存的无界增长帽(合计)—— 10k 文件仓库的
        # plan --themes 曾 ~400MB 常驻; 超帽后新文件不缓存(单文件本就有
        # 300KB 读限, 已缓存的保留)
        self._text_budget = 64 * 1024 * 1024
        self._text_bytes = 0
        # P72: grep 的小写文本缓存 —— themes 每个 needle 曾对全部记忆文本
        # 重 .lower() 一遍(数十 needle = 数 GB 瞬时字符串扰动)
        self._text_lower: dict = {}
        self._docs = [f for f in files
                      if f.get("lang") in DOC_LANGS
                      or f["path"].lower().endswith((".md", ".mdx", ".rst"))]
        # 路径集合: doc_hits 对 pool 的成员测试曾是 list 的 O(pool×docs)
        # 全表扫(10k 文件 × 1.7k 文档 ≈ 227ms/次, themes 一次规划调 23 次)
        self._doc_paths = {f["path"] for f in self._docs}
        # collect 记忆化: instantiate_pack 对同一 spec 反复收集(P_FRONTEND
        # 一轮扫 3 遍), (patterns, exclude) 相同直接复用
        self._collect_cache: dict = {}

    def _maybe_cache(self, cache: dict, path: str, value: str) -> str:
        """r8 #49: 超预算守卫 + 存储 + 记账三元组的单一属主 —— read 与
        read_lower 各持一份曾文本分叉(守卫条件 _text vs _text_lower),
        未来策略(逐出/联合记账)须改两遍否则两缓存分叉。"""
        if self._text_bytes + len(value) > self._text_budget and cache:
            return value   # 超帽: 供本次使用, 不常驻
        cache[path] = value
        self._text_bytes += len(value)
        return value

    def read(self, path: str) -> str:
        if path not in self._text:
            self._maybe_cache(self._text, path,
                              read_text(os.path.join(self.repo, path)))
        return self._text[path]

    def match(self, patterns: list, exclude: list = None, pool: list = None) -> list:
        base = self.files if pool is None else pool
        if not patterns:
            return dedupe_files([f for f in base
                                 if not (exclude and match_any(f["path"], exclude))])
        return dedupe_files([
            f for f in base
            if match_any(f["path"], patterns)
            and not (exclude and match_any(f["path"], exclude))
        ])

    def read_lower(self, path: str) -> str:
        """read() 的小写视图(P72 记忆化; grep 专用; 受同一预算帽)。"""
        if path not in self._text_lower:
            self._maybe_cache(self._text_lower, path,
                              self.read(path).lower())
        return self._text_lower[path]

    def grep(self, pool: list, needles: list, limit: int = 300,
             max_bytes: int = 300_000) -> list:
        hits = []
        lowered = [n.lower() for n in needles]
        for f in pool:
            if len(hits) >= limit:
                break
            if f.get("bytes", 0) > max_bytes or f.get("lang") not in TEXT_LANGS:
                continue
            text = self.read_lower(f["path"])
            if text and any(n in text for n in lowered):
                hits.append(f)
        return hits

    def doc_hits(self, needles: list, pool: list = None, limit: int = 60) -> list:
        if pool is None:
            base = self._docs
        else:
            base = [f for f in pool
                    if f["path"] in self._doc_paths
                    or f.get("lang") in DOC_LANGS]
        return self.grep(base, needles, limit=limit)

    def collect(self, spec: dict, pool: list = None) -> list:
        """按 spec 收集证据文件; 对全池(无 pool)调用按 spec 键记忆化。

        记忆化键覆盖**全部**能改变结果的 spec 键 —— r4 #55: min/commands/
        readme 曾不在键里, 两个同 patterns/exclude/grep/docs 却异 min 的
        包, 第二个会拿到第一个的缓存(grep 回补门槛被静默跳过)。"""
        key = None
        if pool is None:
            key = (tuple(spec.get("patterns") or ()),
                   tuple(spec.get("exclude") or ()),
                   tuple(spec.get("grep") or ()),
                   tuple(spec.get("docs") or ()),
                   spec.get("min"),
                   str(spec.get("commands")),
                   bool(spec.get("readme")))
            hit = self._collect_cache.get(key)
            if hit is not None:
                return list(hit)
        out = self._collect(spec, pool)
        if key is not None:
            self._collect_cache[key] = out
        return list(out)

    def _collect(self, spec: dict, pool: list = None) -> list:
        patterns = spec.get("patterns") or []
        exclude = list(DEFAULT_EXCLUDE) + GENERATED_PATTERNS + \
            list(spec.get("exclude") or [])
        # 排除只在 match() 与追加集上各算一遍(结尾的整池复滤是第二次,
        # ~30× 常数因子的主要来源之一)
        files = self.match(patterns, exclude, pool)
        base = self.files if pool is None else pool
        extras: list = []
        if spec.get("grep") and len(files) < max(4, spec.get("min", 2) * 4):
            extras += self.grep(base, spec["grep"], limit=400)
        if spec.get("docs"):
            extras += self.doc_hits(spec["docs"], base, limit=80)
        if spec.get("commands"):
            extras += [f for f in base if f["path"] in self.manifest_paths]
        if spec.get("readme"):
            extras += [f for f in self._docs
                       if "readme" in f["path"].lower()]
        if extras:
            files = dedupe_files(
                files + [f for f in extras if not match_any(f["path"], exclude)])
        return files

    def module_for(self, path: str):
        best = None
        for mp, m in self.modules.items():
            if mp == "" or m.get("synthetic"):
                continue
            if path.startswith(mp + "/"):
                if best is None or len(mp) > len(best):
                    best = mp
        return best
