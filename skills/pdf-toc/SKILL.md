---
name: pdf-toc
description: 给 PDF 添加或替换目录（书签 / Outline），兼容文字版和扫描件，支持单个文件和整个文件夹。用户说“请给 xxx.pdf 增加目录”“请给 xxx 里的 pdf 文件增加目录”“给这本书加书签”“批量给扫描书加目录”时触发。Agent 自动检查环境、识别目录和页码、在工具中确认，默认生成带 .toc 后缀的新文件，绝不修改原文件。
license: MIT
compatibility: Requires Python 3.9+ and pypdf. Works in OpenCode, Claude Code, Cursor, Codex, Gemini CLI and other Agent Skills hosts.
metadata:
  version: "1.1.0"
---

# PDF 目录（书签）助手

用 `pypdf` 给 PDF 写入真实的 PDF Outline（阅读器左侧书签栏），不是往页面里画目录文字。

## 安装

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc
```

## 执行方式

用户只需要回答问题和确认结果，**不需要手动运行任何脚本**。你（Agent）负责：

1. 自动运行环境检查；
2. 自动探测 PDF 类型、目录页和页码；
3. 用提问工具让用户确认目录规则、页码映射和输出位置；
4. 执行写入并校验。

只有用户明确要求“把命令给我，我自己跑”时，才把命令交给用户。

## 触发示例

用户只需要说：

- “请给 `D:\books\Java 8实战.pdf` 增加目录”
- “请给 `D:\books\历史` 里的 PDF 文件增加目录”
- “给这本书加个书签”
- “根据这个目录文件生成 PDF 书签”

不要要求用户先运行 `doctor`、`inspect` 或任何脚本；这些都由你执行。

## 铁律

1. **先问关键信息**：文件、目录来源、页码含义、输出位置、已有书签处理方式。每个问题给推荐值和可选值，不要猜。
2. **绝不修改原文件**：只写新文件；默认同目录、`.toc` 后缀（`书名.pdf` → `书名.toc.pdf`）。
3. **先 dry-run，再写入，最后 verify**：三步都要做。
4. **扫描件不能编造页码**：没有文字层时，先渲染目录页/正文页，用视觉识别印刷页码并让用户确认偏移或 pageMap；无法识别就停下来问用户，不要猜。
5. **权限提示是正常的**：安装依赖、写文件时宿主会弹出授权。请用户核对后允许；被拒绝就停下来说明，不要绕过。
6. **不要删除或覆盖用户已有文件**，除非用户明确选择 `overwrite` 并再次确认。

## 脚本位置

本 Skill 的脚本位于**本 SKILL.md 同级目录下的 `scripts/`**：

```
<技能目录>/scripts/pdf_toc.py          # 主 CLI
<技能目录>/scripts/render_pages.py     # 可选：扫描件页面渲染
```

技能目录 = 包含本 `SKILL.md` 的目录。执行时使用绝对路径，不要依赖宿主的路径变量。下面用 `<PDF_TOC>` 表示实际命令前缀。

## 第 0 步：检查环境（每次触发都先做，自动安装）

每次调用本 skill 都先运行环境检查，不要跳过，也不要让用户手动执行。检查内容包括 Python 版本、pypdf 是否可用、输出目录是否可写。

先定位命令。按顺序：

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
- `capabilities.scannedPdf=true` 表示**能给扫描件写书签**；`scannedAutoCalibration=false` 表示**CLI 不内置 OCR**，扫描件目录/页码由 `render_pages.py` + Agent 视觉识别。

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

1. **文件范围**：单个 PDF（推荐）/ 多个 PDF / 整个文件夹
2. **PDF 路径 / 文件夹**：列出当前目录的 `*.pdf` 或子文件夹（排除 `*.toc*.pdf` 输出），推荐用户刚提到的目标；允许手动输入
3. **目录来源**：自动识别（推荐，见第 3 步）/ 已有目录文件 / 粘贴目录文本 / 使用 PDF 现有书签
4. **目录页码含义**：自动识别偏移（推荐）/ PDF 物理页 / 手动给偏移 / 逐页确认并生成 pageMap
5. **输出位置**：
   - 单个文件：原 PDF 同目录加后缀（推荐）/ 指定目录 / 指定文件名
   - 文件夹：镜像目录（推荐，保持结构）/ 同目录加后缀
6. **已有书签怎么处理**（仅 `outline.count > 0` 时）：替换（推荐）/ 追加合并 / 保留不动

扫描件追加问题（`text.scanned=true` 或 `offset.detected=null` 时）：

> 这是扫描件，没有文字层。我会先渲染目录页和正文前几页来识别。
> 页码映射请选：
> - 整体偏移（推荐）：识别一页的“印刷页码/物理页码”，例如第 6 页在 PDF 第 27 页 → 偏移 21
> - 逐页确认：我抽查每章首页，生成 pageMap
> - 目录里本来就是物理页 → `--page-mode physical`
>
> 识别结果会在写入前给你确认。

## 第 3 步：准备目录文件

### 3.1 用户没有提供目录时：自动识别

先判断 PDF 类型。

**文字版（`text.scanned=false`）**：读取前 40 页文字，找目录页。

```bash
<PDF_TOC> text "<pdf>" --pages 1-40 --json
```

- `tocCandidates` 是可能的目录页。
- 如果目录页文字排版可读，直接根据文字整理层级和页码。
- 如果文字层存在但目录页是图片，走扫描件流程。

**扫描件（`text.scanned=true`）**：把目录页渲染成图片，用视觉识别。

```bash
uv run --script "<技能目录>/scripts/render_pages.py" \
  "<pdf>" --pages 1-40 --out-dir "<临时目录>" --scale 2.0 --json
```

- 用当前宿主的图片读取工具（OpenCode 的 `read`、Claude Code 的图片读取等）查看生成的 PNG。
- 只读取目录页，禁止逐页浏览全书。
- 识别标题时保留原书文字，不翻译、不改写；不确定的字不要编造，标记后让用户确认。
- 整理成 JSON 或 Markdown 目录文件，写入临时目录。
- 如果无法读取图片，停止并请用户提供目录文本。

**已有书签**：`<PDF_TOC> extract "<pdf>" --out "<临时目录>/existing.toc.json"`，再让用户确认是否沿用/编辑。

识别完成后，把“目录页范围、条目数、层级、前 5 项”给用户确认。

### 3.2 页码映射：两种方式

目录页码必须能落到正确的物理页。先看 `inspect` 的结果：

- `offset.detected` 可用 → 默认整体偏移（推荐）。
- 扫描件 / 无偏移 → 需要识别一个锚点。

**方式 A：整体偏移（默认）**

1. 找一页同时能看到“印刷页码”和“PDF 物理页码”的页面：
   - 文字版：`inspect` 自动检测；或 `text` 看页眉/页脚。
   - 扫描件：用 `render_pages.py` 渲染目录页和正文前几页，视觉确认。
2. 计算 `偏移 = 物理页 - 印刷页`。
3. 在提问工具里让用户确认：
   - 使用检测到的偏移（推荐）
   - 手动输入偏移
   - 改用逐页映射
4. 写入时用 `--offset N` 或 `--anchor 印刷=物理`。

**方式 B：逐页确认 / 自定义映射**

适用于页码不连续、缺页、分卷重新编号，或用户要求逐项核对。

1. 抽查每章首页：根据目录里的印刷页码，找到对应物理页，渲染并确认该页确实是目标章节。
2. 生成 `pageMap` JSON（键=印刷页码，值=物理页码）：

```json
{
  "6": 27,
  "95": 116,
  "131": 152
}
```

3. 让用户确认映射表。
4. 写入时用 `--page-map "<pageMap.json>"`；未在映射表中的条目会按 `--offset` 或 `--page-mode` 处理。

两种方式都必须在提问工具中确认，不要静默选择。

### 3.3 目录格式

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
- 扫描件/整体偏移：`--offset 21` 或 `--anchor 6=27`
- 逐页映射：`--page-map "<pageMap.json>"`
- 已有书签追加：`--mode merge`

文件夹批量：

```bash
<PDF_TOC> batch "<文件夹>" --layout mirror --dry-run --json
<PDF_TOC> batch "<文件夹>" --layout alongside --dry-run --json
```

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

用户给的是**文件夹**时，先问输出结构：

1. **镜像目录（推荐）**：新建 `<源文件夹名><后缀>/`，保持源的目录结构和文件名不变。
2. **同目录加后缀**：每个 PDF 旁边生成 `<源文件名><后缀>.pdf`。

### 镜像目录（默认）

```bash
<PDF_TOC> batch "<源文件夹>" --layout mirror --dry-run --json
<PDF_TOC> batch "<源文件夹>" --layout mirror --json
```

- 默认输出到源文件夹同级的 `<源文件夹名><后缀>/`。
- 子目录结构、文件名保持不变。
- 用 `--out-dir` 指定镜像根目录。
- 输出目录如果位于源文件夹内部，会被自动排除，避免二次处理。
- 文件夹默认递归处理子目录；只处理第一层用 `--no-recursive`。
- 如果每本 PDF 的页码偏移不同，改用 manifest：`sourceRoot` 指向源文件夹，逐项写 `offset` / `pageMap`，`layout: "mirror"`。

### 同目录加后缀

```bash
<PDF_TOC> batch "<源文件夹>" --layout alongside --dry-run --json
<PDF_TOC> batch "<源文件夹>" --layout alongside --json
```

### manifest（跨目录/逐项配置）

```json
{
  "sourceRoot": "books",
  "defaults": { "mode": "replace", "suffix": ".toc", "layout": "mirror" },
  "items": [
    { "pdf": "book-a.pdf", "toc": "book-a.toc.json", "pageMode": "physical" },
    { "pdf": "sub/book-b.pdf", "toc": "sub/book-b.toc.md", "offset": 21 },
    { "pdf": "book-c.pdf", "toc": "book-c.toc.json", "pageMap": { "6": 27, "95": 116 } }
  ]
}
```

`sourceRoot` 指定镜像源根目录；适合不同 PDF 需要不同偏移/映射的批量任务。

```bash
<PDF_TOC> batch manifest.json --dry-run --json
<PDF_TOC> batch manifest.json --json
```

- 每个 PDF 必须能找到同名 `.toc.json/.toc.md/.toc.txt`（或 `--toc-dir` 下同名文件）。
- 找不到目录的 PDF 计入 `skipped`，默认让整体 `ok=false`；可忽略时加 `--allow-skipped`。
- 单项失败默认继续；需要立即停止用 `--stop-on-error`。
- 报告每个文件的成功/失败/跳过，不要隐藏失败。

## 输出确认

写入前必须让用户确认：

- **单个文件**：完整输出路径（默认同目录 `<原名><后缀>.pdf`）。
- **文件夹 / 镜像**：输出根目录 + 目录结构是否保持。
- **文件夹 / 同目录**：每个文件旁边的新文件名。
- **目录规则**：条目数、层级、页码映射方式（整体偏移 / pageMap）、已有书签处理方式。

确认后再去掉 `--dry-run` 执行。

## 异常处理对照表

| 错误码 | 含义 | 处理 |
|---|---|---|
| `MISSING_DEP` | 没有 pypdf | `uv run --script` 或 `python -m pip install pypdf` |
| `PDF_ENCRYPTED` | PDF 加密 | 让用户先解密；不要绕过加密 |
| `PDF_PARSE_FAILED` / `INVALID_PDF` | 损坏/不是 PDF | 确认文件、重新下载 |
| `TOC_EMPTY` | 目录为空 | 检查格式；每行要有标题和页码 |
| `TOC_JSON_INVALID` / `TOC_JSON_SHAPE` | JSON 语法/结构错误 | 按示例修正；中文引号也会失败 |
| `TOC_PAGE_MISSING` / `TOC_PAGE_INVALID` | 缺页码/页码非法 | 补正整数页码；罗马页码改物理页 |
| `TOC_PAGE_OUT_OF_RANGE` | 页码越界 | 确认物理/印刷页；检查 offset/anchor/pageMap |
| `PAGE_MAP_NOT_FOUND` / `PAGE_MAP_INVALID` | pageMap 文件缺失/格式错 | 检查 JSON 键值都是正整数 |
| `PAGE_SPEC_INVALID` | 页码范围格式错 | 用 `1-30` 或 `1,5,9-12` |
| `RENDER_FAILED` | 页面渲染失败 | 检查 PDF 是否加密/损坏，或降低 --scale |
| `OFFSET_UNCERTAIN` / `SCANNED_NO_TEXT` | 无法自动校准 | `--offset`、`--anchor`、`--page-map` 或 `--page-mode physical` |
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
<PDF_TOC> text <pdf> [--pages 1-40] [--json]
<PDF_TOC> extract <pdf> --out <file> [--format json|md] [--force]
<PDF_TOC> add <pdf> --toc <file> [--out ... | --out-dir ...] [--suffix .toc] [--dry-run]
<PDF_TOC> batch <目录|manifest.json> [--layout mirror|alongside] [--dry-run] [--json]
<PDF_TOC> verify <pdf> [--expect-count N] [--json]

# 扫描件/目录页渲染（可选脚本，uv 自动装 pypdfium2 + Pillow）
uv run --script "<技能目录>/scripts/render_pages.py" <pdf> --pages 1-40 --out-dir <临时目录> --json
```

`add` 关键选项：

| 选项 | 说明 |
|---|---|
| `--mode replace\|merge\|keep` | 已有书签处理，默认 `replace` |
| `--page-mode auto\|physical\|printed` | 页码含义，默认 `auto` |
| `--offset N` | 物理页 = 印刷页 + N |
| `--anchor 印刷=物理` | 锚点校准，可重复 |
| `--page-map <file>` | 自定义页码映射，适合不连续页码 |
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
