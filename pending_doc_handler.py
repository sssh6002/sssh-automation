"""
pending_doc_handler.py
承辦中公文「點完最上方公文 + 新視窗開啟後」的處理。

職責切割：
- document_system.pending_doc：負責「切到承辦中 frame + 點最上方公文 link」，
  公文閱覽器分頁開啟後即 hand off 給本模組
- 本模組 handle_opened_document(driver)：切到新分頁、後續流程（檢視內容、
  簽辦、送件、結案等）。目前已實作：點公文閱覽器 toolbar 的「下載」按鈕、
  把下載檔存到 document_download/、若是 zip 則解到以檔名為路徑的子目錄。

呼叫方式：
1) 從 document_system.pending_doc 串接（main.py 主流程會走到）
2) 單獨執行階段測試：
     C:\\Python314\\python.exe pending_doc_handler.py
   會跑 document_system standalone 同樣的路徑（開 edoc → cascade →
   pending_doc）,跑完後 chain 自動觸發本模組。session 過期就提示
   去跑 main.py 重登。
"""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
import zipfile

from selenium.webdriver.common.by import By

sys.stdout.reconfigure(encoding='utf-8')

# Win32 API:用來找/操作公文系統 KdApp javaw 開的「匯出公文資料」對話框。
# 公文閱覽器 packageBtn 點下去後,真正下載 zip 的是 http://127.0.0.1:16888 的 KdApp
# 本地元件 (Java/javaw),它透過 JNI 開了一個 Java Swing JFileChooser。Selenium 跟
# Chrome 完全管不到這個對話框 — 它是另一個 process 的視窗。需要靠 Win32 / 鍵盤
# 模擬來填路徑 + 按儲存。
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 下載目標資料夾 — 用絕對路徑避免 CDP setDownloadBehavior 失敗（CDP 要求絕對路徑）。
# 與 README/CLAUDE.md 講的位置一致：~\Documents\GitHub\sssh-automation\document_download
DOWNLOAD_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "document_download"))

# 公文閱覽器 toolbar「下載」按鈕的 XPath 候選。
# 實測 2026-05-20 dump:公文閱覽器是 ExtJS + Font Awesome 圖標 — toolbar 上每個圖
# 是 <div class="x-button"> 外殼包 <span class="x-button-icon fa-XXX">。
# 紅框那個「下載」(向下箭頭到托盤) 是 fa-download icon,外殼 div id="packageBtn"。
# 注意:fa-cloud-download 是另一個圖(中段那個雲端下載),要排除。click handler
# 綁在外殼 div (ExtJS 慣例),所以優先點 #packageBtn 而非內層 span。
DOWNLOAD_BUTTON_XPATHS = [
    "//div[@id='packageBtn']",
    "//*[contains(@class, 'fa-download') "
    "and not(contains(@class, 'fa-cloud-download'))]"
    "/ancestor::div[contains(@class, 'x-button')][1]",
    "//*[contains(@class, 'fa-download') "
    "and not(contains(@class, 'fa-cloud-download'))]",
    # 保留泛用 fallback,系統換版改 id/class 仍可命中
    "//*[@title='下載']",
    "//*[@alt='下載']",
    "//*[@aria-label='下載']",
    "//img[contains(@src, 'download') or contains(@src, 'Download')]"
    "/ancestor::*[self::a or self::button or @role='button'][1]",
    "//*[normalize-space()='下載' and (self::a or self::button or @role='button')]",
]


# ─────────────────────────────────────────────────────────────────────────────
# Win32 鍵盤/視窗操作 — 處理 KdApp 開的「匯出公文資料」JFileChooser 對話框
# ─────────────────────────────────────────────────────────────────────────────

_EXPORT_DIALOG_TITLE = "匯出公文資料"

# SendInput 結構定義 (參考 MSDN INPUT struct)
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_VK_RETURN = 0x0D
_VK_CONTROL = 0x11
_VK_A = 0x41
_VK_BACK = 0x08
_VK_DELETE = 0x2E
_VK_SHIFT = 0x10
_VK_MENU = 0x12  # Alt


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG), ("dy", wt.LONG),
        ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUT_UNION)]


def _send_inputs(inputs):
    """呼叫 SendInput 注入一連串 input 事件。"""
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    sent = _user32.SendInput(n, arr, ctypes.sizeof(_INPUT))
    if sent != n:
        err = ctypes.get_last_error()
        print(f"      [WARN] SendInput 只送了 {sent}/{n}, GetLastError={err}")


def _key_event(vk=0, scan=0, flags=0):
    """建立一個 KEYBDINPUT 事件。vk 為 Virtual-Key Code、scan 為 unicode scan code。"""
    inp = _INPUT()
    inp.type = _INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(vk, scan, flags, 0, None)
    return inp


def _send_key(vk):
    """送 vk 的 down + up。"""
    _send_inputs([_key_event(vk=vk), _key_event(vk=vk, flags=_KEYEVENTF_KEYUP)])


def _send_ctrl_combo(vk):
    """送 Ctrl+<vk>。"""
    _send_inputs([
        _key_event(vk=_VK_CONTROL),
        _key_event(vk=vk),
        _key_event(vk=vk, flags=_KEYEVENTF_KEYUP),
        _key_event(vk=_VK_CONTROL, flags=_KEYEVENTF_KEYUP),
    ])


def _send_text_vk(text, per_char_delay=0.02):
    """用 VkKeyScan 把每個字元拆成 vk + shift state,模擬實體鍵盤按鍵。

    為何不用 KEYEVENTF_UNICODE:Java AWT (JFileChooser 是 Swing 包 AWT) 對 SendInput
    的 unicode 事件不正確處理 — 它把 wScan 當實體鍵 scan code 解讀,結果是「小寫
    被打成大寫、底線之類符號錯亂、輸入中段失敗」。實測 2026-05-20:對話框被填成
    「C:\\USERS\\LDC\\DOCUM\\GITHU\\SSSH-AUTOM\\DOCUM_」(全大寫 + 截斷)。

    VkKeyScan 對 ASCII / 西文符號 OK。中文、Emoji 等 IME 字元需要走別的路 (這個
    路徑只用英文,夠用)。
    """
    for ch in text:
        result = _user32.VkKeyScanW(ord(ch))
        if result == -1 or result == 0xFFFF:
            print(f"      [WARN] VkKeyScan 不認識字元 {ch!r} (code 0x{ord(ch):X}),跳過")
            continue
        # LOWORD 是 vk,HIWORD 是 shift state (bit0 shift, bit1 ctrl, bit2 alt)
        vk = result & 0xFF
        shift_state = (result >> 8) & 0xFF
        events = []
        if shift_state & 1:
            events.append(_key_event(vk=_VK_SHIFT))
        if shift_state & 2:
            events.append(_key_event(vk=_VK_CONTROL))
        if shift_state & 4:
            events.append(_key_event(vk=_VK_MENU))
        events.append(_key_event(vk=vk))
        events.append(_key_event(vk=vk, flags=_KEYEVENTF_KEYUP))
        if shift_state & 4:
            events.append(_key_event(vk=_VK_MENU, flags=_KEYEVENTF_KEYUP))
        if shift_state & 2:
            events.append(_key_event(vk=_VK_CONTROL, flags=_KEYEVENTF_KEYUP))
        if shift_state & 1:
            events.append(_key_event(vk=_VK_SHIFT, flags=_KEYEVENTF_KEYUP))
        _send_inputs(events)
        if per_char_delay > 0:
            time.sleep(per_char_delay)


def _find_dialog_hwnd(title_contains, timeout=15):
    """EnumWindows 找標題含 title_contains 的可見 top-level window。回 hwnd 或 None。"""
    deadline = time.time() + timeout
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HWND, wt.LPARAM)

    while time.time() < deadline:
        found = []

        def _cb(hwnd, _lparam):
            if not _user32.IsWindowVisible(hwnd):
                return True
            length = _user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if title_contains in title:
                found.append(hwnd)
                return False  # 找到就停
            return True

        _user32.EnumWindows(EnumWindowsProc(_cb), 0)
        if found:
            return found[0]
        time.sleep(0.3)
    return None


def _bring_to_front(hwnd):
    """把 hwnd 拉到前景。Windows 對 SetForegroundWindow 有限制,先用 AttachThreadInput
    trick 提高成功率。"""
    try:
        cur_tid = _kernel32.GetCurrentThreadId()
        target_tid = _user32.GetWindowThreadProcessId(hwnd, None)
        if cur_tid != target_tid:
            _user32.AttachThreadInput(cur_tid, target_tid, True)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE = 9
        if cur_tid != target_tid:
            _user32.AttachThreadInput(cur_tid, target_tid, False)
    except Exception as e:
        print(f"      [WARN] 把對話框拉前景失敗:{type(e).__name__}: {e}")


def _handle_export_dialog(download_dir, timeout=30):
    """等「匯出公文資料」對話框出現、把路徑填進去、按 Enter 確認。

    對話框是 KdApp (javaw) 的 JFileChooser,Java 自繪內部控制元件,Win32
    EnumChildWindows 找不到 Edit/Button。所以走鍵盤模擬:
      1. 找對話框 hwnd
      2. SetForegroundWindow + 等 0.5s 確保焦點到 Edit 框
      3. Ctrl+A 全選 → 路徑文字 → Enter
      4. 等對話框消失 (visibility=False or hwnd invalid)

    成功 → True;timeout 內找不到對話框 / 對話框沒消失 → False。
    """
    print(f"[pending_doc_handler] 等「{_EXPORT_DIALOG_TITLE}」對話框出現 (timeout {timeout}s)...")
    hwnd = _find_dialog_hwnd(_EXPORT_DIALOG_TITLE, timeout=timeout)
    if not hwnd:
        print(f"[ERROR] 等了 {timeout}s 沒看到「{_EXPORT_DIALOG_TITLE}」對話框")
        return False

    print(f"      OK:找到對話框 hwnd=0x{hwnd:X},拉到前景...")
    _bring_to_front(hwnd)
    time.sleep(0.6)  # 等焦點轉到 Edit 框 (JFileChooser 預設焦點在「資料夾名稱」)

    print(f"      鍵盤:Ctrl+A 全選 → 清空 → 輸入 {download_dir} → Enter")
    _send_ctrl_combo(_VK_A)
    time.sleep(0.15)
    _send_key(_VK_BACK)
    time.sleep(0.15)
    _send_text_vk(download_dir)
    time.sleep(0.3)
    _send_key(_VK_RETURN)

    # 等對話框關閉
    print("      等對話框關閉...")
    close_deadline = time.time() + 10
    while time.time() < close_deadline:
        if not _user32.IsWindow(hwnd) or not _user32.IsWindowVisible(hwnd):
            print("      OK:對話框已關閉")
            return True
        time.sleep(0.3)
    print("      [WARN] 10s 內對話框沒關閉 — 路徑可能無效或按鈕沒被觸發")
    return False


def _set_chrome_download_dir(driver, download_dir):
    """用 CDP `Page.setDownloadBehavior` 把當前 driver session 的下載目錄設為
    `download_dir`，不重啟 Chrome 也生效。

    為何不用 Chrome options prefs：本模組的呼叫時機是「公文閱覽器分頁已開」，
    Chrome 已啟動，options 改不到。CDP 是 runtime 解法。

    behavior='allow' + downloadPath=<絕對路徑> → 所有下載一律存到該路徑、不彈
    儲存對話框（公文閱覽器點下載按鈕預設會跳系統 Save As，CDP 設了就不會跳）。
    """
    os.makedirs(download_dir, exist_ok=True)
    ok_any = False
    # 雙保險:Page.setDownloadBehavior 是舊版 API、Chrome 137+ 是 deprecated;
    # Browser.setDownloadBehavior 是新版且 browser-wide。新版 driver 對應的
    # Chrome 兩個都吃,但其中一個可能失效,所以兩個都嘗試。
    try:
        driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": download_dir,
        })
        print(f"      OK:Page.setDownloadBehavior allow → {download_dir}")
        ok_any = True
    except Exception as e:
        print(f"      [WARN] Page.setDownloadBehavior 失敗:{type(e).__name__}: {e}")
    try:
        driver.execute_cdp_cmd("Browser.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": download_dir,
        })
        print(f"      OK:Browser.setDownloadBehavior allow → {download_dir}")
        ok_any = True
    except Exception as e:
        print(f"      [WARN] Browser.setDownloadBehavior 失敗:{type(e).__name__}: {e}")
    return ok_any


def _snapshot_dir(download_dir):
    """snapshot 下載資料夾既有檔案,記 {檔名: mtime}。

    為何不只記檔名:KdApp 在覆寫既有同名 zip 時 (例如使用者跑同一份公文兩次),檔名
    沒變但 mtime 會更新。只用 set 比對檔名會永遠錯過這次「下載完成」事件。
    回 dict[str, float],key=檔名,value=mtime。讀不到的單檔忽略 (race condition 安全)。
    """
    out = {}
    try:
        for name in os.listdir(download_dir):
            try:
                out[name] = os.path.getmtime(os.path.join(download_dir, name))
            except OSError:
                pass
    except FileNotFoundError:
        pass
    return out


def _sencha_tap_element(driver, el):
    """對 Sencha Touch / ExtJS 按鈕觸發 tap 事件。

    Sencha Touch (公文閱覽器用的 ExtJS Modern 早期版本) 監聽 touch/pointer 事件而
    非 click,純 `el.click()` 不會觸發 Ext 元件的 tap listener。所以分三層試:
    1. 抓 Ext.getCmp(id).fireAction('tap', ...) — 最 reliable,直接用 framework API
    2. 模擬完整 pointer + touch 事件序列 (pointerdown/up, touchstart/end, click)
    3. fallback 原生 click

    成功(JS 沒丟錯)即回 True。實際是否觸發下載要由呼叫端 polling 檔案來驗證。
    """
    try:
        ok = driver.execute_script("""
            var el = arguments[0];
            // Strategy 1: Ext.getCmp by id
            try {
                if (window.Ext && Ext.getCmp && el.id) {
                    var cmp = Ext.getCmp(el.id);
                    if (cmp && typeof cmp.fireAction === 'function') {
                        cmp.fireAction('tap', [cmp, {}], function() {});
                        return 'ext-fireAction';
                    }
                    if (cmp && typeof cmp.fireEvent === 'function') {
                        cmp.fireEvent('tap', cmp, {});
                        return 'ext-fireEvent';
                    }
                }
            } catch (e) {}
            // Strategy 2: 完整 pointer + touch + mouse 事件序列
            try {
                var r = el.getBoundingClientRect();
                var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                function pe(type) {
                    return new PointerEvent(type, {
                        bubbles: true, cancelable: true, composed: true,
                        pointerId: 1, pointerType: 'touch',
                        clientX: cx, clientY: cy,
                        isPrimary: true,
                    });
                }
                function te(type) {
                    var touch = new Touch({
                        identifier: 1, target: el,
                        clientX: cx, clientY: cy,
                        pageX: cx, pageY: cy,
                    });
                    return new TouchEvent(type, {
                        bubbles: true, cancelable: true,
                        touches: type === 'touchend' ? [] : [touch],
                        targetTouches: type === 'touchend' ? [] : [touch],
                        changedTouches: [touch],
                    });
                }
                function me(type) {
                    return new MouseEvent(type, {
                        bubbles: true, cancelable: true,
                        clientX: cx, clientY: cy, button: 0,
                    });
                }
                el.dispatchEvent(pe('pointerdown'));
                try { el.dispatchEvent(te('touchstart')); } catch(e){}
                el.dispatchEvent(me('mousedown'));
                el.dispatchEvent(pe('pointerup'));
                try { el.dispatchEvent(te('touchend')); } catch(e){}
                el.dispatchEvent(me('mouseup'));
                el.dispatchEvent(me('click'));
                return 'pointer+touch+mouse';
            } catch (e) {
                // Strategy 3: 純 click
                el.click();
                return 'native-click';
            }
        """, el) or "?"
        print(f"      OK:tap 觸發 ({ok})")
        return True
    except Exception as e:
        print(f"      x  tap 觸發失敗:{type(e).__name__}: {e}")
        return False


def _try_click_in_current_frame(driver):
    """在當前 frame context 試 DOWNLOAD_BUTTON_XPATHS。命中即點並回 True。"""
    for xp in DOWNLOAD_BUTTON_XPATHS:
        try:
            els = driver.find_elements(By.XPATH, xp)
        except Exception:
            continue
        for el in els:
            try:
                if not el.is_displayed():
                    continue
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                if _sencha_tap_element(driver, el):
                    print(f"      OK:點到「下載」按鈕(XPath: {xp})")
                    return True
            except Exception as e:
                print(f"      x  XPath {xp} 點擊例外:{type(e).__name__}: {e}")
                continue
    return False


def _click_download_button(driver, timeout=20):
    """點公文閱覽器 toolbar 上的「下載」按鈕（紅框那個下載箭頭）。

    公文閱覽器 (公文簽核 v1.0.344) 是 iframe-based 排版,toolbar 多半在內 frame。
    策略:
    1. driver.switch_to.default_content() 確保 frame focus 重置到 top
    2. 等頁面 ready (poll body innerText 不為空)
    3. 在 top-level 試 DOWNLOAD_BUTTON_XPATHS
    4. 找不到 → 遍歷所有 iframe (含巢狀),逐個進去試
    5. 全部失敗 → dump 所有 frame 結構 + 每個 frame 的 toolbar 候選

    JS click 繞遮罩。
    """
    deadline = time.time() + timeout

    # 先等頁面內容稍微 ready
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    time.sleep(2)

    while time.time() < deadline:
        # Top-level frame
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        if _try_click_in_current_frame(driver):
            return True

        # 遍歷所有 iframe (含巢狀第一層)
        try:
            iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
        except Exception:
            iframes = []
        for ifr in iframes:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(ifr)
            except Exception:
                continue
            if _try_click_in_current_frame(driver):
                return True
            # 巢狀:再進一層
            try:
                inner_iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
            except Exception:
                inner_iframes = []
            for inner in inner_iframes:
                try:
                    driver.switch_to.frame(inner)
                except Exception:
                    continue
                if _try_click_in_current_frame(driver):
                    return True
                try:
                    driver.switch_to.parent_frame()
                except Exception:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(ifr)

        time.sleep(1)

    # 全部失敗 → 大型診斷
    print(f"[ERROR] 等 {timeout}s 仍找不到「下載」按鈕。診斷:")

    def _dump_toolbar_candidates_here(label):
        """在當前 frame 列可見 toolbar 候選元素。擴大 selector 含 div/span/li 帶
        background-image / icon font / aria-label / title 任何提示。"""
        try:
            infos = driver.execute_script("""
                var sel = 'img, button, a, input[type=button], input[type=image], '
                        + '[role=button], svg, [onclick], [aria-label], [title], '
                        + 'i, span[class*="icon"], span[class*="ico"], '
                        + 'div[class*="icon"], div[class*="ico"], '
                        + 'li[class*="icon"], li[class*="ico"], '
                        + '[class*="download"], [class*="Download"], '
                        + '[id*="download"], [id*="Download"]';
                var els = document.querySelectorAll(sel);
                var seen = new Set();
                var out = [];
                for (var i = 0; i < els.length && out.length < 80; i++) {
                    var el = els[i];
                    if (seen.has(el)) continue;
                    seen.add(el);
                    if (el.offsetParent === null) continue;
                    var cls = (el.className && el.className.baseVal !== undefined
                        ? el.className.baseVal : (el.className || '')).toString();
                    var style = window.getComputedStyle(el);
                    var bg = style.backgroundImage || '';
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        title: el.getAttribute('title') || '',
                        alt: el.getAttribute('alt') || '',
                        aria: el.getAttribute('aria-label') || '',
                        src: (el.getAttribute('src') || '').substring(0, 120),
                        onclick: (el.getAttribute('onclick') || '').substring(0, 100),
                        text: (el.textContent || '').trim().substring(0, 30),
                        cls: cls.substring(0, 100),
                        id: (el.id || '').substring(0, 60),
                        bg: bg.substring(0, 120),
                    });
                }
                return out;
            """) or []
            print(f"      [{label}] 候選元素 = {len(infos)} 個")
            for j, info in enumerate(infos):
                # 只印有「下載」/download 提示的、或前 30 個
                hint_str = (
                    info.get('title','') + ' ' + info.get('alt','') + ' '
                    + info.get('aria','') + ' ' + info.get('cls','') + ' '
                    + info.get('id','') + ' ' + info.get('src','') + ' '
                    + info.get('bg','') + ' ' + info.get('onclick','')
                ).lower()
                has_hint = '下載' in hint_str or 'download' in hint_str
                if not has_hint and j >= 30:
                    continue
                mark = "★" if has_hint else " "
                print(f"           {mark}[{j+1}] <{info.get('tag')}> "
                      f"title=「{info.get('title')}」 alt=「{info.get('alt')}」 "
                      f"aria=「{info.get('aria')}」 text=「{info.get('text')}」 "
                      f"id=「{info.get('id')}」")
                print(f"             src={info.get('src')} bg={info.get('bg')}")
                print(f"             onclick={info.get('onclick')} class=「{info.get('cls')}」")
        except Exception as e:
            print(f"      [{label}] toolbar dump 失敗:{type(e).__name__}: {e}")

    try:
        driver.switch_to.default_content()
        # Top-level body 前 500 字
        top_body = driver.execute_script(
            "return document.body ? document.body.innerText.substring(0, 500) : '';") or ""
        print(f"      [top] body innerText 前 500 字:{top_body!r}")
        _dump_toolbar_candidates_here("top")
        # 列出所有 iframe metadata
        iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
        print(f"      [top] iframes = {len(iframes)} 個")
        frame_meta = []
        for i, ifr in enumerate(iframes):
            try:
                frame_meta.append({
                    "idx": i,
                    "el": ifr,
                    "tag": ifr.tag_name,
                    "name": ifr.get_attribute("name") or "",
                    "id": ifr.get_attribute("id") or "",
                    "src": (ifr.get_attribute("src") or "")[:200],
                })
            except Exception as e:
                frame_meta.append({"idx": i, "el": ifr, "err": str(e)})
        for m in frame_meta:
            if "err" in m:
                print(f"      [{m['idx']+1}] metadata 抓不到:{m['err']}")
                continue
            print(f"      [{m['idx']+1}] <{m['tag']}> name='{m['name']}' "
                  f"id='{m['id']}' src={m['src']}")
        # 每個 iframe 進去 dump body + toolbar 候選
        for m in frame_meta:
            if "err" in m:
                continue
            label = m['name'] or m['id'] or f"#{m['idx']+1}"
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(m['el'])
                inner_url = driver.execute_script("return document.URL;") or "?"
                body_preview = driver.execute_script(
                    "return document.body ? document.body.innerText.substring(0, 300) : '';") or ""
                print(f"      [{label}] inner_url={inner_url[:120]}")
                print(f"           body_preview={body_preview!r}")
                _dump_toolbar_candidates_here(label)
            except Exception as e:
                print(f"      [{label}] 進 frame 失敗:{type(e).__name__}: {str(e)[:150]}")
        driver.switch_to.default_content()
    except Exception as e:
        print(f"      diagnostic 失敗:{type(e).__name__}: {e}")
    return False


# 「半成品」副檔名集合 — 看到就視為下載/打包進行中。
# - .crdownload:Chrome 下載暫存
# - .tmp / .part / .download:常見的下載中副檔名
# - 空字串:Java KdApp 寫 zip 時先建無副檔名檔案,寫完再 rename 加 .zip
_IN_PROGRESS_EXTS = {".crdownload", ".tmp", ".part", ".download", ""}


def _wait_for_new_file(download_dir, snapshot, timeout=60):
    """等到 `download_dir` 內出現新檔且 size 穩定 1s+,回傳新檔絕對路徑。

    完成判定:
    - 新出現的檔(不在 snapshot 內) **或** 既存檔的 mtime 已變(被覆寫,例如使用者
      跑同一份公文兩次,KdApp 同名覆寫)
    - 副檔名不在 _IN_PROGRESS_EXTS (排除 .crdownload / .tmp / .part / 無副檔名)
    - 且 size 穩定 1s+ (避免捕捉到剛 rename 完但 OS 還在 flush 的瞬間)

    特別處理 KdApp:Java 寫 zip 時會先建一個無副檔名的暫存 (例:MWAA1156005008),
    寫完才 rename 為 MWAA1156005008.zip。所以無副檔名階段不算完成。

    snapshot 是 dict[檔名 → mtime] (來自 _snapshot_dir)。
    timeout 內找不到 → 回 None。每 5s 印一次進度供觀察。
    """
    start = time.time()
    deadline = start + timeout
    next_progress = start + 5
    while time.time() < deadline:
        try:
            now_files = os.listdir(download_dir)
        except FileNotFoundError:
            time.sleep(0.5)
            continue
        # 「新」= 不在 snapshot 內 OR mtime 改變
        new = []
        for f in now_files:
            try:
                mt = os.path.getmtime(os.path.join(download_dir, f))
            except OSError:
                continue
            if f not in snapshot or snapshot[f] != mt:
                new.append(f)
        if time.time() >= next_progress:
            elapsed = int(time.time() - start)
            done = [f for f in new if os.path.splitext(f)[1].lower() not in _IN_PROGRESS_EXTS]
            in_progress = [f for f in new if os.path.splitext(f)[1].lower() in _IN_PROGRESS_EXTS]
            print(f"      [{elapsed}s] 新檔狀態:已完成 {done},進行中 {in_progress}")
            next_progress += 5
        finished = [f for f in new
                    if os.path.splitext(f)[1].lower() not in _IN_PROGRESS_EXTS]
        if finished:
            # 取第一個 (預期只會有一個)
            name = finished[0]
            path = os.path.join(download_dir, name)
            # 確認 size 穩定 1s — 兩次間隔 0.5s 取樣相同即視為穩定。Java
            # rename 後可能仍有最後 flush,1s buffer 是經驗值。
            try:
                s1 = os.path.getsize(path)
                time.sleep(0.5)
                s2 = os.path.getsize(path)
                time.sleep(0.5)
                s3 = os.path.getsize(path)
                if s1 == s2 == s3 and s1 > 0:
                    return path
            except OSError:
                pass
        time.sleep(0.5)
    return None


def _extract_zip(zip_path, base_dir):
    """把 `zip_path` 解到 `base_dir/<zip 檔名去副檔名>/`,回傳目標目錄絕對路徑。

    目標目錄已存在 → 印警告繼續 extract(會 overwrite 同名檔案)。
    非 zip 檔 → 印警告,回傳 None。
    """
    if not zipfile.is_zipfile(zip_path):
        print(f"[WARN] 下載檔不是 zip,跳過解壓縮:{zip_path}")
        return None

    base = os.path.splitext(os.path.basename(zip_path))[0]
    target = os.path.join(base_dir, base)
    if os.path.exists(target):
        print(f"      [WARN] 目標目錄已存在,覆蓋 extract:{target}")
    else:
        os.makedirs(target, exist_ok=True)

    # KdApp 打包出來的 zip 內檔名用 cp950 (Big5 ANSI Windows zh-TW) 編碼。
    # Python zipfile 預設 cp437 解 — 結果中文資料夾/檔名變亂碼 (例如「來文」變
    # 「¿╙ñσ」)。Python 3.11+ 的 ZipFile 接受 metadata_encoding 參數,直接指定
    # cp950 即可正確解。Python < 3.11 fallback 走 cp437→cp950 手動重編碼。
    try:
        if sys.version_info >= (3, 11):
            with zipfile.ZipFile(zip_path, "r", metadata_encoding="cp950") as zf:
                zf.extractall(target)
        else:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.infolist():
                    try:
                        fixed = member.filename.encode("cp437").decode("cp950")
                    except UnicodeError:
                        fixed = member.filename
                    member.filename = fixed
                    zf.extract(member, target)
        print(f"      OK:已解壓縮到 {target}")
        return target
    except Exception as e:
        print(f"[ERROR] 解壓縮失敗:{type(e).__name__}: {e}")
        return None


# KdApp 打包的 zip 頂層子資料夾命名隨公文類型而異。實測:
# - 來文公文 (一般受理):「來文」
# - 自簽/簽稿:「公文」(根據使用者敘述;尚未直接觀察 repr)
# 未來如遇到新名稱,加進此清單即可。
_FLATTEN_CANDIDATE_NAMES = ("公文", "來文")


def _flatten_subdir(parent_dir, names=_FLATTEN_CANDIDATE_NAMES):
    """若 parent_dir 內存在任一 names 中的子資料夾,就把它內部檔案/資料夾搬到
    parent_dir 下,搬完刪掉空殼。

    names 接 str(單一名稱) 或 iterable of str(多候選名);用 iterable 時依序檢查,
    遇到第一個命中的就處理並 return。

    例:parent_dir = .../MWAA1156005008/,names = ('公文', '來文'),實際存在「來文」
        .../MWAA1156005008/來文/<檔案A>
        .../MWAA1156005008/來文/<子資料夾>/<檔案B>
      → .../MWAA1156005008/<檔案A>
        .../MWAA1156005008/<子資料夾>/<檔案B>
        (來文/ 被刪掉)

    回 True 表示有 flatten;False 表示候選名都不存在(視為 no-op,印 INFO 列出實
    際看到的子資料夾名以便將來擴充清單)或處理失敗。
    """
    if isinstance(names, str):
        names = (names,)

    sub_path = None
    for n in names:
        p = os.path.join(parent_dir, n)
        if os.path.isdir(p):
            sub_path = p
            break

    if sub_path is None:
        try:
            actual = [d for d in os.listdir(parent_dir)
                      if os.path.isdir(os.path.join(parent_dir, d))]
        except OSError:
            actual = []
        print(f"      [INFO] flatten 候選 {names} 都不存在於 {parent_dir},"
              f"實際子資料夾 = {actual}(若有想 flatten 的請加進 _FLATTEN_CANDIDATE_NAMES)")
        return False

    print(f"      flatten {sub_path} → {parent_dir}")
    for item in os.listdir(sub_path):
        src = os.path.join(sub_path, item)
        dst = os.path.join(parent_dir, item)
        if os.path.exists(dst):
            print(f"      [WARN] 目標已存在,保留原檔案位置不搬:{dst}")
            continue
        try:
            os.rename(src, dst)
        except OSError as e:
            print(f"      [WARN] 搬 {src} → {dst} 失敗:{type(e).__name__}: {e}")

    try:
        os.rmdir(sub_path)
        print(f"      OK:flatten 完成,已刪空殼 {sub_path}")
        return True
    except OSError as e:
        print(f"      [WARN] 刪 {sub_path} 失敗(可能還有檔案殘留):{e}")
        return False


def _delete_zip_with_retry(zip_path, attempts=10, interval=0.5):
    """刪除 zip 原檔,失敗時重試。

    為何要重試:KdApp(javaw)是常駐本地元件,剛打包完那一瞬間可能仍持有 zip 的
    檔案 handle。Windows 不允許刪除被其他 process 開啟的檔,os.remove 會丟
    PermissionError(OSError)。單次刪除輸掉這個 race,就會把 zip 永久遺留在
    download_dir。這裡重試 attempts 次、每次間隔 interval 秒,等 javaw 釋放
    handle(condition-based wait)。

    最終仍失敗才印 ERROR(連同檔案是否還在)— 不再像舊版默默吞成一行 WARN。
    回 True=已刪除/本就不存在;False=重試用盡仍刪不掉。
    """
    last_err = None
    for i in range(attempts):
        try:
            os.remove(zip_path)
            tail = f"(第 {i + 1} 次嘗試)" if i else ""
            print(f"      OK:已刪除 zip 原檔 {zip_path}{tail}")
            return True
        except FileNotFoundError:
            print(f"      OK:zip 原檔已不存在,無需刪除 {zip_path}")
            return True
        except OSError as e:
            last_err = e
            time.sleep(interval)
    still = os.path.exists(zip_path)
    print(f"      [ERROR] 重試 {attempts} 次仍無法刪除 zip:"
          f"{type(last_err).__name__}: {last_err}(檔案仍存在={still})")
    return False


def _download_and_extract(driver, download_dir=None, summarize=True):
    """公文閱覽器:點「下載」按鈕、下載到 download_dir、若為 zip 解到子目錄。

    參數:
    - download_dir:下載目標資料夾(絕對路徑);None 用模組預設 DOWNLOAD_DIR
      (= document_download/)。結案存查流程會傳 document_download_closure/。
    - summarize:解壓後是否鏈式呼叫 summarize_doc 對公文主檔產 LLM 總結。
      預設 True(承辦中流程);結案存查流程傳 False(已核可的公文不需摘要)。

    流程:
    1. CDP 設下載目錄 = download_dir
    2. snapshot 既有檔案
    3. 點「下載」按鈕
    4. 等新檔(非 .crdownload / .tmp / 無副檔名)出現且 size 穩定
    5. 是 zip → 解壓縮到 download_dir/<檔名去副檔名>/,解壓成功則刪原 zip
    6. 若 summarize=True 則呼叫 summarize_doc.summarize_extracted

    回 (True, extract_dir 或 None) 表示流程順利;extract_dir=None 代表下載成功
    但非 zip(原檔保留)。回 (False, None) 表示中途失敗。
    """
    if download_dir is None:
        download_dir = DOWNLOAD_DIR

    print(f"[pending_doc_handler] 設定下載目錄 → {download_dir}")
    if not _set_chrome_download_dir(driver, download_dir):
        return False, None

    snapshot = _snapshot_dir(download_dir)
    print(f"      snapshot 既有 {len(snapshot)} 個檔案/目錄")

    print("[pending_doc_handler] 點「下載」按鈕...")
    if not _click_download_button(driver):
        return False, None

    # 點完 packageBtn 觸發的不是 Chrome 下載 — 是 KdApp (javaw) 開的 JFileChooser
    # 「匯出公文資料」對話框,讓使用者選資料夾。Chrome CDP setDownloadBehavior 對
    # 它無效。用 Win32 鍵盤模擬填路徑 + 按 Enter。
    if not _handle_export_dialog(download_dir, timeout=30):
        return False, None

    print(f"[pending_doc_handler] 等 zip 出現在 {download_dir} (timeout 120s)...")
    new_path = _wait_for_new_file(download_dir, snapshot, timeout=120)
    if new_path is None:
        print("[ERROR] 等了 120s 沒看到新檔。可能下載被瀏覽器擋、或按鈕點到了但沒觸發下載。")
        return False, None

    print(f"      OK:下載完成 — {new_path}")

    print("[pending_doc_handler] 解壓縮...")
    extract_dir = _extract_zip(new_path, download_dir)

    if extract_dir is not None:
        # 解壓後若 zip 內頂層只是個外殼資料夾 (例如「公文」/「來文」,隨公文類型異),
        # flatten 上來讓使用者直接看到內容。候選名清單在 _FLATTEN_CANDIDATE_NAMES。
        _flatten_subdir(extract_dir)

        # 解壓縮成功 → 刪 zip 原檔 (節省空間,解壓後的目錄已是完整內容)。
        # 解壓失敗保留 zip 供事後檢查。用重試版刪除:KdApp(javaw)剛打包完可能還
        # 握著 zip handle,Windows 下會擋刪除,需等它放手(詳見 _delete_zip_with_retry)。
        _delete_zip_with_retry(new_path)

        if summarize:
            # 鏈式呼叫 summarize_doc:對解壓出來的公文主檔產出 markdown 總結。
            # summarize_doc 規格寫在 summarize_doc.md (LLM 用、人類維護),程式 runtime
            # 讀該檔當 LLM instruction。沒 ANTHROPIC_API_KEY 會跳過 LLM 步驟、只寫保留
            # 欄位 (發文日期/字號/主旨),整體流程不會因此 fail。
            try:
                from summarize_doc import summarize_extracted
                summarize_extracted(extract_dir)
            except Exception as e:
                print(f"      [WARN] summarize_doc 呼叫失敗 (不影響下載流程):"
                      f"{type(e).__name__}: {e}")

    return True, extract_dir


def handle_opened_document(driver):
    """承辦中公文點完最上方 link、新分頁開啟後的處理流程。

    呼叫時機:document_system.pending_doc 點完公文 + sleep 等新 window 開啟之後。
    driver focus 仍在原(承辦中清單) window;本函式負責切到新公文閱覽器分頁。

    流程:
    1. 確認 window_handles 數 > 1 (新分頁已開)
    2. 切到非主 handle 的 window(通常是最新開的公文閱覽器)
    3. 等載入 + 印 URL/title 確認到位
    4. 呼叫 _download_and_extract:點 toolbar 下載按鈕、下載到
       document_download/、若為 zip 解到以檔名為路徑的子目錄
    5. TODO:後續動作 (檢視內容/簽辦/送件/結案等)

    回 True 表示順利切換並完成下載+解壓縮;False 表示沒找到新 window、
    切換失敗、或下載/解壓縮中途失敗。
    """
    try:
        main_handle = driver.current_window_handle
        handles = driver.window_handles
    except Exception as e:
        print(f"[pending_doc_handler] 讀 window_handles 失敗:{type(e).__name__}: {e}")
        return False

    if len(handles) <= 1:
        print("[pending_doc_handler] 只有 1 個 window,沒偵測到公文閱覽器新分頁。")
        return False

    new_handle = None
    for h in handles:
        if h != main_handle:
            new_handle = h
    if new_handle is None:
        print("[pending_doc_handler] 沒找到非主 window 的 handle。")
        return False

    try:
        driver.switch_to.window(new_handle)
    except Exception as e:
        print(f"[pending_doc_handler] 切 window 失敗:{type(e).__name__}: {e}")
        return False

    # 公文閱覽器 (公文簽核 v1.0.344) 載入 PDF + JS 需要幾秒;eager strategy
    # 已等到 DOMContentLoaded,但 PDF 渲染可能還沒完
    time.sleep(1)

    try:
        print(f"[pending_doc_handler] 切到公文閱覽器分頁,URL={driver.current_url}")
        print(f"[pending_doc_handler] 標題={driver.title}")
    except Exception as e:
        print(f"[pending_doc_handler] 讀狀態失敗:{type(e).__name__}: {e}")

    # 第一個動作:下載公文整包 (toolbar 上的下載箭頭)、解壓縮到子目錄。
    # 之後檢視內容/簽辦/送件等動作可再 chain 在這後面。
    ok, extract_dir = _download_and_extract(driver)
    if not ok:
        print("[pending_doc_handler] 下載/解壓縮失敗,後續動作中止。")
        return False
    if extract_dir:
        print(f"[pending_doc_handler] 下載+解壓縮完成,內容在 {extract_dir}")
    else:
        print("[pending_doc_handler] 下載完成 (非 zip,沒解壓縮)")

    # TODO:在公文閱覽器內做後續動作 (檢視內容/簽辦/送件/結案等)
    return True


if __name__ == "__main__":
    # standalone 階段測試:跑 document_system 同樣的路徑 (開 edoc → cascade →
    # pending_doc),跑完後 document_system.pending_doc 內已 chain 呼叫
    # handle_opened_document,所以本入口只需跑 process_document_system 即可。
    # 之所以另外提供本入口而非用 document_system.py:語義上「本檔關心新分頁
    # 之後的處理」,跑這檔代表「在測試新分頁那段」;document_system.py 則代表
    # 「測整個公文系統流程」。未來若加 Chrome --remote-debugging-port + driver
    # attach,可以改為「只 attach 既有 Chrome session 跳到新分頁直接測」,
    # 不必每次從 edoc 首頁開始。
    from taipeion_login_selenium import _setup_stdout_logging
    _setup_stdout_logging()

    from document_system import (
        _standalone_open_chrome_at_edoc,
        process_document_system,
    )
    driver = _standalone_open_chrome_at_edoc()
    if driver is None:
        sys.exit(1)

    ok = process_document_system(driver)
    sys.exit(0 if ok else 1)
