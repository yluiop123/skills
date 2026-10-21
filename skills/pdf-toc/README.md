# pdf-toc

[![skills.sh](https://skills.sh/b/yluiop123/skills)](https://skills.sh/yluiop123/skills)

[English](README.en.md)

在 Agent（OpenCode / Claude Code / Cursor / Codex 等）里说一句话，就能给 PDF 加目录（书签 / Outline）。

**你不需要运行任何脚本。** Agent 会检查环境、识别目录和页码、在工具里向你确认，然后写入新文件。运行环境只使用 `uv run --script`；不需要 pip、pipx 或虚拟环境。

## 怎么触发

安装 Skill 后，直接说：

> 请给 `D:\books\Java 8实战.pdf` 增加目录

> 请给 `D:\books\历史` 里的 PDF 文件增加目录

也可以说：

- “给这本书加书签”
- “根据这个目录文件生成 PDF 书签”
- “批量给这个文件夹里的扫描书加目录”

## 安装 Skill

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc
```

安装到全局：

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc -g
```

指定 Agent、跳过交互：

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc \
  -a opencode -a claude-code -y
```

> `npx skills` 需要 Node.js 22.20 或更高版本。

## 触发后会发生什么

### 1. 自动检查环境

Agent 自动检查 uv 环境和依赖（由 uv 按脚本声明自动安装）。缺依赖会在宿主的授权弹窗中提示。

### 2. 自动检查 PDF

Agent 自动判断：

- 文字版还是扫描件
- 总页数
- 是否已有书签
- 是否自带页码标签
- 能否自动检测“印刷页码 → PDF 物理页”偏移

### 3. 在工具里问你

Agent 只问必要的两项：

**1）页码核对方式**

- 只用印刷页 + 偏移量（推荐）
- 核对最后一页
- 核对目录涉及的每一页

**2）如果输入是文件夹，确认输出布局**

- 新建文件夹（`源文件夹名 + .toc`），PDF 文件名和目录结构不变（推荐）
- 每个 PDF 输出到各自同目录，文件名加 `.toc`

其他都用默认规则：

- 单个 PDF 默认同目录 `book.pdf → book.toc.pdf`
- 已有书签默认替换
- 目录默认自动读取；读不到时才请你提供

### 4. 读取目录和页码

Agent 调用一次 `scan`：

- 文字版：读取 PDF 文字，定位目录页。
- 扫描件：把目录页渲染成 6×3 拼图，用视觉识别；不会逐页浏览全书。
- 同时得到总页数、文字层、已有书签、印刷页码偏移。

如果选择了核对页码：

- 核对最后一页：只渲染最后一项所在页。
- 核对每一页：把所有目录项映射到物理页，**按页去重**；两个章节在同一页只读一次。

### 5. 生成并校验

Agent 生成新的带目录 PDF，并重新打开校验标题、页码、层级和页面目标。原文件不修改。

### 6. 汇报结果

Agent 在对话里汇报输入/输出、书签数量、页码模式、校验结果和警告，不生成额外的报告文件。

## 输出规则

### 单个文件

默认输出到原 PDF 同目录：

```
book.pdf  →  book.toc.pdf
```

不修改 `book.pdf`。输出已存在时自动改名 `book.toc-2.pdf`，不会覆盖。

### 文件夹：镜像目录（默认）

给一个文件夹时，新建 `<文件夹名>.toc/`，保持目录结构和文件名：

```
books/                      books.toc/
  Java 8实战.pdf      →      Java 8实战.pdf
  历史/中国战争史.pdf  →      历史/中国战争史.pdf
```

### 文件夹：同目录加后缀

也可以选择：

```
books/                      books/
  Java 8实战.pdf      →      Java 8实战.toc.pdf
  历史/中国战争史.pdf  →      历史/中国战争史.toc.pdf
```

## 支持的情况

- 文字版 PDF
- 扫描件 PDF（书签不依赖文字层）
- 中文 / 英文 / 中英混排
- 英文编号：Chapter / Section / Part / Appendix；中文编号：第一章 / 第一节 / 1.1
- 单文件 / 多文件 / 整个文件夹
- 已有书签：替换、合并、保留
- 页码：整体偏移、锚点、逐页 pageMap、物理页
- 加密 PDF：会拒绝并提示先解密
- 损坏 PDF：会报错，不会写出半成品

## 安全

- 原文件只读；输出路径等于原文件时直接拒绝。
- 原子写入，失败不会留下半个文件。
- 默认不覆盖已有输出。
- 写入后自动校验；校验失败不交付。
- 重新保存会使已有数字签名失效；Agent 会先提示。

## 开发者

CLI 的安装、参数和批量 manifest 见 [CLI.md](CLI.md)。

## License

MIT。依赖 pypdf（BSD-3-Clause）。
