# -*- coding: utf-8 -*-
"""
prep_batch.py
收新公文（備料）的 fork 版 —— **把「下載」與「叫 AI 寫摘要」拆成兩段跑**。

    python prep_batch.py                  兩段都跑（介面的「收新公文」走這支）
    python prep_batch.py --download-only  只下載，不叫 AI
    python prep_batch.py --summary-only   只補摘要，**完全不碰 edoc、不用插卡**

為什麼要拆（2026-08-10 承辦人踩到，見交接檔坑 #19）:

原本 `main.py 4` 的節奏是「點開 → 下載（約 12 秒）→ 叫 AI 寫摘要（1～2.5 分）
→ 下一筆」。等 AI 那段 Selenium 完全沒有碰 edoc，閒置久了 edoc 就跳
「操作時間逾期，請您重新登入」把人踢掉。實測 8/10:6 筆裡只有 2 筆真的跑 AI
就已經被踢，後面兩筆變成 `切不到清單 frame,跳過` —— 而且最後印
「共處理 4/6 筆」、結束碼 0，**看起來跟正常結束一模一樣**。
公文越多越後面越掛:6 筆全要摘要的話，第 3 筆開始就會全掛。

拆開之後:

    第一段 只下載   —— edoc 一路都在動，每筆之間只隔十幾秒，不會閒置到逾期。
    第二段 離線補摘要 —— 完全不碰 edoc，逾期也無所謂，而且**可以動滑鼠**。

做法跟 `archive_batch.py` 同一招:呼叫前把 `summarize_doc.summarize_extracted`
換成不做事的版本。`pending_doc_handler` 那行是**函式內 import**
（`from summarize_doc import summarize_extracted`），換模組屬性就生效 ——
`document_system.py`、`pending_doc_handler.py` **一行沒改**，系管師自己跑
`main.py 4` 完全不受影響。

第二段用 `review_sheet.prepare()`，它呼叫的 `summarize_doc(d)` 正是
`summarize_extracted` 包的同一支 —— 產出的 `內容.txt` / `總結.*.md` 一模一樣，
拆開跑不會有任何差異。
"""

import contextlib
import glob
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import review_sheet as rs  # noqa: E402

# 只補「承辦中」那個工作區。document_download_closure/ 是已經結案的公文，
# 缺總結也不該在收文時花 token 去補。
WORK_DIRS = rs.SCAN_DIRS[:1]

# 比對 mtime 時往前退的秒數。理由見 touched_since。
_MTIME_SLACK = 2.0


def _noop_summarize(extract_dir):
    """第一段用的假摘要:什麼都不做，把時間留給 edoc。

    回 False（跟真的 summarize_extracted 一樣是 bool），呼叫端只印 log 不判斷。
    """
    print("      [prep_batch] 這一段先不叫 AI —— 等公文全部下載完再一次補")
    return False


# ── 中文安裝路徑（2026-08-14，同事那台踩到）──────────────────────────────
#
# KdApp 的「匯出公文資料」對話框（Java Swing JFileChooser）是用**模擬實體鍵盤**
# 填路徑的:`pending_doc_handler._send_text_vk` 把每個字元丟給 `VkKeyScanW`，
# 而那支只認得 ASCII —— 中文字拿到 -1，程式印一行 WARN 然後 **`continue` 跳過**。
#
# 於是安裝在中文路徑下時，路徑會被吃掉一段:
#     D:\D_資訊系統\自動辦文工具\document_download
#   → D:\D_\document_download          ← 公文真的被下載到這裡
#
# 那支的註解自己寫著「這個路徑只用英文，夠用」—— 在承辦人的機器上成立
# （`D:\sssh-automation`），在同事的機器上不成立。**典型的「在我這台是好的」。**
#
# 修法（fork 側，`pending_doc_handler.py` 一行沒改）:路徑含非 ASCII 時改走
# **剪貼簿貼上**（Ctrl+V）—— Swing 對剪貼簿是 Unicode 安全的，而模擬鍵盤不是。
# 純 ASCII 的路徑仍走原本那條已經驗過的鍵盤路，行為完全不變。
#
# ⚠️ **貼不上去就什麼都不打。** 打半截路徑的下場是公文靜靜掉到別的資料夾
# （正是這次的症狀）；什麼都不打的話對話框不會關，原本那道「10s 內對話框沒關閉」
# 就會叫出來 —— 失敗要看得見。

_VK_V = 0x56


def _copy_to_clipboard(text):
    """把字串放進剪貼簿，並**讀回來確認**。回 True/False。"""
    try:
        import win32clipboard as cb
    except ImportError:
        print("      [prep_batch] 沒有 pywin32，無法用剪貼簿貼路徑。")
        return False
    try:
        cb.OpenClipboard()
        try:
            cb.EmptyClipboard()
            cb.SetClipboardData(cb.CF_UNICODETEXT, text)
        finally:
            cb.CloseClipboard()
        cb.OpenClipboard()
        try:
            back = cb.GetClipboardData(cb.CF_UNICODETEXT)
        finally:
            cb.CloseClipboard()
        return back == text
    except Exception as e:
        print(f"      [prep_batch] 剪貼簿操作失敗:{type(e).__name__}: {e}")
        return False


@contextlib.contextmanager
def unicode_safe_dialog_path():
    """路徑含中文時，改用剪貼簿把它貼進 KdApp 的對話框。用完還原。"""
    import pending_doc_handler as pdh
    real = pdh._send_text_vk

    def patched(text, per_char_delay=0.02):
        if all(ord(c) < 128 for c in str(text)):
            return real(text, per_char_delay)      # 原本那條路，行為不變
        print(f"      [prep_batch] 路徑含中文 —— 改用剪貼簿貼上（模擬鍵盤打不出中文）")
        if not _copy_to_clipboard(str(text)):
            print("      [prep_batch] ⛔ 剪貼簿放不進去 —— **什麼都不打**。")
            print("      [prep_batch]    打半截路徑會讓公文靜靜掉到別的資料夾。")
            print("      [prep_batch]    最保險的解法:把這個工具搬到**純英數路徑**"
                  "（例如 D:\\sssh-tool）再跑。")
            return
        pdh._send_ctrl_combo(_VK_V)
        time.sleep(0.25)

    pdh._send_text_vk = patched
    try:
        yield
    finally:
        pdh._send_text_vk = real


@contextlib.contextmanager
def download_only():
    """在這個區塊內，下載完不會鏈式呼叫 AI 摘要。

    **一定要還原**（try/finally）—— 不還原的話，同一個行程裡第二段的補摘要
    也會變成不做事，而且是**靜靜地什麼都沒產出**。
    （同 announce_doc 那個模型覆寫的教訓:覆寫只在該段有效，出了區塊必須復原。）
    """
    import summarize_doc
    real = summarize_doc.summarize_extracted
    summarize_doc.summarize_extracted = _noop_summarize
    try:
        yield
    finally:
        summarize_doc.summarize_extracted = real


def pending_summaries(dirs=None):
    """已經下載、但還沒有總結的公文目錄（**包含歷史的**）。"""
    return [d for d in rs.iter_doc_dirs(dirs or WORK_DIRS)
            if not glob.glob(os.path.join(d, "*總結.*.md"))]


def touched_since(t0, dirs=None):
    """這次真的下載到、而且還缺總結的公文目錄。

    **收新公文只補這次收進來的。** 工作區裡躺著一堆歷史公文，其中有些是早期
    跑到一半留下的空殼（2026-08-10 實測:`document_download/` 有 9 個 006xxx
    的目錄缺總結，旁邊還留著沒刪的 .zip）。如果第二段直接補「所有缺總結的」，
    按一次「收新公文」就會默默對那 9 份叫 LLM —— 十幾分鐘、九次 token，
    而且多半是早就辦完的公文。那不是按下那顆鈕的意思。

    要補歷史的請明確跑 `--summary-only`，那條會全部補。

    判斷用目錄的 mtime:解壓與 flatten 都會動到它，所以「這次被寫過」的目錄
    mtime 一定晚於下載開始的時間。

    ⚠️ 要留安全邊際:`t0` 是浮點秒，但檔案系統的 mtime 解析度可能只到秒
    （FAT 甚至 2 秒）。同一秒內建立的目錄，mtime 被截斷後會看起來「比 t0 早」，
    於是**這次剛收到的公文被當成歷史公文、不補摘要**。
    兩種錯的代價不對稱:漏掉 = 公文沒摘要，要人工發現；多算一份 = 多花一次
    token。所以往「寧可多補」的方向退。
    """
    cutoff = t0 - _MTIME_SLACK
    out = []
    for d in pending_summaries(dirs):
        try:
            if os.path.getmtime(d) >= cutoff:
                out.append(d)
        except OSError:
            continue
    return out


# Chrome 會還原「上次工作階段」的那幾個檔。Sessions/ 是新版放的地方，
# 根目錄那四個是舊版格式 —— 兩種都清，不確定哪一版就全掃。
_SESSION_FILES = ("Current Session", "Current Tabs", "Last Session", "Last Tabs")


def clear_stale_session():
    """把 profile 裡「上次開了哪些分頁」清掉。回清掉幾個檔。

    **Chrome 必須已經關閉**（在 `_close_selenium_chrome_only()` 之後呼叫），
    不然檔案被鎖住，刪不掉的就跳過。

    為什麼要做（2026-08-11，承辦人連續三次收不到公文）:

    登入用的 Chrome 是被 taskkill 關掉的，Chrome 因此認定上次是異常結束；
    而啟動旗標寫的是 `--restore-last-session=false` —— Chrome 的 switch
    **只看「有沒有這個旗標」，不看值**，所以那行的實際效果是「強制還原上次
    工作階段」，跟原作者註解裡「避免不小心還原」的本意相反。
    結果:前一天那個已經逾期的 edoc 分頁（sessionTimeout.jsp）連同**過期的
    session cookie** 一起被復活，新登入一進 edoc 就被判定成過期 session、
    直接踢回 index.jsp，左側選單當然一個都讀不到 → 一份公文都收不到。
    而且殘骸會一直留著，所以**重跑幾次都一樣**。

    這裡不動那個旗標 —— `taipeion_login_selenium.py` 是共用的登入模組，
    全自動路徑（`main.py 1`/`2`/`3`）也在用它。改成把「可以被還原的東西」
    先清掉:沒有上次工作階段，還原旗標就無事可做，兩條路都不受影響。

    只清「開了哪些分頁」，**不碰 cookie 與網站設定** —— edoc 那個
    「不需再顯示環境檢測」的勾選可能就存在 cookie 裡，清掉會讓對話框每次都跳。
    """
    from taipeion_login_selenium import PROFILE_DIR, USER_DATA_DIR
    base = os.path.join(USER_DATA_DIR, PROFILE_DIR)
    targets = [os.path.join(base, n) for n in _SESSION_FILES]
    targets += glob.glob(os.path.join(base, "Sessions", "*"))

    def sweep():
        g = l = 0
        for p in targets:
            if not os.path.isfile(p):
                continue
            try:
                os.remove(p)
                g += 1
            except OSError:
                l += 1
        return g, l

    gone, locked = sweep()
    if locked:
        # taskkill 之後 Windows 不一定馬上放開檔案 handle。沒清乾淨的話症狀跟
        # 沒修一模一樣（又還原舊分頁、又被踢回登入頁），寧可多等一秒再試。
        import time
        time.sleep(1.0)
        g2, locked = sweep()
        gone += g2
    if gone or locked:
        print(f"[prep_batch] 清掉上次殘留的分頁記錄:{gone} 個")
    if locked:
        # 靜靜失敗最糟 —— 這時症狀會跟沒修一樣，要讓人知道往哪裡看。
        print(f"[prep_batch] ⚠️ 有 {locked} 個檔刪不掉（還被鎖著）。如果待會又被踢回")
        print("[prep_batch]    登入頁，請手動把所有 Chrome 關乾淨再跑一次。")
    return gone


def clear_edoc_session_cookies():
    """清掉 edoc 的**工作階段 cookie**。回清掉幾筆。**Chrome 必須已經關閉。**

    2026-08-11 系管師的說法是「遇到那個登入頁，把瀏覽器關掉就行」——
    但我們的程式每次都關了（`_close_selenium_chrome_only`），還是卡住。
    差別在**怎麼關**:

      他手動點右上角的 X → Chrome 正常結束，會把 `is_persistent=0` 的
                          工作階段 cookie 丟掉，下次登入是乾淨的。
      我們 taskkill 強制殺 → 那些 cookie 留在磁碟上，再加上啟動旗標
                          `--restore-last-session=false` 實際等於強制還原
                          （見 clear_stale_session），過期的 edoc session
                          就被復活 → edoc 認出它過期 → 導回 index.jsp
                          並彈「操作時間逾期」。

    這裡做的就是「正常關閉會做的那件事」。

    ⚠️ **只刪 is_persistent=0 的**:網站偏好（例如 edoc 那個「先前已安裝過
    相對應的元件，不需再顯示此訊息」的勾選）是持久 cookie，一起刪掉會讓
    環境檢測對話框每次都跳出來擋路。
    """
    import sqlite3
    from taipeion_login_selenium import PROFILE_DIR, USER_DATA_DIR
    base = os.path.join(USER_DATA_DIR, PROFILE_DIR)
    for name in (os.path.join("Network", "Cookies"), "Cookies"):
        db = os.path.join(base, name)
        if os.path.isfile(db):
            break
    else:
        return 0
    try:
        con = sqlite3.connect(db)
        cur = con.execute(
            "DELETE FROM cookies WHERE host_key LIKE '%edoc.gov.taipei%' "
            "AND is_persistent = 0")
        n = cur.rowcount
        con.commit()
        con.close()
    except Exception as e:
        # 清不掉不是致命傷（大不了症狀重現），但要講出來，不要靜靜跳過。
        print(f"[prep_batch] ⚠️ 清 edoc 工作階段 cookie 失敗:{type(e).__name__}: {e}")
        return 0
    if n:
        print(f"[prep_batch] 清掉 edoc 的工作階段 cookie:{n} 筆"
              f"（等同你手動正常關閉瀏覽器;網站偏好保留）")
    return max(n, 0)


def close_timeout_tabs(driver):
    """關掉「操作時間逾期」那個警告分頁。回關掉幾個。

    它是 edoc 自己 `window.open` 開的獨立分頁（不是 JS alert，按不掉），
    留著會讓後面每一道「有沒有在主畫面」的檢查都要繞過它，而且 Selenium 的
    焦點可能就停在它身上 —— 那頁沒有左側選單，於是四個選單項全部讀不到。
    """
    from post_draft_batch import _TIMEOUT_MARK
    closed = 0
    for h in list(driver.window_handles):
        try:
            driver.switch_to.window(h)
            if _TIMEOUT_MARK in (driver.current_url or "").lower():
                driver.close()
                closed += 1
        except Exception:
            continue
    # close() 之後目前的 handle 已經失效，一定要切回還活著的分頁，
    # 否則接下來每個 driver 操作都會炸。
    try:
        rest = driver.window_handles
        if rest:
            driver.switch_to.window(rest[0])
    except Exception:
        pass
    return closed


def settle_on_main_screen(driver, timeout=30):
    """等 edoc 跳完，並把焦點停在有左側選單的主畫面分頁。回 True/False。

    **要等**（2026-08-11 承辦人連踩五次）:`auth3.jsp` 到主畫面中間會先經過
    `index.jsp`。導航後 3 秒讀到的是那個中繼站，判定「停在登入頁」就放棄 ——
    但事後看 Chrome，分頁**確實停在 `home/default.jsp`**，主畫面根本就開起來了。
    讀太早比讀不到更騙人:它會給你一個看起來很確定、其實是半路快照的答案。

    **也要切分頁**:旁邊那個 `sessionTimeout.jsp` 獨立視窗沒有左側選單，
    Selenium 的焦點停在它身上時，選單項一個都找不到（坑 #9 同型）。
    用 `post_draft_batch.focus_main_window` 逐分頁找有 `#leftSideBar` 的那個 ——
    **靠元素認、不靠網址認**，這正是存查那條路沒中招的原因。
    """
    import time
    from post_draft_batch import focus_main_window
    deadline = time.time() + timeout
    last = None
    while True:
        close_timeout_tabs(driver)
        try:
            if focus_main_window(driver):
                return True
        except Exception:
            pass
        # 把「現在停在哪」印出來 —— 2026-08-11 卡住時完全看不出它在等什麼，
        # 只能事後 attach 上去看。等待中的沉默跟當機長得一模一樣。
        try:
            now = driver.current_url or ""
        except Exception:
            now = "(讀不到網址)"
        if now != last:
            print(f"[prep_batch]   還在等…目前停在:{now[:80]}")
            last = now
        if time.time() >= deadline:
            return False
        time.sleep(1.5)


# edoc 憑證登入頁那個 PinCode 欄位。**用精確的選擇器**，不吃
# `taipeion_login_selenium.PIN_INPUT_XPATHS` 裡那條寬鬆的 `input[@type=password]`
# —— 填錯框再按登入就是一次 PIN 錯誤，而 PIN 錯誤會累積、會鎖卡。
_PIN_SELECTORS = (
    "input[placeholder*='pinCode']",
    "input[placeholder*='PinCode']",
    "input[placeholder*='PINCODE']",
    "input[id*='pinCode']",
    "input[name*='pinCode']",
)


def needs_pin_login(driver):
    """畫面上是不是 edoc 的憑證登入頁（要再輸入一次卡片 PinCode）。

    **會切到那個分頁**，讓接著的填 PIN 動作對到正確的頁面。

    2026-08-11 承辦人踩到:edoc 自 114/6/17 起採雙因子身分驗證 ——
    TAIPEION 用憑證登入只是第一關，**進 edoc 之後還要再輸入一次卡片 PinCode**。
    程式以為進了 edoc 就該看到主畫面，於是一直停在 index.jsp，
    旁邊還跳「操作時間逾期」把人往登入的方向誤導。
    8/10 之所以能跑，是那天 edoc 那邊的 session 還在、不用重驗。
    """
    for h in list(driver.window_handles):
        try:
            driver.switch_to.window(h)
            if "/tcqb/index.jsp" not in (driver.current_url or "").lower():
                continue
            driver.switch_to.default_content()
            for sel in _PIN_SELECTORS:
                if driver.find_elements("css selector", sel):
                    return True
        except Exception:
            continue
    return False


def edoc_pin_login(driver):
    """在 edoc 的憑證登入頁補填一次卡片 PinCode 並送出。回 True/False。

    ⚠️ **只做一次，失敗一律不重試。** 自然人憑證連續輸錯 PIN 會鎖卡，
    鎖了要跑一趟戶政事務所 —— 那個代價遠大於「今天少收一次文」。
    用的 PIN 跟 TAIPEION 那關是同一個（env.env 的 `pin=`），而那一關幾秒前
    才驗過，所以**值本身一定是對的**;真正的風險只有「填到別的框」，
    所以欄位選擇器寫得很窄（見 _PIN_SELECTORS），寧可找不到也不亂填。

    呼叫端**絕對不可以放進重試迴圈**。
    """
    from taipeion_login_selenium import _read_pin
    pin = _read_pin()
    if not pin:
        print("[prep_batch] ⛔ env.env 裡沒有 pin=，沒辦法自動過 edoc 這一關。")
        print("[prep_batch]    請自己在畫面上輸入卡片 PinCode 按登入，")
        print("[prep_batch]    再跑 python prep_batch.py --attach。")
        return False

    box = None
    for sel in _PIN_SELECTORS:
        found = [e for e in driver.find_elements("css selector", sel)
                 if e.is_displayed()]
        if found:
            box = found[0]
            break
    if box is None:
        print("[prep_batch] ⛔ 找不到 PinCode 輸入框，不再嘗試（避免填錯鎖卡）。")
        return False

    print("[prep_batch] edoc 要求第二次憑證驗證（雙因子）—— 補填一次卡片 PinCode。")
    try:
        box.click()
        box.clear()
        box.send_keys(pin)
    except Exception as e:
        print(f"[prep_batch] ⛔ 填 PinCode 失敗:{type(e).__name__}: {e}")
        return False

    for xp in ("//button[contains(., '登入')]",
               "//input[@type='submit' and contains(@value, '登入')]",
               "//input[@type='button' and contains(@value, '登入')]",
               "//a[contains(., '登入')]"):
        try:
            hit = [e for e in driver.find_elements("xpath", xp) if e.is_displayed()]
        except Exception:
            continue
        if hit:
            hit[0].click()
            print("[prep_batch] 已送出 PinCode，等主畫面…")
            return True
    # 填好了但按不到 —— PIN 就留在框裡，讓人自己按一下就好，不要再自動試。
    print("[prep_batch] ⛔ PinCode 已填入，但找不到「登入」按鈕。")
    print("[prep_batch]    請自己按一下畫面上的「登入」，再跑 --attach。")
    return False


def timed_out(driver):
    """畫面上有沒有那個「操作時間逾期」的警告視窗。

    判定借 `post_draft_batch.looks_timed_out`，不另立標準 —— 介面那盞燈
    （`ui.chrome_state`）用的是同一支。
    """
    from post_draft_batch import looks_timed_out
    urls = []
    try:
        handles = list(driver.window_handles)
    except Exception:
        return False
    for h in handles:
        try:
            driver.switch_to.window(h)
            urls.append(driver.current_url or "")
        except Exception:
            continue
    return looks_timed_out(urls)


# ── 第一段:只下載 ─────────────────────────────────────────────────────────

def attach_driver():
    """接上**已經開著、已經登入好**的 Chrome。回 driver 或 None。

    送陳核（`post_draft_batch`）與存查（`archive_batch`）本來就是這樣做的 ——
    備料沒有理由一定要自己重登一次。2026-08-11 承辦人卡在登入那段（TAIPEION
    過了、edoc 遲遲不跳主畫面）連跑六次都收不到公文，但那時 Chrome 其實**就停在
    可用的主畫面上**。有這條路就能直接把公文收下來，不用再跟登入耗。
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    o = Options()
    o.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    try:
        return webdriver.Chrome(options=o)
    except Exception as e:
        print(f"[prep_batch] 接不上既有的 Chrome:{type(e).__name__}: {e}")
        print("[prep_batch] --attach 需要「收新公文開的那個 Chrome」還開著。")
        print("[prep_batch] 沒有的話請跑不帶 --attach 的版本（會自己登入）。")
        return None


def download_stage(attach=False):
    """登入 → 進公文系統 → 簽收 → 逐筆下載（不叫 AI）。回 (driver, ok)。

    這幾步跟 `main.py 4`（FEATURES[3]）呼叫的是同一組函式，一個字都沒改 ——
    差別只在外面包了 `download_only()`。

    attach=True 則跳過登入，直接用既有的 Chrome（見 attach_driver）。
    """
    from click_document import click_document_card
    from document_system import process_document_prep
    from ime_utils import ensure_english_ime
    from taipeion_login_selenium import login_taipeion_selenium

    # 起手式:輸入法切回英文。「匯出公文資料」對話框要用鍵盤打路徑，
    # 被 IME 攔截就會填錯。
    ensure_english_ime()

    print("[prep_batch] ── 第一段:下載（不叫 AI）──")
    print("[prep_batch] 這一段會操作 Chrome，**請不要動滑鼠鍵盤**。憑證要插著、螢幕不能鎖。")
    if attach:
        print("[prep_batch] --attach:接既有的 Chrome，不重新登入。")
        driver = attach_driver()
        if driver is None:
            return None, False
    else:
        driver = login_taipeion_selenium(return_driver=True)
        if driver is None:
            print("[prep_batch] 登入沒完成，停在這裡（沒有下載任何東西）。")
            return None, False
        if not click_document_card(driver):
            print("[prep_batch] 進不了公文系統，停在這裡（沒有下載任何東西）。")
            return driver, False

    # 進了 edoc，但**不能馬上判斷**成功與否:auth3.jsp 到主畫面中間會先經過
    # index.jsp，而且旁邊可能多一個沒有選單的 sessionTimeout.jsp 分頁。
    # 等它跳完、順便把焦點停在有左側選單的那一頁（見 settle_on_main_screen）。
    print("[prep_batch] 等公文系統主畫面出現…")
    ok_main = settle_on_main_screen(driver, timeout=15)
    if not ok_main and needs_pin_login(driver):
        # edoc 的雙因子:第二關要再輸入一次卡片 PinCode。
        # ⚠️ 這行**只能執行一次**，不可以放進重試迴圈 —— PIN 連續錯會鎖卡。
        if edoc_pin_login(driver):
            ok_main = settle_on_main_screen(driver, timeout=45)
    if not ok_main:
        try:
            url = driver.current_url or ""
        except Exception:
            url = ""
        print("[prep_batch] ⛔ 等不到公文系統主畫面（左側有選單那個）。")
        print(f"[prep_batch]    目前網址:{url}")
        print("[prep_batch]    如果畫面停在 edoc 的「憑證登入」頁（要輸入卡片")
        print("[prep_batch]    PinCode 那張），請自己輸入 PinCode 按登入，")
        print("[prep_batch]    進到主畫面後跑:python prep_batch.py --attach")
        print("[prep_batch]    （程式不會替你重試 PIN —— 連續輸錯會鎖卡。）")
        print("[prep_batch]    處理完再跑一次 —— **這次一份公文都還沒收，不會重複**。")
        return driver, False
    print("[prep_batch] OK:已停在主畫面，開始下載。")

    # 中文安裝路徑那道也一起裝上 —— 見上面那段（同事 2026-08-14 踩到）。
    with download_only(), unicode_safe_dialog_path():
        ok = process_document_prep(driver)
    return driver, bool(ok)


# ── 第一段跑完:逐筆對帳（看磁碟，不看畫面） ──────────────────────────────

def _has_main_pdf(doc_dir):
    """這個公文目錄裡到底有沒有來文主檔（數字_數字[A-Z].pdf）。

    沿用 summarize_doc 的 pattern 與「第一層沒有就往下找一層」規則 ——
    判斷「有沒有收到」要跟後面判斷「能不能摘要」同一把尺，兩邊不一致的話
    會出現「這裡說收到了、那裡說找不到主檔」的鬼故事。
    """
    from summarize_doc import _MAIN_DOC_PATTERN as pat
    try:
        names = os.listdir(doc_dir)
    except OSError:
        return False
    if any(pat.match(n) for n in names):
        return True
    for n in names:
        sub = os.path.join(doc_dir, n)
        if os.path.isdir(sub):
            try:
                if any(pat.match(m) for m in os.listdir(sub)):
                    return True
            except OSError:
                pass
    return False


def download_report(base=None):
    """第一段結束後，拿清單上的每一筆去跟磁碟對帳，回 (收到的, 沒收到的)。

    為什麼要多做這一段:`document_system` 那句「共處理 N/N 筆」是按「點過幾筆」
    算的 —— 點進去之後下載失敗，它照樣 `done += 1`。2026-09-03 實測:清單 2 筆，
    1 筆成功 1 筆失敗，畫面印的是「共處理 2/2 筆」，跟全部收完長得一模一樣。
    那支是系管師的檔，這個 fork 不改它（改了兩邊行為會不一樣），所以改成
    **在 fork 這一側看磁碟再老實報一次**。

    失敗的那幾筆會盡量講出原因:清單「簽核」欄是「紙」的，就是紙本轉線上、
    來文本文還沒上傳，不是程式壞掉。

    讀不到清單（沒走到那一步、或欄位改名）就回 ([], []) 不亂報 ——
    寧可什麼都不說，也不要報一份假的對帳。
    """
    import list_pager
    seen = list(list_pager.SIGN_KINDS.keys())
    if not seen:
        return [], []
    if base is None:   # base 可傳入,測試才好塞假的工作區
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "document_download")
    got, missing = [], []
    for no in seen:
        d = os.path.join(base, no)
        (got if os.path.isdir(d) and _has_main_pdf(d) else missing).append(no)

    print(f"[prep_batch] 逐筆對帳（看磁碟，不看畫面）:清單 {len(seen)} 筆，"
          f"收到 {len(got)} 筆、沒收到 {len(missing)} 筆。")
    for no in missing:
        kind = list_pager.sign_kind(no)
        if list_pager.is_paper(no):
            print(f"[prep_batch] ⚠️ {no} 沒收到 —— 這是**紙本轉線上**公文"
                  f"（清單「簽核」欄＝「{kind}」）。")
            print("[prep_batch]    來文本文還沒掛進 edoc，所以公文閱覽器裡根本沒有「下載」按鈕。")
            print("[prep_batch]    要收它:拿到紙本 → 自己掃描 → 在 edoc 勾那一列按「轉線上」")
            print("[prep_batch]    → 來文 pdf 與附件**分開**上傳 → 再按一次「收新公文」。")
            print("[prep_batch]    這一步沒辦法自動化（要實體公文和掃描器），不是程式壞掉。")
        else:
            print(f"[prep_batch] ⚠️ {no} 沒收到"
                  f"（清單「簽核」欄＝「{kind or '讀不到'}」）—— 看上面那一筆的訊息。")
    return got, missing


# ── 第二段:離線補摘要 ─────────────────────────────────────────────────────

def summary_stage(targets=None):
    """對缺總結的目錄補跑 LLM。回 (成功數, 失敗清單)。

    targets — 只補這幾個目錄（收新公文那條路傳「這次下載到的」）。
              None = 全部缺總結的都補（`--summary-only` 走這條）。

    這一段**不碰 edoc**，所以 edoc 逾不逾期都無所謂 —— 這正是拆開的目的。
    """
    todo = pending_summaries() if targets is None else list(targets)
    print("[prep_batch] ── 第二段:補摘要（離線，不碰 edoc）──")
    if not todo:
        print("[prep_batch] 沒有要補摘要的公文，這一段不用做。")
        return 0, []
    # 坑 #13 的教訓:能不能動滑鼠要**明講**，不要讓人靠「視窗還在不在」去猜。
    print(f"[prep_batch] {len(todo)} 份要叫 AI，每份約 1～2 分鐘。")
    print("[prep_batch] ✋ 這一段不會動 Chrome —— **可以動滑鼠了**，去忙別的沒關係。")
    return rs.prepare(WORK_DIRS, only=todo)


def sync_sheet():
    """把新公文同步進審核表。寫不進去（Excel 開著）不算失敗，講清楚就好。"""
    try:
        added, updated, n, skipped = rs.sync()
    except PermissionError:
        print("[prep_batch] ⚠️ 公告彙整.xlsx 正被 Excel 開著，新公文暫時進不了審核表。")
        print("[prep_batch]    關掉 Excel 後跑 `python review_sheet.py` 補同步即可，")
        print("[prep_batch]    **公文本身已經收下來了**，不用重收。")
        return
    except Exception as e:
        print(f"[prep_batch] ⚠️ 同步審核表出錯:{type(e).__name__}: {e}")
        return
    print(f"[prep_batch] 審核表:新增 {added} 列、更新 {updated} 列"
          f"（掃到 {n} 個目錄，{skipped} 個還沒備料）")


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="收新公文:先全部下載，再離線補摘要（避免 edoc 在等 AI 時逾期）")
    ap.add_argument("--download-only", action="store_true",
                    help="只下載，不叫 AI（之後用 --summary-only 補）")
    ap.add_argument("--summary-only", action="store_true",
                    help="只補摘要，完全不碰 edoc、不用插卡")
    ap.add_argument("--attach", action="store_true",
                    help="接已經登入好的 Chrome，不重新登入（登入那段卡住時用）")
    a = ap.parse_args()

    if a.download_only and a.summary_only:
        print("[prep_batch] --download-only 跟 --summary-only 不能一起用。")
        raise SystemExit(2)
    if a.attach and a.summary_only:
        print("[prep_batch] --attach 跟 --summary-only 不能一起用"
              "（補摘要本來就不碰瀏覽器）。")
        raise SystemExit(2)

    # --summary-only 不碰 edoc，所以**不要**去關 Chrome ——
    # 那會把承辦人正在用的視窗一起收掉，而這一段根本用不到瀏覽器。
    if a.summary_only:
        ok, failed = summary_stage()
        print(f"\n[prep_batch] 補摘要完成 {ok} 份"
              + (f"，失敗 {len(failed)} 份:{failed}" if failed else ""))
        sync_sheet()
        return

    from taipeion_login_selenium import (_close_selenium_chrome_only,
                                         _setup_stdout_logging)
    _setup_stdout_logging()
    # --attach 是要用**現在開著那個**已登入的 Chrome，關掉它等於自廢武功。
    if not a.attach:
        _close_selenium_chrome_only()
        # 這兩步一定要在關掉 Chrome **之後**、開新的 Chrome **之前** ——
        # 檔案這時才沒被鎖，而且清完馬上就會被新的 Chrome 讀到。
        # 合起來等於「你手動正常關閉瀏覽器」會做的事:丟掉上次的分頁記錄，
        # 也丟掉過期的工作階段 cookie（taskkill 不會做這兩件）。
        clear_stale_session()
        clear_edoc_session_cookies()

    # 記下起跑時間 —— 第二段靠它認出「這次真的下載到的是哪幾份」，
    # 才不會連工作區裡的歷史公文一起叫 LLM（見 touched_since）。
    import time
    t0 = time.time()
    driver, ok = download_stage(attach=a.attach)
    print("[prep_batch] ── 第一段結束 ──")

    # 逾期要**明講**。坑 #19 的症狀就是它靜靜跳過、結束碼 0，看起來像正常跑完。
    if driver is not None and timed_out(driver):
        print("[prep_batch] ⚠️ edoc 跳出「操作時間逾期」—— 下載可能沒做完。")
        print("[prep_batch]    請關掉那個灰色警告小視窗，等這支跑完再按一次「收新公文」，")
        print("[prep_batch]    已經下載好的不會重收。")
    elif not ok:
        print("[prep_batch] ⚠️ 下載階段沒有正常結束，可能有公文沒收到 ——")
        print("[prep_batch]    看上面的訊息，必要時再按一次「收新公文」。")

    # 不管上面 ok 是真是假，都拿磁碟對一次帳 —— 「共處理 N/N 筆」會騙人。
    download_report()

    fresh = touched_since(t0)
    if a.download_only:
        print(f"[prep_batch] --download-only:停在這裡，這次收到的 {len(fresh)} 份還沒摘要。")
        print("[prep_batch] 要補摘要跑:python prep_batch.py --summary-only")
        return

    got, failed = summary_stage(fresh)
    print(f"\n[prep_batch] 補摘要完成 {got} 份"
          + (f"，失敗 {len(failed)} 份:{failed}" if failed else ""))
    if failed:
        print("[prep_batch] ⚠️ 摘要失敗的那幾份**公文已經下載好了**，不用重收 ——")
        print("[prep_batch]    跑 `python prep_batch.py --summary-only` 只補摘要就好。")
    sync_sheet()

    # 工作區裡的歷史公文缺總結:**講出來，但不擅自去補**。
    # 那些多半是早期跑到一半留下的，補一份就是一次 token、一兩分鐘，
    # 而且可能是早就辦完的公文 —— 要不要花這個錢是承辦人的決定。
    old = [d for d in pending_summaries() if d not in fresh]
    if old:
        print(f"\n[prep_batch] ⓘ 另外有 {len(old)} 份**舊**公文的目錄缺摘要"
              f"（{'、'.join(os.path.basename(d) for d in old[:3])}"
              f"{'…' if len(old) > 3 else ''}）。")
        print("[prep_batch]   這次沒有動它們。多半是早期跑到一半留下的，")
        print("[prep_batch]   確定要補再跑:python prep_batch.py --summary-only")


if __name__ == "__main__":
    main()
