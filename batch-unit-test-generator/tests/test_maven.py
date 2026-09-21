"""maven 命令构建、pom 解析、模块探测、报告清理单测。"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jaut import config, maven
from jaut.models import State


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #
def _write_pom(path: Path, content: str) -> None:
    """写入 pom.xml 文件。"""
    path.mkdir(parents=True, exist_ok=True)
    (path / "pom.xml").write_text(content, encoding="utf-8")


def _single_module_pom(jacoco: bool = False) -> str:
    """单模块 pom, 可选 jacoco-maven-plugin。"""
    plugin = """
    <build>
      <plugins>
        <plugin>
          <groupId>org.jacoco</groupId>
          <artifactId>jacoco-maven-plugin</artifactId>
          <version>0.8.12</version>
        </plugin>
      </plugins>
    </build>
""" if jacoco else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>test-project</artifactId>
  <version>1.0-SNAPSHOT</version>
  {plugin}
</project>
"""


def _plugin_management_only_pom() -> str:
    """仅在 pluginManagement 中声明 jacoco-maven-plugin（未实际启用）。"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>parent</artifactId>
  <version>1.0-SNAPSHOT</version>
  <packaging>pom</packaging>
  <build>
    <pluginManagement>
      <plugins>
        <plugin>
          <groupId>org.jacoco</groupId>
          <artifactId>jacoco-maven-plugin</artifactId>
          <version>0.8.12</version>
        </plugin>
      </plugins>
    </pluginManagement>
  </build>
</project>
"""


def _plugin_management_with_build_plugins_pom() -> str:
    """pluginManagement 声明 + build/plugins 实际启用 jacoco。"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>test-project</artifactId>
  <version>1.0-SNAPSHOT</version>
  <build>
    <pluginManagement>
      <plugins>
        <plugin>
          <groupId>org.jacoco</groupId>
          <artifactId>jacoco-maven-plugin</artifactId>
          <version>0.8.12</version>
        </plugin>
      </plugins>
    </pluginManagement>
    <plugins>
      <plugin>
        <groupId>org.jacoco</groupId>
        <artifactId>jacoco-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
"""


def _multi_module_pom() -> str:
    """多模块 pom。"""
    return """<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>parent</artifactId>
  <version>1.0-SNAPSHOT</version>
  <packaging>pom</packaging>
  <modules>
    <module>module-a</module>
    <module>module-b</module>
  </modules>
</project>
"""


def _state_with_cache(result: bool, pom_mtimes: dict) -> State:
    """构造带 jacoco 配置缓存的 State。"""
    state = MagicMock(spec=State)
    state.jacoco_config_cache = {"result": result, "pom_mtimes": pom_mtimes}
    return state


# --------------------------------------------------------------------------- #
# _collect_pom_mtimes
# --------------------------------------------------------------------------- #
def test_collect_pom_mtimes_finds_all_poms(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    _write_pom(tmp_path / "sub", _single_module_pom())
    mtimes = maven._collect_pom_mtimes(tmp_path)
    assert len(mtimes) == 2
    assert any("sub" in p for p in mtimes)


def test_collect_pom_mtimes_skips_pruned_dirs(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    _write_pom(tmp_path / "target" / "nested", _single_module_pom())
    mtimes = maven._collect_pom_mtimes(tmp_path)
    assert len(mtimes) == 1


def test_collect_pom_mtimes_empty_project(tmp_path):
    mtimes = maven._collect_pom_mtimes(tmp_path)
    assert mtimes == {}


# --------------------------------------------------------------------------- #
# _pom_mtimes_valid
# --------------------------------------------------------------------------- #
def test_pom_mtimes_valid_returns_true_for_unchanged(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    mtimes = maven._collect_pom_mtimes(tmp_path)
    assert maven._pom_mtimes_valid(mtimes) is True


def test_pom_mtimes_valid_returns_false_for_modified(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    mtimes = maven._collect_pom_mtimes(tmp_path)
    # 修改文件
    (tmp_path / "pom.xml").write_text("<!-- modified -->", encoding="utf-8")
    assert maven._pom_mtimes_valid(mtimes) is False


def test_pom_mtimes_valid_returns_false_for_deleted(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    mtimes = maven._collect_pom_mtimes(tmp_path)
    (tmp_path / "pom.xml").unlink()
    assert maven._pom_mtimes_valid(mtimes) is False


def test_pom_mtimes_valid_returns_true_for_empty():
    assert maven._pom_mtimes_valid({}) is True


# --------------------------------------------------------------------------- #
# _scan_jacoco_config
# --------------------------------------------------------------------------- #
def test_scan_jacoco_config_finds_plugin(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    assert maven._scan_jacoco_config(tmp_path) is True


def test_scan_jacoco_config_no_plugin(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=False))
    assert maven._scan_jacoco_config(tmp_path) is False


def test_scan_jacoco_config_nested_modules(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=False))
    _write_pom(tmp_path / "sub", _single_module_pom(jacoco=True))
    assert maven._scan_jacoco_config(tmp_path) is True


def test_scan_jacoco_config_skips_pruned_dirs(tmp_path):
    _write_pom(tmp_path / "target", _single_module_pom(jacoco=True))
    assert maven._scan_jacoco_config(tmp_path) is False


def test_scan_jacoco_config_empty_project(tmp_path):
    assert maven._scan_jacoco_config(tmp_path) is False


def test_scan_jacoco_config_ignores_plugin_management_only(tmp_path):
    """pluginManagement 中声明 jacoco 不应视为已启用。"""
    _write_pom(tmp_path, _plugin_management_only_pom())
    assert maven._scan_jacoco_config(tmp_path) is False


def test_scan_jacoco_config_ignores_plugin_management_in_parent(tmp_path):
    """父 pom 仅在 pluginManagement 声明，子模块未启用 jacoco。"""
    _write_pom(tmp_path, _plugin_management_only_pom())
    _write_pom(tmp_path / "sub", _single_module_pom(jacoco=False))
    assert maven._scan_jacoco_config(tmp_path) is False


def test_scan_jacoco_config_finds_build_plugins_with_plugin_management(tmp_path):
    """pluginManagement 声明 + build/plugins 实际启用 jacoco。"""
    _write_pom(tmp_path, _plugin_management_with_build_plugins_pom())
    assert maven._scan_jacoco_config(tmp_path) is True


def test_scan_jacoco_config_submodule_enables_jacoco(tmp_path):
    """父 pom 仅 pluginManagement 声明，子模块在 build/plugins 启用 jacoco。"""
    _write_pom(tmp_path, _plugin_management_only_pom())
    _write_pom(tmp_path / "sub", _single_module_pom(jacoco=True))
    assert maven._scan_jacoco_config(tmp_path) is True


def test_has_jacoco_in_build_plugins_returns_false_for_malformed_xml(tmp_path):
    """格式错误的 pom.xml 应返回 False。"""
    pom_path = tmp_path / "pom.xml"
    pom_path.write_text("not xml", encoding="utf-8")
    assert maven._has_jacoco_in_build_plugins(pom_path) is False


def test_has_jacoco_in_build_plugins_returns_false_for_missing_pom(tmp_path):
    """不存在的 pom.xml 应返回 False。"""
    assert maven._has_jacoco_in_build_plugins(tmp_path / "nonexistent.xml") is False


# --------------------------------------------------------------------------- #
# parse_jacoco_config (带缓存)
# --------------------------------------------------------------------------- #
def test_parse_jacoco_config_no_state_scans(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    assert maven.parse_jacoco_config(tmp_path) is True


def test_parse_jacoco_config_uses_valid_cache(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=False))
    mtimes = maven._collect_pom_mtimes(tmp_path)
    state = _state_with_cache(True, mtimes)
    # 实际 pom 无 jacoco, 但缓存说有
    assert maven.parse_jacoco_config(tmp_path, state=state) is True


def test_parse_jacoco_config_refreshes_stale_cache(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    mtimes = maven._collect_pom_mtimes(tmp_path)
    # 构造过期缓存
    stale_mtimes = {p: mtime - 1000 for p, mtime in mtimes.items()}
    state = _state_with_cache(False, stale_mtimes)
    assert maven.parse_jacoco_config(tmp_path, state=state) is True


def test_parse_jacoco_config_force_recheck_ignores_cache(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    mtimes = maven._collect_pom_mtimes(tmp_path)
    state = _state_with_cache(False, mtimes)
    assert maven.parse_jacoco_config(tmp_path, state=state, force_recheck=True) is True


def test_parse_jacoco_config_updates_state_cache(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    state = MagicMock(spec=State)
    state.jacoco_config_cache = None
    maven.parse_jacoco_config(tmp_path, state=state)
    assert state.jacoco_config_cache is not None
    assert state.jacoco_config_cache["result"] is True


# --------------------------------------------------------------------------- #
# is_multi_module
# --------------------------------------------------------------------------- #
def test_is_multi_module_true(tmp_path):
    _write_pom(tmp_path, _multi_module_pom())
    assert maven.is_multi_module(tmp_path) is True


def test_is_multi_module_false(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    assert maven.is_multi_module(tmp_path) is False


def test_is_multi_module_no_pom(tmp_path):
    assert maven.is_multi_module(tmp_path) is False


def test_is_multi_module_invalid_xml(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pom.xml").write_text("not xml", encoding="utf-8")
    assert maven.is_multi_module(tmp_path) is False


# --------------------------------------------------------------------------- #
# get_maven_modules
# --------------------------------------------------------------------------- #
def test_get_maven_modules_multi(tmp_path):
    _write_pom(tmp_path, _multi_module_pom())
    modules = maven.get_maven_modules(tmp_path)
    assert modules == ["module-a", "module-b"]


def test_get_maven_modules_single(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    modules = maven.get_maven_modules(tmp_path)
    assert modules == ["."]


def test_get_maven_modules_no_pom(tmp_path):
    modules = maven.get_maven_modules(tmp_path)
    assert modules == ["."]


def test_get_maven_modules_invalid_xml(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pom.xml").write_text("not xml", encoding="utf-8")
    modules = maven.get_maven_modules(tmp_path)
    assert modules == ["."]


# --------------------------------------------------------------------------- #
# find_module_for_source
# --------------------------------------------------------------------------- #
def test_find_module_for_source_in_submodule(tmp_path):
    _write_pom(tmp_path, _multi_module_pom())
    _write_pom(tmp_path / "module-a", _single_module_pom())
    src = tmp_path / "module-a" / "src" / "main" / "java" / "Foo.java"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.touch()
    assert maven.find_module_for_source(tmp_path, src) == "module-a"


def test_find_module_for_source_in_root(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    src = tmp_path / "src" / "main" / "java" / "Foo.java"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.touch()
    assert maven.find_module_for_source(tmp_path, src) == "."


def test_find_module_for_source_no_pom_returns_dot(tmp_path):
    src = tmp_path / "Foo.java"
    src.touch()
    assert maven.find_module_for_source(tmp_path, src) == "."


# --------------------------------------------------------------------------- #
# build_mvn_cmd
# --------------------------------------------------------------------------- #
def test_build_mvn_cmd_jacoco_configured(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["DummyTest"])
    assert cmd is not None
    assert "/usr/bin/mvn" in cmd
    assert "test" in cmd
    assert "jacoco:report" in cmd
    # 不含 prepare-agent
    assert not any("prepare-agent" in c for c in cmd)


def test_build_mvn_cmd_jacoco_not_configured(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=False))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["DummyTest"])
    assert cmd is not None
    assert any("prepare-agent" in c for c in cmd)
    assert any(":report" in c for c in cmd)


def test_build_mvn_cmd_with_test_classes(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["FooTest", "BarTest"])
    assert cmd is not None
    test_arg = next(c for c in cmd if c.startswith("-Dtest="))
    assert "FooTest" in test_arg
    assert "BarTest" in test_arg


def test_build_mvn_cmd_multi_module_with_pl(tmp_path):
    _write_pom(tmp_path, _multi_module_pom())
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["DummyTest"],
                                  module="module-a")
    assert cmd is not None
    assert "-pl" in cmd
    idx = cmd.index("-pl")
    assert cmd[idx + 1] == "module-a"
    assert "-am" in cmd


def test_build_mvn_cmd_single_module_no_pl(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["DummyTest"],
                                  module="module-a")
    assert cmd is not None
    assert "-pl" not in cmd


def test_build_mvn_cmd_root_module_no_pl(tmp_path):
    _write_pom(tmp_path, _multi_module_pom())
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["DummyTest"],
                                  module=".")
    assert cmd is not None
    assert "-pl" not in cmd


def test_build_mvn_cmd_mvn_not_found(tmp_path):
    _write_pom(tmp_path, _single_module_pom())
    with patch("jaut.maven.resolve_tool", return_value=None):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["DummyTest"])
    assert cmd is None


def test_build_mvn_cmd_always_flags(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        cmd = maven.build_mvn_cmd(tmp_path, test_classes=["DummyTest"])
    assert cmd is not None
    for flag in config.MVN_ALWAYS_FLAGS:
        assert flag in cmd


def test_build_mvn_cmd_empty_test_classes_raises(tmp_path):
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"):
        with pytest.raises(ValueError, match="test_classes 不能为空"):
            maven.build_mvn_cmd(tmp_path, test_classes=[])


# --------------------------------------------------------------------------- #
# run_mvn
# --------------------------------------------------------------------------- #
def test_run_mvn_delegates_to_run_command(tmp_path):
    with patch("jaut.maven.run_command") as mock_run:
        mock_run.return_value = MagicMock(ok=True)
        result = maven.run_mvn(["mvn", "test"], cwd=str(tmp_path),
                               log_file=str(tmp_path / "mvn.log"))
    mock_run.assert_called_once()
    assert result.ok is True


def test_run_mvn_uses_config_timeout(tmp_path):
    with patch("jaut.maven.run_command") as mock_run, \
         patch("jaut.maven.config.mvn_timeout", return_value=999):
        mock_run.return_value = MagicMock(ok=True)
        maven.run_mvn(["mvn", "test"], cwd=str(tmp_path),
                      log_file=str(tmp_path / "mvn.log"))
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 999


def test_run_mvn_custom_timeout(tmp_path):
    with patch("jaut.maven.run_command") as mock_run:
        mock_run.return_value = MagicMock(ok=True)
        maven.run_mvn(["mvn", "test"], cwd=str(tmp_path),
                      log_file=str(tmp_path / "mvn.log"), timeout=42)
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 42


# --------------------------------------------------------------------------- #
# clean_jacoco_dirs
# --------------------------------------------------------------------------- #
def test_clean_jacoco_dirs_removes_report_dir(tmp_path):
    jacoco_dir = tmp_path / "target" / "site" / "jacoco"
    jacoco_dir.mkdir(parents=True)
    (jacoco_dir / "report.html").touch()
    maven.clean_jacoco_dirs(tmp_path, ["."])
    assert not jacoco_dir.exists()


def test_clean_jacoco_dirs_removes_exec_file(tmp_path):
    jacoco_exec = tmp_path / "target" / "jacoco.exec"
    jacoco_exec.parent.mkdir(parents=True)
    jacoco_exec.touch()
    maven.clean_jacoco_dirs(tmp_path, ["."])
    assert not jacoco_exec.exists()


def test_clean_jacoco_dirs_no_existing_dirs(tmp_path):
    # 不应抛异常
    maven.clean_jacoco_dirs(tmp_path, ["."])


def test_clean_jacoco_dirs_multi_modules(tmp_path):
    for mod in ["mod-a", "mod-b"]:
        d = tmp_path / mod / "target" / "site" / "jacoco"
        d.mkdir(parents=True)
        (d / "report.html").touch()
    maven.clean_jacoco_dirs(tmp_path, ["mod-a", "mod-b"])
    assert not (tmp_path / "mod-a" / "target" / "site" / "jacoco").exists()
    assert not (tmp_path / "mod-b" / "target" / "site" / "jacoco").exists()


# --------------------------------------------------------------------------- #
# clean_surefire_dirs
# --------------------------------------------------------------------------- #
def test_clean_surefire_dirs_removes_dir(tmp_path):
    surefire_dir = tmp_path / "target" / "surefire-reports"
    surefire_dir.mkdir(parents=True)
    (surefire_dir / "TEST-Foo.xml").touch()
    uncleaned = maven.clean_surefire_dirs(tmp_path, ["."])
    assert uncleaned == []
    assert not surefire_dir.exists()


def test_clean_surefire_dirs_no_existing(tmp_path):
    uncleaned = maven.clean_surefire_dirs(tmp_path, ["."])
    assert uncleaned == []


def test_clean_surefire_dirs_returns_uncleaned_on_failure(tmp_path):
    surefire_dir = tmp_path / "target" / "surefire-reports"
    surefire_dir.mkdir(parents=True)
    # rmtree ignore_errors=True, 模拟目录仍然存在(清理失败)
    with patch("shutil.rmtree"), \
         patch("os.path.isdir", return_value=True):
        uncleaned = maven.clean_surefire_dirs(tmp_path, ["."])
    assert len(uncleaned) == 1
    assert "surefire-reports" in uncleaned[0]


# --------------------------------------------------------------------------- #
# _module_base
# --------------------------------------------------------------------------- #
def test_module_base_with_dot():
    assert maven._module_base("/project", ".") == "/project"


def test_module_base_with_module():
    assert maven._module_base("/project", "mod-a") == "/project/mod-a"


def test_module_base_with_empty_module():
    assert maven._module_base("/project", "") == "/project"


# --------------------------------------------------------------------------- #
# P0-6: 编译错误解析
# --------------------------------------------------------------------------- #
def test_parse_compile_errors_finds_java_errors(tmp_path):
    """解析 mvn.log 中的 Java 编译错误。"""
    log_file = tmp_path / "mvn.log"
    log_file.write_text("""[INFO] Scanning for projects...
[INFO] Building test-project 1.0-SNAPSHOT
[ERROR] /src/main/java/com/example/MyService.java:[42,10] cannot find symbol
[ERROR] /src/main/java/com/example/MyService.java:[55,5] incompatible types: int cannot be converted to String
[ERROR] /src/main/java/com/example/Helper.java:[10,1] class, interface, or enum expected
[INFO] BUILD FAILURE
""", encoding="utf-8")
    
    errors = maven.parse_compile_errors(log_file)
    assert len(errors) == 3
    assert errors[0].file == "/src/main/java/com/example/MyService.java"
    assert errors[0].line == 42
    assert errors[0].col == 10
    assert "cannot find symbol" in errors[0].message
    assert errors[1].line == 55
    assert errors[2].file == "/src/main/java/com/example/Helper.java"


def test_parse_compile_errors_empty_log(tmp_path):
    """空日志文件应返回空列表。"""
    log_file = tmp_path / "mvn.log"
    log_file.write_text("", encoding="utf-8")
    
    errors = maven.parse_compile_errors(log_file)
    assert errors == []


def test_parse_compile_errors_no_compile_error(tmp_path):
    """无编译错误时应返回空列表。"""
    log_file = tmp_path / "mvn.log"
    log_file.write_text("""[INFO] Scanning for projects...
[INFO] Building test-project 1.0-SNAPSHOT
[ERROR] Failed to execute goal on project: Could not resolve dependencies
[INFO] BUILD FAILURE
""", encoding="utf-8")
    
    errors = maven.parse_compile_errors(log_file)
    assert errors == []


def test_parse_compile_errors_missing_file(tmp_path):
    """日志文件不存在时应返回空列表。"""
    log_file = tmp_path / "nonexistent.log"
    
    errors = maven.parse_compile_errors(log_file)
    assert errors == []


def test_is_compile_error_true(tmp_path):
    """存在编译错误时应返回 True。"""
    log_file = tmp_path / "mvn.log"
    log_file.write_text("[ERROR] /src/Foo.java:[1,1] error\n", encoding="utf-8")
    
    assert maven.is_compile_error(log_file) is True


def test_is_compile_error_false(tmp_path):
    """无编译错误时应返回 False。"""
    log_file = tmp_path / "mvn.log"
    log_file.write_text("[ERROR] BUILD FAILURE\n", encoding="utf-8")
    
    assert maven.is_compile_error(log_file) is False


def test_compile_error_summary_line():
    """CompileError.summary_line() 应返回正确格式。"""
    from jaut.maven import CompileError
    error = CompileError(file="MyService.java", line=42, col=10, message="cannot find symbol")
    assert error.summary_line() == "MyService.java:42:10 - cannot find symbol"


# --------------------------------------------------------------------------- #
# run_fast_single_cov
# --------------------------------------------------------------------------- #
@pytest.fixture
def _ensure_jacoco_script():
    """确保 jar 文件存在(不修改脚本本身)，测试后清理。"""
    script_dir = Path(maven.__file__).resolve().parent.parent / "jacoco"
    lib_dir = script_dir / "lib"
    agent = lib_dir / "jacocoagent.jar"
    cli = lib_dir / "jacococli.jar"
    created_agent = not agent.is_file()
    created_cli = not cli.is_file()
    lib_dir.mkdir(parents=True, exist_ok=True)
    if created_agent:
        agent.touch()
    if created_cli:
        cli.touch()
    yield
    if created_agent:
        agent.unlink(missing_ok=True)
    if created_cli:
        cli.unlink(missing_ok=True)


def test_run_fast_single_cov_success(tmp_path, _ensure_jacoco_script):
    """正常执行: 脚本存在、jar 存在、返回 ok=True。"""
    with patch("jaut.maven.run_command") as mock_run:
        mock_run.return_value = MagicMock(ok=True)
        result = maven.run_fast_single_cov(
            tmp_path, "mod-a", "com.example.Foo", "com.example.FooTest",
            str(tmp_path / "mvn.log"), no_am=False, timeout=600)

    assert result.ok is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "bash" in cmd[0] or cmd[0].endswith("bash")
    assert "--project-root" in cmd
    assert str(tmp_path) in cmd
    assert "mod-a" in cmd
    assert "com.example.Foo" in cmd
    assert "com.example.FooTest" in cmd
    assert "--no-am" not in cmd
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 600


def test_run_fast_single_cov_with_no_am(tmp_path, _ensure_jacoco_script):
    """no_am=True 时命令中应包含 --no-am。"""
    with patch("jaut.maven.run_command") as mock_run:
        mock_run.return_value = MagicMock(ok=True)
        maven.run_fast_single_cov(
            tmp_path, "mod-a", "com.example.Foo", "com.example.FooTest",
            str(tmp_path / "mvn.log"), no_am=True)

    cmd = mock_run.call_args[0][0]
    assert "--no-am" in cmd
    assert "--project-root" in cmd


def test_run_fast_single_cov_script_not_found(tmp_path):
    """脚本不存在时应返回 ok=False。"""
    fake_module = tmp_path / "fake_maven.py"
    fake_module.touch()
    with patch("jaut.maven.__file__", str(fake_module)):
        result = maven.run_fast_single_cov(
            tmp_path, "mod-a", "com.example.Foo", "com.example.FooTest",
            str(tmp_path / "mvn.log"))
    assert result.ok is False


def test_run_fast_single_cov_jar_missing(tmp_path):
    """JaCoCo jar 缺失时应返回 ok=False。"""
    script_dir = Path(maven.__file__).resolve().parent.parent / "jacoco"
    lib_dir = script_dir / "lib"
    script = script_dir / "fast-single-cov.sh"
    script_existed = script.is_file()
    lib_dir.mkdir(parents=True, exist_ok=True)
    if not script_existed:
        script.write_text("#!/bin/bash\nexit 0", encoding="utf-8")
    (lib_dir / "jacocoagent.jar").unlink(missing_ok=True)
    (lib_dir / "jacococli.jar").unlink(missing_ok=True)
    try:
        result = maven.run_fast_single_cov(
            tmp_path, "mod-a", "com.example.Foo", "com.example.FooTest",
            str(tmp_path / "mvn.log"))
        assert result.ok is False
    finally:
        if not script_existed:
            script.unlink(missing_ok=True)


def test_run_fast_single_cov_uses_config_timeout(tmp_path, _ensure_jacoco_script):
    """未传 timeout 时应使用 config.mvn_timeout()。"""
    with patch("jaut.maven.run_command") as mock_run, \
         patch("jaut.maven.config.mvn_timeout", return_value=888):
        mock_run.return_value = MagicMock(ok=True)
        maven.run_fast_single_cov(
            tmp_path, "mod-a", "com.example.Foo", "com.example.FooTest",
            str(tmp_path / "mvn.log"))
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 888


# --------------------------------------------------------------------------- #
# run_mvn_test_with_jacoco (降级方案)
# --------------------------------------------------------------------------- #
def test_run_mvn_test_with_jacoco_success(tmp_path):
    """降级方案正常执行: mvn 存在、pom 有 jacoco。"""
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"), \
         patch("jaut.maven.run_mvn") as mock_run:
        mock_run.return_value = MagicMock(ok=True)
        result = maven.run_mvn_test_with_jacoco(
            tmp_path, ".", "FooTest", "0.8.12", str(tmp_path / "mvn.log"))
    assert result.ok is True
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "test" in cmd
    assert "jacoco:report" in cmd


def test_run_mvn_test_with_jacoco_mvn_not_found(tmp_path):
    """mvn 不可用时应返回 ok=False。"""
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value=None):
        result = maven.run_mvn_test_with_jacoco(
            tmp_path, ".", "FooTest", "0.8.12", str(tmp_path / "mvn.log"))
    assert result.ok is False


def test_run_mvn_test_with_jacoco_no_jacoco_configured(tmp_path):
    """pom 未配置 jacoco 时应使用完整坐标。"""
    _write_pom(tmp_path, _single_module_pom(jacoco=False))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"), \
         patch("jaut.maven.run_mvn") as mock_run:
        mock_run.return_value = MagicMock(ok=True)
        maven.run_mvn_test_with_jacoco(
            tmp_path, ".", "FooTest", "0.8.12", str(tmp_path / "mvn.log"))
    cmd = mock_run.call_args[0][0]
    assert any("prepare-agent" in c for c in cmd)


def test_run_mvn_test_with_jacoco_passes_timeout(tmp_path):
    """timeout 参数应传递给 run_mvn。"""
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"), \
         patch("jaut.maven.run_mvn") as mock_run:
        mock_run.return_value = MagicMock(ok=True)
        maven.run_mvn_test_with_jacoco(
            tmp_path, ".", "FooTest", "0.8.12", str(tmp_path / "mvn.log"), timeout=500)
    _, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 500


def test_run_mvn_test_with_jacoco_forwards_state(tmp_path):
    """state 参数应传递给 build_mvn_cmd。"""
    _write_pom(tmp_path, _single_module_pom(jacoco=True))
    mock_state = MagicMock()
    with patch("jaut.maven.resolve_tool", return_value="/usr/bin/mvn"), \
         patch("jaut.maven.run_mvn") as mock_run, \
         patch("jaut.maven.build_mvn_cmd", return_value=["mvn", "test"]) as mock_build:
        mock_run.return_value = MagicMock(ok=True)
        maven.run_mvn_test_with_jacoco(
            tmp_path, ".", "FooTest", "0.8.12", str(tmp_path / "mvn.log"), state=mock_state)
    _, kwargs = mock_build.call_args
    assert kwargs["state"] is mock_state


# --------------------------------------------------------------------------- #
# run_single_cov_with_fallback (共享降级逻辑)
# --------------------------------------------------------------------------- #
def test_run_single_cov_fast_succeeds(tmp_path):
    """快速路径成功 -> 不调用降级方案, log_path=None。"""
    with patch("jaut.maven.run_fast_single_cov") as mock_fast, \
         patch("jaut.maven.run_mvn_test_with_jacoco") as mock_fallback:
        mock_fast.return_value = MagicMock(ok=True)
        result, log_path = maven.run_single_cov_with_fallback(
            tmp_path, ".", "com.example.Foo", "FooTest",
            str(tmp_path / "mvn.log"), jacoco_version="0.8.12")

    assert result.ok is True
    assert log_path is None
    mock_fast.assert_called_once()
    mock_fallback.assert_not_called()


def test_run_single_cov_fast_fails_fallback_succeeds(tmp_path):
    """快速路径失败 -> 降级成功, 返回 ok=True 并备份日志。"""
    mvn_log = str(tmp_path / "mvn.log")
    Path(mvn_log).write_text("fast-single-cov output")

    with patch("jaut.maven.run_fast_single_cov") as mock_fast, \
         patch("jaut.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("jaut.maven.clean_jacoco_dirs"):
        mock_fast.return_value = MagicMock(ok=False)
        mock_fallback.return_value = MagicMock(ok=True)
        result, log_path = maven.run_single_cov_with_fallback(
            tmp_path, ".", "com.example.Foo", "FooTest",
            mvn_log, jacoco_version="0.8.12")

    assert result.ok is True
    assert log_path is not None
    assert log_path.endswith(".fast-single-cov.log")
    assert Path(log_path).is_file()
    mock_fallback.assert_called_once()


def test_run_single_cov_both_fail(tmp_path):
    """两种方案都失败 -> 返回 ok=False 并备份日志。"""
    mvn_log = str(tmp_path / "mvn.log")
    Path(mvn_log).write_text("fast-single-cov output")

    with patch("jaut.maven.run_fast_single_cov") as mock_fast, \
         patch("jaut.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("jaut.maven.clean_jacoco_dirs"):
        mock_fast.return_value = MagicMock(ok=False)
        mock_fallback.return_value = MagicMock(ok=False)
        result, log_path = maven.run_single_cov_with_fallback(
            tmp_path, ".", "com.example.Foo", "FooTest",
            mvn_log, jacoco_version="0.8.12")

    assert result.ok is False
    assert log_path is not None
    assert Path(log_path).is_file()


def test_run_single_cov_fast_fails_no_log_file(tmp_path):
    """快速路径失败且日志文件不存在 -> 跳过备份, 降级执行。"""
    mvn_log = str(tmp_path / "nonexistent.log")

    with patch("jaut.maven.run_fast_single_cov") as mock_fast, \
         patch("jaut.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("jaut.maven.clean_jacoco_dirs"):
        mock_fast.return_value = MagicMock(ok=False)
        mock_fallback.return_value = MagicMock(ok=True)
        result, log_path = maven.run_single_cov_with_fallback(
            tmp_path, ".", "com.example.Foo", "FooTest",
            mvn_log, jacoco_version="0.8.12")

    assert result.ok is True
    # log_path 返回但文件不存在(调用方可选择是否使用)
    assert log_path is not None
    assert not Path(log_path).is_file()


def test_run_single_cov_default_jacoco_version(tmp_path):
    """不传 jacoco_version 时, 降级路径使用 config.DEFAULT_JACOCO_VERSION。"""
    mvn_log = str(tmp_path / "mvn.log")
    Path(mvn_log).write_text("fast-single-cov output")

    with patch("jaut.maven.run_fast_single_cov") as mock_fast, \
         patch("jaut.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("jaut.maven.clean_jacoco_dirs"):
        mock_fast.return_value = MagicMock(ok=False)
        mock_fallback.return_value = MagicMock(ok=True)
        # 不传 jacoco_version, 使用默认值
        result, _ = maven.run_single_cov_with_fallback(
            tmp_path, ".", "com.example.Foo", "FooTest", mvn_log)

    assert result.ok is True
    # 验证降级调用时 jacoco_version 为 config.DEFAULT_JACOCO_VERSION, 非空字符串
    mock_fallback.assert_called_once()
    args, kwargs = mock_fallback.call_args
    # run_mvn_test_with_jacoco(project_root, module, test_class, jacoco_version, log_file, ...)
    # jacoco_version 是第 4 个位置参数(索引 3)
    assert args[3] == config.DEFAULT_JACOCO_VERSION
    assert args[3] != ""


# --------------------------------------------------------------------------- #
# _is_fallbackable_failure 判定函数
# --------------------------------------------------------------------------- #
def test_is_fallbackable_compilation_failure(tmp_path):
    """编译失败 -> 不可降级。"""
    log = tmp_path / "mvn.log"
    log.write_text("🔨 [1/4] 编译模块及依赖...\n❌ 编译失败！\n")
    assert maven._is_fallbackable_failure(str(log)) is False


def test_is_fallbackable_mvn_exec_failure(tmp_path):
    """mvn 执行失败(测试报错) -> 不可降级。"""
    log = tmp_path / "mvn.log"
    log.write_text("some output\n❌ mvn 执行失败(退出码 1)。\n")
    assert maven._is_fallbackable_failure(str(log)) is False


def test_is_fallbackable_mvn_compilation_error(tmp_path):
    """Maven 原生 COMPILATION ERROR(step4 surefire:test 阶段) -> 不可降级。"""
    log = tmp_path / "mvn.log"
    log.write_text(
        "[INFO] BUILD FAILURE\n"
        "[ERROR] COMPILATION ERROR :\n"
        "[ERROR] /path/to/Foo.java:[10,5] cannot find symbol\n"
        "❌ mvn 执行失败(退出码 1)。\n"
    )
    assert maven._is_fallbackable_failure(str(log)) is False


def test_is_fallbackable_mvn_compilation_error_long_log(tmp_path):
    """Maven 编译错误详情超过 tail 行数 -> COMPILATION ERROR 仍命中。"""
    # 模拟 Maven 输出大量编译错误, 超过 _TAIL_LINES(50)
    lines = ["[ERROR] error detail line %d\n" % i for i in range(60)]
    lines.append("[ERROR] COMPILATION ERROR :\n")
    lines.append("❌ mvn 执行失败(退出码 1)。\n")
    log = tmp_path / "mvn.log"
    log.write_text("".join(lines))
    assert maven._is_fallbackable_failure(str(log)) is False


def test_is_fallbackable_no_tests_executed(tmp_path):
    """No tests were executed -> 不可降级。"""
    log = tmp_path / "mvn.log"
    log.write_text("running tests...\n❌ 致命错误: Surefire 没有找到或执行任何测试！\nNo tests were executed\n")
    assert maven._is_fallbackable_failure(str(log)) is False


def test_is_fallbackable_missing_params(tmp_path):
    """缺少参数 -> 不可降级。"""
    log = tmp_path / "mvn.log"
    log.write_text("❌ 错误: 缺少参数！\n")
    assert maven._is_fallbackable_failure(str(log)) is False


def test_is_fallbackable_exec_not_generated(tmp_path):
    """exec 文件未生成(argLine 写死) -> 可降级。"""
    log = tmp_path / "mvn.log"
    log.write_text(
        "Tests run: 1, Failures: 0\n"
        "❌ 致命错误: 测试跑完了，但依然未找到覆盖率数据文件\n"
    )
    assert maven._is_fallbackable_failure(str(log)) is True


def test_is_fallbackable_empty_log(tmp_path):
    """空日志 -> 视为可降级。"""
    log = tmp_path / "mvn.log"
    log.write_text("")
    assert maven._is_fallbackable_failure(str(log)) is True


def test_is_fallbackable_nonexistent_log(tmp_path):
    """日志文件不存在 -> 视为可降级。"""
    assert maven._is_fallbackable_failure(str(tmp_path / "nope.log")) is True


# --------------------------------------------------------------------------- #
# _tail_file 辅助函数
# --------------------------------------------------------------------------- #
def test_tail_file_returns_last_n_lines(tmp_path):
    """_tail_file 返回文件末尾 N 行。"""
    log = tmp_path / "test.log"
    log.write_text("line1\nline2\nline3\nline4\nline5\n")
    assert maven._tail_file(str(log), lines=3).strip().splitlines() == [
        "line3", "line4", "line5",
    ]


def test_tail_file_nonexistent(tmp_path):
    """文件不存在时返回空字符串。"""
    assert maven._tail_file(str(tmp_path / "nope.log")) == ""


def test_tail_file_empty(tmp_path):
    """空文件返回空字符串。"""
    log = tmp_path / "empty.log"
    log.write_text("")
    assert maven._tail_file(str(log)) == ""


def test_tail_file_fewer_lines_than_requested(tmp_path):
    """文件行数少于请求行数时返回全部内容。"""
    log = tmp_path / "short.log"
    log.write_text("only one line\n")
    assert maven._tail_file(str(log), lines=30).strip() == "only one line"


def test_run_single_cov_fast_fails_log_tail_in_warning(tmp_path):
    """快速路径失败时, warning 消息包含日志文件尾部错误详情。"""
    mvn_log = str(tmp_path / "mvn.log")
    error_output = "mvn compile error\n[ERROR] Failed to execute goal\nBUILD FAILURE\n"
    Path(mvn_log).write_text("some header\n" + error_output)

    mock_log = MagicMock()
    with patch("jaut.maven.run_fast_single_cov") as mock_fast, \
         patch("jaut.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("jaut.maven.clean_jacoco_dirs"), \
         patch("jaut.maven.module_logger", return_value=mock_log):
        mock_fast.return_value = MagicMock(ok=False)
        mock_fallback.return_value = MagicMock(ok=True)
        maven.run_single_cov_with_fallback(
            tmp_path, ".", "com.example.Foo", "FooTest",
            mvn_log, jacoco_version="0.8.12")

    # warning 调用中应包含日志尾部
    warning_calls = [str(call) for call in mock_log.warning.call_args_list]
    assert any("BUILD FAILURE" in c for c in warning_calls), (
        f"warning 消息中未包含日志尾部错误详情: {warning_calls}"
    )


def test_run_single_cov_non_fallbackable_skips_fallback(tmp_path):
    """编译失败等不可恢复场景 -> 跳过降级, 不调用 run_mvn_test_with_jacoco。"""
    mvn_log = str(tmp_path / "mvn.log")
    Path(mvn_log).write_text("🔨 编译模块...\n❌ 编译失败！\n")

    with patch("jaut.maven.run_fast_single_cov") as mock_fast, \
         patch("jaut.maven.run_mvn_test_with_jacoco") as mock_fallback, \
         patch("jaut.maven.clean_jacoco_dirs"):
        mock_fast.return_value = MagicMock(ok=False)
        result, log_path = maven.run_single_cov_with_fallback(
            tmp_path, ".", "com.example.Foo", "FooTest",
            mvn_log, jacoco_version="0.8.12")

    # 日志仍被备份
    assert log_path is not None
    assert log_path.endswith(".fast-single-cov.log")
    assert Path(log_path).is_file()
    # 降级未被调用
    mock_fallback.assert_not_called()
    # 结果仍为失败
    assert result.ok is False
