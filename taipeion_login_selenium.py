"""
taipeion_login_selenium.py
臺北市單一帳號認證平台（TAIPEION）自然人憑證登入 — Selenium 版。

對應 taipeion_login.py（pyautogui 像素點擊版）的替代實作：
  - 用 Selenium WebDriver 操作 DOM，不依賴像素座標
  - 用獨立的 Chrome User Data 目錄（繞 Chrome 136+ 安全限制）
  - 用 pyautogui 點 Chrome 站台權限對話框「允許」按鈕（Chrome 瀏覽器級 UI，
    無法用 Selenium 點，且授權後 Chrome 會記住，下次同 origin 不再跳）
  - 用 JS 點擊（execute_script）繞過頁面遮罩
  - 自動讀 env.env 填 PIN 並送出登入；卡片偵測失敗時自動點「重新檢測」重試

注意：本流程必須在「螢幕未鎖定」狀態下執行 — Windows 鎖屏會阻擋
Smart Card API，HiCOS 無法讀卡。

從其他腳本呼叫：
    from taipeion_login_selenium import login_taipeion_selenium
    ok = login_taipeion_selenium()                       # 回傳 True 表示流程跑完
    driver = login_taipeion_selenium(return_driver=True) # 回傳 driver（失敗回 None），方便串接後續動作
"""

import json
import os
import subprocess
import time
from datetime import datetime

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

# 帳密設定從 env.env 讀（key=value 一行一筆、# 開頭為註解）。檔案不應該 commit（已在 .gitignore）。
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.env")


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


_LOG_FILE_HANDLE = None  # 模組層保留 file handle 避免 GC 提早關閉

# 全域 LOG 規則（C:\Users\ldc\.claude\CLAUDE.md）：
#  - 每行 LOG 開頭含 ISO 8601 時間戳：YYYY-MM-DDThh:mm:ss
#  - 檔案 >10MB 時 rotate，最多保留 7 份（current + .1~.6）
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_KEEP = 6  # 加上當前那份共 7 份


def _rotate_log_if_needed(log_path):
    """若 log_path 大於 _LOG_MAX_BYTES，做檔名滾動：

        log_path.6 → 刪除
        log_path.5 → log_path.6
        ...
        log_path.1 → log_path.2
        log_path   → log_path.1

    任何 IO 失敗都 silently skip — log rotate 不該影響主流程。
    """
    if not os.path.exists(log_path):
        return
    try:
        if os.path.getsize(log_path) <= _LOG_MAX_BYTES:
            return
    except OSError:
        return
    oldest = f"{log_path}.{_LOG_KEEP}"
    if os.path.exists(oldest):
        try:
            os.remove(oldest)
        except OSError:
            pass
    for i in range(_LOG_KEEP - 1, 0, -1):
        src = f"{log_path}.{i}"
        if os.path.exists(src):
            try:
                os.replace(src, f"{log_path}.{i + 1}")
            except OSError:
                pass
    try:
        os.replace(log_path, f"{log_path}.1")
    except OSError:
        pass


def _setup_stdout_logging(filename="run.log"):
    """把 stdout/stderr 同時印到螢幕與 <filename>（與本檔同目錄）。每次跑覆寫舊內容。

    用途：Python print 輸出原本只在終端機 scrollback，關視窗就消失，無法事後讀取
    來除錯。包一層 Tee 落地後，下次出問題使用者只要說「跑過了你去看 run.log」我
    就能用 Read tool 直接讀，不用手動 Pipe。

    寫進 file 時每行開頭會自動加上 ISO 8601 timestamp（螢幕輸出維持原樣不加，
    避免破壞終端機的可讀性）。開檔前若舊檔 >10MB 會先 rotate（保留 .1~.6）。

    呼叫時機：在 entry point script (main.py / document_system.py standalone) 啟動
    最開頭呼叫一次。底層用 module-level _LOG_FILE_HANDLE 保留 file 引用避免 GC，
    Python 程序退出時自動 close。

    限制：sys.stdout.reconfigure(...) 在 wrap 後會作用在原 stream 上，建議在
    呼叫本函式前先 reconfigure。
    """
    global _LOG_FILE_HANDLE
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    _rotate_log_if_needed(log_path)
    _LOG_FILE_HANDLE = open(log_path, "w", encoding="utf-8")

    class _Tee:
        """Tee：螢幕原樣印；檔案逐行前綴 ISO 8601 timestamp。

        _at_line_start 追蹤「下一個寫進 file 的字元是否在行首」。print() 通常會
        把訊息和 '\\n' 拆成兩次 write，所以要把 data 依 '\\n' 切段、為每段「位於
        行首且非空」的部分補上 timestamp，才能避免在續行中間誤插時戳。
        """

        def __init__(self, original, file_handle):
            self._original = original
            self._file = file_handle
            self._at_line_start = True

        def write(self, data):
            try:
                self._original.write(data)
            except Exception:
                pass
            try:
                if not data:
                    return
                ts = datetime.now().isoformat(timespec="seconds")
                segments = data.split("\n")
                out = []
                for i, seg in enumerate(segments):
                    is_last = i == len(segments) - 1
                    if self._at_line_start and seg:
                        out.append(f"{ts} {seg}")
                        self._at_line_start = False
                    else:
                        out.append(seg)
                    if not is_last:
                        out.append("\n")
                        self._at_line_start = True
                self._file.write("".join(out))
                self._file.flush()
            except Exception:
                pass

        def flush(self):
            for s in (self._original, self._file):
                try:
                    s.flush()
                except Exception:
                    pass

        def __getattr__(self, name):
            # 其他屬性（encoding / fileno / reconfigure 等）forward 到原 stream
            return getattr(self._original, name)

    import sys as _sys
    _sys.stdout = _Tee(_sys.stdout, _LOG_FILE_HANDLE)
    _sys.stderr = _Tee(_sys.stderr, _LOG_FILE_HANDLE)
    print(f"[logging] stdout/stderr 同步落地到 {log_path}")


def _close_selenium_chrome_only():
    """只關閉 Selenium 相關的 chrome.exe + chromedriver.exe，不動使用者個人 Chrome。

    委派給 scripts/close-profile2-chrome.ps1：該腳本用 Get-CimInstance 過濾 command line，
    只殺帶 --user-data-dir=*Chrome-Selenium*、--remote-debugging-port 或 --test-type=webdriver
    的程序，並先 CloseMainWindow 優雅關閉再強制終止，順帶清 profile lockfile。
    這樣個人 Chrome 不會被強殺，下次手動打開不會跳「未正確關閉，要還原網頁嗎？」對話框。
    """
    script_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "scripts", "close-profile2-chrome.ps1",
    )
    if not os.path.isfile(script_path):
        print(f"[WARN] 找不到 {script_path}，跳過 Chrome 預清理")
        return
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
            capture_output=True, timeout=30,
        )
    except Exception as e:
        print(f"[WARN] Chrome 預清理失敗：{e}")


def _build_chrome_options():
    """建構 Selenium Chrome 的 Options。

    所有 entry-point（main.py 走的 login 流程、document_system.py standalone 流程）
    共用同一份 options，避免兩邊飄移後出現「main.py OK 但 document_system 連 PNA 都
    沒關」這種詭異 bug。

    各旗標的詳細理由見對應的 inline 註解。
    """
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
    # 開 remote debugging port,讓 fill_in_draft.py(4-2)standalone 能 attach 既有
    # Chrome session、直接接手停在公文閱覽器分頁的 driver,不必重跑整個 cascade。
    options.add_argument("--remote-debugging-port=9222")
    # 關閉 Chrome Local Network Access (LNA) + Private Network Access (PNA) 阻擋：
    # edoc.gov.taipei (公開來源) 會 fetch 兩個本地簽章元件：
    #   - https://127.0.0.1:56420/56520/56620 (TCGServiSign by Changingtec)
    #   - http://127.0.0.1:16888 (公文系統 KdApp javaw 本地元件，HTTP not HTTPS)
    # Chrome 142+ 把 LNA 預設啟用，console 印
    #   "blocked by CORS policy: Permission was denied for this request to access
    #    the `loopback` address space"
    # — 這是 LNA 訊息（不是 PNA）。LNA 比 PNA 嚴 — 完全不准 fetch loopback 除非
    # profile 授權過。個人 Chrome Profile 2 早授權，Selenium 全新 profile 沒。
    # 同時關掉 PNA 三個 feature 當保險；只影響 fetch loopback/private network，
    # 不動一般網頁安全性。
    options.add_argument(
        "--disable-features=LocalNetworkAccessChecks,"
        "BlockInsecurePrivateNetworkRequests,"
        "PrivateNetworkAccessRespectPreflightResults,"
        "PrivateNetworkAccessSendPreflights"
    )
    # 允許 HTTPS 頁面 fetch http://127.0.0.1:16888 (公文 KdApp 元件是 HTTP)；
    # 不加會被 mixed content 擋（已在 console log 看到 Mixed Content 警告）。
    options.add_argument("--allow-running-insecure-content")
    options.add_experimental_option("detach", True)
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    # 剪貼簿權限預設「允許」(content_settings 層)，避免公文閱覽器分頁載入時跳
    # 「請取已複製到剪貼簿的文字和圖片」對話框。1=allow, 2=block, 0=ask（預設）。
    # CDP grant (_grant_clipboard_permission) 是 PermissionContext 層的雙保險。
    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.clipboard": 1,
    })
    # 自動接受所有 JS dialog（alert/confirm/prompt）— 公文系統點方塊可能跳 JS confirm，
    # 沒處理會讓 Selenium 永遠卡在 unhandled prompt 狀態，後續 driver.xxx 全部 hang。
    options.set_capability("unhandledPromptBehavior", "accept")
    # 開啟瀏覽器 console / 網路日誌，下次出簽章元件、CORS、PNA 問題可直接看 log
    options.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    return options


def _grant_clipboard_permission(driver):
    """用 CDP 給所有 origin grant clipboard 讀寫權限，避免分頁載入時跳「請取
    已複製到剪貼簿的文字和圖片」對話框。

    edoc 公文閱覽器（公文簽核 v1.0.344）開啟單筆公文後會 call navigator.clipboard，
    Chrome 預設要使用者點允許。CDP Browser.grantPermissions 直接寫到 Permission
    Context、繞過對話框，**本 driver session 內**所有 origin 都生效（不指定
    origin 即全 origin）。每次新啟動 Chrome 都要重新 grant — CDP 狀態 per driver。

    Chrome 啟動 flag (`--disable-features=...`) 對這類「需 user gesture」的權限
    沒效（Permissions API 早就用 PermissionContext 管，不看 flag），所以 CDP 是
    唯一無 UI 互動的解法。
    """
    try:
        driver.execute_cdp_cmd("Browser.grantPermissions", {
            "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]
        })
        print("      OK：已 grant clipboard 權限（CDP，所有 origin）")
    except Exception as e:
        print(f"      [WARN] CDP grant clipboard 失敗：{type(e).__name__}: {e}")


def _read_config(key):
    """從 env.env 讀指定 key 的值（key=value 一行一筆，# 開頭整行為註解、空行略過）。

    回傳值字串（strip 過）；檔案不存在、找不到 key 或讀檔失敗時回傳 None。
    """
    if not os.path.isfile(ENV_FILE):
        return None
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip()
    except Exception as e:
        print(f"      [警告] 讀取 env.env 失敗：{e}")
    return None


def _read_pin():
    """從 env.env 讀 PIN（pin=...）；回傳字串，找不到/空時回傳 None。

    向後相容舊格式：若 env.env 是「整檔即 PIN」（沒有任何 key=value），
    則把整檔內容當 PIN。
    """
    if not os.path.isfile(ENV_FILE):
        print(f"      [警告] 找不到設定檔：{ENV_FILE}，需要手動輸入 PIN")
        return None
    pin = _read_config("pin")
    if pin:
        return pin
    # 向後相容：舊檔為「整檔即 PIN」（無 key=value、非註解）
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if raw and "=" not in raw and not raw.startswith("#"):
            return raw
    except Exception as e:
        print(f"      [警告] 讀取 PIN 失敗：{e}")
        return None
    print(f"      [警告] env.env 內無 pin=，需要手動輸入 PIN")
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
        driver: Selenium WebDriver,用於辨識正確的 Selenium Chrome PID。若為 None
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

    options = _build_chrome_options()

    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
    except WebDriverException as e:
        print(f"[FATAL] 無法啟動 Chrome：{str(e)[:300]}")
        return None if return_driver else False

    _grant_clipboard_permission(driver)

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

    print("[5/6] 自動填入 PIN（從 env.env 讀）...")
    pin = _read_pin()
    if pin:
        if not _fill_pin(driver, pin):
            print("      [警告] PIN 自動填入失敗，請手動輸入後再按登入")
    else:
        print("      無 PIN 可填，請手動輸入後再按登入")

    print("[6/6] 點選『登入』按鈕送出...")
    if not _js_click(driver, LOGIN_BTN_XPATHS, "登入按鈕"):
        return None if return_driver else False

    # 等系統處理登入 + 跳轉。原本 sleep(5) 是 worst-case 固定保守等，改成「URL 變化
    # + readyState complete」條件等待，常見情境秒進、server 慢時 fallback 等到 6s。
    deadline = time.time() + 6
    start_url = None
    try:
        start_url = driver.current_url
    except Exception:
        pass
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            if start_url is not None and driver.current_url != start_url:
                # URL 已變 → 再等 readyState complete (最多 2s)
                for _ in range(20):
                    try:
                        if driver.execute_script("return document.readyState") == "complete":
                            break
                    except Exception:
                        pass
                    time.sleep(0.1)
                break
        except Exception:
            pass
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
