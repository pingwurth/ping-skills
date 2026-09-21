"""analyze_sensitive_fields 深度敏感性分析单测。"""

from __future__ import annotations

import json
import textwrap

import pytest

import analyze_sensitive_fields as asf


def _rules_file(tmp_path, rules) -> "Path":
    f = tmp_path / "rules.json"
    f.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return f


def _java_file(tmp_path, source: str):
    f = tmp_path / "UserDO.java"
    f.write_text(textwrap.dedent(source), encoding="utf-8")
    return f


RULES = [
    {
        "category": "身份鉴别信息",
        "level": "C3",
        "patterns": ["password", "pwd"],
        "reason": "身份鉴别信息 - C3",
    },
    {
        "category": "金融账户信息",
        "level": "C3/C2",
        "patterns": ["card.?no", "balance"],
        "reason": "金融账户信息 - C3/C2",
    },
]


class TestLoadRules:
    def test_valid_rules_compiled_with_ignorecase(self, tmp_path):
        rules = asf.load_rules(_rules_file(tmp_path, RULES))
        assert len(rules) == 2
        # 正则已预编译且忽略大小写
        assert rules[0]["_re"][0].search("LOGINPASSWORD")

    def test_missing_file_exits_2(self, tmp_path):
        with pytest.raises(SystemExit) as excinfo:
            asf.load_rules(tmp_path / "missing.json")
        assert excinfo.value.code == 2

    def test_broken_json_exits_2_with_e004(self, tmp_path, capsys):
        broken = tmp_path / "broken.json"
        broken.write_text('[{"category": "x", ]', encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            asf.load_rules(broken)
        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "错误[E004]" in err
        assert "行 " in err  # 提示 JSON 出错行列位置

    def test_invalid_regex_exits_2(self, tmp_path):
        bad = [{"category": "x", "patterns": ["("], "reason": "r"}]
        with pytest.raises(SystemExit) as excinfo:
            asf.load_rules(_rules_file(tmp_path, bad))
        assert excinfo.value.code == 2


class TestAnalyzeFile:
    def _rules(self, tmp_path):
        return asf.load_rules(_rules_file(tmp_path, RULES))

    def test_rule_hit_is_case_insensitive(self, tmp_path):
        f = _java_file(tmp_path, """
            public class UserDO {
                private String LOGINPASSWORD;
            }
        """)
        hits = asf.analyze_file(f, self._rules(tmp_path), set())
        assert len(hits) == 1
        assert "身份鉴别信息 - C3" in hits[0]
        assert "LOGINPASSWORD" in hits[0]
        assert "(line 3)" in hits[0]

    def test_first_matching_rule_wins(self, tmp_path):
        # pwdBalance 同时命中规则1(pwd)与规则2(balance)：首次命中即止
        f = _java_file(tmp_path, """
            public class UserDO {
                private String pwdBalance;
            }
        """)
        hits = asf.analyze_file(f, self._rules(tmp_path), set())
        assert len(hits) == 1
        assert "身份鉴别信息 - C3" in hits[0]
        assert "金融账户信息" not in hits[0]

    def test_whitelist_exempts_field(self, tmp_path):
        f = _java_file(tmp_path, """
            public class UserDO {
                private String cardNo;
            }
        """)
        # 无白名单时命中；白名单（小写整名）豁免
        assert len(asf.analyze_file(f, self._rules(tmp_path), set())) == 1
        assert asf.analyze_file(f, self._rules(tmp_path), {"cardno"}) == []

    def test_clean_file_no_hits(self, tmp_path):
        f = _java_file(tmp_path, """
            public class UserDO {
                private String nickname;
                private Integer sortOrder;
            }
        """)
        assert asf.analyze_file(f, self._rules(tmp_path), set()) == []
