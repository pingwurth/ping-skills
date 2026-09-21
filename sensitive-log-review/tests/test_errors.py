"""common.errors 统一错误码与结构化提示单测。"""

from __future__ import annotations

import pytest

from common.errors import ErrorCode, format_error, print_error


class TestFormatError:
    def test_contains_code_location_detail_and_steps(self):
        msg = format_error(ErrorCode.E001_GIT_NOT_REPO,
                           location="C:/repo", detail="not a git repository")
        assert "错误[E001]" in msg
        assert "位置: C:/repo" in msg
        assert "详情: not a git repository" in msg
        assert "修复建议:" in msg
        # 分步建议按序号列出
        assert "    1. " in msg
        assert "    2. " in msg

    def test_optional_parts_omitted(self):
        msg = format_error(ErrorCode.E002_JAVA_NOT_FOUND)
        assert "错误[E002]" in msg
        assert "位置:" not in msg
        assert "详情:" not in msg
        assert "修复建议:" in msg

    @pytest.mark.parametrize("code,expected", [
        (ErrorCode.E001_GIT_NOT_REPO, "E001"),
        (ErrorCode.E002_JAVA_NOT_FOUND, "E002"),
        (ErrorCode.E003_BRANCH_INVALID, "E003"),
        (ErrorCode.E004_RULE_FILE_BROKEN, "E004"),
        (ErrorCode.E005_RUN_ID_INVALID, "E005"),
        (ErrorCode.E006_OUTPUT_DIR_UNWRITABLE, "E006"),
        (ErrorCode.E007_JAR_CHECKSUM_MISMATCH, "E007"),
    ])
    def test_all_codes_have_catalog_entries(self, code, expected):
        msg = format_error(code)
        assert f"错误[{expected}]" in msg
        assert "修复建议:" in msg

    def test_rule_broken_mentions_json_tool(self):
        msg = format_error(ErrorCode.E004_RULE_FILE_BROKEN,
                           location="rules/x.json", detail="Expecting ',' (行 3 列 5)")
        assert "python -m json.tool" in msg
        assert "rules/x.json" in msg


class TestPrintError:
    def test_writes_to_stderr(self, capsys):
        print_error(ErrorCode.E003_BRANCH_INVALID, location="-bad", detail="非法分支")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "错误[E003]" in captured.err
        assert "-bad" in captured.err
