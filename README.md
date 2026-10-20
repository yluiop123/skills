# Skills

Agent Skills 集合，兼容 OpenCode、Claude Code、Cursor、Codex、Gemini CLI 等支持 `SKILL.md` 的宿主。

| Skill | 说明 |
|---|---|
| [`pdf-toc`](skills/pdf-toc/) | 给文字版或扫描版 PDF 添加/替换目录（书签），支持单个和批量，默认不修改原文件 |

## 目录结构

```text
skills/
└── pdf-toc/
    ├── SKILL.md
    ├── README.md
    ├── pyproject.toml
    ├── scripts/
    │   └── pdf_toc.py
    ├── examples/
    └── tests/
```

每个 skill 是一个独立目录，入口为 `SKILL.md`。

## 安装 skill

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

安装后 `pdf-toc` 会出现在所选 Agent 的 skills 目录中（例如 `.agents/skills/pdf-toc/`、`.claude/skills/pdf-toc/`、`.opencode/skills/pdf-toc/`）。

## 目录结构

```text
skills/
└── pdf-toc/
    ├── SKILL.md
    ├── README.md
    ├── pyproject.toml
    ├── scripts/
    │   └── pdf_toc.py
    ├── examples/
    └── tests/
```

每个 skill 是一个独立目录，入口为 `SKILL.md`。`skills` CLI 会自动发现 `skills/<name>/SKILL.md`。

## License

MIT
