import os
import re
import shutil
import sys
import tkinter as tk
from tkinter import filedialog

import pdfplumber


# --- 用户配置 ---
USER_NAME = "刘永军"  # 文件名中间的名字


def safe_print(message=""):
    """Print without crashing on older Windows console encodings."""
    try:
        print(message)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(str(message).encode(encoding, errors="replace").decode(encoding))


def select_input_folder():
    """弹出窗口让用户选择文件夹。"""
    root = tk.Tk()
    root.withdraw()
    folder_path = filedialog.askdirectory(title="请选择包含发票的文件夹（支持多级子目录）")
    root.destroy()
    return folder_path


def clean_text(text):
    """清洗文本：去除空格、换行，并统一常见金额符号。"""
    if not text:
        return ""
    text = text.replace(" ", "").replace("\n", "").replace("\r", "")
    text = text.replace("：", ":").replace("¥", "￥")
    return text


def extract_invoice_info(pdf_path):
    """提取发票号码和金额。"""
    invoice_num = None
    amount = None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return None, None

            text = clean_text(pdf.pages[0].extract_text())
            if not text:
                return None, None

            # 1. 提取发票号码
            num_match = re.search(r"(?:发票号码|No\.?|Number):?(\d{8,20})", text)
            if num_match:
                invoice_num = num_match.group(1)
            else:
                num_match_digital = re.search(r"(?<!\d)(\d{20})(?!\d)", text)
                if num_match_digital:
                    invoice_num = num_match_digital.group(1)

            # 2. 提取金额
            # 场景 A: 铁路/客运
            if "铁路" in text or "客票" in text or "退票费" in text:
                amount_match = re.search(r"(?:票价|退票费|金额).*?[￥¥]?\s*(\d+\.\d{2})", text)
                if amount_match:
                    amount = amount_match.group(1)

            # 场景 B: 标准发票
            if not amount:
                amount_match = re.search(r"(?:小写|价税合计).*?[￥¥]?\s*(\d+\.\d{2})", text)
                if amount_match:
                    amount = amount_match.group(1)

            # 场景 C: 保底匹配
            if not amount and ("￥" in text or "¥" in text):
                loose_match = re.search(r"[￥¥]\s*(\d+\.\d{2})", text)
                if loose_match:
                    amount = loose_match.group(1)

    except Exception as exc:
        safe_print(f"[错误] 读取失败: {pdf_path} ({exc})")
        return None, None

    return invoice_num, amount


def unique_target_path(path):
    """Avoid overwriting if two PDFs produce the same target file name."""
    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    index = 2
    while True:
        candidate = f"{base}_{index}{ext}"
        if not os.path.exists(candidate):
            return candidate
        index += 1


def main():
    safe_print("[开始] 等待选择文件夹...")

    source_root = select_input_folder()
    if not source_root:
        safe_print("[退出] 未选择文件夹。")
        return

    folder_name = os.path.basename(source_root)
    parent_dir = os.path.dirname(source_root)
    output_root = os.path.join(parent_dir, f"{folder_name}_已重命名")
    os.makedirs(output_root, exist_ok=True)

    safe_print(f"[来源] {source_root}")
    safe_print(f"[输出] {output_root}")
    safe_print("")

    success_count = 0
    total_files = 0

    for root_dir, _dirs, files in os.walk(source_root):
        for filename in files:
            if not filename.lower().endswith(".pdf"):
                continue

            total_files += 1
            file_path = os.path.join(root_dir, filename)

            relative_path = os.path.relpath(root_dir, source_root)
            target_dir = os.path.join(output_root, relative_path)
            os.makedirs(target_dir, exist_ok=True)

            invoice_num, amount = extract_invoice_info(file_path)

            if invoice_num and amount:
                new_filename = f"{invoice_num}+{USER_NAME}+{amount}元.pdf"
                new_full_path = unique_target_path(os.path.join(target_dir, new_filename))

                shutil.copy2(file_path, new_full_path)
                shown_target = os.path.relpath(new_full_path, output_root)
                safe_print(f"[成功] {filename} -> {shown_target}")
                success_count += 1
            else:
                shown_source = os.path.join(relative_path, filename)
                safe_print(f"[无法识别] {shown_source}")

    safe_print("")
    safe_print("[完成] 处理完毕。")
    safe_print(f"[统计] 共扫描 {total_files} 个 PDF，成功处理 {success_count} 个。")
    safe_print(f"[位置] {output_root}")

    if os.name == "nt":
        os.startfile(output_root)


if __name__ == "__main__":
    main()
