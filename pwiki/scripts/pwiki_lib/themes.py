"""主题包规划器(repowiki plan_wiki 的移植 + 去试点化)。

18 个主题包从证据实例化页面; 分层证据门(D12)拒绝弱证据子项并记录
theme_declined; 兄弟分歧四件套(D13); pack 锚定(D21, 框架例外走 plan
aliases 的目标模块 —— yudao 硬编码规则已数据化); check gate(D3/D4/D6)。

去试点化: repowiki 混入 ping-english-assistant 试点的标题映射
(FEATURE_TITLES/STORE_TITLES/WINDOW_TITLES/UI_PAGE_TITLES/
TITLE_ALIASES/FEATURE_FAMILIES) —— 这里全部抽到仓库级 pwiki.yaml 的
overrides(通用回退 humanize); 生态级表格(DEP_SCORES/
ARCH_SHAPING_DEPS/DEP_DISPLAY/PROVIDER_TITLES)是通用知识, 保留在代码。

pwiki.yaml 覆盖格式(被分析仓库根):
  title_maps:
    feature:  {library: 材料库页面, ...}     # FEATURE_TITLES
    store:    {...}                          # STORE_TITLES
    window:   {...}                          # WINDOW_TITLES
    ui_page:  {...}                          # UI_PAGE_TITLES
    families: {...}                          # FEATURE_FAMILIES
  title_aliases: {"功能指南|设置页面": 设置功能, ...}
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict

from . import config, corpus as cz
from .config import Ctx, now_iso
from .styles import preset
from .corpus import (Corpus, dedupe_files, dirs_of, is_generated,
                     is_never_cite, is_weak_evidence, match_any, path_match,
                     pick_files, primary_first, stem_of, trim_sibling_overlap)
from .util import dirname, slugify as pslugify

# --------------------------------------------------------------------------- #
# profile 参数(repowiki PROFILES; max_pages/target_min 已在 config)
# --------------------------------------------------------------------------- #

PROFILES = {
    "lite": {"module_sheet_min": 10, "pack_scale": 2.0, "source_limit": 5,
             "leaves_cap": 3, "module_page_min": 40},
    "standard": {"module_sheet_min": 5, "pack_scale": 1.0, "source_limit": 8,
                 "leaves_cap": 6, "module_page_min": 25},
    "full": {"module_sheet_min": 3, "pack_scale": 0.5, "source_limit": 10,
             "leaves_cap": 10, "module_page_min": 12},
}

# 探针模式(repowiki P_*)
P_FRONTEND = ["*.tsx", "*.jsx", "*.vue", "*.svelte", "/client/", "/frontend/", "/web/", "/app/", "/components/"]
P_BACKEND = ["/server/", "/backend/", "/services/", "/service/", "/api/", "/gateway/", "/worker/", "*.java", "*.kt", "*.go", "*.py"]
P_DESKTOP = ["tauri", "electron", "/desktop/", "src-tauri/"]
P_AI = ["/ai/", "/llm/", "/ml/", "/inference/", "/tts/", "/asr/", "/soe/", "/embedding/", "/prompt/", "/agent/", "/orchestrator/", "provider", "adapter", "/contracts/", "/contract/", "/mock/", "/stream/", "/streaming/", "/chat/", "/generate/", "/enhance/"]
P_DATA = ["/models/", "/model/", "/schema/", "/schemas/", "/migrations/", "/entities/", "/db/", "/database/", "/storage/", "/repo/", "/persistence/", "/types/", "/stores/", "/store/", "indexeddb", "/idb/", "/settings/", "/backup/", "*.sql", ".prisma"]
P_API = ["/routes/", "/controllers/", "/handlers/", "/api/", "/endpoints/", "*.proto", "openapi", "swagger", "/ws/", "/websocket/", "/socket/", "/ipc/", "contracts.", "/contract/", "/stream/", "sse"]
P_SEC = ["/auth/", "/security/", "/crypto/", "/permissions/", "/permission/", "/rbac/", "/oauth/", "/jwt/", "/audit/", "/validation/", "auth", "security", "crypto", "permission", "rbac", "jwt", "oauth"]
P_PERF = ["/perf/", "/performance/", "/cache/", "/caching/", "/benchmark/", "/benchmarks/", "/optimiz/", "/metrics/"]
P_TEST = cz.TEST_PATTERNS
P_INFRA = ["Dockerfile", "docker-compose", "compose.", "/k8s/", "/kubernetes/", "/helm/", "/charts/", "/deploy/", "/deployment/", "/terraform/", ".github/workflows/", ".gitlab-ci", "Jenkinsfile", "Procfile"]
P_TOOLS = ["/tools/", "/scripts/", "/scaffolds/", "/scaffold/", "/codegen/", "/contract-check/", "/prototype/"]

# --------------------------------------------------------------------------- #
# 通用生态表(保留在代码; 非试点专属)
# --------------------------------------------------------------------------- #

PROVIDER_TITLES = {
    "azure": "Azure Speech Services 适配", "tencent": "腾讯语音评测适配",
    "iflytek": "讯飞语音评测适配", "mock": "Mock 测试提供商",
    "openai": "OpenAI 适配", "anthropic": "Anthropic 适配",
    "huggingface": "Hugging Face 推理代理", "hf": "Hugging Face 推理代理",
    "kokoro": "Kokoro TTS 集成", "ollama": "Ollama 本地推理",
    "whisper": "Whisper 转写",
}

# provider 证据的排除路径: 测试/示例/仿造物不是生产集成
_PROVIDER_NOISE = ("/__tests__/", "/__mocks__/", "/mocks/", "/testdata/",
                   "/fixtures/", "/e2e/", ".test.", ".spec.", ".mock.")


def provider_hits(files: list, name: str) -> list:
    """provider 键的路径证据: **词边界**匹配 + 排除测试/mock 路径。

    裸子串曾把 __mocks__/*.mock.ts 或 src/pathfinder.ts(含 'hf')当证据,
    造出不存在集成的知识卡(后续门禁只查路径存在性, 无人拦)。"""
    bound = re.compile(rf"(^|[/_.\-]){re.escape(str(name))}([/_.\-]|$)")
    out = []
    for f in files:
        low = str(f.get("path") or "").lower()
        if not low or any(seg in low for seg in _PROVIDER_NOISE):
            continue
        if bound.search(low):
            out.append(f)
    return out

# 塑造架构、恒得知识卡的依赖
ARCH_SHAPING_DEPS = {
    "fastify", "tauri", "electron", "zustand", "rusqlite", "sqlx", "axum", "tokio",
    "prisma", "drizzle-orm", "sqlalchemy", "typeorm", "onnxruntime", "transformers",
}

DEP_SCORES = {
    "react": 100, "react-dom": 95, "next": 98, "vue": 95, "nuxt": 95,
    "svelte": 90, "sveltekit": 90, "tauri": 100, "electron": 95, "vite": 60,
    "webpack": 50, "tailwindcss": 70, "shadcn": 65,
    "fastify": 85, "express": 80, "koa": 70, "nestjs": 80, "@nestjs/core": 80,
    "spring-boot": 85, "spring": 80, "django": 80, "flask": 75, "fastapi": 85,
    "tokio": 75, "axum": 70, "actix-web": 65, "sqlx": 60, "rusqlite": 60,
    "prisma": 65, "drizzle-orm": 60, "sqlalchemy": 65, "typeorm": 60,
    "zustand": 65, "redux": 55, "pinia": 55, "@tanstack/react-query": 60,
    "vitest": 45, "jest": 45, "pytest": 45, "junit": 40,
    "onnxruntime": 55, "transformers": 55, "torch": 55, "numpy": 40,
    "redis": 60, "kafka": 60, "rabbitmq": 55, "postgres": 60, "sqlite": 55,
}

DEP_DISPLAY = {
    "react": "React", "react-dom": "React DOM", "next": "Next.js", "vue": "Vue",
    "nuxt": "Nuxt", "svelte": "Svelte", "sveltekit": "SvelteKit", "tauri": "Tauri",
    "@tauri-apps/api": "Tauri", "electron": "Electron", "vite": "Vite",
    "tailwindcss": "Tailwind CSS", "shadcn": "shadcn/ui", "fastify": "Fastify",
    "express": "Express", "koa": "Koa", "nestjs": "NestJS", "@nestjs/core": "NestJS",
    "spring-boot": "Spring Boot", "spring": "Spring", "django": "Django",
    "flask": "Flask", "fastapi": "FastAPI", "tokio": "Tokio", "axum": "Axum",
    "actix-web": "Actix Web", "sqlx": "SQLx", "rusqlite": "rusqlite",
    "prisma": "Prisma", "drizzle-orm": "Drizzle ORM", "sqlalchemy": "SQLAlchemy",
    "typeorm": "TypeORM", "zustand": "Zustand", "redux": "Redux", "pinia": "Pinia",
    "@tanstack/react-query": "TanStack Query", "vitest": "Vitest", "jest": "Jest",
    "pytest": "pytest", "onnxruntime": "ONNX Runtime",
    "transformers": "Transformers", "torch": "PyTorch", "redis": "Redis",
    "kafka": "Kafka", "rabbitmq": "RabbitMQ", "postgres": "PostgreSQL",
    "sqlite": "SQLite",
}

FEATURE_EXCLUDE = {
    "index", "layout", "page", "app", "main", "_app", "_document", "template",
    "root", "slug", "...slug", "[...slug]",
    "error", "not-found", "loading", "empty", "skeleton", "fallback",
    "placeholder", "provider", "context", "wrapper", "styles", "types",
}

# repowiki 深文九节模板。五类 kind 曾各抄一份逐字相同的清单 —— 单一常量;
# gates 侧另有 config.ARTICLE_TYPE_SECTIONS(类型→最少节数)兜底, 两套以
# "required_sections 存在则优先"衔接。concise 不盖章(短页与九节硬模板互斥)。
REQUIRED_SECTIONS = ["引言", "项目结构", "核心组件", "架构总览", "详细组件分析",
                     "依赖关系分析", "性能考量", "故障排查指南", "结论"]

# repowiki kind → plan v3 article type
KIND_TO_TYPE = {"overview": "overview", "architecture": "domain",
                "module": "deep_dive", "cross": "domain", "feature": "deep_dive"}

# 13 种手写语言的证据白名单(make_leaves 种子过滤与 run_themes 回填共用;
# r4 #62: 两处内联各写一份 —— 加一种语言漏改另一处, 种子/回填口径分裂)
HAND_LANGS = ("typescript", "tsx", "javascript", "python", "go", "rust",
              "java", "kotlin", "proto", "protobuf", "yaml", "sql", "markdown")


# --------------------------------------------------------------------------- #
# overrides(试点标题映射的落点)
# --------------------------------------------------------------------------- #

def load_overrides(ctx: Ctx) -> dict:
    """仓库级 pwiki.yaml → {title_maps, title_aliases}; 缺失为空。

    解析失败/Tab 缩进必须大声警告 —— 曾静默返回空表: 试点映射整批失效,
    受影响页面全部回退碰撞后缀名、路径齐变, 下一次 update 全量重写,
    全程无一行提示。"""
    from .util import info
    from .yamlite import yaml_load
    empty = {"title_maps": {}, "title_aliases": {}}
    if not os.path.isfile(ctx.overrides_path):
        return empty
    try:
        with open(ctx.overrides_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        info(f"warn: 读取 {ctx.overrides_path} 失败({e}) — overrides 忽略")
        return empty
    if re.search(r"^[ \t]*\t", text, re.M):
        info(f"warn: {ctx.overrides_path} 缩进用了 Tab(YAML 不允许) — "
             "overrides 全部忽略; 换成空格缩进后重跑")
        return empty
    try:
        data = yaml_load(text) or {}
    except ValueError as e:
        info(f"warn: {ctx.overrides_path} 解析失败({e}) — overrides 全部忽略")
        return empty
    maps = data.get("title_maps") or {}
    if not isinstance(maps, dict):
        info(f"warn: {ctx.overrides_path} title_maps 不是映射"
             f"(得到 {type(maps).__name__}) — 该键全部忽略")
        maps = {}
    aliases_raw = data.get("title_aliases") or {}
    if not isinstance(aliases_raw, dict):
        # 列表本是该键的自然 YAML 写法; 曾裸 .items() AttributeError 把
        # 整条 themes 路线打死, 违背本函数"大声警告 + 忽略"的自述契约
        info(f"warn: {ctx.overrides_path} title_aliases 不是映射"
             f"(得到 {type(aliases_raw).__name__}) — 该键全部忽略")
        aliases_raw = {}
    aliases = {}
    for key, val in aliases_raw.items():
        scope, _, title = str(key).partition("|")
        if title:
            aliases[(scope, title)] = str(val)
    # P29: platform_rules 是 (子串, 标题) 对的序列(make_leaves 按对解包),
    # 其余键是映射 —— 曾只验"值是映射": dict 形态通过过滤后在解包处裸
    # ValueError 打死 plan --themes, 列表形态反被静默丢成死配置。两种
    # 自然写法都收, 坏形状大声警告 + 忽略(docstring 自述契约)
    pr = (maps.get("platform_rules") if isinstance(maps, dict) else None)
    norm_maps = {}
    for k, v in maps.items():
        if k == "platform_rules":
            continue
        if isinstance(v, dict):
            norm_maps[k] = dict(v)
    if pr is not None:
        pairs = []
        if isinstance(pr, dict):
            pairs = [[str(a), str(b)] for a, b in pr.items()]
        elif isinstance(pr, list) and all(
                isinstance(x, (list, tuple)) and len(x) == 2 for x in pr):
            pairs = [[str(a), str(b)] for a, b in pr]
        else:
            info(f"warn: {ctx.overrides_path} title_maps.platform_rules 需要"
                 " {子串: 标题} 映射或 [[子串, 标题], …] 列表"
                 f"(得到 {type(pr).__name__}) — 该键忽略")
        if pairs:
            norm_maps["platform_rules"] = pairs
    return {"title_maps": norm_maps, "title_aliases": aliases}


def merge_manual(new_items: list, old_items: list, manual_fields,
                 keep=None) -> list:
    """新规划结果合并人工编辑(c027 契约的单一落点, pages/cards 两份结构
    相同的手抄曾各自漂移): 同 id 保留手工调过的 manual_fields; 引擎不再
    输出且 keep() 为真的旧条目保留(keep=None 全保)。"""
    old_by_id = {str(p.get("id")): p for p in old_items}
    new_ids = {str(p.get("id")) for p in new_items}
    out = []
    for np in new_items:
        old = old_by_id.get(str(np.get("id")))
        if old is not None:
            for f in manual_fields:
                if str(old.get(f) or "").strip():
                    np[f] = old[f]
        out.append(np)
    keep = keep or (lambda _old: True)
    out += [old for pid, old in old_by_id.items()
            if pid and pid not in new_ids and keep(old)]
    return out


def humanize(name: str) -> str:
    """薄封装 modules.humanize(单一实现 —— 两份同名手抄曾各自漂移:
    modules 认 TITLE_MAP 不剥扩展名, themes 剥扩展名不认表, 同一目录在
    两条规划路由上得到不同标题)。"""
    from .modules import humanize as _mh
    return _mh(name)


def slugify(s: str) -> str:
    """薄封装 util.slugify(全局唯一 slug 规则 —— 第二套手写规则曾让同一
    模块路径在两条规划路由上产出不同 id); 空串回退 md5 短哈希防撞。"""
    slug = pslugify(str(s), "")
    return slug or "p-" + hashlib.md5(str(s).encode("utf-8")).hexdigest()[:8]




# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #

class Builder:
    def __init__(self, corpus: Corpus, profile: dict, locale: str, overrides: dict,
                 style: str = "deep"):
        self.corpus = corpus
        self.profile = profile
        self.locale = locale
        self.overrides = overrides
        self.style = style
        self.pages: list = []
        self.by_id: dict = {}
        self.used_ids: set = set()
        self.used_slugs: set = set()
        self.used_titles: set = set()
        self.theme_gaps: list = []
        self.title_collisions: list = []
        # D12: 证据门拒绝的主题子项(带原因), 进 plan.md 评审
        self.theme_declined: list = []
        # D13: 兄弟源重叠裁剪记录
        self.sibling_trims: list = []

    def _alias(self, scope_key, title):
        return self.overrides["title_aliases"].get((scope_key, title))

    def add(self, page_id, title, kind, parent, scope, source_files, priority,
            prompt, related=None, module_path=None, title_hint=None,
            required_sections=None, pack_key=None) -> dict:
        # P67: 唯一性循环用 planner._unique_id 单一实现(额外守卫空候选,
        # 两份手抄丢掉了该守卫) —— id 方案要变只改一处
        from .planner import _unique_id
        page_id = _unique_id(page_id, self.used_ids)
        slug = _unique_id(slugify(page_id), self.used_slugs)

        final_title = title
        if final_title in self.used_titles:
            scope_key = pack_key or title_hint or kind
            # 1) 显式别名(优先, 保全局标题唯一)
            alias = self._alias(scope_key, title)
            if alias and alias not in self.used_titles:
                final_title = alias
            # 2) 通用: 功能类页面去掉「页面」后缀
            elif title.endswith("页面") and title[:-2] not in self.used_titles \
                    and title[:-2]:
                final_title = title[:-2]
            # 3) 兜底: 冒号前缀(浮出为 plan 警告, 不应触发)
            else:
                hint = title_hint or kind
                final_title = hint + "：" + title
                n = 2
                while final_title in self.used_titles:
                    final_title = hint + "-" + str(n) + "：" + title
                    n += 1
                self.title_collisions.append(
                    {"scope": scope_key, "title": title, "resolved": final_title})
        self.used_titles.add(final_title)

        src = []
        for p in source_files:
            if p in self.corpus.by_path and p not in src and not is_generated(p):
                src.append(p)
        src = src[: self.profile["source_limit"] + 4]

        page = {
            "id": page_id, "title": final_title, "slug": slug, "kind": kind,
            "parent": parent, "scope": scope[:20] or ["."],
            "source_files": src, "prompt": prompt,
            "required_sections": (required_sections or REQUIRED_SECTIONS)
            if preset(self.style).get("required_sections_fallback")
            else (required_sections or []),
            "related_pages": related or [], "module_path": module_path,
            "priority": priority, "pack_key": pack_key,
        }
        self.pages.append(page)
        self.by_id[page["id"]] = page
        return page


def prompt_for(title: str, kind: str, style: str = "deep",
               locale: str = "zh") -> str:
    """writer 提示词 —— 单一机制 i18n.page_prompt(style × locale × kind)。

    r4 #59: 曾是本文件私有的 zh-only 分支表, 与 i18n.SHEET_PROMPTS 平行
    两套提示词系统; 深文模板的"至少两张图"对 concise 是 0-8 图带, 曾让
    按提示词写的页面被自家门禁拒绝(该修复曾只落在 zh 分支)。"""
    from . import i18n
    return i18n.page_prompt(kind, title, style, locale)


def scaled_min(spec: dict, default: int, profile: dict) -> int:
    base = spec.get("min", default)
    return max(1, int(round(base * profile["pack_scale"])))


# --------------------------------------------------------------------------- #
# make_leaves
# --------------------------------------------------------------------------- #

def make_leaves(builder: Builder, corpus: Corpus, parent: dict, spec: dict,
                evidence: list, kind: str) -> list:
    leaves_spec = spec.get("leaves")
    if not leaves_spec:
        return []
    specs = leaves_spec if isinstance(leaves_spec, list) else [leaves_spec]
    made = []
    for ls in specs:
        cap = ls.get("cap", builder.profile["leaves_cap"])
        total_cap = ls.get("total_cap", 999)
        mode = ls.get("mode", "dirs")
        groups: list = []
        if mode == "files":
            by_key: dict = defaultdict(list)
            key_title: dict = {}
            rules = ls.get("title_rules") or []
            file_titles = (builder.overrides["title_maps"].get("feature") or {})
            for f in evidence:
                if ls.get("pattern") and not path_match(f["path"], ls["pattern"]):
                    continue
                stem = stem_of(f["path"])
                low = stem.lower()
                if low in FEATURE_EXCLUDE or low in (ls.get("exclude") or []):
                    continue
                # P41: 测试文件谓词与全库同源(P_TEST) —— 手搓词干检查与
                # pick_files/gates 的谓词已分歧(foo_test.go、__tests__/
                # 等漏拦, 关键词丰富的测试助手进 feature 页 source_files)
                if match_any(f["path"].lower(), P_TEST):
                    continue
                matched = None
                low_path = f["path"].lower()
                for sub, rule_title in rules:
                    if sub.lower() in low_path:
                        matched = rule_title
                        break
                if matched:
                    key = matched
                    key_title[key] = matched
                else:
                    key = low
                    mapped = (ls.get("title_map") or file_titles).get(low)
                    title = mapped or humanize(stem)
                    suffix = ls.get("suffix") or ""
                    if suffix and not mapped and not title.endswith(suffix):
                        title += suffix
                    key_title[key] = title
                by_key[key].append(f)
            ordered = list(by_key.items())
            prefer = ls.get("prefer") or []
            if prefer:
                def pref(item):
                    key, group = item
                    text = (key + " " + group[0]["path"]).lower()
                    for i, p in enumerate(prefer):
                        if p in text:
                            return i
                    return len(prefer) + 1
                ordered.sort(key=pref)
            for key, group in ordered:
                groups.append((key_title.get(key) or humanize(key), group))
        elif mode == "group":
            group = [f for f in evidence
                     if not ls.get("pattern") or path_match(f["path"], ls["pattern"])]
            if group:
                groups.append((ls.get("title") or
                               humanize(ls.get("pattern") or "group"), group))
        elif mode == "names":
            by_name: dict = defaultdict(list)
            title_map = ls.get("title_map") or {}
            names = ls.get("names") or []
            for f in evidence:
                low = f["path"].lower()
                for name in names:
                    if name in low:
                        by_name[name].append(f)
                        break
            for name, group in by_name.items():
                groups.append((title_map.get(name, humanize(name)), group))
        elif mode == "modules":
            by_mod: dict = defaultdict(list)
            for f in evidence:
                mp = corpus.module_for(f["path"])
                if mp:
                    by_mod[mp].append(f)
            allowed = {x.lower() for x in (ls.get("module_names") or [])}
            for mp, group in by_mod.items():
                m = corpus.modules.get(mp, {})
                if m.get("files", 0) < ls.get("min_module_files", 5):
                    continue
                if allowed and mp.split("/")[-1].lower() not in allowed:
                    continue
                groups.append((m.get("title") or mp, group))
        elif mode == "proto_services":
            by_service: dict = defaultdict(list)
            for f in evidence:
                if not f["path"].endswith(".proto"):
                    continue
                text = corpus.read(f["path"])
                for line in text.splitlines():
                    s = line.strip()
                    if s.startswith("service "):
                        name = s.split()[1].strip("{ ")
                        if name:
                            by_service[name].append(f)
            for name, group in by_service.items():
                groups.append((humanize(name), group))
        else:
            by_dir: dict = defaultdict(list)
            for f in evidence:
                by_dir[dirname(f["path"])].append(f)
            for d, group in by_dir.items():
                seg = d.split("/")[-1] if d else "根目录"
                title = humanize(seg)
                suffix = ls.get("suffix") or ""
                if suffix and title and not title.endswith(suffix):
                    title += suffix
                groups.append((title, group))

        seen = set()
        per_spec = 0
        for title, group in groups:
            if per_spec >= cap or len(made) >= total_cap:
                break
            if not title or title in seen:
                continue
            seen.add(title)
            src = pick_files(group, ls.get("source_keywords") or
                             ["index", "main", "config", "model", "schema", "service"],
                             builder.profile["source_limit"])
            if not src:
                continue
            # D9: 瘦种子(单 proto/单文件组)从父页 scope 目录补种, 让 spec 自带
            # ≥4 源, 而不是指望 writer 的 3-extra-file 预算
            seeded: list = []
            if len(src) < 4:
                have = set(src)
                for d in [x for x in parent.get("scope", []) if x and x != "."]:
                    d = d.rstrip("/")
                    cand = [
                        f for f in corpus.files
                        if (f["path"] == d or f["path"].startswith(d + "/"))
                        and f["path"] not in have
                        and not is_generated(f["path"])
                        and not match_any(f["path"], P_TEST)
                        and f.get("lang") in HAND_LANGS
                    ]
                    for f in sorted(cand, key=lambda x: (-(x.get("lines") or 0),
                                                         x["path"])):
                        src.append(f["path"])
                        have.add(f["path"])
                        seeded.append(f["path"])
                        if len(src) >= 4:
                            break
                    if len(src) >= 4:
                        break
            group_dirs = dirs_of(group, limit=1000)
            # 补种证据合法地加宽叶的 scope(如 proto-only 页引用其 Java 实现)
            # —— 记录这些目录, 别让 scope/hit 门禁败在我们自己播的种子上
            for s in seeded:
                d = dirname(s)
                if d and d not in group_dirs:
                    group_dirs.append(d)
            page = builder.add(
                page_id=parent["id"] + "-" + slugify(title),
                title=title, kind=kind, parent=parent["id"],
                scope=group_dirs or parent["scope"], source_files=src,
                priority=parent["priority"] - 30,
                prompt=prompt_for(title, kind, builder.style, builder.locale),
                title_hint=parent["title"],
                module_path=None, pack_key=parent.get("pack_key"))
            # 与 pack 子项同规: 跨 >20 目录的叶继承父 scope
            if len(group_dirs) > 20:
                page["scope"] = parent["scope"][:8]
                page["cross_cutting"] = True
            if len(src) < 4:
                page["evidence_thin"] = True
            made.append(page)
            per_spec += 1
    # D13: 同一 spec 的叶(如每个训练模式页都喂全家族文件)必须分歧
    for p in made:
        primary_first(p)
    trim_sibling_overlap(made, builder.sibling_trims)
    return made


# --------------------------------------------------------------------------- #
# instantiate_pack
# --------------------------------------------------------------------------- #

def instantiate_pack(builder: Builder, corpus: Corpus, pack: dict,
                     profile: dict, framework_mods: set) -> dict:
    evidence = corpus.collect(pack)
    if not pack.get("always") and len(evidence) < scaled_min(pack, 4, profile):
        if pack.get("key"):
            builder.theme_gaps.append({"key": pack["key"], "title": pack["title"],
                                       "evidence": len(evidence),
                                       "reason": "insufficient evidence"})
        return None
    # (P68: 此处曾有一段不可达的空证据守卫 —— scaled_min 恒 ≥1, 上面已
    # 把空证据的非 always 包全部返回, 残留分支只会误导调试者)
    src = pick_files(evidence, pack.get("source_keywords") or
                     ["readme", "index", "main", "config", "app", "server"],
                     profile["source_limit"] + 2)
    if not src:
        src = [f["path"] for f in evidence[: profile["source_limit"] + 2]]
    # D12: pack 父页强证据领先; 文档最多占 3 槽
    src = ([s for s in src if not is_weak_evidence(s)]
           + [s for s in src if is_weak_evidence(s)][:3])
    # D21: pack-key 模块锚定。子串模式("contract" 命中 CrmContract*)会把外模块
    # 文件拉进父页源列表, 而 pack-root 的 scope_hit 豁免放行(yudao 验收: AI 包
    # 列了 10 个 CRM 契约文件)。当 pack key 命名了一个模块, 父页源只能来自
    # 该模块族、框架模块(plan aliases 的目标)或仓库根。
    pk = pack.get("key")
    if pk:
        def _mod_named(mp):
            return bool(mp) and any(pk in seg.split("-") for seg in mp.split("/"))

        def _allowed(mp):
            return (mp in (None, "")) or _mod_named(mp) or (mp in framework_mods)

        anchor_mods = {_m for _m in (corpus.module_for(f["path"]) for f in evidence)
                       if _mod_named(_m)}
        if anchor_mods:
            anchored_src = [s for s in src if _allowed(corpus.module_for(s))]
            if anchored_src:
                src = anchored_src
            else:
                # 关键词打分可能把所有槽位填满外模块文件(yudao: "config" 把
                # CrmContractConfig* 排在所有 AI 文件前) —— 从锚定模块族重建
                fam = [f for f in evidence if _allowed(corpus.module_for(f["path"]))]
                fam_sorted = sorted(fam, key=lambda x: (-(x.get("lines") or 0),
                                                        x["path"]))
                src = [f["path"] for f in fam_sorted[: profile["source_limit"] + 2]]
    parent = builder.add(
        page_id="pack-" + pack["key"],
        title=pack["title"], kind=pack.get("kind", "cross"), parent=None,
        scope=dirs_of(evidence) or ["."], source_files=src,
        priority=pack.get("priority", 650),
        prompt=pack.get("prompt") or prompt_for(pack["title"],
                                                pack.get("kind", "cross"),
                                                builder.style, builder.locale),
        pack_key=pack["key"])
    # D3: 路径级锚定目录 —— PATH 模式命中(绝非 grep/文档)定义 pack 的地盘;
    # grep/文档证据只能在那些目录里贡献子项源
    parent_path_dirs = {dirname(f["path"]) for f in evidence
                        if match_any(f["path"], pack.get("patterns") or [])}
    # D12: ops-thin 上限 —— 强证据 <3 时 pack 至多实例化 ops_thin_cap 个子项
    pack_strong = [f for f in evidence if not is_weak_evidence(f["path"])]
    child_cap = pack.get("ops_thin_cap") if len(pack_strong) < 3 else None
    children = []
    for child in pack.get("children", []):
        if child_cap is not None and len(children) >= child_cap:
            builder.theme_declined.append({
                "pack": pack["key"], "child": child.get("key"),
                "title": child.get("title"),
                "reason": f"ops-thin repo cap ({len(pack_strong)} strong evidence "
                          f"files): kept top {child_cap} children"})
            continue
        child_ev = corpus.collect(child, pool=evidence)
        if len(child_ev) < scaled_min(child, 2, profile):
            # P45: 证据不足的静默丢弃也入 theme_declined —— 人审门要能区分
            # "仓库没有该子主题"与"被大小底线切掉"(D12 要关掉的盲点)
            builder.theme_declined.append({
                "pack": pack["key"], "child": child.get("key"),
                "title": child.get("title"),
                "reason": f"evidence below minimum: {len(child_ev)} file(s), "
                          f"needs {scaled_min(child, 2, profile)} "
                          "(D12 evidence gate)"})
            continue
        # D12 证据门 tier 1: 关键词门控子项需要 ≥1 个非文档非锁文件且路径含关键词
        strong_ev = [f for f in child_ev if not is_weak_evidence(f["path"])]
        kws = child.get("evidence_keywords")
        if kws:
            kw_strong = [f for f in strong_ev
                         if any(k in f["path"].lower() for k in kws)]
            if not kw_strong:
                builder.theme_declined.append({
                    "pack": pack["key"], "child": child.get("key"),
                    "title": child.get("title"),
                    "reason": "no strong evidence matching keywords: " +
                              ", ".join(kws[:6]) + "…"})
                continue
        # tier 2: requires_strong 子项需要 N 个强文件(纯文档证据归 devguide)
        need_strong = child.get("requires_strong")
        if need_strong and len(strong_ev) < need_strong:
            builder.theme_declined.append({
                "pack": pack["key"], "child": child.get("key"),
                "title": child.get("title"),
                "reason": f"only {len(strong_ev)} strong evidence file(s), needs "
                          f"{need_strong} (docs-only evidence belongs to the dev guide)"})
            continue
        anchor_dirs = {dirname(f["path"]) for f in child_ev
                       if match_any(f["path"], child.get("patterns") or [])}
        if not anchor_dirs:
            anchor_dirs = parent_path_dirs
        anchored = [f for f in child_ev
                    if not anchor_dirs or dirname(f["path"]) in anchor_dirs]
        pool_for_src = anchored if anchor_dirs else child_ev
        # 子项继承 pack 的源关键词: 通用默认会按 main/service 排序, 把
        # src/main/java 下所有 Java 文件抬到主题真证据之上
        child_kws = child.get("source_keywords") or pack.get("source_keywords") \
            or ["index", "main", "config", "service", "model", "route"]
        child_src = pick_files(pool_for_src, child_kws, profile["source_limit"])
        if not child_src:
            child_src = [f["path"] for f in pool_for_src[: profile["source_limit"]]]
        if not child_src:
            continue
        child_page = builder.add(
            page_id="pack-" + pack["key"] + "-" + child["key"],
            title=child["title"], kind=pack.get("kind", "cross"),
            parent=parent["id"],
            scope=(sorted(anchor_dirs) if anchor_dirs
                   else (dirs_of(child_ev) or parent["scope"])),
            source_files=child_src, priority=pack.get("priority", 650) - 20,
            prompt=child.get("prompt") or
            prompt_for(child["title"], pack.get("kind", "cross"), builder.style,
                     builder.locale),
            title_hint=pack["title"], pack_key=pack["key"])
        # D22: 目录命名的子项标题来自主题包而非本仓库 —— writer 必须验证标题
        # 概念存在并报告 mismatch, 而不是静默自裁范围
        child_page["title_check"] = True
        # 宽锚集(>20 目录)= 天然跨切: 截断 scope 会伪造 scope/hit 失败,
        # 继承父 scope 并豁免 check gate
        if len(anchor_dirs) > 20:
            child_page["scope"] = parent["scope"][:8]
            child_page["cross_cutting"] = True
        if len(child_src) < 4:
            child_page["evidence_thin"] = True
        leaves = make_leaves(builder, corpus, child_page, child, pool_for_src,
                             pack.get("kind", "cross"))
        child_page["related_pages"] = [x["id"] for x in leaves]
        children.append(child_page)
    # D13: pack 内兄弟不得共享 >65% 源; primary 文件领先
    for c in children:
        primary_first(c)
    trim_sibling_overlap(children, builder.sibling_trims)
    parent["related_pages"] = [x["id"] for x in children]
    return parent


def collect_dependencies(inv: dict) -> Counter:
    counts = Counter()
    for m in inv.get("manifests", []):
        raw = m.get("dependencies") or {}
        if isinstance(raw, dict):
            for k in raw:
                counts[k] += 1
        elif isinstance(raw, list):
            for k in raw:
                counts[k] += 1
    return counts


# --------------------------------------------------------------------------- #
# pack 装配(条件化于证据探针)
# --------------------------------------------------------------------------- #

def build_packs(builder: Builder, corpus: Corpus, modules: dict) -> list:
    packs: list = []

    arch_children = []
    for key, title, pats in [
            ("client", "客户端架构", P_FRONTEND),
            ("server", "服务端架构", P_BACKEND),
            ("data", "数据架构", P_DATA),
            ("comm", "通信架构", P_API),
            ("deploy", "部署架构", P_INFRA),
            ("security", "安全架构", P_SEC),
            ("performance", "性能架构", P_PERF)]:
        ev = corpus.collect({"patterns": pats})
        if len(ev) >= 2:
            arch_children.append({"key": key, "title": title,
                                  "patterns": pats, "min": 2})
    packs.append({
        "key": "architecture", "title": "架构设计", "kind": "architecture",
        "always": True, "priority": 950, "patterns": [],
        "source_keywords": ["readme", "main", "index", "app", "server", "config"],
        "children": arch_children})

    frontend_ev = corpus.collect({"patterns": P_FRONTEND})
    backend_ev = corpus.collect({"patterns": P_BACKEND})
    desktop_ev = corpus.collect({"patterns": P_DESKTOP})
    ai_ev = corpus.collect({"patterns": P_AI})
    data_ev = corpus.collect({"patterns": P_DATA})
    api_ev = corpus.collect({"patterns": P_API})
    sec_ev = corpus.collect({"patterns": P_SEC})
    perf_ev = corpus.collect({"patterns": P_PERF})
    test_ev = corpus.collect({"patterns": P_TEST})
    infra_ev = corpus.collect({"patterns": P_INFRA})
    tools_ev = corpus.collect({"patterns": P_TOOLS})

    if len(frontend_ev) >= 5 and len(desktop_ev) < 3:
        packs.append({
            "key": "frontend", "title": "前端架构", "kind": "architecture",
            "priority": 900, "patterns": P_FRONTEND,
            "exclude": ["src-tauri/", "tauri", "electron"],
            "source_keywords": ["app", "main", "index", "router", "store", "component"],
            "children": [
                {"key": "entry", "title": "应用入口与路由",
                 "patterns": ["/app/", "/pages/", "/router", "main.", "app.", "index."], "min": 2},
                {"key": "state", "title": "状态管理",
                 "patterns": ["/stores/", "/store/", "/state/", "/reducers/", "/context/", "/hooks/"],
                 "min": 2,
                 "leaves": {"mode": "files", "pattern": "/stores/", "suffix": "",
                            "cap": 8,
                            "title_map": builder.overrides["title_maps"].get("store") or {}}},
                {"key": "components", "title": "组件系统",
                 "patterns": ["/components/", "/ui/", "/views/", "/widgets/"], "min": 2,
                 "leaves": [
                     {"mode": "files", "pattern": "/pages/", "suffix": "",
                      "title_map": builder.overrides["title_maps"].get("feature") or {},
                      "cap": 6},
                     {"mode": "group", "pattern": "/shared/", "title": "共享业务组件"},
                     {"mode": "group", "pattern": "/ui/", "title": "基础 UI 组件"}]},
                {"key": "platform", "title": "平台适配层",
                 "patterns": ["/platform/", "/adapters/", "/native/", "/bridge/", "/integrations/"],
                 "min": 2,
                 "leaves": {"mode": "files", "suffix": "", "cap": 5,
                            "title_rules": builder.overrides["title_maps"].get("platform_rules")
                            or []}},
                {"key": "styles", "title": "样式系统",
                 "patterns": ["*.css", "*.scss", "*.sass", "*.less", "/styles/", "/theme/", "/tokens/"],
                 "min": 2},
                {"key": "i18n", "title": "国际化系统",
                 "patterns": ["/i18n/", "/locale/", "/locales/", "/translations/"], "min": 1},
            ]})

    if len(desktop_ev) >= 3:
        packs.append({
            "key": "desktop", "title": "桌面应用", "kind": "architecture",
            "priority": 890, "patterns": P_DESKTOP,
            "source_keywords": ["tauri", "main", "config", "ipc", "window"],
            "children": [
                {"key": "structure", "title": "桌面应用结构",
                 "patterns": ["src-tauri/", "tauri.conf", "electron", "main.rs"], "min": 2},
                {"key": "ipc", "title": "IPC 通信机制",
                 "patterns": ["/ipc/", "/commands/", "/transport/", "bridge"], "min": 2},
                {"key": "windows", "title": "窗口管理系统",
                 "patterns": ["/windows/", "main"], "min": 2,
                 # 去试点化: 通用窗口名 + pwiki.yaml title_maps.window 的键
                 # (试点的 'orb' 这类专名由被分析仓库自己声明, 不进共享代码)
                 "leaves": {"mode": "names",
                            "names": sorted(
                                set(builder.overrides["title_maps"].get("window") or {})
                                | {"main", "settings", "stats", "library",
                                   "chat", "card"}),
                            "title_map": builder.overrides["title_maps"].get("window") or {},
                            "cap": 6}},
                {"key": "storage", "title": "本地存储系统",
                 "patterns": ["/db/", "/sqlite/", "/storage/", "/repo/", "/migrations/"], "min": 2},
                {"key": "security", "title": "安全与认证",
                 "patterns": ["/auth/", "/security/", "/credential/", "/keyring/"], "min": 2},
            ]})

    ui_ev = corpus.collect({"patterns": ["/components/", "/ui/", "/views/", "/pages/",
                                         "/hooks/", "/i18n/", "/styles/"]})
    if len(ui_ev) >= 10 and len(desktop_ev) >= 3:
        packs.append({
            "key": "ui", "title": "UI 组件", "kind": "cross", "priority": 870,
            "patterns": ["/components/", "/ui/", "/views/", "/pages/", "/hooks/",
                         "/i18n/", "/styles/"],
            "source_keywords": ["components", "views", "pages", "ui", "styles"],
            "children": [
                {"key": "library", "title": "组件库",
                 "patterns": ["/ui/", "/components/ui/", "/primitives/"], "min": 2},
                {"key": "pages", "title": "页面组件",
                 "patterns": ["/pages/", "/views/", "/screens/"], "min": 2,
                 "leaves": {"mode": "files", "suffix": "",
                            "title_map": builder.overrides["title_maps"].get("ui_page")
                            or builder.overrides["title_maps"].get("feature") or {},
                            "cap": 6}},
                {"key": "styles", "title": "样式系统",
                 "patterns": ["*.css", "*.scss", "/styles/", "/theme/"], "min": 2},
                {"key": "i18n", "title": "国际化系统",
                 "patterns": ["/i18n/", "/locale/", "/locales/"], "min": 1},
                {"key": "hooks", "title": "Hooks 与状态管理",
                 "patterns": ["/hooks/", "/stores/", "/store/", "/state/"], "min": 2},
            ]})

    core_ev = corpus.collect({"patterns": ["/engine/", "/execution/", "/core/",
                                           "/domain/", "/parser/", "/player/",
                                           "/audio/", "/state/", "/runtime/"]})
    # 去试点化: 通用标题(试点的「训练会话状态机/字幕解析器/句子级播放器」
    # 曾按 training/subtitle 关键词漏进任何相似仓库; 专名走 pwiki.yaml 的
    # title_aliases 覆盖)
    core_state_title = "状态机设计"
    core_parser_title = "解析器"
    core_player_title = "播放器"
    if len(core_ev) >= 4:
        packs.append({
            "key": "core", "title": "核心模块", "kind": "architecture",
            "priority": 860,
            "patterns": ["/engine/", "/execution/", "/core/", "/domain/",
                         "/parser/", "/player/", "/audio/", "/state/", "/runtime/"],
            "source_keywords": ["engine", "core", "state", "parser", "player", "audio"],
            "children": [
                {"key": "engine", "title": "执行引擎",
                 "patterns": ["/engine/", "/execution/", "/runtime/", "/run/"], "min": 2},
                {"key": "state", "title": core_state_title,
                 "patterns": ["/state/", "state-machine", "statemachine", "/machine/",
                              "training", "session"], "min": 1},
                {"key": "template", "title": "模板系统",
                 "patterns": ["/template/", "/templates/", "template"], "min": 1},
                {"key": "context", "title": "执行上下文管理",
                 "patterns": ["/context/", "/contexts/", "context."], "min": 1},
                {"key": "snapshot", "title": "快照机制",
                 "patterns": ["/snapshot/", "/snapshots/", "snapshot"], "min": 1},
                {"key": "errors", "title": "错误处理机制",
                 "patterns": ["/error/", "/errors/", "/exception/", "error.", "errors."],
                 "min": 1},
                {"key": "parser", "title": core_parser_title,
                 "patterns": ["/parser/", "/subtitle/", "/caption/", "/parse/"], "min": 1},
                {"key": "player", "title": core_player_title,
                 "patterns": ["/player/", "/sentence/", "/audio/", "/recorder/"], "min": 1},
                {"key": "audio", "title": "音频处理引擎",
                 "patterns": ["/audio/", "/recorder/", "wav", "/tts/"], "min": 1},
            ]})

    if len(ai_ev) >= 4:
        ai_title = ("AI 功能" if corpus.match(["orchestrator", "/ml/", "/worker/", "agent"],
                                              pool=ai_ev) else "AI 服务集成")
        packs.append({
            "key": "ai", "title": ai_title, "kind": "architecture", "priority": 850,
            "patterns": P_AI,
            "source_keywords": ["ai", "llm", "contract", "mock", "stream",
                                "provider", "orchestrator"],
            "children": [
                {"key": "contracts", "title": "服务契约定义",
                 "patterns": ["/contracts/", "/interfaces/", "/services/"], "min": 1},
                {"key": "mock", "title": "Mock 服务实现",
                 "patterns": ["/mock/", "/mocks/"], "min": 1},
                {"key": "streaming", "title": "流式响应处理",
                 "patterns": ["/stream/", "/streaming/", "/sse/"], "min": 1,
                 "grep": ["text/event-stream", "EventSource", "AsyncGenerator", "SSE"]},
                {"key": "orchestrator", "title": "AI 编排器",
                 "patterns": ["/orchestrator/", "/agent/", "/agents/", "/workflow/"], "min": 1},
                {"key": "ml", "title": "机器学习工作流",
                 "patterns": ["/ml/", "/ml-worker/", "/inference/", "/training", "train"],
                 "min": 1},
                {"key": "providers", "title": "多厂商适配层",
                 "patterns": ["provider", "adapter", "azure", "tencent", "iflytek",
                              "openai", "anthropic", "huggingface", "kokoro", "ollama"],
                 "min": 1,
                 "leaves": {"mode": "names",
                            "names": ["azure", "tencent", "iflytek", "mock", "openai",
                                      "anthropic", "huggingface", "kokoro", "ollama"],
                            "title_map": PROVIDER_TITLES, "cap": 6}},
                {"key": "backend", "title": "AI 后端服务",
                 "patterns": ["/soe/", "/asr/", "/tts/", "/transcribe/", "/align/",
                              "/routes/"], "min": 1},
                {"key": "mapping", "title": "结果映射与标准化",
                 "patterns": ["mapper", "mapping", "normalize", "result"], "min": 1},
                {"key": "chat", "title": "AI 对话与生成",
                 "patterns": ["/chat/", "/generate/", "/enhance/", "/prompt/"], "min": 1},
            ]})

    if len(api_ev) >= 3:
        api_title = "API 接口参考" if corpus.match(["*.proto"], pool=api_ev) else "API 参考"
        packs.append({
            "key": "api", "title": api_title, "kind": "cross", "priority": 840,
            "patterns": P_API,
            "source_keywords": ["route", "proto", "openapi", "contract", "model"],
            "children": [
                {"key": "rest", "title": "REST API 接口",
                 "patterns": ["/routes/", "/controllers/", "/handlers/", "openapi", "swagger"],
                 "min": 2},
                {"key": "ws", "title": "WebSocket 实时接口",
                 "patterns": ["/ws/", "/websocket/", "/socket/"], "min": 1},
                {"key": "grpc", "title": "gRPC 接口文档", "patterns": ["*.proto"],
                 "min": 1, "leaves": {"mode": "proto_services", "cap": 6}},
                {"key": "ipc", "title": "IPC 通信接口",
                 "patterns": ["/ipc/", "/commands/", "/transport/"], "min": 1},
                {"key": "contracts", "title": "服务契约",
                 "patterns": ["/contracts/", "/contract/", "contracts.", "openapi", "swagger"],
                 "min": 1},
                {"key": "models", "title": "数据模型参考",
                 "patterns": ["/types/", "/models/", "/schema/"], "min": 1},
            ]})

    if len(data_ev) >= 4:
        data_title = ("数据存储" if corpus.match(["/db/", "/storage/", "/sqlite/",
                                                  "indexeddb"], pool=data_ev)
                      else "数据模型")
        packs.append({
            "key": "data", "title": data_title, "kind": "cross", "priority": 830,
            "patterns": P_DATA,
            "source_keywords": ["model", "schema", "migration", "db", "storage", "type"],
            "children": [
                {"key": "domain", "title": "领域模型",
                 "patterns": ["/types/", "/domain/", "/models/", "/entities/", "/stores/"],
                 "min": 1},
                {"key": "localdb", "title": "本地数据库",
                 "patterns": ["/sqlite/", "/db/", "/local/"], "min": 1},
                {"key": "clouddb", "title": "云端数据库",
                 "patterns": ["postgres", "/cloud/", "/database/", "/migrations/"], "min": 1},
                {"key": "sync", "title": "数据同步机制",
                 "patterns": ["/sync/", "/outbox/", "/events/", "/event/"], "min": 1},
                {"key": "storage", "title": "存储封装层",
                 "patterns": ["/storage/", "indexeddb", "/idb/"], "min": 1},
                {"key": "preferences", "title": "偏好设置存储",
                 "patterns": ["/settings/", "settings", "preferences", "localstorage"],
                 "min": 1},
                {"key": "backup", "title": "备份与恢复",
                 "patterns": ["/backup/", "/export/", "/import/", "/restore/"], "min": 1},
            ]})

    deploy_pack = {
        "key": "deploy", "title": "部署运维", "kind": "cross", "priority": 820,
        "patterns": P_INFRA + ["readme", "/start", "docker"],
        "docs": ["部署", "deployment", "deploy", "启动", "startup"],
        "source_keywords": ["docker", "deploy", "start", "config", "readme"],
        # D12: ops-thin 仓库(无 dockerfile/ci/systemd 强证据)把 deploy 子项
        # 上限压到 2 —— 只留文档支撑的启动/部署页, 不编全套运维
        "ops_thin_cap": 2,
        "children": [
            {"key": "env", "title": "环境准备",
             "patterns": ["readme", "/env", "requirements", ".env"], "min": 1,
             # devguide/环境搭建 拥有纯文档故事; deploy env 需要真实搭建产物
             "requires_strong": 2},
            {"key": "start", "title": "服务启动",
             "patterns": ["/start", "start.sh", "startup", "main."], "min": 1},
            {"key": "process", "title": "进程管理",
             "patterns": ["pm2", "systemd", "supervisor", "procfile"], "min": 1,
             "docs": ["pm2", "systemd", "进程", "supervisor"],
             "evidence_keywords": ["pm2", "systemd", "supervisor", "procfile",
                                   "daemon", "runit", "openrc"]},
            {"key": "network", "title": "网络配置",
             "patterns": ["nginx", "proxy", "port", "/config/"], "min": 1,
             "docs": ["端口", "port", "nginx", "反向代理", "proxy"],
             "evidence_keywords": ["nginx", "haproxy", "caddy", "traefik",
                                   "envoy", "firewall", "iptables", "certbot",
                                   "letsencrypt", "cert-manager"]},
            {"key": "frontend", "title": "前端部署",
             "patterns": ["/client/", "/web/", "next.config", "vercel"], "min": 1,
             "docs": ["前端部署", "frontend deploy", "静态资源", "build"],
             # next.config 是应用配置, 不是部署证据
             "requires_strong": 2},
            {"key": "backend", "title": "后端部署",
             "patterns": ["/server/", "/services/", "docker"], "min": 1,
             "docs": ["后端部署", "backend deploy", "服务启动", "server"]},
            {"key": "monitor", "title": "监控与日志",
             "patterns": ["/log/", "/monitor/", "/metrics/", "/health/"], "min": 1,
             "docs": ["监控", "日志", "monitor", "log"],
             # 应用自己的 logger 不是监控基础设施
             "evidence_keywords": ["prometheus", "grafana", "otel",
                                   "opentelemetry", "sentry", "datadog",
                                   "zabbix", "alertmanager", "loki"]},
            {"key": "backup", "title": "备份与恢复",
             "patterns": ["/backup/", "/restore/"], "min": 1,
             "docs": ["备份", "恢复", "backup", "restore"],
             # data 包拥有应用级备份; deploy 备份需要运维产物才值第二页
             "evidence_keywords": ["backup", "restore", "snapshot", "dump"]},
        ]}
    # 证据门与 pack 用同一份 spec(探针双写曾各自漂移, 门判的证据集与
    # pack 实际收集的证据集不一致)
    deploy_ev = corpus.collect({"patterns": deploy_pack["patterns"],
                                "docs": deploy_pack["docs"]})
    if len(deploy_ev) >= 2:
        packs.append(deploy_pack)

    if len(infra_ev) >= 3:
        packs.append({
            "key": "infra", "title": "基础设施", "kind": "cross", "priority": 810,
            "patterns": P_INFRA,
            "source_keywords": ["docker", "compose", "k8s", "helm", "ci", "migration"],
            "children": [
                {"key": "containers", "title": "容器化部署",
                 "patterns": ["Dockerfile", "docker-compose", "compose", "/k8s/", "/helm/"],
                 "min": 1,
                 "leaves": {"mode": "names",
                            "names": ["docker", "compose", "kubernetes", "k8s", "helm", "offline"],
                            "title_map": {"docker": "Docker 镜像构建",
                                          "compose": "Compose 编排",
                                          "kubernetes": "Kubernetes 部署",
                                          "k8s": "Kubernetes 部署",
                                          "helm": "Helm 部署",
                                          "offline": "离线包构建"},
                            "cap": 5}},
                {"key": "ci", "title": "CI/CD 流水线",
                 "patterns": [".github/workflows/", ".gitlab-ci", "Jenkinsfile"], "min": 1},
                {"key": "migrations", "title": "数据库迁移管理",
                 "patterns": ["/migrations/", "/migration/", "*.sql"], "min": 1},
                {"key": "observability", "title": "监控与可观测性",
                 "patterns": ["otel", "opentelemetry", "/metrics/", "prometheus",
                              "grafana", "/trace/"], "min": 1},
                {"key": "benchmarks", "title": "性能基准测试",
                 "patterns": ["/bench/", "/benchmark/", "/benchmarks/"], "min": 1},
            ]})

    if len(sec_ev) >= 3:
        packs.append({
            "key": "security", "title": "安全设计", "kind": "cross", "priority": 800,
            "patterns": P_SEC,
            "source_keywords": ["auth", "security", "permission", "config", "validation"],
            "children": [
                {"key": "authn", "title": "认证与授权",
                 "patterns": ["/auth/", "/auth", "auth.", "oauth", "jwt", "/login/",
                              "/session/", "session."], "min": 1},
                {"key": "access", "title": "访问控制",
                 "patterns": ["/permission/", "/permissions/", "/rbac/", "/access/",
                              "/acl/", "permission", "role"], "min": 1},
                {"key": "data", "title": "数据安全",
                 "patterns": ["/crypto/", "/encrypt/", "/secret/", "/credential/",
                              "crypto", "hash", "hmac", "argon"], "min": 1},
                {"key": "input", "title": "输入验证",
                 "patterns": ["/validation/", "/validators/", "/schema/",
                              "validation", "validator", "zod"], "min": 1},
                {"key": "audit", "title": "审计日志",
                 "patterns": ["/audit/", "/audit-log/", "audit"], "min": 1},
            ]})

    if len(perf_ev) >= 2 or len(frontend_ev) >= 20 or len(backend_ev) >= 20:
        packs.append({
            "key": "performance", "title": "性能优化", "kind": "cross", "priority": 790,
            "patterns": P_PERF + P_FRONTEND + P_BACKEND,
            "source_keywords": ["cache", "perf", "bench", "optimize", "metric"],
            "children": [
                {"key": "frontend", "title": "前端性能优化",
                 "patterns": ["/client/", "/frontend/", "/web/", "*.tsx", "*.css"], "min": 2},
                {"key": "backend", "title": "后端性能优化",
                 "patterns": ["/server/", "/services/", "/api/", "*.java", "*.go", "*.py"],
                 "min": 2},
                {"key": "desktop", "title": "桌面应用性能优化",
                 "patterns": ["src-tauri/", "tauri", "electron"], "min": 1},
                {"key": "monitoring", "title": "性能监控与度量",
                 "patterns": ["/metrics/", "/monitor/", "prometheus", "otel", "/trace/"],
                 "min": 1},
            ]})

    if len(test_ev) >= 3:
        packs.append({
            "key": "testing", "title": "测试策略", "kind": "cross", "priority": 780,
            "patterns": P_TEST,
            "source_keywords": ["test", "spec", "fixture", "mock"],
            "children": [
                {"key": "frontend", "title": "前端测试",
                 "patterns": ["*.test.ts", "*.test.tsx", "*.spec.ts", "*.spec.tsx",
                              "/__tests__/"], "min": 1},
                {"key": "backend", "title": "后端测试",
                 "patterns": ["_test.go", "_test.py", "Test.java", "Test.kt",
                              "/test/", "/tests/"], "min": 1},
                {"key": "contract", "title": "契约测试",
                 "patterns": ["contract", "openapi", "proto", "schema"], "min": 1},
                {"key": "infra", "title": "测试基础设施",
                 "patterns": ["fixture", "mock", "factory", "setup", "conftest",
                              "vitest.config", "jest.config"], "min": 1},
            ]})

    trouble_spec = {
        "patterns": ["/log/", "/logging/", "/health/", "/metrics/", "/monitor/",
                     "/tracing/", "/debug/", "/diagnostic/", "error.", "logger"],
        "docs": ["故障", "排查", "troubleshoot", "debug", "faq", "常见问题"],
        "grep": ["errorhandler", "health", "metrics", "logger", "trace",
                 "diagnostic"]}
    trouble_ev = corpus.collect(trouble_spec)
    if len(trouble_ev) >= 2:
        packs.append({
            "key": "troubleshooting", "title": "故障排查", "kind": "cross",
            "priority": 770,
            **trouble_spec,
            "source_keywords": ["log", "health", "metrics", "debug", "error"],
            "children": [
                {"key": "startup", "title": "启动问题排查",
                 "patterns": ["/start", "main.", "app.", "boot", "startup"], "min": 1,
                 "docs": ["启动", "startup"]},
                {"key": "connection", "title": "连接问题排查",
                 "patterns": ["/net/", "/socket/", "/ws/", "/http/", "connection"],
                 "min": 1, "docs": ["连接", "connection"]},
                {"key": "performance", "title": "性能问题排查",
                 "patterns": ["/metrics/", "/trace/", "/perf/", "/profile/"], "min": 1,
                 "docs": ["性能", "performance"]},
                {"key": "logs", "title": "日志分析方法",
                 "patterns": ["/log/", "/logging/", "/logger/", "log.", "logger"],
                 "min": 1, "grep": ["slf4j", "pino", "logger", "log."],
                 "docs": ["日志", "log"]},
                {"key": "debug", "title": "调试技巧",
                 "patterns": ["/debug/", "/diagnostic/", "debug"], "min": 1,
                 "docs": ["调试", "debug"]},
                {"key": "alerts", "title": "监控告警",
                 "patterns": ["/monitor/", "/alert/", "prometheus", "grafana", "alert"],
                 "min": 1, "docs": ["告警", "alert"]},
                {"key": "emergency", "title": "应急预案",
                 "patterns": ["/emergency/", "/runbook/", "/playbook/", "runbook"],
                 "min": 1, "docs": ["应急", "预案", "runbook"]},
            ]})

    packs.append({
        "key": "devguide", "title": "开发指南", "kind": "cross", "always": True,
        "priority": 760,
        "patterns": ["readme", "Makefile", "package.json", "pyproject.toml",
                     "Cargo.toml", "go.work", "settings.gradle", "pom.xml",
                     "CONTRIBUTING", "/scripts/"],
        "commands": True,
        "source_keywords": ["readme", "makefile", "package", "contributing", "script"],
        "children": [
            {"key": "env", "title": "环境搭建",
             "patterns": ["readme", "requirements", "package.json", ".env", "Makefile"],
             "min": 1, "docs": ["install", "安装", "环境", "requirements", "setup"]},
            {"key": "standards", "title": "代码规范",
             "patterns": ["lint", "eslint", "prettier", "ruff", "checkstyle",
                          "editorconfig", "rustfmt"], "min": 1,
             "docs": ["规范", "lint", "eslint", "prettier", "代码风格"]},
            {"key": "testing", "title": "测试指南",
             "patterns": ["test", "vitest", "jest", "pytest", "junit"], "min": 1,
             "docs": ["测试", "test", "vitest", "pytest", "jest"]},
            {"key": "build", "title": "构建与部署",
             "patterns": ["build", "Makefile", "package.json", "gradle", "cargo"],
             "min": 1, "docs": ["构建", "build", "部署", "deploy"]},
            {"key": "debug", "title": "调试工具",
             "patterns": ["debug", "devtools", "inspector", "diagnostic"], "min": 1,
             "docs": ["调试", "debug"]},
            {"key": "contributing", "title": "贡献指南",
             "patterns": ["CONTRIBUTING", "contributing", "CODE_OF_CONDUCT"], "min": 1,
             "docs": ["贡献", "contributing"]},
        ]})

    if len(tools_ev) >= 3:
        packs.append({
            "key": "devtools", "title": "开发工具", "kind": "cross", "priority": 750,
            "patterns": P_TOOLS,
            "source_keywords": ["tool", "script", "scaffold", "codegen",
                                "contract", "prototype"],
            "children": [
                {"key": "scaffold", "title": "脚手架工具",
                 "patterns": ["/scaffold/", "/scaffolds/", "scaffolding"], "min": 1},
                {"key": "codegen", "title": "代码生成工具",
                 "patterns": ["/codegen/", "/generate/", "/gen/"], "min": 1},
                {"key": "contract", "title": "契约验证工具",
                 "patterns": ["/contract-check/", "/contract/", "openapi", "proto"],
                 "min": 1},
                {"key": "prototype", "title": "原型验证工具",
                 "patterns": ["/prototype/", "/poc/"], "min": 1},
                {"key": "deps", "title": "依赖管理工具",
                 "patterns": ["workspace", "lock", "pnpm", "uv", "go.work", "gradle"],
                 "min": 1},
            ]})

    # D2: 服务目录包 —— ≥3 个 service 模块即实例化; 按名字线索(网关/BFF、
    # AI/ML)与生态聚类, 任何 monorepo 形态都不静默丢失服务章节。
    # min_module_files=1 保骨架服务可见。
    service_modules = [mp for mp in modules
                       if mp.startswith("services/")
                       and not modules[mp].get("synthetic")]
    if len(service_modules) >= 3:
        def _leaf_name(mp):
            return mp.split("/")[-1].lower()

        gw_names = [mp for mp in service_modules
                    if any(k in _leaf_name(mp) for k in
                           ("gateway", "bff", "proxy", "edge"))]
        ai_names = [mp for mp in service_modules
                    if any(k in _leaf_name(mp) for k in
                           ("ai-", "ml-", "llm", "orchestrat", "inference", "recommend"))]
        rest = [mp for mp in service_modules
                if mp not in gw_names and mp not in ai_names]
        eco_titles = {"java": "Java 业务服务", "go": "Go 微服务集",
                      "python": "Python 智能服务", "npm": "TypeScript 服务",
                      "rust": "Rust 服务", "other": "其他服务"}
        by_eco: dict = defaultdict(list)
        for mp in rest:
            by_eco[modules[mp].get("ecosystem") or "other"].append(mp)

        def _group_child(key, title, names):
            return {
                "key": key, "title": title,
                "patterns": sorted({_leaf_name(x) for x in names})[:8] or ["/services/"],
                "min": 1,
                "leaves": {"mode": "modules",
                           "cap": builder.profile["leaves_cap"] + 2,
                           "min_module_files": 1,
                           "module_names": sorted({_leaf_name(x) for x in names})}}

        ms_children = [{"key": "overview", "title": "服务架构总览",
                        "patterns": ["/services/"], "min": 1,
                        "docs": ["服务规划", "服务编号", "服务拆分", "架构"]}]
        if gw_names:
            ms_children.append(_group_child("gateway", "网关与 BFF 服务", gw_names))
        if ai_names:
            ms_children.append(_group_child("ai", "AI 智能服务", ai_names))
        for eco in ("java", "go", "python", "npm", "rust", "other"):
            group = by_eco.get(eco) or []
            if group:
                ms_children.append(_group_child("eco-" + eco, eco_titles[eco], group))
        packs.append({
            "key": "microservices", "title": "微服务集群", "kind": "architecture",
            "priority": 840, "patterns": ["/services/"],
            "source_keywords": ["readme", "main", "application", "service", "config"],
            "children": ms_children})

    package_modules = [mp for mp in modules
                       if mp.startswith("packages/")
                       and not modules[mp].get("synthetic")]
    if len(package_modules) >= 2:
        packs.append({
            "key": "packages", "title": "共享包集合", "kind": "cross", "priority": 730,
            "patterns": ["/packages/"],
            "source_keywords": ["index", "package", "src"],
            "children": [
                {"key": "all", "title": "共享包总览", "patterns": ["/packages/"],
                 "min": 1,
                 "leaves": {"mode": "modules", "cap": 8, "min_module_files": 3}},
            ]})
    return packs


# --------------------------------------------------------------------------- #
# 主流程: 主题规划(纯函数) → plan v3 页面 + 报告
# --------------------------------------------------------------------------- #

def run_themes(ctx: Ctx, inv: dict, decomposed: dict, profile_name: str,
               framework_mods: set, check_warn_only: bool = False,
               style: str = "") -> dict:
    """跑一遍主题规划。返回 {pages(plan v3 形状), cards, report,
    check_failed}(P52: 旧 docstring 承诺的 'sheets' 键从不存在)。
    style 为 plan 级风格(缺省退 config) —— 提示词与节模板都按它取口径。"""
    profile = dict(PROFILES[profile_name])
    profile["max_pages"] = config.PROFILE_BUDGETS[profile_name]
    profile["target_min"] = config.PROFILE_TARGETS[profile_name]
    modules = decomposed["modules"]
    order = decomposed["order"]
    repo = inv["repo"]
    files = inv["files"]
    manifest_paths = {m["path"] for m in inv.get("manifests", [])}
    corpus = Corpus(repo, files, modules, manifest_paths)
    overrides = load_overrides(ctx)
    style = str(style or ctx.style)
    builder = Builder(corpus, profile, ctx.locale, overrides, style=style)

    # ---------- overview + quickstart ----------
    priority_docs = [
        "README.md", "README.rst", "README", "CLAUDE.md", "AGENTS.md",
        "package.json", "pyproject.toml", "Cargo.toml", "go.work", "go.mod",
        "Makefile", "docker-compose.yml", "docker-compose.yaml",
        "settings.gradle", "settings.gradle.kts", "pom.xml",
    ]
    overview_src = [p for p in priority_docs if p in corpus.by_path]
    overview_src += [e["path"] for e in inv.get("entry_points", [])[:4]]
    overview_src += [f["path"] for f in files
                     if f["path"].startswith("docs/")
                     and f["path"].endswith((".md", ".mdx"))][:3]
    builder.add(
        page_id="project-overview", title="项目概述", kind="overview", parent=None,
        scope=["."], source_files=overview_src, priority=1000,
        prompt=prompt_for("项目概述", "overview", style, builder.locale))
    readme_docs = corpus.doc_hits(["install", "getting started", "quick start",
                                   "运行", "启动", "安装"], limit=20)
    if readme_docs:
        qsrc = [f["path"] for f in readme_docs[:4]] + overview_src[:4]
        builder.add(
            page_id="quickstart", title="快速开始", kind="feature", parent=None,
            scope=dirs_of(readme_docs) or ["."], source_files=qsrc,
            priority=980,
            prompt="创建快速开始指南：环境要求、安装、配置、启动、验证、常见问题。"
                   "命令必须来自仓库脚本或文档。")

    # ---------- packs ----------
    packs = build_packs(builder, corpus, modules)
    for pack in packs:
        instantiate_pack(builder, corpus, pack, profile, framework_mods)

    # ---------- features(桌面 UI 包已覆盖页面时跳过) ----------
    desktop_ev = corpus.collect({"patterns": P_DESKTOP})
    if len(desktop_ev) < 3:
        feature_files: list = []
        feature_titles = overrides["title_maps"].get("feature") or {}
        feature_families = overrides["title_maps"].get("families") or {}
        for f in files:
            p = f["path"]
            if not p.endswith((".tsx", ".jsx", ".vue", ".svelte")):
                continue
            parts = p.split("/")
            stem = stem_of(p)
            low = stem.lower()
            if low in FEATURE_EXCLUDE:
                continue
            if any(part.lower() in ("pages", "views", "screens") for part in parts[:-1]):
                feature_files.append((low, f))
        for f in files:
            p = f["path"]
            if not p.endswith(("page.tsx", "page.ts", "page.js")):
                continue
            parts = p.split("/")
            if "app" not in parts:
                continue
            i = parts.index("app")
            route = parts[i + 1:-1]
            if not route:
                continue
            stem = route[-1].strip("[]")
            if stem.startswith("[") or "..." in stem or "." in stem \
                    or stem.lower() == "slug":
                continue
            if stem.lower() in FEATURE_EXCLUDE:
                continue
            feature_files.append((stem.lower(), f))
        seen = set()
        families: dict = defaultdict(list)
        others: list = []
        for stem, f in feature_files:
            if stem in seen:
                continue
            seen.add(stem)
            if "." in stem or "..." in stem:
                continue
            title = feature_titles.get(stem) or (humanize(stem) + " 页面")
            fam = feature_families.get(stem)
            if fam:
                families[fam].append((title, f))
            else:
                others.append((title, f))
        if families or others:
            all_feature_files = [f for _, f in others] + \
                [x for v in families.values() for _, x in v]
            parent = builder.add(
                page_id="feature-guide", title="功能指南", kind="feature",
                parent=None, scope=dirs_of(all_feature_files) or ["."],
                source_files=pick_files(all_feature_files,
                                        ["page", "view", "screen"],
                                        profile["source_limit"]),
                priority=720, prompt=prompt_for("功能指南", "feature", style, builder.locale))
            top_family = max(families, key=lambda k: len(families[k])) \
                if families else None
            for fam, items in families.items():
                fam_src = pick_files([f for _, f in items],
                                     ["page", "view", "training"],
                                     profile["source_limit"])
                fam_page = builder.add(
                    page_id="feature-" + slugify(fam), title=fam, kind="feature",
                    parent=parent["id"],
                    scope=dirs_of([f for _, f in items]) or parent["scope"],
                    source_files=fam_src, priority=700,
                    prompt=prompt_for(fam, "feature", style, builder.locale),
                    title_hint="功能指南")
                if fam != top_family:
                    continue
                for title, f in items[:profile["leaves_cap"]]:
                    builder.add(
                        page_id="feature-" + slugify(fam) + "-" +
                        slugify(stem_of(f["path"])),
                        title=title, kind="feature", parent=fam_page["id"],
                        scope=[dirname(f["path"])] if dirname(f["path"])
                        else parent["scope"],
                        source_files=[f["path"]] + pick_files(
                            [x for x in files
                             if dirname(x["path"]) == dirname(f["path"])],
                            ["shared", "store", "service", "api", "type"], 3),
                        priority=690, prompt=prompt_for(title, "feature", style, builder.locale),
                        title_hint=fam)
            for title, f in others[:2]:
                builder.add(
                    page_id="feature-" + slugify(stem_of(f["path"])),
                    title=title, kind="feature", parent=parent["id"],
                    scope=[dirname(f["path"])] if dirname(f["path"])
                    else parent["scope"],
                    source_files=[f["path"]] + pick_files(
                        [x for x in files
                         if dirname(x["path"]) == dirname(f["path"])],
                        ["shared", "store", "service", "api", "type"], 3),
                    priority=690, prompt=prompt_for(title, "feature", style, builder.locale),
                    title_hint="功能指南")

    # ---------- module deep-dive fallback ----------
    covered_modules = {p["module_path"] for p in builder.pages if p.get("module_path")}
    if len(builder.pages) < int(profile["target_min"] * 0.8):
        candidates = [mp for mp in order
                      if mp and modules[mp].get("files", 0) >= profile["module_page_min"]
                      and mp not in covered_modules]
        candidates.sort(key=lambda mp: -modules[mp].get("files", 0))
        for mp in candidates:
            if len(builder.pages) >= profile["target_min"]:
                break
            m = modules[mp]
            builder.add(
                page_id="module-deep-" + slugify(mp), title=m["title"],
                kind="module", parent=None,
                scope=m.get("scope") or [mp + "/"],
                source_files=m.get("source_files") or [], priority=500,
                prompt=prompt_for(m["title"], "module", style, builder.locale),
                    module_path=mp)

    # ---------- trim(每个主题包至少保一个代表) ----------
    max_pages = profile["max_pages"]
    protected = set()
    by_pack: dict = defaultdict(list)
    for p in builder.pages:
        if p.get("pack_key"):
            by_pack[p["pack_key"]].append(p)
    for key, ps in by_pack.items():
        parent_id = "pack-" + key
        for p in ps:
            if p["id"] == parent_id or p["parent"] == parent_id:
                protected.add(p["id"])
        leaves = [p for p in ps if p["id"] != parent_id and p["parent"] != parent_id]
        leaves.sort(key=lambda p: -p["priority"])
        for p in leaves[:5]:
            protected.add(p["id"])
    while len(builder.pages) > max_pages:
        # P76: 叶判定用子计数映射(曾每删一页 O(n²) any() 嵌套扫描), related
        # 改增量移除(曾每删一页为全库重建列表)
        _child_n: dict = {}
        for c in builder.pages:
            if c.get("parent"):
                _child_n[c["parent"]] = _child_n.get(c["parent"], 0) + 1
        leaves = [p for p in builder.pages
                  if not _child_n.get(p["id"])
                  and p["kind"] != "overview" and p["id"] not in protected]
        if not leaves:
            break
        drop = min(leaves, key=lambda p: (p["priority"],
                                          len(p["source_files"]), p["id"]))
        builder.pages.remove(drop)
        builder.by_id.pop(drop["id"], None)
        builder.used_ids.discard(drop["id"])
        builder.used_slugs.discard(drop["slug"])
        builder.used_titles.discard(drop["title"])
        for p in builder.pages:
            if drop["id"] in p["related_pages"]:
                p["related_pages"].remove(drop["id"])
    ids = {p["id"] for p in builder.pages}
    for p in builder.pages:
        if p["parent"] not in ids:
            p["parent"] = ""
        p["related_pages"] = [x for x in p["related_pages"] if x in ids]

    # ---------- D8: 骨架目录 声明 vs 现状 ----------
    skeleton_dirs = {mp.rstrip("/") for mp, m in modules.items() if m.get("skeleton")}
    skeleton_clause = ("注意：本页范围包含骨架/占位目录（仅 README、清单与配置，无实现代码）。"
                       "README 中的规划内容一律标注为「声明」，与「实现现状」分开写；"
                       "缺失的实现用「仓库中未发现 X 的实现」表述，不得把声明写成已交付行为。")

    def touches_skeleton(page):
        for sd in page.get("scope", []):
            sd = sd.rstrip("/")
            if not sd or sd == ".":
                continue
            for sk in skeleton_dirs:
                if sd == sk or sd.startswith(sk + "/") or sk.startswith(sd + "/"):
                    return True
        return False

    skeleton_pages = []
    for p in builder.pages:
        if skeleton_dirs and touches_skeleton(p) and skeleton_clause not in p["prompt"]:
            p["prompt"] = p["prompt"].rstrip() + "\n" + skeleton_clause
            p["skeleton_scope"] = True
            skeleton_pages.append(p["id"])

    # ---------- D3/D4 check gate ----------
    def scope_hit(page):
        if not page["source_files"]:
            return 0.0
        dirs = {d.rstrip("/") for d in page.get("scope", []) if d and d != "."}
        if not dirs:
            return 1.0
        hit = sum(1 for s in page["source_files"]
                  if any(s == d or s.startswith(d + "/") for d in dirs))
        return hit / len(page["source_files"])

    # D6: 手改计划(与规划器)会拼出不存在的路径; 先试 _/- 近邻再丢弃
    by_dir: dict = defaultdict(set)
    for p in corpus.by_path:
        by_dir[dirname(p)].add(os.path.basename(p))

    def near_neighbor(path):
        d, base = os.path.split(path)
        for cand in (base.replace("_", "-"), base.replace("-", "_")):
            if cand != base and cand in by_dir.get(dirname(path), set()):
                return (dirname(path) + "/" + cand) if dirname(path) else cand
        return None

    hand_langs = HAND_LANGS

    check_errors: list = []
    check_warnings: list = []
    repaired = {"missing": 0, "generated": 0, "backfilled": 0}
    # D13: 回填把已被认领的文件排最后, 兄弟自然分歧; 计数也喂给最终裁剪
    backfill_claims: dict = defaultdict(int)
    # P73: 合格候选排序一次 + 路径索引 —— 曾每个薄页对全仓文件列表过一
    # 遍 ~45 项模式检查(几十薄页 × 10k 文件 = 数千万次字符串操作)
    _eligible = sorted(
        f["path"] for f in files
        if f.get("lang") in hand_langs
        and not is_generated(f["path"])
        and not match_any(f["path"], P_TEST))
    _by_path = {f["path"]: f for f in files}

    def backfill_from_scope(page, need):
        got: list = []

        def stem_affinity(cand_path):
            # 同名伴生(dictation.tsx ↔ dictation-diff.ts)是页面自己的逻辑,
            # 不是随机填充 —— 排最前
            src = page.get("source_files") or []
            if not src:
                return 0
            a, b = stem_of(src[0]).lower(), stem_of(cand_path).lower()
            n = min(len(a), len(b))
            same = 0
            for i in range(n):
                if a[i] != b[i]:
                    break
                same += 1
            return same if same >= 4 else 0

        def take(cands, cap=None):
            added = 0
            for f in sorted(cands, key=lambda x: (-stem_affinity(x["path"]),
                                                  backfill_claims[x["path"]],
                                                  -(x.get("lines") or 0), x["path"])):
                if f["path"] in page["source_files"] or f["path"] in got:
                    continue
                got.append(f["path"])
                backfill_claims[f["path"]] += 1
                added += 1
                if len(got) >= need or (cap is not None and added >= cap):
                    return

        # P73: 合格文件排序一次 + 前缀二分 —— 曾每个薄页对全仓文件
        # 列表过一遍 ~45 项模式检查(几十薄页 × 10k 文件)
        import bisect as _bis

        def _under(dirs):
            out = []
            for d in dirs:
                d2 = str(d).rstrip("/")
                if not d2 or d2 == ".":
                    continue
                j = _bis.bisect_left(_eligible, d2)
                if j < len(_eligible) and _eligible[j] == d2:
                    out.append(d2)
                i = _bis.bisect_left(_eligible, d2 + "/")
                while i < len(_eligible) and _eligible[i].startswith(d2 + "/"):
                    out.append(_eligible[i])
                    i += 1
            return out

        def scope_cands(extra_dirs=None):
            dirs = [x for x in (extra_dirs or page.get("scope") or [])
                    if x and x != "."]
            return [
                _by_path[p_] for p_ in _under(dirs)
                # D12: 永不用锁文件/元文档垫页 —— pea 评估里随机
                # CLAUDE.md/pnpm-lock 引用正是编造证据的痕迹
                if not is_never_cite(p_)
            ]

        # tier 0: 全仓同名伴生(与 primary 同 4 字符 stem 前缀), 上限 2
        comp_dirs: list = []
        if page["source_files"]:
            prim_path = page["source_files"][0]
            prim = stem_of(prim_path).lower()
            if len(prim) >= 4:
                comp = [
                    f for f in files
                    if f["path"] not in page["source_files"]
                    and f["path"] != prim_path
                    and f.get("lang") in hand_langs
                    and not is_generated(f["path"])
                    and not match_any(f["path"], P_TEST)
                    and not is_never_cite(f["path"])
                    and stem_of(f["path"]).lower()[:4] == prim[:4]
                ]
                take(comp, cap=2)
                comp_dirs = [dirname(s) for s in got]
        # tier 1: 自身 scope, claim 序
        take(scope_cands())
        # tier 2: 父 scope(窄叶加宽自身 scope, gate 保持诚实)
        if len(got) < need and page.get("parent"):
            parent_pg = builder.by_id.get(page["parent"])
            if parent_pg:
                extra = [d for d in (parent_pg.get("scope") or [])
                         if d and d not in (page.get("scope") or [])]
                if extra:
                    page["scope"] = list(dict.fromkeys(list(page["scope"]) + extra))
                    take(scope_cands())
        # tier 3: tier 0 伴生目录记录进 scope
        for d in comp_dirs:
            if d and d not in page["scope"]:
                page["scope"].append(d)
        page["source_files"].extend(got)

    for p in builder.pages:
        fixed_missing, still_missing, new_src = [], [], []
        for s in p["source_files"]:
            if s in corpus.by_path:
                new_src.append(s)
                continue
            nn = near_neighbor(s)
            if nn:
                new_src.append(nn)
                fixed_missing.append(s + " -> " + nn)
            else:
                still_missing.append(s)
        if fixed_missing or still_missing:
            p["source_files"] = new_src
            repaired["missing"] += len(still_missing)
            if fixed_missing:
                check_warnings.append({"page": p["id"], "code": "source/name-fixed",
                                       "detail": fixed_missing[:3]})
            if still_missing:
                check_warnings.append({"page": p["id"], "code": "source/missing-dropped",
                                       "detail": still_missing[:3]})
        gen = [s for s in p["source_files"] if is_generated(s)]
        if gen:
            p["source_files"] = [s for s in p["source_files"] if not is_generated(s)]
            repaired["generated"] += len(gen)
            check_warnings.append({"page": p["id"], "code": "source/generated-dropped",
                                   "detail": gen[:3]})
        if 0 < len(p["source_files"]) < 4:
            before = len(p["source_files"])
            backfill_from_scope(p, 4 - before)
            repaired["backfilled"] += len(p["source_files"]) - before
            # 实例化期的 thin 标记在回填到 4 后过期
            if len(p["source_files"]) >= 4 and p.get("evidence_thin") \
                    and not p.get("skeleton"):
                p["evidence_thin"] = False
        for s in p["source_files"]:
            backfill_claims[s] += 1
        if 0 < len(p["source_files"]) < 4:
            p["evidence_thin"] = True
            check_warnings.append({"page": p["id"], "code": "source/thin",
                                   "detail": str(len(p["source_files"])) +
                                   " sources — needs review or merge"})
        pack_root = not p.get("parent") and str(p["id"]).startswith("pack-")
        if not pack_root and not p.get("cross_cutting") \
                and p["kind"] not in ("overview", "feature"):
            hit = scope_hit(p)
            if hit < 0.6 and len(p["source_files"]) >= 3:
                check_errors.append({"page": p["id"], "code": "scope/hit",
                                     "detail": "only " + f"{hit:.0%}" +
                                     " of sources fall inside the page's scope dirs"})

    # D13 终局: 回填可能重新聚合兄弟 —— 按父组再裁一遍
    _final_groups: dict = defaultdict(list)
    for p in builder.pages:
        if p.get("parent"):
            _final_groups[p["parent"]].append(p)
    for group in _final_groups.values():
        for c in group:
            primary_first(c)
        trim_sibling_overlap(group, builder.sibling_trims)

    # ---------- knowledge(卡片; sheet 由注册表展开, 见 to_plan_pages) ----------
    cards = _plan_cards(ctx, inv, corpus, files, profile_name)

    # ---------- 汇总 ----------
    theme_gaps = []
    for pack in packs:
        key = pack.get("key")
        if not key:
            continue
        if not any(p["id"] == "pack-" + key for p in builder.pages):
            theme_gaps.append({"key": key, "title": pack.get("title"),
                               "reason": "no parent page"})

    report = {
        "profile": profile_name,
        "pages": len(builder.pages),
        "packs": len(packs),
        "theme_gaps": builder.theme_gaps + theme_gaps,
        "theme_declined": builder.theme_declined,
        "sibling_trims": builder.sibling_trims,
        "title_collisions": builder.title_collisions,
        # theme_declined 只存顶层一份(r4 #61: 嵌套副本无消费方,
        # 两处真源必漂移)
        "check": {"errors": check_errors,
                  "warnings": check_warnings,
                  "repaired": repaired,
                  "strict": not check_warn_only,
                  "skeleton_scope_pages": skeleton_pages},
        "generated_at": now_iso(),
    }
    pages_v3 = to_plan_pages(builder, corpus)
    return {"pages": pages_v3, "cards": cards, "report": report,
            "check_failed": bool(check_errors) and not check_warn_only}


def to_plan_pages(builder: Builder, corpus: Corpus = None) -> list:
    """Builder 页面 → plan v3 pages(kind→type 映射, 附主题字段)。

    给 corpus 时, 页面的 `modules` 从其源文件所属模块推导 —— 这让覆盖硬规
    (每个注册模块被至少一篇覆盖)在主题路由下自动成立。ordinal 按 priority
    **降序**发号(overview 1000 最重要 → 0 号): 曾按升序, 概述拿到最大
    ordinal, 树视图/认领顺序/目录序全部倒挂(scaffold 路线概述是 0 号)。"""
    out = []
    ordinal_by_parent: dict = defaultdict(int)
    # 带子篇的页绝不落 LEAF_TYPES(deep_dive 等按设计是叶子)——
    # KIND_TO_TYPE 曾把 feature 父篇(功能指南, 天然带子页)映成 deep_dive,
    # 产出的 plan 过不了自家 validate 的"叶型不得有子篇", begin 直接被挡
    parent_ids = {p["parent"] for p in builder.pages if p.get("parent")}
    for p in sorted(builder.pages,
                    key=lambda x: (x["parent"] or "", -x["priority"])):
        ordinal = ordinal_by_parent[p["parent"] or ""]
        ordinal_by_parent[p["parent"] or ""] += 1
        mods = []
        if corpus is not None:
            for s in p.get("source_files") or []:
                mp = corpus.module_for(s)
                if mp and mp not in mods:
                    mods.append(mp)
        if p.get("module_path") and p["module_path"] not in mods:
            mods.insert(0, p["module_path"])
        atype = KIND_TO_TYPE.get(p["kind"], "domain")
        if p["id"] in parent_ids and atype in config.LEAF_TYPES:
            atype = "domain"
        entry = {
            "id": p["id"], "title": p["title"], "slug": p["slug"],
            "parent": p["parent"] or "", "ordinal": ordinal,
            "kind": "page", "type": atype,
            "objective": "", "modules": mods, "module": mods[0] if mods else "",
            "prompt": p["prompt"], "source_files": p["source_files"],
            "scope": p["scope"], "required_sections": p["required_sections"],
            "pack_key": p.get("pack_key") or "",
            "title_check": bool(p.get("title_check")),
            "related_pages": p.get("related_pages") or [],
        }
        if p.get("evidence_thin"):
            entry["evidence_thin"] = True
        if p.get("cross_cutting"):
            entry["cross_cutting"] = True
        if p.get("skeleton_scope"):
            entry["skeleton_scope"] = True
        out.append(entry)
    return out


def _plan_cards(ctx: Ctx, inv: dict, corpus: Corpus, files: list,
                profile_name: str) -> list:
    """知识卡规划(repowiki 的证据驱动版, 取代 planner.seed_cards 的主题路由)。"""
    from . import i18n
    cards = []
    all_text = [f for f in files if f.get("lang") in cz.TEXT_LANGS]
    deps = collect_dependencies(inv)

    card_ids: set = set()
    card_titles: set = set()

    def add_card(kind, name, slug, source_files, prompt):
        srcs = []
        for p in source_files:
            if p in corpus.by_path and p not in srcs:
                srcs.append(p)
        if not srcs:
            return
        # P18: id/标题唯一性守卫(planner.seed_cards 同规) —— slugify 对
        # '@azure/storage' 与 'azure-storage' 同值, DEP_DISPLAY 又会把
        # 'tauri' 与 '@tauri-apps/api' 都映射到 'Tauri': 重复 id 阻塞
        # begin(validate 的重复检查), 撞标题则 _die_on_path_collision
        # 打死 plan --themes
        cid = "card-" + slug
        n = 2
        while cid in card_ids:
            cid = f"card-{slug}-{n}"
            n += 1
        card_ids.add(cid)
        title = str(name)
        n = 2
        while title in card_titles:
            title = f"{name}({n})"
            n += 1
        card_titles.add(title)
        cards.append({"id": cid, "kind": kind, "title": title,
                      "scope": ["**"], "source_files": srcs[:8], "prompt": prompt})

    def dep_score(name):
        low = name.lower()
        score = DEP_SCORES.get(low, 0)
        if low.startswith("@types/"):
            score -= 50
        if low in ("typescript", "eslint", "prettier", "rimraf", "nodemon",
                   "ts-node", "tsx"):
            score -= 30
        return score + min(deps[name], 5)

    ranked_deps = sorted(deps, key=lambda d: (-dep_score(d), d))
    dep_limit = {"lite": 5, "standard": 8, "full": 10}[profile_name]
    for dep in ranked_deps:
        if len([c for c in cards if c["kind"] == "external_dependency"]) >= dep_limit:
            break
        if dep_score(dep) <= 0:
            continue
        token = dep.split("/")[-1].lower()
        hits = corpus.grep(all_text, [token], limit=20)
        # 门: 普通 npm/pip 依赖只有塑造架构才配卡 —— 白名单基建, 或有专属
        # provider/adapter/platform 包装模块(而不是到处被 import)
        stem = re.split(r"[-@]", token)[0] if token else token
        provider_overlap = any(stem and (stem in pv or pv in stem)
                               for pv in PROVIDER_TITLES)
        wrapper_hits = [
            f for f in hits
            if stem and stem in os.path.basename(f["path"]).lower()
            and os.path.basename(f["path"]).lower().endswith(
                (".ts", ".tsx", ".js", ".py", ".go", ".rs", ".java", ".kt"))
            and not any(bad in os.path.basename(f["path"]).lower()
                        for bad in ("config", ".d.ts", "test", "spec", "readme"))
        ]
        if provider_overlap or (dep.lower() not in ARCH_SHAPING_DEPS
                                and not wrapper_hits):
            continue
        src = [f["path"] for f in hits]
        src += [m["path"] for m in inv.get("manifests", [])
                if isinstance(m.get("dependencies"), dict)
                and dep in m["dependencies"]][:1]
        if not src:
            continue
        display = DEP_DISPLAY.get(dep.lower(), dep)
        add_card("external_dependency", display, slugify(dep), src,
                 "为外部依赖「" + display + "」创建知识卡：身份与角色、集成点、"
                 "行为约束、版本要求、失败模式，以及仓库为适配它所做的封装。")

    for name, display in PROVIDER_TITLES.items():
        hits = provider_hits(files, name)
        if not hits:
            continue
        if len([c for c in cards if c["title"] == display]) > 0:
            continue
        add_card("external_dependency", display, slugify(name),
                 [f["path"] for f in hits[:6]],
                 "为「" + display + "」创建知识卡：身份与角色、集成点、认证/协议、"
                 "行为约束与失败模式。")

    def card_sources(patterns, exclude, needles, limit=8):
        matches = corpus.match(patterns, list(cz.DEFAULT_EXCLUDE) + exclude)
        if len(matches) < 3 and needles:
            matches += corpus.grep(all_text, needles, limit=60)
        return [f["path"] for f in dedupe_files(matches)[:limit]]

    config_src = card_sources(
        ["/config/", "/configs/", "/settings/", ".env", "application.yml",
         "application.yaml", "application.properties", "tauri.conf"],
        ["test", "spec", "mock"],
        ["process.env", "os.environ", "configurationproperties", "config.get("])
    if config_src:
        add_card("configuration_system", "配置体系", "configuration-system",
                 config_src, "创建配置体系知识卡：各语言/进程如何加载配置、优先级、"
                 "关键文件、必填项与 fail-closed 规则、密钥处理。")

    dep_src = card_sources(
        ["pnpm-workspace", "package.json", "pyproject.toml", "uv.lock",
         "pnpm-lock", "package-lock", "go.work", "go.mod", "go.sum", "cargo.toml",
         "cargo.lock", "settings.gradle", "pom.xml", "gradle.properties", ".npmrc"],
        ["node_modules"], [], 8)
    if dep_src:
        add_card("dependency_management", "依赖管理", "dependency-management",
                 dep_src, "创建依赖管理知识卡：工作区结构、包管理器、锁文件、"
                 "版本策略、各语言规则、升级与门禁。")

    log_src = card_sources(
        ["/log/", "/logging/", "/logger/", "/telemetry/", "/observability/",
         "logger", "logging"],
        ["test", "spec", "mock", "node_modules"],
        ["pino", "winston", "log4j", "slf4j", "tracing::", "log.info", "console.log"])
    if log_src:
        add_card("logging_system", "日志体系", "logging-system", log_src,
                 "创建日志体系知识卡：框架、输出目标、字段与级别、跨服务追踪、已知缺口。")

    err_src = card_sources(
        ["/error/", "/errors/", "/exception/", "/error-handling/"],
        ["test", "spec", "mock", "node_modules"],
        ["class ", "error", "setErrorHandler", "ErrorBoundary", "thiserror", "anyhow::"])
    if err_src:
        add_card("error_handling", "错误处理体系", "error-handling", err_src,
                 "创建错误处理知识卡：错误类型与错误码、信封格式、分层处理策略、"
                 "边界与缺口。")

    build_src = card_sources(
        ["Makefile", "justfile", "taskfile", "build.gradle", "pom.xml",
         "cargo.toml", "pyproject.toml", "package.json", ".github/workflows/",
         ".gitlab-ci", "jenkinsfile"],
        ["node_modules"], [], 8)
    if build_src:
        add_card("build_system", "构建与交付体系", "build-system", build_src,
                 "创建构建与交付知识卡：构建入口、各语言构建器映射、生成物与契约"
                 "管线、测试矩阵、镜像与版本交付。")

    style_src = card_sources(
        ["/styles/", "/theme/", "/themes/", "/tokens/", "*.css", "*.scss",
         "*.sass", "*.less", "tailwind", "postcss", "styled"],
        ["node_modules", "test", "spec", "dist", "build"],
        ["tailwindcss", "@theme", "styled.", "createtheme", "design tokens"])
    if style_src:
        add_card("frontend_style", "前端样式体系", "frontend-style", style_src,
                 "创建前端样式知识卡：设计令牌、组件样式组织、响应式布局、"
                 "主题与暗色模式、约定与约束。")

    docs_ev = [f for f in files if f.get("lang") in cz.DOC_LANGS
               or f["path"].lower().endswith((".md", ".mdx", ".rst"))]
    term_src = [f["path"] for f in docs_ev][:6]
    term_src += [f["path"] for f in files
                 if match_any(f["path"], ["/types/", "/models/", "/domain/"])][:4]
    add_card("business_term", "业务术语表", "business-terms", term_src,
             "从 README、产品文档、类型与领域模型中提取 5–15 个业务术语，"
             "逐条写定义与别名。只收产品/领域概念、服务编号、协议缩写，"
             "不收泛化编程词。")
    return cards


# --------------------------------------------------------------------------- #
# cmd 入口 + plan.md 评审门
# --------------------------------------------------------------------------- #

def _report_path(ctx: Ctx) -> str:
    return os.path.join(ctx.root, "work", "themes-report.json")


def cmd_plan_themes(ctx: Ctx, args, plan: dict) -> int:
    from . import modules as mods
    from .planner import assign_paths, save_plan

    inv = mods.load_inventory(ctx)
    if not inv:
        inv = mods._autoscan(ctx)
    decomposed = mods.decompose(inv)
    if decomposed["remaining"]:
        for d in decomposed["remaining"]:
            print(f"[coverage] ERROR orphan dir not represented: {d}",
                  file=__import__("sys").stderr)
        return config.EXIT_GATE
    # D21 框架模块 = plan aliases 的目标模块(yudao 规则数据化)
    framework_mods = {str(r.get("module")) for r in (plan.get("aliases") or [])}
    profile_name = ctx.config.get("profile", "standard")
    if profile_name not in PROFILES:
        # config 值不经过 argparse choices 校验 —— 曾在 run_themes 裸
        # KeyError: 'standrd' 打死整条 themes 路线, 而 validate/layers
        # 又静默按默认预算跑(口径不一致)
        from .util import die
        die(f"config.json profile {profile_name!r} 不在 {sorted(PROFILES)} — "
            "修 pwiki 根目录 config.json 的 profile 字段", config.EXIT_GATE)
    result = run_themes(ctx, inv, decomposed, profile_name, framework_mods,
                        check_warn_only=False,
                        style=str(plan.get("style") or ""))

    # 合并: 人工字段(repo_summary/limits/aliases/author_*/modules)保留;
    # c027 回修 —— 页/卡不再整体替换: 同 id 页保留手工调过的字段
    # (MANUAL_FIELDS, 与 --layers commit 同一集合: 曾只保 title/prompt,
    # 评审门让用户调"来源"而再生把手改的 source_files/scope/objective/
    # parent/ordinal 静默回退), 手加页/卡默认保留(引擎不再输出的 id 不静
    # 默删); `--reset` 才整体重建(PLAN_HEADER 承诺的逃生门, 此前在本路由
    # 是被静默忽略的 no-op)。
    # module_sheet 页保留(与 --layers commit 同口径): 重跑主题规划曾把它们
    # 整体丢掉, 下一次 begin 队列里没有 sheet, finish 还会把已发布的
    # sheet 文件当孤儿剪掉。
    from .planner import MANUAL_FIELDS as _MANUAL_FIELDS
    reset = bool(getattr(args, "reset", False))
    kept_sheets = [p for p in (plan.get("pages") or [])
                   if p.get("kind") == "module_sheet"]
    old_pages = plan.get("pages") or []
    new_ids = {str(p["id"]) for p in result["pages"]}
    if reset:
        dropped = [str(p.get("id")) for p in old_pages
                   if str(p.get("id")) not in new_ids
                   and str(p.get("id"))]
        plan["pages"] = result["pages"] + kept_sheets
    else:
        # 合并/保留人工编辑统一走 merge_manual(pages 与 cards 共用一份)
        merged_pages = merge_manual(
            result["pages"], old_pages, _MANUAL_FIELDS,
            keep=lambda p: p.get("kind") != "module_sheet")
        kept_manual_n = len(merged_pages) - len(result["pages"])
        plan["pages"] = merged_pages + kept_sheets
        dropped = []
    plan["cards"] = result["cards"] if reset else merge_manual(
        result["cards"], plan.get("cards") or [], _MANUAL_FIELDS)
    plan["modules"] = plan.get("modules") or mods.to_plan_modules(decomposed)
    plan["schema_version"] = 3
    assign_paths(plan, ctx.locale)
    save_plan(ctx, plan)

    # 注册表快照进 DB
    pid = ctx.partition()["id"]
    ctx.db.publish_modules(pid, plan.get("modules") or [])

    # 报告落盘(work/ 可再生缓存)
    from .util import atomic_write   # P56: 中断不留半份报告
    os.makedirs(os.path.dirname(_report_path(ctx)), exist_ok=True)
    atomic_write(_report_path(ctx),
                 json.dumps(result["report"], ensure_ascii=False, indent=2))

    r = result["report"]
    print(f"pages: {r['pages']}  cards: {len(result['cards'])}  "
          f"packs: {r['packs']}")
    if dropped:
        print(f"replaced pages: {len(dropped)} dropped from previous plan "
              f"(--reset 整体重建; 手工字段与注册表照旧保留)")
    elif not reset and kept_manual_n:
        print(f"kept {kept_manual_n} page(s) the engine no longer emits "
              f"(手加/手工页不静默删; 确认不要后用 `plan --themes --reset` 重建)")
    if r["theme_declined"]:
        print("theme_declined (D12): " + ", ".join(
            str(d["title"]) + "←" + str(d.get("reason", ""))[:40]
            for d in r["theme_declined"][:8]))
    if r["sibling_trims"]:
        print(f"sibling overlap trims (D13): {len(r['sibling_trims'])}")
    check = r["check"]
    print(f"check gate: {len(check['errors'])} errors, "
          f"{len(check['warnings'])} warnings, repaired: "
          f"{json.dumps(check['repaired'], ensure_ascii=False)}")
    render_plan_md(ctx, plan)
    print(f"review gate: {os.path.join(ctx.root, 'work', 'plan.md')} "
          f"(先给用户看再写正文)")
    if result["check_failed"]:
        for e in check["errors"]:
            print(f"  ERROR {e['code']} {e['page']}: {e['detail']}",
                  file=__import__("sys").stderr)
        return config.EXIT_GATE
    return config.EXIT_OK


def render_plan_md(ctx: Ctx, plan: dict) -> str:
    """再生 work/plan.md —— repowiki 的人工评审门。"""
    from .planner import plan_cards, plan_pages
    report = {}
    if os.path.isfile(_report_path(ctx)):
        try:
            with open(_report_path(ctx), encoding="utf-8") as fh:
                report = json.load(fh)
        except (OSError, ValueError):
            report = {}
    pages = plan_pages(plan)
    cards = plan_cards(plan)
    children: dict = defaultdict(list)
    for p in pages:
        children[p.get("parent") or ""].append(p)
    lines = [f"# pwiki Plan — {plan.get('title') or 'Repo Wiki'}", "",
             f"- profile: `{ctx.config.get('profile', 'standard')}`  "
             f"style: `{plan.get('style', config.DEFAULT_STYLE)}`",
             f"- pages: **{len(pages)}**  cards: **{len(cards)}**  "
             f"modules: **{len(plan.get('modules') or [])}**", ""]
    if report:
        lines.append(f"- themes: {report.get('packs', '?')} packs  "
                     f"(declined: {len(report.get('theme_declined', []))}, "
                     f"trims: {len(report.get('sibling_trims', []))})")
        lines.append("")
    lines += ["## Page tree", ""]

    def emit(parent_id, depth=0):
        # P22: ordinal 经 as_int 容错 —— 手编 wiki_plan.yaml 的显式空
        # `ordinal:` 解析为 None, .get 默认值不生效, sorted 裸 TypeError
        from .util import as_int
        for p in sorted(children.get(parent_id, []),
                        key=lambda x: (as_int(x.get("ordinal"), 0),
                                       str(x.get("id") or ""))):
            flags = []
            if p.get("pack_key"):
                flags.append(p["pack_key"])
            if p.get("evidence_thin"):
                flags.append("thin")
            if p.get("title_check"):
                flags.append("title_check")
            if p.get("skeleton_scope"):
                flags.append("skeleton")
            fl = ("  _" + ",".join(flags) + "_") if flags else ""
            lines.append("  " * depth + "- " + p.get("title", "?") +
                         " · " + str(p.get("id", "")) +
                         "  (" + str(len(p.get("source_files") or [])) +
                         " files)" + fl)
            emit(p.get("id", ""), depth + 1)

    emit("")
    lines += ["", "## Cards", ""]
    for c in cards:
        lines.append("- [" + str(c.get("kind")) + "] " + str(c.get("title")) +
                     "  (" + str(len(c.get("source_files") or [])) + " files)")
    if report.get("theme_gaps"):
        lines += ["", "## Theme gap", ""]
        for g in report["theme_gaps"]:
            lines.append("- " + str(g.get("title")) + " (`" +
                         str(g.get("key")) + "`): " + str(g.get("reason")))
    if report.get("theme_declined"):
        lines += ["", "## theme_declined (D12 evidence gate)", "",
                  "这些主题子项没有实例化 —— 仓库缺少强证据(纯文档不算)。"
                  "确实需要的主题请手工把子项加进 plan, 附真实来源:", ""]
        for d in report["theme_declined"]:
            lines.append("- " + str(d.get("title")) + " (`" +
                         str(d.get("pack")) + "/" + str(d.get("child")) +
                         "`): " + str(d.get("reason")))
    if report.get("sibling_trims"):
        lines += ["", "## Sibling overlap trims (D13)", ""]
        for t in report["sibling_trims"]:
            lines.append("- `" + str(t.get("page")) + "` " + str(t.get("detail")))
    check = report.get("check") or {}
    thin_pages = [p for p in pages if p.get("evidence_thin")]
    if check.get("errors") or check.get("warnings") or thin_pages:
        lines += ["", "## Check gate", ""]
        if check.get("repaired"):
            lines.append("- auto-repaired: " +
                         json.dumps(check["repaired"], ensure_ascii=False))
        for e in check.get("errors", []):
            lines.append("- **ERROR** `" + e["code"] + "` " + str(e["page"]) +
                         ": " + str(e["detail"]))
        for w in check.get("warnings", []):
            lines.append("- warn `" + str(w.get("code")) + "` " +
                         str(w.get("page")) + ": " + str(w.get("detail")))
        if thin_pages:
            lines.append("- evidence_thin pages: " +
                         ", ".join(str(p.get("id")) for p in thin_pages))
    if check.get("skeleton_scope_pages"):
        # P21: 曾 `lines +=("句子")` —— list += str 按字符扩展, plan.md 在
        # 此发出数十个单字符行
        lines += ["", "## Skeleton-scope pages (声明 vs 现状)", "",
                  "这些页面覆盖占位目录; 其 prompt 要求 README 声明标注为"
                  "「声明」, 不得写成已交付行为:", ""]
        lines += ["- " + str(p) for p in check["skeleton_scope_pages"]]
    lines += ["", "## Next", "",
              "评审本文件 → 调整 `wiki_plan.yaml`(标题/合并/来源) → `pwiki validate`"
              " → `pwiki digest` → `pwiki begin`。"]
    from .util import atomic_write   # P56: 中断不留半份评审门文档
    out = os.path.join(ctx.root, "work", "plan.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    atomic_write(out, "\n".join(lines) + "\n")
    return out
