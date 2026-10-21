"""兼容原有启动命令：python rename_invoice.py。"""
from invoice_core import extract_invoice_info, unique_target_path

USER_NAME = "刘永军"


def main():
    from invoice_app import main as launch
    launch()


if __name__ == "__main__":
    main()
