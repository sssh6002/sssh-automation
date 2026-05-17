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

設計重點：
  使用 Selenium 專用 Chrome User Data 目錄（非預設位置），
  繞過 Chrome 136+ 對「預設 User Data dir 啟用 DevTools port」的安全限制。
  此目錄首次執行 Chrome 會自動建立，不影響使用者真正的 Profile 2。
  使用者需在此 profile 內手動登入一次（瀏覽器會儲存密碼），之後即可自動帶入。

執行方式：
    C:\\Python314\\python.exe -m pip install selenium
    C:\\Python314\\python.exe selenium_login_test.py

執行後請觀察：
  A. 是否成功點到「自然人憑證」分頁（畫面切換）
  B. 是否成功點到「登入」按鈕
  C. 結束時自動存截圖 after_login_click.png 供檢查當下畫面
"""

import json
import os
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)

import pyautogui
from PIL import ImageGrab
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException

pyautogui.FAILSAFE = False

URL = "https://login.gov.taipei/login.php"
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selenium_login_test.log")


def log(msg):
    """同時印到 stdout 與寫到 LOG_FILE。stdout 可能被 harness 緩衝，
    log 檔可立即在磁碟上看到，方便 debug 卡住的位置。"""
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# Selenium 專用 Chrome User Data 目錄（非預設位置，繞過 Chrome 136+ 自動化限制）
# 首次執行 Chrome 會自動建立此目錄。使用者於此 profile 手動登入一次後，
# 之後執行可自動帶入儲存的密碼。
USER_DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Chrome-Selenium\User Data")
PROFILE_DIR = "Default"

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


def try_click(driver, xpaths, label, timeout=4):
    """依序嘗試多組 XPath，回傳第一個成功點到的元素描述；全失敗則回傳 None。
    使用 JS 點擊以繞過頁面遮罩/疊加層阻擋。"""
    wait = WebDriverWait(driver, timeout)
    for xp in xpaths:
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            # 用 JS 點擊避免被遮罩擋住
            driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)
            log(f"      OK：以 XPath JS 點擊 → {xp}")
            return xp
        except TimeoutException:
            log(f"      x  XPath 找不到 → {xp}")
            continue
        except Exception as e:
            log(f"      x  XPath {xp} 點擊例外：{type(e).__name__}: {e}")
            continue
    log(f"[ERROR] {label} 全部 XPath 都失敗。")
    return None


def grab_desktop(path):
    """擷取整個桌面（含 Chrome UI、瀏覽器級對話框）儲存到 path。
    driver.save_screenshot() 只截網頁區域，看不到 Chrome chrome-level 對話框。"""
    try:
        img = ImageGrab.grab()
        img.save(path)
        return True
    except Exception as e:
        log(f"      [警告] 桌面截圖失敗：{e}")
        return False


def click_chrome_allow_button(timeout_wait=2.0):
    """
    點擊 Chrome 站台權限對話框「允許」按鈕。

    對話框錨點為 URL bar 左側下方，按鈕為「淺底 + Chrome 藍邊+文字」風格，
    不是純藍背景；像素偵測難度高，改用實測螢幕座標。
    Chrome 對話框允許後會記在 profile 內，下次同 origin 不再跳。
    """
    # 等對話框完全渲染
    time.sleep(timeout_wait)

    # 點擊前截圖供 debug
    before_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "before_allow_click.png")
    grab_desktop(before_path)
    log(f"      點擊前桌面截圖 → {before_path}")

    # 實測座標：對話框錨在 URL bar 左下，「允許」按鈕中心約在 (322, 197)
    allow_x, allow_y = 322, 197
    try:
        pyautogui.moveTo(allow_x, allow_y, duration=0.2)
        pyautogui.click()
        log(f"      已點擊『允許』於 ({allow_x}, {allow_y})")
    except Exception as e:
        log(f"      pyautogui 點擊失敗：{e}")
        return False

    time.sleep(1.0)
    after_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "after_allow_click.png")
    grab_desktop(after_path)
    log(f"      點擊後桌面截圖 → {after_path}")
    return True


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
    # 清空 log file 開始新的一輪
    try:
        open(LOG_FILE, "w", encoding="utf-8").close()
    except Exception:
        pass

    log(f"[0/5] 使用 Selenium 專用 profile：{PROFILE_DIR}")
    log(f"      路徑：{USER_DATA_DIR}")
    profile_path = os.path.join(USER_DATA_DIR, PROFILE_DIR)
    if not os.path.isdir(profile_path):
        log(f"      首次執行 — Chrome 將自動建立此目錄")
        os.makedirs(USER_DATA_DIR, exist_ok=True)
    else:
        mark_profile_clean_exit()

    options = Options()
    options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
    options.add_argument(f"--profile-directory={PROFILE_DIR}")
    options.add_argument("--start-maximized")
    # 隱藏「Chrome 目前受到自動測試軟體控制」infobar
    options.add_argument("--disable-blink-features=AutomationControlled")
    # 跑完不要關閉瀏覽器，方便人工觀察 / 接手登入
    options.add_experimental_option("detach", True)
    # 抑制 USB / Bluetooth log + 移除 enable-automation 旗標（infobar 來源）
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    log("[1/5] 啟動 Chrome（Selenium Manager 自動下載對應 ChromeDriver）...")
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
    except WebDriverException as e:
        msg = str(e)
        log(f"[FATAL] 無法啟動 Chrome：")
        log(msg[:500])
        if "user data directory is already in use" in msg.lower():
            log("提示：請先關閉佔用此 profile 的 Chrome 視窗再執行。")
        return

    try:
        log(f"[2/5] 開啟 {URL}")
        try:
            driver.get(URL)
        except TimeoutException:
            log("      [警告] 頁面載入超過 15 秒，繼續執行（HiCOS 元件可能仍在背景載入）")

        handles_before = list(driver.window_handles)
        log(f"      啟動後共 {len(handles_before)} 個 window/tab")
        if len(handles_before) > 1:
            for h in handles_before[1:]:
                driver.switch_to.window(h)
                driver.close()
        driver.switch_to.window(handles_before[0])

        try:
            driver.maximize_window()
        except Exception:
            pass

        # 頁面剛開可能立刻跳 Chrome 站台權限對話框（HiCOS 元件需要存取裝置），
        # 此對話框會阻擋部分頁面互動。預先點一次「允許」避免後續 Selenium 命令 timeout。
        log("[Pre] 預先嘗試點擊 Chrome 站台權限對話框（若有）...")
        try:
            click_chrome_allow_button(timeout_wait=2.0)
        except Exception as e:
            log(f"      預先點擊權限對話框例外：{e}")

        log("[3/5] 點選『自然人憑證』分頁...")
        if not try_click(driver, CERT_TAB_XPATHS, "自然人憑證分頁"):
            dump_page_for_debug(driver)
            return
        time.sleep(1.5)

        log("[4/5] 點選『登入』按鈕...")
        if not try_click(driver, LOGIN_BTN_XPATHS, "登入按鈕"):
            dump_page_for_debug(driver)
            return
        time.sleep(2)

        log("[5/5] 自動點擊 Chrome 站台權限對話框「允許」按鈕...")
        try:
            click_chrome_allow_button()
        except Exception as e:
            log(f"      click_chrome_allow_button 例外：{type(e).__name__}: {e}")

        try:
            log(f"      最終 URL：{driver.current_url}")
            log(f"      頁面標題：{driver.title}")
        except Exception as e:
            log(f"      讀取頁面狀態失敗：{e}")
        log("[完成] 瀏覽器保持開啟，請依畫面插入卡片並輸入 PIN。")

    except Exception as e:
        log(f"[ERROR] 流程中斷：{type(e).__name__}: {e}")
        try:
            dump_page_for_debug(driver)
        except Exception:
            pass


if __name__ == "__main__":
    main()
