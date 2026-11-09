"""模板一致性：references/doc-templates.md 的 H2 与 dd.py MANIFEST_DEFAULTS 同文，防漂移。"""
from __future__ import annotations

from pathlib import Path

import dd as ddmod

TEMPLATES = Path(__file__).resolve().parent.parent / "references" / "doc-templates.md"


def test_all_manifest_titles_present_in_template():
    text = TEMPLATES.read_text(encoding="utf-8")
    missing = [f"## {title}" for _, _, title, _ in ddmod.MANIFEST_DEFAULTS
               if f"## {title}" not in text]
    assert not missing, f"模板缺少章节标题（与 dd.py MANIFEST_DEFAULTS 漂移）：{missing}"


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
