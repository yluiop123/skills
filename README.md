# Skills

Agent Skills 集合，兼容 OpenCode、Claude Code、Cursor、Codex、Gemini CLI 等支持 `SKILL.md` 的宿主。

| Skill | 说明 |
|---|---|
| [`pdf-toc`](skills/pdf-toc/) | 给文字版或扫描版 PDF 添加/替换目录（书签），支持单个文件和整个文件夹，默认不修改原文件 |

## 安装

使用 [`skills`](https://github.com/vercel-labs/skills) CLI 安装到当前项目：

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc
```

安装到全局（所有项目可用）：

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc -g
```

指定 Agent、跳过交互：

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc \
  -a opencode -a claude-code -y
```

只查看仓库里有哪些 skill，不安装：

```bash
npx skills add https://github.com/yluiop123/skills --list
```

> `npx skills` 需要 Node.js 22.20 或更高版本。

## 使用

安装后，直接对 Agent 说：

> 请给 `D:\books\Java 8实战.pdf` 增加目录

> 请给 `D:\books\历史` 里的 PDF 文件增加目录

Agent 会自动检查环境、识别目录和页码、在工具里向你确认，然后写入新文件并生成报告。**你不需要手动运行任何脚本。** 运行环境只使用 `uv`。

详细说明见 [`skills/pdf-toc/README.md`](skills/pdf-toc/README.md)。

## 目录结构

```text
skills/
└── pdf-toc/
    ├── SKILL.md
    ├── README.md          # 用户说明
    ├── CLI.md             # 开发者命令行参考
    ├── pyproject.toml
    ├── scripts/
    │   └── pdf_toc.py
    ├── examples/
    └── tests/
```

每个 skill 是一个独立目录，入口为 `SKILL.md`。`skills` CLI 会自动发现 `skills/<name>/SKILL.md`。

## License

MIT
