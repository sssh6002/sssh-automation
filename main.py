"""
main.py
自動化主程式 — 統一呼叫各功能模組的入口。

執行方式：
    C:\\Python314\\python.exe main.py
    C:\\Python314\\python.exe c:\\Users\\ldc\\Documents\\GitHub\\sssh-automation\\main.py

執行後直接跑 FEATURES 清單中的第一項，跑完即結束，回到原本的 PowerShell / CMD 視窗。
"""

import sys

sys.stdout.reconfigure(encoding='utf-8')

from taipeion_login import login_taipeion

# ── 功能清單 ──────────────────────────────────────────────────────────────────
# 每新增一個功能，在此加入一列：(顯示名稱, 呼叫函式)
# 目前固定執行第一項；若要改為其他項目，調整下方 main() 的索引即可。

FEATURES = [
    ("臺北市單一帳號認證平台 — 自然人憑證登入", login_taipeion),
]


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    name, func = FEATURES[0]
    print(f"▶ 執行：{name}")
    print("-" * 40)
    func()
    print("-" * 40)
    print("[完成] 程式結束。")


if __name__ == "__main__":
    main()
