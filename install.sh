#!/usr/bin/env bash
# install.sh — 将脚本所在目录的 skill 安装到指定项目
# 支持入参：qwen, qoder, opencode, claude, all
# 无入参时交互式多选，然后询问项目目录

set -euo pipefail

# ── 脚本所在目录（即 skills 目录） ──────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 可选的工具列表及其对应的目录名 ────────────────────────────────
TOOLS=("claude" "qwen" "qoder" "opencode")

# ── 扫描脚本目录下所有含 SKILL.md 的 skill 子目录（排除隐藏目录） ──
discover_skills() {
    local skills=()
    for d in "$SCRIPT_DIR"/*/; do
        [ -d "$d" ] || continue
        # skill 的判定标准：目录内必须存在 SKILL.md
        [ -f "$d/SKILL.md" ] || continue
        local name
        name="$(basename "$d")"
        # 跳过隐藏目录和非目录文件
        [[ "$name" == .* ]] && continue
        [[ "$name" == "agents" ]] && continue
        skills+=("$name")
    done
    echo "${skills[@]}"
}

# ── 解析工具入参，返回选中的工具列表 ──────────────────────────────
parse_tools_arg() {
    local arg="$1"
    if [[ "$arg" == "all" ]]; then
        echo "${TOOLS[*]}"
        return
    fi
    # 逗号分隔或空格分隔
    local selected=()
    IFS=', ' read -ra parts <<< "$arg"
    for part in "${parts[@]}"; do
        part="$(echo "$part" | xargs)" # trim
        local found=false
        for t in "${TOOLS[@]}"; do
            if [[ "$t" == "$part" ]]; then
                found=true
                selected+=("$t")
                break
            fi
        done
        if ! $found; then
            echo "错误: 未知工具 '$part'，可选值: ${TOOLS[*]}" >&2
            exit 1
        fi
    done
    echo "${selected[@]}"
}

# ── 交互式选择工具（支持多选） ──────────────────────────────────────
# 菜单提示输出到 stderr，仅最终结果输出到 stdout（供命令替换捕获）
prompt_tools() {
    echo "" >&2
    echo "请选择要安装的目标工具：" >&2
    echo "  1) all        — 全部" >&2
    for i in "${!TOOLS[@]}"; do
        echo "  $((i+2))) ${TOOLS[$i]}" >&2
    done
    echo "" >&2
    read -rp "输入编号，多个用空格分隔（如 1 或 2 3）: " choice </dev/tty
    if [[ -z "$choice" ]]; then
        echo "错误: 未选择任何工具" >&2
        exit 1
    fi
    # 如果选了 all
    if [[ "$choice" == "1" ]] || [[ "$choice" == "all" ]]; then
        echo "${TOOLS[*]}"
        return
    fi
    local selected=()
    IFS=', ' read -ra nums <<< "$choice"
    for num in "${nums[@]}"; do
        num="$(echo "$num" | xargs)"
        if [[ "$num" =~ ^[0-9]+$ ]] && (( num >= 2 && num <= ${#TOOLS[@]}+1 )); then
            selected+=("${TOOLS[$((num-2))]}")
        else
            echo "警告: 忽略无效选项 '$num'" >&2
        fi
    done
    if [[ ${#selected[@]} -eq 0 ]]; then
        echo "错误: 未选择任何有效工具" >&2
        exit 1
    fi
    echo "${selected[@]}"
}

# ── 交互式选择 skill（支持多选） ──────────────────────────────────
# 菜单提示输出到 stderr，仅最终结果输出到 stdout（供命令替换捕获）
prompt_skills() {
    local skills=("$@")
    echo "" >&2
    echo "请选择要安装的 skill：" >&2
    echo "  1) all — 全部" >&2
    for i in "${!skills[@]}"; do
        echo "  $((i+2))) ${skills[$i]}" >&2
    done
    echo "" >&2
    read -rp "输入编号，多个用空格分隔（如 1 或 2 3）: " choice </dev/tty
    if [[ -z "$choice" ]]; then
        echo "错误: 未选择任何 skill" >&2
        exit 1
    fi
    if [[ "$choice" == "1" ]] || [[ "$choice" == "all" ]]; then
        echo "${skills[@]}"
        return
    fi
    local selected=()
    IFS=', ' read -ra nums <<< "$choice"
    for num in "${nums[@]}"; do
        num="$(echo "$num" | xargs)"
        if [[ "$num" =~ ^[0-9]+$ ]] && (( num >= 2 && num <= ${#skills[@]}+1 )); then
            selected+=("${skills[$((num-2))]}")
        else
            echo "警告: 忽略无效选项 '$num'" >&2
        fi
    done
    if [[ ${#selected[@]} -eq 0 ]]; then
        echo "错误: 未选择任何有效 skill" >&2
        exit 1
    fi
    echo "${selected[@]}"
}

# ── 删除 .md 文件中第一个出现的 `tools: .*` 行 ──────────────────────
remove_first_tools_line() {
    local file="$1"
    [ -f "$file" ] || return 0
    # 仅删除第一个匹配行（兼容 macOS 和 Linux）
    # GNU sed 支持 0,addr 范围；macOS sed 不支持，使用 perl 替代
    if sed --version &>/dev/null; then
        sed -i '0,/^tools: .*/{/^tools: .*/d}' "$file"
    else
        perl -i -ne 'print unless !$done && /^tools: .*/ && ($done = 1)' "$file"
    fi
}

# ── 安装单个 skill 到指定工具的目录 ──────────────────────────────────
install_skill() {
    local skill_name="$1"
    local tool="$2"
    local project_dir="$3"
    local need_strip_tools="$4" # true/false

    local src="$SCRIPT_DIR/$skill_name"
    local target_base="$project_dir/.$tool/skills"
    local target="$target_base/$skill_name"

    # 检查源目录是否存在
    if [[ ! -d "$src" ]]; then
        echo "  警告: skill 目录不存在 '$src'，跳过" >&2
        return
    fi

    # 检查目标是否已存在同名 skill
    if [[ -d "$target" ]]; then
        read -rp "  目标已存在 '$target'，是否覆盖？(y/N): " overwrite </dev/tty
        if [[ "$overwrite" != "y" && "$overwrite" != "Y" ]]; then
            echo "  跳过 $skill_name（已存在）"
            return
        fi
        rm -rf "$target"
    fi

    # 创建目标目录并复制 skill
    mkdir -p "$target_base"
    cp -r "$src" "$target"
    echo "  已复制 $skill_name -> $target"

    # 删除测试、缓存与随装无意义的文件（docs/ 是仓库内文档，不装进目标项目）
    find "$target" -name "test.log" -or -name "__pycache__" -or -name "tests" -or -name ".pytest_cache" -or -name "docs" | xargs rm -rf

    # workflows/ 只有带 Workflow 工具的宿主（claude）用得上
    if [[ "$tool" != "claude" ]]; then
        rm -rf "$target/workflows"
    fi

    # 如果不是 qoder/qwen，删除 SKILL.md 中第一个 tools: 行
    if $need_strip_tools; then
        local skill_md="$target/SKILL.md"
        if [[ -f "$skill_md" ]]; then
            remove_first_tools_line "$skill_md"
            echo "  已移除 $skill_md 中的 tools 行"
        fi
    fi

    # 处理 agents 目录（如果存在）——平铺进共享的 .<tool>/agents/，同名覆盖前先询问
    local agents_src="$src/agents"
    if [[ -d "$agents_src" ]]; then
        local agents_target="$project_dir/.$tool/agents"
        mkdir -p "$agents_target"
        local md_file agent_file
        for md_file in "$agents_src"/*.md; do
            [[ -f "$md_file" ]] || continue
            agent_file="$agents_target/$(basename "$md_file")"
            if [[ -f "$agent_file" ]]; then
                # 无 tty（CI/脚本调用）时 read 失败不致命：默认不覆盖，继续安装
                overwrite=""
                read -rp "  agents 已存在 '$(basename "$md_file")'，是否覆盖？(y/N): " overwrite </dev/tty || true
                if [[ "$overwrite" != "y" && "$overwrite" != "Y" ]]; then
                    echo "  跳过 $(basename "$md_file")（已存在）"
                    continue
                fi
            fi
            cp "$md_file" "$agent_file"
        done
        echo "  已复制 agents -> $agents_target"

        # 仅 opencode 删除 agents 中 .md 文件的第一个 tools: 行
        # （claude/qwen/qoder 的 agent frontmatter 支持 tools，保留作为硬限制）
        if [[ "$tool" == "opencode" ]]; then
            find "$agents_target" -name '*.md' -type f | while read -r md_file; do
                remove_first_tools_line "$md_file"
            done
            echo "  已移除 agents/*.md 中的 tools 行"
        fi
    fi
}

# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

# 第一步：确定要安装的工具
selected_tools=()
if [[ $# -ge 1 ]]; then
    read -ra selected_tools <<< "$(parse_tools_arg "$1")"
else
    read -ra selected_tools <<< "$(prompt_tools)"
fi

# 第二步：选择要安装的 skill
all_skills=()
read -ra all_skills <<< "$(discover_skills)"

if [[ ${#all_skills[@]} -eq 0 ]]; then
    echo "错误: 在 '$SCRIPT_DIR' 中未找到任何含 SKILL.md 的 skill 目录" >&2
    exit 1
fi

selected_skills=()
if [[ $# -ge 2 ]]; then
    # 第二个参数指定 skill（可选）
    IFS=', ' read -ra selected_skills <<< "$2"
else
    read -ra selected_skills <<< "$(prompt_skills "${all_skills[@]}")"
fi

# 第三步：询问项目目录
echo ""
if [[ $# -ge 3 ]]; then
    project_dir="$3"
else
    read -rp "请输入项目目录路径: " project_dir
fi

# 验证项目目录
if [[ -z "$project_dir" ]]; then
    echo "错误: 项目目录不能为空" >&2
    exit 1
fi

# 支持 ~ 展开
project_dir="${project_dir/#\~/$HOME}"

if [[ ! -d "$project_dir" ]]; then
    read -rp "目录 '$project_dir' 不存在，是否创建？(y/N): " create_dir
    if [[ "$create_dir" == "y" || "$create_dir" == "Y" ]]; then
        mkdir -p "$project_dir"
    else
        echo "已取消" >&2
        exit 1
    fi
fi

# 转为绝对路径
project_dir="$(cd "$project_dir" && pwd)"

# 第四步：执行安装
echo ""
echo "═══════════════════════════════════════════════════"
echo " 工具: ${selected_tools[*]}"
echo " Skills: ${selected_skills[*]}"
echo " 项目目录: $project_dir"
echo "═══════════════════════════════════════════════════"
echo ""

for tool in "${selected_tools[@]}"; do
    # qoder 和 qwen 保留 tools 行；claude 和 opencode 需要删除
    need_strip=true
    if [[ "$tool" == "qoder" || "$tool" == "qwen" ]]; then
        need_strip=false
    fi

    echo "▶ 安装到 .$tool/ ..."
    for skill in "${selected_skills[@]}"; do
        install_skill "$skill" "$tool" "$project_dir" "$need_strip"
    done
    echo ""
done

echo "✅ 安装完成！"
