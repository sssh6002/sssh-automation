"""
taipeion_login_selenium.py
臺北市單一帳號認證平台（TAIPEION）自然人憑證登入 — Selenium 版。

對應 taipeion_login.py（pyautogui 像素點擊版）的替代實作：
  - 用 Selenium WebDriver 操作 DOM，不依賴像素座標
  - 用獨立的 Chrome User Data 目錄（繞 Chrome 136+ 安全限制）
  - 用 pyautogui 點 Chrome 站台權限對話框「允許」按鈕（Chrome 瀏覽器級 UI，
    無法用 Selenium 點，且授權後 Chrome 會記住，下次同 origin 不再跳）
  - 用 JS 點擊（execute_script）繞過頁面遮罩
  - 跑完留下瀏覽器（detach），使用者插卡 + 輸入 PIN 即可完成登入

從其他腳本呼叫：
    from taipeion_login_selenium import login_taipeion_selenium
    ok = login_taipeion_selenium()  # 回傳 True 表示流程到 PIN 輸入畫面
"""

import json
import os
import time

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
USER_DATA_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Chrome-Selenium\User Data")
PROFILE_DIR = "Default"

CERT_TAB_XPATHS = [
    "//a[contains(., '自然人憑證')]",
    "//button[contains(., '自然人憑證')]",
    "//li[contains(., '自然人憑證')]",
    "//*[@role='tab' and contains(., '自然人憑證')]",
]
LOGIN_BTN_XPATHS = [
    "//button[normalize-space()='登入']",
    "//input[@type='submit' and contains(@value, '登入')]",
    "//a[normalize-space()='登入']",
]
PIN_INPUT_XPATHS = [
    "//input[contains(@placeholder, 'PIN')]",
    "//input[@type='password']",
    "//input[@id='pin']",
    "//input[contains(@name, 'pin')]",
]
RECHECK_XPATHS = [
    "//*[normalize-space()='重新檢測']",
    "//*[normalize-space()='重新偵測卡片']",
    "//button[contains(., '重新檢測')]",
    "//button[contains(., '重新偵測卡片')]",
    "//a[contains(., '重新檢測')]",
    "//a[contains(., '重新偵測卡片')]",
]

# PIN 從 id.txt 讀。檔案不應該 commit（已在 .gitignore）。
PIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "id.txt")


def _mark_profile_clean_exit():
    """避免 Chrome 跳「未正確關閉，是否還原網頁」對話框。"""
    prefs_path = os.path.join(USER_DATA_DIR, PROFILE_DIR, "Preferences")
    if not os.path.isfile(prefs_path):
        return
    try:
        with open(prefs_path, "r", encoding="utf-8") as f:
            prefs = json.load(f)
        prefs.setdefault("profile", {}).update({"exit_type": "Normal", "exited_cleanly": True})
        with open(prefs_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except Exception:
        pass


def _reset_crash_streak():
    """重置 Local State 的 variations_crash_streak。Chrome 累積 5+ 次 crash 後會安全模式
    拒絕啟動，每次 Stop-Process 強制殺 Chrome 都會讓這個值 +1。"""
    local_state = os.path.join(USER_DATA_DIR, "Local State")
    if not os.path.isfile(local_state):
        return
    try:
        with open(local_state, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["variations_crash_streak"] = 0
        stab = data.setdefault("user_experience_metrics", {}).setdefault("stability", {})
        for k in ("crash_count", "system_crash_count", "incomplete_session_end_count"):
            stab[k] = 0
        with open(local_state, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _js_click(driver, xpaths, label, timeout=4):
    """用 JS execute_script 點擊第一個符合的 XPath 元素（繞遮罩）。"""
    wait = WebDriverWait(driver, timeout)
    for xp in xpaths:
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)
            print(f"      OK：點到 {label}")
            return True
        except TimeoutException:
            continue
        except Exception as e:
            print(f"      x  {label} 點擊例外：{type(e).__name__}: {e}")
            continue
    print(f"[ERROR] {label} 全部 XPath 都失敗")
    return False


def _wait_for_login_button(driver, max_retries=8, interval=2.0):
    """等讀卡機完成卡片偵測，「登入」按鈕真正可用。
    若頁面顯示「重新檢測 / 重新偵測卡片」（卡片或讀卡機尚未就位），
    依序點擊這些按鈕直到「登入」按鈕出現，最多 max_retries 次。
    回傳是否在重試上限內看到「登入」。"""
    for attempt in range(1, max_retries + 1):
        # 看「登入」是否已出現
        for xp in LOGIN_BTN_XPATHS:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        print(f"      [check {attempt}] 已看到『登入』按鈕")
                        return True
            except Exception:
                pass

        # 沒「登入」→ 試著點「重新檢測 / 重新偵測卡片」
        clicked = False
        for xp in RECHECK_XPATHS:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        print(f"      [retry {attempt}] 點到 {xp}")
                        clicked = True
                        break
                if clicked:
                    break
            except Exception:
                pass

        if not clicked:
            print(f"      [retry {attempt}] 找不到登入或重新檢測按鈕，等 {interval}s")
        time.sleep(interval)

    print(f"[WARN] 重試 {max_retries} 次後仍未看到『登入』按鈕")
    return False


def _read_pin():
    """從 id.txt 讀 PIN，回傳字串；檔案不存在或空白時回傳 None。"""
    if not os.path.isfile(PIN_FILE):
        print(f"      [警告] 找不到 PIN 檔：{PIN_FILE}，需要手動輸入 PIN")
        return None
    try:
        with open(PIN_FILE, "r", encoding="utf-8") as f:
            pin = f.read().strip()
        if not pin:
            print(f"      [警告] PIN 檔內容為空，需要手動輸入 PIN")
            return None
        return pin
    except Exception as e:
        print(f"      [警告] 讀取 PIN 失敗：{e}")
        return None


def _fill_pin(driver, pin, timeout=10):
    """找到 PIN 輸入框並填入。回傳是否成功。"""
    wait = WebDriverWait(driver, timeout)
    for xp in PIN_INPUT_XPATHS:
        try:
            el = wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
            el.click()
            el.clear()
            el.send_keys(pin)
            print(f"      OK：填入 PIN 於 XPath {xp}")
            return True
        except TimeoutException:
            continue
        except Exception as e:
            print(f"      x  PIN 欄位 {xp} 例外：{type(e).__name__}: {e}")
            continue
    print("[ERROR] 找不到 PIN 輸入欄位")
    return False


def _click_chrome_allow_button():
    """點擊 Chrome 站台權限對話框「允許」按鈕（固定螢幕座標）。
    對話框錨點為 URL bar 左下，按鈕中心約在 (322, 197)，實測在不同解析度穩定。
    Chrome 授權後會記在 profile 內，下次同 origin 不再跳，所以這個動作是冪等的。"""
    time.sleep(1.5)
    try:
        pyautogui.moveTo(322, 197, duration=0.2)
        pyautogui.click()
    except Exception as e:
        print(f"      pyautogui 點擊失敗：{e}")


def login_taipeion_selenium():
    """開啟 TAIPEION 並用 Selenium 走完「自然人憑證 → 登入 → 允許對話框」流程。
    回傳 True 表示已到 PIN 輸入畫面，等待使用者插卡 + 輸入 PIN。"""
    print(f"[1/5] 啟動 Chrome（Selenium 專用 profile：{USER_DATA_DIR}）...")
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    _reset_crash_streak()
    if os.path.isdir(os.path.join(USER_DATA_DIR, PROFILE_DIR)):
        _mark_profile_clean_exit()

    options = Options()
    options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
    options.add_argument(f"--profile-directory={PROFILE_DIR}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("detach", True)
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
    except WebDriverException as e:
        print(f"[FATAL] 無法啟動 Chrome：{str(e)[:300]}")
        return False

    print(f"[2/5] 開啟 {URL}")
    try:
        driver.get(URL)
    except TimeoutException:
        print("      [警告] 頁面載入超時，繼續執行")

    try:
        driver.maximize_window()
    except Exception:
        pass

    print("[3/6] 預先點 Chrome 站台權限對話框「允許」按鈕（HiCOS 元件需要存取裝置）...")
    _click_chrome_allow_button()

    print("[4/6] 點選『自然人憑證』分頁...")
    if not _js_click(driver, CERT_TAB_XPATHS, "自然人憑證分頁"):
        return False
    time.sleep(1.5)

    # 切到分頁後 HiCOS 元件可能再要求一次權限，保險再點一次
    _click_chrome_allow_button()

    # 卡片/讀卡機未就位時頁面顯示「重新檢測 / 重新偵測卡片」，需重複點擊直到「登入」出現
    print("[4.5/6] 等讀卡機完成卡片偵測，必要時點重新檢測...")
    _wait_for_login_button(driver)

    print("[5/6] 自動填入 PIN（從 id.txt 讀）...")
    pin = _read_pin()
    if pin:
        if not _fill_pin(driver, pin):
            print("      [警告] PIN 自動填入失敗，請手動輸入後再按登入")
    else:
        print("      無 PIN 可填，請手動輸入後再按登入")

    print("[6/6] 點選『登入』按鈕送出...")
    if not _js_click(driver, LOGIN_BTN_XPATHS, "登入按鈕"):
        return False

    # 等系統處理登入後再記錄最終狀態（包含可能的跳轉）
    time.sleep(5)
    final_state = os.path.join(os.path.dirname(os.path.abspath(__file__)), "final_state.png")
    try:
        driver.save_screenshot(final_state)
        print(f"[final] Selenium 截圖 → {final_state}")
    except Exception as e:
        print(f"[final] 截圖失敗：{e}")
    try:
        print(f"[final] URL：{driver.current_url}")
        print(f"[final] 標題：{driver.title}")
    except Exception as e:
        print(f"[final] 讀狀態失敗：{e}")

    print("[完成] 已送出登入請求，請依畫面完成自然人憑證驗證（插卡 / 卡片 PIN 對話框）。")
    return True


if __name__ == "__main__":
    login_taipeion_selenium()
