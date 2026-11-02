#!/usr/bin/env python3
"""
Tiện ích tự động cấu hình Tên Miền Riêng Cố Định cho Telegram Mini App
Sử dụng Cloudflare Named Tunnel (Miễn phí 100%)
"""

import sys
import os
import re
from pathlib import Path

ENV_FILE = Path(r"e:\khaihoan-telegram-bot\telegram_bot.env")


def update_env(key: str, value: str):
    if not ENV_FILE.exists():
        ENV_FILE.touch()
    
    text = ENV_FILE.read_text(encoding="utf-8", errors="ignore")
    pattern = rf"^{re.escape(key)}=.*$"
    new_line = f"{key}={value}"

    if re.search(pattern, text, flags=re.MULTILINE):
        new_text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
    else:
        new_text = text.rstrip() + f"\n{new_line}\n"

    ENV_FILE.write_text(new_text, encoding="utf-8")
    print(f"[*] Đã lưu cấu hình: {key}={value}")


def main():
    print("=" * 60)
    print("   CẤU HÌNH TÊN MIỀN RIÊNG CỐ ĐỊNH CHO MINI APP")
    print("=" * 60)
    print("Tên miền đề xuất: https://app.khaihoanderma.com")
    print("-" * 60)

    token = None
    domain = "https://app.khaihoanderma.com"

    if len(sys.argv) > 1:
        token = sys.argv[1].strip()
        if len(sys.argv) > 2:
            domain = sys.argv[2].strip()
    else:
        print("Vui lòng lấy Cloudflare Tunnel Token từ trang:")
        print("https://one.dash.cloudflare.com -> Networks -> Tunnels\n")
        try:
            token = input("Nhập Cloudflare Tunnel Token (chuỗi eyJhIj...): ").strip()
            custom_domain_input = input("Nhập URL tên miền riêng [Mặc định: https://app.khaihoanderma.com]: ").strip()
            if custom_domain_input:
                domain = custom_domain_input
        except (KeyboardInterrupt, EOFError):
            print("\nĐã hủy thao tác.")
            sys.exit(0)

    if not token:
        print("[!] Lỗi: Bạn chưa cung cấp Cloudflare Tunnel Token.")
        sys.exit(1)

    if not domain.startswith("http://") and not domain.startswith("https://"):
        domain = "https://" + domain

    update_env("CLOUDFLARE_TUNNEL_TOKEN", token)
    update_env("MINI_APP_URL", domain)

    print("\n[✓] Cấu hình hoàn tất!")
    print(f"[✓] Tên miền cố định: {domain}")
    print("[*] Vui lòng chạy lại bot bằng file Chay_Bot_Ngam.bat hoặc khởi động lại tiến trình bot.")


if __name__ == "__main__":
    main()
