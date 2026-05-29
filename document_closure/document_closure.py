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


def _copy_meta_files_from_pending(closure_dir):
    """從 document_download/<同公文文號>/ 找 *總結*.md + *內容.txt 複製到 closure_dir。

    結案存查時公文在承辦中流程通常已產生兩種 meta 檔(由 summarize_doc 產出):
    - *內容.txt:純文字版完整內容(發文日期/字號/主旨/說明/附件)
    - *總結.<LLM 模型名>.md:含分類標記 + LLM 摘要
    把兩類 meta 檔都歸檔到結案存查目錄,讓最終歸檔目錄包含完整脈絡(原始檔 +
    純文字內容 + LLM 摘要),不必兩個資料夾翻來翻去。

    流程:
    1. closure_dir basename = 公文文號(例 MWAA1156005236)
    2. pending_doc_handler.DOWNLOAD_DIR/<公文文號>/ glob *總結*.md + *內容.txt
    3. shutil.copy2 各檔到 closure_dir(保留 mtime)

    回成功複製的檔數(>=0)。源目錄不存在或無命中檔都印 WARN 回 0。
    """
    from pending_doc_handler import DOWNLOAD_DIR as _PENDING_DIR

    doc_no = os.path.basename(os.path.normpath(closure_dir))
    src_dir = os.path.join(_PENDING_DIR, doc_no)
    if not os.path.isdir(src_dir):
        print(f"      [WARN] 找不到承辦中目錄 {src_dir},無 meta 檔可複製")
        return 0

    # 收集 *總結*.md + *內容.txt;sorted 讓 log 順序可預測
    patterns = ["*總結*.md", "*內容.txt"]
    target_files = []
    for pat in patterns:
        target_files.extend(glob.glob(os.path.join(src_dir, pat)))
    target_files = sorted(target_files)

    if not target_files:
        print(f"      [WARN] {src_dir} 內無 *總結*.md 或 *內容.txt")
        return 0

    count = 0
    for src_path in target_files:
        dst_path = os.path.join(closure_dir, os.path.basename(src_path))
        try:
            shutil.copy2(src_path, dst_path)
            print(f"      OK:複製 {os.path.basename(src_path)} → {closure_dir}")
            count += 1
        except OSError as e:
            print(f"      x  複製 {src_path} 失敗:{type(e).__name__}: {e}")
    return count


# JS 用「公文文號」表頭 column-index 精確定位含 doc_no 的列,並回傳該列的
# checkbox web element。先前 XPath 版 (`//tr[.//td[contains(...)]]`) 實測抓到
# 錯的列(可能列文字含其他 doc 號的字串、或 td 內有 nested element 影響 contains
# 比對),改成 column-index 比對「公文文號」那一欄的值 === doc_no 才命中,最精準。
# 回 {ok, cb, rowText} 或 {ok: false, reason, diagnostics}。
_FIND_ROW_CHECKBOX_JS = r"""
var docNo = arguments[0];
// 找「公文文號」表頭 column index
var ths = document.querySelectorAll('th');
var docNoIdx = -1;
var docNoTh = null;
for (var i = 0; i < ths.length; i++) {
    var t = (ths[i].textContent || '').trim();
    if (t.indexOf('公文文號') === -1) continue;
    docNoTh = ths[i];
    var hr = ths[i].parentElement;
    for (var j = 0; j < hr.children.length; j++) {
        if (hr.children[j] === ths[i]) { docNoIdx = j; break; }
    }
    break;
}
if (docNoIdx === -1 || !docNoTh) {
    return {ok: false, reason: '找不到「公文文號」表頭'};
}
var table = docNoTh.closest('table');
if (!table) return {ok: false, reason: '找不到 table'};
var rows = table.querySelectorAll('tbody tr');
// diagnostic:列出每列的「公文文號」cell 文字,失敗時 print 出來
var rowSnapshot = [];
var matched = null;
var matchedRowText = '';
for (var k = 0; k < rows.length; k++) {
    var cells = rows[k].children;
    if (docNoIdx >= cells.length) continue;
    var cell = cells[docNoIdx];
    var txt = (cell.textContent || '').trim();
    rowSnapshot.push('[' + k + '] 公文文號 cell = ' + JSON.stringify(txt));
    if (matched === null && txt.indexOf(docNo) !== -1) {
        matched = rows[k];
        matchedRowText = txt;
    }
}
if (!matched) {
    return {ok: false, reason: '找不到含「' + docNo + '」的列',
            diagnostics: rowSnapshot};
}
var cb = matched.querySelector('input[type=checkbox]');
if (!cb) return {ok: false, reason: '該列無 checkbox',
                 diagnostics: rowSnapshot};
return {ok: true, cb: cb, rowText: matchedRowText, allRows: rowSnapshot};
"""


def _check_pending_closeout_row(driver, doc_no, timeout=10):
    """在待結案清單找含 doc_no 的列、勾選該列的 checkbox。

    流程:
    1. _switch_to_frame_with_xpath:切到含「公文文號」表頭的 frame(dTreeContent)
    2. _FIND_ROW_CHECKBOX_JS:JS column-index 精確定位列 + 抓 checkbox
    3. _try_check_checkbox:iCheck-相容的勾選策略
    4. 讀 JS verify 真的勾起來了(防 iCheck API 報成功但 UI 沒更新)

    為何用 JS column-index 而非 XPath:先前 XPath `//tr[.//td[contains(...)]]`
    實測抓到錯的列(可能某 cell 的子元素內容意外含其他 doc 號的子串)。column-index
    版本明確比對「公文文號」那一欄的值,排除其他欄位干擾。

    回 True = 已勾選並 verify 通過;False = 任一步失敗。
    """
    from document_system import _switch_to_frame_with_xpath, _try_check_checkbox

    sentinel_xpath = "//th[contains(normalize-space(), '公文文號')]"
    if not _switch_to_frame_with_xpath(driver, sentinel_xpath,
                                        "待結案清單(找「公文文號」表頭)",
                                        timeout=timeout):
        print("[ERROR] 找不到待結案清單 frame")
        return False

    try:
        result = driver.execute_script(_FIND_ROW_CHECKBOX_JS, doc_no)
    except Exception as e:
        print(f"[ERROR] JS find row checkbox 例外:{type(e).__name__}: {e}")
        return False

    if not result or not result.get('ok'):
        reason = (result or {}).get('reason', 'unknown')
        diag = (result or {}).get('diagnostics') or []
        print(f"[ERROR] JS 找列 checkbox 失敗:{reason}")
        for line in diag:
            print(f"        {line}")
        return False

    cb = result['cb']
    row_text = result.get('rowText', '')
    all_rows = result.get('allRows', [])
    print(f"      OK:JS 定位含「{doc_no}」的列(該列公文文號 cell = 「{row_text}」)")
    print(f"      [diag] 清單共 {len(all_rows)} 列,各列公文文號:")
    for line in all_rows:
        print(f"        {line}")

    if not _try_check_checkbox(driver, cb):
        print(f"[ERROR] 勾選「{doc_no}」列的 checkbox 失敗(_try_check_checkbox 回 False)")
        return False

    # Double check:JS 讀該列 checkbox 真的 checked(避免 iCheck API 報成功
    # 但實際 DOM state 沒更新、或勾到別的 checkbox 的 false positive)
    try:
        checked_now = driver.execute_script("return arguments[0].checked;", cb)
    except Exception:
        checked_now = None
    if not checked_now:
        print(f"[ERROR] 勾選後 cb.checked={checked_now} — 勾選未生效")
        return False
    print(f"      OK:cb.checked=True,勾選確認生效")
    return True


# 「存查」按鈕 XPath 候選 — 與 document_system.SIGNOFF_BUTTON_XPATHS 同套路:
# edoc 清單上方按鈕多半是 <input value="存查">,純文字搜尋抓不到,要 @value。
_ARCHIVE_BUTTON_XPATHS = [
    "//input[@value='存查']",
    "//*[@value='存查']",
    "//button[normalize-space()='存查']",
    "//a[normalize-space()='存查']",
    "//input[@type='button' and @value='存查']",
    "//input[@type='submit' and @value='存查']",
    "//*[normalize-space()='存查' and (self::button or @role='button')]",
    "//*[normalize-space()='存查']/ancestor::button[1]",
    "//*[normalize-space()='存查']/ancestor::a[1]",
]


def _click_archive_button(driver, timeout=15):
    """點清單上方的「存查」按鈕(_ARCHIVE_BUTTON_XPATHS 由窄到寬)。

    使用 JS click 繞遮罩,呼叫前 driver focus 應已在含按鈕的 frame
    (_check_pending_closeout_row 已切好)。

    成功 → True;timeout 內所有 XPath 都失敗 → False。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for xp in _ARCHIVE_BUTTON_XPATHS:
            try:
                els = driver.find_elements(By.XPATH, xp)
            except Exception:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'}); "
                        "arguments[0].click();", el)
                    print(f"      OK:點到「存查」按鈕(XPath: {xp})")
                    return True
                except Exception:
                    continue
        time.sleep(0.5)
    print("[ERROR] 找不到「存查」按鈕(全部 XPath 都失敗)")
    return False


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


def _search_keyword_in_all_frames(driver, keyword, timeout=10):
    """在 top-level + 所有 iframe 內搜尋 keyword(reuse _FIND_KEYWORD_JS,
    會檢 innerText、input/textarea/select.value、title/placeholder/aria-label
    等屬性)。

    每輪 0.5s 一次直到 timeout，讓非同步載入的內容也能命中。命中時印命中來源
    與 frame name,方便將來除錯。

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


def _has_approval_text(driver, keyword=_APPROVAL_KEYWORD, timeout=10):
    """檢查公文閱覽器頁面是否含結案存查判定關鍵字（預設「如擬」）。

    Delegate 到 _search_keyword_in_all_frames(通用搜尋)。本函式只是給「如擬」
    判定一個語義明確的入口。
    """
    return _search_keyword_in_all_frames(driver, keyword, timeout)


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
        # 把承辦中流程已產生的 meta 檔(*總結*.md + *內容.txt)一起歸檔到結案目錄
        print("[document_closure] 從承辦中目錄複製 *總結*.md + *內容.txt 到結案目錄...")
        copied = _copy_meta_files_from_pending(extract_dir)
        # 已歸檔完成 → 刪除承辦中重複目錄。
        # 條件:copied > 0 才刪 — 沒任何 meta 檔可複製代表承辦中流程沒走完
        # (或 LLM 不可用未產 meta 檔),保留承辦中目錄供事後檢查比較安全。
        if copied > 0:
            print(f"[document_closure] 已歸檔 {copied} 個 meta 檔,刪除承辦中重複目錄...")
            _delete_pending_archive(extract_dir)
        else:
            print("[document_closure] 未複製到任何 meta 檔,保留承辦中目錄供檢查(不刪除)。")
    else:
        print("[document_closure] ✓ 結案存查下載完成 (非 zip，原檔保留)")

    # 歸檔完成,關閉公文閱覽器分頁讓畫面回到待結案清單
    print("[document_closure] 關閉公文閱覽器分頁...")
    _close_doc_viewer_window(driver)

    # ── 待結案清單:勾選同公文號的列、點「存查」、驗證存查表單公文號 ─────
    print(f"[document_closure] 在待結案清單找「{doc_no}」的列、勾選...")
    if not _check_pending_closeout_row(driver, doc_no):
        print("[document_closure] 勾選失敗,跳過存查表單流程(歸檔已成功)。")
        return True

    print("[document_closure] 點「存查」按鈕...")
    if not _click_archive_button(driver):
        print("[document_closure] 點「存查」失敗,跳過存查表單流程(歸檔已成功)。")
        return True

    # 驗證表單真的載入了 — 用「確定存檔」(只在存查表單出現的按鈕)當 sentinel,
    # 不能只用 doc_no(清單頁本就含 doc_no,verify 會偽陽性,先前實測就是中這招)。
    print("[document_closure] 等存查表單載入(找 sentinel「確定存檔」)...")
    if not _search_keyword_in_all_frames(driver, "確定存檔", timeout=15):
        print("[ERROR] 存查表單未載入(找不到「確定存檔」按鈕) — 「存查」點擊可能未生效。"
              "保持視窗不關閉,請手動檢查。")
        return False

    # 表單已載入,再驗證表單上的 doc_no 是否 = 剛勾選的 doc_no
    print(f"[document_closure] 表單已載入,驗證公文文號 = 「{doc_no}」...")
    if not _search_keyword_in_all_frames(driver, doc_no, timeout=3):
        print(f"[ERROR] 存查表單上看不到公文文號「{doc_no}」 — "
              "可能系統開錯表單(勾錯列了)。保持視窗不關閉,請手動檢查。")
        return False

    print(f"[document_closure] ✓ 存查表單已載入且公文文號確認 = 「{doc_no}」")

    # TODO: 依存檔層級/案次號/檔號 等欄位填表 + 點「確定存檔」
    print("[document_closure] TODO: 填存查表單欄位、點「確定存檔」（尚未實作）")
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
