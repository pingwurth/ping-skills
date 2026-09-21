"""sensitive-log-review 公共模块包。

集中管理所有审查脚本共享的能力：
- paths:       跨平台输出目录与产物路径（替代 /tmp 硬编码）
- git_utils:   git 调用、分支名校验、变更文件/新增行提取
- java_lexer:  Java 词法分析（注释/字面量屏蔽、字段/类迭代）
- pojo_config: pojo.config 唯一解析器与 POJO 文件判定
- dictionary:  分层词典加载（core/extended/blacklist/whitelist）
- text:        字段名拆分、路径规范化等文本工具
"""
