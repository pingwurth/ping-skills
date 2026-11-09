"""模板一致性：references/doc-templates.md 的 H2 与 dd.py MANIFEST_DEFAULTS 同文，防漂移。"""
from __future__ import annotations

import re
from pathlib import Path

import dd as ddmod

TEMPLATES = Path(__file__).resolve().parent.parent / "references" / "doc-templates.md"

_SKELETON_RE = re.compile(r"^````markdown\n(.*?)^````$\n", re.M | re.S)
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")


def _skeleton_h2s(text: str, h1_marker: str) -> list[str]:
    """提取指定骨架（按 H1 标志区分）围栏外的 H2 标题。

    骨架用四反引号围栏承载、内含 ```mermaid 等内层围栏——内层围栏里的
    行不算标题（与 dd.py 门禁同口径）。
    """
    h2s: list[str] = []
    for m in _SKELETON_RE.finditer(text):
        block = m.group(1)
        first = next((l for l in block.splitlines() if l.strip()), "")
        if h1_marker not in first:
            continue
        inside = False
        for line in block.splitlines():
            s = line.strip()
            if s.startswith("```"):
                inside = not inside
            elif not inside:
                hm = _H2_RE.match(line)
                if hm:
                    h2s.append(hm.group(1))
    return h2s


def test_template_skeletons_match_manifest_both_ways():
    """双向集合锁（F2）：骨架 H2 ↔ MANIFEST_DEFAULTS 一致。

    旧版单向子串锁（"## x" in text）被 H3 `### x` 满足——模板新增/重构
    章节为 H3 会静默漏出清单与门禁的约束。
    """
    text = TEMPLATES.read_text(encoding="utf-8")
    for doc_key, marker in (("analysis", "需求分析"), ("design", "详细设计")):
        skeleton = set(_skeleton_h2s(text, marker))
        manifest = {t for d, _s, t, _e in ddmod.MANIFEST_DEFAULTS if d == doc_key}
        assert skeleton == manifest, (
            f"{doc_key} 骨架与 MANIFEST_DEFAULTS 漂移："
            f"模板多出 {sorted(skeleton - manifest)} / 清单多出 {sorted(manifest - skeleton)}")


def test_manifest_titles_appear_as_real_h2_not_h3():
    """清单标题在模板中必须是真 H2（H3 不满足章节门禁的标题匹配）。"""
    text = TEMPLATES.read_text(encoding="utf-8")
    for _, _, title, _ in ddmod.MANIFEST_DEFAULTS:
        assert re.search(rf"^## {re.escape(title)}\s*$", text, re.M), \
            f"标题「{title}」未以 H2 形式出现在模板中"


def test_manifest_ids_unique_and_shapes_cover_all_sections():
    ids = [sid for _, sid, _, _ in ddmod.MANIFEST_DEFAULTS]
    assert len(ids) == len(set(ids))
    for shape, preset in ddmod.SHAPE_PRESETS.items():
        unknown = set(preset) - set(ids)
        assert not unknown, f"shape={shape} 预置引用未知章节：{unknown}"


def test_dimensions_enumeration_stable():
    """SKILL.md 命令速查中的 dimension 枚举与 dd.py 一致。"""
    skill_md = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(encoding="utf-8")
    for dim in ddmod.DIMENSIONS:
        assert f"`{dim}`" in skill_md, f"SKILL.md 缺 dimension `{dim}`"


def test_placeholder_words_absent_in_templates():
    """模板自身不含占位符禁词（避免 LLM 照抄进文档触发门禁）。

    例外 `……`：模板骨架刻意用省略号标记「落笔时必须替换」的位置——
    门禁同样禁 `……`（doc-templates.md 的承诺由此兑现），照抄会被 FAIL。
    """
    text = TEMPLATES.read_text(encoding="utf-8")
    hits = [p for p in ddmod.PLACEHOLDERS if p != "……" and p in text]
    assert not hits, f"模板含占位符禁词，会被照抄：{hits}"
