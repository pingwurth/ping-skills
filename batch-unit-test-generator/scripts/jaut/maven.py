"""Maven 命令构建与执行、JaCoCo/Surefire 报告清理。

命令构建规则(SKILL.md §3):
    - pom 已配置 jacoco-maven-plugin -> `mvn test jacoco:report`
    - 未配置 -> 用完整坐标挂载 prepare-agent + report
    - 单模块不加 -pl/-am; 多模块且指定模块时加 -pl <module> -am
    - 必带三个 -D 参数(忽略测试失败, 由 surefire 做唯一判定)

每次执行前清理旧 JaCoCo / Surefire 报告, 防止读取历史结果。
"""

from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from . import config
from .logutil import module_logger
from .models import State
from .proc import CommandResult, resolve_tool, run_command

# 编译错误正则: [ERROR] /path/to/File.java:[line,col] message
_COMPILE_ERROR_RE = re.compile(
    r"\[ERROR\]\s+(.+?\.java):\[(\d+),(\d+)\]\s+(.*)"
)


@dataclass
class CompileError:
    """单个编译错误。"""
    file: str
    line: int
    col: int
    message: str

    def summary_line(self) -> str:
        """一行摘要: File.java:line:col - message"""
        return f"{self.file}:{self.line}:{self.col} - {self.message}"


def parse_compile_errors(log_file: str | Path) -> list[CompileError]:
    """解析 mvn.log 中的编译错误。
    
    Args:
        log_file: mvn 日志文件路径。
        
    Returns:
        编译错误列表(空列表表示无编译错误)。
    """
    errors: list[CompileError] = []
    log_path = Path(log_file)
    if not log_path.is_file():
        return errors
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _COMPILE_ERROR_RE.search(line)
                if m:
                    errors.append(CompileError(
                        file=m.group(1),
                        line=int(m.group(2)),
                        col=int(m.group(3)),
                        message=m.group(4).strip(),
                    ))
    except OSError:
        pass
    return errors


def is_compile_error(log_file: str | Path) -> bool:
    """判断 mvn 失败是否为编译错误(而非依赖/环境问题)。
    
    Args:
        log_file: mvn 日志文件路径。
        
    Returns:
        True 表示存在编译错误，False 表示其他类型失败。
    """
    return len(parse_compile_errors(log_file)) > 0


def _collect_pom_mtimes(project_root: str | Path) -> dict[str, float]:
    """收集项目中所有 pom.xml 的路径和修改时间(用于缓存失效检测)。"""
    mtimes: dict[str, float] = {}
    for dirpath, dirnames, filenames in os.walk(str(project_root)):
        dirnames[:] = [d for d in dirnames if d not in config.PRUNE_DIRS]
        if "pom.xml" in filenames:
            pom_path = Path(dirpath) / "pom.xml"
            try:
                mtimes[str(pom_path)] = pom_path.stat().st_mtime
            except OSError:
                pass
    return mtimes


def _pom_mtimes_valid(cached_mtimes: dict[str, float]) -> bool:
    """验证缓存的 pom.xml mtime 是否仍然有效(文件未被修改/删除)。"""
    for pom_path, old_mtime in cached_mtimes.items():
        path = Path(pom_path)
        try:
            if not path.exists() or path.stat().st_mtime != old_mtime:
                return False
        except OSError:
            return False
    return True


def _has_jacoco_in_build_plugins(pom_path: Path) -> bool:
    """检查 pom.xml 的 <build><plugins> 中是否有 jacoco-maven-plugin。

    只检查实际启用的插件（<build><plugins>），忽略 <pluginManagement> 中的声明。
    """
    try:
        tree = ET.parse(pom_path)
    except (ET.ParseError, OSError):
        return False

    root = tree.getroot()

    # 遍历 <project> 的直接子元素找到 <build>
    for child in root:
        tag = str(child.tag).split("}")[-1] if "}" in str(child.tag) else str(child.tag)
        if tag != "build":
            continue

        # 在 <build> 中找到直接子元素 <plugins>（忽略 <pluginManagement>）
        for build_child in child:
            build_child_tag = str(build_child.tag).split("}")[-1] if "}" in str(build_child.tag) else str(build_child.tag)
            if build_child_tag != "plugins":
                continue

            # 检查 <plugins> 中的每个 <plugin>
            for plugin in build_child:
                plugin_tag = str(plugin.tag).split("}")[-1] if "}" in str(plugin.tag) else str(plugin.tag)
                if plugin_tag != "plugin":
                    continue

                # 检查 <artifactId> 是否为 jacoco-maven-plugin
                for plugin_child in plugin:
                    plugin_child_tag = str(plugin_child.tag).split("}")[-1] if "}" in str(plugin_child.tag) else str(plugin_child.tag)
                    if plugin_child_tag == "artifactId":
                        if (plugin_child.text or "").strip() == "jacoco-maven-plugin":
                            return True

    return False


def _scan_jacoco_config(project_root: str | Path) -> bool:
    """全树遍历探测 jacoco-maven-plugin 配置。

    只检查 <build><plugins> 中实际启用的插件，忽略 <pluginManagement> 中的声明。
    """
    for dirpath, dirnames, filenames in os.walk(str(project_root)):
        dirnames[:] = [d for d in dirnames if d not in config.PRUNE_DIRS]
        if "pom.xml" not in filenames:
            continue
        if _has_jacoco_in_build_plugins(Path(dirpath) / "pom.xml"):
            return True
    return False


def parse_jacoco_config(project_root: str | Path, *,
                        state: Optional[State] = None,
                        force_recheck: bool = False) -> bool:
    """探测项目任一 pom.xml 是否配置 jacoco-maven-plugin。

    支持缓存机制: 如果传入 state 且缓存有效, 直接返回缓存结果;
    否则执行全树探测并更新缓存(当 state 存在时, 会就地修改 state.jacoco_config_cache)。

    Args:
        project_root: 项目根目录。
        state: 可选的状态对象, 用于读取/写入缓存。注意: 传入 state 时本函数会修改
               state.jacoco_config_cache, 不是纯查询函数。
        force_recheck: 强制重新探测, 忽略缓存。
    """
    # 检查缓存是否有效
    if not force_recheck and state is not None and state.jacoco_config_cache is not None:
        cached = state.jacoco_config_cache
        if isinstance(cached, dict) and _pom_mtimes_valid(cached.get("pom_mtimes", {})):
            log = module_logger()
            log.debug("使用 jacoco 配置缓存")
            return bool(cached.get("result", False))

    # 执行全树探测
    result = _scan_jacoco_config(project_root)

    # 更新缓存(如果 state 存在)
    if state is not None:
        pom_mtimes = _collect_pom_mtimes(project_root)
        state.jacoco_config_cache = {"result": result, "pom_mtimes": pom_mtimes}

    return result


def is_multi_module(project_root: str | Path) -> bool:
    """根 pom 是否声明 <modules>(决定是否使用 -pl/-am)。"""
    pom = Path(project_root) / "pom.xml"
    if not pom.is_file():
        return False
    try:
        root = ET.parse(pom).getroot()
    except (ET.ParseError, OSError):
        return False
    return any(str(el.tag).endswith("modules") for el in root.iter())


def get_maven_modules(project_root: str | Path) -> list[str]:
    """解析根 pom.xml，返回模块目录列表；非多模块返回 ['.']。"""
    pom = Path(project_root) / "pom.xml"
    if not pom.is_file():
        return ["."]
    try:
        tree = ET.parse(pom)
    except (ET.ParseError, OSError):
        return ["."]
    root = tree.getroot()
    modules = []
    for el in root.iter():
        if str(el.tag).endswith("modules"):
            for mod_el in el:
                if str(mod_el.tag).endswith("module") and mod_el.text:
                    modules.append(mod_el.text.strip())
            break
    return modules if modules else ["."]


def find_module_for_source(project_root: str | Path, source_file: str | Path) -> str:
    """源文件所属 Maven 模块(向上找最近含 pom.xml 的目录), 根模块返回 '.'。"""
    root = Path(project_root).resolve()
    cur = Path(source_file).resolve().parent
    while True:
        if (cur / "pom.xml").is_file():
            try:
                rel = cur.relative_to(root)
                return "." if str(rel) == "." else rel.as_posix()
            except ValueError:
                return "."
        if cur == root or cur.parent == cur:
            return "."
        cur = cur.parent


def build_mvn_cmd(project_root: str | Path, *,
                  test_classes: Sequence[str],
                  jacoco_version: str = config.DEFAULT_JACOCO_VERSION,
                  module: Optional[str] = None,
                  state: Optional[State] = None,
                  include_dependencies: bool = True) -> Optional[list[str]]:
    """构建 mvn test + jacoco 命令; mvn 不可用返回 None。

    Args:
        project_root: 项目根目录。
        test_classes: 测试类列表(必填, SKILL.md §3: 无论在什么情况下执行 mvn test
            必须用 -Dtest 指定测试类)。
        jacoco_version: JaCoCo 版本。
        module: Maven 模块名。
        state: 状态对象(用于缓存)。
        include_dependencies: 是否包含依赖模块(加 -am 参数)。
            当依赖模块已预装时可设为 False 以提升性能。
    """
    mvn = resolve_tool("mvn")
    if mvn is None:
        return None
    log = module_logger()
    cmd = [mvn]
    if parse_jacoco_config(project_root, state=state):
        cmd += ["test", "jacoco:report"]
    else:
        coord = f"org.jacoco:jacoco-maven-plugin:{jacoco_version}"
        log.info(f"pom 未配置 jacoco-maven-plugin, 使用完整坐标挂载(版本 {jacoco_version})")
        cmd += [f"{coord}:prepare-agent", "test", f"{coord}:report"]
    if not test_classes:
        raise ValueError("test_classes 不能为空, 必须指定至少一个测试类")
    cmd.append("-Dtest=" + ",".join(test_classes))
    if module and module != "." and is_multi_module(project_root):
        cmd += ["-pl", module]
        if include_dependencies:
            cmd += ["-am"]
    cmd += list(config.MVN_ALWAYS_FLAGS)
    return cmd


def build_install_cmd(project_root: str | Path, *,
                     module: Optional[str] = None,
                     jacoco_version: str = config.DEFAULT_JACOCO_VERSION,
                     state: Optional[State] = None) -> Optional[list[str]]:
    """构建 mvn install -DskipTests 命令; mvn 不可用返回 None。

    用于在技能开始时确保依赖模块已安装到本地仓库，
    后续构建可不加 -am 参数以提升性能。

    Args:
        project_root: 项目根目录。
        module: Maven 模块名。
        jacoco_version: JaCoCo 版本(用于 prepare-agent)。
    """
    mvn = resolve_tool("mvn")
    if mvn is None:
        return None
    log = module_logger()
    cmd = [mvn]
    if parse_jacoco_config(project_root, state=state):
        cmd += ["install", "-DskipTests"]
    else:
        coord = f"org.jacoco:jacoco-maven-plugin:{jacoco_version}"
        log.info(f"pom 未配置 jacoco-maven-plugin, 使用完整坐标挂载(版本 {jacoco_version})")
        cmd += [f"{coord}:prepare-agent", "install", "-DskipTests"]
    if module and module != "." and is_multi_module(project_root):
        cmd += ["-pl", module, "-am"]
    cmd += ["-Dmaven.test.failure.ignore=true", "-DfailIfNoTests=false"]
    return cmd


def run_mvn(cmd: Sequence[str], *, cwd: str, log_file: str,
            timeout: Optional[int] = None) -> CommandResult:
    """流式执行 mvn 到日志文件, 返回 CommandResult(不抛 SystemExit)。"""
    return run_command(cmd, cwd=cwd, capture=False, log_file=log_file,
                       timeout=timeout if timeout is not None else config.mvn_timeout())


def run_fast_single_cov(
    project_root: str | Path,
    module: str,
    target_class: str,
    test_class: str,
    log_file: str,
    *,
    no_am: bool = False,
    timeout: Optional[int] = None,
) -> CommandResult:
    """使用 fast-single-cov.sh 获取单类覆盖率。

    替代 build_mvn_cmd + run_mvn 的 mvn test jacoco:report 流程。
    脚本内部完成: 编译 -> 挂载 JaCoCo agent 运行测试 -> 生成 XML/CSV/HTML 报告。
    报告输出到 <module>/target/site/jacoco/，与 jacoco.report_paths() 兼容。

    Args:
        project_root: 项目根目录(脚本的 cwd)。
        module: Maven 模块名(脚本第1参数)。
        target_class: 目标类 FQCN(脚本第2参数)。
        test_class: 测试类简单名(脚本第3参数)。
        log_file: 日志文件路径(脚本内部所有输出重定向到此文件)。
        no_am: 是否跳过依赖模块编译(verify 阶段依赖已预装时传 True)。
        timeout: 超时秒数(默认 mvn_timeout)。

    Returns:
        CommandResult: ok=True 表示脚本成功退出(退出码 0)。
    """
    log = module_logger()
    script_dir = Path(__file__).resolve().parent.parent / "jacoco"
    script = script_dir / "fast-single-cov.sh"
    if not script.is_file():
        log.error(f"fast-single-cov.sh 不存在: {script}")
        return CommandResult(ok=False, returncode=None)
    # 检查依赖的 JaCoCo 工具
    jacoco_agent = script_dir / "lib" / "jacocoagent.jar"
    jacoco_cli = script_dir / "lib" / "jacococli.jar"
    if not jacoco_agent.is_file() or not jacoco_cli.is_file():
        log.error(f"JaCoCo 工具缺失: {jacoco_agent} / {jacoco_cli}")
        return CommandResult(ok=False, returncode=None)
    cmd = [
        "bash", str(script),
        "--project-root", str(project_root),
        module, target_class, test_class,
    ]
    if no_am:
        cmd.append("--no-am")
    return run_command(cmd, cwd=str(project_root), capture=False,
                       log_file=log_file,
                       timeout=timeout if timeout is not None else config.mvn_timeout())


def run_mvn_test_with_jacoco(
    project_root: str | Path,
    module: str,
    test_class: str,
    jacoco_version: str,
    log_file: str,
    *,
    state: Optional[State] = None,
    timeout: Optional[int] = None,
) -> CommandResult:
    """降级方案: 使用 mvn test jacoco:report 获取覆盖率。

    当 fast-single-cov.sh 因 pom.xml <argLine> 写死等原因失败时,
    回退到 jacoco-maven-plugin 自动注入 agent 的方式。

    Args:
        project_root: 项目根目录。
        module: Maven 模块名。
        test_class: 测试类简单名。
        jacoco_version: JaCoCo 版本。
        log_file: 日志文件路径。
        state: 状态对象(用于 jacoco 配置缓存)。
        timeout: 超时秒数。

    Returns:
        CommandResult: ok=True 表示 mvn 成功退出。
    """
    log = module_logger()
    cmd = build_mvn_cmd(project_root, test_classes=[test_class],
                        jacoco_version=jacoco_version, module=module,
                        state=state, include_dependencies=False)
    if cmd is None:
        log.error("未找到 mvn 可执行程序, 无法执行降级方案")
        return CommandResult(ok=False, returncode=None)
    log.info(f"降级方案 mvn test jacoco:report: {' '.join(cmd)}")
    return run_mvn(cmd, cwd=str(project_root), log_file=log_file,
                   timeout=timeout)


def _tail_file(path: str, *, lines: int = 30) -> str:
    """读取文件末尾 N 行, 文件不存在或为空时返回空字符串。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:]) if all_lines else ""
    except OSError:
        return ""


# fast-single-cov.sh 日志中表示「降级无意义」的关键字
_NON_FALLBACKABLE_PATTERNS = re.compile(
    r"编译失败"                       # mvn test-compile 失败(脚本输出)
    r"|mvn 执行失败"                  # surefire:test 失败(脚本输出)
    r"|COMPILATION ERROR"            # mvn 编译失败(Maven 原生输出, step4)
    r"|No tests were executed"       # 测试类未找到
    r"|缺少参数"                      # 参数校验失败
)

def _read_log(path: str) -> str:
    """读取日志文件全部内容, 文件不存在或读取失败返回空字符串。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _is_fallbackable_failure(log_file: str) -> bool:
    """检查 fast-single-cov.sh 的失败日志是否值得降级。

    编译失败、测试失败、测试类未找到等场景降级也会同样失败,
    只有 exec 文件未生成(argLine 写死)或工具缺失等场景降级才有意义。

    全文搜索而非尾部截取, 因为 COMPILATION ERROR 等关键字可能出现在
    Maven 输出的任意位置, 截取尾部会因错误详情行数不定而漏判。

    Returns:
        True 表示值得尝试降级; False 表示降级无意义。
    """
    content = _read_log(log_file)
    if not content:
        # 无日志内容(如 launch_failed), 视为可降级
        return True
    return not _NON_FALLBACKABLE_PATTERNS.search(content)


def run_single_cov_with_fallback(
    project_root: str | Path,
    module: str,
    target_class: str,
    test_class: str,
    log_file: str,
    *,
    no_am: bool = False,
    jacoco_version: str = config.DEFAULT_JACOCO_VERSION,
    state: Optional[State] = None,
    timeout: Optional[int] = None,
) -> tuple[CommandResult, str | None]:
    """获取单类覆盖率, fast-single-cov.sh 失败时自动降级到 mvn test jacoco:report。

    将日志备份与降级逻辑封装在一处, 避免 init_coverage / verify_coverage 重复实现。

    Args:
        project_root: 项目根目录。
        module: Maven 模块名。
        target_class: 目标类 FQCN。
        test_class: 测试类简单名。
        log_file: 日志文件路径。
        no_am: 是否跳过依赖模块编译。
        jacoco_version: JaCoCo 版本(降级方案需要)。
        state: 状态对象(降级方案传递给 build_mvn_cmd)。
        timeout: 超时秒数。

    Returns:
        (CommandResult, fast_cov_log_path):
            - result.ok=True 表示最终成功;
            - fast_cov_log_path 非 None 时为快速路径的日志备份路径。
    """
    log = module_logger()
    result = run_fast_single_cov(
        project_root, module, target_class, test_class, log_file,
        no_am=no_am, timeout=timeout)

    fast_cov_log_path: str | None = None
    if not result.ok:
        # 始终备份快速路径日志, 便于排查
        fast_cov_log_path = log_file + ".fast-single-cov.log"
        if Path(log_file).is_file():
            shutil.copy2(log_file, fast_cov_log_path)
            log.info(f"已备份快速路径日志: {fast_cov_log_path}")
        else:
            log.warning(f"快速路径日志文件不存在, 跳过备份: {log_file}")

        if not _is_fallbackable_failure(log_file):
            # 编译失败、测试失败、测试类未找到等场景降级也会同样失败
            tail = _tail_file(log_file, lines=30)
            log.error(
                "fast-single-cov.sh 失败且降级无意义(编译/测试问题)\n"
                "--- 日志(末尾 %d 行) ---\n%s"
                "--- 结束 ---", len(tail.splitlines()) if tail else 0, tail or "")
        else:
            # 可恢复失败(如 exec 未生成) -> 执行降级
            tail = _tail_file(log_file, lines=30)
            if tail:
                log.warning(
                    "fast-single-cov.sh 失败, 降级到 mvn test jacoco:report\n"
                    "--- fast-single-cov.sh 日志(末尾 %d 行) ---\n%s"
                    "--- 结束 ---", len(tail.splitlines()), tail)
            else:
                log.warning("fast-single-cov.sh 失败, 降级到 mvn test jacoco:report")
            clean_jacoco_dirs(project_root, [module])
            result = run_mvn_test_with_jacoco(
                project_root, module, test_class, jacoco_version, log_file,
                state=state, timeout=timeout)

    return result, fast_cov_log_path


# --------------------------------------------------------------------------- #
# 批量模式: 多模块命令构建
# --------------------------------------------------------------------------- #
def build_batch_install_cmd(project_root: str | Path, *,
                           modules: Sequence[str],
                           jacoco_version: str = config.DEFAULT_JACOCO_VERSION,
                           state: Optional[State] = None) -> Optional[list[str]]:
    """构建批量 mvn install -DskipTests 命令(多模块一次安装)。

    多模块时加 -pl m1,m2 -am; 单模块退化为 build_install_cmd。
    """
    mvn = resolve_tool("mvn")
    if mvn is None:
        return None
    log = module_logger()
    cmd = [mvn]
    if parse_jacoco_config(project_root, state=state):
        cmd += ["install", "-DskipTests"]
    else:
        coord = f"org.jacoco:jacoco-maven-plugin:{jacoco_version}"
        log.info(f"pom 未配置 jacoco-maven-plugin, 使用完整坐标挂载(版本 {jacoco_version})")
        cmd += [f"{coord}:prepare-agent", "install", "-DskipTests"]
    # 多模块: -pl m1,m2 -am(安装依赖模块)
    real_modules = [m for m in modules if m and m != "."]
    if real_modules and is_multi_module(project_root):
        cmd += ["-pl", ",".join(real_modules), "-am"]
    cmd += ["-Dmaven.test.failure.ignore=true", "-DfailIfNoTests=false"]
    return cmd


def build_batch_mvn_cmd(project_root: str | Path, *,
                       modules: Sequence[str],
                       test_classes: Optional[Sequence[str]] = None,
                       jacoco_version: str = config.DEFAULT_JACOCO_VERSION,
                           state: Optional[State] = None) -> Optional[list[str]]:
    """构建批量 mvn test + jacoco:report 命令。

    必须指定 -pl 模块列表; 可选 -Dtest 指定测试类列表。
    """
    mvn = resolve_tool("mvn")
    if mvn is None:
        return None
    log = module_logger()
    cmd = [mvn]
    if parse_jacoco_config(project_root, state=state):
        cmd += ["test", "jacoco:report"]
    else:
        coord = f"org.jacoco:jacoco-maven-plugin:{jacoco_version}"
        log.info(f"pom 未配置 jacoco-maven-plugin, 使用完整坐标挂载(版本 {jacoco_version})")
        cmd += [f"{coord}:prepare-agent", "test", f"{coord}:report"]
    # 必须指定 -pl 模块列表
    real_modules = [m for m in modules if m and m != "."]
    if real_modules:
        cmd += ["-pl", ",".join(real_modules)]
    # 可选 -Dtest 指定测试类列表(逗号分隔)
    if test_classes:
        cmd += [f"-Dtest={','.join(test_classes)}"]
    cmd += list(config.MVN_ALWAYS_FLAGS)
    return cmd


def _module_base(project_root: str | Path, module: str) -> str:
    return os.path.join(str(project_root), module) if module and module != "." else str(project_root)


def clean_jacoco_dirs(project_root: str | Path, modules: Sequence[str]) -> None:
    """mvn 前清理 target/site/jacoco 与 target/jacoco.exec, 防止误读陈旧报告/执行数据。"""
    log = module_logger()
    for m in modules:
        base = _module_base(project_root, m)
        jacoco_dir = os.path.join(base, "target", "site", "jacoco")
        if os.path.isdir(jacoco_dir):
            shutil.rmtree(jacoco_dir, ignore_errors=True)
            if os.path.isdir(jacoco_dir):
                log.warning(f"旧覆盖率报告清理失败(可能被占用): {jacoco_dir}")
            else:
                log.info(f"已清理旧覆盖率报告: {jacoco_dir}")
        # 删除陈旧执行数据, 防止 mvn 未重新采集时旧 exec 生成虚假达标报告
        jacoco_exec = os.path.join(base, "target", "jacoco.exec")
        if os.path.isfile(jacoco_exec):
            try:
                os.remove(jacoco_exec)
                log.info(f"已清理旧覆盖率执行数据: {jacoco_exec}")
            except OSError:
                log.warning(f"旧覆盖率执行数据清理失败(可能被占用): {jacoco_exec}")


def clean_surefire_dirs(project_root: str | Path, modules: Sequence[str]) -> list[str]:
    """mvn 前清理 target/surefire-reports, 防止误读上一轮陈旧测试结果报告。

    返回未清理成功的目录列表(空列表表示全部成功); 清理失败时本轮测试结果不可信。
    """
    log = module_logger()
    uncleaned: list[str] = []
    for m in modules:
        base = _module_base(project_root, m)
        surefire_dir = os.path.join(base, "target", "surefire-reports")
        if os.path.isdir(surefire_dir):
            shutil.rmtree(surefire_dir, ignore_errors=True)
            if os.path.isdir(surefire_dir):
                log.warning(f"旧测试报告清理失败(可能被占用): {surefire_dir}")
                uncleaned.append(surefire_dir)
            else:
                log.info(f"已清理旧测试报告: {surefire_dir}")
    return uncleaned
