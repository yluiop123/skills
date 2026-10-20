#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pypdfium2>=4.30.0", "pillow>=10.0"]
# ///
"""Render PDF pages to PNG so an agent can inspect scanned pages visually.

This file is optional and separate from pdf_toc.py: the main CLI still only
needs pypdf. `uv run --script render_pages.py` installs pypdfium2 + Pillow in a
temporary environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import pypdfium2 as pdfium
    from PIL import Image  # noqa: F401  (needed by PdfBitmap.to_pil)
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(f"缺少渲染依赖：{exc}\n请使用：uv run --script render_pages.py ...\n")
    raise SystemExit(3) from exc


class RenderError(Exception):
    pass


def parse_pages(spec: str | None, page_count: int) -> list[int]:
    if not spec:
        return list(range(min(page_count, 30)))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            start, end = int(start), int(end)
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    selected = sorted(page for page in pages if 1 <= page <= page_count)
    if not selected:
        raise RenderError(f"页码范围不在 1..{page_count} 内：{spec}")
    return [page - 1 for page in selected]


def render(
    pdf_path: str,
    pages_spec: str | None,
    out_dir: str,
    scale: float,
    image_format: str,
) -> dict:
    source = Path(pdf_path).resolve()
    if not source.is_file():
        raise RenderError(f"PDF 不存在：{source}")
    target_dir = Path(out_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        document = pdfium.PdfDocument(str(source))
    except Exception as exc:
        raise RenderError(f"PDF 打开失败：{exc}") from exc

    try:
        page_count = len(document)
        indices = parse_pages(pages_spec, page_count)
        items = []
        for index in indices:
            page = document[index]
            width, height = page.get_size()
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            target = target_dir / f"page-{index + 1:03d}.{image_format}"
            image.save(target, format=image_format.upper())
            items.append(
                {
                    "page": index + 1,
                    "path": str(target),
                    "width": round(width, 1),
                    "height": round(height, 1),
                }
            )
        return {
            "ok": True,
            "file": str(source),
            "pageCount": page_count,
            "outDir": str(target_dir),
            "scale": scale,
            "items": items,
        }
    finally:
        document.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 PDF 页面渲染成 PNG，供 Agent 视觉识别目录/页码。")
    parser.add_argument("pdf")
    parser.add_argument("--pages", help="页码范围，例如 1-30 或 11,12,27；默认前 30 页")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--scale", type=float, default=2.0, help="渲染倍率，默认 2.0")
    parser.add_argument("--format", choices=("png", "jpg", "jpeg"), default="png")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    image_format = "jpeg" if args.format in ("jpg", "jpeg") else "png"
    try:
        result = render(args.pdf, args.pages, args.out_dir, args.scale, image_format)
    except RenderError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": {"code": "RENDER_FAILED", "message": str(exc)}}, ensure_ascii=False, indent=2))
        else:
            print(f"✖ {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"已渲染 {len(result['items'])} 页到：{result['outDir']}")
        for item in result["items"]:
            print(f"  第 {item['page']} 页 → {item['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
