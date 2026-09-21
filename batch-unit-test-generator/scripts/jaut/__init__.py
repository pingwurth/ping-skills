"""batch-unit-test-generator 脚本内部包。

分层(单向依赖, 禁止反向):
    L0 基础   : config, models, logutil, protocol
    L1 基础设施: proc, state, gitops
    L2 解析构建: jacoco, surefire, javasrc, maven, worktree
    L3 领域   : rules, prompt, decisions
    L4 编排   : transitions, cli
    L5 入口   : scripts/ 下的 6 个瘦 CLI 脚本

设计约束:
    - 纯标准库, 运行期不依赖任何第三方包。
    - 决策核心(decisions)为纯函数, 不做 I/O, 便于单测。
    - stdout 只允许 NEXT_STEP 协议块与显式计划摘要, 过程日志一律写文件。
"""

__all__ = [
    "config",
    "models",
    "logutil",
    "protocol",
]
