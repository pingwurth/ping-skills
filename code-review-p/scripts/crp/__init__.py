"""code-review-p 确定性引擎(crp)内部包。

分层(单向依赖, 禁止反向):
    L0 基础   : config, models, logutil, protocol
    L1 基础设施: gitops, state
    L2 领域   : scope, overlay, ledger, verify, journal, report
    L3 决策   : decisions(纯函数, 零 I/O)
    L4 编排   : transitions, handlers, cli

设计约束:
    - 纯标准库, 运行期不依赖任何第三方包。
    - 决策核心(decisions)为纯函数, 不做 I/O, 便于单测。
    - 被审仓库绝对只读: 一切写入只落运行目录(仓库之外的 state home)。
    - stdout 只允许 NEXT_STEP 协议块, 过程日志一律写 <run-dir>/logs/。
"""

__all__ = ["config", "models", "logutil", "protocol"]
