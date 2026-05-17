"""
main.py
自動化主程式 — 統一呼叫各功能模組的入口。

執行方式：
    C:\\Python314\\python.exe main.py
    C:\\Python314\\python.exe c:\\Users\\ldc\\Documents\\GitHub\\sssh-automation\\main.py
"""

import msvcrt
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

from taipeion_login import login_taipeion

# ── 功能清單 ──────────────────────────────────────────────────────────────────
# 每新增一個功能，在此加入一列：(顯示名稱, 呼叫函式)

FEATURES = [
    ("臺北市單一帳號認證平台 — 自然人憑證登入", login_taipeion),
]

AUTO_SELECT = "1"   # 逾時後自動選擇的編號
TIMEOUT     = 1     # 等待秒數


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def timed_input(prompt, timeout=TIMEOUT, default=AUTO_SELECT):
    """顯示提示並等待按鍵；超過 timeout 秒無輸入則回傳 default。"""
    print(prompt, end="", flush=True)
    start = time.time()
    chars = []
    while True:
        if time.time() - start >= timeout:
            print(f"\n（{timeout} 秒未操作，自動選擇：{default}）")
            return default
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ('\r', '\n'):      # Enter
                print()
                return "".join(chars) if chars else default
            elif ch == '\x08':          # Backspace
                if chars:
                    chars.pop()
                    print('\b \b', end="", flush=True)
            elif ch.isprintable():
                chars.append(ch)
                print(ch, end="", flush=True)
        time.sleep(0.05)


# ── 主選單 ────────────────────────────────────────────────────────────────────

def show_menu():
    print("\n===== 自動化功能選單 =====")
    for i, (name, _) in enumerate(FEATURES, start=1):
        print(f"  {i}. {name}")
    print("  0. 離開")
    print("==========================")


def main():
    while True:
        show_menu()
        choice = timed_input(
            f"請選擇功能編號（{TIMEOUT} 秒後自動選 {AUTO_SELECT}）："
        )

        if choice == "0":
            print("再見！")
            break

        if not choice.isdigit() or not (1 <= int(choice) <= len(FEATURES)):
            print(f"[錯誤] 請輸入 0~{len(FEATURES)} 之間的數字")
            continue

        name, func = FEATURES[int(choice) - 1]
        print(f"\n▶ 執行：{name}")
        print("-" * 40)
        func()
        print("-" * 40)
        input("按 Enter 返回選單...")


if __name__ == "__main__":
    main()
