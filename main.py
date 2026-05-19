"""
main.py
自動化主程式 — 統一呼叫各功能模組的入口。

執行方式：
    C:\\Python314\\python.exe main.py            # 預設跑 FEATURES[0]（Selenium 版）
    C:\\Python314\\python.exe main.py 2          # 跑 FEATURES[1]（pyautogui 版）
    C:\\Python314\\python.exe c:\\Users\\ldc\\Documents\\GitHub\\sssh-automation\\main.py

執行後跑指定 FEATURE，結束即回到原本的 PowerShell / CMD 視窗（不再進入選單迴圈）。
"""

import sys

sys.stdout.reconfigure(encoding='utf-8')

from taipeion_login_selenium import login_taipeion_selenium, _close_selenium_chrome_only
from taipeion_login import login_taipeion
from click_document import click_document_card

_close_selenium_chrome_only()

# ── 功能清單 ──────────────────────────────────────────────────────────────────
# 每新增一列：(顯示名稱, 主函式, 登入後動作 or None)
# - 若有「登入後動作」，主函式須支援 return_driver=True 並回傳 driver，跑完接著呼叫 post_login(driver)
# - 沒有後續動作則第三欄填 None，直接呼叫主函式
# 預設執行 FEATURES[0]；可用 CLI 引數選其他項，例如：
#   python main.py        # 跑 FEATURES[0]（Selenium 版 + 點公文）
#   python main.py 2      # 跑 FEATURES[1]（pyautogui 像素點擊版）

FEATURES = [
    ("臺北市單一帳號認證平台 — 自然人憑證登入 + 點公文（Selenium 版）", login_taipeion_selenium, click_document_card),
    ("臺北市單一帳號認證平台 — 自然人憑證登入（pyautogui 像素版）", login_taipeion, None),
]


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    idx = 0
    if len(sys.argv) > 1:
        try:
            idx = int(sys.argv[1]) - 1
            if not (0 <= idx < len(FEATURES)):
                raise ValueError
        except ValueError:
            print(f"[ERROR] 無效引數 '{sys.argv[1]}'，請傳入 1~{len(FEATURES)}")
            return

    name, func, post_login = FEATURES[idx]
    print(f"▶ 執行：{name}")
    print("-" * 40)
    if post_login is None:
        func()
    else:
        driver = func(return_driver=True)
        if driver is None:
            print("[ERROR] 登入未完成，跳過後續動作。")
        else:
            post_login(driver)
    print("-" * 40)
    print("[完成] 程式結束。")


if __name__ == "__main__":
    main()
