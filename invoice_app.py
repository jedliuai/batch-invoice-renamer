"""发票批量重命名的本地桌面界面，只使用 Python 自带的 Tkinter。"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from invoice_core import (
    FIELDS, FIELD_LABELS, Invoice, NamingOptions, execute_plan, export_report,
    normalize_amount, parse_folder, plan_targets, scan_invoices, validate_folders,
)

BG = "#F4F6FA"
INK = "#1D293D"
MUTED = "#66758D"
BLUE = "#345BE8"
CONFIG_PATH = Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "BatchInvoiceRenamer" / "settings.json"


class InvoiceApp:
    def __init__(self, root: tk.Tk, config_path: Path = CONFIG_PATH):
        self.root = root
        self.config_path = config_path
        self.rows: list[Invoice] = []
        self.source_root: Path | None = None
        self.scan_context = None
        self.order = list(FIELDS)
        self.busy = False
        self.run_finished = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.controls = []
        self.refresh_id = None
        self.loading = True
        self.last_output: Path | None = None
        self.root.title("发票整理 · 批量重命名")
        width = min(1320, root.winfo_screenwidth() - 70)
        height = min(880, root.winfo_screenheight() - 90)
        root.geometry(f"{width}x{height}")
        root.minsize(1060, 720)
        root.configure(bg=BG)
        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.name_var = tk.StringVar(value="刘永军")
        self.mode_var = tk.StringVar(value="copy")
        self.sep_var = tk.StringVar(value="+")
        self.recursive_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="从左侧选择发票文件夹，然后点击「扫描并预览」。")
        self.summary_var = tk.StringVar(value="尚未扫描")
        self.detail_var = tk.StringVar(value="选择表格中的一张发票，可查看完整路径和识别说明。")
        self.edit_number = tk.StringVar()
        self.edit_amount = tk.StringVar()
        self.load_settings()
        self.configure_styles()
        self.build_ui()
        self.loading = False
        for variable in (self.source_var, self.output_var, self.name_var, self.mode_var, self.sep_var, self.recursive_var):
            variable.trace_add("write", self.on_settings_change)
        self.update_order()
        self.refresh_preview()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll_events)

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        font = ("Microsoft YaHei UI", 10)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=font[0], size=font[1])
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background=BG, foreground=INK, font=font)
        style.configure("Card.TLabel", background="white")
        style.configure("Muted.TLabel", background="white", foreground=MUTED, font=(font[0], 9))
        style.configure("Title.TLabel", font=(font[0], 22, "bold"))
        style.configure("Section.TLabel", background="white", font=(font[0], 11, "bold"))
        style.configure("TButton", padding=(10, 7), font=font, background="#EDF1F8", foreground=INK, borderwidth=0)
        style.configure("Compact.TButton", padding=(5, 1))
        style.map("TButton", background=[("active", "#E1E8F4")], foreground=[("disabled", "#A0ABBD")])
        style.configure("Primary.TButton", background=BLUE, foreground="white", padding=(16, 9))
        style.map("Primary.TButton", background=[("disabled", "#D5DDEC"), ("active", "#284AC8")], foreground=[("disabled", "#7F8FA8")])
        style.configure("TEntry", padding=6, font=font, fieldbackground="white", bordercolor="#DCE3EE")
        style.configure("TCombobox", padding=5, fieldbackground="white")
        style.configure("TRadiobutton", background="white", padding=(0, 3))
        style.configure("TCheckbutton", background="white", padding=(0, 4))
        style.configure("Treeview", font=(font[0], 9), rowheight=38, background="white", fieldbackground="white", foreground=INK, borderwidth=0)
        style.configure("Treeview.Heading", font=(font[0], 9, "bold"), padding=(7, 10), background="#F0F3F8", foreground=MUTED, relief="flat")
        style.map("Treeview", background=[("selected", "#E7EDFF")], foreground=[("selected", "#203D95")])
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#E5EAF3", borderwidth=0)

    def track(self, widget, normal="normal"):
        self.controls.append((widget, normal))
        return widget

    def label(self, parent, text, style="Card.TLabel", **kwargs):
        widget = ttk.Label(parent, text=text, style=style, **kwargs)
        widget.pack(anchor="w")
        return widget

    def card(self, parent, title):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=12)
        frame.pack(fill="x", pady=(0, 10))
        self.label(frame, title, "Section.TLabel").pack_configure(pady=(0, 8))
        return frame

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=(24, 18, 24, 16))
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 18))
        ttk.Label(header, text="发票整理", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="把报销前的重复工作，交给工具。", foreground=MUTED).pack(side="left", padx=20, pady=(9, 0))
        ttk.Label(header, text="本地处理 · 无需上传", foreground=BLUE).pack(side="right", pady=(9, 0))

        bottom = ttk.Frame(outer)
        bottom.pack(side="bottom", fill="x", pady=(14, 0))
        self.progress = ttk.Progressbar(bottom, maximum=100)
        self.progress.pack(fill="x", pady=(0, 9))
        buttons = ttk.Frame(bottom)
        buttons.pack(fill="x")
        self.status_label = ttk.Label(buttons, textvariable=self.status_var, foreground=MUTED, wraplength=650)
        self.status_label.pack(side="left", fill="x", expand=True)
        self.cancel_button = ttk.Button(buttons, text="停止", command=self.stop, state="disabled")
        self.cancel_button.pack(side="left", padx=(12, 8))
        self.run_button = ttk.Button(buttons, text="开始处理", style="Primary.TButton", command=self.start_run, state="disabled")
        self.run_button.pack(side="right")

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, width=340)
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)
        # 小屏幕上的设置可以滚动，不会遮住保存方式。
        canvas = tk.Canvas(left, bg=BG, highlightthickness=0, width=322)
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        settings = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=settings, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        settings.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        def wheel(event):
            if str(event.widget).startswith(str(settings)) or event.widget == canvas:
                canvas.yview_scroll(int(-event.delta / 120), "units")
        self.root.bind_all("<MouseWheel>", wheel, add="+")

        folder = self.card(settings, "01  选择发票")
        self.label(folder, "发票文件夹", "Muted.TLabel")
        source_row = ttk.Frame(folder, style="Card.TFrame")
        source_row.pack(fill="x", pady=(5, 3))
        self.source_entry = self.track(ttk.Entry(source_row, textvariable=self.source_var, width=18))
        self.source_entry.pack(side="left", fill="x", expand=True)
        self.track(ttk.Button(source_row, text="选择…", command=self.choose_source)).pack(side="left", padx=(5, 0))
        self.track(ttk.Checkbutton(folder, text="包含子文件夹", variable=self.recursive_var)).pack(anchor="w", pady=(6, 0))
        self.label(folder, "也可直接粘贴文件夹地址。", "Muted.TLabel")

        naming = self.card(settings, "02  设置文件名")
        self.label(naming, "姓名 / 自定义文字", "Muted.TLabel")
        self.track(ttk.Entry(naming, textvariable=self.name_var)).pack(fill="x", pady=(5, 10))
        self.label(naming, "排列顺序（↑↓ 调整）", "Muted.TLabel")
        self.order_labels = []
        for i in range(3):
            row = ttk.Frame(naming, style="Card.TFrame")
            row.pack(fill="x", pady=(5, 0))
            label = ttk.Label(row, style="Card.TLabel")
            label.pack(side="left", fill="x", expand=True)
            self.order_labels.append(label)
            self.track(ttk.Button(row, text="↑", width=3, style="Compact.TButton", command=lambda idx=i: self.move_field(idx, -1))).pack(side="left", padx=(0, 4))
            self.track(ttk.Button(row, text="↓", width=3, style="Compact.TButton", command=lambda idx=i: self.move_field(idx, 1))).pack(side="left")
        sep_row = ttk.Frame(naming, style="Card.TFrame")
        sep_row.pack(fill="x", pady=(12, 8))
        ttk.Label(sep_row, text="分隔符", style="Muted.TLabel").pack(side="left")
        self.track(ttk.Combobox(sep_row, textvariable=self.sep_var, values=("+", "_", "-", "空格"), width=7, state="readonly"), "readonly").pack(side="right")
        self.example_label = ttk.Label(naming, foreground=BLUE, background="#EFF3FF", padding=10, wraplength=265, font=("Microsoft YaHei UI", 9))
        self.example_label.pack(fill="x")

        save = self.card(settings, "03  保存方式")
        self.track(ttk.Radiobutton(save, text="复制到新文件夹（推荐）", variable=self.mode_var, value="copy")).pack(anchor="w")
        self.track(ttk.Radiobutton(save, text="在原位置重命名", variable=self.mode_var, value="rename")).pack(anchor="w")
        output_row = ttk.Frame(save, style="Card.TFrame")
        output_row.pack(fill="x", pady=(6, 0))
        self.output_entry = self.track(ttk.Entry(output_row, textvariable=self.output_var, width=18))
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.output_button = self.track(ttk.Button(output_row, text="选择…", command=self.choose_output))
        self.output_button.pack(side="left", padx=(5, 0))
        self.mode_hint = ttk.Label(save, style="Muted.TLabel", wraplength=268)
        self.mode_hint.pack(fill="x", pady=(8, 0))

        right = ttk.Frame(body, style="Card.TFrame", padding=18)
        right.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(right, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="重命名预览", style="Section.TLabel").pack(side="left")
        self.scan_button = self.track(ttk.Button(top, text="扫描并预览", style="Primary.TButton", command=self.start_scan))
        self.scan_button.pack(side="right")
        ttk.Label(right, textvariable=self.summary_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 12))
        actions = ttk.Frame(right, style="Card.TFrame")
        actions.pack(fill="x", pady=(0, 10))
        self.track(ttk.Button(actions, text="全选", command=lambda: self.select_all(True))).pack(side="left", padx=(0, 5))
        self.track(ttk.Button(actions, text="全不选", command=lambda: self.select_all(False))).pack(side="left")
        self.report_button = ttk.Button(actions, text="导出清单", command=self.save_report, state="disabled")
        self.report_button.pack(side="right")
        self.open_button = ttk.Button(actions, text="打开结果文件夹", command=self.open_output, state="disabled")
        self.open_button.pack(side="right", padx=(0, 6))

        table = ttk.Frame(right, style="Card.TFrame")
        table.pack(fill="both", expand=True)
        columns = ("selected", "source", "number", "amount", "target", "status")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (("selected", "处理", 45), ("source", "原文件", 150), ("number", "发票号码", 155),
                                   ("amount", "金额 / 元", 85), ("target", "新文件名", 240), ("status", "状态", 80)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=width, stretch=key in ("source", "target"))
        scroll_y = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.tag_configure("warning", foreground="#A06713")
        self.tree.tag_configure("success", foreground="#178263")
        self.tree.tag_configure("failure", foreground="#BE3D50")
        self.tree.bind("<Button-1>", self.toggle_row)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<space>", self.toggle_selected)

        self.empty_hint = ttk.Label(self.tree, text="先选一个发票文件夹\n\n扫描后，在这里核对号码、金额和新文件名。", foreground=MUTED, background="white", justify="center")
        self.empty_hint.place(relx=0.5, rely=0.46, anchor="center")
        editor = ttk.Frame(right, style="Card.TFrame")
        editor.pack(side="bottom", fill="x", pady=(14, 0), before=table)
        ttk.Label(editor, text="核对 / 手动修正", style="Section.TLabel").pack(anchor="w")
        self.detail_label = ttk.Label(editor, textvariable=self.detail_var, style="Muted.TLabel", wraplength=600)
        self.detail_label.pack(fill="x", pady=(7, 8))
        edit_row = ttk.Frame(editor, style="Card.TFrame")
        edit_row.pack(fill="x")
        ttk.Label(edit_row, text="号码", style="Muted.TLabel").pack(side="left", padx=(0, 5))
        self.track(ttk.Entry(edit_row, textvariable=self.edit_number, width=23)).pack(side="left", fill="x", expand=True)
        ttk.Label(edit_row, text="金额", style="Muted.TLabel").pack(side="left", padx=(10, 5))
        self.track(ttk.Entry(edit_row, textvariable=self.edit_amount, width=10)).pack(side="left")
        self.apply_button = self.track(ttk.Button(edit_row, text="应用修正", command=self.apply_edit))
        self.apply_button.pack(side="left", padx=(8, 0))
        right.bind("<Configure>", lambda e: self.detail_label.configure(wraplength=max(300, e.width - 40)))

    def options(self):
        return NamingOptions(self.name_var.get(), tuple(self.order), " " if self.sep_var.get() == "空格" else self.sep_var.get())

    def folders(self):
        source = parse_folder(self.source_var.get())
        output = parse_folder(self.output_var.get()) if self.mode_var.get() == "copy" else None
        validate_folders(source, output, self.mode_var.get())
        return source, output

    def context(self):
        source, output = self.folders()
        # 输出目录改进源目录内部时必须重扫，避免把上一次生成的结果再次处理。
        excluded = output if output and source in output.parents else None
        return source, self.recursive_var.get(), excluded

    def choose_source(self):
        chosen = filedialog.askdirectory(parent=self.root, title="选择包含发票 PDF 的文件夹")
        if chosen:
            self.source_var.set(chosen)
            source = Path(chosen)
            self.output_var.set(str(source.with_name(source.name + "_已重命名")))

    def choose_output(self):
        chosen = filedialog.askdirectory(parent=self.root, title="选择结果保存文件夹（也可直接输入新路径）")
        if chosen:
            self.output_var.set(chosen)

    def move_field(self, index, direction):
        other = index + direction
        if 0 <= other < 3 and not self.busy:
            self.order[index], self.order[other] = self.order[other], self.order[index]
            self.update_order()
            self.refresh_preview()

    def update_order(self):
        for i, label in enumerate(self.order_labels):
            label.configure(text=f"{i + 1:02d}   {FIELD_LABELS[self.order[i]]}")

    def on_settings_change(self, *_):
        if self.loading or self.busy:
            return
        if self.refresh_id:
            self.root.after_cancel(self.refresh_id)
        self.refresh_id = self.root.after(180, self.refresh_preview)

    def refresh_preview(self):
        self.refresh_id = None
        if self.busy:
            return
        copying = self.mode_var.get() == "copy"
        self.output_entry.configure(state="normal" if copying else "disabled")
        self.output_button.configure(state="normal" if copying else "disabled")
        self.mode_hint.configure(text="保留原文件和子文件夹结构；同名自动加序号。" if copying else "仅修改原文件名，不改 PDF 内容。处理前会再次确认。")
        error = ""
        try:
            options = self.options()
            self.example_label.configure(text="命名示例\n" + options.filename("26117000000957348288", "119.30"))
        except ValueError as exc:
            self.example_label.configure(text=str(exc))
            error = str(exc)
        if self.rows and not error:
            try:
                source, output = self.folders()
                if self.context() != self.scan_context:
                    raise ValueError("文件夹或扫描范围已改变，请重新扫描并预览。")
                plan_targets(self.rows, source, output, self.mode_var.get(), options)
            except (ValueError, OSError) as exc:
                error = str(exc)
        self.render_rows()
        pending = sum(bool(row.selected and row.ready and row.target) for row in self.rows)
        self.run_button.configure(state="normal" if pending and not error and not self.run_finished else "disabled", text=f"开始处理 {pending} 张" if pending else "开始处理")
        if error and self.rows:
            self.status_var.set(error)
        elif self.rows and not self.run_finished:
            self.status_var.set("请核对预览，点击「处理」列可勾选或排除；确认后开始处理。")
        self.report_button.configure(state="normal" if self.rows else "disabled")

    def render_row(self, index):
        row = self.rows[index]
        source = str(row.source.relative_to(self.source_root)) if self.source_root else row.source.name
        tag = "success" if row.status in ("已完成", "未更改") else "failure" if row.status == "失败" else "warning" if not row.ready else ""
        values = ("☑" if row.selected else "☐", source, row.number or "—", row.amount or "—",
                  row.target.name if row.target else "待补填" if row.selected and not row.ready else "—", row.status)
        iid = str(index)
        if self.tree.exists(iid):
            self.tree.item(iid, values=values, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=iid, values=values, tags=(tag,))

    def render_rows(self):
        if self.rows:
            self.empty_hint.place_forget()
        for index in range(len(self.rows)):
            self.render_row(index)
        ready = [row for row in self.rows if row.selected and row.ready]
        from decimal import Decimal
        total = sum((Decimal(normalize_amount(row.amount)) for row in ready), Decimal("0"))
        needs = sum(row.status == "待补填" for row in self.rows)
        self.summary_var.set(f"共 {len(self.rows)} 张 PDF  ·  已选待处理 {len(ready)} 张  ·  待补填 {needs} 张  ·  待处理合计 ¥{total:,.2f}")

    def selected_index(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def on_select(self, _event=None):
        if self.busy:
            return
        index = self.selected_index()
        if index is None:
            return
        row = self.rows[index]
        self.edit_number.set(row.number)
        self.edit_amount.set(row.amount)
        self.detail_var.set(f"{row.source}\n{row.message or '已自动识别，请核对金额是否为价税合计。'}" + (f"\n保存到：{row.target}" if row.target else ""))

    def toggle_row(self, event):
        if self.busy or self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self.toggle(int(iid))

    def toggle_selected(self, _event):
        index = self.selected_index()
        if index is not None:
            self.toggle(index)
        return "break"

    def toggle(self, index):
        if self.busy or self.run_finished:
            return
        self.rows[index].selected = not self.rows[index].selected
        self.refresh_preview()

    def select_all(self, selected):
        if self.busy or self.run_finished:
            return
        for row in self.rows:
            row.selected = selected
        self.refresh_preview()

    def apply_edit(self):
        index = self.selected_index()
        if index is None:
            messagebox.showinfo("先选择发票", "请先在表格中选择需要修改的一张发票。", parent=self.root)
            return
        if self.run_finished or self.busy:
            messagebox.showinfo("请重新扫描", "本批次已结束，请重新扫描后再修改。", parent=self.root)
            return
        try:
            amount = normalize_amount(self.edit_amount.get())
            self.options().filename(self.edit_number.get(), amount)
        except ValueError as exc:
            messagebox.showerror("请检查输入", str(exc), parent=self.root)
            return
        row = self.rows[index]
        row.number, row.amount = self.edit_number.get().strip(), amount
        row.status, row.message = "待处理", "已由用户手动核对"
        self.refresh_preview()
        self.on_select()

    def set_busy(self, busy):
        self.busy = busy
        for widget, normal in self.controls:
            widget.configure(state="disabled" if busy else normal)
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.report_button.configure(state="disabled" if busy or not self.rows else "normal")
        self.open_button.configure(state="normal" if not busy and self.last_output and self.last_output.is_dir() else "disabled")

    def start_scan(self):
        if self.busy:
            return
        try:
            source = parse_folder(self.source_var.get())
            if not self.output_var.get().strip():
                self.output_var.set(str(source.with_name(source.name + "_已重命名")))
            source, output = self.folders()
            self.options().validate()
            context = self.context()
        except (ValueError, OSError) as exc:
            messagebox.showerror("请检查设置", str(exc), parent=self.root)
            return
        self.save_settings()
        self.rows = []
        self.source_root = source
        self.scan_context = context
        self.run_finished = False
        self.last_output = None
        self.tree.delete(*self.tree.get_children())
        self.edit_number.set("")
        self.edit_amount.set("")
        self.detail_var.set("扫描中，识别完成后可选择发票核对。")
        self.cancel.clear()
        self.set_busy(True)
        self.status_var.set("正在读取 PDF，请稍候…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        recursive = self.recursive_var.get()
        def worker():
            try:
                rows = scan_invoices(source, recursive, output if self.mode_snapshot == "copy" else None, self.cancel,
                                     lambda row, count: self.events.put(("scan_progress", (row, count))))
                self.events.put(("scan_done", (rows, self.cancel.is_set())))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        self.mode_snapshot = self.mode_var.get()
        threading.Thread(target=worker, daemon=True).start()

    def start_run(self):
        if self.busy or self.run_finished:
            return
        self.refresh_preview()
        if str(self.run_button["state"]) == "disabled":
            return
        try:
            source, output = self.folders()
            if self.context() != self.scan_context:
                raise ValueError("扫描范围已改变，请重新扫描。")
        except (ValueError, OSError) as exc:
            messagebox.showerror("无法处理", str(exc), parent=self.root)
            return
        pending = sum(bool(row.selected and row.ready and row.target) for row in self.rows)
        missing = sum(row.selected and not row.ready for row in self.rows)
        mode = self.mode_var.get()
        if mode == "rename" or missing:
            explanation = f"即将处理 {pending} 张发票。\n"
            if mode == "rename":
                explanation += "将直接修改原文件名，PDF 内容保持不变。\n"
            if missing:
                explanation += f"其中 {missing} 张信息不全的发票会跳过，原文件保留。\n"
            if not messagebox.askokcancel("确认处理", explanation + "\n是否继续？", parent=self.root):
                return
        self.save_settings()
        self.last_output = output if mode == "copy" else source
        self.cancel.clear()
        self.set_busy(True)
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("正在处理，请勿移动源文件或关闭程序…")
        def worker():
            try:
                result = execute_plan(self.rows, mode, self.cancel,
                                      lambda row, index, total: self.events.put(("run_progress", (index, total))))
                self.events.put(("run_done", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll_events(self):
        # 每轮限量处理消息，大批量扫描时也保持窗口可响应。
        for _ in range(80):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "scan_progress":
                row, count = payload
                self.rows.append(row)
                self.render_row(count - 1)
                self.empty_hint.place_forget()
                self.summary_var.set(f"正在识别第 {count} 张 PDF…")
                self.status_var.set(f"正在识别：{row.source.name}")
            elif kind == "scan_done":
                self.rows, cancelled = payload
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)
                self.set_busy(False)
                # 不允许对取消后的半批次直接执行；必须完整扫描后预览。
                if cancelled:
                    self.scan_context = None
                self.refresh_preview()
                if cancelled:
                    self.status_var.set("扫描已停止，请重新扫描后再处理。")
                elif not self.rows:
                    self.empty_hint.configure(text="这个文件夹中没有 PDF\n\n检查文件夹地址，或开启「包含子文件夹」。")
                    self.empty_hint.place(relx=0.5, rely=0.46, anchor="center")
                    self.status_var.set("没有找到 PDF 文件。")
                self.detail_var.set("选择一张发票，核对识别结果；缺失的号码或金额可以手动补填。")
            elif kind == "run_progress":
                index, total = payload
                self.progress.configure(value=index / max(total, 1) * 100)
                self.status_var.set(f"正在处理 {index} / {total} 张…")
            elif kind == "run_done":
                self.run_finished = True
                self.set_busy(False)
                self.refresh_preview()
                verb = "已停止" if payload.cancelled else "处理完成"
                self.status_var.set(f"{verb}：成功 {payload.completed} 张，未更改 {payload.unchanged} 张，失败 {payload.failed} 张。可导出清单，或重新扫描。")
                self.detail_var.set("本批次已结束。选择发票可查看处理结果和目标路径；再次处理请重新扫描。")
            elif kind == "error":
                self.progress.stop()
                self.scan_context = None
                self.run_finished = True
                self.set_busy(False)
                self.refresh_preview()
                self.status_var.set("本批次中断，请检查后重新扫描。")
                messagebox.showerror("处理出错", payload, parent=self.root)
        self.root.after(100, self.poll_events)

    def stop(self):
        if self.busy:
            self.cancel.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("正在停止，将完成当前文件后停止，已完成的文件会保留。")

    def save_report(self):
        if self.busy or not self.rows:
            return
        chosen = filedialog.asksaveasfilename(parent=self.root, title="导出处理清单", defaultextension=".csv",
                                             initialfile="发票处理清单.csv", filetypes=[("CSV 清单", "*.csv")])
        if chosen:
            try:
                export_report(Path(chosen), self.rows)
                self.status_var.set(f"清单已导出：{chosen}")
            except (OSError, ValueError) as exc:
                messagebox.showerror("导出失败", str(exc), parent=self.root)

    def open_output(self):
        if self.last_output and self.last_output.is_dir():
            try:
                if os.name == "nt":
                    os.startfile(self.last_output)
                else:
                    subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.last_output)])
            except OSError as exc:
                messagebox.showerror("无法打开文件夹", str(exc), parent=self.root)

    def load_settings(self):
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            options = NamingOptions(data.get("name", "刘永军"), tuple(data.get("order", FIELDS)), data.get("separator", "+"))
            options.validate()
            self.name_var.set(options.name)
            self.order = list(options.order)
            self.sep_var.set("空格" if options.separator == " " else options.separator)
            for key, variable in (("source", self.source_var), ("output", self.output_var)):
                if isinstance(data.get(key), str):
                    variable.set(data[key])
            self.recursive_var.set(data.get("recursive", True) is not False)
            # 每次启动恢复安全默认保存方式，不自动恢复原位修改。
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_settings(self):
        try:
            options = self.options()
            options.validate()
            data = {"name": options.name, "order": options.order, "separator": options.separator,
                    "source": self.source_var.get(), "output": self.output_var.get(), "recursive": self.recursive_var.get()}
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.config_path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.config_path)
        except (OSError, ValueError):
            pass  # 偏好设置保存失败不影响发票处理。

    def on_close(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请先点击「停止」，等当前文件处理完成后再关闭窗口。", parent=self.root)
            return
        self.save_settings()
        self.root.destroy()


def main():
    root = tk.Tk()
    InvoiceApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
