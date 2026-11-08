"""yamlite 往返与边界(qwiki bug1-3 回归 + v3 plan 形状)。"""

from __future__ import annotations

import pytest

from pwiki_lib import yamlite


def _rt(obj):
    """dump → load → 应完全等于原对象(引擎自己写的文件精确往返)。"""
    text = yamlite.yaml_dump(obj)
    back = yamlite.yaml_load(text)
    assert back == obj
    return text


def test_roundtrip_nested_maps_and_lists():
    _rt({"a": {"b": {"c": 1}}, "d": [1, 2, 3], "e": []})


def test_roundtrip_list_of_maps():
    _rt({"pages": [
        {"id": "a", "title": "总览", "parent": "", "ordinal": 0},
        {"id": "b", "title": "核心", "parent": "a", "ordinal": 1, "modules": ["/src"]},
    ]})


def test_roundtrip_quoting_hostile_strings():
    # 冒号/井号/引号/首尾空格/数字样/布尔样/null 样/换行/制表
    hostile = {
        "colon": "a: b",
        "hash": "value # comment",
        "quote": 'he said "hi"',
        "spaces": "  padded  ",
        "numeric": "12345",
        "floatish": "1.5e3",
        "boolish": "yes",
        "nullish": "~",
        "newline": "line1\nline2",
        "tab": "a\tb",
        "dash": "- leading dash",
        "empty": "",
    }
    _rt(hostile)


def test_roundtrip_cjk_and_unicode():
    _rt({"标题": "模块核心逻辑", "目标": "让新读者建立整体认识：系统做什么。", "emoji": "🚀"})


def test_multiline_via_quoted_scalar():
    """qwiki 发射器对多行串用双引号转义(\n)而非 | 块 —— 往返仍然精确。"""
    obj = {"hints": "line1\nline2\nline3"}
    dumped = _rt(obj)
    assert "\\n" in dumped


def test_block_scalar_accepted():
    """| 块标量可被解析(手写文件友好); 发射器不产出它。"""
    obj = yamlite.yaml_load("hints: |\n    line1\n    line2\n")
    assert obj == {"hints": "line1\nline2"}


def test_flow_list_roundtrip():
    _rt({"scope": ["src/**", "lib/**"], "depends_on": []})


def test_comment_stripping_ignores_hash_in_quotes():
    # qwiki bug2: 引号里的 # 不是注释
    text = 'key: "value # not a comment"  # real comment\n'
    obj = yamlite.yaml_load(text)
    assert obj["key"] == "value # not a comment"


def test_comment_stripping_escaped_quote():
    text = 'key: "say \\"hi\\""  # c\n'
    obj = yamlite.yaml_load(text)
    assert obj["key"] == 'say "hi"'


def test_numbers_and_bools_typed():
    obj = yamlite.yaml_load("count: 5\nratio: 1.5\nflag: true\nname: null\n")
    assert obj == {"count": 5, "ratio": 1.5, "flag": True, "name": None}


def test_full_v3_plan_roundtrip():
    """完整 v3 plan 形状往返(所有字段类型齐备)。"""
    plan = {
        "schema_version": 3,
        "locale": "zh",
        "style": "deep",
        "title": "Repo Wiki",
        "repo_summary": "一句话定位",
        "export_style": "legacy",
        "aliases": [{"patterns": ["yudao-spring-boot-starter-*"], "module": "framework"}],
        "limits": {"max_depth": 4, "max_total_articles": 135,
                   "max_top_articles": 20, "max_sub_per_parent": 8,
                   "min_roots": 6, "min_total_articles": 40},
        "modules": [{
            "id": "src", "title": "核心业务逻辑", "dir": "src/", "path": "src",
            "parent": "root", "scope": ["src/"], "depends_on": ["root"],
            "related_to": [], "notes": "一句话职责",
            "module_type": "real", "skeleton": False,
            "evidence": {"source": "src/main.py", "lines": 1200, "files": 30},
        }],
        "pages": [{
            "id": "overview", "title": "项目概览", "slug": "overview",
            "parent": "", "ordinal": 0, "kind": "page", "type": "overview",
            "objective": "项目定位与模块地图。Out of scope: 各领域细节。",
            "modules": ["/"], "module": "root", "sheet": "",
            "prompt": "写一篇总览…", "source_files": ["README.md"],
            "scope": [], "required_sections": [], "pack_key": "",
            "title_check": False, "related_pages": [],
        }],
        "cards": [{
            "id": "dep-lodash", "kind": "external_dependency", "title": "lodash",
            "scope": ["**"], "prompt": "…", "source_files": ["package.json"],
        }],
        "author_notes": [{"text": "把 payments/* 合并成支付域。", "authority": "binding"}],
        "author_pages": [],
    }
    _rt(plan)


def test_parse_error_raises_value_error():
    with pytest.raises(ValueError):
        yamlite.yaml_load("- a\nbogus line without colon\n")


def test_empty_input():
    assert yamlite.yaml_load("") == {}
    assert yamlite.yaml_load("# only a comment\n") == {}
