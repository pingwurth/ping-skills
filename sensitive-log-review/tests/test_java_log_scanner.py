"""java_log_scanner 日志扫描单测（fixture 驱动，tmp_path 构造样例 Java 文件）。"""

from __future__ import annotations

import textwrap

import java_log_scanner as scanner
from common.dictionary import SensitiveDicts


def _dicts() -> SensitiveDicts:
    return SensitiveDicts(
        core={"password", "idcard"},
        extended={"phone", "email"},
        blacklist={"pwd"},
        whitelist={"cardtype"},
    )


def _write(tmp_path, source: str, name: str = "Demo.java"):
    f = tmp_path / name
    f.write_text(textwrap.dedent(source), encoding="utf-8")
    return str(f)


def _scan(tmp_path, source: str, logger_names: list[str] | None = None):
    pattern = scanner.build_log_call_pattern(logger_names or ["log", "LOGGER"])
    return scanner.scan_java_file(_write(tmp_path, source), _dicts(), pattern)


class TestLoggerNames:
    def test_configured_logger_hit(self, tmp_path):
        violations, ok = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("user pwd: {}", password);
                }
            }
        """)
        assert len(violations) == 1
        assert "SENSITIVE:password" in violations[0]
        assert ok == []

    def test_unconfigured_logger_not_matched(self, tmp_path):
        # myTracer 不在 logger_names 中，整行不视为日志调用
        violations, ok = _scan(tmp_path, """
            public class A {
                void m() {
                    myTracer.info("pwd: {}", password);
                }
            }
        """)
        assert violations == []
        assert ok == []

    def test_custom_logger_names_take_effect(self, tmp_path):
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    LogUtil.warn("login: {}", loginPassword);
                }
            }
        """, logger_names=["LogUtil"])
        assert len(violations) == 1
        assert "SENSITIVE:password" in violations[0]

    def test_clean_log_goes_to_ok_list(self, tmp_path):
        violations, ok = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("order finished: {}", orderStatus);
                }
            }
        """)
        assert violations == []
        assert len(ok) == 1


class TestJsonConversion:
    def test_json_tojsonstring_detected(self, tmp_path):
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("user: {}", JSON.toJSONString(user));
                }
            }
        """)
        assert len(violations) == 1
        assert "JSON_LOG" in violations[0]

    def test_jackson_writevalueasstring_detected(self, tmp_path):
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    log.debug("payload={}", objectMapper.writeValueAsString(req));
                }
            }
        """)
        assert len(violations) == 1
        assert "JSON_LOG" in violations[0]


class TestConfidenceLevels:
    def test_core_hit_tagged_sensitive(self, tmp_path):
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("login: {}", userPassword);
                }
            }
        """)
        assert "[SENSITIVE:password]" in violations[0]

    def test_extended_hit_tagged_low(self, tmp_path):
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("contact: {}", userPhone);
                }
            }
        """)
        assert len(violations) == 1
        assert "[LOW:phone]" in violations[0]

    def test_core_wins_over_extended(self, tmp_path):
        # 同一行同时出现高/低置信标识符：仅报高置信
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("x: {} {}", userPhone, password);
                }
            }
        """)
        assert len(violations) == 1
        assert "SENSITIVE:password" in violations[0]
        assert "LOW:" not in violations[0]

    def test_whitelist_identifier_exempted(self, tmp_path):
        violations, ok = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("card type: {}", cardType);
                }
            }
        """)
        assert violations == []
        assert len(ok) == 1

    def test_word_in_string_literal_not_flagged(self, tmp_path):
        # 字符串常量中的敏感词不算标识符命中
        violations, ok = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("password rule updated");
                }
            }
        """)
        assert violations == []
        assert len(ok) == 1


class TestMultiLineLogCall:
    def test_multiline_call_detected_with_first_line_number(self, tmp_path):
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("user login, name={}, pwd={}",
                            userName,
                            password);
                }
            }
        """)
        assert len(violations) == 1
        # 违规定位到调用起始行（dedent 后 log.info 在第 4 行）
        assert "#4 " in violations[0]
        assert "SENSITIVE:password" in violations[0]

    def test_comment_lines_inside_call_skipped(self, tmp_path):
        violations, _ = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("x={}",
                            // 注释行不参与拼接
                            password);
                }
            }
        """)
        assert len(violations) == 1
        assert "SENSITIVE:password" in violations[0]


class TestCharLiteralParens:
    """F03: 括号闭合跟踪需正确跳过单引号字符字面量（含转义）。"""

    def test_paren_in_char_literal_is_closed(self):
        # 旧版只跟踪双引号，'(' / ')' 字符字面量打坏括号深度
        assert scanner.is_parentheses_closed("log.info('(', x);") is True
        assert scanner.is_parentheses_closed("log.info(')', x);") is True
        assert scanner.is_parentheses_closed("if (c == '(') { m(); }") is True

    def test_escaped_char_literals(self):
        # '\'' 与 '"' 中的引号是转义内容，不得翻转字面量状态
        assert scanner.is_parentheses_closed(r"log.info('\'', x);") is True
        assert scanner.is_parentheses_closed("""log.info('"', x);""") is True
        assert scanner.is_parentheses_closed(r"if (s == '\\') { m(); }") is True

    def test_quote_inside_string_literal_ignored(self):
        # 双引号字符串里的单引号（如 "it's"）不进入字符字面量状态
        assert scanner.is_parentheses_closed('log.info("it\'s ok: {}", x);') is True

    def test_unclosed_call_still_detected(self):
        assert scanner.is_parentheses_closed("log.info(\"x={},\",") is False

    def test_four_calls_after_char_literal_all_scanned(self, tmp_path):
        """F03 复现（failure_scenario）：字符字面量打断后的后续日志语句不被吞。"""
        violations, ok = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info("start");
                    log.info('(', x);
                    log.info("after one");
                    log.info("pwd: {}", password);
                }
            }
        """)
        # 旧版：第 2 行括号深度打坏 → 多行拼接吞掉第 3/4 行 → 只扫出 2 条
        total = len(violations) + len(ok)
        assert total == 4, (violations, ok)
        # 被吞的敏感日志必须进入违规清单（旧版漏报 0 条）
        assert len(violations) == 1
        assert "SENSITIVE:password" in violations[0]
        assert "#7 " in violations[0]  # 定位到第 4 条日志所在行（首空行计入后第 7 行）

    def test_double_quote_in_char_literal_does_not_break_string_state(self, tmp_path):
        # '"' 字符字面量里的双引号不得被当成字符串边界（否则后续真字符串解析错乱）
        violations, ok = _scan(tmp_path, """
            public class A {
                void m() {
                    log.info('"', sep);
                    log.info("pwd: {}", password);
                }
            }
        """)
        assert len(violations) + len(ok) == 2
        assert len(violations) == 1
        assert "SENSITIVE:password" in violations[0]


class TestBrokenEncoding:
    def test_invalid_utf8_bytes_do_not_crash_and_still_detect(self, tmp_path):
        f = tmp_path / "Broken.java"
        source = (
            b"public class Broken {\n"
            b"    // \xff\xfe\x9c broken comment bytes\n"
            b"    void m() {\n"
            b"        String s = \"\xf0\x28\x8c\x28\";\n"
            b"        log.info(\"pwd: {}\", password);\n"
            b"    }\n"
            b"}\n"
        )
        f.write_bytes(source)
        pattern = scanner.build_log_call_pattern(["log"])
        violations, ok = scanner.scan_java_file(str(f), _dicts(), pattern)
        assert len(violations) == 1
        assert "SENSITIVE:password" in violations[0]
        assert ok == []


class TestMultiWordPhrase:
    """F35: 多词短语子集预计算；无空格词典时整段跳过逐行拷贝。"""

    def _phrase_dicts(self):
        return SensitiveDicts(
            core={"password", "bank account"},
            extended=set(),
            blacklist=set(),
            whitelist=set(),
        )

    def test_core_phrases_precomputed(self):
        dicts = self._phrase_dicts()
        assert set(dicts.core_phrases) == {"bank account"}
        # 缓存: 二次访问拿到同一对象
        assert dicts.core_phrases is dicts.core_phrases

    def test_no_space_dictionary_yields_empty_phrases(self):
        dicts = _dicts()
        assert dicts.core_phrases == ()

    def test_phrase_hit_tagged_sensitive(self):
        pattern = scanner.build_log_call_pattern(["log"])
        # 行内注释（非字符串）中的短语在剥离字符串常量后仍可命中
        tags = scanner.detect_violations(
            'log.info("a"); // bank account', self._phrase_dicts(), pattern)
        assert "SENSITIVE:bank account" in tags

    def test_phrase_in_string_literal_not_flagged(self):
        pattern = scanner.build_log_call_pattern(["log"])
        tags = scanner.detect_violations(
            'log.info("bank account");', self._phrase_dicts(), pattern)
        assert tags == []

    def test_empty_phrase_subset_skips_per_line_strip(self, monkeypatch):
        """F35: 词典无空格条目时不做行级 regex-sub/lower 拷贝。

        标识符检查仍需一次 sub（参数段），短语段被整段跳过 → 全程恰好 1 次；
        对照组（含空格短语）为 2 次。
        """
        class _CountingPattern:
            def __init__(self, inner):
                self._inner = inner
                self.calls = 0

            def sub(self, *args, **kwargs):
                self.calls += 1
                return self._inner.sub(*args, **kwargs)

        counting = _CountingPattern(scanner.STRING_LITERAL_PATTERN)
        monkeypatch.setattr(scanner, "STRING_LITERAL_PATTERN", counting)
        pattern = scanner.build_log_call_pattern(["log"])
        line = 'log.info("x: {}", password);'

        scanner.detect_violations(line, _dicts(), pattern)
        assert counting.calls == 1  # 仅参数段标识符检查

        counting.calls = 0
        scanner.detect_violations(line, self._phrase_dicts(), pattern)
        assert counting.calls == 2  # 参数段 + 短语检查的行级拷贝
