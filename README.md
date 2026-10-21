# Skills

[![skills.sh](https://skills.sh/b/yluiop123/skills)](https://skills.sh/yluiop123/skills)

**English** | [简体中文](README.zh-CN.md)

A collection of Agent Skills for OpenCode, Claude Code, Cursor, Codex, Gemini CLI, and other hosts that support `SKILL.md`.

| Skill | Description |
|---|---|
| [`pdf-toc`](skills/pdf-toc/) | Add or replace a PDF table of contents (bookmarks / outline) in text or scanned PDFs. Single file or whole folder. The original PDF is never modified. |

## Install

Install the `pdf-toc` skill into the current project with the [`skills`](https://github.com/vercel-labs/skills) CLI:

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc
```

Install globally (available in all projects):

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc -g
```

Choose agents and skip prompts:

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc \
  -a opencode -a claude-code -y
```

List available skills without installing:

```bash
npx skills add https://github.com/yluiop123/skills --list
```

> `npx skills` requires Node.js 22.20 or newer.

## Usage

After installing, simply tell your Agent:

> Add a table of contents to `D:\books\Java 8实战.pdf`

> Add bookmarks to the PDFs in `D:\books\history`

The Agent checks the environment, reads the TOC, asks you to confirm the verification mode, writes a new PDF, and reports the result. **You do not need to run any scripts manually.** The runtime uses `uv` only.

See [`skills/pdf-toc/README.md`](skills/pdf-toc/README.md) for details ([简体中文](skills/pdf-toc/README.zh-CN.md)).

## Structure

```text
skills/
└── pdf-toc/
    ├── SKILL.md
    ├── README.md          # user guide (English)
    ├── README.zh-CN.md    # user guide (Chinese)
    ├── CLI.md             # developer CLI reference
    ├── scripts/
    │   └── pdf_toc.py
    ├── examples/
    └── tests/
```

Each skill is a separate directory with `SKILL.md` as its entry point. The `skills` CLI discovers `skills/<name>/SKILL.md`.

## License

MIT
