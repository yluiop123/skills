# pdf-toc

给 PDF 添加或替换目录（书签 / Outline）的 Python 命令行工具，同时是一个 Agent Skill。

## 安装 Skill

```bash
# 安装到当前项目
npx skills add https://github.com/yluiop123/skills --skill pdf-toc

# 安装到全局
npx skills add https://github.com/yluiop123/skills --skill pdf-toc -g

# 指定 Agent，跳过交互
npx skills add https://github.com/yluiop123/skills --skill pdf-toc -a claude-code -a opencode -y
```

安装后直接对 Agent 说“给这个 PDF 加目录”，或按下面的方式手动运行脚本。

## 特性

- **只依赖 pypdf**：纯 Python、BSD-3-Clause，不依赖 PyMuPDF、Tesseract、qpdf。
- **文字版 / 扫描件都支持**：书签是 PDF 结构，扫描件也能直接写入。
- **不修改原文件**：默认输出同目录，文件名加 `.toc` 后缀。
- **单文件 / 批量**：`add` 处理一个，`batch` 按 manifest 或目录配对处理一批。
- **页码校准**：文字版可用 `/PageLabels` 或页眉/页脚文字自动检测偏移；扫描件用 `--offset` / `--anchor` 手动校准。
- **写入前预览、写入后校验**：`--dry-run` + `verify`。
- **不丢页码标签**：使用 `PdfWriter(clone_from=...)` 保留原 PDF 的 `/PageLabels` 和元数据。

## 环境要求

- Python **>= 3.9**
- `pypdf >= 6.0`

## 运行方式

### 方式 1：uv（推荐，零安装）

```bash
uv run --script scripts/pdf_toc.py doctor --json
```

`uv` 会读取脚本顶部的 PEP 723 元数据，自动创建临时环境并安装 pypdf。

### 方式 2：pipx / uv tool 安装成全局命令

```bash
pipx install "git+https://github.com/yluiop123/skills#subdirectory=skills/pdf-toc"
# 或
uv tool install "git+https://github.com/yluiop123/skills#subdirectory=skills/pdf-toc"

pdf-toc doctor --json
```

### 方式 3：当前 Python

```bash
python -m pip install pypdf
python scripts/pdf_toc.py doctor --json
```

## 快速开始

### 1. 检查 PDF

```bash
python scripts/pdf_toc.py inspect "book.pdf" --json
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
python scripts/pdf_toc.py add "book.pdf" --toc "book.toc.md" --dry-run --json
```

检查 `entries`、`pageMode`、`offset`、`preview`、`warnings`。确认后去掉 `--dry-run`：

```bash
python scripts/pdf_toc.py add "book.pdf" --toc "book.toc.md" --json
```

默认输出 `book.toc.pdf`。

### 4. 校验

```bash
python scripts/pdf_toc.py verify "book.toc.pdf" --expect-count 42 --json
```

## 扫描件：页码偏移

pypdf 不会 OCR。扫描件没有文字层，无法自动知道“书上第 6 页”是 PDF 第几页。

**锚点（推荐）**：

```bash
# 书上第 6 页 = PDF 第 27 页
python scripts/pdf_toc.py add "scanned.pdf" --toc "scanned.toc.md" --anchor 6=27
```

**整体偏移**：

```bash
# 物理页 = 印刷页 + 21
python scripts/pdf_toc.py add "scanned.pdf" --toc "scanned.toc.md" --offset 21
```

**目录页码就是物理页**：

```bash
python scripts/pdf_toc.py add "scanned.pdf" --toc "scanned.toc.json" --page-mode physical
```

## 批量处理

### manifest

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
python scripts/pdf_toc.py batch manifest.json --dry-run --json
python scripts/pdf_toc.py batch manifest.json --json
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
python scripts/pdf_toc.py batch "books" --dry-run --json
python scripts/pdf_toc.py batch "books" --json
```

- 每项单独成功/失败，默认继续处理后续项目。
- 找不到目录的 PDF 计为 `skipped`，默认让整体返回失败；可忽略时加 `--allow-skipped`。
- 输出文件默认写到各自 PDF 同目录；用 `--out-dir` 统一输出。
- 以 `.toc` 结尾的 PDF 会被自动排除，避免二次处理自己的产物。

## 命令

```
pdf-toc doctor [--json] [--fix] [--require-ready]
pdf-toc inspect <pdf> [--json]
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
python -m unittest discover -s tests -v
```

本地端到端测试（需要把两个测试 PDF 的目录通过环境变量传入）：

```bash
set PDF_TOC_TEST_DIR=D:\my-project\pdf-menu
python tests/e2e_local.py
```

## 限制

- 不做 OCR：扫描件页面文字、扫描版目录页不会自动识别。目录需要用户提供，或由具备视觉能力的 Agent 读取目录页后整理。
- 加密 PDF 不支持；请先解密。
- 重新保存会改变 PDF 字节，可能使数字签名失效。
- 合并模式会把原有书签重新写出；仅网址/脚本动作的书签可能失效，`inspect` / batch 会给出警告。

## License

MIT。依赖 pypdf（BSD-3-Clause）。
