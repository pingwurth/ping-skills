# 门禁参考(validation.md)

本表与 `scripts/pwiki_lib/styles.py` 的 `STYLE_PRESETS` 同源 ——
`tests/test_styles.py::test_validation_doc_matches_presets` 断言本表 ⊆ 预设,
改数字必须两处同步(repowiki 教训: 文档列了代码没有的检查)。

## 双严重度模型

- **submit 阶段**: 真实性硬错(引用路径/行范围/H1/卡片 frontmatter)才拒绝;
  形状与尺寸全警告 —— 拒绝也保 checkpoint。
- **finish/LINING 阶段**: 按风格出错误级(见下); DOCUMENT_FAILED 可修稿重交。

## 尺寸表(按风格)

| 维度 | concise | deep |
|---|---|---|
| 页面行数 | 无下限(自然长度) | 400–520 |
| 词上限 | 3300(超限=警告) | — |
| mermaid 图 | 0–8 | 5–8(0 图=错误) |
| 引用链接(分档 D15) | 无分档, 上限 70 | thin leaf 40–70 / standard 60–90 / module 60–110 / 宽带 80–120 |
| 引用文件数 | ≥1 | 6–13 |
| 行级引用占比 | ≥85%(警告) | ≥85%(警告) |
| 章节来源/图表来源 | 缺失=警告 | 缺失=错误 |
| 知识层引用(D16) | 卡/sheet 0(证据走 source_files) | 卡 ≥3 行级、sheet ≥1(错误) |
| `<cite>` 块/目录/结论 | 禁止(writer 不写, 发布层注入) | cite 块必须自带且==正文集合; 结论节允许 |

分档判定(`styles.cite_band_for`): pack_key ∈ `config.WIDE_CITE_PACK_KEYS`
(architecture/ai/api) 或 type=overview → 80–120; module_sheet → 60–110;
source_files ≤2 → 40–70; 其余 → 60–90。

## 错误码一览(gates.gate_item / validate --content)

错误(submit 即拒 / LINING 即拒):
- `schema/h1` — 无 H1 或 H1 ≠ 计划标题(page/sheet; 卡片身份在 frontmatter)
- `citation/path` / `citation/range` — 引用路径不存在 / 行范围越界
- `citation/none` — 页面 0 条 file:// 引用
- `knowledge/frontmatter` — 卡片缺 frontmatter / kind、name 不符 /
  frontmatter 声明的 source_files 不存在
- `schema/frontmatter` / `schema/fence` — 页面带 frontmatter / 外层代码栅栏
- `citation/extra`(LINING 错误, submit 警告) — >10 个引用文件落在声明的
  source_files/scope 之外(glob scope 视为覆盖不罚)
- `links/internal` — `validate --content` 里相对 .md 内链不解析(错误级,
  断链导航不得随"已验证"契约发布)

风格错误(deep 在 LINING 升级为错误; concise 保持警告):
- `schema/toc` / `schema/cite` / `schema/wrapup`(concise 禁注入物)
- `schema/cite`(deep 必须自带 cite 块)
- `size/page` / `size/words` / `size/citations` / `size/cited-files` / `size/mermaid`
- `mermaid/type` / `mermaid/empty` / `mermaid/style`(图语法与样式)
- `sheet/links` / `sheet/shape`(concise 速查表形状)
- `sources/section` / `sources/diagram` / `knowledge/citations`(D16 按
  **行级**引用计数)
- `schema/sections` — 节数低于 required_sections 或类型用途清单

D16 的计数口径是行级引用(带 `#L` 范围); 无行号的整文件链接不计入
卡片/速查表的下限。章节枚举与"章节来源"判定都是栅栏感知的: 示例代码里
的 `## ` 不是节, 栅栏内的来源块不满足要求。

常驻警告(两风格, 不挡发布):
- `evidence/code` — 代码文件引用 <2, 纯文档证据不能佐证代码行为
- `size/cite-lines` — 行级引用占比 <85%(qwiki 曾是 LINING 硬错, 降为警告)

可修复(`validate --content --fix` 机械修复, 不新增事实; 导出层
compose_export_content 也对差一引用钳位 —— 只改导出文件, DB 存原稿):
- `citation/off-by-one`(D23) — end==行数+1 钳位到真实末行; 单行形式
  `[x](file://a.py#L5)` 在 4 行文件上同样钳到 #L4
- `sources/section`(D18) — 从该节现有行内引用生成 `**章节来源**` 块

行数口径(D23): `data.count(b"\n") + 末行无换行补 1`。`wc -l` 少算 —— 
writer 曾对 4 行文件引 L5。数文件行用 `grep -c ''`, 不要 `wc -l`。

## 检索门与修复环

`validate --content` 走发布树: errors 非零退出(exit 2), `--fix` 先机械修复
再复查; needs_review 列表不隐藏。修复环至多两轮定向 repair, 之后如实报告。
