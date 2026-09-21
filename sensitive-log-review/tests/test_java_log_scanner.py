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
