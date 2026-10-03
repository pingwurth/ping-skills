"""rules 规则引擎单测: 规则 c(try/catch)、规则 d(git 跳过)、注册表可扩展、渲染。"""

from __future__ import annotations

from unittest.mock import patch

from jaut import javasrc, rules


def _ctx(source: str, project_root=None, baseline=None, main_files=None) -> rules.RuleContext:
    return rules.RuleContext(
        stripped_source=javasrc.strip_comments_and_strings(source),
        project_root=project_root,
        git_baseline=baseline or {},
        git_main_source_files=main_files or set(),
    )


def test_rule_c_catch_is_violation():
    src = "void t(){ try { f(); } catch (Exception e) { } }"
    v = rules.NoTryCatchRule().check(_ctx(src))
    ids = [x.rule_id for x in v]
    assert "c" in ids
    assert any("catch" in x.message for x in v)


def test_rule_c_bare_try_is_violation():
    src = "void t(){ try { f(); } finally { g(); } }"
    v = rules.NoTryCatchRule().check(_ctx(src))
    assert len(v) == 1 and "裸 try" in v[0].message


def test_rule_c_try_with_resources_no_catch_passes():
    src = "void t(){ try (Stream s = open()) { use(s); } }"
    assert rules.NoTryCatchRule().check(_ctx(src)) == []


def test_rule_c_try_with_resources_with_catch_flags_catch_only():
    src = "void t(){ try (Stream s = open()) { use(s); } catch (IOException e) { } }"
    v = rules.NoTryCatchRule().check(_ctx(src))
    assert len(v) == 1 and "catch" in v[0].message   # 资源括号放行, 仅 catch 违规


def test_rule_c_ignores_try_inside_string_and_comment():
    src = 'void t(){ String s = "try { } catch"; // try catch\n }'
    assert rules.NoTryCatchRule().check(_ctx(src)) == []


def test_rule_d_skipped_without_project_root():
    assert rules.NoMainSourceEditRule().check(_ctx("x", project_root=None)) == []


def test_run_hard_rules_registry_extensible():
    class AlwaysRule:
        id = "z"

        def check(self, ctx):
            return [rules.Violation(rule_id="z", line=1, message="always")]

    v = rules.run_hard_rules(_ctx("void t(){}"), rules=[AlwaysRule()])
    assert len(v) == 1 and v[0].rule_id == "z"


def test_violation_render():
    assert rules.Violation("c", 12, "bad").render() == "规则 c [行 12]: bad"
    assert rules.Violation("d", 0, "bad", path="p").render() == "规则 d: bad"


def test_default_registry_contains_c_and_d():
    ids = {r.id for r in rules.HARD_RULES}
    assert ids == {"c", "d"}


# --------------------------------------------------------------------------- #
# 规则 d: 违规提示与删除检测快照/基线(M1 / L5 / P0-3 回归)
# --------------------------------------------------------------------------- #
def test_rule_d_changed_file_message_has_no_acknowledge_flag():
    """基线外变更的违规提示不得再引用幽灵参数 --acknowledge(M1)。"""
    with patch("jaut.rules.git_changed_paths", return_value=["src/main/java/Foo.java"]), \
         patch("jaut.rules.git_deleted_main_source_paths", return_value=[]):
        v = rules.NoMainSourceEditRule().check(_ctx("x", project_root="/proj", baseline={}))
    assert len(v) == 1
    assert "--acknowledge" not in v[0].message
    assert "请还原" in v[0].message


def test_rule_d_ignores_deletion_not_in_baseline():
    """无快照信息(旧 state 且不在基线)的删除不应归咎于本次测试生成流程(L5)。"""
    with patch("jaut.rules.git_changed_paths", return_value=[]), \
         patch("jaut.rules.git_deleted_main_source_paths",
               return_value=["src/main/java/Old.java"]):
        v = rules.NoMainSourceEditRule().check(_ctx("x", project_root="/proj", baseline={}))
    assert v == []


def test_rule_d_flags_deletion_in_baseline():
    """基线中的文件被删除应违规(旧 state 无快照字段的回退路径)。"""
    with patch("jaut.rules.git_changed_paths", return_value=[]), \
         patch("jaut.rules.git_deleted_main_source_paths",
               return_value=["src/main/java/Old.java"]):
        v = rules.NoMainSourceEditRule().check(
            _ctx("x", project_root="/proj",
                 baseline={"src/main/java/Old.java": "deadbeef"}))
    assert len(v) == 1
    assert "删除" in v[0].message
    assert v[0].path == "src/main/java/Old.java"


def test_rule_d_flags_deletion_of_file_existing_at_init():
    """init 时已存在的未修改文件被删除应违规(干净 worktree 基线为空也不失效)。"""
    with patch("jaut.rules.git_changed_paths", return_value=[]), \
         patch("jaut.rules.git_deleted_main_source_paths",
               return_value=["src/main/java/Pristine.java"]):
        v = rules.NoMainSourceEditRule().check(
            _ctx("x", project_root="/proj",
                 main_files={"src/main/java/Pristine.java"}))
    assert len(v) == 1
    assert "删除" in v[0].message
    assert v[0].path == "src/main/java/Pristine.java"


def test_rule_d_ignores_deletion_absent_from_init_snapshot():
    """快照外的删除(init 前已删除)不应归咎于本次测试生成流程。"""
    with patch("jaut.rules.git_changed_paths", return_value=[]), \
         patch("jaut.rules.git_deleted_main_source_paths",
               return_value=["src/main/java/Gone.java"]):
        v = rules.NoMainSourceEditRule().check(
            _ctx("x", project_root="/proj",
                 main_files={"src/main/java/Kept.java"}))
    assert v == []
