# pdf-toc

[![skills.sh](https://skills.sh/b/yluiop123/skills)](https://skills.sh/yluiop123/skills)

[中文说明](README.md)

Add a table of contents (bookmarks / PDF outline) to PDFs from your Agent — OpenCode, Claude Code, Cursor, Codex, and other Agent Skills hosts.

**You do not run any scripts.** The Agent checks the environment, reads the TOC, asks you to confirm the verification mode, writes a new PDF, and reports the result.

## Trigger

After installing the skill, say:

> Add a table of contents to `D:\books\Java 8实战.pdf`

> Add bookmarks to the PDFs in `D:\books\history`

Also works for:

- “Add bookmarks to this PDF”
- “Build a PDF outline from this TOC file”
- “Batch-add TOCs to the scanned books in this folder”

## Install

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc
```

Global install:

```bash
npx skills add https://github.com/yluiop123/skills --skill pdf-toc -g
```

## What happens

1. The Agent checks `uv`. If uv is missing, it asks whether to install it (official installer, once). Python and pypdf/pypdfium2/Pillow are then installed automatically by uv.
2. It scans the PDF: text vs. scanned, page count, existing bookmarks, page-label offset.
3. It asks you exactly two things:
   - **Page verification**: printed page + offset (recommended) / verify the last page / verify every page referenced by the TOC.
   - **Folder output** (only for folders): mirror folder with unchanged names and structure (recommended), or output next to each PDF with a suffix.
4. It reads the TOC:
   - text PDFs: extracts page text;
   - scanned PDFs: renders TOC pages as 6×3 contact sheets and reads them visually.
5. It generates a new PDF with the outline and verifies every bookmark.
6. It reports the output path, bookmark count, page mode, warnings, and verification result in the chat.

## Output

Single file:

```text
book.pdf  →  book.toc.pdf
```

Folder, mirror mode (default):

```text
books/                      books.toc/
  book-a.pdf          →      book-a.pdf
  vol-1/book-b.pdf    →      vol-1/book-b.pdf
```

Folder, alongside mode:

```text
books/book-a.pdf  →  books/book-a.toc.pdf
```

The original PDF is never modified.

## Languages

The skill is language-agnostic:

- Chinese, English, and mixed Chinese/English titles are preserved.
- TOC parsing understands:
  - Chinese numbering: `第一章`, `第一节`, `第一`, `1.1`
  - English numbering: `Chapter 1`, `Section 1.1`, `Part I`, `Appendix A`
  - Markdown indentation and JSON `children`.
- Page-number detection understands `Page 12`, `12  Chapter 1`, `Chapter 1  12`, and Chinese page labels.
- Scanned pages in any language can be read visually by the Agent.

Example English TOC:

```markdown
Chapter 1 Introduction ......... 1
  Section 1.1 Background ....... 2
    1.1.1 Details .............. 3
Chapter 2 Methods .............. 10
Appendix A Glossary ............ 20
```

## Page mapping

- **Offset**: `physical = printed + offset`, e.g. printed page 6 is PDF page 27 → `--offset 21`.
- **Page map**: for discontinuous or missing page numbers:

```json
{ "6": 27, "95": 116, "131": 152 }
```

- **Physical pages**: if the TOC already uses PDF page numbers.

## Safety

- Read-only input; output equal to input is rejected.
- Atomic write; default no overwrite (renames to `-2`, `-3`).
- Encrypted PDFs are rejected.
- Verification reopens the output and checks titles, pages, levels, and destinations.
- Re-saving invalidates existing digital signatures; the Agent warns first.

## Developer CLI

See [CLI.md](CLI.md). Runtime uses **uv only**:

```bash
uv run --script scripts/pdf_toc.py doctor --json
```

No pip, pipx, or local `.venv`.

## License

MIT. pypdf is BSD-3-Clause; page rendering uses pypdfium2 + Pillow.
