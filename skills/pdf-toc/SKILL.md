---
name: pdf-toc
description: "PDF bookmarks / table of contents (TOC / Outline) generator for text and scanned PDFs, single file or folder batch. Triggers include “add a table of contents to xxx.pdf” and “add bookmarks to the PDFs in xxx folder”. 给 PDF 添加/替换目录：用户说“请给 xxx.pdf 增加目录”“请给 xxx 里的 pdf 文件增加目录”“给这本书加书签”“批量给扫描书加目录”时触发。Agent 自动读取目录和页码、在宿主中确认核对方式，生成新的带目录 PDF 并汇报结果，绝不修改原文件。"
license: MIT
compatibility: Requires Python 3.9+ and uv. Dependencies are installed by uv from the script's PEP 723 header.
metadata:
  version: "1.5.3"
---

# PDF 目录（书签）助手

只使用 `uv run --script` 运行，不需要用户手动执行脚本，也不使用 pip / pipx / 虚拟环境。

执行顺序：环境检查 → **提问并等待回答** → 扫描/读目录 → 生成 → 校验 → 汇报。任何宿主都不得跳过提问。

## 触发示例

- “请给 `D:\books\Java 8实战.pdf` 增加目录”
- “请给 `D:\books\历史` 里的 PDF 文件增加目录”
- “Add a table of contents to `D:\books\Java 8实战.pdf`”
- “Add bookmarks to the PDFs in `D:\books\history`”

中英文 TOC 都支持；标题按原文保留，不翻译。

## 语言

**用用户请求的语言回复。** 英文触发就用英文提问、英文状态、英文报告；中文触发就用中文。

- `Add a table of contents to ...` → English questions and English final report.
- `请给 ... 增加目录` → 中文提问和中文报告。
- 混合语言 → 跟随用户主要使用的语言；不确定时用英文。
- 绝对不要翻译 PDF 里的目录标题；书签标题保持原文。
- 命令和 JSON 字段不变；CLI 输出的中文提示由你翻译给用户。
- 始终用 `--json` 解析结果，不要直接把中文 CLI 输出贴给英文用户。

## 铁律

1. **只使用 uv**：所有命令都通过 `uv run --script "<技能目录>/scripts/pdf_toc.py"`。不要用 `python`、`pip`、`pipx` 或临时脚本。
2. **不写临时脚本**：不要用 `fitz` / PyMuPDF，也不要自己拼图；`scan` / `check-pages` 已经内置渲染和拼图。
3. **不改原文件**：只生成新文件，默认同目录加后缀。
4. **先确认，再生成（所有宿主）**：必须用宿主提问工具确认“页码核对方式”；如果是文件夹，再确认输出布局。没有提问工具就用文本问；用户回答前不得执行 `add` / `batch`。
5. **不重复读页**：`check-pages` 会按物理页去重，两个章节在同一页时只渲染一次。

## 第 0 步：环境检查

### 0.1 先检查 uv（uv 是引导工具，必须先有）

```bash
uv --version
```

- 有 → 进入 0.2。
- 没有 → 用提问工具问用户（按用户语言）：

  > 这个 skill 需要先安装 uv（只需一次）。安装会从 astral.sh 下载，可能弹出权限提示。是否安装？
  > - 安装 uv（推荐）
  > - 我自己安装
  > - 取消

  English user:

  > This skill needs `uv` once. The official installer downloads from astral.sh and may trigger a permission prompt. Install it now?
  > - Install uv (recommended)
  > - I'll install it myself
  > - Cancel

  用户同意后按平台执行官方安装脚本：

  ```powershell
  # Windows PowerShell
  powershell -NoProfile -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

  安装后如果当前 shell 还找不到 `uv`，使用安装器输出的路径，例如：
  - Windows：`%USERPROFILE%\.local\bin\uv.exe`
  - macOS/Linux：`~/.local/bin/uv`

  用户拒绝或安装失败 → 停止，不要改用 pip / pipx / 系统 Python。

### 0.2 检查 Python / pypdf / 渲染依赖

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" doctor --json
```

- `ready: true` → 继续。
- `ready: false` → 检查 `missing`。
  - 缺 Python：`uv run --script` 会按 `requires-python` 自动下载/使用 Python。
  - 缺 pypdf / pypdfium2 / pillow：`uv run --script` 会自动装到 uv 缓存，重新执行即可。
- 第一次运行会联网下载，宿主会弹出授权，请用户确认。

> 总结：**uv 需要先安装一次；Python 和 pypdf 等依赖由 uv 自动安装。**

## 第 1 步：提问（只问必要的）

**所有宿主都必须提问，禁止跳过。** 用当前 Agent 自带的提问/选择工具：
OpenCode `question`、Claude Code `AskUserQuestion`、Cursor / Codex / Gemini CLI 等宿主的同类工具。

- 只问两组；文件夹问题只在输入是文件夹时加入。
- 用户回答前，禁止执行 `add` / `batch`；不要自动选择推荐项。
- 如果宿主没有提问工具、调用失败，或用户看不到弹窗：立刻改用普通文本列出同样的选项，然后停止并等待回复。

### 中文提问模板

下面是 OpenCode `question` 的参数结构。其他宿主把同样的 `header` / `question` / `options` 映射到自己的提问工具；不要因为不是 OpenCode 就跳过提问。

```json
{
  "questions": [
    {
      "header": "页码核对",
      "question": "页码怎么核对？",
      "options": [
        { "label": "印刷页 + 偏移（推荐）", "description": "优点：最快，只读一页；适合页码连续、偏移固定的书。缺点：发现不了局部错页、缺页或目录页码错误；偏移错会导致全部书签偏移。" },
        { "label": "核对最后一页", "description": "优点：比逐页快，能确认整体偏移和末尾是否对齐。缺点：只抽查末尾，前面章节的局部错页发现不了；末尾是附录/索引时可能误判。" },
        { "label": "核对目录涉及的每一页", "description": "优点：最可靠，能发现局部错页、缺页和目录页码错误。缺点：最慢，需要渲染并查看较多页；大书耗时和 token 消耗高。" }
      ]
    },
    {
      "header": "文件夹输出",
      "question": "文件夹的输出方式？",
      "options": [
        { "label": "新建镜像文件夹（推荐）", "description": "新建 <源文件夹名><后缀>/，保持 PDF 文件名和目录结构" },
        { "label": "每个 PDF 同目录", "description": "输出到各自目录，文件名为 <源文件名><后缀>.pdf" }
      ]
    }
  ]
}
```

### English question template

Same `header` / `question` / `options` structure; map it to the host's question tool. Do not skip the questions just because the host is not OpenCode.

```json
{
  "questions": [
    {
      "header": "Page verification",
      "question": "How should I verify the page numbers?",
      "options": [
        { "label": "Printed page + offset (recommended)", "description": "Pros: fastest, reads one page; good for continuous numbering with a fixed offset. Cons: misses local page errors, missing pages, or wrong TOC numbers; a wrong offset shifts every bookmark." },
        { "label": "Verify the last page", "description": "Pros: faster than a full check; confirms the overall offset and the end of the book. Cons: samples only the last entry, so earlier local errors go unnoticed; an appendix/index ending can mislead." },
        { "label": "Verify every page in the TOC", "description": "Pros: most reliable; catches local page errors, missing pages, and wrong TOC numbers. Cons: slowest; renders and inspects many pages, so large books cost more time and tokens." }
      ]
    },
    {
      "header": "Folder output",
      "question": "How should I write the output for this folder?",
      "options": [
        { "label": "New mirror folder (recommended)", "description": "Create <source folder><suffix>/ and keep PDF names and directory structure" },
        { "label": "Next to each PDF", "description": "Write <source name><suffix>.pdf beside each original" }
      ]
    }
  ]
}
```

输入是文件夹时才加入文件夹问题；单个文件不要问。Claude Code、Cursor、Codex、Gemini CLI 等宿主使用相同的 `header` / `question` / `label` / `description`，字段名按宿主调整。

### 没有提问工具时的纯文本兜底

如果宿主没有提问工具，或调用失败、用户看不到弹窗，直接把下面的选项作为普通文本发给用户，然后停止等待回复；不要省略选项，也不要替用户选。

中文：

```text
页码怎么核对？
1. 印刷页 + 偏移（推荐）：最快，只读一页；适合页码连续、偏移固定。缺点：局部错页/缺页发现不了，偏移错会全部偏移。
2. 核对最后一页：比逐页快，能确认整体偏移和末尾。缺点：只抽查末尾，前面局部错页发现不了。
3. 核对目录涉及的每一页：最可靠，能发现局部错页/缺页。缺点：最慢，大书耗时和 token 高。
请回复 1 / 2 / 3。

如果是文件夹，再问：
文件夹输出方式？
1. 新建镜像文件夹（推荐）：保持 PDF 文件名和目录结构。
2. 每个 PDF 同目录：输出到原文件旁边。
请回复 1 / 2。
```

English:

```text
How should I verify the page numbers?
1. Printed page + offset (recommended): fastest, reads one page; good for continuous numbering and a fixed offset. Cons: misses local errors/missing pages; a wrong offset shifts everything.
2. Verify the last page: faster than a full check; confirms the overall offset and the end. Cons: samples only the end; earlier local errors go unnoticed.
3. Verify every page in the TOC: most reliable; catches local errors and missing pages. Cons: slowest; large books cost more time and tokens.
Reply 1 / 2 / 3.

If this is a folder, also ask:
Folder output?
1. New mirror folder (recommended): keep PDF names and directory structure.
2. Next to each PDF: write beside each original.
Reply 1 / 2.
```

用户回复后，按选择继续；不要重复问已经回答过的问题。

其他规则（不需要问）：

- 单个 PDF 默认同目录，`book.pdf → book.toc.pdf`。
- 已有书签默认替换；如果检测到旧书签，在报告里说明。
- 目录来源默认自动读取；只有读不到目录时才请用户提供文件/文本。

Final status/report must also be English for an English request, including warnings and `OFFSET_MISMATCH`.

## 第 2 步：读取文件数据（兼容扫描件）

一次扫描拿到 PDF 信息、目录页文字/图片和偏移量：

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" \
  scan "<pdf>" --pages 1-40 --out-dir "<临时目录>" --json
```

返回：

- `text.scanned`：文字版 / 扫描件
- `offset.detected`：自动检测的偏移
- `textPages` / `tocCandidates`：目录页文字
- `rendered.files`：扫描件的 6×3 拼图（每页标 `p.N`）

处理：

- 文字版：从 `textPages` / `tocCandidates` 解析目录。
- 扫描件：读取 `rendered.files` 图片，视觉识别目录。
- 文字版但目录页排版不可读：加 `--render` 强制渲染目录页。
- 已有书签：`extract` 导出。
- 如果前 40 页没有目录，调整 `--pages` 再 scan。
- 整理成 TOC JSON/Markdown（临时文件），页码默认按印刷页记录。

扫描件只需读目录页和少量正文页，不要逐页浏览全书。

## 第 3 步：核对页码

根据第 1 步的选择：

### 只用印刷页 + 偏移量

用 `scan` 的 `offset.detected`，或找一个“印刷页 ↔ 物理页”锚点：

```text
<OFFSET> = <物理页> - <印刷页>
```

```bash
--offset "<OFFSET>"
# 或
--anchor "<印刷页>=<物理页>"
```

**不要把示例里的 `21` 或 `6=27` 当成默认值。** 每本书、每个 PDF 的偏移都可能不同；必须来自当前 PDF 的 `scan` 或你核对过的一页。代码不会写死偏移；如果传入的 offset 与自动检测值冲突，报告里会出现 `OFFSET_MISMATCH` 警告。

### 核对最后一页

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" check-pages "<pdf>" \
  --toc "<toc>" --offset "<OFFSET>" --mode last --out-dir "<临时目录>" --json
```

读取输出的图片，确认页面上实际印刷页码 = `items[].printedPages`。

### 核对目录涉及的每一页

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" check-pages "<pdf>" \
  --toc "<toc>" --offset "<OFFSET>" --mode all --out-dir "<临时目录>" --json
```

- `items[]` 按物理页去重；同一页上的多个标题只出现一次。
- 读取 `rendered.files`（每张 6×3 拼图），逐页核对。
- 如果某些页不匹配，整理成 `pageMap.json`（下面是示例，不要照抄）：

```json
{ "<印刷页>": "<物理页>", "<印刷页>": "<物理页>" }
```

页码核对不通过时不要生成，先修正偏移或 pageMap。

## 第 4 步：生成带目录 PDF

执行前确认第 1 步已经得到用户明确选择；没有就回到第 1 步，不要生成。

### 单个文件

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" add "<pdf>" \
  --toc "<toc>" --offset "<OFFSET>" --json
```

需要逐页映射时：

```bash
--page-map "<pageMap.json>"
```

### 文件夹

```bash
# 新建文件夹（推荐）
uv run --script "<技能目录>/scripts/pdf_toc.py" batch "<文件夹>" \
  --layout mirror --offset "<OFFSET>" --json

# 每个 PDF 同目录
uv run --script "<技能目录>/scripts/pdf_toc.py" batch "<文件夹>" \
  --layout alongside --offset "<OFFSET>" --json
```

如果每本 PDF 偏移不同，用 manifest（`sourceRoot` + `layout` + `offset`/`pageMap`），见 [CLI.md](CLI.md)。

批量里的 `--offset` 是统一偏移；不同书不同偏移时必须用 manifest。

## 第 5 步：汇报结果（不写报告文件）

不生成 Markdown 报告文件。直接根据 `add` / `batch` 的 JSON 结果，在对话里汇报：

- 输入 / 输出
- 页数
- 书签数量和层级
- 页码模式（偏移 / pageMap / 物理页）
- 校验结果
- 警告
- 前几项预览

示例：

```text
已生成：D:\books\Java 8实战.toc.pdf
书签：313 个 / 5 级
页码：印刷页 +<OFFSET> → 物理页
原文件：未修改
校验：通过
```

## 速度原则

- 文字版只读文字，不渲染。
- 扫描件只渲染目录页和需要核对的页；`scan` / `check-pages` 自动拼成 6×3，一次读 18 页。
- `check-pages --mode all` 会按物理页去重，避免同一页读两次。
- 400 页扫描书，扫描 + 拼图约 2 秒；不要整本渲染。

## 常用命令

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" doctor --json
uv run --script "<技能目录>/scripts/pdf_toc.py" scan "<pdf>" --pages 1-40 --out-dir "<dir>" --json
uv run --script "<技能目录>/scripts/pdf_toc.py" check-pages "<pdf>" --toc "<toc>" --offset "<OFFSET>" --mode last|all --out-dir "<dir>" --json
uv run --script "<技能目录>/scripts/pdf_toc.py" add "<pdf>" --toc "<toc>" --offset "<OFFSET>" --json
uv run --script "<技能目录>/scripts/pdf_toc.py" batch "<dir>" --layout mirror|alongside --offset "<OFFSET>" --json
uv run --script "<技能目录>/scripts/pdf_toc.py" verify "<output>" --json
```

## 结果模板

```text
已生成：<输出路径>
书签：<N> 个 / <L> 级
页码：<偏移 / pageMap / 物理页>
原文件：未修改
校验：<通过 / 未通过>
```
