"""覆盖真实 PDF 读取与文件保护行为；全部在临时文件夹中执行。"""
import csv
import itertools
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_core import (
    FIELDS, Invoice, NamingOptions, execute_plan, export_report, extract_from_text,
    file_signature, normalize_amount, plan_targets, read_invoice, scan_invoices,
    validate_folders,
)


def make_pdf(path: Path, lines: list[str]):
    """生成含中文文本的最小 PDF，不引入额外的运行/测试依赖。"""
    text = ["BT /F1 12 Tf 40 780 Td"]
    for line in lines:
        text.append(f"<{line.encode('utf-16-be').hex()}> Tj 0 -24 Td")
    stream = "\n".join(text + ["ET"]).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [5 0 R] >>",
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> /FontDescriptor << /Type /FontDescriptor /FontName /STSong-Light /Flags 4 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    content = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(content)
    content += b"xref\n0 7\n0000000000 65535 f \n"
    content += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    content += f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class ExtractionTests(unittest.TestCase):
    def test_total_priority_and_thousands(self):
        self.assertEqual(extract_from_text("发票号码：26117000000957348288\n税额 ￥10.00\n价税合计（大写）壹仟元（小写）￥1,234.50"),
                         ("26117000000957348288", "1234.50"))

    def test_rail_and_negative_invoice(self):
        self.assertEqual(extract_from_text("No.12345678 铁路客票 票价：￥119.30"), ("12345678", "119.30"))
        self.assertEqual(extract_from_text("Number:12345678 小写：￥-100.00"), ("12345678", "-100.00"))

    def test_ambiguous_amount_is_not_guessed(self):
        self.assertEqual(extract_from_text("发票号码12345678 ￥10.00 ￥30.00"), ("12345678", None))

    def test_reject_invalid_numbers_and_normalize_amount(self):
        self.assertEqual(normalize_amount("1,234.5"), "1234.50")
        for amount in ("nan", "1.001", "abc", "=1+2", "Infinity"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                normalize_amount(amount)
        self.assertIsNone(extract_from_text("发票号码123456789012345678901 ￥10.00")[0])

    def test_all_orders_and_invalid_name(self):
        for order in itertools.permutations(FIELDS):
            name = NamingOptions("张三", order, "_").filename("12345678", "10")
            expected = {"name": "张三", "invoice": "12345678", "amount": "10.00元"}
            self.assertEqual(name, "_".join(expected[key] for key in order) + ".pdf")
        for name in ("", "../张三", "张/三", "CON", "a:b", "李四."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                NamingOptions(name).validate()


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "原始发票"
        self.source.mkdir()
        self.output = self.base / "整理结果"
        self.pdf = self.source / "测试发票.PDF"
        make_pdf(self.pdf, ["发票号码：26117000000957348288", "税额 ￥10.00", "价税合计（小写）￥119.30"])

    def scan(self, **kwargs):
        return scan_invoices(self.source, **kwargs)

    def plan(self, rows, mode="copy"):
        plan_targets(rows, self.source, self.output if mode == "copy" else None, mode, NamingOptions("张三"))

    def test_real_chinese_pdf(self):
        number, amount, message = read_invoice(self.pdf)
        self.assertEqual((number, amount, message), ("26117000000957348288", "119.30", ""))

    def test_copy_and_subfolder_structure_preserves_original(self):
        child = self.source / "火车票" / "ticket.pdf"
        make_pdf(child, ["No.12345678", "铁路客票 票价 ￥12.50"])
        originals = {path: path.read_bytes() for path in (self.pdf, child)}
        rows = self.scan()
        self.plan(rows)
        result = execute_plan(rows, "copy")
        self.assertEqual(result.completed, 2)
        for row in rows:
            self.assertEqual(row.source.read_bytes(), originals[row.source])
            self.assertEqual(row.target.read_bytes(), originals[row.source])
        self.assertTrue((self.output / "火车票" / "12345678+张三+12.50元.pdf").is_file())

    def test_duplicate_and_existing_target_are_preserved(self):
        other = self.source / "duplicate.pdf"
        other.write_bytes(self.pdf.read_bytes())
        self.output.mkdir()
        existing = self.output / "26117000000957348288+张三+119.30元.pdf"
        existing.write_bytes(b"existing invoice")
        rows = self.scan()
        self.plan(rows)
        self.assertEqual(len({row.target for row in rows}), 2)
        self.assertTrue(all(row.target != existing for row in rows))
        self.assertEqual(execute_plan(rows, "copy").completed, 2)
        self.assertEqual(existing.read_bytes(), b"existing invoice")

    def test_collision_after_preview_never_overwrites(self):
        for mode in ("copy", "rename"):
            with self.subTest(mode=mode):
                rows = self.scan()
                self.plan(rows, mode)
                target = rows[0].target
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(b"new file after preview")
                result = execute_plan(rows, mode)
                self.assertEqual(result.failed, 1)
                self.assertTrue(self.pdf.exists())
                self.assertEqual(target.read_bytes(), b"new file after preview")
                target.unlink()

    def test_in_place_rename_and_idempotent_second_run(self):
        original = self.pdf.read_bytes()
        rows = self.scan()
        self.plan(rows, "rename")
        self.assertEqual(execute_plan(rows, "rename").completed, 1)
        self.assertFalse(self.pdf.exists())
        self.assertEqual(rows[0].target.read_bytes(), original)
        rows = self.scan()
        self.plan(rows, "rename")
        self.assertEqual(execute_plan(rows, "rename").unchanged, 1)

    def test_changed_source_is_skipped(self):
        rows = self.scan()
        self.plan(rows)
        self.pdf.write_bytes(b"changed after preview")
        self.assertEqual(execute_plan(rows, "copy").failed, 1)
        self.assertFalse(rows[0].target.exists())

    def test_cancel_skips_remaining_files(self):
        rows = self.scan()
        self.plan(rows)
        cancel = threading.Event()
        cancel.set()
        result = execute_plan(rows, "copy", cancel)
        self.assertTrue(result.cancelled)
        self.assertFalse(self.output.exists())

    def test_partial_copy_is_cleaned_up(self):
        rows = self.scan()
        self.plan(rows)
        def fail_copy(reader, writer):
            writer.write(b"partial")
            raise OSError("disk full")
        with patch("invoice_core.shutil.copyfileobj", side_effect=fail_copy):
            result = execute_plan(rows, "copy")
        self.assertEqual(result.failed, 1)
        self.assertFalse(rows[0].target.exists())
        self.assertTrue(self.pdf.exists())

    def test_recursive_switch_and_output_exclusion(self):
        child = self.source / "nested" / "ticket.pdf"
        make_pdf(child, ["No.12345678", "票价 ￥12.50"])
        output = self.source / "output"
        make_pdf(output / "old.pdf", ["No.12345678", "票价 ￥12.50"])
        self.assertEqual(len(self.scan(recursive=False)), 1)
        self.assertEqual(len(self.scan(output=output)), 2)
        with self.assertRaises(ValueError):
            validate_folders(self.source, self.source, "copy")
        with self.assertRaises(ValueError):
            validate_folders(self.source, self.base, "copy")

    def test_bad_pdf_and_manual_correction(self):
        bad = self.source / "损坏.pdf"
        bad.write_bytes(b"not a PDF")
        rows = self.scan()
        row = next(row for row in rows if row.source == bad)
        self.assertEqual(row.status, "待补填")
        self.assertIn("读取失败", row.message)
        row.number, row.amount, row.status = "12345678", "10.00", "待处理"
        self.plan(rows)
        self.assertIsNotNone(row.target)

    def test_unselected_rows_not_written(self):
        rows = self.scan()
        rows[0].selected = False
        self.plan(rows)
        self.assertIsNone(rows[0].target)
        self.assertEqual(execute_plan(rows, "copy").completed, 0)

    def test_report_retains_invoice_number(self):
        rows = self.scan()
        self.plan(rows)
        path = self.base / "清单.csv"
        export_report(path, rows)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            data = list(csv.reader(handle))
        self.assertEqual(data[1][1], "'26117000000957348288")
        self.assertEqual(data[1][2], "119.30")

    def test_report_cannot_replace_invoice(self):
        original = self.pdf.read_bytes()
        with self.assertRaises(ValueError):
            export_report(self.pdf, self.scan())
        self.assertEqual(self.pdf.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
