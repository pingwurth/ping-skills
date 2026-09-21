"""config 配置常量和环境变量解析单测。

config 模块是全局单一事实源, 测试重点:
    1. mvn_timeout() 的环境变量覆盖 / 非法值回退逻辑
    2. 关键常量的类型与约束契约(退出码互异、预算为正、枚举元组非空)
"""

from __future__ import annotations

import os

from jaut import config


# --------------------------------------------------------------------------- #
# mvn_timeout()
# --------------------------------------------------------------------------- #
class TestMvnTimeout:
    """mvn_timeout 环境变量覆盖与容错。"""

    def test_default_when_env_not_set(self, monkeypatch):
        """未设置环境变量时返回默认值。"""
        monkeypatch.delenv(config.MVN_TIMEOUT_ENV, raising=False)
        assert config.mvn_timeout() == config._DEFAULT_MVN_TIMEOUT

    def test_env_override_valid(self, monkeypatch):
        """合法环境变量覆盖默认值。"""
        monkeypatch.setenv(config.MVN_TIMEOUT_ENV, "600")
        assert config.mvn_timeout() == 600

    def test_env_override_zero(self, monkeypatch):
        """零值视为合法(由调用方决定语义)。"""
        monkeypatch.setenv(config.MVN_TIMEOUT_ENV, "0")
        assert config.mvn_timeout() == 0

    def test_env_invalid_string_fallback(self, monkeypatch):
        """非数字字符串回退默认值。"""
        monkeypatch.setenv(config.MVN_TIMEOUT_ENV, "abc")
        assert config.mvn_timeout() == config._DEFAULT_MVN_TIMEOUT

    def test_env_empty_string_fallback(self, monkeypatch):
        """空字符串回退默认值(os.environ.get 返回 '' 时 or 分支命中)。"""
        monkeypatch.setenv(config.MVN_TIMEOUT_ENV, "")
        assert config.mvn_timeout() == config._DEFAULT_MVN_TIMEOUT

    def test_env_float_string_fallback(self, monkeypatch):
        """浮点数格式字符串回退默认值。"""
        monkeypatch.setenv(config.MVN_TIMEOUT_ENV, "1.5")
        assert config.mvn_timeout() == config._DEFAULT_MVN_TIMEOUT

    def test_env_negative_value(self, monkeypatch):
        """负数值可解析(由调用方决定语义, config 层不拦截)。"""
        monkeypatch.setenv(config.MVN_TIMEOUT_ENV, "-10")
        assert config.mvn_timeout() == -10


# --------------------------------------------------------------------------- #
# 常量契约
# --------------------------------------------------------------------------- #
class TestExitCodes:
    """退出码互异且在 0-3 范围内。"""

    def test_exit_codes_distinct(self):
        codes = {config.EXIT_OK, config.EXIT_CONTINUE, config.EXIT_ERROR, config.EXIT_STATE}
        assert len(codes) == 4

    def test_exit_ok_is_zero(self):
        assert config.EXIT_OK == 0

    def test_exit_codes_non_negative(self):
        for code in (config.EXIT_OK, config.EXIT_CONTINUE,
                     config.EXIT_ERROR, config.EXIT_STATE):
            assert code >= 0


class TestProtocolConstants:
    """协议相关常量契约。"""

    def test_protocol_version_is_string(self):
        assert isinstance(config.NEXT_STEP_PROTOCOL_VERSION, str)

    def test_begin_end_markers_distinct(self):
        assert config.NEXT_STEP_BEGIN_MARKER != config.NEXT_STEP_END_MARKER

    def test_statuses_non_empty(self):
        assert len(config.NEXT_STEP_STATUSES) >= 3

    def test_types_non_empty(self):
        assert len(config.NEXT_STEP_TYPES) >= 3


class TestBudgetConstants:
    """预算常量为正整数。"""

    def test_budget_values_positive(self):
        assert config.method_round_budget() > 0
        assert config.global_round_budget() > 0
        assert config.NO_IMPROVEMENT_ROUNDS > 0
        assert config.TEST_FAIL_STREAK_ROUNDS > 0
        assert config.FINAL_CHECK_FAIL_STREAK_LIMIT > 0
        assert config.VALIDATE_FAIL_STREAK_LIMIT > 0

    def test_global_budget_ge_method_budget(self):
        """全局预算应不小于单方法预算。"""
        assert config.global_round_budget() >= config.method_round_budget()


class TestPruneDirs:
    """剪枝目录集合为不可变 frozenset。"""

    def test_prune_dirs_is_frozenset(self):
        assert isinstance(config.PRUNE_DIRS, frozenset)

    def test_prune_dirs_contains_common_dirs(self):
        for d in (".git", "target", "node_modules"):
            assert d in config.PRUNE_DIRS


class TestWorktreeConstants:
    """worktree 相关常量。"""

    def test_container_suffix_differs_from_name_suffix(self):
        """容器后缀(复数)与单个 worktree 后缀(单数)不同。"""
        assert config.WORKTREE_CONTAINER_SUFFIX != config.WORKTREE_NAME_SUFFIX

    def test_disallowed_chars_contains_path_separators(self):
        assert "/" in config.WORKTREE_NAME_DISALLOWED_CHARS
        assert "\\" in config.WORKTREE_NAME_DISALLOWED_CHARS


class TestJvmTypes:
    """JVM 描述符映射完整性。"""

    def test_all_primitives_present(self):
        expected = {"B", "C", "D", "F", "I", "J", "S", "Z", "V"}
        assert set(config.JVM_PRIMITIVE_TYPES.keys()) == expected

    def test_void_mapping(self):
        assert config.JVM_PRIMITIVE_TYPES["V"] == "void"
