"""config 常量与模式参数的契约测试。"""

from __future__ import annotations

from crp import config


def test_协议标记与退出码():
    assert config.NEXT_STEP_BEGIN_MARKER == ":::NEXT_STEP_BEGIN:::"
    assert config.NEXT_STEP_END_MARKER == ":::NEXT_STEP_END:::"
    assert (config.EXIT_OK, config.EXIT_CONTINUE, config.EXIT_ERROR, config.EXIT_STATE) == (0, 1, 2, 3)
    # do_work 替代 jaut 的 write_code
    assert "do_work" in config.NEXT_STEP_TYPES
    assert "write_code" not in config.NEXT_STEP_TYPES


def test_模式参数表():
    p = config.mode_params("quick")
    assert p["keep"] == config.KEEP_NONE and p["cap"] == 4 and p["max_rounds"] == 1
    p = config.mode_params("standard")
    assert p["keep"] == config.KEEP_CONFIRMED_P01 and p["cap"] == 10
    p = config.mode_params("deep")
    assert p["keep"] == config.KEEP_CONFIRMED_PLAUSIBLE and p["cap"] == 20


def test_轮次上限环境覆盖并钳制(monkeypatch):
    monkeypatch.setenv(config.CRP_MAX_ROUNDS_ENV, "9")
    assert config.mode_params("quick")["max_rounds"] == config.MAX_ROUNDS_MAX
    monkeypatch.setenv(config.CRP_MAX_ROUNDS_ENV, "0")
    assert config.mode_params("deep")["max_rounds"] == config.MAX_ROUNDS_MIN
    monkeypatch.setenv(config.CRP_MAX_ROUNDS_ENV, "abc")  # 非法回退默认
    assert config.mode_params("deep")["max_rounds"] == 4
    monkeypatch.delenv(config.CRP_MAX_ROUNDS_ENV, raising=False)
    assert config.mode_params("quick")["max_rounds"] == 1


def test_角度集():
    assert set(config.QUICK_ANGLE_KEYS) <= set(config.FULL_ANGLE_KEYS)
    assert "T" in config.FULL_ANGLE_KEYS and config.GAP_ANGLE_KEY not in config.FULL_ANGLE_KEYS
    # 排序类别: correctness < security < cleanup < altitude/conventions
    assert config.CATEGORY_RANK["correctness"] < config.CATEGORY_RANK["security"]
    assert config.CATEGORY_RANK["security"] < config.CATEGORY_RANK["cleanup"]
    assert config.CATEGORY_RANK["altitude"] == config.CATEGORY_RANK["conventions"]


def test_载体预算随宽度扩展():
    assert config.default_max_agents(1) == 45
    assert config.default_max_agents(12) == 96
    assert config.default_max_agents(13) == 104
