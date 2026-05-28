"""
document_closure.py
結案存查功能主模組 — 假設 driver 已導航到 edoc 公文首頁（已登入）。

呼叫方式：
1) 從 main.py 串接（FEATURES[2]，python main.py 3）：
     process_document_closure(driver)
2) 單獨執行（跳過登入，直接開 Chrome 到 edoc）：
     C:\\Python314\\python.exe document_closure/document_closure.py
   session 過期時會提示跑 main.py 重新登入。
"""

import glob
import os
import shutil
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

# 注意:selenium 是這個 process 的硬性相依(driver 物件從 document_system 傳進來時
# Selenium 必定已 load),這裡 import By 提早到模組層級,避免 _has_approval_text
# 在 while 迴圈每輪都重複 import。
from selenium.webdriver.common.by import By  # noqa: E402

# 單獨執行（python document_closure/document_closure.py）時，Python 只把腳本所在的
# document_closure/ 加進 sys.path，找不到專案根目錄的 taipeion_login_selenium /
# document_system 等模組。把專案根目錄（本檔的上層目錄）插進 sys.path 才能 import。
# 從 main.py 以 package 形式 import 時根目錄已在 path，重複插入無害。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# edoc 公文首頁 URL（與 document_system.py 保持一致）
EDOC_HOME_URL = "https://edoc.gov.taipei/tcqb/home/default.jsp?inLine=Y"

# 結案存查下載目標 — 用絕對路徑(CDP setDownloadBehavior 要求絕對路徑)。
# 與承辦中流程的 document_download/ 分開,結案存查獨立存放在 document_download_closure/
CLOSURE_DOWNLOAD_DIR = os.path.normpath(os.path.join(_PROJECT_ROOT, "document_download_closure"))

# 結案存查判定關鍵字 — 公文閱覽器右側「核決」意見區出現此字串,代表主管已核可,
# 才執行下載+結案存查動作。其他狀態(如還在簽核、意見有異)保守不動作。
_APPROVAL_KEYWORD = "如擬"


def _copy_summary_from_pending(closure_dir):
    """從 document_download/<同公文文號>/ 找 *總結*.md 複製到 closure_dir。

    結案存查時通常公文在承辦中流程已下載並摘要過(summarize_doc 產出
    「公文主檔名總結.<LLM 模型名>.md」)。把該摘要也歸檔到結案存查目錄,
    讓最終歸檔目錄一目了然(原始檔 + LLM 摘要),不必兩個資料夾翻來翻去。

    流程:
    1. closure_dir 的 basename = 公文文號(例 MWAA1156005236)
    2. pending_doc_handler.DOWNLOAD_DIR/<公文文號>/ 找 *總結*.md
    3. shutil.copy2 到 closure_dir(保留 mtime)

    回成功複製的檔數(>=0)。源目錄不存在或無命中檔案都回 0(印警告)。
    """
    from pending_doc_handler import DOWNLOAD_DIR as _PENDING_DIR

    doc_no = os.path.basename(os.path.normpath(closure_dir))
    src_dir = os.path.join(_PENDING_DIR, doc_no)
    if not os.path.isdir(src_dir):
        print(f"      [WARN] 找不到承辦中目錄 {src_dir},無 *總結*.md 可複製")
        return 0

    summary_files = glob.glob(os.path.join(src_dir, "*總結*.md"))
    if not summary_files:
        print(f"      [WARN] {src_dir} 內無 *總結*.md")
        return 0

    count = 0
    for src_path in summary_files:
        dst_path = os.path.join(closure_dir, os.path.basename(src_path))
        try:
            shutil.copy2(src_path, dst_path)
            print(f"      OK:複製 {os.path.basename(src_path)} → {closure_dir}")
            count += 1
        except OSError as e:
            print(f"      x  複製 {src_path} 失敗:{type(e).__name__}: {e}")
    return count


def _close_doc_viewer_window(driver):
    """關閉當前 focus 的公文閱覽器分頁,切回主(待結案清單)分頁。

    流程:
    1. 讀 window_handles;只剩 1 個就跳過(避免關掉唯一 window 把 driver 弄掛)
    2. 記下 current handle、切回去用的 other handle
    3. driver.close() 關當前 window
    4. switch_to.window(other) 切回主分頁

    成功 → True;失敗(讀 handles 例外 / 找不到其他 window / close 例外)→ False。
    """
    try:
        handles = driver.window_handles
    except Exception as e:
        print(f"      x  讀 window_handles 失敗:{type(e).__name__}: {e}")
        return False

    if len(handles) <= 1:
        print("      [INFO] 只剩 1 個 window,不關閉(避免 driver session 失效)")
        return True

    try:
        current = driver.current_window_handle
        other = next((h for h in handles if h != current), None)
        if other is None:
            print("      [WARN] 找不到其他 window,不關閉")
            return False
        driver.close()
        driver.switch_to.window(other)
        print("      OK:已關閉公文閱覽器分頁,切回主分頁")
        return True
    except Exception as e:
        print(f"      x  關閉分頁失敗:{type(e).__name__}: {e}")
        return False


def _delete_pending_archive(closure_dir):
    """刪除 document_download/<同公文文號>/ 目錄。

    呼叫時機:結案存查已歸檔到 closure_dir + *總結*.md 已成功複製過去。
    此時承辦中目錄內容已完全在結案目錄,刪除節省空間、避免重複。

    安全前提:呼叫端應先確認 _copy_summary_from_pending 回傳 > 0,代表
    承辦中目錄存在且 *總結*.md 已成功複製;否則不該呼叫本函式(會誤刪
    尚未歸檔的內容)。

    回 True 表示已刪除/不存在無需刪除;False 表示刪除失敗(rmtree 例外)。
    """
    from pending_doc_handler import DOWNLOAD_DIR as _PENDING_DIR

    doc_no = os.path.basename(os.path.normpath(closure_dir))
    src_dir = os.path.join(_PENDING_DIR, doc_no)
    if not os.path.isdir(src_dir):
        print(f"      [INFO] 承辦中目錄 {src_dir} 不存在,無需刪除")
        return True
    try:
        shutil.rmtree(src_dir)
        print(f"      OK:已刪除承辦中目錄 {src_dir}")
        return True
    except OSError as e:
        print(f"      x  刪除承辦中目錄失敗:{type(e).__name__}: {e}")
        return False


def _switch_to_doc_viewer_window(driver):
    """點待結案公文後，新分頁(公文閱覽器)會開啟，把 driver focus 切到非主 window。

    流程同 pending_doc_handler.handle_opened_document 的開頭：
    1. 讀 window_handles，>=2 才繼續
    2. 切到非 current 的那個 handle(預設只會有 1 個新分頁)

    成功 → driver focus 留在新公文閱覽器分頁，回 True
    失敗(只有 1 個 window / 切換例外) → 回 False
    """
    try:
        main_handle = driver.current_window_handle
        handles = driver.window_handles
    except Exception as e:
        print(f"[document_closure] 讀 window_handles 失敗：{type(e).__name__}: {e}")
        return False

    if len(handles) <= 1:
        print("[document_closure] 只有 1 個 window，沒偵測到公文閱覽器新分頁。")
        return False

    new_handle = next((h for h in handles if h != main_handle), None)
    if new_handle is None:
        print("[document_closure] 沒找到非主 window 的 handle。")
        return False

    try:
        driver.switch_to.window(new_handle)
    except Exception as e:
        print(f"[document_closure] 切 window 失敗：{type(e).__name__}: {e}")
        return False

    # 公文閱覽器載入 PDF + JS 需要幾秒
    time.sleep(2)
    try:
        print(f"[document_closure] 切到公文閱覽器分頁，URL={driver.current_url}")
        print(f"[document_closure] 標題={driver.title}")
    except Exception as e:
        print(f"[document_closure] 讀狀態失敗：{type(e).__name__}: {e}")
    return True


# 在當前 frame 內搜尋 keyword 的 JS:innerText 只看純文字節點,看不到 <input>
# 與 <textarea> 的 value(實測「如擬」是核決區的 input value,innerText 抓不到)。
# 補:also check element.value、value attr、title/placeholder/aria-label 屬性。
# 命中時連帶回傳命中來源(供 diagnostic),沒命中回 false。
_FIND_KEYWORD_JS = """
var kw = arguments[0];
if (document.body && document.body.innerText.indexOf(kw) !== -1) {
    return 'innerText';
}
// <input> / <textarea> 的 value(form field 內顯示的文字)
var fields = document.querySelectorAll('input, textarea, select');
for (var i = 0; i < fields.length; i++) {
    var v = fields[i].value || '';
    if (v.indexOf(kw) !== -1) return 'input.value';
}
// 屬性兜底:title / placeholder / aria-label / value attribute(有些 UI
// 把可見文字塞屬性)
var attrs = ['title', 'placeholder', 'aria-label', 'value', 'data-value'];
var all = document.querySelectorAll('*');
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    for (var j = 0; j < attrs.length; j++) {
        var a = el.getAttribute && el.getAttribute(attrs[j]);
        if (a && a.indexOf(kw) !== -1) return 'attr:' + attrs[j];
    }
}
return false;
"""


def _find_keyword_in_current_frame(driver, keyword):
    """在當前 frame 找 keyword(回傳命中來源字串或 None)。"""
    try:
        result = driver.execute_script(_FIND_KEYWORD_JS, keyword)
        return result if result else None
    except Exception:
        return None


def _has_approval_text(driver, keyword=_APPROVAL_KEYWORD, timeout=10):
    """檢查公文閱覽器頁面是否含結案存查判定關鍵字（預設「如擬」）。

    公文閱覽器是 iframe-based 排版，右側「承辦/會辦/核決」意見區的「如擬」實測
    是 <input> 的 value(不是純文字節點),innerText 抓不到。本函式用 _FIND_KEYWORD_JS
    同時檢查 innerText / form field value / 常見屬性。

    策略：每輪先檢查 top-level，再遍歷所有 iframe；每輪 0.5s 直到 timeout。
    命中時印命中來源(innerText / input.value / attr:xxx) + 哪個 frame，方便將來
    判斷失敗時除錯。

    回 True 表示頁面任一處有 keyword、False 表示 timeout 內都找不到。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        hit = _find_keyword_in_current_frame(driver, keyword)
        if hit:
            print(f"      OK：top-level frame 找到「{keyword}」(來源: {hit})")
            return True
        try:
            iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
        except Exception:
            iframes = []
        for ifr in iframes:
            try:
                name = ifr.get_attribute("name") or ifr.get_attribute("id") or "<unnamed>"
                driver.switch_to.default_content()
                driver.switch_to.frame(ifr)
                hit = _find_keyword_in_current_frame(driver, keyword)
                if hit:
                    print(f"      OK：frame '{name}' 找到「{keyword}」(來源: {hit})")
                    driver.switch_to.default_content()
                    return True
            except Exception:
                continue
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _dump_frames_diagnostic(driver, keyword=_APPROVAL_KEYWORD):
    """『如擬』找不到時，dump 每個 frame 的關鍵狀態：URL、body 前 200 字、
    input/textarea value 前幾個，方便看是哪個 frame 沒進去、或關鍵字實際長啥樣。
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    def _dump_here(label):
        try:
            info = driver.execute_script("""
                var inputs = document.querySelectorAll('input, textarea, select');
                var values = [];
                for (var i = 0; i < inputs.length && values.length < 8; i++) {
                    var v = (inputs[i].value || '').trim();
                    if (v) values.push(v.substring(0, 60));
                }
                return {
                    url: document.URL || '',
                    title: document.title || '',
                    body: document.body
                        ? document.body.innerText.substring(0, 200) : '',
                    inputs: values,
                };
            """) or {}
            print(f"      [{label}] URL = {(info.get('url') or '')[:120]}")
            print(f"             title = {info.get('title')!r}")
            print(f"             body 前 200 字 = {(info.get('body') or '')!r}")
            for j, v in enumerate(info.get('inputs') or []):
                print(f"             input[{j+1}] value = {v!r}")
        except Exception as e:
            print(f"      [{label}] dump 失敗：{type(e).__name__}: {e}")

    print(f"[document_closure] [diagnostic] 找不到「{keyword}」,dump 各 frame:")
    _dump_here("top")
    try:
        iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
    except Exception:
        iframes = []
    for i, ifr in enumerate(iframes):
        try:
            name = ifr.get_attribute("name") or ifr.get_attribute("id") or f"#{i+1}"
            driver.switch_to.default_content()
            driver.switch_to.frame(ifr)
            _dump_here(name)
        except Exception as e:
            print(f"      [#{i+1}] 進 frame 失敗：{type(e).__name__}: {e}")
    try:
        driver.switch_to.default_content()
    except Exception:
        pass


def process_document_closure(driver):
    """結案存查主流程。driver 必須已導航到 edoc 公文首頁。

    流程：
        1. 確認 current_url 在 edoc.gov.taipei
        2. 讀左側 sidebar「待結案(N)」數字
           - > 0：點進待結案清單，切到 dTreeContent frame，執行結案存查
           - = 0：印「無待結案公文，跳過」
           - 判讀失敗 (-1)：印警告，return False
        3. 切回 default_content
    回傳 True 表示流程跑完；False 表示前置檢查失敗。
    """
    from document_system import (
        _get_sidebar_paren_count,
        _click_sidebar_item,
        _switch_to_frame_with_xpath,
        _click_first_document_in_pending,
    )

    print("[document_closure] 開始結案存查流程...")

    try:
        current = driver.current_url
    except Exception as e:
        print(f"[ERROR] 讀 current_url 失敗：{type(e).__name__}: {e}")
        return False

    if "edoc.gov.taipei" not in current:
        print(f"[ERROR] 當前 URL 不在 edoc：{current}")
        return False

    # ── 待結案 ────────────────────────────────────────────────────────────
    print("[document_closure] 讀左側 sidebar「待結案」數...")
    count = _get_sidebar_paren_count(driver, "待結案")
    if count < 0:
        print("[document_closure] 無法判讀待結案數，保守不點，結束。")
        return False
    if count == 0:
        print("[document_closure] 待結案 = 0，無待辦，跳過。")
        return True

    print(f"[document_closure] 待結案 = {count}，點選進入...")
    if not _click_sidebar_item(driver, "待結案"):
        print("[document_closure] 點「待結案」失敗，請手動處理。")
        return False

    time.sleep(0.5)
    try:
        print(f"[document_closure] 待結案頁 URL：{driver.current_url}")
        print(f"[document_closure] 待結案頁標題：{driver.title}")
    except Exception as e:
        print(f"[document_closure] 讀狀態失敗：{type(e).__name__}: {e}")

    # ── 切到內容 frame ────────────────────────────────────────────────────
    # 待結案清單在 dTreeContent iframe 內，操作前必須切換 frame
    target_xpath = "//th[contains(normalize-space(), '公文文號')]"
    print("[document_closure] 切到 dTreeContent frame...")
    if not _switch_to_frame_with_xpath(driver, target_xpath, "待結案清單表頭"):
        print("[document_closure] 切不到內容 frame，請手動處理。")
        return False

    # ── 點待結案清單第一筆公文、記下文號 ───────────────────────────────
    # 待結案與承辦中共用同一張含「公文文號」欄的表格，重用 document_system 的
    # _click_first_document_in_pending（回傳公文文號字串）。記下文號供後續
    # 選擇「存查檔號」使用。
    print(f"[document_closure] 待結案清單共 {count} 筆，點選最上方第一筆...")
    doc_no = _click_first_document_in_pending(driver, label="待結案")
    if not doc_no:
        print("[document_closure] 點待結案公文失敗，請手動處理。")
        driver.switch_to.default_content()
        return False

    print("=" * 50)
    print(f"[document_closure] ★ 已選定待結案公文文號：{doc_no}")
    print("[document_closure] ★（此文號供後續選擇存查檔號使用）")
    print("=" * 50)

    # 點公文後系統開新分頁(公文閱覽器),切回主文件並切到新分頁
    driver.switch_to.default_content()
    time.sleep(1)
    if not _switch_to_doc_viewer_window(driver):
        print("[document_closure] 切不到公文閱覽器分頁，無法判定核決狀態，結束。")
        return False

    # ── 判定核決區是否有「如擬」 ───────────────────────────────────────
    print(f"[document_closure] 判定核決區是否有「{_APPROVAL_KEYWORD}」...")
    if not _has_approval_text(driver, timeout=10):
        print(f"[document_closure] 核決區未見「{_APPROVAL_KEYWORD}」，"
              "保守不下載(可能還在簽核或意見有異)。dump frame 內容供除錯:")
        _dump_frames_diagnostic(driver)
        print("[document_closure] 關閉公文閱覽器分頁...")
        _close_doc_viewer_window(driver)
        return True

    print(f"[document_closure] ✓ 核決區有「{_APPROVAL_KEYWORD}」，"
          f"進行結案存查下載到 {CLOSURE_DOWNLOAD_DIR}...")

    # ── 下載到 document_download_closure/ ───────────────────────────────
    # 重用 pending_doc_handler._download_and_extract,只是 download_dir 換成
    # CLOSURE_DOWNLOAD_DIR、不跑 summarize(已核可的公文不需 LLM 摘要)。
    from pending_doc_handler import _download_and_extract
    ok, extract_dir = _download_and_extract(
        driver, download_dir=CLOSURE_DOWNLOAD_DIR, summarize=False)
    if not ok:
        print("[document_closure] 結案存查下載/解壓縮失敗。")
        return False
    if extract_dir:
        print(f"[document_closure] ✓ 結案存查下載完成，解壓到 {extract_dir}")
        # 把承辦中流程已產生的 *總結*.md 一起歸檔到結案目錄
        print("[document_closure] 從承辦中目錄複製 *總結*.md 到結案目錄...")
        copied = _copy_summary_from_pending(extract_dir)
        # 已歸檔完成(總結也已複製)→ 刪除承辦中重複目錄。
        # 條件:copied > 0 才刪 — 沒總結可複製代表承辦中流程沒走完(或無 API key
        # 沒產 LLM 摘要),保留承辦中目錄供事後檢查比較安全。
        if copied > 0:
            print("[document_closure] 總結已歸檔,刪除承辦中重複目錄...")
            _delete_pending_archive(extract_dir)
        else:
            print("[document_closure] 未複製到總結,保留承辦中目錄供檢查(不刪除)。")
    else:
        print("[document_closure] ✓ 結案存查下載完成 (非 zip，原檔保留)")

    # 歸檔完成,關閉公文閱覽器分頁讓畫面回到待結案清單
    print("[document_closure] 關閉公文閱覽器分頁...")
    _close_doc_viewer_window(driver)

    # TODO: 依 doc_no 選擇存查檔號、送出結案存查表單
    print("[document_closure] TODO: 依文號選擇存查檔號、送出結案存查表單（尚未實作）")
    print("[document_closure] 結案存查流程結束。")
    return True


if __name__ == "__main__":
    # 把 stdout/stderr 同步落地到 run.log — entry point 開頭就 setup，確保
    # Chrome 預清理 / 啟動 / 導航每行 print 都進 log。
    from taipeion_login_selenium import _setup_stdout_logging
    from document_system import _standalone_open_chrome_at_edoc

    _setup_stdout_logging()
    driver = _standalone_open_chrome_at_edoc()
    if driver is None:
        sys.exit(1)
    ok = process_document_closure(driver)
    sys.exit(0 if ok else 1)
