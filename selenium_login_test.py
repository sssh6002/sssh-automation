"""
selenium_login_test.py
最小 Selenium 範例 — 驗證能否走完 TAIPEION 登入流程的前兩步。

實驗目的（第一階段）：
  1. 確認 Selenium 能在 Python 3.14 + 現有 Chrome 啟動
  2. 確認可定位並點擊「自然人憑證」分頁（不靠像素，改用 DOM）
  3. 確認可定位並點擊「登入」按鈕
  4. 觀察 Windows 憑證選擇對話框的出現時機

本範例不處理（保留至第二階段再評估）：
  - AutoSelectCertificateForUrls 群組原則（讓憑證對話框自動選取）
  - 螢幕鎖定下的執行
  - 失敗重試 / 截圖儲存

執行前必做：
  ★ 關閉所有「1504@sssh.tp.edu.tw（Profile 2）」的 Chrome 視窗 ★
  Chrome 同一 profile 不允許兩個程序同時開啟，否則會出現
  "user data directory is already in use" 錯誤。

執行方式：
    C:\\Python314\\python.exe -m pip install selenium
    C:\\Python314\\python.exe selenium_login_test.py

執行後請觀察：
  A. 是否成功點到「自然人憑證」分頁（畫面切換）
  B. 是否成功點到「登入」按鈕
  C. 是否跳出 Windows 憑證選擇對話框 / 密碼欄是否帶入儲存密碼
"""

import json
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException

URL = "https://login.gov.taipei/login.php"

# 使用既有 Chrome Profile 2，繼承儲存的密碼 / 憑證設定 / 擴充功能
USER_DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
PROFILE_DIR = "Profile 2"

# 嘗試多組 XPath，依序測試
CERT_TAB_XPATHS = [
    "//a[contains(., '自然人憑證')]",
    "//button[contains(., '自然人憑證')]",
    "//li[contains(., '自然人憑證')]",
    "//*[@role='tab' and contains(., '自然人憑證')]",
    "//*[contains(text(), '自然人憑證')]",
]

LOGIN_BTN_XPATHS = [
    "//button[normalize-space()='登入']",
    "//input[@type='submit' and contains(@value, '登入')]",
    "//a[normalize-space()='登入']",
    "//button[contains(., '登入')]",
]


def mark_profile_clean_exit():
    """
    將 profile 的 Preferences 標記為正常退出，跳過「Chrome 未正確關閉，是否還原網頁」對話框。
    Selenium 強制接管 profile 時 Chrome 會把上次視為異常終止，需要這個處理。
    """
    prefs_path = os.path.join(USER_DATA_DIR, PROFILE_DIR, "Preferences")
    if not os.path.isfile(prefs_path):
        print(f"      [警告] 找不到 Preferences：{prefs_path}")
        return
    try:
        with open(prefs_path, "r", encoding="utf-8") as f:
            prefs = json.load(f)
        profile = prefs.setdefault("profile", {})
        profile["exit_type"] = "Normal"
        profile["exited_cleanly"] = True
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
        print("      Preferences 已標記為正常退出，跳過還原網頁提示。")
    except Exception as e:
        print(f"      [警告] 無法修改 Preferences：{e}")


def try_click(driver, xpaths, label, timeout=8):
    """依序嘗試多組 XPath，回傳第一個成功點到的元素描述；全失敗則回傳 None。"""
    wait = WebDriverWait(driver, timeout)
    for xp in xpaths:
        try:
            el = wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
            el.click()
            print(f"      OK：以 XPath 命中 → {xp}")
            return xp
        except TimeoutException:
            print(f"      x  XPath 失敗 → {xp}")
            continue
    print(f"[ERROR] {label} 全部 XPath 都失敗。")
    return None


def dump_page_for_debug(driver):
    """流程卡住時印出頁面標題與部分 HTML，方便調整 selector。"""
    print("\n─── 除錯資訊 ───")
    print(f"目前網址：{driver.current_url}")
    print(f"頁面標題：{driver.title}")
    try:
        html = driver.page_source
        # 只印前 2000 字，避免洗版
        snippet = html[:2000].replace("\n", " ")
        print(f"HTML 前 2000 字：\n{snippet}")
    except Exception as e:
        print(f"無法讀取 page_source：{e}")
    print("────────────────")


def main():
    print(f"[0/4] 使用 Chrome Profile：{PROFILE_DIR}")
    print(f"      路徑：{USER_DATA_DIR}")
    if not os.path.isdir(os.path.join(USER_DATA_DIR, PROFILE_DIR)):
        print(f"[FATAL] 找不到 profile 目錄：{os.path.join(USER_DATA_DIR, PROFILE_DIR)}")
        return

    mark_profile_clean_exit()

    options = Options()
    options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
    options.add_argument(f"--profile-directory={PROFILE_DIR}")
    options.add_argument("--start-maximized")
    # 跑完不要關閉瀏覽器，方便人工觀察 / 接手登入
    options.add_experimental_option("detach", True)
    # 抑制 USB / Bluetooth 等噪音 log
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    print("[1/4] 啟動 Chrome（Selenium Manager 自動下載對應 ChromeDriver）...")
    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as e:
        msg = str(e)
        if "user data directory is already in use" in msg.lower() or "session not created" in msg.lower():
            print("[FATAL] Chrome 該 profile 正在使用中。")
            print("        請先關閉所有 1504@sssh.tp.edu.tw（Profile 2）的 Chrome 視窗再執行。")
        else:
            print(f"[FATAL] 無法啟動 Chrome：{e}")
        return

    try:
        print(f"[2/4] 開啟 {URL}")
        driver.get(URL)
        time.sleep(2)
        print(f"      頁面標題：{driver.title}")

        print("[3/4] 點選『自然人憑證』分頁...")
        if not try_click(driver, CERT_TAB_XPATHS, "自然人憑證分頁"):
            dump_page_for_debug(driver)
            return
        time.sleep(1.5)

        print("[4/4] 點選『登入』按鈕...")
        if not try_click(driver, LOGIN_BTN_XPATHS, "登入按鈕"):
            dump_page_for_debug(driver)
            return
        time.sleep(3)

        print("\n[完成] 已執行到憑證對話框出現的階段。")
        print("      請觀察：")
        print("        A. 是否成功切換到自然人憑證分頁？")
        print("        B. 是否成功點到登入按鈕？")
        print("        C. 是否跳出 Windows 憑證選擇對話框？")
        print("      瀏覽器保持開啟，可接手人工完成或關閉。")

    except Exception as e:
        print(f"[ERROR] 流程中斷：{e}")
        dump_page_for_debug(driver)


if __name__ == "__main__":
    main()
