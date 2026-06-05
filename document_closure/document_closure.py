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
import re
import shutil
import sys
import time
from datetime import datetime

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


# #存查分類: 行的 8 位檔號 regex。spec 規範:
#   #存查分類:<分類文字> <8位數字>
# 例:`#存查分類:資安 03750402`
# 為防 LLM 偶有空白/全形冒號變異,放寬冒號接受「:或：」+ 任意空白;
# 8 位數字必須精確 8 碼(spec 的對應表就是 8 碼:0375040x)。
_ARCHIVE_CATEGORY_LINE_RE = re.compile(
    r'#\s*存查分類\s*[:：]\s*\S+\s+(\d{8})'
)
# fallback:若 LLM 漏寫分類文字、只放數字
_ARCHIVE_CATEGORY_FALLBACK_RE = re.compile(
    r'#\s*存查分類\s*[:：]\s*(\d{8})'
)


def _read_archive_category_from_summary(doc_no):
    """從 document_download_closure/<doc_no>/*總結.*.md 讀 #存查分類: 的 8 位檔號。

    summarize_doc.md 規格:總結檔最上方一行為 #存查分類:<分類文字> <8位檔號>。
    本函式讀檔、regex 抓 8 位數字。

    回字串(成功;如 '03750402')或 None(找不到目錄/檔/格式不符)。
    """
    src_dir = os.path.join(CLOSURE_DOWNLOAD_DIR, doc_no)
    if not os.path.isdir(src_dir):
        print(f"      [ERROR] 找不到結案目錄 {src_dir}")
        return None
    summary_files = sorted(glob.glob(os.path.join(src_dir, "*總結.*.md")))
    if not summary_files:
        print(f"      [ERROR] {src_dir} 內無 *總結.*.md")
        return None
    md_path = summary_files[0]
    try:
        text = open(md_path, encoding='utf-8').read()
    except OSError as e:
        print(f"      [ERROR] 讀 {md_path} 失敗:{type(e).__name__}: {e}")
        return None
    m = _ARCHIVE_CATEGORY_LINE_RE.search(text) or _ARCHIVE_CATEGORY_FALLBACK_RE.search(text)
    if not m:
        print(f"      [ERROR] {os.path.basename(md_path)} 內找不到 #存查分類:... <8位數字>")
        # 印開頭 5 行供除錯
        for i, line in enumerate(text.splitlines()[:5]):
            print(f"           line{i+1}: {line!r}")
        return None
    cat = m.group(1)
    print(f"      OK:從 {os.path.basename(md_path)} 讀到 #存查分類 檔號 = {cat}")
    return cat


# 填表 JS:找 value '0115' 的 input(spec 用語為「檔號」第一格)再取它的「下一個」
# input(同 parent 內第二個 text input)填 category。用 native setter 繞 React/Vue
# 攔截過的 value setter,並 dispatch input + change 觸發框架的 dirty/validation。
_FILL_CATEGORY_JS = r"""
var cat = arguments[0];
var inputs = document.querySelectorAll(
    'input[type=text], input[type=""], input:not([type])');
function setValue(target, val) {
    var desc = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value');
    if (desc && desc.set) {
        desc.set.call(target, val);
    } else {
        target.value = val;
    }
    target.dispatchEvent(new Event('input', {bubbles: true}));
    target.dispatchEvent(new Event('change', {bubbles: true}));
}
for (var i = 0; i < inputs.length; i++) {
    var first = inputs[i];
    if ((first.value || '').trim() !== '0115') continue;
    // 嘗試 1:同 parent 內 input 序列的下一個
    var parent = first.parentElement;
    if (parent) {
        var sib = parent.querySelectorAll(
            'input[type=text], input[type=""], input:not([type])');
        for (var j = 0; j < sib.length - 1; j++) {
            if (sib[j] === first) {
                setValue(sib[j+1], cat);
                return {ok: true, where: 'parent siblings',
                        newValue: sib[j+1].value};
            }
        }
    }
    // 嘗試 2:document.querySelectorAll 序列的下一個
    if (i + 1 < inputs.length) {
        setValue(inputs[i+1], cat);
        return {ok: true, where: 'global next', newValue: inputs[i+1].value};
    }
    return {ok: false, reason: '找到 value=0115 的 input 但無 next input'};
}
return {ok: false, reason: '找不到 value=0115 的 input'};
"""


def _fill_archive_form_category_input(driver, category_num, timeout=10):
    """把 8 位 category_num 填到存查表單「檔號」第一格(0115)右側的第二個 input。

    DOM 結構未知,策略:先嘗試 top-level,再遍歷所有 iframe;在每個 frame 內跑
    _FILL_CATEGORY_JS:找 value '0115' 的 input → 取它的下一個 input → set value
    + fire input/change。成功即停。

    成功 → True(driver focus 留在成功填到的 frame,後續若有再填的動作可接續);
    失敗 → False(印 diagnostic)。
    """
    deadline = time.time() + timeout
    last_reason = None
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        try:
            r = driver.execute_script(_FILL_CATEGORY_JS, category_num)
            if r and r.get('ok'):
                print(f"      OK:top-level 填「{category_num}」(策略: {r.get('where')}, "
                      f"newValue={r.get('newValue')!r})")
                return True
            if r:
                last_reason = r.get('reason')
        except Exception as e:
            last_reason = f"JS 例外:{type(e).__name__}: {e}"
        try:
            iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
        except Exception:
            iframes = []
        for ifr in iframes:
            try:
                name = ifr.get_attribute("name") or ifr.get_attribute("id") or "?"
                driver.switch_to.default_content()
                driver.switch_to.frame(ifr)
                r = driver.execute_script(_FILL_CATEGORY_JS, category_num)
                if r and r.get('ok'):
                    print(f"      OK:frame '{name}' 填「{category_num}」(策略: "
                          f"{r.get('where')}, newValue={r.get('newValue')!r})")
                    return True
                if r:
                    last_reason = f"frame '{name}': {r.get('reason')}"
            except Exception:
                continue
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        time.sleep(0.5)
    print(f"      [ERROR] 所有 frame 都填不到「檔號」第二格 input(last reason: {last_reason})")
    return False


# 用 label 文字定位表單欄位的工具 JS — 給後續所有「找 案次號 / 保存年限 / 密等
# 等 label 旁的 select / input」共用,避免每次都複製貼上 DOM 走訪邏輯。
_LABEL_LOOKUP_HELPERS_JS = r"""
function findLabelLeafByText(text) {
    // 葉子驗證:找含 text 的最小元素(本身文字 <= 20 字、且沒子元素也含 text)
    var all = document.querySelectorAll('label, span, div, td, th, p, b, strong');
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        var t = (el.textContent || '').trim();
        if (t.length > 20 || t.indexOf(text) === -1) continue;
        var inner = el.querySelectorAll('*');
        var hasChild = false;
        for (var j = 0; j < inner.length; j++) {
            var st = (inner[j].textContent || '').trim();
            if (st.indexOf(text) !== -1 && st.length <= 20) {
                hasChild = true; break;
            }
        }
        if (hasChild) continue;
        return el;
    }
    return null;
}
function nearestControl(labelEl, selector) {
    // 由 label 往父層走最多 5 層,找第一個符合 selector 的控制元素
    if (!labelEl) return null;
    var node = labelEl.parentElement;
    for (var d = 0; d < 5 && node; d++) {
        var c = node.querySelector(selector);
        if (c) return c;
        node = node.parentElement;
    }
    return null;
}
"""

# 選「案次號」+ 讀「保存年限」 JS。
# 案次號 select 在使用者填好 檔號 後,系統會 dynamically populate options。
# 我們選 selectedIndex = 1(跳過 index 0 的「請選擇」placeholder)、dispatch change,
# 系統的 change handler 應該自動填「保存年限」。回傳當下讀到的值。
_SELECT_CASE_NO_JS = _LABEL_LOOKUP_HELPERS_JS + r"""
var caseSel = nearestControl(findLabelLeafByText('案次號'), 'select');
if (!caseSel) return {ok: false, reason: '找不到 案次號 select'};
if (caseSel.options.length <= 1) {
    return {ok: false, reason: '案次號 options 未載入 (option count=' +
            caseSel.options.length + ')'};
}
caseSel.selectedIndex = 1;
caseSel.dispatchEvent(new Event('change', {bubbles: true}));
var optionText = caseSel.options[1].text;

var retInput = nearestControl(findLabelLeafByText('保存年限'), 'input');
var retVal = retInput ? (retInput.value || '').trim() : '';
return {ok: true, optionText: optionText, retention: retVal};
"""

# 只讀「保存年限」值用的 JS(系統可能 async 填入,主流程 poll 時用)。
_READ_RETENTION_JS = _LABEL_LOOKUP_HELPERS_JS + r"""
var retInput = nearestControl(findLabelLeafByText('保存年限'), 'input');
return retInput ? (retInput.value || '').trim() : '';
"""


def _select_case_no_and_read_retention(driver, timeout=10, retention_poll_s=2.0):
    """選「案次號」第一個非 placeholder option、確認系統自動填「保存年限」。

    流程:
    1. 遍歷 top + 所有 iframe 跑 _SELECT_CASE_NO_JS
    2. 命中後:若回傳的 retention 為空,poll 該 frame 內「保存年限」值最多
       retention_poll_s 秒(系統可能 async 填)
    3. 印選到的 option text 與 保存年限 值

    成功 → True (即使 retention 仍空也回 True,讓後續步驟自己決定是否容忍);
    失敗 → False。
    """
    deadline = time.time() + timeout
    last_reason = None
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        frames_to_try = [None]
        try:
            frames_to_try += driver.find_elements(By.XPATH, "//iframe | //frame")
        except Exception:
            pass
        for ifr in frames_to_try:
            try:
                if ifr is None:
                    driver.switch_to.default_content()
                    frame_label = "top"
                else:
                    name = ifr.get_attribute("name") or ifr.get_attribute("id") or "?"
                    driver.switch_to.default_content()
                    driver.switch_to.frame(ifr)
                    frame_label = f"frame '{name}'"
                r = driver.execute_script(_SELECT_CASE_NO_JS)
            except Exception as e:
                last_reason = f"{type(e).__name__}: {e}"
                continue
            if not r or not r.get('ok'):
                if r:
                    last_reason = r.get('reason')
                continue
            print(f"      OK:{frame_label} 選到「{r.get('optionText')}」")
            retention = r.get('retention') or ''
            # 系統可能 async 填入「保存年限」,給點時間 poll
            if not retention:
                end = time.time() + retention_poll_s
                while time.time() < end:
                    time.sleep(0.1)
                    try:
                        retention = (driver.execute_script(_READ_RETENTION_JS) or '').strip()
                    except Exception:
                        retention = ''
                    if retention:
                        break
            if retention:
                print(f"      OK:「保存年限」= {retention}")
            else:
                print("      [WARN] 「保存年限」載入後仍為空 — 後續若必填可能會擋")
            return True
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        time.sleep(0.5)
    print(f"      [ERROR] 案次號選取失敗(last reason: {last_reason})")
    return False


# 「確定存檔」按鈕 XPath 候選 — 同 _ARCHIVE_BUTTON_XPATHS 套路
_CONFIRM_SAVE_BUTTON_XPATHS = [
    "//input[@value='確定存檔']",
    "//*[@value='確定存檔']",
    "//button[normalize-space()='確定存檔']",
    "//a[normalize-space()='確定存檔']",
    "//input[@type='button' and @value='確定存檔']",
    "//input[@type='submit' and @value='確定存檔']",
    "//*[normalize-space()='確定存檔' and (self::button or @role='button')]",
    "//*[normalize-space()='確定存檔']/ancestor::button[1]",
    "//*[normalize-space()='確定存檔']/ancestor::a[1]",
]


def _click_confirm_save_button(driver, timeout=10):
    """點存查表單上方的「確定存檔」按鈕。

    **重要**:此按鈕送出表單、把公文真正存查歸檔,無 admin 介入無法復原。
    呼叫端必須先 verify 必填欄位齊備、確認 doc_no 正確,再呼叫本函式。

    成功 → True;失敗 → False。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        for xp in _CONFIRM_SAVE_BUTTON_XPATHS:
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
                    print(f"      OK:點到「確定存檔」按鈕(XPath: {xp})")
                    return True
                except Exception:
                    continue
        time.sleep(0.5)
    print("[ERROR] 找不到「確定存檔」按鈕(全部 XPath 都失敗)")
    return False


# 一次讀回三必填欄位的 JS
_READ_REQUIRED_FIELDS_JS = _LABEL_LOOKUP_HELPERS_JS + r"""
function getArchiveCategoryValue() {
    // 找 value '0115' input、回它「下一個」input 的 value
    var inputs = document.querySelectorAll(
        'input[type=text], input[type=""], input:not([type])');
    for (var i = 0; i < inputs.length; i++) {
        if ((inputs[i].value || '').trim() !== '0115') continue;
        var parent = inputs[i].parentElement;
        if (parent) {
            var sib = parent.querySelectorAll(
                'input[type=text], input[type=""], input:not([type])');
            for (var j = 0; j < sib.length - 1; j++) {
                if (sib[j] === inputs[i]) {
                    return (sib[j+1].value || '').trim();
                }
            }
        }
        if (i + 1 < inputs.length) return (inputs[i+1].value || '').trim();
        return '';
    }
    return '';
}
var arch = getArchiveCategoryValue();
var caseSel = nearestControl(findLabelLeafByText('案次號'), 'select');
var caseVal = caseSel ? (caseSel.value || '').trim() : '';
var caseText = caseSel && caseSel.selectedIndex >= 0
    ? (caseSel.options[caseSel.selectedIndex].text || '').trim() : '';
var retInput = nearestControl(findLabelLeafByText('保存年限'), 'input');
var retVal = retInput ? (retInput.value || '').trim() : '';
return {arch: arch, caseVal: caseVal, caseText: caseText, retention: retVal};
"""


def _verify_required_fields_filled(driver, expected_category, timeout=5):
    """確認必填欄位齊備:檔號(第二格)= expected_category、案次號已選非 placeholder
    option。任一未齊備 → return False。

    保存年限由系統依案次號自動填入,不在驗證範圍(讀出來只給 log 看)。

    遍歷 top + iframe,任一 frame 都符合就 True。printf 印 diagnostic 值
    讓 log 可看到實際讀到啥。
    """
    deadline = time.time() + timeout
    last_snapshot = None
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        frames = [None]
        try:
            frames += driver.find_elements(By.XPATH, "//iframe | //frame")
        except Exception:
            pass
        for ifr in frames:
            try:
                if ifr is not None:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(ifr)
                r = driver.execute_script(_READ_REQUIRED_FIELDS_JS)
            except Exception:
                continue
            if not r:
                continue
            arch = r.get('arch', '')
            case_text = r.get('caseText', '')
            ret = r.get('retention', '')
            last_snapshot = r
            # 案次號:顯示文字不能是「請選擇」placeholder、必須有其他文字
            # (不依賴 select.value — 部分系統 option 未設 value 屬性時 value 可能空)
            case_ok = bool(case_text) and case_text != '請選擇'
            if arch == expected_category and case_ok:
                # 保存年限 不在驗證範圍,只印出來供 log 參考
                print(f"      OK:必填欄位齊備 — 檔號={arch}, 案次號={case_text!r}"
                      f"(保存年限={ret!r},僅供參考、不驗證)")
                return True
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        time.sleep(0.3)
    print(f"[ERROR] 必填欄位未齊備,最後讀到:{last_snapshot!r}")
    print(f"        期望:檔號={expected_category!r}、案次號 != '請選擇'")
    return False


# pinCode input XPath 候選(KdApp localhost:16888/doPostMsg popup)
_PINCODE_INPUT_XPATHS = [
    "//input[@id='pinCode']",
    "//input[@name='pinCode']",
    "//input[contains(@placeholder, 'pinCode')]",
    "//input[contains(@placeholder, 'PIN')]",
    "//input[@type='password']",
    "//input[@type='text']",  # fallback
]

# pinCode popup 內「確定」按鈕 XPath 候選
_PINCODE_CONFIRM_XPATHS = [
    "//button[normalize-space()='確定']",
    "//input[@value='確定']",
    "//*[normalize-space()='確定' and (self::button or @role='button')]",
    "//a[normalize-space()='確定']",
    "//*[@value='確定']",
]


def _fill_pincode_robust(driver, pincode_input, pin):
    """多策略填 PIN — Chrome popup 剛開時 send_keys 常因 input 還沒 interactable
    而拋 InvalidElementStateException;改用多策略提升成功率。

    策略順序(任一成功立即回 True):
    1. JS focus → Selenium clear + send_keys
    2. Selenium click → clear + send_keys
    3. 純 JS:focus → native setter → dispatch input/change(繞 framework value tracker)

    每策略後讀回 el.value 比對,值符合才視為成功。全部失敗 → False。
    """
    # Strategy 1: JS focus + Selenium send_keys
    try:
        driver.execute_script("arguments[0].focus();", pincode_input)
        time.sleep(0.2)
        pincode_input.clear()
        pincode_input.send_keys(pin)
        v = driver.execute_script("return arguments[0].value;", pincode_input) or ''
        if v == pin:
            print("      OK:已填入 PIN(策略: JS focus + send_keys)")
            return True
    except Exception as e:
        print(f"      [WARN] JS focus + send_keys 失敗:{type(e).__name__}: {str(e)[:120]}")

    # Strategy 2: click + send_keys
    try:
        pincode_input.click()
        time.sleep(0.2)
        pincode_input.clear()
        pincode_input.send_keys(pin)
        v = driver.execute_script("return arguments[0].value;", pincode_input) or ''
        if v == pin:
            print("      OK:已填入 PIN(策略: click + send_keys)")
            return True
    except Exception as e:
        print(f"      [WARN] click + send_keys 失敗:{type(e).__name__}: {str(e)[:120]}")

    # Strategy 3: 純 JS native setter(避開 Selenium element interactability 檢查)
    try:
        ok = driver.execute_script("""
            var el = arguments[0];
            var v = arguments[1];
            el.focus();
            var desc = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value');
            if (desc && desc.set) {
                desc.set.call(el, v);
            } else {
                el.value = v;
            }
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return el.value === v;
        """, pincode_input, pin)
        if ok:
            print("      OK:已填入 PIN(策略: JS native setter)")
            return True
    except Exception as e:
        print(f"      [WARN] JS native setter 失敗:{type(e).__name__}: {str(e)[:120]}")

    return False


def _handle_pincode_popup(driver, popup_timeout=15, close_timeout=20):
    """處理「確定存檔」後跳出的 pinCode 視窗(URL: localhost:16888/doPostMsg)。

    流程(全自動,任一步失敗就停):
    1. 等新 window 出現(popup_timeout 秒),switch 過去
    2. 讀 PIN from env.env(失敗 → 停)
    3. 找 pinCode input(失敗 → 停)
    4. _fill_pincode_robust 多策略填入 PIN(全部失敗 → 停)
    5. 點「確定」(失敗 → 停)
    6. 等 popup 關閉(close_timeout)

    回 True 表示 popup 已關閉(系統完成簽章);任一步失敗 → False,
    popup 保留供使用者手動完成,但主流程結束、不會走後續寫存查標記。
    """
    from taipeion_login_selenium import _read_pin

    try:
        original_handle = driver.current_window_handle
        original_handles = set(driver.window_handles)
    except Exception as e:
        print(f"[ERROR] 讀 window_handles 失敗:{type(e).__name__}: {e}")
        return False

    # Fast-path:先掃既有 window,若已有 localhost:16888 的 popup 直接接手。
    # (4-2 陳會場景:陳會點下去後 popup 可能在本函式被呼叫前就開了;
    #  4-結案存查場景:popup 通常是本函式內等的「新 window」,fast-path 不會命中。)
    new_handle = None
    for h in list(original_handles):
        try:
            driver.switch_to.window(h)
            if "16888" in (driver.current_url or ""):
                new_handle = h
                print(f"      OK:接手既有 pinCode 對話框 url={driver.current_url}")
                break
        except Exception:
            continue
    if not new_handle:
        # 切回原 window 後再等新 popup,避免 next loop 在錯誤 handle 上跑
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass
        # 等新 window 出現
        deadline = time.time() + popup_timeout
        while time.time() < deadline:
            try:
                handles = driver.window_handles
            except Exception:
                time.sleep(0.3)
                continue
            diff = set(handles) - original_handles
            if diff:
                new_handle = next(iter(diff))
                break
            time.sleep(0.3)
    if not new_handle:
        print(f"[ERROR] {popup_timeout}s 內沒有偵測到 pinCode 視窗")
        return False

    try:
        driver.switch_to.window(new_handle)
        time.sleep(0.5)
        cur_url = driver.current_url
        print(f"      OK:切到 pinCode 視窗 URL={cur_url}")
    except Exception as e:
        print(f"[ERROR] 切到 pinCode 視窗失敗:{type(e).__name__}: {e}")
        return False

    if "16888" not in cur_url:
        print(f"      [WARN] 視窗 URL 不像 pinCode popup,繼續嘗試:{cur_url}")

    pin = _read_pin()
    if not pin:
        print("[ERROR] 讀不到 PIN(env.env 不存在/無 pin=),popup 保留供手動處理。")
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass
        return False

    # 找 pinCode input — timeout 拉長到 15s,popup 從 about:blank 切到實際 form
    # 可能要幾秒,DOM ready 的時機難精準預測
    pincode_input = None
    input_deadline = time.time() + 15
    while time.time() < input_deadline and not pincode_input:
        for xp in _PINCODE_INPUT_XPATHS:
            try:
                els = driver.find_elements(By.XPATH, xp)
            except Exception:
                continue
            for el in els:
                try:
                    if el.is_displayed():
                        pincode_input = el
                        print(f"      OK:找到 pinCode input(XPath: {xp})")
                        break
                except Exception:
                    continue
            if pincode_input:
                break
        if not pincode_input:
            time.sleep(0.3)

    if not pincode_input:
        print("[ERROR] 找不到 pinCode input,popup 保留供手動處理。")
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass
        return False

    if not _fill_pincode_robust(driver, pincode_input, pin):
        print("[ERROR] 所有自動填 PIN 策略都失敗,停止流程。popup 保留,請手動完成。")
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass
        return False

    # 點「確定」
    clicked = False
    for xp in _PINCODE_CONFIRM_XPATHS:
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
                print(f"      OK:點到 pinCode「確定」(XPath: {xp})")
                clicked = True
                break
            except Exception:
                continue
        if clicked:
            break
    if not clicked:
        print("[ERROR] 找不到 pinCode 視窗「確定」按鈕,popup 保留供手動處理。")
        try:
            driver.switch_to.window(original_handle)
        except Exception:
            pass
        return False

    # 等 popup 關閉
    close_deadline = time.time() + close_timeout
    popup_closed = False
    while time.time() < close_deadline:
        try:
            current_handles = driver.window_handles
        except Exception:
            current_handles = []
        if new_handle not in current_handles:
            popup_closed = True
            print("      OK:pinCode 視窗已關閉(系統完成簽章)")
            break
        time.sleep(0.3)
    if not popup_closed:
        print(f"      [WARN] pinCode 視窗 {close_timeout}s 內沒關閉,可能仍處理中")

    # 切回主 window
    try:
        if original_handle in driver.window_handles:
            driver.switch_to.window(original_handle)
            print("      OK:切回主 window")
    except Exception as e:
        print(f"      [WARN] 切回主 window 失敗:{type(e).__name__}: {e}")
    return True


# 找 doc_no 在可見 <tr>(待結案清單的列)的 JS — 比通用 _FIND_KEYWORD_JS 更精確:
# 通用版會掃 innerText + 所有 input.value + 所有 attribute,即使 doc_no 已從清單
# 消失,仍可能誤命中(系統殘留的 hidden form field、attribute、system message 等),
# 導致 verify 誤判失敗。
# 本 JS 只看「可見 <tr>」是否含 doc_no — 與使用者視覺感知一致(看不到 row 就算成功)。
_FIND_DOC_IN_VISIBLE_ROW_JS = r"""
var docNo = arguments[0];
var trs = document.querySelectorAll('tr');
for (var i = 0; i < trs.length; i++) {
    var tr = trs[i];
    if (tr.offsetParent === null) continue;  // 不可見跳過
    var t = (tr.textContent || '').trim();
    if (t.indexOf(docNo) !== -1) return true;
}
return false;
"""


def _verify_archive_success_by_listing(driver, doc_no, timeout=20):
    """確認 doc_no 已從「待結案」清單可見列消失 → 存查成功。

    pinCode + popup 關閉後系統會刷新清單;成功歸檔的公文應從清單消失。
    只看「可見 <tr>」是否含 doc_no — 與視覺感知一致;不掃 hidden form fields
    / attributes / 系統訊息(會誤命中)。

    流程:每 1s 在 top + iframe 跑 _FIND_DOC_IN_VISIBLE_ROW_JS;找不到 → 成功。
    timeout 內始終找到 → 印 WARN 回 False。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        found = False
        # top-level
        try:
            if driver.execute_script(_FIND_DOC_IN_VISIBLE_ROW_JS, doc_no):
                found = True
        except Exception:
            pass
        # iframes
        if not found:
            try:
                iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
            except Exception:
                iframes = []
            for ifr in iframes:
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(ifr)
                    if driver.execute_script(_FIND_DOC_IN_VISIBLE_ROW_JS, doc_no):
                        found = True
                        break
                except Exception:
                    continue
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
        if not found:
            print(f"      OK:doc_no「{doc_no}」已從待結案清單可見列消失,存查成功")
            return True
        time.sleep(1)
    print(f"      [WARN] {timeout}s 內 doc_no「{doc_no}」仍在某個可見列,可能存查未成功")
    return False


# 公文主檔名 regex — 同 summarize_doc._MAIN_DOC_PATTERN(数字_数字[A-Z]?)
_MAIN_DOC_BASENAME_RE = re.compile(r'^(\d+_\d+[A-Z]?)')


def _find_main_doc_basename(closure_dir):
    """從 closure_dir 找公文主檔名(spec:数字_数字[A-Z]?,如 28708231_1150050003)。

    優先順序:
    1. *內容.txt(命名為「公文主檔名內容.txt」)→ 去掉「內容.txt」後綴
    2. 主檔 PDF (`數字_數字[A-Z]?.pdf`)→ 去掉「.pdf」後綴

    沒找到回 None。
    """
    # 先看 *內容.txt
    for p in sorted(glob.glob(os.path.join(closure_dir, "*內容.txt"))):
        m = _MAIN_DOC_BASENAME_RE.match(os.path.basename(p))
        if m:
            return m.group(1)
    # fallback: 主檔 PDF
    for p in sorted(glob.glob(os.path.join(closure_dir, "*.pdf"))):
        name = os.path.basename(p)
        m = re.match(r'^(\d+_\d+[A-Z]?)\.pdf$', name)
        if m:
            return m.group(1)
    return None


def _write_archive_marker(closure_dir):
    """在 closure_dir 寫存查完成標記檔 `<公文主檔名>已存查.txt`。

    內容為 ISO 8601 日期時間 + `_存查`(如 `2026-05-30T14:23:45_存查`),
    讓人類與 grep 都能識別「這份公文何時完成存查」。檔名不含日期 —
    一個公文只會存查一次,固定後綴「已存查」即可。

    成功 → 回檔案路徑;找不到主檔名/寫檔失敗 → None。
    """
    if not os.path.isdir(closure_dir):
        print(f"      [ERROR] closure 目錄不存在:{closure_dir}")
        return None
    main_basename = _find_main_doc_basename(closure_dir)
    if not main_basename:
        print(f"      [ERROR] {closure_dir} 內找不到公文主檔(*內容.txt 或 數字_數字[A-Z]?.pdf)")
        return None

    iso_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fname = f"{main_basename}已存查.txt"
    out_path = os.path.join(closure_dir, fname)
    content = f"{iso_str}_存查"
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"      OK:已寫存查標記檔 {fname}(內容: {content!r})")
        return out_path
    except OSError as e:
        print(f"      [ERROR] 寫標記檔失敗:{type(e).__name__}: {e}")
        return None


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


def process_document_closure(driver, max_iterations=30):
    """迴圈處理「待結案」清單,直到清空或失敗。

    每輪先讀 sidebar「待結案(N)」:
    - N = 0:全部處理完畢,return True(畫面停在待結案清單)
    - N < 0:無法判讀(可能 driver 在錯的頁面),return False
    - N > 0:呼叫 _process_one_pending_closure_doc 處理第一筆;成功則繼續下一輪,
            失敗則 return False(剩餘交給使用者手動)

    max_iterations(預設 30)是 runaway 保險:若 count 因某種理由沒下降,
    達到上限就強制停止,避免無限迴圈。

    給 standalone __main__、main.py FEATURES[2]、document_system.pending_closeout_doc
    delegate 共用同一入口。
    """
    from document_system import _get_sidebar_paren_count

    for i in range(1, max_iterations + 1):
        count = _get_sidebar_paren_count(driver, "待結案")
        if count == 0:
            print(f"\n[document_closure] ✓ 待結案 = 0,全部處理完畢(共跑了 {i - 1} 輪)")
            return True
        if count < 0:
            print("\n[document_closure] [ERROR] 無法判讀待結案數,中止迴圈")
            return False
        print(f"\n[document_closure] ═══ 第 {i} 輪(待結案剩 {count} 筆)═══")
        ok = _process_one_pending_closure_doc(driver)
        if not ok:
            print(f"\n[document_closure] 第 {i} 輪失敗,中止迴圈(剩 {count} 筆未處理,"
                  "請手動處理或重跑)")
            return False
    print(f"\n[document_closure] [WARN] 達到 max_iterations={max_iterations},強制停止")
    return False


def _process_one_pending_closure_doc(driver):
    """處理「待結案」清單第一筆公文(單筆)。driver 必須已導航到 edoc 公文首頁。

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

    # 從結案目錄總結讀 #存查分類: 的 8 位檔號(由 summarize_doc 依規格產生)
    print(f"[document_closure] 從結案目錄 *總結.*.md 讀 #存查分類 檔號...")
    category = _read_archive_category_from_summary(doc_no)
    if not category:
        print("[ERROR] 讀不到分類檔號(*總結.*.md 缺檔或格式不符),保持視窗,後續中止。")
        return False

    # 填到「檔號」第二格(0115 右側那格)
    print(f"[document_closure] 把檔號「{category}」填到表單 檔號 第二格...")
    if not _fill_archive_form_category_input(driver, category):
        print("[ERROR] 填檔號失敗,保持視窗供手動處理。")
        return False

    print(f"[document_closure] ✓ 已填入分類檔號「{category}」")

    # 點「案次號」dropdown 選唯一非 placeholder 的 option,系統自動填「保存年限」
    print("[document_closure] 選「案次號」第一個 option,等系統填「保存年限」...")
    if not _select_case_no_and_read_retention(driver):
        print("[ERROR] 案次號選取失敗,保持視窗供手動處理。")
        return False

    # 再次確認三必填欄位齊備(防呆:即使前面 step 都報 OK,送出前再實際讀回核對)
    print("[document_closure] 送出前再次 verify「檔號 / 案次號 / 保存年限」...")
    if not _verify_required_fields_filled(driver, category):
        print("[ERROR] 必填欄位未齊備,保持視窗,不點「確定存檔」。")
        return False

    # 暫存「檔號」(在主流程變數中,跨過 pinCode 視窗階段仍可參照)
    archived_category = category  # 點 確定存檔 後 form 內容會清掉,先存一份
    print(f"[document_closure] (暫存) 檔號 = {archived_category},稍後 PIN 完會用於 log")

    # 點「確定存檔」— **重要**:此動作會跳 pinCode 視窗(KdApp localhost:16888),
    # 填完 PIN 才真正完成簽章歸檔,送出後無 admin 介入無法復原。
    print(f"[WARN] 即將點「確定存檔」— 送出後公文 {doc_no} 會被歸檔到檔號 {archived_category}")
    print(f"[WARN] 此動作無法自動復原,如要中止請於 pinCode 視窗按「取消」")
    print("[document_closure] 點「確定存檔」按鈕...")
    if not _click_confirm_save_button(driver):
        print("[ERROR] 點「確定存檔」失敗,保持視窗供手動處理。")
        return False

    # 處理 pinCode 視窗(KdApp localhost:16888/doPostMsg popup)
    # 自動處理失敗(找不到 input / PIN 填不進去 / 找不到確定按鈕等)→ 程式停止自動
    # 操作,但**不中止主流程** — 等使用者手動完成後,下一步 verify 仍會抓到
    # doc_no 從清單消失,順利寫存查標記檔。
    print("[document_closure] 等 pinCode 視窗、自動填 PIN(讀 env.env)、按確定...")
    pin_auto_ok = _handle_pincode_popup(driver)
    if not pin_auto_ok:
        print("[WARN] 自動 pinCode 處理未完成 — 若 popup 仍開著,請手動填 PIN + 按確定")
        print("[WARN] 程式繼續到 verify 階段(timeout 60s),手動完成後會自動寫標記檔")

    # 確認 doc_no 已從「待結案」清單可見列消失 → 存查成功
    # timeout 拉長到 60s(原 20s),給使用者足夠手動處理時間
    print(f"[document_closure] 確認 doc_no「{doc_no}」已從待結案清單消失(等候 60s)...")
    if not _verify_archive_success_by_listing(driver, doc_no, timeout=60):
        print("[WARN] doc_no 仍在頁面,存查可能未完成 — 不寫標記,保持視窗供檢查。")
        return False

    # 在結案目錄寫存查完成標記檔
    closure_target = os.path.join(CLOSURE_DOWNLOAD_DIR, doc_no)
    print(f"[document_closure] 在 {closure_target} 寫存查完成標記檔...")
    marker_path = _write_archive_marker(closure_target)
    if not marker_path:
        print("[WARN] 寫標記檔失敗,但存查已成功(歸檔不受影響)")

    print(f"[document_closure] ✓ 已完成存查(公文 {doc_no} 歸檔到檔號 {archived_category})")
    print("[document_closure] 結案存查流程結束。")
    return True


if __name__ == "__main__":
    # 把 stdout/stderr 同步落地到 run.log — entry point 開頭就 setup，確保
    # Chrome 預清理 / 啟動 / 導航每行 print 都進 log。
    from ime_utils import ensure_english_ime
    from taipeion_login_selenium import _setup_stdout_logging
    from document_system import _standalone_open_chrome_at_edoc

    ensure_english_ime()  # 起手式:把輸入法切回英文
    _setup_stdout_logging()
    driver = _standalone_open_chrome_at_edoc()
    if driver is None:
        sys.exit(1)
    ok = process_document_closure(driver)
    sys.exit(0 if ok else 1)
