"""
taipeion_login_selenium.py
臺北市單一帳號認證平台（TAIPEION）自然人憑證登入 — Selenium 版。

對應 taipeion_login.py（pyautogui 像素點擊版）的替代實作：
  - 用 Selenium WebDriver 操作 DOM，不依賴像素座標
  - 用獨立的 Chrome User Data 目錄（繞 Chrome 136+ 安全限制）
  - 用 pyautogui 點 Chrome 站台權限對話框「允許」按鈕（Chrome 瀏覽器級 UI，
    無法用 Selenium 點，且授權後 Chrome 會記住，下次同 origin 不再跳）
  - 用 JS 點擊（execute_script）繞過頁面遮罩
  - 自動讀 id.txt 填 PIN 並送出登入；卡片偵測失敗時自動點「重新檢測」重試

注意：本流程必須在「螢幕未鎖定」狀態下執行 — Windows 鎖屏會阻擋
Smart Card API，HiCOS 無法讀卡。

從其他腳本呼叫：
    from taipeion_login_selenium import login_taipeion_selenium
    ok = login_taipeion_selenium()                       # 回傳 True 表示流程跑完
    driver = login_taipeion_selenium(return_driver=True) # 回傳 driver（失敗回 None），方便串接後續動作
"""

import json
import os
import time

import pyautogui
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
# 卡片偵測失敗時頁面會出現的兩個獨立按鈕（兩個都要點）
RECHECK_BUTTONS = ["重新檢測", "重新偵測卡片"]

# PIN 從 id.txt 讀。檔案不應該 commit（已在 .gitignore）。
PIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "id.txt")


def _mark_profile_clean_exit():
    """徹底壓掉「Chrome 未正確關閉，要還原網頁嗎？」對話框 — 雙管齊下：
       1) Preferences 改 exit_type=Normal、exited_cleanly=True，並把 session 還原
          模式設為「開新分頁」（restore_on_startup=5），避免不小心還原。
       2) 刪掉 session 殘留檔（Last/Current Session、Last/Current Tabs、Sessions/
          整個目錄）。這 profile 是 Selenium 專用，不必保留分頁。
       Chrome 啟動旗標 --disable-session-crashed-bubble / --hide-crash-restore-bubble
       是第三層保險，在 login_taipeion_selenium() 內設定。"""
    profile_path = os.path.join(USER_DATA_DIR, PROFILE_DIR)
    prefs_path = os.path.join(profile_path, "Preferences")
    if os.path.isfile(prefs_path):
        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            prefs.setdefault("profile", {}).update({
                "exit_type": "Normal",
                "exited_cleanly": True,
            })
            # restore_on_startup=5 → 開新分頁；4=指定 URL；1=最後分頁。設 5 最保險
            prefs.setdefault("session", {})["restore_on_startup"] = 5
            with open(prefs_path, "w", encoding="utf-8") as f:
                json.dump(prefs, f)
        except Exception:
            pass

    # 刪 profile 根目錄下的 session 殘留檔
    for filename in ("Last Session", "Last Tabs", "Current Session", "Current Tabs"):
        p = os.path.join(profile_path, filename)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass

    # 清 Sessions/ 子目錄（裡面是 SNSS 二進位 session 快照）
    sessions_dir = os.path.join(profile_path, "Sessions")
    if os.path.isdir(sessions_dir):
        for name in os.listdir(sessions_dir):
            try:
                os.remove(os.path.join(sessions_dir, name))
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


def _wait_for_login_button(driver, max_retries=2, interval=3.0):
    """若頁面顯示「重新檢測」/「重新偵測卡片」（卡片偵測未完成），
    依序點兩個按鈕（用 Selenium 原生 click），等 HiCOS 完成讀卡後「登入」會出現。
    每輪 interval 秒，最多 max_retries 輪；HiCOS 一般需要約 3-5 秒完成偵測。"""
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

        # 沒「登入」→ 把兩個重試按鈕都點一次（用 Selenium 原生 click，不用 JS 合成）
        clicked_any = False
        for label in RECHECK_BUTTONS:
            xp = f"//*[normalize-space()='{label}']"
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        try:
                            el.click()
                            print(f"      [retry {attempt}] 點到「{label}」（原生 click）")
                            clicked_any = True
                            break
                        except Exception:
                            driver.execute_script("arguments[0].click();", el)
                            print(f"      [retry {attempt}] 點到「{label}」（JS fallback）")
                            clicked_any = True
                            break
            except Exception:
                pass

        if not clicked_any:
            print(f"      [retry {attempt}] 找不到登入或重試按鈕，等 {interval}s")
        time.sleep(interval)

    print(f"[WARN] 重試 {max_retries} 輪後仍未看到『登入』按鈕")
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


def _fill_pin(driver, pin, timeout=3):
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


def _get_selenium_chrome_pids(driver):
    """走 chromedriver 的 process tree，回傳所有 chrome.exe descendant PID 集合。

    用途：_bring_chrome_to_foreground 要把 Selenium 控制的 Chrome 拉到前景，不能
    誤抓 VSCode（Electron app，class name 同樣是 Chrome_WidgetWin_1）或使用者
    個人 Chrome（同 exe 名 chrome.exe，但不在 chromedriver process tree 內）。
    用 Toolhelp32 走 chromedriver → chrome.exe (browser) → chrome.exe (renderer)
    的 process tree，回傳所有 chrome.exe 的 PID。BFS 走完整 tree，因為 chromedriver
    對 chrome.exe browser process 是直接子，renderer/GPU 是孫。
    無法取得時回傳空集合（呼叫端會 fallback 到 exe 名 chrome.exe 過濾）。
    """
    if driver is None:
        return set()
    try:
        chromedriver_pid = driver.service.process.pid
    except Exception:
        return set()

    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == wintypes.HANDLE(-1).value:
            return set()
        try:
            parent_to_children = {}
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                return set()
            while True:
                parent_to_children.setdefault(entry.th32ParentProcessID, []).append(
                    (entry.th32ProcessID, entry.szExeFile.lower())
                )
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break

            pids = set()
            queue = [chromedriver_pid]
            seen = {chromedriver_pid}
            while queue:
                pid = queue.pop()
                for child_pid, child_exe in parent_to_children.get(pid, []):
                    if child_pid in seen:
                        continue
                    seen.add(child_pid)
                    if child_exe == "chrome.exe":
                        pids.add(child_pid)
                    queue.append(child_pid)
            return pids
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        return set()


def _bring_chrome_to_foreground(driver=None):
    """把 Selenium 控制的 Chrome 視窗拉到最上層，否則 pyautogui 點到的可能是
    VSCode/個人 Chrome/其他視窗。

    用 EnumWindows + class name 'Chrome_WidgetWin_1' 找 Chrome，再用
    SetForegroundWindow 拉到前面。為繞過 Windows 對 SetForegroundWindow 的「只有
    前景程序能設定前景」限制，先 keyDown/keyUp 一次 Alt 鍵讓本程序短暫取得前景權限。

    重要：**單純比對 class name 不夠**。`Chrome_WidgetWin_1` 是所有 Chromium-based
    應用共用 — 包含 VSCode 等 Electron app 以及使用者個人 Chrome。若 driver 已傳入，
    用 _get_selenium_chrome_pids 取得 Selenium Chrome 的 PID 集合，
    GetWindowThreadProcessId 過濾才能保證抓到正確視窗。
    Fallback：driver=None 或 PID 取不到時，至少用 QueryFullProcessImageNameW 過濾
    exe 必須是 chrome.exe，避免抓到 VSCode（Code.exe）。

    重要：**不能無條件呼叫 ShowWindow(SW_RESTORE)**——SW_RESTORE 在已最大化視窗
    上會把它縮成普通大小！只有 IsIconic(hwnd)=true（最小化）時才用 SW_SHOWMAXIMIZED
    還原並最大化；非最小化狀態完全不動視窗，只 SetForegroundWindow，保留原本最大化。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        chrome_hwnd = [0]
        selenium_pids = _get_selenium_chrome_pids(driver)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        def _exe_name(pid):
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return None
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return buf.value.rsplit("\\", 1)[-1].lower()
            finally:
                kernel32.CloseHandle(h)
            return None

        def _cb(hwnd, _):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value != "Chrome_WidgetWin_1" or not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            # 有 selenium_pids 時嚴格比對；沒有時 fallback 用 exe 名過濾 VSCode/Electron
            if selenium_pids:
                if pid.value not in selenium_pids:
                    return True
            else:
                if _exe_name(pid.value) != "chrome.exe":
                    return True
            chrome_hwnd[0] = hwnd
            return False

        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        if not chrome_hwnd[0]:
            print("      [WARN] 找不到 Selenium Chrome 視窗，pyautogui 點擊可能落在別處")
            return False

        # SetForegroundWindow 在非前景程序呼叫會失敗，先按 Alt 取得權限
        pyautogui.keyDown("alt")
        pyautogui.keyUp("alt")
        # 只在最小化時還原為最大化；其他狀態（普通/已最大化）保持不動，避免把
        # 最大化視窗縮成普通大小（SW_RESTORE=9 在最大化視窗上會「還原」成原始尺寸）
        if user32.IsIconic(chrome_hwnd[0]):
            user32.ShowWindow(chrome_hwnd[0], 3)  # SW_SHOWMAXIMIZED
        user32.SetForegroundWindow(chrome_hwnd[0])
        time.sleep(0.3)
        return True
    except Exception as e:
        print(f"      [WARN] 拉 Chrome 到前景失敗：{e}")
        return False


def _click_chrome_allow_button(driver=None):
    """點擊 Chrome 站台權限對話框「允許」按鈕（固定螢幕座標 (322, 197)）。
    對話框錨點為 URL bar 左下；Chrome 授權後記在 profile 內，下次同 origin 不再跳。

    參數：
        driver: Selenium WebDriver，用於辨識正確的 Selenium Chrome PID。若為 None
                則 fallback 用 exe 名過濾（避免抓到 VSCode），但可能會抓到使用者
                個人 Chrome 視窗。建議盡量傳入。

    流程：
    1. 把 Selenium Chrome 拉到最上層（_bring_chrome_to_foreground）。**單純比對
       class `Chrome_WidgetWin_1` 不夠** — VSCode/Slack/Electron app 同樣是這個
       class，會誤抓導致 VSCode 蓋過公文網頁。傳 driver 進去過濾 PID 才正確。
    2. **先檢查 (322, 197) 像素是否為允許按鈕的藍底色再決定要不要點**。授權後
       對話框不再跳，那個座標當下可能是分頁列/網頁內容/工具列，盲點會亂點到其他
       東西。Chrome 允許按鈕底色約 #1A73E8，用 r<80 且 80<g<180 且 b>200 判斷藍色。
    """
    _bring_chrome_to_foreground(driver)
    time.sleep(0.5)
    try:
        r, g, b = pyautogui.pixel(322, 197)
        is_blue_allow_button = (r < 80 and 80 < g < 180 and b > 200)
        if not is_blue_allow_button:
            print(f"      (322,197) RGB=({r},{g},{b}) 非允許按鈕藍底色，無對話框，跳過點擊（避免亂點）")
            return
        pyautogui.moveTo(322, 197, duration=0.1)
        pyautogui.click()
        print("      OK：已點允許")
    except Exception as e:
        print(f"      pyautogui 點擊失敗：{e}")


def login_taipeion_selenium(return_driver=False):
    """開啟 TAIPEION 並用 Selenium 走完「自然人憑證 → 登入 → 允許對話框」流程。

    參數：
        return_driver: 預設 False，回傳 bool（True 表示流程跑完）。
                       傳 True 則回傳 driver 物件（失敗回 None），方便呼叫端串接後續動作。
    """
    print(f"[1/6] 啟動 Chrome（Selenium 專用 profile：{USER_DATA_DIR}）...")
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    _reset_crash_streak()
    if os.path.isdir(os.path.join(USER_DATA_DIR, PROFILE_DIR)):
        _mark_profile_clean_exit()

    options = Options()
    # page_load_strategy='eager'：等 DOMContentLoaded 就 return，不等 onload。
    # 必要：點公文方塊後 edoc 新分頁會跳 Chrome 站台權限對話框，會讓頁面 onload
    # 永遠不觸發；預設 strategy='normal' 下任何 driver.xxx 指令（window_handles、
    # current_url 等）都會卡在 pending pageload 等到 onload 才返回，整個 driver
    # 鎖死。eager 只等 DOMContentLoaded，不受對話框影響。
    options.page_load_strategy = "eager"
    options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
    options.add_argument(f"--profile-directory={PROFILE_DIR}")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # 壓掉 taskkill 後啟動 Chrome 會跳的「未正確關閉 / 是否還原網頁」泡泡 + 對話框
    options.add_argument("--disable-session-crashed-bubble")
    options.add_argument("--hide-crash-restore-bubble")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--restore-last-session=false")
    options.add_experimental_option("detach", True)
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    # 自動接受所有 JS dialog（alert/confirm/prompt）— 公文系統點方塊可能跳 JS confirm，
    # 沒處理會讓 Selenium 永遠卡在 unhandled prompt 狀態，後續 driver.xxx 全部 hang。
    options.set_capability("unhandledPromptBehavior", "accept")

    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
    except WebDriverException as e:
        print(f"[FATAL] 無法啟動 Chrome：{str(e)[:300]}")
        return None if return_driver else False

    print(f"[2/6] 開啟 {URL}")
    try:
        driver.get(URL)
    except TimeoutException:
        print("      [警告] 頁面載入超時，繼續執行")

    try:
        driver.maximize_window()
    except Exception:
        pass

    print("[3/6] 預先點 Chrome 站台權限對話框「允許」按鈕（HiCOS 元件需要存取裝置）...")
    _click_chrome_allow_button(driver)

    print("[4/6] 點選『自然人憑證』分頁...")
    if not _js_click(driver, CERT_TAB_XPATHS, "自然人憑證分頁"):
        return None if return_driver else False
    time.sleep(0.3)

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
        return None if return_driver else False

    # 等系統處理登入 + 跳轉（HiCOS 簽章 + server redirect 實測需 4-5 秒）
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

    print("[完成] 登入流程結束，已跳轉至 TAIPEION 入口網（或仍在最後驗證中）。")
    return driver if return_driver else True


if __name__ == "__main__":
    login_taipeion_selenium()
