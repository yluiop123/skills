#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pypdf>=6,<7", "pypdfium2>=4.30,<6", "pillow>=10,<12"]
# ///
"""Add or replace PDF bookmarks (outline / TOC) safely.

Only pypdf is required. Bookmarks are PDF structure, so text-based and scanned
PDFs are both supported. Automatic page-number calibration needs a text layer
or /PageLabels; scanned PDFs can use --offset, --anchor or --page-mode physical.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

VERSION = "1.5.3"
DEFAULT_SUFFIX = ".toc"
PAGE_MODE_VALUES = ("auto", "physical", "printed")
MODE_VALUES = ("replace", "merge", "keep")
ON_EXISTS_VALUES = ("rename", "error", "overwrite")

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
except ImportError:  # pragma: no cover - doctor reports this
    PdfReader = None  # type: ignore
    PdfWriter = None  # type: ignore
    NameObject = None  # type: ignore

# pypdf can emit "Object x 0 not defined" while cloning unusual PDFs. The
# output is still valid; keep stdout/stderr clean for JSON consumers.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)


class CliError(Exception):
    def __init__(self, code: str, message: str, hint: str = "", details: Any = None, exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details
        self.exit_code = exit_code


class PdfMenuParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - argparse path
        raise CliError("USAGE", message, exit_code=2)


def error_payload(error: Exception) -> dict[str, Any]:
    if isinstance(error, CliError):
        payload: dict[str, Any] = {
            "ok": False,
            "error": {"code": error.code, "message": error.message},
        }
        if error.hint:
            payload["error"]["hint"] = error.hint
        if error.details is not None:
            payload["error"]["details"] = error.details
        return payload
    return {"ok": False, "error": {"code": "UNEXPECTED", "message": str(error) or error.__class__.__name__}}


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def human(message: str = "") -> None:
    print(message)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def require_pypdf() -> None:
    if PdfReader is None:
        raise CliError(
            "MISSING_DEP",
            "缺少 pypdf 依赖。",
            hint="运行 `python -m pip install pypdf`，或使用 `uv run --script <本脚本>` 自动安装。",
        )


# ---------------------------------------------------------------------------
# TOC parsing
# ---------------------------------------------------------------------------

LEADER_PAGE = re.compile(r"(?:[.．·…⋯•]{2,}|…+)\s*(\d{1,4})\s*\Z", re.UNICODE)
TAB_PAGE = re.compile(r"\t+(\d{1,4})\s*\Z", re.UNICODE)
SPACED_PAGE = re.compile(r"\s{2,}(\d{1,4})\s*\Z", re.UNICODE)
SINGLE_SPACE_PAGE = re.compile(r"\s(\d{1,4})\s*\Z", re.UNICODE)
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]", re.UNICODE)


def parse_toc_content(content: str, file_path: str = "") -> dict[str, Any]:
    content = content.lstrip("\ufeff")
    if not content.strip():
        raise CliError(
            "TOC_EMPTY",
            "目录内容为空。",
            hint="请提供包含标题和页码的目录文件，例如 Markdown 列表或 JSON。",
        )

    suffix = Path(file_path).suffix.lower() if file_path else ""
    stripped = content.strip()
    if suffix == ".json" or stripped.startswith("{") or stripped.startswith("["):
        parsed = _parse_json_toc(stripped)
    else:
        parsed = _parse_text_toc(content)

    if not parsed["entries"]:
        raise CliError(
            "TOC_EMPTY",
            "没有解析到任何目录项。",
            hint="每一行应包含标题和页码，例如：第一章 概述 .......... 12",
            details=parsed.get("warnings"),
        )
    return parsed


def _parse_json_toc(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(
            "TOC_JSON_INVALID",
            "JSON 目录解析失败。",
            details={"message": exc.msg, "line": exc.lineno, "column": exc.colno},
            hint="请检查 JSON 语法，例如是否缺少逗号或使用了中文引号。",
        ) from exc

    page_mode = None
    if isinstance(data, list):
        raw_entries = data
    elif isinstance(data, dict) and isinstance(data.get("entries"), list):
        raw_entries = data["entries"]
        page_mode = data.get("pageMode")
    elif isinstance(data, dict) and isinstance(data.get("toc"), list):
        raw_entries = data["toc"]
        page_mode = data.get("pageMode")
    elif isinstance(data, dict):
        raw_entries = []
        for title, value in data.items():
            if isinstance(value, dict) and not isinstance(value, list):
                raw_entries.append({"title": title, **value})
            else:
                raw_entries.append({"title": title, "page": value})
    else:
        raise CliError(
            "TOC_JSON_SHAPE",
            "JSON 目录格式不正确。",
            hint="顶层应为数组，或包含 entries 数组的对象。",
        )

    if page_mode is not None and page_mode not in PAGE_MODE_VALUES:
        raise CliError(
            "TOC_PAGE_MODE_INVALID",
            f"JSON 中的 pageMode 无效：{page_mode}",
            hint=f"可选值：{', '.join(PAGE_MODE_VALUES)}",
        )

    entries: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()

    def walk(items: Any, inherited_level: int, parent_title: str = "") -> None:
        if not isinstance(items, list):
            raise CliError(
                "TOC_JSON_SHAPE",
                f"目录项 children 必须是数组{f'（{parent_title}）' if parent_title else ''}。",
            )
        for raw in items:
            if not isinstance(raw, dict):
                raise CliError("TOC_JSON_SHAPE", '目录项必须是对象，例如 {"title":"第一章","page":12}。')
            title = _stringify_title(raw.get("title", raw.get("name", raw.get("text", raw.get("label")))))
            raw_page = raw.get("page", raw.get("pageNumber", raw.get("p", raw.get("target"))))
            if not title:
                raise CliError("TOC_TITLE_MISSING", "存在缺少 title 的目录项。", details={"raw": raw})
            if raw_page is None:
                raise CliError(
                    "TOC_PAGE_MISSING",
                    f"目录项缺少 page：{title}",
                    hint="JSON 目录中每一项都必须提供页码。",
                )
            page = _parse_page_number(raw_page, f"目录项「{title}」")
            level = raw.get("level", inherited_level)
            if isinstance(level, bool) or not isinstance(level, (int, float)) or int(level) != level or level < 1:
                raise CliError("TOC_LEVEL_INVALID", f"目录项层级无效：{title}（level={raw.get('level')}）")
            level = int(level)
            key = (level, title, page)
            if key in seen:
                warnings.append({"code": "DUPLICATE_ENTRY", "message": f"重复目录项：{title}（第 {page} 页）"})
            seen.add(key)
            entries.append({"title": title, "page": page, "level": level, "source_line": None})
            children = raw.get("children", raw.get("items", raw.get("sub")))
            if children is not None:
                walk(children, level + 1, title)

    walk(raw_entries, 1)
    return {"entries": entries, "warnings": warnings, "pageMode": page_mode, "format": "json"}


def _parse_text_toc(content: str) -> dict[str, Any]:
    lines = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    warnings: list[dict[str, Any]] = []
    raw_entries: list[dict[str, Any]] = []

    for index, original in enumerate(lines, start=1):
        if not original.strip():
            continue
        if re.fullmatch(r"\s*<!--.*-->\s*", original):
            continue
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", original):
            continue

        line = original.expandtabs(4)
        indent = len(line) - len(line.lstrip(" "))
        rest = line.strip()

        hash_level = 0
        hash_match = re.match(r"^(#{1,6})\s+(.*)$", rest)
        if hash_match:
            hash_level = len(hash_match.group(1))
            rest = hash_match.group(2).strip()

        if re.match(r"^[-*+]\s+", rest):
            rest = re.sub(r"^[-*+]\s+", "", rest)

        numbering_level = _detect_numbering_level(rest)
        page_match = _extract_page(rest)
        if not page_match:
            without_page = rest.strip()
            if not without_page:
                continue
            if re.fullmatch(r"(目\s*录|contents|table\s+of\s+contents)", without_page, re.IGNORECASE):
                continue
            if re.search(r"目录|contents", without_page, re.IGNORECASE) and len(without_page) <= 30:
                continue
            raw_entries.append(
                {
                    "title": _clean_title(without_page),
                    "page": None,
                    "indent": indent,
                    "numbering_level": numbering_level,
                    "hash_level": hash_level,
                    "source_line": index,
                }
            )
            continue

        page, match_start = page_match
        title = _clean_title(rest[:match_start])
        if not title:
            raise CliError(
                "TOC_TITLE_MISSING",
                f"第 {index} 行缺少标题。",
                details={"line": original.strip()},
            )
        raw_entries.append(
            {
                "title": title,
                "page": page,
                "indent": indent,
                "numbering_level": numbering_level,
                "hash_level": hash_level,
                "source_line": index,
            }
        )

    if not raw_entries:
        return {"entries": [], "warnings": warnings, "format": "text"}

    for i, entry in enumerate(raw_entries):
        if entry["page"] is not None:
            continue
        next_entry = next((candidate for candidate in raw_entries[i + 1 :] if candidate["page"] is not None), None)
        if next_entry and (
            _is_structural_heading(entry["title"])
            or next_entry["indent"] > entry["indent"]
            or next_entry["numbering_level"] > (entry["numbering_level"] or 1)
        ):
            entry["page"] = next_entry["page"]
            entry["inferred_page"] = True
            warnings.append(
                {
                    "code": "PAGE_INFERRED",
                    "message": f"「{entry['title']}」没有页码，已使用下一项的页码 {next_entry['page']}。",
                    "line": entry["source_line"],
                }
            )
        else:
            entry["skip"] = True
            warnings.append(
                {
                    "code": "SKIPPED_NO_PAGE",
                    "message": f"「{entry['title']}」没有页码，已跳过。",
                    "line": entry["source_line"],
                    "hint": "如果这是一条正式目录项，请补上页码。",
                }
            )

    kept = [entry for entry in raw_entries if not entry.get("skip") and entry["page"] is not None]
    distinct_indents = sorted({entry["indent"] for entry in kept})
    use_indent = len(distinct_indents) > 1
    indent_level = {value: index + 1 for index, value in enumerate(distinct_indents)}

    entries = []
    for entry in kept:
        if use_indent:
            level = indent_level[entry["indent"]]
            if entry["hash_level"] > 0:
                level = max(1, entry["hash_level"] - 1)
        elif entry["hash_level"] > 0:
            level = max(1, entry["hash_level"] - 1)
        else:
            level = entry["numbering_level"] or 1
        entries.append(
            {
                "title": entry["title"],
                "page": entry["page"],
                "level": level,
                "source_line": entry["source_line"],
                "inferred_page": bool(entry.get("inferred_page")),
            }
        )

    seen: set[tuple[int, str, int]] = set()
    for entry in entries:
        key = (entry["level"], entry["title"], entry["page"])
        if key in seen:
            warnings.append(
                {
                    "code": "DUPLICATE_ENTRY",
                    "message": f"重复目录项：{entry['title']}（第 {entry['page']} 页）",
                    "line": entry["source_line"],
                }
            )
        seen.add(key)

    return {"entries": entries, "warnings": warnings, "format": "text"}


def _extract_page(text: str) -> tuple[int, int] | None:
    for pattern in (LEADER_PAGE, TAB_PAGE, SPACED_PAGE, SINGLE_SPACE_PAGE):
        match = pattern.search(text)
        if match:
            return int(match.group(1)), match.start()
    return None


def _detect_numbering_level(text: str) -> int:
    if re.match(r"^第[〇零一二三四五六七八九十百千万0-9]+[章篇编部卷]", text):
        return 1
    if re.match(r"^第[〇零一二三四五六七八九十百千万0-9]+节", text):
        return 2
    if re.match(r"^[〇零一二三四五六七八九十百]+、", text):
        return 2
    if re.match(r"^[（(][〇零一二三四五六七八九十百0-9]+[）)]", text):
        return 3
    if re.match(r"^第[〇零一二三四五六七八九十百千万0-9]+(?![章节篇编部卷])", text):
        return 3

    # English / Latin numbering
    if re.match(r"^(chapter|book|part|volume|unit|appendix)\s+([0-9]+|[ivxlcdm]+|[a-z]+)\b", text, re.IGNORECASE):
        return 1
    section = re.match(r"^section\s+(\d+(?:\.\d+)*)\b", text, re.IGNORECASE)
    if section:
        return max(2, len(section.group(1).split(".")))
    if re.match(r"^[A-Z]\.\s+\S", text):
        return 2
    if re.match(r"^[IVXLCDM]+\.\s+\S", text):
        return 1

    numeric = re.match(r"^(\d+(?:\.\d+)+)[.、)\s]", text)
    if numeric:
        return min(5, len(numeric.group(1).split(".")))
    numeric_single = re.match(r"^(\d+)[.)]?\s+\S", text)
    if numeric_single:
        return 1
    if re.match(r"^\d+[.、]\s+", text):
        return 1
    return 0


def _is_structural_heading(title: str) -> bool:
    return bool(
        re.match(r"^第[〇零一二三四五六七八九十百千万0-9]+[卷篇编部]", title)
        or re.match(r"^[上中下]卷", title)
        or re.match(r"^(part|volume|book|appendix)\s+([ivxlcdm0-9]+|[a-z]+)", title, re.IGNORECASE)
    )


def _clean_title(title: str) -> str:
    title = CONTROL_CHARS.sub(" ", title)
    title = re.sub(r"[.．·…⋯•\s]+$", "", title, flags=re.UNICODE)
    title = re.sub(r"[ \t\r\n\f\v]+", " ", title)
    return title.strip()


def _stringify_title(value: Any) -> str:
    if value is None:
        return ""
    return _clean_title(str(value))


def _parse_page_number(value: Any, context: str) -> int:
    if isinstance(value, bool):
        raise CliError("TOC_PAGE_INVALID", f"{context} 的页码无效：{value}")
    if isinstance(value, int):
        page = value
    elif isinstance(value, float) and value.is_integer():
        page = int(value)
    elif isinstance(value, str):
        match = re.fullmatch(r"\s*(?:第)?\s*(\d{1,4})\s*(?:页)?\s*", value)
        if not match:
            raise CliError(
                "TOC_PAGE_INVALID",
                f"{context} 的页码无效：{value}",
                hint="页码必须是正整数。罗马数字页码请改用对应的物理页码。",
            )
        page = int(match.group(1))
    else:
        raise CliError("TOC_PAGE_INVALID", f"{context} 的页码无效：{value}")
    if page < 1:
        raise CliError("TOC_PAGE_INVALID", f"{context} 的页码必须大于 0。")
    return page


def normalize_levels(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    previous = 0
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        level = int(entry.get("level") or 1)
        if index == 0:
            if level != 1:
                warnings.append(
                    {
                        "code": "LEVEL_CLAMPED",
                        "message": f"第一项目录层级为 {level}，已调整为 1。",
                        "line": entry.get("source_line"),
                    }
                )
            level = 1
        else:
            level = max(1, level)
            if level > previous + 1:
                warnings.append(
                    {
                        "code": "LEVEL_CLAMPED",
                        "message": f"「{entry['title']}」层级从 {level} 调整为 {previous + 1}（不能跳级）。",
                        "line": entry.get("source_line"),
                    }
                )
                level = previous + 1
        previous = level
        normalized.append({**entry, "level": level})
    return normalized, warnings


def resolve_entry_pages(
    entries: list[dict[str, Any]],
    page_count: int,
    page_mode: str,
    offset: int = 0,
    page_map: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    for entry in entries:
        mapped = page_map.get(int(entry["page"])) if page_map else None
        if mapped is not None:
            page_index = mapped - 1
        elif page_mode == "printed":
            page_index = entry["page"] + offset - 1
        else:
            page_index = entry["page"] - 1
        if page_index < 0 or page_index >= page_count:
            reason = "小于第 1 页" if page_index < 0 else f"超过总页数 {page_count}"
            if mapped is not None:
                reason = f"页码映射到第 {mapped} 页，{reason}"
            errors.append(
                {
                    "line": entry.get("source_line"),
                    "title": entry["title"],
                    "page": entry["page"],
                    "reason": reason,
                }
            )
        resolved.append({**entry, "page_index": page_index})
    if errors:
        raise CliError(
            "TOC_PAGE_OUT_OF_RANGE",
            "目录中存在超出 PDF 页数范围的页码。",
            details={
                "pageCount": page_count,
                "pageMode": page_mode,
                "offset": offset,
                "pageMap": page_map or {},
                "entries": errors[:10],
                "total": len(errors),
            },
            hint=(
                "请检查页码是印刷页码、偏移量或 pageMap 是否正确，或改用 --page-mode physical。"
                if page_mode == "printed"
                else "请检查目录页码是否对应 PDF 的物理页（从 1 开始）。"
            ),
        )
    return resolved


def build_tree(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    root: dict[str, Any] = {"level": 0, "children": []}
    stack = [root]
    for entry in entries:
        while len(stack) > 1 and stack[-1]["level"] >= entry["level"]:
            stack.pop()
        node = {**entry, "children": []}
        stack[-1]["children"].append(node)
        stack.append(node)
    return root["children"]


def tree_to_markdown(nodes: list[dict[str, Any]], level: int = 0) -> str:
    lines: list[str] = []
    for node in nodes:
        lines.append(f"{'  ' * level}- {node['title']} {node['page']}")
        if node.get("children"):
            lines.append(tree_to_markdown(node["children"], level + 1))
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# PDF reading / inspection
# ---------------------------------------------------------------------------


def open_pdf(path: str | Path):
    require_pypdf()
    target = Path(path).resolve()
    if not target.is_file():
        raise CliError("FILE_NOT_FOUND", f"PDF 文件不存在：{target}", hint="请检查路径；包含空格或中文时请使用引号。")
    try:
        reader = PdfReader(str(target))
    except Exception as exc:
        raise CliError(
            "PDF_PARSE_FAILED",
            f"PDF 解析失败：{target}",
            details=str(exc),
            hint="文件可能已损坏、下载不完整，或不是真正的 PDF。",
        ) from exc
    if reader.is_encrypted:
        raise CliError(
            "PDF_ENCRYPTED",
            "PDF 已加密，无法安全添加书签。",
            hint="请先用 qpdf --decrypt 或 PDF 工具解除密码保护，再重试。不要绕过加密写书签。",
        )
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise CliError("PDF_PARSE_FAILED", f"PDF 页面读取失败：{target}", details=str(exc)) from exc
    if page_count == 0:
        raise CliError("INVALID_PDF", f"PDF 没有页面：{target}")
    try:
        if not reader.pdf_header.startswith("%PDF-"):
            raise CliError("INVALID_PDF", f"文件不是有效的 PDF：{target}")
    except CliError:
        raise
    except Exception:
        pass
    return reader


def read_outline(reader) -> dict[str, Any]:
    try:
        raw = reader.outline
    except Exception as exc:
        raise CliError("OUTLINE_READ_FAILED", "读取 PDF 书签失败。", details=str(exc)) from exc

    items: list[dict[str, Any]] = []

    def walk(entries, level: int) -> None:
        for entry in entries or []:
            if isinstance(entry, list):
                walk(entry, level + 1)
                continue
            title = getattr(entry, "title", None)
            if title is None:
                try:
                    title = entry.get("/Title", "")
                except Exception:
                    title = ""
            try:
                page = reader.get_destination_page_number(entry) + 1
            except Exception:
                page = -1
            items.append({"level": level, "title": str(title), "page": int(page)})

    if isinstance(raw, list):
        walk(raw, 1)
    elif raw:
        walk([raw], 1)

    return {
        "count": len(items),
        "top_level": sum(1 for item in items if item["level"] == 1),
        "items": items,
        "exists": bool(items),
    }


def get_page_labels(reader) -> list[str]:
    try:
        root = reader.trailer["/Root"]
        if "/PageLabels" not in root:
            return []
        labels = reader.page_labels
    except Exception:
        return []
    if not labels:
        return []
    return [str(label) for label in labels]


def offset_from_labels(labels: list[str]) -> dict[str, Any] | None:
    observations = []
    for index, label in enumerate(labels):
        match = re.fullmatch(r"\s*(\d{1,4})\s*", label)
        if not match:
            continue
        printed = int(match.group(1))
        observations.append({"page": index + 1, "printed": printed, "offset": index + 1 - printed})
    if not observations:
        return None
    votes: dict[int, int] = {}
    for item in observations:
        votes[item["offset"]] = votes.get(item["offset"], 0) + 1
    value, count = max(votes.items(), key=lambda pair: pair[1])
    confidence = "high" if count >= 2 else "medium"
    return {
        "detected": value,
        "confidence": confidence,
        "source": "page-labels",
        "votes": count,
        "observations": len(observations),
        "samples": [item for item in observations if item["offset"] == value][:8],
    }


def _sample_pages(page_count: int, count: int, start_ratio: float = 0.0) -> list[int]:
    if page_count <= 0:
        return []
    first = min(page_count - 1, int(page_count * start_ratio))
    last = page_count - 1
    available = last - first + 1
    if available <= count:
        return list(range(first, last + 1))
    pages = {first, last}
    for i in range(1, count - 1):
        pages.add(first + round((available - 1) * i / (count - 1)))
    return sorted(pages)


def _page_number_from_line(line: str) -> int | None:
    line = line.strip()
    if not line or len(line) > 100:
        return None

    compact = re.sub(r"\s+", "", line)
    match = re.fullmatch(r"[—–－\-·.…⋯]*(\d{1,4})[—–－\-·.…⋯]*", compact)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"第(\d{1,4})页", compact)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"(\d{1,4})/\d{1,4}", compact)
    if match:
        return int(match.group(1))
    match = re.fullmatch(r"(?:page|p\.?)(\d{1,4})", compact, re.IGNORECASE)
    if match:
        return int(match.group(1))

    match = re.match(r"^(\d{1,4})\s{2,}\S", line)
    if match:
        return int(match.group(1))
    match = re.match(r"^(\d{1,4})\s+第", line)
    if match:
        return int(match.group(1))
    match = re.search(r"\s{2,}(\d{1,4})\s*$", line)
    if match:
        return int(match.group(1))
    return None


def _page_offset_observation(reader, index: int) -> dict[str, Any] | None:
    try:
        text = reader.pages[index].extract_text() or ""
    except Exception:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    candidates = lines[:2] + lines[-2:]
    for line in candidates:
        printed = _page_number_from_line(line)
        if printed is None or printed < 1:
            continue
        page_number = index + 1
        offset = page_number - printed
        if offset < -10:
            continue
        return {"page": page_number, "printed": printed, "offset": offset, "line": line[:60]}
    return None


def detect_text_offset(reader, page_count: int, samples: int = 30) -> dict[str, Any]:
    pages = _sample_pages(page_count, samples, 0.06 if page_count > 40 else 0.0)
    observations = []
    for index in pages:
        observation = _page_offset_observation(reader, index)
        if observation:
            observations.append(observation)
    if not observations:
        return {"detected": None, "confidence": "none", "source": "text", "votes": 0, "observations": 0, "samples": []}

    votes: dict[int, int] = {}
    for observation in observations:
        votes[observation["offset"]] = votes.get(observation["offset"], 0) + 1
    ranked = sorted(votes.items(), key=lambda pair: pair[1], reverse=True)
    best_offset, best_votes = ranked[0]
    second_votes = ranked[1][1] if len(ranked) > 1 else 0
    ratio = best_votes / len(observations)
    confidence = "low"
    if best_votes >= 3 and ratio >= 0.6 and best_votes >= second_votes + 2:
        confidence = "high"
    elif best_votes >= 2 and ratio >= 0.4:
        confidence = "medium"

    matching = [item for item in observations if item["offset"] == best_offset]
    consecutive = 0
    for previous, current in zip(matching, matching[1:]):
        if current["page"] == previous["page"] + 1 and current["printed"] == previous["printed"] + 1:
            consecutive += 1
    if consecutive >= 2 and confidence == "medium":
        confidence = "high"

    return {
        "detected": best_offset,
        "confidence": confidence,
        "source": "text",
        "votes": best_votes,
        "observations": len(observations),
        "samples": matching[:8],
    }


def analyze_text(reader, stat_samples: int = 14, offset_samples: int = 30) -> dict[str, Any]:
    page_count = len(reader.pages)
    stat_pages = _sample_pages(page_count, stat_samples)
    pages_with_text = 0
    failures = []
    for index in stat_pages:
        try:
            text = reader.pages[index].extract_text() or ""
            if len(text.strip()) >= 20:
                pages_with_text += 1
        except Exception as exc:
            failures.append({"page": index + 1, "message": str(exc)})
    ratio = pages_with_text / len(stat_pages) if stat_pages else 0.0
    return {
        "text": {
            "sampled": len(stat_pages),
            "pagesWithText": pages_with_text,
            "ratio": round(ratio, 3),
            "scanned": ratio < 0.15,
            "failures": failures,
        },
        "offset": detect_text_offset(reader, page_count, offset_samples),
    }


def inspect_pdf(path: str | Path, text_samples: int = 14, offset_samples: int = 30) -> dict[str, Any]:
    target = Path(path).resolve()
    reader = open_pdf(target)
    labels = get_page_labels(reader)
    outline = read_outline(reader)
    analysis = analyze_text(reader, text_samples, offset_samples)
    text = analysis["text"]
    text_offset = analysis["offset"]
    label_offset = offset_from_labels(labels)
    offset = label_offset or text_offset

    recommendations = []
    if outline["count"] > 0:
        recommendations.append(
            {
                "code": "HAS_OUTLINE",
                "message": f"已有 {outline['count']} 个书签。请选择 replace（替换）/ merge（追加）/ keep（保留并拒绝修改）。",
            }
        )
    if labels:
        examples = []
        for index in _sample_pages(len(labels), 6):
            examples.append(f"第 {index + 1} 页={labels[index]}")
        recommendations.append({"code": "HAS_PAGE_LABELS", "message": f"PDF 自带页码标签，例如 {'、'.join(examples)}"})
    if text["scanned"]:
        recommendations.append(
            {
                "code": "SCANNED",
                "message": (
                    "未检测到文字层，属于扫描件。添加书签仍然可用，但无法自动检测印刷页码偏移；"
                    "请提供 --offset（物理页 - 印刷页）或 --anchor（印刷页=物理页），或使用 --page-mode physical。"
                ),
            }
        )
    elif offset and offset.get("detected") is not None:
        sign = "+" if offset["detected"] >= 0 else ""
        source = "来自 PDF 页码标签" if offset.get("source") == "page-labels" else "来自页眉/页脚文字"
        recommendations.append(
            {
                "code": "OFFSET_DETECTED",
                "message": (
                    f"检测到印刷页码偏移 {sign}{offset['detected']}（{source}，置信度：{offset['confidence']}）。"
                    "若目录使用印刷页码，会自动使用该偏移。"
                ),
            }
        )
    if not text["scanned"] and not offset:
        recommendations.append(
            {
                "code": "NO_OFFSET",
                "message": "未能从页眉/页脚检测到印刷页码。若目录页码不是 PDF 物理页，请提供 --offset 或 --anchor。",
            }
        )

    top_level = [item for item in outline["items"] if item["level"] == 1][:8]
    header = ""
    try:
        header = str(reader.pdf_header)
    except Exception:
        header = ""

    return {
        "file": str(target),
        "sizeBytes": target.stat().st_size,
        "pdfVersion": header.replace("%PDF-", "") if header else None,
        "pages": len(reader.pages),
        "encrypted": False,
        "pageLabels": {
            "present": bool(labels),
            "count": len(labels),
            "examples": [
                {"page": index + 1, "label": labels[index]} for index in _sample_pages(len(labels), 6)
            ]
            if labels
            else [],
        },
        "text": text,
        "offset": offset,
        "footerOffset": text_offset if label_offset else None,
        "outline": {
            "count": outline["count"],
            "topLevelCount": outline["top_level"],
            "topLevel": [{"title": item["title"], "page": item["page"]} for item in top_level],
            "firstItems": outline["items"][:5],
        },
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# Outline writing / verification
# ---------------------------------------------------------------------------


def _safe_title(title: str) -> str:
    title = CONTROL_CHARS.sub(" ", str(title))
    return re.sub(r"[ \t\r\n\f\v]+", " ", title).strip()


def write_pdf(reader, entries: list[dict[str, Any]], mode: str, open_panel: bool) -> PdfWriter:
    writer = PdfWriter(clone_from=reader)
    if mode == "replace":
        if NameObject("/Outlines") in writer._root_object:
            del writer._root_object[NameObject("/Outlines")]

    def add_nodes(nodes: list[dict[str, Any]], parent) -> None:
        for node in nodes:
            item = writer.add_outline_item(
                title=_safe_title(node["title"]),
                page_number=int(node["page_index"]),
                parent=parent,
                is_open=True,
            )
            if node.get("children"):
                add_nodes(node["children"], item)

    add_nodes(build_tree(entries), None)
    if open_panel:
        writer.page_mode = "/UseOutlines"
    return writer


def _save_atomic(writer: PdfWriter, target: Path) -> None:
    temp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        with temp.open("wb") as handle:
            writer.write(handle)
        os.replace(temp, target)
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        raise CliError(
            "WRITE_FAILED",
            f"写入输出文件失败：{target}",
            details=str(exc),
            hint="请检查磁盘空间和文件夹权限。",
        ) from exc


def verify_outline(reader, entries: list[dict[str, Any]], mode: str, existing_count: int, page_count: int) -> dict[str, Any]:
    outline = read_outline(reader)
    toc = outline["items"]
    expected_count = existing_count + len(entries) if mode == "merge" else len(entries)
    actual = toc[-len(entries):] if mode == "merge" else toc
    mismatches = []
    for index, (actual_item, expected) in enumerate(zip(actual, entries), start=1):
        expected_page = int(expected["page_index"]) + 1
        if (
            int(actual_item["level"]) != int(expected["level"])
            or str(actual_item["title"]) != expected["title"]
            or int(actual_item["page"]) != expected_page
        ):
            mismatches.append(
                {
                    "index": index,
                    "expected": {"title": expected["title"], "page": expected_page, "level": expected["level"]},
                    "actual": {
                        "title": actual_item["title"],
                        "page": actual_item["page"],
                        "level": actual_item["level"],
                    },
                }
            )
            if len(mismatches) >= 8:
                break
    unresolved = sum(1 for item in actual if int(item["page"]) < 1)
    out_of_range = sum(1 for item in actual if int(item["page"]) > page_count)
    return {
        "ok": not mismatches and len(toc) == expected_count and unresolved == 0 and out_of_range == 0,
        "count": len(toc),
        "expectedCount": expected_count,
        "topLevel": outline["top_level"],
        "unresolved": unresolved,
        "outOfRange": out_of_range,
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Add / verify / extract
# ---------------------------------------------------------------------------


def _load_page_map(value: Any, base_dir: Path | None = None) -> dict[int, int] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        data = value
    else:
        path = Path(str(value))
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        path = path.resolve()
        if not path.is_file():
            raise CliError("PAGE_MAP_NOT_FOUND", f"pageMap 文件不存在：{path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise CliError("PAGE_MAP_INVALID", f"pageMap 解析失败：{path}", details=str(exc)) from exc
        if isinstance(data, dict) and "pageMap" in data:
            data = data["pageMap"]
    if not isinstance(data, dict):
        raise CliError(
            "PAGE_MAP_INVALID",
            "pageMap 必须是对象，例如 {\"6\": 27, \"95\": 116}。",
            hint="键是印刷页码，值是 PDF 物理页码（都从 1 开始）。",
        )
    result: dict[int, int] = {}
    for key, raw_value in data.items():
        try:
            printed = int(key)
            physical = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise CliError("PAGE_MAP_INVALID", f"pageMap 中的映射无效：{key} -> {raw_value}") from exc
        if printed < 1 or physical < 1:
            raise CliError("PAGE_MAP_INVALID", f"pageMap 页码必须大于 0：{key} -> {raw_value}")
        result[printed] = physical
    return result


def parse_page_spec(spec: str | None, page_count: int) -> list[int]:
    if not spec:
        return list(range(min(page_count, 30)))
    pages: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        elif part.isdigit():
            pages.add(int(part))
        else:
            raise CliError("PAGE_SPEC_INVALID", f"页码范围格式错误：{part}", exit_code=2)
    selected = sorted(page for page in pages if 1 <= page <= page_count)
    if not selected:
        raise CliError("PAGE_SPEC_INVALID", f"页码范围不在 1..{page_count} 内：{spec}", exit_code=2)
    return [page - 1 for page in selected]


def extract_text_pages(path: str | Path, pages_spec: str | None = None) -> dict[str, Any]:
    target = Path(path).resolve()
    reader = open_pdf(target)
    indices = parse_page_spec(pages_spec, len(reader.pages))
    items = []
    for index in indices:
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception as exc:
            items.append({"page": index + 1, "chars": 0, "text": "", "error": str(exc)})
            continue
        head = text[:500]
        score = 0
        if "目录" in head or re.search(r"contents", head, re.IGNORECASE):
            score += 5
        if len(re.findall(r"(?:\.{3,}|…{2,}|·{3,})\s*\d{1,4}", text)) >= 3:
            score += 3
        if re.search(r"\n\s*\d{1,4}\s*\n", text):
            score += 1
        items.append(
            {
                "page": index + 1,
                "chars": len(text),
                "score": score,
                "text": text,
            }
        )
    return {
        "file": str(target),
        "pages": len(reader.pages),
        "range": pages_spec or f"1-{min(len(reader.pages), 30)}",
        "items": items,
        "tocCandidates": [item["page"] for item in items if item.get("score", 0) >= 5],
    }


def _require_render_deps():
    try:
        import pypdfium2 as pdfium
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise CliError(
            "MISSING_RENDER_DEP",
            "缺少页面渲染依赖（pypdfium2 / Pillow）。",
            hint="使用 `uv run --script <本脚本>` 会自动安装；或运行 `python -m pip install pypdfium2 pillow`。",
            details=str(exc),
        ) from exc
    return pdfium, Image, ImageDraw


def render_pdf_pages(
    path: str | Path,
    pages_spec: str | None,
    out_dir: str | Path,
    scale: float | None = None,
    image_format: str = "png",
    sheet: bool = False,
    cols: int = 6,
    rows: int = 3,
    quality: int = 82,
) -> dict[str, Any]:
    pdfium, Image, ImageDraw = _require_render_deps()
    target = Path(path).resolve()
    if not target.is_file():
        raise CliError("FILE_NOT_FOUND", f"PDF 文件不存在：{target}")
    output_dir = Path(out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if image_format not in ("png", "jpeg", "jpg"):
        raise CliError("RENDER_FORMAT_INVALID", f"不支持的图片格式：{image_format}", exit_code=2)
    extension = "jpg" if image_format in ("jpeg", "jpg") else "png"
    if scale is None:
        scale = 0.7 if sheet else 1.5
    try:
        document = pdfium.PdfDocument(str(target))
    except Exception as exc:
        raise CliError("RENDER_FAILED", f"PDF 打开失败：{exc}") from exc

    try:
        page_count = len(document)
        indices = parse_page_spec(pages_spec, page_count)
        files: list[dict[str, Any]] = []
        if sheet:
            per_sheet = max(1, int(cols) * int(rows))
            for start in range(0, len(indices), per_sheet):
                chunk = indices[start:start + per_sheet]
                images = []
                for index in chunk:
                    page = document[index]
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil().convert("RGB")
                    images.append((index + 1, image))
                width = max(image.width for _, image in images)
                height = max(image.height for _, image in images)
                padding = 8
                label_height = 22
                canvas = Image.new(
                    "RGB",
                    (cols * (width + padding) + padding, rows * (height + padding + label_height) + padding),
                    "white",
                )
                draw = ImageDraw.Draw(canvas)
                for position, (page_number, image) in enumerate(images):
                    row, column = divmod(position, cols)
                    x = padding + column * (width + padding)
                    y = padding + row * (height + padding + label_height)
                    draw.text((x + 4, y + 4), f"p.{page_number}", fill="red")
                    canvas.paste(image, (x, y + label_height))
                first, last = chunk[0] + 1, chunk[-1] + 1
                output = output_dir / f"sheet-{first}-{last}.{extension}"
                if extension == "jpg":
                    canvas.save(output, quality=quality, optimize=True)
                else:
                    canvas.save(output, optimize=True)
                files.append(
                    {
                        "path": str(output),
                        "type": "sheet",
                        "firstPage": first,
                        "lastPage": last,
                        "width": canvas.width,
                        "height": canvas.height,
                    }
                )
        else:
            for index in indices:
                page = document[index]
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                output = output_dir / f"page-{index + 1:03d}.{extension}"
                if extension == "jpg":
                    image.convert("RGB").save(output, quality=quality, optimize=True)
                else:
                    image.save(output, optimize=True)
                files.append(
                    {
                        "path": str(output),
                        "type": "page",
                        "page": index + 1,
                        "width": image.width,
                        "height": image.height,
                    }
                )
        return {
            "ok": True,
            "file": str(target),
            "pageCount": page_count,
            "outDir": str(output_dir),
            "mode": "sheet" if sheet else "pages",
            "scale": scale,
            "format": extension,
            "files": files,
        }
    except CliError:
        raise
    except Exception as exc:
        raise CliError("RENDER_FAILED", f"页面渲染失败：{exc}") from exc
    finally:
        try:
            document.close()
        except Exception:
            pass


def scan_pdf(
    path: str | Path,
    pages_spec: str | None = None,
    out_dir: str | Path | None = None,
    force_render: bool = False,
    sheet: bool = True,
    cols: int = 6,
    rows: int = 3,
    scale: float | None = None,
    image_format: str = "png",
    quality: int = 82,
) -> dict[str, Any]:
    """One-shot analysis for TOC recognition.

    Text PDFs return page text; scanned PDFs also render the requested pages.
    """
    target = Path(path).resolve()
    info = inspect_pdf(target)
    range_spec = pages_spec or "1-40"
    text_data = extract_text_pages(target, range_spec)
    has_text = any(item.get("chars", 0) >= 20 for item in text_data["items"])
    should_render = bool(force_render or info["text"]["scanned"] or not has_text)
    rendered = None
    if should_render:
        render_dir = Path(out_dir).resolve() if out_dir else Path(tempfile.mkdtemp(prefix="pdf-toc-scan-"))
        rendered = render_pdf_pages(
            target,
            range_spec,
            render_dir,
            scale=scale,
            image_format=image_format,
            sheet=sheet,
            cols=cols,
            rows=rows,
            quality=quality,
        )
    return {
        **info,
        "scanRange": range_spec,
        "textPages": text_data["items"],
        "tocCandidates": text_data["tocCandidates"],
        "rendered": rendered,
    }


def check_pages(
    path: str | Path,
    toc_path: str,
    mode: str = "last",
    offset: int | None = None,
    page_map: str | dict[str, Any] | None = None,
    out_dir: str | Path | None = None,
    sheet: bool = True,
    cols: int = 6,
    rows: int = 3,
    scale: float | None = None,
    image_format: str = "png",
    quality: int = 82,
) -> dict[str, Any]:
    """Render the unique physical pages that need printed-page verification.

    `mode=last` checks only the last TOC entry; `mode=all` checks every
    distinct physical page. Entries that share a physical page are grouped so
    the page is rendered and read only once.
    """
    target = Path(path).resolve()
    toc_file = Path(toc_path).resolve()
    if not toc_file.is_file():
        raise CliError("FILE_NOT_FOUND", f"目录文件不存在：{toc_file}")
    reader = open_pdf(target)
    page_count = len(reader.pages)
    labels = get_page_labels(reader)
    parsed = parse_toc_content(toc_file.read_text(encoding="utf-8-sig"), str(toc_file))
    entries, _ = normalize_levels(parsed["entries"])
    mapping = _load_page_map(page_map)

    page_mode = parsed.get("pageMode")
    if page_mode is None:
        page_mode = "printed" if (offset is not None or mapping) else "physical"
    if page_mode == "printed" and offset is None and not mapping:
        label_offset = offset_from_labels(labels)
        if label_offset and label_offset.get("detected") is not None:
            offset = int(label_offset["detected"])
        else:
            text_offset = detect_text_offset(reader, page_count, 20)
            if text_offset.get("detected") is not None:
                offset = int(text_offset["detected"])
            else:
                raise CliError(
                    "OFFSET_REQUIRED",
                    "核对页码前需要先提供偏移量或 pageMap。",
                    hint="用 --offset N 或 --page-map <file>；扫描件可先用 scan 查看目录页和正文页码。",
                )
    resolved = resolve_entry_pages(entries, page_count, page_mode, offset or 0, mapping)

    if mode == "last":
        last_page = int(resolved[-1]["page_index"])
        selected = [entry for entry in resolved if int(entry["page_index"]) == last_page]
    elif mode == "all":
        selected = resolved
    else:
        raise CliError("CHECK_MODE_INVALID", f"未知核对模式：{mode}", exit_code=2)

    grouped: dict[int, dict[str, Any]] = {}
    for entry in selected:
        page_index = int(entry["page_index"])
        group = grouped.setdefault(page_index, {"physicalPage": page_index + 1, "titles": [], "printedPages": []})
        if entry["title"] not in group["titles"]:
            group["titles"].append(entry["title"])
        if int(entry["page"]) not in group["printedPages"]:
            group["printedPages"].append(int(entry["page"]))

    items = [grouped[key] for key in sorted(grouped)]
    pages_spec = ",".join(str(item["physicalPage"]) for item in items)
    render_dir = Path(out_dir).resolve() if out_dir else Path(tempfile.mkdtemp(prefix="pdf-toc-check-"))
    use_sheet = sheet and len(items) > 1
    rendered = render_pdf_pages(
        target,
        pages_spec,
        render_dir,
        scale=scale if scale is not None else (0.9 if use_sheet else 1.5),
        image_format=image_format,
        sheet=use_sheet,
        cols=cols,
        rows=rows,
        quality=quality,
    )
    return {
        "ok": True,
        "file": str(target),
        "mode": mode,
        "offset": offset or 0,
        "pageMapEntries": len(mapping or {}),
        "uniquePages": len(items),
        "items": items,
        "rendered": rendered,
        "instruction": (
            "读取 rendered.files，核对每页上实际印刷页码是否等于 printedPages。"
            "同一物理页对应多个标题时只读一次；不一致时生成 pageMap 后再写入。"
        ),
    }


def _resolve_offset(offset: int | None, anchors: list[str] | None) -> int | None:
    if isinstance(anchors, str):
        anchors = [anchors]
    parsed = []
    for raw in anchors or []:
        match = re.fullmatch(r"\s*(\d+)\s*[=:]\s*(\d+)\s*", str(raw))
        if not match:
            raise CliError(
                "ANCHOR_INVALID",
                f"--anchor 格式错误：{raw}",
                hint="格式为 印刷页码=PDF物理页码，例如 --anchor 10=31（表示书上第 10 页在 PDF 第 31 页）。",
                exit_code=2,
            )
        printed, physical = int(match.group(1)), int(match.group(2))
        parsed.append({"printed": printed, "physical": physical, "offset": physical - printed})
    if offset is not None and parsed:
        raise CliError(
            "OFFSET_CONFLICT",
            "--offset 与 --anchor 不能同时使用。",
            hint="只保留一个即可；--anchor 10=31 等价于 --offset 21。",
            exit_code=2,
        )
    if not parsed:
        return offset
    values = {item["offset"] for item in parsed}
    if len(values) > 1:
        raise CliError(
            "ANCHOR_CONFLICT",
            "多个 --anchor 计算出的偏移不一致。",
            details=parsed,
            hint="同一本 PDF 的 物理页 - 印刷页 应该相同。",
            exit_code=2,
        )
    return values.pop()


def _choose_page_mode(
    requested: str | None, source_mode: str | None, offset_provided: bool, detected: dict[str, Any] | None
) -> str:
    reliable = bool(detected and detected.get("detected") is not None and detected.get("confidence") in ("high", "medium"))
    if requested == "auto":
        if offset_provided:
            return "printed"
        return "printed" if reliable else "physical"
    if requested in ("physical", "printed"):
        return requested
    if offset_provided:
        return "printed"
    if source_mode in ("physical", "printed"):
        return source_mode
    return "printed" if reliable else "physical"


def _same_path(left: Path, right: Path) -> bool:
    a = str(left.resolve())
    b = str(right.resolve())
    if os.name == "nt":
        return a.casefold() == b.casefold()
    return a == b


def _resolve_output(
    input_path: Path, out: str | None, out_dir: str | None, suffix: str, on_exists: str
) -> tuple[Path, bool]:
    if out:
        target = Path(out).resolve()
    else:
        directory = Path(out_dir).resolve() if out_dir else input_path.parent
        target = directory / f"{input_path.stem}{suffix}{input_path.suffix or '.pdf'}"
    if _same_path(input_path, target):
        raise CliError(
            "OUTPUT_IS_INPUT",
            "输出路径不能与原文件相同；默认不会修改原文件。",
            hint="请使用默认后缀输出，或通过 --out 指定另一个文件。",
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not os.access(target.parent, os.W_OK):
            raise OSError("not writable")
    except OSError as exc:
        raise CliError("DIR_NOT_WRITABLE", f"输出目录不可写：{target.parent}", details=str(exc)) from exc

    if target.exists():
        if on_exists == "error":
            raise CliError(
                "OUTPUT_EXISTS",
                f"输出文件已存在：{target}",
                hint="使用 --on-exists overwrite 覆盖，或 --on-exists rename 自动改名。",
            )
        if on_exists == "overwrite":
            return target, True
        counter = 2
        while True:
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            if not candidate.exists():
                return candidate, False
            counter += 1
    return target, False


def add_pdf(
    pdf_path: str | Path,
    toc_path: str | None = None,
    toc_content: str | None = None,
    out: str | None = None,
    out_dir: str | None = None,
    suffix: str = DEFAULT_SUFFIX,
    mode: str = "replace",
    page_mode: str | None = None,
    offset: int | None = None,
    anchors: list[str] | None = None,
    page_map: str | dict[str, Any] | None = None,
    on_exists: str = "rename",
    dry_run: bool = False,
    verify: bool = True,
    open_panel: bool = True,
) -> dict[str, Any]:
    started = time.time()
    input_path = Path(pdf_path).resolve()
    reader = open_pdf(input_path)
    page_count = len(reader.pages)
    existing = read_outline(reader)
    labels = get_page_labels(reader)
    label_offset = offset_from_labels(labels)
    explicit_offset = _resolve_offset(offset, anchors)
    mapping = _load_page_map(page_map)
    offset_provided = explicit_offset is not None

    if toc_content is not None:
        parsed = parse_toc_content(toc_content, toc_path or "")
    else:
        if not toc_path:
            raise CliError(
                "TOC_REQUIRED",
                "缺少目录文件。",
                hint="使用 --toc <file.json|md|txt> 指定目录，或 --toc - 从标准输入读取。",
                exit_code=2,
            )
        if toc_path == "-":
            parsed = parse_toc_content(sys.stdin.read(), "")
        else:
            toc_file = Path(toc_path).resolve()
            if not toc_file.is_file():
                raise CliError("FILE_NOT_FOUND", f"目录文件不存在：{toc_file}")
            parsed = parse_toc_content(toc_file.read_text(encoding="utf-8-sig"), str(toc_file))

    text_analysis = None
    detected = label_offset
    need_analysis = not label_offset and page_mode in (None, "auto", "printed") and not offset_provided
    if need_analysis:
        text_analysis = analyze_text(reader)
        detected = text_analysis["offset"]
    elif label_offset and page_mode in (None, "auto") and not offset_provided:
        text_analysis = analyze_text(reader, offset_samples=1)

    effective_mode = _choose_page_mode(page_mode, parsed.get("pageMode"), offset_provided, detected)
    effective_offset = 0
    warnings = list(parsed.get("warnings") or [])
    if effective_mode == "printed":
        if explicit_offset is not None:
            effective_offset = explicit_offset
        elif detected and detected.get("detected") is not None and detected.get("confidence") in ("high", "medium"):
            effective_offset = int(detected["detected"])
        else:
            effective_offset = 0
            warnings.append(
                {
                    "code": "OFFSET_UNCERTAIN",
                    "message": "目录按印刷页码处理，但未能可靠检测页码偏移，已按偏移 0 计算。",
                    "hint": "请用 --offset N 或 --anchor 印刷页=物理页 明确指定。",
                }
            )

    if (
        effective_mode == "printed"
        and explicit_offset is not None
        and detected
        and detected.get("detected") is not None
        and detected.get("confidence") in ("high", "medium")
        and int(detected["detected"]) != int(explicit_offset)
    ):
        warnings.append(
            {
                "code": "OFFSET_MISMATCH",
                "message": (
                    f"你指定的偏移是 {explicit_offset}，但自动检测到的偏移是 {detected['detected']}。"
                    "请确认没有照抄示例值。"
                ),
                "hint": "如果当前 PDF 的页码确实不同，可忽略；否则改用检测到的偏移。",
            }
        )

    if text_analysis and text_analysis["text"]["scanned"] and effective_mode != "physical" and not offset_provided:
        warnings.append(
            {
                "code": "SCANNED_NO_TEXT",
                "message": "这是扫描件，没有文字层，无法自动校准印刷页码。",
                "hint": "若页码超出范围或书签落点不对，请改用 --page-mode physical，或提供 --offset/--anchor。",
            }
        )

    entries, level_warnings = normalize_levels(parsed["entries"])
    warnings.extend(level_warnings)
    try:
        entries = resolve_entry_pages(entries, page_count, effective_mode, effective_offset, mapping)
    except CliError as error:
        fallback = detected.get("detected") if detected else None
        if (
            effective_mode == "printed"
            and effective_offset == 0
            and fallback not in (None, 0)
            and error.code == "TOC_PAGE_OUT_OF_RANGE"
            and not mapping
        ):
            entries = resolve_entry_pages(entries, page_count, effective_mode, int(fallback), mapping)
            effective_offset = int(fallback)
            warnings.append(
                {"code": "OFFSET_FALLBACK", "message": f"偏移 0 会导致页码越界，已自动改用检测到的偏移 {effective_offset}。"}
            )
        else:
            raise

    if existing["count"] > 0:
        if mode == "keep":
            raise CliError(
                "OUTLINE_EXISTS",
                f"PDF 已有 {existing['count']} 个书签，--mode keep 不会修改。",
                hint="使用 --mode replace 替换，或 --mode merge 追加。",
            )
        if mode == "replace":
            warnings.append(
                {
                    "code": "OUTLINE_REPLACED",
                    "message": f"已替换原有的 {existing['count']} 个书签。",
                    "hint": "如需保留原书签，请使用 --mode merge。",
                }
            )
        if mode == "merge" and any(int(item["page"]) < 1 for item in existing["items"]):
            warnings.append(
                {
                    "code": "UNRESOLVED_EXISTING",
                    "message": "原有书签中有不带页面目标的项（可能是网址/脚本动作）；合并后可能失效。",
                }
            )

    output, _ = _resolve_output(input_path, out, out_dir, suffix, on_exists)
    level_count = max((int(entry["level"]) for entry in entries), default=1)
    plan = {
        "ok": True,
        "dryRun": dry_run,
        "input": str(input_path),
        "output": str(output),
        "pages": page_count,
        "entries": len(entries),
        "levels": level_count,
        "mode": mode,
        "pageMode": effective_mode,
        "offset": effective_offset if effective_mode == "printed" else 0,
        "pageMap": mapping or {},
        "pageMapEntries": len(mapping or {}),
        "existingOutline": existing["count"],
        "warnings": warnings,
        "offsetDetection": detected,
        "textLayer": text_analysis["text"] if text_analysis else None,
        "preview": [
            {
                "title": entry["title"],
                "level": entry["level"],
                "tocPage": entry["page"],
                "physicalPage": int(entry["page_index"]) + 1,
            }
            for entry in entries[:12]
        ],
    }
    if dry_run:
        plan["durationMs"] = int((time.time() - started) * 1000)
        return plan

    writer = write_pdf(reader, entries, mode, open_panel)
    _save_atomic(writer, output)

    verification = None
    if verify:
        output_reader = open_pdf(output)
        verification = verify_outline(output_reader, entries, mode, existing["count"], page_count)
        if not verification["ok"]:
            raise CliError(
                "VERIFY_FAILED",
                f"输出文件已生成，但书签校验未通过：{output}",
                details=verification,
                hint="请保留该文件用于排查，并尝试 --mode replace 或检查目录数据。",
            )

    return {
        **plan,
        "dryRun": False,
        "sizeBytes": output.stat().st_size,
        "verification": verification,
        "durationMs": int((time.time() - started) * 1000),
    }


def verify_pdf(path: str | Path, expect_count: int | None = None) -> dict[str, Any]:
    target = Path(path).resolve()
    reader = open_pdf(target)
    outline = read_outline(reader)
    page_count = len(reader.pages)
    size = target.stat().st_size
    if outline["count"] == 0:
        raise CliError("NO_OUTLINE", "PDF 中没有书签。", hint="如果这是原始文件，请先用 add 添加目录。")
    if expect_count is not None and outline["count"] != expect_count:
        raise CliError("VERIFY_COUNT_MISMATCH", f"书签数量不符：期望 {expect_count}，实际 {outline['count']}。")
    unresolved = [item for item in outline["items"] if int(item["page"]) < 1]
    out_of_range = [item for item in outline["items"] if int(item["page"]) > page_count]
    return {
        "ok": not unresolved and not out_of_range,
        "file": str(target),
        "sizeBytes": size,
        "pages": page_count,
        "count": outline["count"],
        "topLevel": outline["top_level"],
        "unresolved": unresolved[:10],
        "outOfRange": out_of_range[:10],
        "firstItems": outline["items"][:5],
    }


def _tree_to_json(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for node in nodes:
        item = {"title": node["title"], "page": node["page"]}
        if node.get("children"):
            item["children"] = _tree_to_json(node["children"])
        result.append(item)
    return result


def extract_toc(path: str | Path, output_format: str = "json") -> dict[str, Any]:
    reader = open_pdf(path)
    outline = read_outline(reader)
    if outline["count"] == 0:
        raise CliError("NO_OUTLINE", "PDF 中没有可导出的书签。", hint="若是扫描件，需要先准备目录文件再使用 add。")
    flat = [item for item in outline["items"] if int(item["page"]) >= 1]
    skipped = outline["count"] - len(flat)
    if not flat:
        raise CliError("NO_OUTLINE_PAGES", "PDF 的书签都没有页面目标（可能全是网址或脚本动作），无法导出为目录。")
    warnings = []
    if skipped:
        warnings.append({"code": "EXTRACT_SKIPPED", "message": f"有 {skipped} 个书签没有页面目标，已跳过。"})

    tree = build_tree(flat)
    if output_format == "markdown":
        return {
            "format": "markdown",
            "count": len(flat),
            "skipped": skipped,
            "warnings": warnings,
            "content": tree_to_markdown(tree) + "\n",
        }

    content = json.dumps(
        {"pageMode": "physical", "source": Path(path).name, "entries": _tree_to_json(tree)},
        ensure_ascii=False,
        indent=2,
    )
    return {
        "format": "json",
        "count": len(flat),
        "skipped": skipped,
        "warnings": warnings,
        "content": content + "\n",
    }


def cleanup_workdir(path: str | Path, force: bool = False) -> dict[str, Any]:
    """Remove a temporary work directory created for scan/check-pages.

    Only directories under the system temp directory are removed by default.
    A directory elsewhere needs an explicit --force, and the current working
    directory (or one of its parents) is never removed.
    """
    target = Path(path).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    cwd = Path.cwd().resolve()
    if not target.exists():
        return {"ok": True, "removed": False, "path": str(target), "reason": "目录不存在"}
    if not target.is_dir():
        raise CliError("CLEANUP_NOT_DIR", f"不是目录：{target}", exit_code=2)
    if target == temp_root:
        raise CliError("CLEANUP_REFUSED", "拒绝删除系统临时目录根目录。", exit_code=2)
    if target == cwd or target in cwd.parents:
        raise CliError(
            "CLEANUP_REFUSED",
            f"拒绝删除当前项目目录或其上级目录：{target}",
            hint="清理命令只用于扫描/核对产生的临时目录。",
            exit_code=2,
        )
    allowed = target.is_relative_to(temp_root) or (target / ".pdf-toc-work").is_file()
    if not allowed and not force:
        raise CliError(
            "CLEANUP_REFUSED",
            f"目录不在系统临时目录下：{target}",
            hint="只清理临时工作目录；如果确认要删除，加 --force。",
            exit_code=2,
        )
    shutil.rmtree(target)
    return {"ok": True, "removed": True, "path": str(target)}




# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------


def doctor() -> dict[str, Any]:
    checks = []
    python_ok = sys.version_info >= (3, 9)
    checks.append(
        {
            "name": "python",
            "ok": python_ok,
            "detail": f"Python {sys.version.split()[0]}",
            "required": ">=3.9",
        }
    )

    global PdfReader, PdfWriter, NameObject
    if PdfReader is None:
        try:
            from pypdf import PdfReader as _PdfReader, PdfWriter as _PdfWriter
            from pypdf.generic import NameObject as _NameObject

            PdfReader, PdfWriter, NameObject = _PdfReader, _PdfWriter, _NameObject
        except ImportError:
            PdfReader = None

    pypdf_version = None
    if PdfReader is not None:
        try:
            from importlib.metadata import version as package_version

            pypdf_version = package_version("pypdf")
        except Exception:
            pypdf_version = "unknown"
    checks.append(
        {
            "name": "pypdf",
            "ok": PdfReader is not None,
            "detail": f"pypdf {pypdf_version}" if PdfReader is not None else "未安装",
            "install": "python -m pip install pypdf",
        }
    )

    render_ok = True
    render_detail = "已安装"
    try:
        import pypdfium2  # noqa: F401
        import PIL  # noqa: F401
    except ImportError as exc:
        render_ok = False
        render_detail = f"未安装（{exc}）"
    checks.append(
        {
            "name": "render-deps",
            "ok": render_ok,
            "detail": render_detail,
            "install": "python -m pip install pypdfium2 pillow",
            "optional": True,
        }
    )

    uv_path = shutil.which("uv")
    checks.append(
        {
            "name": "uv",
            "ok": bool(uv_path),
            "detail": uv_path or "未安装（本 skill 只使用 uv）",
        }
    )

    writable = True
    writable_error = ""
    try:
        probe = Path.cwd() / f".pdf-toc-write-test-{os.getpid()}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except Exception as exc:
        writable = False
        writable_error = str(exc)
    checks.append(
        {
            "name": "output-writable",
            "ok": writable,
            "detail": f"可写：{Path.cwd()}" if writable else writable_error,
        }
    )

    missing = [check["name"] for check in checks if not check["ok"] and not check.get("optional")]
    ready = not missing
    install_command = (
        "安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
        if not uv_path
        else f'uv run --script "{Path(__file__).resolve()}" doctor'
    )
    return {
        "ok": True,
        "ready": ready,
        "version": VERSION,
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
            "required": ">=3.9",
            "ok": python_ok,
        },
        "platform": f"{sys.platform} {os.name}",
        "checks": checks,
        "missing": missing,
        "capabilities": {
            "textPdf": True,
            "scannedPdf": True,
            "scannedAutoCalibration": False,
            "scannedRendering": render_ok,
            "ocr": False,
            "batch": True,
            "safeOutput": True,
        },
        "script": str(Path(__file__).resolve()),
        "install": install_command,
        "notes": [
            "pypdf 使用 BSD-3-Clause 许可证；页面渲染使用 pypdfium2 + Pillow。",
            "扫描件可以直接添加书签；目录页由 scan/render 渲染成图片，再由 Agent 视觉识别。",
            "agent 界面弹出命令授权时，请核对命令后允许；被拒绝时不要绕过。",
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--verbose", action="store_true", help="输出更多细节")


def build_parser() -> argparse.ArgumentParser:
    parser = PdfMenuParser(prog="pdf-toc", description="给 PDF 添加/替换目录（书签），默认生成新文件，不修改原文件。")
    parser.add_argument("--version", action="version", version=f"pdf-toc {VERSION}")
    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser("doctor", help="检查 Python / pypdf 环境")
    _add_common(doctor_parser)
    doctor_parser.add_argument("--require-ready", action="store_true", help="未就绪时返回退出码 3")

    inspect_parser = subparsers.add_parser("inspect", help="检查 PDF 页数、文字层、页码偏移、现有书签")
    _add_common(inspect_parser)
    inspect_parser.add_argument("pdf")
    inspect_parser.add_argument("--text-samples", type=int, default=14)
    inspect_parser.add_argument("--offset-samples", type=int, default=30)

    extract_parser = subparsers.add_parser("extract", help="导出 PDF 现有书签为 JSON / Markdown")
    _add_common(extract_parser)
    extract_parser.add_argument("pdf")
    extract_parser.add_argument("--out")
    extract_parser.add_argument("--format", choices=("json", "markdown", "md"))
    extract_parser.add_argument("--force", action="store_true")

    text_parser = subparsers.add_parser("text", help="导出指定页的文字，用于自动识别目录页")
    _add_common(text_parser)
    text_parser.add_argument("pdf")
    text_parser.add_argument("--pages", help="页码范围，例如 1-30 或 1,5,9-12；默认前 30 页")

    scan_parser = subparsers.add_parser("scan", help="一次完成 PDF 检查 + 目录页文字/图片导出")
    _add_common(scan_parser)
    scan_parser.add_argument("pdf")
    scan_parser.add_argument("--pages", default="1-40", help="扫描范围，默认 1-40")
    scan_parser.add_argument("--out-dir")
    scan_parser.add_argument("--render", action="store_true", help="即使有文字层也强制渲染")
    scan_parser.add_argument("--no-sheet", action="store_true", help="不拼图，每页一张图片")
    scan_parser.add_argument("--cols", type=int, default=6)
    scan_parser.add_argument("--rows", type=int, default=3)
    scan_parser.add_argument("--scale", type=float)
    scan_parser.add_argument("--format", choices=("png", "jpg", "jpeg"), default="png")
    scan_parser.add_argument("--quality", type=int, default=82)

    render_parser = subparsers.add_parser("render", help="把 PDF 页面渲染成 PNG/JPG")
    _add_common(render_parser)
    render_parser.add_argument("pdf")
    render_parser.add_argument("--pages", default="1-30")
    render_parser.add_argument("--out-dir", required=True)
    render_parser.add_argument("--no-sheet", action="store_true")
    render_parser.add_argument("--cols", type=int, default=6)
    render_parser.add_argument("--rows", type=int, default=3)
    render_parser.add_argument("--scale", type=float)
    render_parser.add_argument("--format", choices=("png", "jpg", "jpeg"), default="png")
    render_parser.add_argument("--quality", type=int, default=82)

    check_parser = subparsers.add_parser("check-pages", help="渲染需要核对的唯一物理页，避免同页重复读取")
    _add_common(check_parser)
    check_parser.add_argument("pdf")
    check_parser.add_argument("--toc", required=True)
    check_parser.add_argument("--mode", choices=("last", "all"), default="last")
    check_parser.add_argument("--offset", type=int)
    check_parser.add_argument("--page-map")
    check_parser.add_argument("--out-dir")
    check_parser.add_argument("--no-sheet", action="store_true")
    check_parser.add_argument("--cols", type=int, default=6)
    check_parser.add_argument("--rows", type=int, default=3)
    check_parser.add_argument("--scale", type=float)
    check_parser.add_argument("--format", choices=("png", "jpg", "jpeg"), default="png")
    check_parser.add_argument("--quality", type=int, default=82)

    add_parser = subparsers.add_parser("add", help="给一个 PDF 添加/替换目录")
    _add_common(add_parser)
    add_parser.add_argument("pdf")
    add_parser.add_argument("--toc", required=True, help="目录文件；- 表示从标准输入读取")
    add_parser.add_argument("--out")
    add_parser.add_argument("--out-dir")
    add_parser.add_argument("--suffix", default=DEFAULT_SUFFIX)
    add_parser.add_argument("--mode", choices=MODE_VALUES, default="replace")
    add_parser.add_argument("--page-mode", choices=PAGE_MODE_VALUES)
    add_parser.add_argument("--offset", type=int)
    add_parser.add_argument("--anchor", action="append", default=[], help="印刷页码=物理页码，可重复")
    add_parser.add_argument("--page-map", help="自定义页码映射 JSON：{\"6\":27,\"95\":116}")
    add_parser.add_argument("--on-exists", choices=ON_EXISTS_VALUES, default="rename")
    add_parser.add_argument("--dry-run", action="store_true")
    add_parser.add_argument("--no-verify", action="store_true")
    add_parser.add_argument("--no-open-panel", action="store_true")

    batch_parser = subparsers.add_parser("batch", help="批量处理目录或 manifest.json")
    _add_common(batch_parser)
    batch_parser.add_argument("target", nargs="?")
    batch_parser.add_argument("--manifest")
    batch_parser.add_argument("--toc-dir")
    batch_parser.add_argument("--toc")
    batch_parser.add_argument("--recursive", action="store_true", help="兼容旧参数；文件夹默认递归")
    batch_parser.add_argument("--no-recursive", action="store_true", help="只处理文件夹第一层")
    batch_parser.add_argument("--allow-skipped", action="store_true")
    batch_parser.add_argument("--stop-on-error", action="store_true")
    batch_parser.add_argument("--out")
    batch_parser.add_argument("--out-dir")
    batch_parser.add_argument("--suffix", default=DEFAULT_SUFFIX)
    batch_parser.add_argument("--mode", choices=MODE_VALUES, default="replace")
    batch_parser.add_argument("--page-mode", choices=PAGE_MODE_VALUES)
    batch_parser.add_argument("--offset", type=int)
    batch_parser.add_argument("--anchor", action="append", default=[])
    batch_parser.add_argument("--page-map")
    batch_parser.add_argument("--layout", choices=("mirror", "alongside"))
    batch_parser.add_argument("--on-exists", choices=ON_EXISTS_VALUES, default="rename")
    batch_parser.add_argument("--dry-run", action="store_true")
    batch_parser.add_argument("--no-verify", action="store_true")
    batch_parser.add_argument("--no-open-panel", action="store_true")
    batch_parser.add_argument("--exclude", action="append", default=[])

    verify_parser = subparsers.add_parser("verify", help="校验 PDF 书签")
    _add_common(verify_parser)
    verify_parser.add_argument("pdf")
    verify_parser.add_argument("--expect-count", type=int)

    cleanup_parser = subparsers.add_parser("cleanup", help="清理 scan/check-pages 产生的临时目录")
    _add_common(cleanup_parser)
    cleanup_parser.add_argument("dir")
    cleanup_parser.add_argument("--force", action="store_true", help="允许删除系统临时目录以外的目录")

    return parser


def _print_add(result: dict[str, Any]) -> None:
    prefix = "预览（未写入）" if result.get("dryRun") else "✔ 已生成"
    human(f"{prefix}：{result['output']}")
    human(
        f"书签：{result['entries']} 个 / {result['levels']} 级  |  PDF：{result['pages']} 页  |  模式：{result['mode']}"
    )
    if result.get("pageMapEntries"):
        human(f"页码：自定义映射（{result['pageMapEntries']} 条）")
    elif result["pageMode"] == "printed":
        sign = "+" if result["offset"] >= 0 else ""
        human(f"页码：印刷页码 {sign}{result['offset']} → 物理页")
    else:
        human("页码：PDF 物理页")
    if result.get("existingOutline"):
        human(f"原有书签：{result['existingOutline']} 个")
    if result.get("preview"):
        human("")
        human("预览：")
        for item in result["preview"]:
            human(f"  {'  ' * (int(item['level']) - 1)}- {item['title']}  → 第 {item['physicalPage']} 页")
        if result["entries"] > len(result["preview"]):
            human(f"  ... 其余 {result['entries'] - len(result['preview'])} 项")
    warnings = result.get("warnings") or []
    if warnings:
        human("")
        human(f"警告（{len(warnings)}）：")
        for warning in warnings[:10]:
            line = f"（第 {warning['line']} 行）" if warning.get("line") else ""
            human(f"  ! {warning['message']}{line}")
        if len(warnings) > 10:
            human(f"  ... 其余 {len(warnings) - 10} 条见 --json 输出")
    if result.get("dryRun"):
        human("")
        human("这是预览。确认无误后去掉 --dry-run 即可写入新文件。")
    elif result.get("verification"):
        verification = result["verification"]
        human("")
        human(
            f"验证：{'通过' if verification['ok'] else '未通过'}"
            f"（输出 {verification['count']} 个书签，期望 {verification['expectedCount']}）"
        )


def _cmd_add(args) -> int:
    result = add_pdf(
        pdf_path=args.pdf,
        toc_path=args.toc,
        out=args.out,
        out_dir=args.out_dir,
        suffix=args.suffix,
        mode=args.mode,
        page_mode=args.page_mode,
        offset=args.offset,
        anchors=args.anchor,
        page_map=args.page_map,
        on_exists=args.on_exists,
        dry_run=args.dry_run,
        verify=not args.no_verify,
        open_panel=not args.no_open_panel,
    )
    if args.json:
        emit_json({"ok": True, "command": "add", **result})
    else:
        _print_add(result)
    return 0


def _cmd_inspect(args) -> int:
    result = inspect_pdf(args.pdf, args.text_samples, args.offset_samples)
    if args.json:
        emit_json({"ok": True, "command": "inspect", **result})
        return 0
    human(f"PDF：{result['file']}")
    human(f"页数：{result['pages']}  |  大小：{format_bytes(result['sizeBytes'])}  |  {result['pdfVersion'] or 'PDF'}")
    text = result["text"]
    if text["scanned"]:
        human(f"文字层：未检测到（抽样 {text['sampled']} 页，有文字 {text['pagesWithText']} 页）→ 扫描件")
    else:
        human(
            f"文字层：有（抽样 {text['sampled']} 页，有文字 {text['pagesWithText']} 页，"
            f"比例 {round(text['ratio'] * 100)}%）"
        )
    human(f"页码标签：{'有' if result['pageLabels']['present'] else '无'}")
    human(f"现有书签：{result['outline']['count']} 个（顶层 {result['outline']['topLevelCount']} 个）")
    offset = result.get("offset")
    if offset and offset.get("detected") is not None:
        sign = "+" if offset["detected"] >= 0 else ""
        human(f"印刷页码偏移：{sign}{offset['detected']}（{offset['confidence']}，{offset['source']}）")
    else:
        human("印刷页码偏移：未检测到")
    if result["recommendations"]:
        human("")
        human("建议：")
        for recommendation in result["recommendations"]:
            human(f"  - {recommendation['message']}")
    return 0


def _cmd_extract(args) -> int:
    output_format = args.format or ("markdown" if args.out and args.out.lower().endswith(".md") else "json")
    if output_format == "md":
        output_format = "markdown"
    result = extract_toc(args.pdf, output_format)
    if args.out:
        target = Path(args.out).resolve()
        if target.exists() and not args.force:
            raise CliError("FILE_EXISTS", f"文件已存在：{target}", hint="使用 --force 覆盖。")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result["content"], encoding="utf-8")
        if args.json:
            emit_json(
                {
                    "ok": True,
                    "command": "extract",
                    "input": str(Path(args.pdf).resolve()),
                    "output": str(target),
                    "format": result["format"],
                    "count": result["count"],
                    "skipped": result["skipped"],
                    "warnings": result["warnings"],
                }
            )
        else:
            human(f"✔ 已导出 {result['count']} 个书签：{target}")
            for warning in result["warnings"]:
                human(f"  ! {warning['message']}")
        return 0
    if args.json:
        emit_json({"ok": True, "command": "extract", **result})
    else:
        sys.stdout.write(result["content"])
        for warning in result["warnings"]:
            sys.stderr.write(f"! {warning['message']}\n")
    return 0


def _cmd_text(args) -> int:
    result = extract_text_pages(args.pdf, args.pages)
    if args.json:
        emit_json({"ok": True, "command": "text", **result})
        return 0
    human(f"PDF：{result['file']}  |  页数：{result['pages']}  |  范围：{result['range']}")
    for item in result["items"]:
        human("")
        human(f"===== 第 {item['page']} 页（{item['chars']} 字符，目录评分 {item['score']}）=====")
        human(item["text"].rstrip())
    if result["tocCandidates"]:
        human("")
        human(f"可能的目录页：{', '.join(str(page) for page in result['tocCandidates'])}")
    return 0


def _cmd_scan(args) -> int:
    result = scan_pdf(
        args.pdf,
        pages_spec=args.pages,
        out_dir=args.out_dir,
        force_render=args.render,
        sheet=not args.no_sheet,
        cols=args.cols,
        rows=args.rows,
        scale=args.scale,
        image_format=args.format,
        quality=args.quality,
    )
    if args.json:
        emit_json({"ok": True, "command": "scan", **result})
        return 0
    human(f"PDF：{result['file']}  |  页数：{result['pages']}  |  {'扫描件' if result['text']['scanned'] else '文字版'}")
    offset = result.get("offset")
    if offset and offset.get("detected") is not None:
        human(f"页码偏移：{offset['detected']:+d}（{offset['confidence']}，{offset['source']}）")
    if result["tocCandidates"]:
        human(f"可能的目录页：{', '.join(str(page) for page in result['tocCandidates'])}")
    if result.get("rendered"):
        human(f"已渲染：{len(result['rendered']['files'])} 个文件 → {result['rendered']['outDir']}")
        for item in result["rendered"]["files"]:
            human(f"  {item['path']}")
    return 0


def _cmd_render(args) -> int:
    result = render_pdf_pages(
        args.pdf,
        args.pages,
        args.out_dir,
        scale=args.scale,
        image_format=args.format,
        sheet=not args.no_sheet,
        cols=args.cols,
        rows=args.rows,
        quality=args.quality,
    )
    if args.json:
        emit_json({"ok": True, "command": "render", **result})
        return 0
    human(f"已渲染 {len(result['files'])} 个文件到：{result['outDir']}（{result['mode']}）")
    for item in result["files"]:
        human(f"  {item['path']}")
    return 0


def _cmd_check_pages(args) -> int:
    result = check_pages(
        args.pdf,
        args.toc,
        mode=args.mode,
        offset=args.offset,
        page_map=args.page_map,
        out_dir=args.out_dir,
        sheet=not args.no_sheet,
        cols=args.cols,
        rows=args.rows,
        scale=args.scale,
        image_format=args.format,
        quality=args.quality,
    )
    if args.json:
        emit_json({"ok": True, "command": "check-pages", **result})
        return 0
    human(f"核对模式：{result['mode']}  |  唯一页数：{result['uniquePages']}  |  偏移：{result['offset']}")
    for item in result["items"]:
        human(f"  物理页 {item['physicalPage']} ← 印刷页 {item['printedPages']}：{' / '.join(item['titles'])}")
    if result.get("rendered"):
        human(f"图片：{result['rendered']['outDir']}")
        for item in result["rendered"]["files"]:
            human(f"  {item['path']}")
    return 0


def _cmd_cleanup(args) -> int:
    result = cleanup_workdir(args.dir, force=args.force)
    if args.json:
        emit_json({"ok": True, "command": "cleanup", **result})
    else:
        human(("已删除：" if result["removed"] else "无需删除：") + result["path"])
        if result.get("reason"):
            human(result["reason"])
    return 0


def _cmd_verify(args) -> int:
    result = verify_pdf(args.pdf, args.expect_count)
    if args.json:
        emit_json({"ok": True, "command": "verify", **result})
        return 0
    human(f"PDF：{result['file']}")
    human(f"页数：{result['pages']}  |  书签：{result['count']} 个（顶层 {result['topLevel']} 个）")
    human("✔ 校验通过：所有书签都有有效的页面目标。" if result["ok"] else "✖ 校验未通过：部分书签缺少有效的页面目标。")
    if result["firstItems"]:
        human("")
        human("前几项：")
        for item in result["firstItems"]:
            human(f"  - {item['title']}  → 第 {item['page']} 页（{item['level']} 级）")
    return 0 if result["ok"] else 1


def _cmd_doctor(args) -> int:
    result = doctor()
    if args.json:
        emit_json(result)
    else:
        human(f"pdf-toc {VERSION} 环境检查")
        human("")
        for check in result["checks"]:
            symbol = "✔" if check["ok"] else ("·" if check.get("optional") else "✖")
            human(f"{symbol} {check['name']:<16} {check['detail']}")
        human("")
        human("环境就绪，可以处理 PDF。" if result["ready"] else f"环境未就绪，缺少：{', '.join(result['missing'])}")
        if not result["ready"]:
            human(result["install"])
    if not result["ready"] and args.require_ready:
        return 3
    return 0


def _find_toc_for_pdf(pdf: Path, toc_dir: Path | None) -> Path | None:
    base = pdf.stem
    candidates = [pdf.parent / f"{base}.toc.json", pdf.parent / f"{base}.toc.md", pdf.parent / f"{base}.toc.txt"]
    if toc_dir:
        directory = toc_dir.resolve()
        candidates.extend(
            [
                directory / f"{base}.toc.json",
                directory / f"{base}.toc.md",
                directory / f"{base}.toc.txt",
                directory / f"{base}.json",
                directory / f"{base}.md",
                directory / f"{base}.txt",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _cmd_batch(args) -> int:
    target = args.manifest or args.target
    if not target:
        raise CliError("USAGE", "请提供要批量处理的目录或 manifest.json。", exit_code=2)
    target_path = Path(target).resolve()
    if not target_path.exists():
        raise CliError("FILE_NOT_FOUND", f"路径不存在：{target_path}")
    started = time.time()
    items: list[dict[str, Any]] = []
    layout = args.layout
    if layout is None:
        layout = "mirror" if target_path.is_dir() else "alongside"
    mirror_root = None
    if layout == "mirror":
        if args.out_dir:
            mirror_root = Path(args.out_dir).resolve()
        elif target_path.is_dir():
            mirror_root = target_path.with_name(target_path.name + args.suffix)
        else:
            mirror_root = target_path.parent / f"{target_path.parent.name}{args.suffix}"
    common = {
        "out": args.out,
        "out_dir": args.out_dir,
        "suffix": args.suffix,
        "mode": args.mode,
        "page_mode": args.page_mode,
        "offset": args.offset,
        "anchors": args.anchor,
        "page_map": args.page_map,
        "on_exists": args.on_exists,
        "dry_run": args.dry_run,
        "verify": not args.no_verify,
        "open_panel": not args.no_open_panel,
    }

    if target_path.is_file():
        try:
            manifest = json.loads(target_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise CliError(
                "MANIFEST_INVALID",
                "manifest.json 解析失败。",
                details=str(exc),
                hint='manifest 示例：{"items":[{"pdf":"a.pdf","toc":"a.toc.md","offset":21}]}',
            ) from exc
        raw_items = manifest if isinstance(manifest, list) else manifest.get("items")
        defaults = {} if isinstance(manifest, list) else (manifest.get("defaults") or {})
        if not isinstance(raw_items, list) or not raw_items:
            raise CliError("MANIFEST_EMPTY", "manifest 中没有 items。")
        base_dir = target_path.parent
        source_root_setting = manifest.get("sourceRoot") if isinstance(manifest, dict) else None
        source_root = (
            (base_dir / source_root_setting).resolve() if source_root_setting else base_dir
        )
        manifest_mirror_root = source_root.with_name(source_root.name + args.suffix)
        defaults_layout = defaults.get("layout", layout)
        if defaults_layout == "mirror" and mirror_root is None:
            mirror_root = manifest_mirror_root
        for raw in raw_items:
            item = {**defaults, **raw}
            if not item.get("pdf"):
                items.append({"pdf": None, "toc": None, "config": common, "error": "manifest 项缺少 pdf 字段。"})
                continue
            item_layout = item.get("layout", defaults_layout)
            page_map_value = item.get("pageMap", common["page_map"])
            if isinstance(page_map_value, str):
                page_map_value = str((base_dir / page_map_value).resolve())
            pdf_path = (base_dir / item["pdf"]).resolve()
            config = {
                **common,
                "out": str((base_dir / item["out"]).resolve()) if item.get("out") else common["out"],
                "out_dir": str((base_dir / item["outDir"]).resolve()) if item.get("outDir") else common["out_dir"],
                "suffix": item.get("suffix", common["suffix"]),
                "mode": item.get("mode", common["mode"]),
                "page_mode": item.get("pageMode", common["page_mode"]),
                "offset": item.get("offset", common["offset"]),
                "anchors": item.get("anchors", item.get("anchor", common["anchors"])),
                "page_map": page_map_value,
                "on_exists": item.get("onExists", common["on_exists"]),
                "dry_run": item.get("dryRun", common["dry_run"]),
                "verify": item.get("verify", common["verify"]),
                "open_panel": item.get("openPanel", common["open_panel"]),
            }
            if item_layout == "mirror" and not item.get("out"):
                try:
                    relative = pdf_path.relative_to(source_root)
                except ValueError:
                    relative = Path(pdf_path.name)
                root_setting = item.get("outputRoot")
                if root_setting:
                    root = Path(root_setting)
                    if not root.is_absolute():
                        root = base_dir / root
                    root = root.resolve()
                else:
                    root = manifest_mirror_root
                config["out"] = str(root / relative)
                config["out_dir"] = None
            toc = item.get("toc")
            items.append(
                {
                    "pdf": str(pdf_path),
                    "toc": str((base_dir / toc).resolve()) if toc else args.toc,
                    "config": config,
                    "layout": item_layout,
                }
            )
    elif target_path.is_dir():
        if args.out:
            raise CliError("USAGE", "文件夹批量不支持 --out；请使用 --out-dir 或 --layout。", exit_code=2)
        toc_dir = Path(args.toc_dir) if args.toc_dir else None
        exclude = [re.compile(pattern) for pattern in args.exclude]
        files = []
        iterator = target_path.glob("*.pdf") if args.no_recursive else target_path.rglob("*.pdf")
        mirror_resolved = mirror_root.resolve() if mirror_root else None
        for file in sorted(iterator):
            if mirror_resolved and mirror_resolved.is_relative_to(target_path):
                if file.resolve().is_relative_to(mirror_resolved):
                    continue
            stem = file.stem
            if stem.endswith(args.suffix) or re.search(r"\.toc(?:-\d+)?$", stem):
                continue
            if any(pattern.search(str(file)) for pattern in exclude):
                continue
            files.append(file)
        if not files:
            raise CliError("BATCH_NO_PDF", f"目录中没有找到 PDF：{target_path}")
        for file in files:
            toc = Path(args.toc).resolve() if args.toc else _find_toc_for_pdf(file, toc_dir)
            config = dict(common)
            if layout == "mirror" and mirror_root is not None:
                relative = file.relative_to(target_path)
                config["out"] = str((mirror_root / relative).resolve())
                config["out_dir"] = None
            items.append({"pdf": str(file.resolve()), "toc": str(toc) if toc else None, "config": config, "layout": layout})
    else:
        raise CliError("USAGE", f"不支持的路径类型：{target_path}", exit_code=2)

    results = []
    succeeded = failed = skipped = 0
    for index, item in enumerate(items, start=1):
        if not item.get("pdf") or not item.get("toc"):
            skipped += 1
            results.append(
                {
                    "ok": False,
                    "skipped": True,
                    "input": item.get("pdf"),
                    "error": item.get("error") or "找不到对应的目录文件。",
                }
            )
            if not args.json:
                human(f"[{index}/{len(items)}] 跳过：{item.get('pdf') or '(无文件)'}（没有目录文件）")
            continue
        try:
            if not args.json:
                human(f"[{index}/{len(items)}] 处理：{item['pdf']}")
            result = add_pdf(pdf_path=item["pdf"], toc_path=item["toc"], **item["config"])
            succeeded += 1
            results.append(
                {
                    "ok": True,
                    "input": result["input"],
                    "output": result["output"],
                    "entries": result["entries"],
                    "pages": result["pages"],
                    "pageMode": result["pageMode"],
                    "offset": result["offset"],
                    "warnings": result["warnings"],
                    "dryRun": result["dryRun"],
                }
            )
            if not args.json:
                human(f"   → {'预览' if result['dryRun'] else '✔'} {result['output']}（{result['entries']} 个书签）")
        except Exception as exc:
            failed += 1
            payload = error_payload(exc)
            results.append({"ok": False, "input": item["pdf"], **payload})
            if not args.json:
                human(f"   ✖ {exc}")
            if args.stop_on_error:
                break

    ok = failed == 0 and (skipped == 0 or args.allow_skipped)
    item_layouts = {item.get("layout") for item in items if item.get("layout")}
    summary_layout = item_layouts.pop() if len(item_layouts) == 1 else ("mixed" if item_layouts else layout)
    summary = {
        "ok": ok,
        "command": "batch",
        "total": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "dryRun": bool(args.dry_run),
        "layout": summary_layout,
        "outputRoot": str(mirror_root) if mirror_root else None,
        "items": results,
        "durationMs": int((time.time() - started) * 1000),
    }
    if args.json:
        emit_json(summary)
    else:
        human("")
        human(f"完成：成功 {succeeded}，失败 {failed}，跳过 {skipped}")
        if mirror_root:
            human(f"输出目录：{mirror_root}")
        if not ok:
            human("有项目未成功处理，请查看上方错误或使用 --json 获取详情。")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    if json_mode and not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not args.command:
            parser.print_help()
            return 0
        if args.command == "add":
            return _cmd_add(args)
        if args.command == "inspect":
            return _cmd_inspect(args)
        if args.command == "extract":
            return _cmd_extract(args)
        if args.command == "text":
            return _cmd_text(args)
        if args.command == "scan":
            return _cmd_scan(args)
        if args.command == "render":
            return _cmd_render(args)
        if args.command == "check-pages":
            return _cmd_check_pages(args)
        if args.command == "batch":
            return _cmd_batch(args)
        if args.command == "verify":
            return _cmd_verify(args)
        if args.command == "cleanup":
            return _cmd_cleanup(args)
        if args.command == "doctor":
            return _cmd_doctor(args)
        raise CliError("USAGE", f"未知命令：{args.command}", exit_code=2)
    except CliError as error:
        if json_mode:
            emit_json(error_payload(error))
        else:
            sys.stderr.write(f"\n✖ {error.message}\n")
            if error.hint:
                sys.stderr.write(f"提示：{error.hint}\n")
            if error.details is not None and "--verbose" in argv:
                sys.stderr.write(json.dumps(error.details, ensure_ascii=False, indent=2) + "\n")
        return error.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # pragma: no cover - last resort
        if json_mode:
            emit_json(error_payload(error))
        else:
            sys.stderr.write(f"\n✖ {error}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
