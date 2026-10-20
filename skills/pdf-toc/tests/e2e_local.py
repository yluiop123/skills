"""Local end-to-end test with the two real PDFs.

Usage:
    set PDF_TOC_TEST_DIR=D:\\my-project\\pdf-menu
    python tests/e2e_local.py

The real PDFs are not committed. The script exits 0 with a SKIP message when
they are not present.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "pdf_toc.py"
EXAMPLES = SKILL_DIR / "examples"


def run(*args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        sys.stderr.write(completed.stdout)
        raise SystemExit(completed.returncode)
    return json.loads(completed.stdout)


def main() -> int:
    test_dir = Path(os.environ.get("PDF_TOC_TEST_DIR", "")).expanduser()
    java_pdf = test_dir / "Java 8实战.pdf"
    scan_pdf = test_dir / "中国战争史 1 .pdf"
    if not java_pdf.is_file() or not scan_pdf.is_file():
        print(f"SKIP: 找不到测试 PDF（PDF_TOC_TEST_DIR={test_dir}）")
        return 0

    with tempfile.TemporaryDirectory(prefix="pdf-toc-e2e-") as directory:
        temp = Path(directory)

        print("[1/4] Java 8实战：导出原有 313 个书签")
        extracted = temp / "java.toc.json"
        run("extract", str(java_pdf), "--out", str(extracted), "--force", "--json")
        data = json.loads(extracted.read_text(encoding="utf-8"))
        assert data["pageMode"] == "physical"
        assert len(data["entries"]) == 14

        print("[2/4] Java 8实战：以 physical 模式重写并校验")
        java_out = temp / "java.toc.pdf"
        added = run(
            "add",
            str(java_pdf),
            "--toc",
            str(extracted),
            "--out",
            str(java_out),
            "--mode",
            "replace",
            "--page-mode",
            "physical",
            "--json",
        )
        assert added["verification"]["ok"], added["verification"]
        assert added["verification"]["count"] == 313
        java_added = added
        verified = run("verify", str(java_out), "--expect-count", "313", "--json")
        assert verified["ok"]

        print("[3/4] 中国战争史 1：扫描件 + 印刷页偏移 21")
        scan_out = temp / "scan.toc.pdf"
        added = run(
            "add",
            str(scan_pdf),
            "--toc",
            str(EXAMPLES / "chinese-history.toc.md"),
            "--out",
            str(scan_out),
            "--mode",
            "replace",
            "--offset",
            "21",
            "--json",
        )
        assert added["pageMode"] == "printed"
        assert added["offset"] == 21
        assert added["verification"]["ok"], added["verification"]
        assert added["verification"]["count"] == 82
        verified = run("verify", str(scan_out), "--expect-count", "82", "--json")
        assert verified["ok"]

        print("[4/4] 校验关键落点：印刷页 6/95/131 → 物理页 27/116/152")
        from pypdf import PdfReader

        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        from pdf_toc import read_outline

        reader = PdfReader(str(scan_out))
        outline = read_outline(reader)
        pages = {item["title"]: item["page"] for item in outline["items"]}
        assert pages["第一卷 西周至春秋战国时期"] == 27
        assert pages["第一章 西周与春秋战国时期总论"] == 27
        assert pages["第二章 周朝东迁前后的战争"] == 116
        assert pages["第三章 春秋时期战争"] == 152

        print("OK")
        print(f"  Java:   {java_out}  ({java_added['pages']} 页 / 313 书签)")
        print(f"  Scanned:{scan_out}  (406 页 / 82 书签 / offset 21)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
