"""通过真实 Tk 控件验证界面到文件处理的完整流程。"""
import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_app import InvoiceApp
from tests.test_core import make_pdf


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "发票"
        self.source.mkdir()
        make_pdf(self.source / "invoice.pdf", ["发票号码 26117000000957348288", "价税合计（小写）￥119.30"])
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = InvoiceApp(self.root, self.base / "settings.json")
        self.addCleanup(self.destroy)
        self.app.source_var.set(str(self.source))
        self.app.output_var.set(str(self.base / "output"))
        self.app.name_var.set("李四")

    def destroy(self):
        # 清理 after 回调，允许同进程中创建多个 Tk 根窗口。
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def wait(self):
        deadline = time.monotonic() + 15
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.app.busy, "后台任务超时")
        self.root.update()

    def scan(self):
        self.app.start_scan()
        self.wait()
        self.assertEqual(len(self.app.rows), 1)

    def test_scan_edit_reorder_and_copy(self):
        self.scan()
        self.app.move_field(1, -1)
        self.app.sep_var.set("_")
        self.app.tree.selection_set("0")
        self.app.edit_number.set("12345678")
        self.app.edit_amount.set("20.5")
        self.app.apply_edit()
        self.assertEqual(self.app.rows[0].target.name, "李四_12345678_20.50元.pdf")
        self.app.start_run()
        self.wait()
        self.assertEqual(self.app.rows[0].status, "已完成")
        self.assertTrue(self.app.rows[0].target.exists())
        self.assertTrue((self.source / "invoice.pdf").exists())
        self.assertTrue(self.app.run_finished)
        self.assertEqual(str(self.app.run_button["state"]), "disabled")

    def test_manual_folder_input_and_in_place_rename(self):
        self.app.source_var.set('"' + str(self.source) + '"')
        self.app.mode_var.set("rename")
        self.scan()
        with patch("invoice_app.messagebox.askokcancel", return_value=True) as confirmation:
            self.app.start_run()
        self.wait()
        confirmation.assert_called_once()
        self.assertFalse((self.source / "invoice.pdf").exists())
        self.assertTrue(self.app.rows[0].target.exists())

    def test_path_change_disables_run(self):
        self.scan()
        self.app.recursive_var.set(False)
        self.app.refresh_preview()
        self.assertEqual(str(self.app.run_button["state"]), "disabled")
        self.assertIn("重新扫描", self.app.status_var.get())

    def test_folder_picker_fills_both_paths(self):
        with patch("invoice_app.filedialog.askdirectory", return_value=str(self.source)):
            self.app.choose_source()
        self.assertEqual(self.app.source_var.get(), str(self.source))
        self.assertEqual(self.app.output_var.get(), str(self.source.with_name("发票_已重命名")))

    def test_editor_is_not_clipped_in_small_window(self):
        self.root.geometry("1060x720")
        self.root.deiconify()
        self.root.update()
        button = self.app.apply_button
        self.assertGreaterEqual(button.master.winfo_height(), button.winfo_y() + button.winfo_height())
        self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())

    def test_preferences_keep_name_order_and_separator(self):
        self.app.move_field(2, -1)
        self.app.save_settings()
        data = json.loads((self.base / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "李四")
        self.assertEqual(data["order"], ["invoice", "amount", "name"])


if __name__ == "__main__":
    unittest.main()
