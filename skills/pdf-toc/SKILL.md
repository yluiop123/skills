---
name: pdf-toc
description: 给 PDF 添加或替换目录（书签 / Outline），兼容文字版和扫描件，支持单个文件和整个文件夹。用户说“请给 xxx.pdf 增加目录”“请给 xxx 里的 pdf 文件增加目录”“给这本书加书签”“批量给扫描书加目录”时触发。Agent 自动读取目录和页码、在工具中确认核对方式，生成新的带目录 PDF 和报告，绝不修改原文件。
license: MIT
compatibility: Requires Python 3.9+ and uv. Dependencies are installed by uv from the script's PEP 723 header.
metadata:
  version: "1.2.0"
---

# PDF 目录（书签）助手

只使用 `uv run --script` 运行，不需要用户手动执行脚本，也不使用 pip / pipx / 虚拟环境。

## 铁律

1. **只使用 uv**：所有命令都通过 `uv run --script "<技能目录>/scripts/pdf_toc.py"`。不要用 `python`、`pip`、`pipx` 或临时脚本。
2. **不写临时脚本**：不要用 `fitz` / PyMuPDF，也不要自己拼图；`scan` / `check-pages` 已经内置渲染和拼图。
3. **不改原文件**：只生成新文件，默认同目录加后缀。
4. **先确认，再生成**：用提问工具确认“页码核对方式”；如果是文件夹，再确认输出布局。
5. **不重复读页**：`check-pages` 会按物理页去重，两个章节在同一页时只渲染一次。

## 第 0 步：环境检查

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" doctor --json
```

- `ready: true` → 继续。
- `ready: false` → 检查 `missing`。
  - 缺 `uv`：请用户先安装 uv（https://docs.astral.sh/uv/），不要改用 pip。
  - 缺依赖：`uv run --script` 会自动安装，重新执行即可。
- 需要联网安装时，宿主会弹出授权，请用户确认。

## 第 1 步：提问（只问必要的）

用 OpenCode `question` / Claude Code `AskUserQuestion`。

### 问题 1：页码核对方式（所有情况都问）

- **只用印刷页 + 偏移量（推荐）**：读一页，算出 `物理页 = 印刷页 + 偏移`，不逐页核对。
- **核对最后一页**：生成前渲染目录最后一项所在页，确认印刷页码。
- **核对目录涉及的每一页**：把所有目录项映射到物理页，去重后逐页核对；两个章节同页只读一次。

### 问题 2：文件夹输出（仅输入是文件夹时问）

- **新建文件夹（推荐）**：新建 `<源文件夹名><后缀>/`，PDF 文件名和目录结构保持不变。
- **每个 PDF 同目录**：输出到各自目录，文件名为 `<源文件名><后缀>.pdf`。

其他规则（不需要问）：

- 单个 PDF 默认同目录，`book.pdf → book.toc.pdf`。
- 已有书签默认替换；如果检测到旧书签，在报告里说明。
- 目录来源默认自动读取；只有读不到目录时才请用户提供文件/文本。

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

```bash
--offset 21
# 或
--anchor 6=27
```

### 核对最后一页

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" check-pages "<pdf>" \
  --toc "<toc>" --offset 21 --mode last --out-dir "<临时目录>" --json
```

读取输出的图片，确认页面上实际印刷页码 = `items[].printedPages`。

### 核对目录涉及的每一页

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" check-pages "<pdf>" \
  --toc "<toc>" --offset 21 --mode all --out-dir "<临时目录>" --json
```

- `items[]` 按物理页去重；同一页上的多个标题只出现一次。
- 读取 `rendered.files`（每张 6×3 拼图），逐页核对。
- 如果某些页不匹配，整理成 `pageMap.json`：

```json
{ "6": 27, "95": 116, "131": 152 }
```

页码核对不通过时不要生成，先修正偏移或 pageMap。

## 第 4 步：生成带目录 PDF

### 单个文件

```bash
uv run --script "<技能目录>/scripts/pdf_toc.py" add "<pdf>" \
  --toc "<toc>" --offset 21 --report "<临时目录>/report.md" --json
```

需要逐页映射时：

```bash
--page-map "<pageMap.json>"
```

### 文件夹

```bash
# 新建文件夹（推荐）
uv run --script "<技能目录>/scripts/pdf_toc.py" batch "<文件夹>" \
  --layout mirror --offset 21 --report "<临时目录>/report.md" --json

# 每个 PDF 同目录
uv run --script "<技能目录>/scripts/pdf_toc.py" batch "<文件夹>" \
  --layout alongside --offset 21 --report "<临时目录>/report.md" --json
```

如果每本 PDF 偏移不同，用 manifest（`sourceRoot` + `layout` + `offset`/`pageMap`），见 [CLI.md](CLI.md)。

批量里的 `--offset` 是统一偏移；不同书不同偏移时必须用 manifest。

## 第 5 步：生成报告

`add` / `batch` 的 `--report` 会写 Markdown 报告，包含：

- 输入 / 输出
- 页数
- 书签数量和层级
- 页码模式（偏移 / pageMap / 物理页）
- 校验结果
- 警告
- 前几项预览

把报告路径和关键结果给用户。报告示例：

```text
已生成：D:\books\Java 8实战.toc.pdf
书签：313 个 / 5 级
页码：印刷页 +21 → 物理页
原文件：未修改
校验：通过
报告：C:\...\report.md
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
uv run --script "<技能目录>/scripts/pdf_toc.py" check-pages "<pdf>" --toc "<toc>" --offset 21 --mode last|all --out-dir "<dir>" --json
uv run --script "<技能目录>/scripts/pdf_toc.py" add "<pdf>" --toc "<toc>" --offset 21 --report "<report.md>" --json
uv run --script "<技能目录>/scripts/pdf_toc.py" batch "<dir>" --layout mirror|alongside --offset 21 --report "<report.md>" --json
uv run --script "<技能目录>/scripts/pdf_toc.py" verify "<output>" --json
```

## 报告模板

```text
已生成：<输出路径>
书签：<N> 个 / <L> 级
页码：<偏移 / pageMap / 物理页>
原文件：未修改
校验：<通过 / 未通过>
报告：<report.md 路径>
```
