"""本地 PDF 识别、命名预览和文件处理，独立于界面。"""
from __future__ import annotations

import csv
import os
import re
import shutil
import threading
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

import pdfplumber

FIELDS = ("invoice", "name", "amount")
FIELD_LABELS = {"invoice": "发票号码", "name": "姓名", "amount": "金额"}
MONEY = r"([-−]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})(?!\d)"
INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAME = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def normalize_amount(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().replace(",", "").replace("−", "-")
    if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", value):
        raise ValueError("金额应为数字，最多保留两位小数，例如 119.30。")
    try:
        return format(Decimal(value).quantize(Decimal("0.01")), "f")
    except InvalidOperation as exc:
        raise ValueError("金额过大或格式不正确。") from exc


def clean_text(text: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).replace("¥", "￥")


def extract_from_text(text: str) -> tuple[str | None, str | None]:
    text = clean_text(text)
    match = re.search(r"(?:发票号码|发票号|Number|No\.?)[:：]?(\d{8,20})(?!\d)", text, re.I)
    if not match:
        match = re.search(r"(?<!\d)(\d{20})(?!\d)", text)
    number = match.group(1) if match else None
    # 总额优先于未税合计、税额及铁路手续费。
    for pattern in (
        r"小写[)）]?[:：]?[￥]?" + MONEY,
        r"价税合计.{0,60}?[￥]" + MONEY,
        r"价税合计[:：]?" + MONEY,
        r"(?:票价|退票费|实收金额|实付金额)[:：]?[￥]?" + MONEY,
    ):
        match = re.search(pattern, text)
        if match:
            return number, normalize_amount(match.group(1))
    values = re.findall(r"￥" + MONEY, text)
    return number, normalize_amount(values[0]) if len(values) == 1 else None


def read_invoice(path: Path) -> tuple[str | None, str | None, str]:
    try:
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                return None, None, "PDF 没有页面"
            text = pdf.pages[0].extract_text() or ""
        if not text.strip():
            return None, None, "未找到文字，可能是扫描件；可手动补填（暂不支持 OCR）"
        number, amount = extract_from_text(text)
        missing = [label for label, value in (("发票号码", number), ("金额", amount)) if not value]
        return number, amount, "未识别到" + "、".join(missing) + "，请手动补填" if missing else ""
    except Exception as exc:
        return None, None, f"PDF 读取失败：{exc}"


def extract_invoice_info(pdf_path):
    number, amount, _ = read_invoice(Path(pdf_path))
    return number, amount


def validate_component(value: str, label: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label}不能为空。")
    if INVALID_CHARS.search(value) or value.endswith(".") or RESERVED_NAME.match(value):
        raise ValueError(f'{label}含文件名不能使用的字符或保留名称，请去掉 <>:"/\\|?* 等字符。')
    if len(value) > 80:
        raise ValueError(f"{label}过长，请限制在 80 个字符以内。")
    return value


@dataclass(frozen=True)
class NamingOptions:
    name: str = "刘永军"
    order: tuple[str, ...] = FIELDS
    separator: str = "+"

    def validate(self):
        validate_component(self.name, "姓名")
        if len(self.order) != 3 or set(self.order) != set(FIELDS):
            raise ValueError("命名顺序必须恰好包含发票号码、姓名、金额。")
        if self.separator not in ("+", "_", "-", " "):
            raise ValueError("请选择支持的分隔符。")

    def filename(self, number: str, amount: str) -> str:
        self.validate()
        number = number.strip()
        if not re.fullmatch(r"\d{8,20}", number):
            raise ValueError("发票号码应为 8 至 20 位数字。")
        values = {"invoice": number, "name": self.name.strip(), "amount": normalize_amount(amount) + "元"}
        return self.separator.join(values[key] for key in self.order) + ".pdf"


@dataclass
class Invoice:
    source: Path
    number: str = ""
    amount: str = ""
    message: str = ""
    selected: bool = True
    status: str = "待处理"
    target: Path | None = None
    signature: tuple[int, int] | None = None

    @property
    def ready(self) -> bool:
        try:
            NamingOptions().filename(self.number, self.amount)
            return self.status in ("待处理", "待补填")
        except ValueError:
            return False


def file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def parse_folder(value: str) -> Path:
    value = value.strip().strip('"').strip("'")
    if not value:
        raise ValueError("请先选择或输入发票文件夹。")
    return Path(os.path.expandvars(value)).expanduser().resolve()


def validate_folders(source: Path, output: Path | None, mode: str):
    if not source.is_dir():
        raise ValueError("发票文件夹不存在，或不是文件夹。")
    if mode not in ("copy", "rename"):
        raise ValueError("保存方式不正确。")
    if mode == "copy":
        if output is None:
            raise ValueError("请选择或输入结果保存文件夹。")
        if output == source or output in source.parents:
            raise ValueError("结果文件夹不能是原文件夹或它的上级目录，请选择新文件夹。")
        if output.exists() and not output.is_dir():
            raise ValueError("结果路径已存在，但不是文件夹。")


def scan_invoices(source: Path, recursive: bool = True, output: Path | None = None,
                  cancel: threading.Event | None = None,
                  progress: Callable[[Invoice, int], None] | None = None) -> list[Invoice]:
    rows = []
    def walk_error(exc):
        raise exc
    for current, dirs, files in os.walk(source, onerror=walk_error):
        dirs[:] = sorted(d for d in dirs if recursive and not (Path(current) / d).is_symlink()
                         and (output is None or (Path(current) / d).resolve() != output))
        for filename in sorted(files):
            if cancel and cancel.is_set():
                return rows
            path = Path(current) / filename
            if path.suffix.lower() != ".pdf" or path.is_symlink():
                continue
            row = Invoice(path)
            try:
                before = file_signature(path)
                number, amount, message = read_invoice(path)
                after = file_signature(path)
                if before != after:
                    raise ValueError("识别过程中源文件发生变化，请重新扫描")
                row.number, row.amount, row.message = number or "", amount or "", message
                row.signature = after
            except (OSError, ValueError) as exc:
                row.message = str(exc)
            if not row.ready:
                row.status = "待补填"
            rows.append(row)
            if progress:
                progress(row, len(rows))
    return rows


def path_key(path: Path) -> str:
    return str(path.absolute()).casefold()


def unique_target_path(path, reserved: set[str] | None = None, source: Path | None = None):
    original = Path(path)
    candidate = original
    index = 2
    reserved = reserved if reserved is not None else set()
    while (candidate.exists() and not (source and path_key(candidate) == path_key(source))) or path_key(candidate) in reserved:
        candidate = original.with_name(f"{original.stem}_{index}{original.suffix}")
        index += 1
    return str(candidate)


def plan_targets(rows: list[Invoice], source: Path, output: Path | None,
                 mode: str, options: NamingOptions):
    options.validate()
    validate_folders(source, output, mode)
    reserved: set[str] = set()
    for row in rows:
        if row.status in ("已完成", "未更改", "失败"):
            continue
        row.target = None
        if not row.selected or not row.ready:
            continue
        parent = row.source.parent if mode == "rename" else output / row.source.parent.relative_to(source)
        desired = parent / options.filename(row.number, row.amount)
        row.target = Path(unique_target_path(desired, reserved, row.source if mode == "rename" else None))
        reserved.add(path_key(row.target))


def copy_exclusive(source: Path, target: Path):
    """独占创建，防止预览后出现的同名文件被覆盖。"""
    created = False
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            created = True
            shutil.copyfileobj(reader, writer)
        shutil.copystat(source, target)
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise


def rename_exclusive(source: Path, target: Path):
    if os.name == "nt":
        os.rename(source, target)  # Windows 目标存在时失败，不覆盖。
    else:
        os.link(source, target)  # POSIX rename 会覆盖，使用独占硬链接保护目标。
        try:
            source.unlink()
        except Exception:
            target.unlink()
            raise


@dataclass
class RunResult:
    completed: int = 0
    unchanged: int = 0
    failed: int = 0
    cancelled: bool = False


def execute_plan(rows: list[Invoice], mode: str, cancel: threading.Event | None = None,
                 progress: Callable[[Invoice, int, int], None] | None = None) -> RunResult:
    if mode not in ("copy", "rename"):
        raise ValueError("保存方式不正确。")
    pending = [row for row in rows if row.selected and row.ready and row.target]
    result = RunResult()
    for index, row in enumerate(pending, 1):
        if cancel and cancel.is_set():
            result.cancelled = True
            break
        try:
            if row.signature is None or file_signature(row.source) != row.signature:
                raise ValueError("源文件在预览后发生变化，请重新扫描")
            if mode == "rename" and path_key(row.target) == path_key(row.source):
                row.status = "未更改"
                row.message = "文件名已经符合当前规则"
                result.unchanged += 1
            else:
                row.target.parent.mkdir(parents=True, exist_ok=True)
                if mode == "copy":
                    copy_exclusive(row.source, row.target)
                else:
                    rename_exclusive(row.source, row.target)
                row.status = "已完成"
                row.message = "已复制并重命名" if mode == "copy" else "已在原位置重命名"
                result.completed += 1
        except Exception as exc:
            row.status = "失败"
            row.message = f"处理失败：{exc}"
            result.failed += 1
        if progress:
            progress(row, index, len(pending))
    return result


def export_report(path: Path, rows: list[Invoice]):
    if path.suffix.lower() != ".csv" or path.is_symlink():
        raise ValueError("清单必须保存为普通的 .csv 文件，不能覆盖发票或符号链接。")
    protected = {row.source.resolve() for row in rows}
    protected.update(row.target.resolve() for row in rows if row.target)
    if path.resolve() in protected:
        raise ValueError("清单保存位置不能与发票文件相同。")
    def cell(value):
        text = str(value)
        return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(("原始路径", "发票号码（文本）", "金额（元）", "目标路径", "状态", "说明"))
        for row in rows:
            writer.writerow((cell(row.source), "'" + row.number if row.number else "", row.amount,
                             cell(row.target or ""), row.status if row.selected else "未勾选", cell(row.message)))
