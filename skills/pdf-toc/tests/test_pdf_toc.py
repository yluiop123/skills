import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pdf_toc  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402


def make_pdf(path: Path, pages: int = 10, outline: bool = False) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=400)
    if outline:
        writer.add_outline_item("已有书签", 0)
    with path.open("wb") as handle:
        writer.write(handle)


class TocParserTests(unittest.TestCase):
    def test_markdown_indentation(self):
        parsed = pdf_toc.parse_toc_content(
            """# 目录

第一章 概述 ................ 1
  第一节 背景 .............. 2
    第一 细节 .............. 3
第二章 实现 ................ 10
""",
            "book.toc.md",
        )
        self.assertEqual(
            [(entry["title"], entry["page"], entry["level"]) for entry in parsed["entries"]],
            [("第一章 概述", 1, 1), ("第一节 背景", 2, 2), ("第一 细节", 3, 3), ("第二章 实现", 10, 1)],
        )

    def test_volume_heading_inherits_next_page(self):
        parsed = pdf_toc.parse_toc_content(
            """第一卷 起源
第一章 开始 6
  第一节 细节 8
附图 1-1 示意图
第二章 继续 20
""",
            "book.toc.txt",
        )
        self.assertEqual(parsed["entries"][0]["title"], "第一卷 起源")
        self.assertEqual(parsed["entries"][0]["page"], 6)
        self.assertTrue(parsed["entries"][0]["inferred_page"])
        self.assertTrue(any(w["code"] == "PAGE_INFERRED" for w in parsed["warnings"]))
        self.assertTrue(any(w["code"] == "SKIPPED_NO_PAGE" for w in parsed["warnings"]))

    def test_json_nested(self):
        parsed = pdf_toc.parse_toc_content(
            json.dumps(
                {
                    "pageMode": "printed",
                    "entries": [
                        {"title": "Chapter 1", "page": 12, "children": [{"title": "Section 1.1", "page": 12}]},
                        {"title": "Chapter 2", "page": "35"},
                    ],
                }
            ),
            "book.toc.json",
        )
        self.assertEqual(parsed["pageMode"], "printed")
        self.assertEqual(
            [(entry["title"], entry["page"], entry["level"]) for entry in parsed["entries"]],
            [("Chapter 1", 12, 1), ("Section 1.1", 12, 2), ("Chapter 2", 35, 1)],
        )

    def test_missing_page_is_rejected(self):
        with self.assertRaises(pdf_toc.CliError) as context:
            pdf_toc.parse_toc_content(json.dumps([{"title": "No page"}]), "x.json")
        self.assertEqual(context.exception.code, "TOC_PAGE_MISSING")

    def test_normalize_levels(self):
        entries, warnings = pdf_toc.normalize_levels(
            [
                {"title": "A", "level": 3},
                {"title": "B", "level": 6},
                {"title": "C", "level": 1},
            ]
        )
        self.assertEqual([entry["level"] for entry in entries], [1, 2, 1])
        self.assertTrue(warnings)

    def test_resolve_pages(self):
        entries = pdf_toc.resolve_entry_pages(
            [{"title": "A", "page": 6, "level": 1}], 100, "printed", 21
        )
        self.assertEqual(entries[0]["page_index"], 26)
        with self.assertRaises(pdf_toc.CliError):
            pdf_toc.resolve_entry_pages([{"title": "A", "page": 101, "level": 1}], 100, "physical", 0)

    def test_page_map_overrides_offset(self):
        entries = pdf_toc.resolve_entry_pages(
            [{"title": "A", "page": 6, "level": 1}, {"title": "B", "page": 7, "level": 1}],
            100,
            "printed",
            0,
            {6: 27},
        )
        self.assertEqual(entries[0]["page_index"], 26)
        self.assertEqual(entries[1]["page_index"], 6)

    def test_parse_page_spec(self):
        self.assertEqual(pdf_toc.parse_page_spec("1-3,5", 10), [0, 1, 2, 4])
        self.assertEqual(pdf_toc.parse_page_spec("3-1", 10), [0, 1, 2])
        with self.assertRaises(pdf_toc.CliError):
            pdf_toc.parse_page_spec("99", 10)


class PdfRoundTripTests(unittest.TestCase):
    def test_add_creates_new_verified_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            output = root / "book.toc.pdf"
            toc = root / "book.toc.json"
            make_pdf(source)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            toc.write_text(
                json.dumps(
                    {
                        "pageMode": "physical",
                        "entries": [
                            {"title": "第一章", "page": 1, "children": [{"title": "1.1", "page": 2}]},
                            {"title": "第二章", "page": 10},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = pdf_toc.add_pdf(
                pdf_path=source,
                toc_path=toc,
                out=output,
                mode="replace",
                page_mode="physical",
                verify=True,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["verification"]["ok"])
            self.assertEqual(result["verification"]["count"], 3)
            self.assertEqual(output.exists(), True)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
            outline = pdf_toc.read_outline(PdfReader(str(output)))
            self.assertEqual(
                [(item["title"], item["page"], item["level"]) for item in outline["items"]],
                [("第一章", 1, 1), ("1.1", 2, 2), ("第二章", 10, 1)],
            )

    def test_keep_refuses_existing_outline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            toc = root / "book.toc.json"
            make_pdf(source, outline=True)
            toc.write_text(json.dumps({"entries": [{"title": "新", "page": 1}]}), encoding="utf-8")
            with self.assertRaises(pdf_toc.CliError) as context:
                pdf_toc.add_pdf(pdf_path=source, toc_path=toc, mode="keep", page_mode="physical")
            self.assertEqual(context.exception.code, "OUTLINE_EXISTS")

    def test_output_cannot_be_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            toc = root / "book.toc.json"
            make_pdf(source)
            toc.write_text(json.dumps({"entries": [{"title": "A", "page": 1}]}), encoding="utf-8")
            with self.assertRaises(pdf_toc.CliError) as context:
                pdf_toc.add_pdf(pdf_path=source, toc_path=toc, out=source, page_mode="physical")
            self.assertEqual(context.exception.code, "OUTPUT_IS_INPUT")

    def test_extract_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            make_pdf(source, outline=True)
            result = pdf_toc.extract_toc(source)
            self.assertEqual(result["count"], 1)
            data = json.loads(result["content"])
            self.assertEqual(data["pageMode"], "physical")
            self.assertEqual(data["entries"][0]["title"], "已有书签")

    def test_batch_mirror_layout_preserves_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "books"
            (root / "sub").mkdir(parents=True)
            make_pdf(root / "a.pdf")
            make_pdf(root / "sub" / "b.pdf")
            (root / "a.toc.md").write_text("第一章 1\n", encoding="utf-8")
            (root / "sub" / "b.toc.md").write_text("第一章 1\n", encoding="utf-8")
            script = Path(__file__).resolve().parents[1] / "scripts" / "pdf_toc.py"
            completed = subprocess.run(
                [sys.executable, str(script), "batch", str(root), "--layout", "mirror", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            data = json.loads(completed.stdout)
            self.assertTrue(data["ok"], data)
            output_root = Path(data["outputRoot"])
            self.assertTrue((output_root / "a.pdf").is_file())
            self.assertTrue((output_root / "sub" / "b.pdf").is_file())
            self.assertFalse((root / "a.toc.pdf").exists())


if __name__ == "__main__":
    unittest.main()
