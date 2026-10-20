---
name: pdf-toc
description: 给 PDF 添加或替换目录（书签 / Outline），兼容文字版和扫描件，支持单个文件和批量处理。默认生成带 .toc 后缀的新文件，绝不修改原文件。适用于“给 PDF 加目录/书签/导航”“根据目录生成 PDF 书签”“批量给扫描书加目录”“修复/合并 PDF 目录”等请求。
license: MIT
compatibility: Requires Python 3.9+ and pypdf. Works in OpenCode, Claude Code, Cursor, Codex, Gemini CLI and other Agent Skills hosts.
metadata:
  version: "1.0.0"
---

# PDF 目录（书签）助手

用 `pypdf` 给 PDF 写入真实的 PDF Outline（阅读器左侧书签栏），不是往页面里画目录文字。

## 铁律

1. **先问关键信息**：文件、目录来源、页码含义、输出位置、已有书签处理方式。每个问题给推荐值和可选值，不要猜。
2. **绝不修改原文件**：只写新文件；默认同目录、`.toc` 后缀（`书名.pdf` → `书名.toc.pdf`）。
3. **先 dry-run，再写入，最后 verify**：三步都要做。
4. **扫描件不假装能自动校准**：扫描件没有文字层，无法自动检测“印刷页码 → PDF 物理页”。必须让用户给偏移/锚点，或确认目录里是物理页。
5. **权限提示是正常的**：安装依赖、写文件时宿主会弹出授权。请用户核对后允许；被拒绝就停下来说明，不要绕过。
6. **不要删除或覆盖用户已有文件**，除非用户明确选择 `overwrite` 并再次确认。

## 脚本位置

本 Skill 的脚本位于**本 SKILL.md 同级目录下的 `scripts/`**：

```
<技能目录>/scripts/pdf_toc.py
```

技能目录 = 包含本 `SKILL.md` 的目录。执行时使用绝对路径，不要依赖宿主的路径变量。

下面用 `<PDF_TOC>` 表示实际命令前缀，按顺序选择：

```bash
# 1) 有 uv：自动创建临时环境并安装 pypdf，零污染（推荐）
uv run --script "<技能目录>/scripts/pdf_toc.py"

# 2) 项目里有 .venv 且已装 pypdf
#    Windows:
"<项目>/.venv/Scripts/python.exe" "<技能目录>/scripts/pdf_toc.py"
#    macOS/Linux:
"<项目>/.venv/bin/python" "<技能目录>/scripts/pdf_toc.py"

# 3) 当前 Python 已装 pypdf
python "<技能目录>/scripts/pdf_toc.py"
```

先运行：

```bash
<PDF_TOC> doctor --json
```

- `ready: true` → 继续。
- `ready: false` → 运行 `<PDF_TOC> doctor --fix --json`（会尝试 `pip install pypdf`），或按 `install` 字段提示安装。
- 需要安装时告诉用户：“接下来会安装 pypdf，界面可能弹出授权，请核对后允许。”
- 仍失败：原样报告 `missing` 和错误，停止。不要 `sudo`，不要改全局环境。
- `capabilities.scannedPdf=true` 表示**能给扫描件写书签**；`scannedAutoCalibration=false` 表示**不会 OCR 自动找页码**。

## 第 1 步：检查 PDF

```bash
<PDF_TOC> inspect "<pdf>" --json
```

重点读：

- `pages`：总页数
- `text.scanned` / `text.ratio`：是否扫描件
- `outline.count`：已有书签数
- `pageLabels.present`：PDF 是否自带页码标签
- `offset.detected` / `offset.confidence` / `offset.source`：印刷页码偏移
  - `source=page-labels` 或 `confidence=high/medium` 可用
  - `confidence=low` **不要直接用**，问用户
- `recommendations`：转述关键建议

## 第 2 步：用提问工具收集信息

使用当前宿主的提问工具：
- OpenCode：`question` 工具（`questions` 数组）
- Claude Code：`AskUserQuestion`
- 都没有：普通对话逐项问

每个问题把推荐项放第一位，label 加“（推荐）”。一次不超过 4 个问题，可分两轮。

必问：

1. **文件范围**：单个 PDF（推荐）/ 多个 PDF / 整个目录
2. **PDF 路径**：列出当前目录 `*.pdf`（排除 `*.toc*.pdf`），推荐用户刚提到的文件；允许手动输入
3. **目录来源**：已有目录文件（推荐）/ 粘贴目录文本 / 使用 PDF 现有书签（`extract` 导出）/ 没有目录
4. **目录页码含义**：印刷页码自动校准（推荐）/ PDF 物理页 / 手动给偏移
5. **输出位置**：原 PDF 同目录加 `.toc` 后缀（推荐）/ 指定目录 / 指定文件名
6. **已有书签怎么处理**（仅 `outline.count > 0` 时）：替换（推荐）/ 追加合并 / 保留不动

扫描件追加问题（`text.scanned=true` 或 `offset.detected=null` 时）：

> 这是扫描件，没有文字层，无法自动校准页码。请任选：
> - 给锚点：书上第 X 页 = PDF 第 Y 页，例如 `--anchor 6=27`（推荐）
> - 给整体偏移：物理页 = 印刷页 + N，例如 `--offset 21`
> - 目录页码本来就是 PDF 物理页 → `--page-mode physical`
>
> 查看方法：翻到有印刷页码的那一页，看页码和阅读器页码。

## 第 3 步：准备目录文件

支持 JSON、Markdown、纯文本。页码必须是正整数；层级用缩进或 `children` 表示。

### JSON

```json
{
  "pageMode": "physical",
  "entries": [
    {
      "title": "第一章 概述",
      "page": 12,
      "children": [
        { "title": "1.1 背景", "page": 12 },
        { "title": "1.2 目标", "page": 18 }
      ]
    },
    { "title": "第二章 实现", "page": 35 }
  ]
}
```

- `pageMode`: `physical`（PDF 物理页）或 `printed`（印刷页）；也可省略，由命令行决定。
- `page` 可写成 `pageNumber`；子项可用 `children` / `items` / `sub`。
- 从现有 PDF 导出：`<PDF_TOC> extract "<pdf>" --out "<pdf>.toc.json"`

### Markdown / 纯文本

```markdown
第一章 概述 ................ 12
  1.1 背景 .................. 12
  1.2 目标 .................. 18
第二章 实现 ................ 35
```

规则：
- 行尾最后一个数字是页码；点线、制表符、空格都可以。
- 缩进（2/4 空格或 Tab）决定层级；没有缩进时按 `1.1`、`第一章/第一节` 推断。
- 没有页码的“第一卷/第X篇”会使用下一项的页码；没有页码的附图行会跳过并给 `SKIPPED_NO_PAGE` 警告。
- 用户粘贴目录时，写到**临时文件**（例如系统临时目录），不要写进原 PDF 目录，除非用户同意。

## 第 4 步：dry-run 预览（必做）

```bash
<PDF_TOC> add "<pdf>" --toc "<toc>" \
  --mode replace \
  --page-mode auto \
  --suffix .toc \
  --dry-run --json
```

常用页码参数：

- 文字版印刷页码：`--page-mode auto`（默认，自动用检测到的偏移）
- 明确物理页：`--page-mode physical`
- 扫描件/手动校准：`--offset 21` 或 `--anchor 6=27`
- 已有书签追加：`--mode merge`

检查 JSON：

- `ok` 为 `true`
- `entries > 0`，`levels` 合理
- `pageMode` / `offset` 符合用户选择
- `preview[].physicalPage` 在 1..`pages` 内
- `warnings` 逐条看：
  - `TOC_PAGE_OUT_OF_RANGE` → 页码/偏移错，修正后重跑
  - `OFFSET_UNCERTAIN` / `SCANNED_NO_TEXT` → 问用户要 offset/anchor/physical
  - `SKIPPED_NO_PAGE` / `PAGE_INFERRED` / `LEVEL_CLAMPED` → 向用户确认
  - `OUTLINE_REPLACED` → 确认用户知道原书签会被替换
  - `DUPLICATE_ENTRY` → 确认是否保留

把“输出路径 + 书签数量 + 页码模式 + 前 5 项预览 + 警告”简要给用户，问“是否写入？”确认后再执行。

## 第 5 步：写入新文件

```bash
<PDF_TOC> add "<pdf>" --toc "<toc>" ... --json
```

默认输出到原 PDF 同目录，文件名 `<原名>.toc.pdf`。输出已存在时默认自动改名（`-2`、`-3`），不会覆盖。

## 第 6 步：校验（必做）

```bash
<PDF_TOC> verify "<output>" --expect-count <N> --json
```

- `ok: true` → 交付。
- `ok: false` → 不交付；保留文件排查，报告 `unresolved` / `outOfRange`，建议换 `--mode replace` 或检查目录。
- 写入时默认已自动校验；这里再独立校验一次。

## 批量处理

### manifest（推荐）

```json
{
  "defaults": { "mode": "replace", "suffix": ".toc" },
  "items": [
    { "pdf": "书A.pdf", "toc": "书A.toc.json", "pageMode": "physical" },
    { "pdf": "书B.pdf", "toc": "书B.toc.md", "offset": 21 }
  ]
}
```

```bash
<PDF_TOC> batch manifest.json --dry-run --json
<PDF_TOC> batch manifest.json --json
```

### 目录配对

```
books/
  书A.pdf
  书A.toc.md
  书B.pdf
  书B.toc.json
```

```bash
<PDF_TOC> batch "books" --dry-run --json
<PDF_TOC> batch "books" --json
```

- 每个 PDF 必须能找到同名 `.toc.json/.toc.md/.toc.txt`（或 `--toc-dir` 下同名文件）。
- 找不到目录的 PDF 计入 `skipped`，默认让整体 `ok=false`；可忽略时加 `--allow-skipped`。
- 单项失败默认继续；需要立即停止用 `--stop-on-error`。
- 报告每个文件的成功/失败/跳过，不要隐藏失败。

## 异常处理对照表

| 错误码 | 含义 | 处理 |
|---|---|---|
| `MISSING_DEP` | 没有 pypdf | `uv run --script` 或 `python -m pip install pypdf` |
| `PDF_ENCRYPTED` | PDF 加密 | 让用户先解密；不要绕过加密 |
| `PDF_PARSE_FAILED` / `INVALID_PDF` | 损坏/不是 PDF | 确认文件、重新下载 |
| `TOC_EMPTY` | 目录为空 | 检查格式；每行要有标题和页码 |
| `TOC_JSON_INVALID` / `TOC_JSON_SHAPE` | JSON 语法/结构错误 | 按示例修正；中文引号也会失败 |
| `TOC_PAGE_MISSING` / `TOC_PAGE_INVALID` | 缺页码/页码非法 | 补正整数页码；罗马页码改物理页 |
| `TOC_PAGE_OUT_OF_RANGE` | 页码越界 | 确认物理/印刷页；检查 offset/anchor |
| `OFFSET_UNCERTAIN` / `SCANNED_NO_TEXT` | 无法自动校准 | `--offset`、`--anchor` 或 `--page-mode physical` |
| `OUTLINE_EXISTS` | `--mode keep` 但已有书签 | 问用户 replace / merge / keep |
| `OUTPUT_EXISTS` | 输出已存在且 `--on-exists error` | 默认 rename，或确认后 overwrite |
| `OUTPUT_IS_INPUT` | 输出指向原文件 | 换输出名；绝不覆盖原文件 |
| `WRITE_FAILED` / `DIR_NOT_WRITABLE` | 写不进去 | 换目录/检查磁盘；不要提权硬写 |
| `VERIFY_FAILED` | 写完校验不通过 | 不交付；保留文件，检查目录和 mode |
| `BATCH_NO_PDF` | 目录里没有 PDF | 检查路径/递归开关 |
| 命令被拒绝 | 用户拒绝授权 | 停止并说明需要哪条命令；不要绕过 |

## 命令速查

```bash
<PDF_TOC> doctor [--json] [--fix]
<PDF_TOC> inspect <pdf> [--json]
<PDF_TOC> extract <pdf> --out <file> [--format json|md] [--force]
<PDF_TOC> add <pdf> --toc <file> [--out ... | --out-dir ...] [--suffix .toc] [--dry-run]
<PDF_TOC> batch <目录|manifest.json> [--dry-run] [--json]
<PDF_TOC> verify <pdf> [--expect-count N] [--json]
```

`add` 关键选项：

| 选项 | 说明 |
|---|---|
| `--mode replace\|merge\|keep` | 已有书签处理，默认 `replace` |
| `--page-mode auto\|physical\|printed` | 页码含义，默认 `auto` |
| `--offset N` | 物理页 = 印刷页 + N |
| `--anchor 印刷=物理` | 锚点校准，可重复 |
| `--on-exists rename\|error\|overwrite` | 输出冲突策略，默认 `rename` |
| `--dry-run` | 只预览 |
| `--no-verify` | 跳过写入后自动校验 |
| `--no-open-panel` | 不设置打开 PDF 时显示书签栏 |

## 完成后的回复模板

```
已生成：<输出路径>
书签：<N> 个 / <L> 级
页码：<印刷页 +21 → 物理页 | PDF 物理页>
原文件：未修改
校验：通过（<N> 个书签，页面目标全部有效）
警告：<...>
```

如果用户要求“顺便覆盖原文件”，先说明风险；默认仍输出新文件。只有用户明确坚持时才使用 `--out <原文件> --on-exists overwrite`，并在写入前再次确认。
