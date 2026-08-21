import json
import os
import re
import shutil
import sys
import threading
import unicodedata
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from PIL import Image, ImageOps, ImageTk

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None


APP_NAME = "Sapo Invoice Desktop"
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(os.getenv("LOCALAPPDATA", Path.home())) / "SapoInvoiceDesktop"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIR / "sapo_database.json"
if not DATABASE_PATH.exists() and (BASE_DIR / "sapo_database.json").exists():
    shutil.copy2(BASE_DIR / "sapo_database.json", DATABASE_PATH)

os.environ["SAPO_DATABASE_PATH"] = str(DATABASE_PATH)
os.environ["SAPO_LEARNING_PATH"] = str(DATA_DIR / "learning_rules.json")
os.environ["SAPO_PRICE_HISTORY_PATH"] = str(DATA_DIR / "price_history.json")

import invoice_engine
import sapo_direct
from config_store import ConfigStore
from storage import (
    export_product_updates,
    export_bulk_skus_from_barcodes,
    export_sapo_excel,
    export_sapo_product_csv,
)


def money(value):
    try:
        return f"{float(value):,.0f} đ".replace(",", ".")
    except (TypeError, ValueError):
        return "0 đ"


def parse_user_number(value):
    text = str(value or "").strip().lower().replace("đ", "").replace(" ", "")
    if not text:
        return 0.0
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts) if len(parts[-1]) == 3 else text.replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            text = "".join(parts)
    return float(text)


def select_all_on_double_click(entry):
    def handler(_event):
        entry.focus_set()
        entry.selection_range(0, "end")
        entry.icursor("end")
        return "break"
    entry.bind("<Double-Button-1>", handler)
    return entry


class InvoiceDesktopApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1320x820")
        self.root.minsize(1080, 680)
        self.root.configure(fg_color="#F4F7FB")
        self.config_store = ConfigStore(DATA_DIR / "settings.json")
        self.config = self.config_store.load()
        self.files = []
        self.results = []
        self.busy = False
        self._style()
        self._build()
        self.set_status("Sẵn sàng. Chọn ảnh hoặc PDF hóa đơn để bắt đầu.")

    def _style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#F4F7FB")
        style.configure("TLabel", background="#F4F7FB", foreground="#25364A", font=("Segoe UI", 9))
        style.configure("TCheckbutton", background="#F4F7FB")
        style.configure("TRadiobutton", background="#F4F7FB")
        style.configure("Card.TFrame", background="#FFFFFF")
        style.configure("Card.TLabel", background="#FFFFFF", foreground="#25364A", font=("Segoe UI", 9))
        style.configure("Card.TCheckbutton", background="#FFFFFF", foreground="#34495E")
        style.configure("Card.TRadiobutton", background="#FFFFFF", foreground="#34495E")
        style.configure("TLabelframe", background="#FFFFFF", bordercolor="#DCE5EF", relief="solid")
        style.configure("TLabelframe.Label", background="#FFFFFF", foreground="#264A70", font=("Segoe UI", 10, "bold"))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#123B63")
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"), foreground="#123B63")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(15, 9), foreground="#FFFFFF", background="#1688F0", bordercolor="#1688F0")
        style.map("Primary.TButton", background=[("active", "#0875D1"), ("disabled", "#A7C8E8")])
        style.configure("Export.TButton", font=("Segoe UI", 9, "bold"), padding=(12, 7), foreground="#FFFFFF", background="#1688F0", bordercolor="#1688F0")
        style.map("Export.TButton", background=[("active", "#0875D1"), ("disabled", "#A7C8E8")])
        style.configure("Secondary.TButton", font=("Segoe UI", 9, "bold"), padding=(11, 7), foreground="#264A70", background="#E8F2FC", bordercolor="#C7DCEF")
        style.map("Secondary.TButton", background=[("active", "#D9EBFA")])
        style.configure("Preview.TButton", font=("Segoe UI", 10, "bold"), padding=(7, 5), foreground="#EAF2FA", background="#273447", bordercolor="#3D4B60")
        style.map("Preview.TButton", background=[("active", "#37465C"), ("disabled", "#1F2937")], foreground=[("disabled", "#68758A")])
        style.configure("Treeview", rowheight=37, font=("Segoe UI", 9), background="#FFFFFF", fieldbackground="#FFFFFF", bordercolor="#E2E9F1")
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), foreground="#36516D", background="#F1F5F9", padding=(7, 9), relief="flat")
        style.map("Treeview", background=[("selected", "#D9ECFF")], foreground=[("selected", "#123B63")])
        style.configure("TNotebook", background="#EEF4FA", borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=(16, 9), background="#E6EDF5", foreground="#536A80")
        style.map("TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", "#123B63")])

    def _build(self):
        shell = ctk.CTkFrame(self.root, fg_color="#F4F7FB", corner_radius=0)
        shell.pack(fill="both", expand=True)
        sidebar = ctk.CTkFrame(shell, fg_color="#0B1730", width=218, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x")
        brand.pack_configure(padx=22, pady=25)
        logo_row = ctk.CTkFrame(brand, fg_color="transparent")
        logo_row.pack(fill="x")
        ctk.CTkLabel(logo_row, text="S", width=34, height=34, corner_radius=10, fg_color="#1688F0", text_color="#FFFFFF", font=ctk.CTkFont("Segoe UI", 17, "bold")).pack(side="left")
        ctk.CTkLabel(logo_row, text="SAPO INVOICE", text_color="#FFFFFF", font=ctk.CTkFont("Segoe UI", 13, "bold")).pack(side="left", padx=(10, 0))
        ctk.CTkLabel(brand, text="Quản lý hóa đơn nhập hàng", text_color="#8EA3BE", font=ctk.CTkFont("Segoe UI", 10)).pack(anchor="w", pady=(9, 0))
        ctk.CTkLabel(sidebar, text="KHÔNG GIAN LÀM VIỆC", text_color="#617894", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(anchor="w", padx=22, pady=(18, 8))
        self.nav_buttons = {}
        for key, text in (("invoice", "▦   Hóa đơn mới"), ("settings", "⚙   Cấu hình")):
            button = ctk.CTkButton(
                sidebar, text=text, command=lambda page=key: self.show_page(page),
                fg_color="transparent", hover_color="#142746", text_color="#B9C7D8",
                anchor="w", corner_radius=10, height=42,
                font=ctk.CTkFont("Segoe UI", 12, "bold"),
            )
            button.pack(fill="x", padx=10, pady=3)
            self.nav_buttons[key] = button
        sidebar_footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        sidebar_footer.pack(side="bottom", fill="x")
        sidebar_footer.pack_configure(padx=22, pady=20)
        ctk.CTkFrame(sidebar_footer, fg_color="#203552", height=1, corner_radius=0).pack(fill="x", pady=(0, 14))
        ctk.CTkLabel(sidebar_footer, text="●  Máy chủ cục bộ", text_color="#55D7AC", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(anchor="w")
        ctk.CTkLabel(sidebar_footer, text="Sapo Invoice Desktop", text_color="#7188A4", font=ctk.CTkFont("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

        main = ctk.CTkFrame(shell, fg_color="#F4F7FB", corner_radius=0)
        main.pack(side="left", fill="both", expand=True)
        header = ctk.CTkFrame(main, fg_color="#FFFFFF", corner_radius=0)
        header.pack(fill="x", padx=20, pady=(18, 0))
        self.page_eyebrow_var = tk.StringVar()
        self.page_title_var = tk.StringVar()
        self.page_subtitle_var = tk.StringVar()
        ctk.CTkLabel(header, textvariable=self.page_eyebrow_var, text_color="#1688F0", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(anchor="w", padx=26, pady=(17, 0))
        ctk.CTkLabel(header, textvariable=self.page_title_var, text_color="#0E1B2E", font=ctk.CTkFont("Segoe UI", 25, "bold")).pack(anchor="w", padx=26, pady=(1, 0))
        ctk.CTkLabel(header, textvariable=self.page_subtitle_var, text_color="#6A7B90", font=ctk.CTkFont("Segoe UI", 11)).pack(anchor="w", padx=26, pady=(2, 17))
        self.page_host = ctk.CTkFrame(main, fg_color="#F4F7FB", corner_radius=0)
        self.page_host.pack(fill="both", expand=True, padx=20, pady=(14, 8))
        self.invoice_tab = ctk.CTkFrame(self.page_host, fg_color="#F4F7FB", corner_radius=0)
        self.settings_tab = ctk.CTkFrame(self.page_host, fg_color="#F4F7FB", corner_radius=0)
        for page in (self.invoice_tab, self.settings_tab):
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._build_invoice_tab()
        self._build_settings_tab()

        self.status_var = tk.StringVar()
        status = ctk.CTkLabel(main, textvariable=self.status_var, anchor="w", fg_color="#FFFFFF", text_color="#607086", height=34, font=ctk.CTkFont("Segoe UI", 10))
        status.pack(fill="x", padx=20, pady=(0, 16))
        self.show_page("invoice")

    def show_page(self, page):
        pages = {
            "invoice": (self.invoice_tab, "SAPO • INVOICE MANAGEMENT", "Xử lý hóa đơn nhập hàng", "Chọn hóa đơn, kiểm tra sản phẩm và xuất file Sapo trong một luồng."),
            "settings": (self.settings_tab, "HỆ THỐNG • THIẾT LẬP", "Cấu hình ứng dụng", "Quản lý kết nối AI, dữ liệu sản phẩm và công cụ bảo trì SKU."),
        }
        frame, eyebrow, title, subtitle = pages.get(page, pages["invoice"])
        frame.tkraise()
        self.active_page = page if page in pages else "invoice"
        self.page_eyebrow_var.set(eyebrow)
        self.page_title_var.set(title)
        self.page_subtitle_var.set(subtitle)
        for key, button in self.nav_buttons.items():
            selected = key == self.active_page
            button.configure(fg_color="#1688F0" if selected else "transparent", hover_color="#1688F0" if selected else "#142746", text_color="#FFFFFF" if selected else "#B9C7D8")

    def _build_invoice_tab(self):
        workspace = tk.PanedWindow(
            self.invoice_tab, orient="horizontal", sashwidth=8, sashrelief="flat",
            bg="#F4F7FB", bd=0,
        )
        workspace.pack(fill="both", expand=True)
        left_panel = ctk.CTkFrame(workspace, fg_color="#F4F7FB", corner_radius=0)
        preview_card = ctk.CTkFrame(workspace, fg_color="#FFFFFF", corner_radius=16, border_width=1, border_color="#DCE6F0")
        workspace.add(left_panel, minsize=760, stretch="always")
        workspace.add(preview_card, minsize=280, width=330, stretch="never")

        upload_card = ctk.CTkFrame(left_panel, fg_color="#FFFFFF", corner_radius=16, border_width=1, border_color="#DCE6F0")
        upload_card.pack(fill="x", pady=(0, 12))
        upload_title = ctk.CTkFrame(upload_card, fg_color="transparent")
        upload_title.pack(fill="x", padx=18, pady=(15, 10))
        ctk.CTkLabel(upload_title, text="HÓA ĐƠN ĐẦU VÀO", text_color="#1688F0", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(anchor="w")
        ctk.CTkLabel(upload_title, text="Chọn file để đọc tự động", text_color="#10213A", font=ctk.CTkFont("Segoe UI", 17, "bold")).pack(anchor="w")

        controls = ctk.CTkFrame(upload_card, fg_color="transparent")
        controls.pack(fill="x", padx=18, pady=(0, 10))
        ctk.CTkButton(controls, text="＋  Chọn ảnh/PDF", command=self.choose_files, height=36, corner_radius=9, fg_color="#1688F0", hover_color="#0875D1", font=ctk.CTkFont("Segoe UI", 11, "bold")).pack(side="left")
        ctk.CTkButton(controls, text="Xóa danh sách", command=self.clear_files, height=36, corner_radius=9, fg_color="#E8F2FC", hover_color="#D9EBFA", text_color="#264A70", font=ctk.CTkFont("Segoe UI", 11, "bold")).pack(side="left", padx=8)
        ctk.CTkLabel(controls, text="Cách xử lý:", text_color="#52667C", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(side="left", padx=(24, 6))
        self.mode_var = tk.StringVar(value="single_invoice")
        ctk.CTkRadioButton(
            controls, text="Một hóa đơn nhiều file", variable=self.mode_var,
            value="single_invoice", text_color="#34495E", font=ctk.CTkFont("Segoe UI", 10),
        ).pack(side="left")
        ctk.CTkRadioButton(
            controls, text="Mỗi file một hóa đơn", variable=self.mode_var,
            value="separate_invoices", text_color="#34495E", font=ctk.CTkFont("Segoe UI", 10),
        ).pack(side="left", padx=10)
        self.analyze_button = ctk.CTkButton(
            controls, text="Đọc lại", command=self.analyze, height=36, corner_radius=9,
            fg_color="#E8F2FC", hover_color="#D9EBFA", text_color="#264A70", font=ctk.CTkFont("Segoe UI", 11, "bold"),
        )
        self.analyze_button.pack(side="right")

        self.file_list = tk.Listbox(
            upload_card, height=3, font=("Segoe UI", 9), borderwidth=0,
            highlightthickness=1, highlightbackground="#E2EAF2", bg="#F7FAFD",
            fg="#34495E", selectbackground="#D9ECFF",
        )
        self.file_list.pack(fill="x", padx=18, pady=(0, 15))

        metrics = ctk.CTkFrame(left_panel, fg_color="transparent")
        metrics.pack(fill="x", pady=(0, 12))
        self.metric_files_var = tk.StringVar(value="0")
        self.metric_items_var = tk.StringVar(value="0")
        self.metric_qty_var = tk.StringVar(value="0")
        self.metric_total_var = tk.StringVar(value="0 đ")
        metric_specs = (
            ("Tệp đã chọn", self.metric_files_var, "#1688F0"),
            ("Mặt hàng", self.metric_items_var, "#F59E0B"),
            ("Tổng số lượng", self.metric_qty_var, "#10A37F"),
            ("Tổng tiền nhập", self.metric_total_var, "#E43D68"),
        )
        for column, (label, variable, accent) in enumerate(metric_specs):
            card = ctk.CTkFrame(metrics, fg_color="#FFFFFF", corner_radius=14, border_width=1, border_color="#DCE6F0")
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0 if column == 3 else 6))
            ctk.CTkFrame(card, fg_color=accent, height=4, corner_radius=4).pack(fill="x", padx=15, pady=(13, 10))
            ctk.CTkLabel(card, text=label, text_color="#718096", font=ctk.CTkFont("Segoe UI", 10)).pack(anchor="w", padx=15)
            ctk.CTkLabel(card, textvariable=variable, text_color="#10213A", font=ctk.CTkFont("Segoe UI", 20, "bold")).pack(anchor="w", padx=15, pady=(2, 13))
            metrics.columnconfigure(column, weight=1)

        result_card = ctk.CTkFrame(left_panel, fg_color="#FFFFFF", corner_radius=16, border_width=1, border_color="#DCE6F0")
        result_card.pack(fill="both", expand=True)
        result_heading = ctk.CTkFrame(result_card, fg_color="transparent")
        result_heading.pack(fill="x", padx=14, pady=(14, 9))
        ctk.CTkLabel(result_heading, text="KẾT QUẢ KIỂM TRA", text_color="#1688F0", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(side="left")
        self.summary_var = tk.StringVar(value="Chưa có kết quả")
        ctk.CTkLabel(result_heading, textvariable=self.summary_var, text_color="#607086", font=ctk.CTkFont("Segoe UI", 10)).pack(side="right")

        result_frame = ttk.Frame(result_card, style="Card.TFrame")
        result_frame.pack(fill="both", expand=True)
        columns = ("state", "invoice", "original", "sapo", "sku", "qty", "price", "sale", "total", "confidence", "alert")
        self.result_tree = ttk.Treeview(result_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "state": "Trạng thái", "invoice": "HĐ", "original": "Tên trên hóa đơn",
            "sapo": "Sản phẩm Sapo", "sku": "SKU", "qty": "SL", "price": "Giá nhập",
            "sale": "Giá bán", "total": "Thành tiền", "confidence": "Tin cậy", "alert": "Giá",
        }
        widths = {"state": 82, "invoice": 38, "original": 195, "sapo": 205, "sku": 90, "qty": 45, "price": 90, "sale": 95, "total": 100, "confidence": 58, "alert": 75}
        for column in columns:
            self.result_tree.heading(column, text=headings[column])
            self.result_tree.column(column, width=widths[column], minwidth=40, anchor="w" if column in ("original", "sapo") else "center")
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=scrollbar.set)
        self.result_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.result_tree.tag_configure("matched", background="#FFFFFF")
        self.result_tree.tag_configure("attention", background="#FFF4E8")
        self.result_tree.tag_configure("new", background="#EAF8EF")
        self.result_tree.bind("<Double-1>", self.open_editor_from_tree_event)
        self.result_tree.bind("<Delete>", lambda _event: self.delete_selected_row())

        footer = ctk.CTkFrame(result_card, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(10, 14))
        self.auto_sync_var = tk.BooleanVar(value=self.config.get("auto_sync_on_export", True))
        ctk.CTkCheckBox(
            footer, text="Đồng bộ sản phẩm và giá lên Sapo khi xuất",
            variable=self.auto_sync_var, text_color="#34495E", font=ctk.CTkFont("Segoe UI", 10),
        ).pack(side="left")
        self.invoice_discount_value_var = tk.StringVar(value="")
        self.invoice_discount_type_var = tk.StringVar(value="VND")
        discount_box = ctk.CTkFrame(footer, fg_color="#F6F9FD", corner_radius=9)
        discount_box.pack(side="left", padx=(14, 0))
        ctk.CTkLabel(discount_box, text="Giảm giá tổng đơn", text_color="#52667C", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(side="left", padx=(10, 6), pady=5)
        ctk.CTkEntry(discount_box, textvariable=self.invoice_discount_value_var, width=78, height=29, corner_radius=7, placeholder_text="0").pack(side="left", pady=5)
        ctk.CTkSegmentedButton(discount_box, values=["VND", "%"], variable=self.invoice_discount_type_var, width=75, height=29, corner_radius=7).pack(side="left", padx=(5, 6), pady=5)
        ctk.CTkButton(footer, text="Sửa dòng", command=self.edit_selected, height=35, corner_radius=9, fg_color="#E8F2FC", hover_color="#D9EBFA", text_color="#264A70").pack(side="right")
        ctk.CTkButton(footer, text="Xóa dòng", command=self.delete_selected_row, height=35, corner_radius=9, fg_color="#FFF1F2", hover_color="#FFE0E4", text_color="#B42340").pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            footer, text="XUẤT EXCEL SAPO", command=self.export_excel,
            height=35, width=165, corner_radius=9, fg_color="#1688F0", hover_color="#0875D1", font=ctk.CTkFont("Segoe UI", 11, "bold"),
        ).pack(side="right", padx=(0, 8))

        ctk.CTkLabel(preview_card, text="XEM HÓA ĐƠN", text_color="#1688F0", font=ctk.CTkFont("Segoe UI", 10, "bold")).pack(anchor="w", padx=14, pady=(14, 0))
        self.preview_name_var = tk.StringVar(value="Chưa chọn file")
        ctk.CTkLabel(preview_card, textvariable=self.preview_name_var, text_color="#10213A", font=ctk.CTkFont("Segoe UI", 13, "bold"), wraplength=285, justify="left").pack(anchor="w", padx=14, pady=(3, 8))
        preview_toolbar = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_toolbar.pack(fill="x", padx=14, pady=(0, 7))
        self.preview_prev_button = ctk.CTkButton(
            preview_toolbar, text="‹", width=3, command=lambda: self.change_preview_page(-1),
            height=32, corner_radius=8, fg_color="#E8F2FC", hover_color="#D9EBFA", text_color="#264A70", state="disabled",
        )
        self.preview_prev_button.pack(side="left")
        self.preview_page_var = tk.StringVar(value="Không có bản xem trước")
        ctk.CTkLabel(preview_toolbar, textvariable=self.preview_page_var, text_color="#607086", font=ctk.CTkFont("Segoe UI", 10)).pack(side="left", expand=True)
        self.preview_next_button = ctk.CTkButton(
            preview_toolbar, text="›", width=3, command=lambda: self.change_preview_page(1),
            height=32, corner_radius=8, fg_color="#E8F2FC", hover_color="#D9EBFA", text_color="#264A70", state="disabled",
        )
        self.preview_next_button.pack(side="right")

        viewer = ctk.CTkFrame(preview_card, fg_color="#182231", corner_radius=12)
        viewer.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        image_toolbar = tk.Frame(viewer, bg="#182231")
        image_toolbar.pack(fill="x", pady=(0, 7))
        ttk.Button(image_toolbar, text="↶", width=3, command=lambda: self.rotate_preview(90), style="Preview.TButton").pack(side="left")
        ttk.Button(image_toolbar, text="↷", width=3, command=lambda: self.rotate_preview(-90), style="Preview.TButton").pack(side="left", padx=(4, 10))
        ttk.Button(image_toolbar, text="−", width=3, command=lambda: self.zoom_preview(0.8), style="Preview.TButton").pack(side="left")
        self.preview_zoom_var = tk.StringVar(value="Vừa khung")
        tk.Label(
            image_toolbar, textvariable=self.preview_zoom_var, bg="#182231", fg="#D8E3F0",
            font=("Segoe UI", 9, "bold"), width=10,
        ).pack(side="left", expand=True)
        ttk.Button(image_toolbar, text="+", width=3, command=lambda: self.zoom_preview(1.25), style="Preview.TButton").pack(side="left")
        ttk.Button(image_toolbar, text="⛶", width=3, command=self.fit_preview, style="Preview.TButton").pack(side="left", padx=(4, 0))

        canvas_frame = tk.Frame(viewer, bg="#182231")
        canvas_frame.pack(fill="both", expand=True)
        self.preview_canvas = tk.Canvas(
            canvas_frame, bg="#222D3D", bd=0, highlightthickness=0,
            xscrollincrement=1, yscrollincrement=1,
        )
        preview_xscroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.preview_canvas.xview)
        preview_yscroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.preview_canvas.yview)
        self.preview_canvas.configure(xscrollcommand=preview_xscroll.set, yscrollcommand=preview_yscroll.set)
        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        preview_yscroll.grid(row=0, column=1, sticky="ns")
        preview_xscroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.preview_canvas.bind("<Configure>", self.resize_preview)
        self.preview_canvas.bind("<MouseWheel>", self.preview_mousewheel)
        self.preview_canvas.bind("<ButtonPress-1>", self.preview_pan_start)
        self.preview_canvas.bind("<B1-Motion>", self.preview_pan_move)
        self.preview_canvas.bind("<Button-1>", lambda _event: self.preview_canvas.focus_set(), add="+")
        self.preview_canvas.bind("<Control-plus>", lambda _event: self.zoom_preview(1.25))
        self.preview_canvas.bind("<Control-equal>", lambda _event: self.zoom_preview(1.25))
        self.preview_canvas.bind("<Control-minus>", lambda _event: self.zoom_preview(0.8))
        self.preview_canvas.bind("<Key-r>", lambda _event: self.rotate_preview(-90))
        self.preview_canvas.bind("<Key-0>", lambda _event: self.fit_preview())
        self.preview_document = None
        self.preview_source_image = None
        self.preview_photo = None
        self.preview_page_index = 0
        self.preview_page_count = 0
        self.preview_zoom = 1.0
        self.preview_rotation = 0
        self.preview_fit_mode = True
        self.set_preview_message("Chọn ảnh hoặc PDF để xem tại đây")
        self.file_list.bind("<<ListboxSelect>>", self.preview_selected_file)

    def _build_settings_tab(self):
        body = tk.Frame(self.settings_tab, bg="#F4F7FB")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        ai_card = tk.Frame(body, bg="#FFFFFF", padx=20, pady=18, highlightthickness=1, highlightbackground="#E2E9F1")
        ai_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7), pady=(0, 12))
        tk.Label(ai_card, text="KẾT NỐI AI", bg="#FFFFFF", fg="#1688F0", font=("Segoe UI", 8, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(ai_card, text="Đọc và phân tích hóa đơn", bg="#FFFFFF", fg="#10213A", font=("Segoe UI", 14, "bold")).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 3))
        tk.Label(ai_card, text="Thông tin này chỉ được lưu an toàn trên máy hiện tại.", bg="#FFFFFF", fg="#718096", font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 15))
        tk.Label(ai_card, text="OpenAI API key", bg="#FFFFFF", fg="#40566E", font=("Segoe UI", 9, "bold")).grid(row=3, column=0, sticky="w", pady=7)
        self.key_var = tk.StringVar(value=self.config.get("openai_key", ""))
        self.key_entry = ttk.Entry(ai_card, textvariable=self.key_var, show="●")
        self.key_entry.grid(row=3, column=1, sticky="ew", padx=(12, 8), pady=7)
        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ai_card, text="Hiện", variable=self.show_key_var, command=self.toggle_key, style="Card.TCheckbutton").grid(row=3, column=2)

        tk.Label(ai_card, text="Model AI", bg="#FFFFFF", fg="#40566E", font=("Segoe UI", 9, "bold")).grid(row=4, column=0, sticky="w", pady=7)
        self.model_var = tk.StringVar(value=self.config.get("model", "gpt-5.6-terra"))
        ttk.Entry(ai_card, textvariable=self.model_var).grid(row=4, column=1, sticky="ew", padx=(12, 8), pady=7)
        ai_card.columnconfigure(1, weight=1)

        data_card = tk.Frame(body, bg="#FFFFFF", padx=20, pady=18, highlightthickness=1, highlightbackground="#E2E9F1")
        data_card.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        tk.Label(data_card, text="DỮ LIỆU SẢN PHẨM", bg="#FFFFFF", fg="#10A37F", font=("Segoe UI", 8, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(data_card, text="Danh mục Sapo", bg="#FFFFFF", fg="#10213A", font=("Segoe UI", 14, "bold")).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 3))
        tk.Label(data_card, text="Chọn nguồn dữ liệu dùng để đối chiếu sản phẩm.", bg="#FFFFFF", fg="#718096", font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 15))
        tk.Label(data_card, text="Database", bg="#FFFFFF", fg="#40566E", font=("Segoe UI", 9, "bold")).grid(row=3, column=0, sticky="w", pady=7)
        self.db_var = tk.StringVar(value=self.config.get("database_path", str(DATABASE_PATH)))
        ttk.Entry(data_card, textvariable=self.db_var).grid(row=3, column=1, sticky="ew", padx=(12, 8), pady=7)
        ttk.Button(data_card, text="Chọn file", command=self.choose_database, style="Secondary.TButton").grid(row=3, column=2)
        data_card.columnconfigure(1, weight=1)

        tools_card = tk.Frame(body, bg="#FFFFFF", padx=20, pady=18, highlightthickness=1, highlightbackground="#E2E9F1")
        tools_card.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(7, 0))
        tk.Label(tools_card, text="CÔNG CỤ BẢO TRÌ", bg="#FFFFFF", fg="#F59E0B", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(tools_card, text="Quản lý danh mục", bg="#FFFFFF", fg="#10213A", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(2, 3))
        tk.Label(tools_card, text="Các thao tác dưới đây ảnh hưởng trực tiếp đến danh mục sản phẩm. Hãy dùng file sao lưu khi cần khôi phục.", bg="#FFFFFF", fg="#718096", font=("Segoe UI", 9), wraplength=340, justify="left").pack(anchor="w", pady=(0, 18))
        ttk.Button(
            tools_card, text="Cập nhật SKU trực tiếp lên Sapo", command=self.direct_update_skus, style="Primary.TButton",
        ).pack(fill="x", pady=(0, 9))
        ttk.Button(
            tools_card, text="Tạo CSV dự phòng", command=self.bulk_create_skus, style="Secondary.TButton",
        ).pack(fill="x", pady=(0, 9))
        ttk.Button(
            tools_card, text="Khôi phục SKU gốc từ file sao lưu", command=self.restore_skus_from_backup, style="Secondary.TButton",
        ).pack(fill="x", pady=(0, 9))
        ttk.Button(
            tools_card, text="Bật cho phép bán âm cho toàn bộ", command=self.enable_negative_inventory, style="Secondary.TButton",
        ).pack(fill="x")
        tk.Frame(tools_card, bg="#E5ECF3", height=1).pack(fill="x", pady=20)
        tk.Label(tools_card, text="VỊ TRÍ LƯU DỮ LIỆU", bg="#FFFFFF", fg="#718096", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(tools_card, text=str(DATA_DIR), bg="#FFFFFF", fg="#40566E", font=("Segoe UI", 8), wraplength=340, justify="left").pack(anchor="w", pady=(5, 0))

        actions = tk.Frame(body, bg="#F4F7FB")
        actions.grid(row=2, column=0, columnspan=2, sticky="e", pady=(14, 0))
        tk.Label(actions, text="API key được mã hóa bằng tài khoản Windows hiện tại.", bg="#F4F7FB", fg="#718096", font=("Segoe UI", 8)).pack(side="left", padx=(0, 14))
        ttk.Button(actions, text="Lưu cấu hình", command=self.save_config, style="Primary.TButton").pack(side="left")

    def set_status(self, text):
        self.status_var.set(text)
        self.root.update_idletasks()

    def close_preview_document(self):
        document = getattr(self, "preview_document", None)
        if document is not None:
            try:
                document.close()
            except Exception:
                pass
        self.preview_document = None

    def clear_preview(self):
        self.close_preview_document()
        self.preview_source_image = None
        self.preview_photo = None
        self.preview_page_index = 0
        self.preview_page_count = 0
        self.preview_zoom = 1.0
        self.preview_rotation = 0
        self.preview_fit_mode = True
        self.preview_file_index = -1
        self.preview_name_var.set("Chưa chọn file")
        self.preview_page_var.set("Không có bản xem trước")
        self.preview_zoom_var.set("Vừa khung")
        self.preview_prev_button.configure(state="disabled")
        self.preview_next_button.configure(state="disabled")
        self.set_preview_message("Chọn ảnh hoặc PDF để xem tại đây")

    def set_preview_message(self, text):
        self.preview_canvas.delete("all")
        width = max(self.preview_canvas.winfo_width(), 260)
        height = max(self.preview_canvas.winfo_height(), 300)
        self.preview_canvas.create_text(
            width / 2, height / 2, text=text, fill="#AAB8C8",
            font=("Segoe UI", 10), width=max(width - 50, 180), justify="center",
        )
        self.preview_canvas.configure(scrollregion=(0, 0, width, height))

    def preview_selected_file(self, _event=None):
        selected = self.file_list.curselection()
        if not selected:
            return
        index = int(selected[0])
        if 0 <= index < len(self.files):
            self.show_file_preview(self.files[index], file_index=index)

    def show_file_preview(self, path, file_index=None):
        self.close_preview_document()
        self.preview_source_image = None
        self.preview_photo = None
        self.preview_page_index = 0
        self.preview_page_count = 1
        self.preview_zoom = 1.0
        self.preview_rotation = 0
        self.preview_fit_mode = True
        if file_index is None:
            try:
                file_index = self.files.index(str(path))
            except ValueError:
                file_index = 0
        self.preview_file_index = file_index
        self.preview_zoom_var.set("Vừa khung")
        path = Path(path)
        self.preview_name_var.set(path.name)
        try:
            if path.suffix.casefold() == ".pdf":
                if pdfium is None:
                    raise RuntimeError("Bộ xem PDF chưa được cài trong ứng dụng")
                self.preview_document = pdfium.PdfDocument(str(path))
                self.preview_page_count = len(self.preview_document)
                if self.preview_page_count <= 0:
                    raise RuntimeError("PDF không có trang")
                self.render_pdf_preview_page()
            else:
                with Image.open(path) as source:
                    self.preview_source_image = ImageOps.exif_transpose(source).convert("RGB").copy()
                self.preview_page_var.set(f"Tệp {file_index + 1} / {len(self.files)} • Ảnh")
                self.update_preview_navigation()
                self.draw_preview_image()
        except Exception as exc:
            self.close_preview_document()
            self.preview_page_count = 0
            self.preview_source_image = None
            self.preview_page_var.set("Không xem trước được")
            self.set_preview_message(f"Không thể hiển thị file này.\n\n{exc}")
            self.update_preview_navigation()

    def render_pdf_preview_page(self):
        if self.preview_document is None or self.preview_page_count <= 0:
            return
        page = self.preview_document[self.preview_page_index]
        try:
            self.preview_source_image = page.render(scale=1.6).to_pil().convert("RGB")
        finally:
            page.close()
        self.preview_page_var.set(
            f"Tệp {self.preview_file_index + 1} / {len(self.files)} • Trang {self.preview_page_index + 1} / {self.preview_page_count}"
        )
        self.update_preview_navigation()
        self.draw_preview_image()

    def update_preview_navigation(self):
        has_previous_file = self.preview_file_index > 0
        has_next_file = self.preview_file_index < len(self.files) - 1
        has_pages = self.preview_document is not None and self.preview_page_count > 1
        self.preview_prev_button.configure(
            state="normal" if has_previous_file or (has_pages and self.preview_page_index > 0) else "disabled"
        )
        self.preview_next_button.configure(
            state="normal"
            if has_next_file or (has_pages and self.preview_page_index < self.preview_page_count - 1)
            else "disabled"
        )

    def change_preview_page(self, offset):
        if self.preview_document is not None:
            target = self.preview_page_index + offset
            if 0 <= target < self.preview_page_count:
                self.preview_page_index = target
                self.render_pdf_preview_page()
                return
        target_file = self.preview_file_index + offset
        if 0 <= target_file < len(self.files):
            self.file_list.selection_clear(0, "end")
            self.file_list.selection_set(target_file)
            self.file_list.activate(target_file)
            self.file_list.see(target_file)
            self.show_file_preview(self.files[target_file], file_index=target_file)

    def resize_preview(self, _event=None):
        if getattr(self, "preview_source_image", None) is None:
            return
        previous_job = getattr(self, "preview_resize_job", None)
        if previous_job:
            try:
                self.root.after_cancel(previous_job)
            except Exception:
                pass
        self.preview_resize_job = self.root.after(80, self.draw_preview_image)

    def draw_preview_image(self):
        self.preview_resize_job = None
        source = getattr(self, "preview_source_image", None)
        if source is None:
            return
        rotated = source.rotate(self.preview_rotation, expand=True)
        canvas_width = max(self.preview_canvas.winfo_width() - 6, 120)
        canvas_height = max(self.preview_canvas.winfo_height() - 6, 180)
        if self.preview_fit_mode:
            scale = min(canvas_width / rotated.width, canvas_height / rotated.height)
            scale = min(scale, 1.0)
            self.preview_zoom = max(scale, 0.05)
            self.preview_zoom_var.set("Vừa khung")
        else:
            scale = self.preview_zoom
            self.preview_zoom_var.set(f"{round(scale * 100):.0f}%")
        target_size = (
            max(1, round(rotated.width * scale)),
            max(1, round(rotated.height * scale)),
        )
        rendered = rotated.resize(target_size, Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(rendered)
        self.preview_canvas.delete("all")
        image_width, image_height = rendered.size
        content_width = max(image_width, canvas_width)
        content_height = max(image_height, canvas_height)
        x = content_width / 2 if image_width <= canvas_width else 0
        y = content_height / 2 if image_height <= canvas_height else 0
        anchor = "center" if image_width <= canvas_width and image_height <= canvas_height else "nw"
        if anchor == "nw":
            x = 0 if image_width > canvas_width else (canvas_width - image_width) / 2
            y = 0 if image_height > canvas_height else (canvas_height - image_height) / 2
        self.preview_canvas.create_image(x, y, image=self.preview_photo, anchor=anchor)
        self.preview_canvas.configure(scrollregion=(0, 0, content_width, content_height))
        if self.preview_fit_mode:
            self.preview_canvas.xview_moveto(0)
            self.preview_canvas.yview_moveto(0)

    def zoom_preview(self, factor):
        if self.preview_source_image is None:
            return "break"
        current = self.preview_zoom if not self.preview_fit_mode else max(self.preview_zoom, 0.05)
        self.preview_fit_mode = False
        self.preview_zoom = min(max(current * factor, 0.10), 5.0)
        self.draw_preview_image()
        return "break"

    def fit_preview(self):
        if self.preview_source_image is None:
            return "break"
        self.preview_fit_mode = True
        self.draw_preview_image()
        return "break"

    def rotate_preview(self, degrees):
        if self.preview_source_image is None:
            return "break"
        self.preview_rotation = (self.preview_rotation + degrees) % 360
        self.draw_preview_image()
        return "break"

    def preview_mousewheel(self, event):
        return self.zoom_preview(1.15 if event.delta > 0 else 1 / 1.15)

    def preview_pan_start(self, event):
        self.preview_canvas.scan_mark(event.x, event.y)

    def preview_pan_move(self, event):
        self.preview_canvas.scan_dragto(event.x, event.y, gain=1)

    def choose_files(self):
        selected = filedialog.askopenfilenames(
            title="Chọn ảnh hoặc PDF hóa đơn",
            filetypes=[("Hóa đơn", "*.jpg *.jpeg *.png *.webp *.pdf"), ("Tất cả file", "*.*")],
        )
        if not selected:
            return
        self.files = list(selected)[:10]
        self.results = []
        self.file_list.delete(0, "end")
        for index, path in enumerate(self.files, start=1):
            self.file_list.insert("end", f"{index}. {Path(path).name}")
        self.file_list.selection_set(0)
        self.file_list.activate(0)
        self.show_file_preview(self.files[0])
        self.refresh_results()
        self.set_status(f"Đã chọn {len(self.files)} file. Đang tự động đọc hóa đơn...")
        self.root.after(80, self.analyze)

    def clear_files(self):
        if self.busy:
            return
        self.files = []
        self.results = []
        self.file_list.delete(0, "end")
        self.clear_preview()
        self.refresh_results()
        self.set_status("Đã xóa hóa đơn hiện tại. Sẵn sàng chọn file mới.")

    def clear_after_export(self, status_text):
        self.files = []
        self.results = []
        self.file_list.delete(0, "end")
        self.clear_preview()
        self.refresh_results()
        self.set_status(status_text)

    def analyze(self):
        if self.busy:
            return
        if not self.files:
            return messagebox.showwarning(APP_NAME, "Hãy chọn ít nhất một ảnh hoặc PDF hóa đơn.")
        api_key = self.key_var.get().strip()
        if not api_key:
            self.show_page("settings")
            return messagebox.showwarning(APP_NAME, "Hãy nhập và lưu OpenAI API key trước.")
        database = Path(self.db_var.get().strip())
        if not database.exists():
            return messagebox.showerror(APP_NAME, "Không tìm thấy database sản phẩm đã chọn.")
        self.busy = True
        self.analyze_button.configure(state="disabled", text="Đang đọc...")
        self.set_status("GPT đang đọc hóa đơn. Bạn có thể chờ trong cửa sổ này...")

        def worker():
            try:
                invoice_engine.configure_runtime(
                    api_key, database, DATA_DIR / "learning_rules.json",
                    DATA_DIR / "price_history.json", self.model_var.get().strip(),
                )
                result = invoice_engine.analyze_paths(self.files, self.mode_var.get())
                self.root.after(0, lambda: self.analysis_done(result))
            except Exception as exc:
                self.root.after(0, lambda: self.analysis_failed(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def analysis_done(self, result):
        self.busy = False
        self.analyze_button.configure(state="normal", text="Đọc lại")
        self.results = result
        self.refresh_results()
        unmatched = sum(not item.get("matched") for item in result)
        self.set_status(f"Đã đọc xong {len(result)} mặt hàng. Còn {unmatched} dòng cần kiểm tra.")

    def analysis_failed(self, error):
        self.busy = False
        self.analyze_button.configure(state="normal", text="Đọc lại")
        self.set_status("Đọc hóa đơn không thành công.")
        messagebox.showerror(APP_NAME, error)

    def refresh_results(self):
        self.result_tree.delete(*self.result_tree.get_children())
        for index, item in enumerate(self.results):
            insight = item.get("price_insight") or {}
            alert = "⚠ Tăng" if insight.get("is_alert") and insight.get("direction") == "increase" else "⚠ Giảm" if insight.get("is_alert") else "Bình thường"
            state = (
                "Cần chọn" if not item.get("matched") else
                "Sản phẩm mới" if item.get("new_product") else
                "SKU mới" if item.get("generated_sku") else "Đã khớp"
            )
            row_tag = "attention" if not item.get("matched") else "new" if item.get("new_product") or item.get("generated_sku") else "matched"
            self.result_tree.insert("", "end", iid=str(index), tags=(row_tag,), values=(
                state,
                int(item.get("invoice_index", 0)) + 1,
                item.get("original_name", ""), item.get("sapo_name") or "—",
                item.get("sku") or "—",
                item.get("qty", 0), money(item.get("price")), money(item.get("new_sale_price")),
                money(float(item.get("qty") or 0) * float(item.get("price") or 0)),
                f"{float(item.get('confidence') or 0) * 100:.0f}%", alert,
            ))
        quantity = sum(float(item.get("qty") or 0) for item in self.results)
        total = sum(float(item.get("qty") or 0) * float(item.get("price") or 0) for item in self.results)
        self.summary_var.set(f"{len(self.results)} mặt hàng  •  Tổng SL {quantity:g}  •  {money(total)}")
        self.metric_files_var.set(str(len(self.files)))
        self.metric_items_var.set(str(len(self.results)))
        self.metric_qty_var.set(f"{quantity:g}")
        self.metric_total_var.set(money(total))

    def delete_selected_row(self):
        selected = self.result_tree.selection()
        if not selected:
            return messagebox.showinfo(APP_NAME, "Hãy chọn dòng cần xóa.")
        indices = sorted((int(row_id) for row_id in selected), reverse=True)
        deleted = 0
        for index in indices:
            if 0 <= index < len(self.results):
                del self.results[index]
                deleted += 1
        self.refresh_results()
        if self.results:
            next_index = min(indices[-1], len(self.results) - 1)
            next_row = str(next_index)
            self.result_tree.selection_set(next_row)
            self.result_tree.focus(next_row)
            self.result_tree.see(next_row)
        self.set_status(f"Đã xóa {deleted} dòng khỏi kết quả hóa đơn.")

    def open_editor_from_tree_event(self, event):
        row = self.result_tree.identify_row(event.y)
        if row:
            self.result_tree.selection_set(row)
        column = self.result_tree.identify_column(event.x)
        focus_fields = {"#5": "sku", "#6": "qty", "#7": "price", "#8": "sale"}
        self.edit_selected(focus_fields.get(column))

    def edit_selected(self, focus_field=None):
        selected = self.result_tree.selection()
        if not selected:
            return messagebox.showinfo(APP_NAME, "Hãy chọn một dòng cần sửa.")
        index = int(selected[0])
        item = self.results[index]
        dialog = tk.Toplevel(self.root)
        dialog.title("Sửa kết quả")
        dialog.geometry("780x760")
        dialog.minsize(700, 680)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=item.get("original_name", ""), style="Section.TLabel", wraplength=600).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        ttk.Label(frame, text="Sản phẩm Sapo:").grid(row=1, column=0, sticky="nw", pady=6)
        suggestions = item.get("suggestions") or []
        choices = ([item.get("sapo_name")] if item.get("sapo_name") else []) + [suggestion.get("name") for suggestion in suggestions]
        choices = list(dict.fromkeys(choice for choice in choices if choice))
        product_var = tk.StringVar(value=item.get("sapo_name") or (choices[0] if choices else ""))
        combo = ttk.Combobox(frame, textvariable=product_var, values=choices, width=62)
        combo.grid(row=1, column=1, sticky="ew", pady=6)
        select_all_on_double_click(combo)
        selected_catalog = {"product": None}
        create_new_var = tk.BooleanVar(value=bool(item.get("new_product")))
        sku_var = tk.StringVar(value=str(item.get("sku") or ""))
        barcode_var = tk.StringVar(value=str(item.get("barcode") or ""))
        unit_var = tk.StringVar(value=str(item.get("unit_name") or "Cái"))
        image_url_var = tk.StringVar(value=str(item.get("image_url") or ""))
        new_sale_var = tk.StringVar(value=str(item.get("new_sale_price", item.get("system_sale_price", 0))))

        def create_new_product():
            details = self.open_new_product_dialog(dialog, product_var.get(), new_sale_var.get())
            if not details:
                return
            product_var.set(details["name"])
            sku_var.set(details["sku"])
            barcode_var.set(details["barcode"])
            unit_var.set(details["unit_name"])
            image_url_var.set(details["image_url"])
            new_sale_var.set(str(details["sale_price"]))
            create_new_var.set(True)
            selected_catalog["product"] = None

        ttk.Button(
            frame, text="＋ TẠO SẢN PHẨM MỚI...", command=create_new_product,
            style="Primary.TButton",
        ).grid(row=2, column=1, sticky="w", pady=(0, 7))
        ttk.Button(
            frame,
            text="🔎 Tìm trong toàn bộ danh mục Sapo...",
            command=lambda: self.open_catalog_search(
                dialog, item, product_var, sku_var, barcode_var, create_new_var, selected_catalog
            ),
        ).grid(row=3, column=1, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="Mã SKU bắt buộc:").grid(row=4, column=0, sticky="w", pady=6)
        sku_entry = ttk.Entry(frame, textvariable=sku_var)
        sku_entry.grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="Barcode:").grid(row=5, column=0, sticky="w", pady=6)
        barcode_entry = ttk.Entry(frame, textvariable=barcode_var)
        barcode_entry.grid(row=5, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="Đơn vị tính:").grid(row=6, column=0, sticky="w", pady=6)
        unit_entry = ttk.Entry(frame, textvariable=unit_var, width=24)
        unit_entry.grid(row=6, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="Image URL:").grid(row=7, column=0, sticky="w", pady=6)
        image_url_entry = ttk.Entry(frame, textvariable=image_url_var)
        image_url_entry.grid(row=7, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="Số lượng:").grid(row=8, column=0, sticky="w", pady=6)
        qty_var = tk.StringVar(value=str(item.get("qty", 0)))
        qty_entry = ttk.Entry(frame, textvariable=qty_var, width=24)
        qty_entry.grid(row=8, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="Giá nhập đã thuế:").grid(row=9, column=0, sticky="w", pady=6)
        price_var = tk.StringVar(value=str(item.get("price", 0)))
        price_entry = ttk.Entry(frame, textvariable=price_var, width=24)
        price_entry.grid(row=9, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="Giá vốn hệ thống:").grid(row=10, column=0, sticky="w", pady=6)
        system_cost_var = tk.StringVar(value=money(item.get("system_cost", 0)))
        ttk.Entry(frame, textvariable=system_cost_var, state="readonly", width=24).grid(row=10, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="Giá bán hiện tại:").grid(row=11, column=0, sticky="w", pady=6)
        system_sale_var = tk.StringVar(value=money(item.get("system_sale_price", 0)))
        ttk.Entry(frame, textvariable=system_sale_var, state="readonly", width=24).grid(row=11, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="Giá bán:").grid(row=12, column=0, sticky="w", pady=6)
        sale_entry = ttk.Entry(frame, textvariable=new_sale_var, width=24)
        sale_entry.grid(row=12, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="Lý do:").grid(row=13, column=0, sticky="nw", pady=6)
        ttk.Label(frame, text=item.get("match_reason", ""), wraplength=500, foreground="#53718C").grid(row=13, column=1, sticky="w", pady=6)

        editable_entries = {"sku": sku_entry, "barcode": barcode_entry, "unit": unit_entry, "image": image_url_entry, "qty": qty_entry, "price": price_entry, "sale": sale_entry}
        for editable in editable_entries.values():
            select_all_on_double_click(editable)

        def save():
            previous_sku = str(item.get("sku") or "").strip()
            previous_barcode = str(item.get("barcode") or "").strip()
            previous_product_name = str(item.get("sapo_name") or "").strip()
            previous_unit_name = str(item.get("unit_name") or "").strip()
            previous_image_url = str(item.get("image_url") or "").strip()
            try:
                item["qty"] = parse_user_number(qty_var.get())
                item["price"] = parse_user_number(price_var.get())
                item["new_sale_price"] = parse_user_number(new_sale_var.get())
            except ValueError:
                return messagebox.showerror(APP_NAME, "Số lượng hoặc giá nhập không hợp lệ.", parent=dialog)
            selected_name = product_var.get().strip()
            if not selected_name:
                return messagebox.showerror(APP_NAME, "Tên sản phẩm không được để trống.", parent=dialog)
            chosen = selected_catalog.get("product") or next(
                (suggestion for suggestion in suggestions if suggestion.get("name") == selected_name), None
            )
            if create_new_var.get():
                if not selected_name:
                    return messagebox.showerror(APP_NAME, "Hãy nhập tên sản phẩm mới.", parent=dialog)
                sku = sku_var.get().strip() or self.suggest_new_sku(selected_name, barcode_var.get())
                item.update({
                    "matched": True, "new_product": True, "variant_id": None,
                    "sapo_name": selected_name, "sku": sku,
                    "barcode": barcode_var.get().strip(), "confidence": 1.0,
                    "unit_name": unit_var.get().strip() or "Cái", "image_url": image_url_var.get().strip(),
                    "system_cost": 0, "system_sale_price": 0,
                    "match_reason": "Sản phẩm mới sẽ được tạo trực tiếp trên Sapo khi xuất.",
                    "generated_sku": False,
                })
            elif chosen:
                item.update({
                    "matched": True, "variant_id": chosen.get("variant_id"),
                    "new_product": False,
                    "sapo_name": chosen.get("name"), "search_query": chosen.get("search_query", ""),
                    "sku": chosen.get("sku", ""), "barcode": chosen.get("barcode", ""), "confidence": 1.0,
                    "system_cost": chosen.get("cost", 0), "system_sale_price": chosen.get("price", 0),
                    "match_reason": "Người dùng xác nhận trong ứng dụng desktop.",
                })
                if not new_sale_var.get().strip():
                    item["new_sale_price"] = chosen.get("price", 0)
                try:
                    invoice_engine.learning_store.learn(
                        item.get("original_name", ""), item.get("price", 0), chosen.get("variant_id")
                    )
                except Exception:
                    pass
            elif selected_name:
                item["sapo_name"] = selected_name
                item["matched"] = bool(item.get("variant_id"))
            if not create_new_var.get():
                item["sapo_name"] = selected_name
                item["unit_name"] = unit_var.get().strip()
                item["image_url"] = image_url_var.get().strip()
                item["product_name_changed"] = selected_name != previous_product_name
                item["unit_changed"] = item["unit_name"] != previous_unit_name
                item["image_url_changed"] = item["image_url"] != previous_image_url
            if not create_new_var.get() and sku_var.get().strip():
                item["sku"] = sku_var.get().strip()
                item["generated_sku"] = not bool(chosen and chosen.get("sku"))
                item["sku_changed"] = item["sku"] != previous_sku
            if not create_new_var.get():
                item["barcode"] = barcode_var.get().strip()
                baseline_barcode = str(chosen.get("barcode") or "").strip() if chosen else previous_barcode
                item["barcode_changed"] = item["barcode"] != baseline_barcode
            generated = list(invoice_engine.generate_missing_skus([item]))
            if generated:
                self.results[index] = generated[0]
            dialog.destroy()
            self.refresh_results()

        save_button = ttk.Button(frame, text="LƯU THAY ĐỔI", command=save, style="Primary.TButton")
        save_button.grid(row=14, column=1, sticky="se", pady=(18, 4))
        def save_with_enter(event):
            if isinstance(event.widget, ttk.Button):
                return None
            save()
            return "break"

        for widget in (combo, sku_entry, barcode_entry, unit_entry, image_url_entry, qty_entry, price_entry, sale_entry):
            widget.bind("<Return>", save_with_enter)
            widget.bind("<KP_Enter>", save_with_enter)
        save_button.bind("<Return>", lambda _event: (save(), "break")[1])
        save_button.bind("<KP_Enter>", lambda _event: (save(), "break")[1])
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(13, weight=1)
        target = editable_entries.get(focus_field)
        if target:
            dialog.after(120, lambda: (target.focus_set(), target.selection_range(0, "end")))

    def suggest_new_sku(self, name, barcode=""):
        barcode = str(barcode or "").strip()
        base = barcode
        if not base:
            normalized = str(name or "").casefold().replace("đ", "d")
            normalized = unicodedata.normalize("NFD", normalized)
            normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
            base = re.sub(r"[^a-z0-9]+", "", normalized)[:42] or "sanphammoi"
        used = {
            str(product.get("sku") or "").strip().casefold()
            for product in invoice_engine.product_index.products
            if str(product.get("sku") or "").strip()
        }
        candidate = base
        suffix = 1
        while candidate.casefold() in used:
            candidate = f"{base[:44]}{suffix}"
            suffix += 1
        return candidate

    def suggest_next_numeric_sku(self):
        """Đề xuất mã tăng dần theo SKU số đang có, tránh ảnh hưởng barcode dài/GTIN."""
        numeric_skus = []
        for product in invoice_engine.product_index.products:
            sku = str(product.get("sku") or "").strip()
            # SKU nội bộ đang dùng là mã ngắn; bỏ qua barcode/GTIN dài 8+ số.
            if sku.isdigit() and 3 <= len(sku) <= 6:
                numeric_skus.append(int(sku))
        return str((max(numeric_skus) + 1) if numeric_skus else 100000)

    def open_new_product_dialog(self, parent, initial_name="", initial_sale_price=""):
        dialog = tk.Toplevel(parent)
        dialog.title("Tạo sản phẩm mới")
        dialog.geometry("620x510")
        dialog.minsize(560, 450)
        dialog.transient(parent)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="TẠO SẢN PHẨM MỚI", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text="Điền thông tin sản phẩm. SKU và barcode được đề xuất theo mã số kế tiếp.", wraplength=540, foreground="#53718C").grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 16))
        next_code = self.suggest_next_numeric_sku()
        name_var = tk.StringVar(value=str(initial_name or "").strip())
        sku_var = tk.StringVar(value=next_code)
        barcode_var = tk.StringVar(value=next_code)
        unit_var = tk.StringVar(value="Cái")
        sale_var = tk.StringVar(value=str(initial_sale_price or "0"))
        image_var = tk.StringVar(value="")
        fields = (
            ("Tên sản phẩm:", name_var),
            ("SKU:", sku_var),
            ("Barcode:", barcode_var),
            ("Đơn vị tính:", unit_var),
            ("Giá bán:", sale_var),
            ("Image URL:", image_var),
        )
        entries = []
        for row, (label, variable) in enumerate(fields, start=2):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=6)
            select_all_on_double_click(entry)
            entries.append(entry)
        outcome = {}

        def save_new_product():
            name = name_var.get().strip()
            sku = sku_var.get().strip()
            barcode = barcode_var.get().strip()
            image_url = image_var.get().strip()
            if not name or not sku:
                return messagebox.showerror(APP_NAME, "Tên sản phẩm và SKU là bắt buộc.", parent=dialog)
            if image_url and not image_url.lower().startswith(("http://", "https://")):
                return messagebox.showerror(APP_NAME, "Image URL phải bắt đầu bằng http:// hoặc https://.", parent=dialog)
            try:
                sale_price = parse_user_number(sale_var.get())
            except ValueError:
                return messagebox.showerror(APP_NAME, "Giá bán không hợp lệ.", parent=dialog)
            outcome.update({
                "name": name, "sku": sku, "barcode": barcode,
                "unit_name": unit_var.get().strip() or "Cái",
                "sale_price": sale_price, "image_url": image_url,
            })
            dialog.destroy()

        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=2, sticky="e", pady=(22, 0))
        ttk.Button(actions, text="Hủy", command=dialog.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="TẠO SẢN PHẨM NÀY", command=save_new_product, style="Primary.TButton").pack(side="left")
        for entry in entries:
            entry.bind("<Return>", lambda _event: (save_new_product(), "break")[1])
            entry.bind("<KP_Enter>", lambda _event: (save_new_product(), "break")[1])
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        frame.columnconfigure(1, weight=1)
        dialog.after(80, lambda: (entries[0].focus_set(), entries[0].selection_range(0, "end")))
        self.root.wait_window(dialog)
        return outcome or None

    def open_catalog_search(
        self, parent, item, product_var, sku_var, barcode_var, create_new_var, selected_catalog
    ):
        dialog = tk.Toplevel(parent)
        dialog.title("Tìm toàn bộ danh mục Sapo")
        dialog.geometry("920x570")
        dialog.transient(parent)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Tìm theo tên, mã SKU hoặc barcode", style="Section.TLabel").pack(anchor="w")
        query_var = tk.StringVar(value=item.get("original_name", ""))
        entry = ttk.Entry(body, textvariable=query_var, font=("Segoe UI", 11))
        entry.pack(fill="x", pady=(7, 9))
        columns = ("name", "sku", "barcode", "cost", "price")
        tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        labels = {"name": "Tên sản phẩm", "sku": "SKU", "barcode": "Barcode", "cost": "Giá vốn", "price": "Giá bán"}
        widths = {"name": 410, "sku": 120, "barcode": 135, "cost": 100, "price": 100}
        for column in columns:
            tree.heading(column, text=labels[column])
            tree.column(column, width=widths[column], anchor="w" if column == "name" else "center")
        tree.pack(fill="both", expand=True)
        result_label = ttk.Label(body, text="")
        result_label.pack(anchor="w", pady=(7, 0))
        current_products = []
        pending = {"job": None}

        def run_search():
            pending["job"] = None
            current_products.clear()
            current_products.extend(invoice_engine.search_catalog(query_var.get(), 100))
            tree.delete(*tree.get_children())
            for index, product in enumerate(current_products):
                tree.insert("", "end", iid=str(index), values=(
                    product.get("name", ""), product.get("sku", ""), product.get("barcode", ""),
                    money(product.get("cost", 0)), money(product.get("price", 0)),
                ))
            if current_products:
                tree.selection_set("0")
                tree.focus("0")
            result_label.configure(text=f"Hiển thị {len(current_products)} kết quả gần nhất. Gõ thêm từ hoặc mã để thu hẹp.")

        def schedule_search(*_args):
            if pending["job"]:
                dialog.after_cancel(pending["job"])
            pending["job"] = dialog.after(250, run_search)

        def choose():
            selected = tree.selection()
            if not selected:
                return messagebox.showinfo(APP_NAME, "Hãy chọn một sản phẩm trong danh sách.", parent=dialog)
            product = current_products[int(selected[0])]
            selected_catalog["product"] = product
            product_var.set(product.get("name", ""))
            sku_var.set(product.get("sku", ""))
            barcode_var.set(product.get("barcode", ""))
            create_new_var.set(False)
            dialog.destroy()

        def choose_with_enter(_event=None):
            if pending["job"]:
                dialog.after_cancel(pending["job"])
                pending["job"] = None
            run_search()
            if current_products:
                tree.selection_set("0")
                tree.focus("0")
                choose()
            return "break"

        query_var.trace_add("write", schedule_search)
        entry.bind("<Return>", choose_with_enter)
        entry.bind("<KP_Enter>", choose_with_enter)
        select_all_on_double_click(entry)
        tree.bind("<Double-1>", lambda _event: choose())
        tree.bind("<Return>", lambda _event: choose())
        tree.bind("<KP_Enter>", lambda _event: choose())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        ttk.Button(body, text="CHỌN SẢN PHẨM NÀY", command=choose, style="Primary.TButton").pack(anchor="e", pady=(10, 0))
        run_search()
        dialog.after(80, lambda: (entry.focus_set(), entry.selection_range(0, "end"), entry.icursor("end")))

    def export_excel(self):
        if not self.results:
            return messagebox.showinfo(APP_NAME, "Chưa có kết quả để xuất.")
        unmatched = sum(not item.get("matched") for item in self.results)
        if unmatched:
            return messagebox.showwarning(
                APP_NAME,
                f"Còn {unmatched} dòng chưa được xử lý. Hãy chọn sản phẩm có sẵn hoặc đánh dấu "
                "là sản phẩm mới trước khi xuất để tránh tạo file đơn nhập lỗi.",
            )
        self.results = list(invoice_engine.generate_missing_skus(self.results))
        self.refresh_results()
        missing_sku = [item for item in self.results if item.get("matched") and not str(item.get("sku") or "").strip()]
        if missing_sku:
            self.show_missing_sku(missing_sku)
            return
        groups = {}
        for item in self.results:
            groups.setdefault(int(item.get("invoice_index", 0)), []).append(item)
        raw_discount = self.invoice_discount_value_var.get().strip()
        if raw_discount:
            try:
                discount_value = parse_user_number(raw_discount)
            except ValueError:
                return messagebox.showwarning(APP_NAME, "Giảm giá tổng đơn phải là một số hợp lệ.")
            discount_type = self.invoice_discount_type_var.get()
            if discount_value < 0 or (discount_type == "%" and discount_value > 100):
                return messagebox.showwarning(APP_NAME, "Giảm giá không hợp lệ. Phần trăm phải từ 0 đến 100.")
            if self.mode_var.get() == "separate_invoices" and len(groups) > 1:
                return messagebox.showwarning(
                    APP_NAME,
                    "Bạn đang xuất nhiều hóa đơn riêng. Hãy xuất từng hóa đơn nếu cần nhập giảm giá tổng đơn khác nhau.",
                )
            for group in groups.values():
                for item in group:
                    summary = dict(item.get("invoice_summary") or {})
                    summary["manual_discount_value"] = discount_value
                    summary["manual_discount_type"] = discount_type
                    item["invoice_summary"] = summary
        product_updates_required = any(
            item.get("new_product")
            or
            item.get("generated_sku")
            or item.get("sku_changed")
            or item.get("barcode_changed")
            or round(float(item.get("new_sale_price") or item.get("system_sale_price") or 0))
            != round(float(item.get("system_sale_price") or 0))
            for item in self.results
            if item.get("matched")
        )
        product_source = None
        auto_sync = bool(self.auto_sync_var.get())
        if product_updates_required and not auto_sync:
            if any(item.get("new_product") for item in self.results):
                return messagebox.showwarning(
                    APP_NAME,
                    "Hóa đơn có sản phẩm mới. Hãy bật 'Đồng bộ sản phẩm và giá lên Sapo khi xuất' "
                    "để ứng dụng tạo sản phẩm trước khi xuất đơn nhập.",
                )
            selected_source = filedialog.askopenfilename(
                title="Chọn file danh sách sản phẩm MỚI NHẤT vừa xuất từ Sapo",
                filetypes=[("Danh sách sản phẩm Sapo", "*.csv"), ("Tất cả file", "*.*")],
            )
            if not selected_source:
                return messagebox.showwarning(
                    APP_NAME,
                    "Cần file danh sách sản phẩm Sapo mới nhất để tạo SKU trước khi nhập đơn hàng.",
                )
            product_source = Path(selected_source)
        if self.mode_var.get() == "separate_invoices" and len(groups) > 1:
            folder = filedialog.askdirectory(title="Chọn thư mục lưu các file Excel")
            if not folder:
                return
            if product_updates_required and auto_sync and not self.sync_results_to_sapo():
                return
            if product_source:
                try:
                    export_sapo_product_csv(
                        self.results, product_source, Path(folder) / "BUOC-1-cap-nhat-san-pham-sapo.csv"
                    )
                    export_product_updates(
                        self.results, Path(folder) / "bang-kiem-tra-sku-gia-ban.xlsx"
                    )
                except Exception as exc:
                    return messagebox.showerror(APP_NAME, f"Không tạo được file cập nhật sản phẩm: {exc}")
            for position, group in enumerate(groups.values(), start=1):
                summary = group[0].get("invoice_summary") or {}
                number = re.sub(r"[^A-Za-z0-9_-]+", "-", str(summary.get("invoice_number") or position)).strip("-_") or str(position)
                export_sapo_excel(
                    group, Path(folder) / f"BUOC-2-don-nhap-hang-{number}.xlsx",
                    BASE_DIR / "sapo_import_template.xlsx",
                )
            exported_count = len(groups)
            self.clear_after_export(f"Đã xuất {exported_count} file Excel vào: {folder}. Danh sách đã được làm mới.")
            messagebox.showinfo(
                APP_NAME,
                (
                    "Đã đồng bộ sản phẩm/giá và xuất các file đơn nhập hàng. "
                    "Hãy tải từng file Excel lên mục Đơn nhập hàng của Sapo."
                    if auto_sync else
                    "Đã xuất xong. Hãy nhập BUOC-1-cap-nhat-san-pham-sapo.csv vào danh sách sản phẩm "
                    "và chờ Sapo hoàn tất, sau đó mới nhập các file BUOC-2 đơn nhập hàng."
                ),
            )
        else:
            destination = filedialog.asksaveasfilename(
                title="Lưu file Excel", defaultextension=".xlsx",
                initialfile="BUOC-2-don-nhap-hang-sapo.xlsx", filetypes=[("Excel", "*.xlsx")],
            )
            if not destination:
                return
            if product_updates_required and auto_sync and not self.sync_results_to_sapo():
                return
            if product_source:
                try:
                    export_sapo_product_csv(
                        self.results,
                        product_source,
                        Path(destination).with_name("BUOC-1-cap-nhat-san-pham-sapo.csv"),
                    )
                except Exception as exc:
                    return messagebox.showerror(APP_NAME, f"Không tạo được file cập nhật sản phẩm: {exc}")
            export_sapo_excel(self.results, destination, BASE_DIR / "sapo_import_template.xlsx")
            if not auto_sync:
                update_destination = Path(destination).with_name(
                    f"cap-nhat-sku-gia-ban-{Path(destination).stem}.xlsx"
                )
                export_product_updates(self.results, update_destination)
            self.clear_after_export(f"Đã xuất: {destination}. Danh sách đã được làm mới.")
            messagebox.showinfo(
                APP_NAME,
                "Đã đồng bộ sản phẩm/giá cần thay đổi và xuất file đơn nhập hàng.\n\n"
                "Sapo chưa công bố API nhập file đơn nhập, nên hãy tải file Excel này lên mục Đơn nhập hàng.",
            )

    def sync_results_to_sapo(self):
        sapo_config = self.get_sapo_config()
        if not sapo_config:
            return False
        dialog = tk.Toplevel(self.root)
        dialog.title("Đồng bộ với Sapo")
        dialog.geometry("560x210")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Đang cập nhật sản phẩm trước khi xuất...", style="Section.TLabel").pack(anchor="w")
        progress_var = tk.StringVar(value="Đang kết nối Sapo...")
        ttk.Label(body, textvariable=progress_var, foreground="#53718C").pack(anchor="w", pady=(12, 8))
        progress = ttk.Progressbar(body, mode="determinate", maximum=1, value=0)
        progress.pack(fill="x", pady=8)
        dialog.update_idletasks()
        outcome = {"ok": False}

        def report(current, total, action, item):
            def update_ui():
                if not dialog.winfo_exists():
                    return
                progress.configure(maximum=max(total, 1), value=current)
                verb = "Tạo" if action == "create" else "Cập nhật"
                progress_var.set(
                    f"{verb} {current}/{total}: {item.get('sapo_name') or item.get('original_name')}"
                )
            self.root.after(0, update_ui)

        def finish_success(stats):
            try:
                self.update_local_database_from_results()
                self.config["auto_sync_on_export"] = bool(self.auto_sync_var.get())
                self.config_store.save(self.config)
            except Exception as exc:
                return finish_error(f"Đã cập nhật Sapo nhưng chưa làm mới được database: {exc}")
            outcome["ok"] = True
            if dialog.winfo_exists():
                dialog.destroy()
            self.refresh_results()
            self.set_status(
                f"Đã tạo {stats['created']} sản phẩm mới và cập nhật {stats['updated']} sản phẩm trên Sapo."
            )

        def finish_error(error):
            if dialog.winfo_exists():
                dialog.destroy()
            messagebox.showerror(
                APP_NAME,
                f"Chưa thể đồng bộ sản phẩm lên Sapo: {error}\n\nFile Excel chưa được xuất để tránh lệch dữ liệu.",
            )

        def worker():
            try:
                client = sapo_direct.SapoApiClient(sapo_config)
                stats = sapo_direct.sync_invoice_products(client, self.results, progress=report)
                self.root.after(0, lambda: finish_success(stats))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): finish_error(error))

        dialog.protocol("WM_DELETE_WINDOW", lambda: None)
        threading.Thread(target=worker, daemon=True).start()
        self.root.wait_window(dialog)
        return outcome["ok"]

    def update_local_database_from_results(self):
        path = Path(self.db_var.get().strip() or DATABASE_PATH)
        try:
            database = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(database, list):
                database = []
        except (FileNotFoundError, OSError, ValueError):
            database = []
        by_id = {
            int(item.get("variant_id")): item for item in database
            if isinstance(item, dict) and item.get("variant_id") is not None
        }
        for result in self.results:
            if not result.get("matched") or result.get("variant_id") is None:
                continue
            variant_id = int(result["variant_id"])
            record = by_id.get(variant_id)
            if record is None:
                record = {"variant_id": variant_id}
                database.append(record)
                by_id[variant_id] = record
            sale = float(result.get("new_sale_price") or result.get("system_sale_price") or 0)
            # Giá vốn chỉ thay đổi sau khi file đơn nhập được Sapo xử lý thành công.
            cost = float(result.get("system_cost") or 0)
            record.update({
                "name": result.get("sapo_name") or result.get("original_name") or "",
                "sku": str(result.get("sku") or ""),
                "barcode": str(result.get("barcode") or ""),
                "product_id": result.get("product_id") or record.get("product_id"),
                "unit_name": str(result.get("unit_name") or record.get("unit_name") or ""),
                "image_url": str(result.get("image_url") or record.get("image_url") or ""),
                "price": sale,
                "cost": cost,
                "prices": sorted({value for value in (sale, cost) if value > 0}),
            })
            result["system_sale_price"] = sale
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(database, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        invoice_engine.configure_runtime(
            self.key_var.get().strip(), path,
            DATA_DIR / "learning_rules.json", DATA_DIR / "price_history.json",
            self.model_var.get().strip(),
        )

    def show_missing_sku(self, items):
        dialog = tk.Toplevel(self.root)
        dialog.title("Không thể xuất Excel — thiếu SKU")
        dialog.geometry("760x470")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=f"Sapo bắt buộc SKU. Có {len(items)} sản phẩm chưa có SKU nên ứng dụng đã chặn file lỗi.",
            style="Section.TLabel", wraplength=700,
        ).pack(anchor="w")
        ttk.Label(
            body,
            text="Hãy bổ sung SKU cho sản phẩm trong Sapo, cập nhật lại database, hoặc chọn sản phẩm khác đã có SKU.",
            foreground="#A33A21", wraplength=700,
        ).pack(anchor="w", pady=(6, 10))
        tree = ttk.Treeview(body, columns=("name", "barcode"), show="headings")
        tree.heading("name", text="Sản phẩm thiếu SKU")
        tree.heading("barcode", text="Barcode hiện có")
        tree.column("name", width=520)
        tree.column("barcode", width=160, anchor="center")
        for item in items:
            tree.insert("", "end", values=(item.get("sapo_name") or item.get("original_name"), item.get("barcode") or "—"))
        tree.pack(fill="both", expand=True)
        ttk.Button(body, text="ĐÓNG VÀ SỬA", command=dialog.destroy, style="Primary.TButton").pack(anchor="e", pady=(10, 0))

    def toggle_key(self):
        self.key_entry.configure(show="" if self.show_key_var.get() else "●")

    def choose_database(self):
        path = filedialog.askopenfilename(title="Chọn database sản phẩm", filetypes=[("JSON", "*.json")])
        if path:
            self.db_var.set(path)

    def bulk_create_skus(self):
        source = filedialog.askopenfilename(
            title="Chọn file danh sách sản phẩm MỚI NHẤT từ Sapo",
            filetypes=[("Danh sách sản phẩm Sapo", "*.csv"), ("Tất cả file", "*.*")],
        )
        if not source:
            return
        if not messagebox.askyesno(
            APP_NAME,
            "Ứng dụng chỉ điền SKU cho sản phẩm đang TRỐNG SKU. "
            "SKU đã có sẽ được giữ nguyên; barcode trùng sẽ được thêm số 1, 2, 3...\n\n"
            "Hãy bảo đảm đây là file sản phẩm mới nhất và giữ lại file gốc để dự phòng. Tiếp tục?",
        ):
            return
        destination = filedialog.asksaveasfilename(
            title="Lưu file cập nhật SKU hàng loạt",
            defaultextension=".csv",
            initialfile="CAP-NHAT-SKU-HANG-LOAT-SAPO.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not destination:
            return
        try:
            stats = export_bulk_skus_from_barcodes(
                source, destination, DATABASE_PATH,
            )
            self.db_var.set(str(DATABASE_PATH))
            self.config["database_path"] = str(DATABASE_PATH)
            self.config_store.save(self.config)
            if self.key_var.get().strip():
                invoice_engine.configure_runtime(
                    self.key_var.get().strip(), DATABASE_PATH,
                    DATA_DIR / "learning_rules.json", DATA_DIR / "price_history.json",
                    self.model_var.get().strip(),
                )
        except Exception as exc:
            return messagebox.showerror(APP_NAME, f"Không tạo được file SKU hàng loạt: {exc}")
        messagebox.showinfo(
            APP_NAME,
            f"Đã thay đổi SKU của {stats['updated']} sản phẩm.\n"
            f"Giữ nguyên {stats['preserved_existing_sku']} sản phẩm đã có SKU.\n"
            f"Có {stats['duplicate_suffixes']} barcode trùng đã được thêm số.\n"
            f"Bỏ qua {stats['skipped_no_barcode']} sản phẩm không có barcode.\n\n"
            "Database trong ứng dụng đã được cập nhật. Bây giờ hãy nhập file CSV vừa tạo vào Sapo một lần.",
        )

    def direct_update_skus(self):
        if self.busy:
            return messagebox.showinfo(APP_NAME, "Ứng dụng đang thực hiện một tác vụ khác.")
        sapo_config = self.get_sapo_config()
        if not sapo_config:
            return

        self.busy = True
        cancel_event = threading.Event()
        dialog = tk.Toplevel(self.root)
        dialog.title("Đồng bộ SKU trực tiếp lên Sapo")
        dialog.geometry("660x250")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Đang đọc danh sách sản phẩm trực tiếp từ Sapo...", style="Section.TLabel").pack(anchor="w")
        progress_var = tk.StringVar(value="Đang kết nối...")
        ttk.Label(body, textvariable=progress_var, wraplength=600, foreground="#53718C").pack(anchor="w", pady=(12, 10))
        progress_bar = ttk.Progressbar(body, mode="indeterminate")
        progress_bar.pack(fill="x", pady=8)
        progress_bar.start(12)
        cancel_button = ttk.Button(body, text="Dừng", state="disabled")
        cancel_button.pack(anchor="e", pady=(14, 0))

        def close_dialog():
            if cancel_button.cget("state") == "normal":
                cancel_event.set()
                progress_var.set("Đang dừng an toàn. Có thể chạy lại để tiếp tục phần còn lại...")

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

        def report(stage, current, total):
            def update_ui():
                if not dialog.winfo_exists():
                    return
                if stage == "download":
                    progress_var.set(f"Đã đọc {current:,} sản phẩm từ Sapo...")
                else:
                    progress_var.set(f"Đang cập nhật SKU: {current:,}/{total:,}")
                    progress_bar.configure(mode="determinate", maximum=max(total, 1), value=current)
            self.root.after(0, update_ui)

        def finish_with_error(error):
            self.busy = False
            if dialog.winfo_exists():
                dialog.destroy()
            messagebox.showerror(
                APP_NAME,
                f"Cập nhật SKU chưa hoàn tất: {error}\n\n"
                "Anh có thể chạy lại; ứng dụng sẽ tự bỏ qua các SKU đã cập nhật thành công.",
            )

        def finish_success(updated, database_count):
            self.busy = False
            if dialog.winfo_exists():
                dialog.destroy()
            self.db_var.set(str(DATABASE_PATH))
            self.config["database_path"] = str(DATABASE_PATH)
            try:
                self.config_store.save(self.config)
                if self.key_var.get().strip():
                    invoice_engine.configure_runtime(
                        self.key_var.get().strip(), DATABASE_PATH,
                        DATA_DIR / "learning_rules.json", DATA_DIR / "price_history.json",
                        self.model_var.get().strip(),
                    )
            except Exception:
                pass
            self.set_status(f"Đã cập nhật trực tiếp {updated:,} SKU trên Sapo.")
            messagebox.showinfo(
                APP_NAME,
                f"Đã cập nhật trực tiếp {updated:,} SKU trên Sapo.\n"
                f"Database ứng dụng đã làm mới với {database_count:,} sản phẩm.\n\n"
                "Từ bây giờ ứng dụng không cần tạo SKU riêng cho các sản phẩm này nữa.",
            )

        def confirm_plan(client, plan):
            if not dialog.winfo_exists():
                self.busy = False
                return
            progress_bar.stop()
            changes = len(plan["changes"])
            if not changes:
                self.busy = False
                dialog.destroy()
                return messagebox.showinfo(
                    APP_NAME, "Không còn sản phẩm trống SKU và có barcode để cập nhật."
                )
            approved = messagebox.askyesno(
                APP_NAME,
                f"Sắp ĐIỀN MỚI {changes:,} SKU đang trống trên Sapo.\n\n"
                f"Tổng phiên bản kiểm tra: {plan['variant_count']:,}\n"
                f"Đã có SKU, giữ nguyên: {plan['existing_sku']:,}\n"
                f"Barcode trùng cần thêm số: {plan['duplicate_suffixes']:,}\n"
                f"Trống SKU nhưng không có barcode, bỏ qua: {plan['no_barcode']:,}\n\n"
                "Thao tác có thể mất nhiều phút. Không tắt máy trong lúc cập nhật. Bắt đầu?",
                parent=dialog,
            )
            if not approved:
                self.busy = False
                dialog.destroy()
                return
            cancel_button.configure(state="normal", command=close_dialog)
            progress_bar.configure(mode="determinate", maximum=max(changes, 1), value=0)
            progress_var.set(f"Bắt đầu cập nhật 0/{changes:,} SKU...")

            def apply_worker():
                try:
                    updated = sapo_direct.apply_bulk_skus(
                        client, plan, progress=report, cancel_event=cancel_event,
                    )
                    if cancel_event.is_set():
                        raise RuntimeError("Đã dừng theo yêu cầu")
                    database_count = sapo_direct.save_database(plan["products"], DATABASE_PATH)
                    self.root.after(0, lambda: finish_success(updated, database_count))
                except Exception as exc:
                    self.root.after(0, lambda error=str(exc): finish_with_error(error))

            threading.Thread(target=apply_worker, daemon=True).start()

        def download_worker():
            try:
                client = sapo_direct.SapoApiClient(sapo_config)
                products = sapo_direct.download_products(client, progress=report)
                plan = sapo_direct.plan_bulk_skus(products)
                self.root.after(0, lambda: confirm_plan(client, plan))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): finish_with_error(error))

        threading.Thread(target=download_worker, daemon=True).start()

    def enable_negative_inventory(self):
        """Enable Sapo's 'Cho phép bán âm' setting for every variant once."""
        if self.busy:
            return messagebox.showinfo(APP_NAME, "Ứng dụng đang thực hiện một tác vụ khác.")
        sapo_config = self.get_sapo_config()
        if not sapo_config:
            return

        self.busy = True
        cancel_event = threading.Event()
        dialog = tk.Toplevel(self.root)
        dialog.title("Bật cho phép bán âm")
        dialog.geometry("660x250")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Đang kiểm tra chính sách tồn kho trên Sapo...", style="Section.TLabel").pack(anchor="w")
        progress_var = tk.StringVar(value="Đang kết nối...")
        ttk.Label(body, textvariable=progress_var, wraplength=600, foreground="#53718C").pack(anchor="w", pady=(12, 10))
        progress_bar = ttk.Progressbar(body, mode="indeterminate")
        progress_bar.pack(fill="x", pady=8)
        progress_bar.start(12)
        cancel_button = ttk.Button(body, text="Dừng", state="disabled")
        cancel_button.pack(anchor="e", pady=(14, 0))

        def close_dialog():
            if cancel_button.cget("state") == "normal":
                cancel_event.set()
                progress_var.set("Đang dừng an toàn. Có thể chạy lại để hoàn tất phần còn lại...")

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

        def report(stage, current, total):
            def update_ui():
                if not dialog.winfo_exists():
                    return
                if stage == "download":
                    progress_var.set(f"Đã đọc {current:,} sản phẩm từ Sapo...")
                else:
                    progress_var.set(f"Đang bật cho phép bán âm: {current:,}/{total:,} phiên bản...")
                    progress_bar.configure(mode="determinate", maximum=max(total, 1), value=current)
            self.root.after(0, update_ui)

        def finish_error(error):
            self.busy = False
            if dialog.winfo_exists():
                dialog.destroy()
            messagebox.showerror(
                APP_NAME,
                f"Chưa bật xong cho phép bán âm: {error}\n\n"
                "Anh có thể chạy lại; các phiên bản đã bật thành công sẽ tự được bỏ qua.",
            )

        def finish_success(updated, already_enabled):
            self.busy = False
            if dialog.winfo_exists():
                dialog.destroy()
            self.set_status(f"Đã bật cho phép bán âm cho {updated:,} phiên bản trên Sapo.")
            messagebox.showinfo(
                APP_NAME,
                f"Đã bật ‘Cho phép bán âm’ cho {updated:,} phiên bản sản phẩm.\n"
                f"Đã bật sẵn từ trước: {already_enabled:,} phiên bản.\n\n"
                "SKU, barcode, giá bán và tồn kho không bị thay đổi.",
            )

        def confirm_plan(client, plan):
            if not dialog.winfo_exists():
                self.busy = False
                return
            progress_bar.stop()
            changes = len(plan["changes"])
            if not changes:
                self.busy = False
                dialog.destroy()
                return messagebox.showinfo(APP_NAME, "Toàn bộ phiên bản sản phẩm đã được cho phép bán âm.")
            approved = messagebox.askyesno(
                APP_NAME,
                f"Sắp bật ‘Cho phép bán âm’ cho {changes:,} phiên bản trên Sapo.\n\n"
                f"Tổng phiên bản kiểm tra: {plan['variant_count']:,}\n"
                f"Đã bật sẵn, giữ nguyên: {plan['already_enabled']:,}\n\n"
                "Thao tác chỉ thay đổi chính sách tồn kho, không đổi SKU, barcode, giá hoặc số lượng. Bắt đầu?",
                parent=dialog,
            )
            if not approved:
                self.busy = False
                dialog.destroy()
                return
            cancel_button.configure(state="normal", command=close_dialog)
            progress_bar.configure(mode="determinate", maximum=max(changes, 1), value=0)

            def apply_worker():
                try:
                    updated = sapo_direct.apply_allow_negative_inventory(
                        client, plan, progress=report, cancel_event=cancel_event,
                    )
                    if cancel_event.is_set():
                        raise RuntimeError("Đã dừng theo yêu cầu")
                    self.root.after(0, lambda: finish_success(updated, plan["already_enabled"]))
                except Exception as exc:
                    self.root.after(0, lambda error=str(exc): finish_error(error))

            threading.Thread(target=apply_worker, daemon=True).start()

        def download_worker():
            try:
                client = sapo_direct.SapoApiClient(sapo_config)
                products = sapo_direct.download_products(client, progress=report)
                plan = sapo_direct.plan_allow_negative_inventory(products)
                self.root.after(0, lambda: confirm_plan(client, plan))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): finish_error(error))

        threading.Thread(target=download_worker, daemon=True).start()

    def restore_skus_from_backup(self):
        if self.busy:
            return messagebox.showinfo(APP_NAME, "Ứng dụng đang thực hiện một tác vụ khác.")
        source = filedialog.askopenfilename(
            title="Chọn file danh sách sản phẩm TRƯỚC KHI SKU bị đổi",
            filetypes=[("Danh sách sản phẩm Sapo", "*.csv"), ("Tất cả file", "*.*")],
        )
        if not source:
            return
        try:
            backup, duplicate_ids = sapo_direct.load_sku_backup_csv(source)
        except Exception as exc:
            return messagebox.showerror(APP_NAME, f"Không đọc được file sao lưu: {exc}")
        sapo_config = self.get_sapo_config()
        if not sapo_config:
            return

        self.busy = True
        cancel_event = threading.Event()
        dialog = tk.Toplevel(self.root)
        dialog.title("Khôi phục SKU gốc")
        dialog.geometry("660x250")
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Đang đối chiếu file sao lưu với Sapo...", style="Section.TLabel").pack(anchor="w")
        progress_var = tk.StringVar(value=f"Đã đọc {len(backup):,} SKU gốc từ file.")
        ttk.Label(body, textvariable=progress_var, wraplength=600, foreground="#53718C").pack(anchor="w", pady=(12, 10))
        progress_bar = ttk.Progressbar(body, mode="indeterminate")
        progress_bar.pack(fill="x", pady=8)
        progress_bar.start(12)
        cancel_button = ttk.Button(body, text="Dừng", state="disabled")
        cancel_button.pack(anchor="e", pady=(14, 0))

        def close_dialog():
            if cancel_button.cget("state") == "normal":
                cancel_event.set()
                progress_var.set("Đang dừng an toàn; có thể chạy lại để tiếp tục...")

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

        def report(stage, current, total):
            def update_ui():
                if not dialog.winfo_exists():
                    return
                if stage == "download":
                    progress_var.set(f"Đã đọc {current:,} sản phẩm hiện tại từ Sapo...")
                else:
                    progress_var.set(f"Đang khôi phục SKU: {current:,}/{total:,}")
                    progress_bar.configure(mode="determinate", maximum=max(total, 1), value=current)
            self.root.after(0, update_ui)

        def finish_error(error):
            self.busy = False
            if dialog.winfo_exists():
                dialog.destroy()
            messagebox.showerror(
                APP_NAME,
                f"Khôi phục chưa hoàn tất: {error}\n\nChạy lại với cùng file để tiếp tục; SKU đã đúng sẽ được bỏ qua.",
            )

        def finish_success(updated, database_count):
            self.busy = False
            if dialog.winfo_exists():
                dialog.destroy()
            self.db_var.set(str(DATABASE_PATH))
            self.config["database_path"] = str(DATABASE_PATH)
            self.config_store.save(self.config)
            self.set_status(f"Đã khôi phục {updated:,} SKU gốc trên Sapo.")
            messagebox.showinfo(
                APP_NAME,
                f"Đã khôi phục {updated:,} SKU gốc trên Sapo.\n"
                f"Database ứng dụng đã làm mới với {database_count:,} sản phẩm.\n\n"
                "Các sản phẩm từng trống SKU trong file sao lưu được giữ nguyên.",
            )

        def confirm_plan(client, plan):
            if not dialog.winfo_exists():
                self.busy = False
                return
            progress_bar.stop()
            changes = len(plan["changes"])
            if not changes:
                self.busy = False
                dialog.destroy()
                return messagebox.showinfo(APP_NAME, "Các SKU gốc trong file đều đã đúng trên Sapo.")
            approved = messagebox.askyesno(
                APP_NAME,
                f"Sắp KHÔI PHỤC {changes:,} SKU gốc lên Sapo.\n\n"
                f"SKU gốc trong file: {plan['backup_sku_count']:,}\n"
                f"Đã đúng, không thay đổi: {plan['already_correct']:,}\n"
                f"Không còn tìm thấy trên Sapo: {plan['missing_current']:,}\n"
                f"SKU trùng vốn có trong file: {plan['duplicate_backup_skus']:,}\n"
                f"Id phiên bản trùng trong file: {duplicate_ids:,}\n\n"
                "Chỉ những dòng từng có SKU mới được khôi phục. Bắt đầu?",
                parent=dialog,
            )
            if not approved:
                self.busy = False
                dialog.destroy()
                return
            cancel_button.configure(state="normal", command=close_dialog)
            progress_bar.configure(mode="determinate", maximum=max(changes, 1), value=0)

            def apply_worker():
                try:
                    updated = sapo_direct.apply_bulk_skus(
                        client, plan, progress=report, cancel_event=cancel_event,
                    )
                    if cancel_event.is_set():
                        raise RuntimeError("Đã dừng theo yêu cầu")
                    database_count = sapo_direct.save_database(plan["products"], DATABASE_PATH)
                    self.root.after(0, lambda: finish_success(updated, database_count))
                except Exception as exc:
                    self.root.after(0, lambda error=str(exc): finish_error(error))

            threading.Thread(target=apply_worker, daemon=True).start()

        def download_worker():
            try:
                client = sapo_direct.SapoApiClient(sapo_config)
                products = sapo_direct.download_products(client, progress=report)
                plan = sapo_direct.plan_restore_skus(products, backup)
                self.root.after(0, lambda: confirm_plan(client, plan))
            except Exception as exc:
                self.root.after(0, lambda error=str(exc): finish_error(error))

        threading.Thread(target=download_worker, daemon=True).start()

    def get_sapo_config(self):
        local = {
            "store": self.config.get("sapo_store", ""),
            "auth_mode": self.config.get("sapo_auth_mode", "basic"),
            "api_key": self.config.get("sapo_api_key", ""),
            "api_secret": self.config.get("sapo_api_secret", ""),
            "access_token": self.config.get("sapo_access_token", ""),
        }
        try:
            return sapo_direct.validate_config(local)
        except Exception:
            pass
        try:
            legacy = sapo_direct.load_saved_config()
            self.config.update({
                "sapo_store": legacy.get("store", ""),
                "sapo_auth_mode": legacy.get("auth_mode", "basic"),
                "sapo_api_key": legacy.get("api_key", ""),
                "sapo_api_secret": legacy.get("api_secret", ""),
                "sapo_access_token": legacy.get("access_token", ""),
            })
            self.config_store.save(self.config)
            return legacy
        except Exception:
            return self.ask_sapo_config()

    def ask_sapo_config(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Kết nối Sapo")
        dialog.geometry("610x330")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Kết nối trực tiếp với Sapo", style="Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(
            body,
            text="Chỉ cần thiết lập một lần. Ứng dụng riêng trên Sapo phải có quyền Sản phẩm: Đọc và ghi.",
            foreground="#53718C", wraplength=550,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))
        store_var = tk.StringVar(value=self.config.get("sapo_store", "hkd-hoang-phi-long.mysapo.net"))
        api_key_var = tk.StringVar(value=self.config.get("sapo_api_key", ""))
        api_secret_var = tk.StringVar(value=self.config.get("sapo_api_secret", ""))
        labels = (("Địa chỉ cửa hàng:", store_var), ("API Key:", api_key_var), ("API Secret:", api_secret_var))
        entries = []
        for row, (label, variable) in enumerate(labels, start=2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=7)
            entry = ttk.Entry(body, textvariable=variable, width=52, show="●" if row in (3, 4) else "")
            entry.grid(row=row, column=1, sticky="ew", padx=(12, 6), pady=7)
            entries.append(entry)
        show_var = tk.BooleanVar(value=False)

        def toggle_secrets():
            marker = "" if show_var.get() else "●"
            entries[1].configure(show=marker)
            entries[2].configure(show=marker)

        ttk.Checkbutton(body, text="Hiện", variable=show_var, command=toggle_secrets).grid(
            row=3, column=2, rowspan=2, sticky="n", pady=8
        )
        result = {}

        def save_connection():
            candidate = {
                "store": store_var.get().strip(), "auth_mode": "basic",
                "api_key": api_key_var.get().strip(), "api_secret": api_secret_var.get().strip(),
            }
            try:
                validated = sapo_direct.validate_config(candidate)
                self.config.update({
                    "sapo_store": validated["store"], "sapo_auth_mode": "basic",
                    "sapo_api_key": validated["api_key"], "sapo_api_secret": validated["api_secret"],
                    "sapo_access_token": "",
                })
                self.config_store.save(self.config)
            except Exception as exc:
                return messagebox.showerror(APP_NAME, f"Không lưu được kết nối Sapo: {exc}", parent=dialog)
            result.update(validated)
            dialog.destroy()

        actions = ttk.Frame(body)
        actions.grid(row=5, column=0, columnspan=3, sticky="e", pady=(20, 0))
        ttk.Button(actions, text="Hủy", command=dialog.destroy).pack(side="left", padx=6)
        ttk.Button(actions, text="LƯU VÀ KẾT NỐI", command=save_connection, style="Primary.TButton").pack(side="left")
        body.columnconfigure(1, weight=1)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        entries[0].focus_set()
        self.root.wait_window(dialog)
        return result or None

    def save_config(self):
        config = {**self.config,
            "openai_key": self.key_var.get().strip(),
            "model": self.model_var.get().strip() or "gpt-5.6-terra",
            "database_path": self.db_var.get().strip() or str(DATABASE_PATH),
            "auto_sync_on_export": bool(self.auto_sync_var.get()),
        }
        try:
            self.config_store.save(config)
        except Exception as exc:
            return messagebox.showerror(APP_NAME, f"Không lưu được cấu hình: {exc}")
        self.config = config
        messagebox.showinfo(APP_NAME, "Đã lưu cấu hình an toàn trên máy.")


def main():
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    InvoiceDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
