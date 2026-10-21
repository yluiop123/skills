# pdf-toc CLI 参考（开发者）

这是命令行工具的开发者文档。**普通用户不需要看这个文件**——安装 Skill 后，直接对 Agent 说：

> 请给 xxx.pdf 增加目录

> 请给 xxx 文件夹里的 PDF 增加目录

Agent 会自动检查环境、识别目录和页码、询问确认并写入。

## 运行

```bash
uv run --script scripts/pdf_toc.py --help
```

不需要 pip、pipx 或虚拟环境。

## 特性

- **依赖少**：核心写入只依赖 pypdf；页面渲染用 pypdfium2 + Pillow，不依赖 PyMuPDF、Tesseract、qpdf。
- **文字版 / 扫描件都支持**：书签是 PDF 结构，扫描件也能直接写入。
- **不修改原文件**：默认输出同目录，文件名加 `.toc` 后缀。
- **单文件 / 批量**：`add` 处理一个，`batch` 按 manifest 或目录配对处理一批。
- **页码校准**：文字版自动检测偏移；扫描件用 `scan` / `check-pages` 渲染页面核对，支持 offset / anchor / pageMap。
- **写入前预览、写入后校验**：`--dry-run` + `verify`。
- **不丢页码标签**：使用 `PdfWriter(clone_from=...)` 保留原 PDF 的 `/PageLabels` 和元数据。

## 环境要求

- **uv**（必须先安装一次；Python 和依赖由 uv 管理）
- Python **>= 3.9**（uv 会自动下载/使用）
- `pypdf >= 6,<7`
- `pypdfium2 >= 4.30,<6`、`Pillow >= 10,<12`（`scan` / `check-pages` 使用）

首次运行 `uv run --script` 会自动安装 Python（如果需要）和上述依赖到 uv 缓存。

## 运行方式

只使用 `uv`：

```bash
uv run --script scripts/pdf_toc.py doctor --json
```

`uv` 会读取脚本顶部的 PEP 723 元数据，自动创建临时环境并安装 pypdf / pypdfium2 / Pillow。

## 快速开始

### 1. 检查 PDF

```bash
uv run --script scripts/pdf_toc.py inspect "book.pdf" --json
```

关注：

- `pages`：总页数
- `text.scanned`：是否扫描件
- `outline.count`：已有书签数
- `pageLabels`：PDF 自带页码标签
- `offset.detected` / `offset.confidence` / `offset.source`：检测到的印刷页码偏移

### 2. 准备目录

**JSON**

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

`pageMode` 可以是 `physical`（PDF 物理页）或 `printed`（书上印刷页码）。省略时由命令行参数决定。

**Markdown / 纯文本**

```markdown
第一章 概述 ................ 12
  1.1 背景 .................. 12
  1.2 目标 .................. 18
第二章 实现 ................ 35
```

- 行尾最后一个数字是页码。
- 缩进决定层级；没有缩进时按 `1.1`、`第一章/第一节` 推断。
- 没有页码的卷/篇标题会使用下一项的页码；没有页码的附图行会跳过并给出警告。

### 3. 预览

```bash
uv run --script scripts/pdf_toc.py add "book.pdf" --toc "book.toc.md" --dry-run --json
```

检查 `entries`、`pageMode`、`offset`、`preview`、`warnings`。确认后去掉 `--dry-run`：

```bash
uv run --script scripts/pdf_toc.py add "book.pdf" --toc "book.toc.md" --json
```

默认输出 `book.toc.pdf`。

### 4. 校验

```bash
uv run --script scripts/pdf_toc.py verify "book.toc.pdf" --expect-count 42 --json
```

## 扫描件：页码偏移

pypdf 不会 OCR。扫描件没有文字层，无法自动知道“书上第 6 页”是 PDF 第几页。先用 `scan` 找到一页的印刷页码和物理页码。

**锚点（推荐）**：

```bash
uv run --script scripts/pdf_toc.py add "scanned.pdf" --toc "scanned.toc.md" \
  --anchor "<PRINTED>=<PHYSICAL>"
```

例如当前 PDF 里书上第 6 页是 PDF 第 27 页，才用 `--anchor 6=27`；**不要照抄这个值**。

**整体偏移**：

```bash
uv run --script scripts/pdf_toc.py add "scanned.pdf" --toc "scanned.toc.md" \
  --offset "<OFFSET>"
```

`OFFSET = 物理页 - 印刷页`。示例中的 27-6=21 只对当前书有效。

**目录页码就是物理页**：

```bash
uv run --script scripts/pdf_toc.py add "scanned.pdf" --toc "scanned.toc.json" --page-mode physical
```

## 自动识别目录和页码

CLI 提供 `scan` 命令，一次完成 PDF 检查和目录页文字/图片导出：

```bash
uv run --script scripts/pdf_toc.py scan "book.pdf" --pages 1-40 --out-dir "./.pdf-toc-pages" --json
```

- 文字版：返回 `textPages`、`tocCandidates`，默认不渲染。
- 扫描件：自动把 1-40 页渲染成 6×3 拼图（如 `sheet-11-28.png`），每页标 `p.N`。
- 强制渲染文字版：加 `--render`。
- 每页一张图：加 `--no-sheet`。
- 只渲染部分页：`uv run --script scripts/pdf_toc.py render "book.pdf" --pages 11-28 --out-dir ... --json`。

`scan` / `render` 使用 `pypdfium2 + Pillow`，由 `uv run --script` 自动安装。Agent 用视觉读取图片，整理目录。

### 页码映射

- 整体偏移（默认）：`--offset "<OFFSET>"` 或 `--anchor "<PRINTED>=<PHYSICAL>"`
- 逐页映射：`--page-map pageMap.json`

示例（不要照抄）：

```json
{
  "6": 27,
  "95": 116,
  "131": 152
}
```

键是印刷页码，值是 PDF 物理页码。未出现在映射表中的条目按 `--offset` / `--page-mode` 处理。实际值必须来自当前 PDF 的 `scan` / 核对结果。

### 核对页码（可选）

```bash
# 只核对最后一项所在页
uv run --script scripts/pdf_toc.py check-pages "book.pdf" \
  --toc "book.toc.json" --offset "<OFFSET>" --mode last --out-dir "./.pdf-toc-check" --json

# 核对所有目录项涉及的页；同一物理页只渲染一次
uv run --script scripts/pdf_toc.py check-pages "book.pdf" \
  --toc "book.toc.json" --offset "<OFFSET>" --mode all --out-dir "./.pdf-toc-check" --json
```

`items[]` 会按物理页去重，并列出该页对应的所有标题和印刷页码；`rendered.files` 是 6×3 拼图。核对不一致时用 `--page-map` 修正。

## 批量处理

### 文件夹：镜像目录（默认）

```bash
uv run --script scripts/pdf_toc.py batch "books" --layout mirror --dry-run --json
uv run --script scripts/pdf_toc.py batch "books" --layout mirror --json
```

默认输出到源文件夹同级的 `books.toc/`：

```
books/                      books.toc/
  书A.pdf          →          书A.pdf
  卷一/书B.pdf     →          卷一/书B.pdf
```

目录结构和文件名保持不变。`--out-dir` 可指定镜像根目录；位于源文件夹内部的输出目录会被自动排除。

### 文件夹：同目录加后缀

```bash
uv run --script scripts/pdf_toc.py batch "books" --layout alongside --json
```

每个 PDF 旁边生成 `书A.toc.pdf`、`书B.toc.pdf`。

### manifest（跨目录/逐项配置）

```json
{
  "sourceRoot": "books",
  "defaults": { "mode": "replace", "suffix": ".toc", "layout": "mirror" },
  "items": [
    { "pdf": "book-a.pdf", "toc": "book-a.toc.json", "pageMode": "physical" },
    { "pdf": "sub/book-b.pdf", "toc": "sub/book-b.toc.md", "pageMap": "sub/book-b.pageMap.json" },
    { "pdf": "book-c.pdf", "toc": "book-c.toc.json", "pageMode": "physical" }
  ]
}
```

`sourceRoot` 是镜像的源根目录；输出默认为同级的 `books.toc/`，保持结构。适合不同 PDF 需要不同偏移/映射的批量任务。文件夹默认递归处理子目录；只处理第一层用 `--no-recursive`。

```bash
uv run --script scripts/pdf_toc.py batch manifest.json --dry-run --json
uv run --script scripts/pdf_toc.py batch manifest.json --json
```

- 每个 PDF 必须能找到同名 `.toc.json/.toc.md/.toc.txt`（或 `--toc-dir` 下同名文件）。
- 找不到目录的 PDF 计为 `skipped`，默认让整体返回失败；可忽略时加 `--allow-skipped`。
- 单项失败默认继续处理后续项目。

## 命令

```
pdf-toc doctor [--json] [--require-ready]
pdf-toc inspect <pdf> [--json]
pdf-toc scan <pdf> [--pages 1-40] [--out-dir <dir>] [--json]
pdf-toc render <pdf> --pages <range> --out-dir <dir> [--json]
pdf-toc check-pages <pdf> --toc <file> --mode last|all [--offset N] [--out-dir <dir>] [--json]
pdf-toc text <pdf> [--pages 1-40] [--json]
pdf-toc extract <pdf> --out <file> [--format json|md] [--force]
pdf-toc add <pdf> --toc <file> [options]
pdf-toc batch <dir|manifest.json> [options]
pdf-toc verify <pdf> [--expect-count N] [--json]
```

`add` 常用选项：

| 选项 | 说明 |
|---|---|
| `--out <file>` | 指定输出文件 |
| `--out-dir <dir>` | 指定输出目录 |
| `--suffix <.toc>` | 输出后缀，默认 `.toc` |
| `--mode replace\|merge\|keep` | 已有书签处理方式，默认 `replace` |
| `--page-mode auto\|physical\|printed` | 目录页码含义，默认 `auto` |
| `--offset <n>` | 物理页 = 印刷页 + n |
| `--anchor <printed=physical>` | 锚点校准，可重复 |
| `--page-map <file>` | 自定义印刷页 → 物理页映射 |
| `--layout mirror\|alongside` | 批量输出布局（`batch`） |
| `--on-exists rename\|error\|overwrite` | 输出冲突策略，默认 `rename` |
| `--dry-run` | 只预览，不写文件 |
| `--no-verify` | 跳过写入后的自动校验 |
| `--no-open-panel` | 不设置打开 PDF 时显示书签栏 |

## 安全设计

- 原文件只读；输出路径等于原文件时直接报 `OUTPUT_IS_INPUT`。
- 原子写入：先写临时文件，再 `os.replace`。
- 输出已存在时默认改名 `-2`、`-3`。
- 加密 PDF 拒绝处理。
- 写入后重新打开输出，逐项比对标题、页码、层级，并检查页面目标。
- 重新保存会使已有数字签名失效；如 PDF 有签名，先告知用户。

## 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 处理失败 |
| `2` | 参数错误 |
| `3` | 环境未就绪（`doctor --require-ready`） |

所有命令支持 `--json`。失败时为 `{ "ok": false, "error": { "code", "message", "hint" } }`。

## 测试

```bash
uv run --with pypdf --with pypdfium2 --with pillow python -m unittest discover -s tests -v
```

本地端到端测试（需要把两个测试 PDF 的目录通过环境变量传入）：

```bash
set PDF_TOC_TEST_DIR=D:\my-project\pdf-menu
uv run --with pypdf --with pypdfium2 --with pillow python tests/e2e_local.py
```

## 限制

- 不内置 OCR：扫描件页面文字不会自动识别；目录页通过 `scan` / `render` 渲染成图片，由具备视觉能力的 Agent 读取整理。
- 自动识别目录依赖 Agent 的文本/视觉能力；CLI 只负责导出文字、渲染页面、校验和写入。
- 加密 PDF 不支持；请先解密。
- 重新保存会改变 PDF 字节，可能使数字签名失效。
- 合并模式会把原有书签重新写出；仅网址/脚本动作的书签可能失效，`inspect` / batch 会给出警告。

## License

MIT。依赖 pypdf（BSD-3-Clause）。
