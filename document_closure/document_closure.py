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

import os
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


def _has_approval_text(driver, keyword=_APPROVAL_KEYWORD, timeout=10):
    """檢查公文閱覽器頁面是否含結案存查判定關鍵字（預設「如擬」）。

    公文閱覽器是 iframe-based 排版，右側「承辦/會辦/核決」意見區可能在內 frame。
    策略：先檢查 top-level body.innerText；找不到則遍歷所有 iframe 各檢查一次。
    每輪 0.5s 一次直到 timeout，給頁面非同步載入留時間。

    回 True 表示頁面任一處可見 keyword，False 表示 timeout 內都找不到。
    """
    deadline = time.time() + timeout
    js = ("return document.body ? document.body.innerText.indexOf(arguments[0]) "
          "!== -1 : false;")
    while time.time() < deadline:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        try:
            if driver.execute_script(js, keyword):
                return True
        except Exception:
            pass
        try:
            iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
        except Exception:
            iframes = []
        for ifr in iframes:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(ifr)
                if driver.execute_script(js, keyword):
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
              "保守不下載(可能還在簽核或意見有異)，結束。")
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
    else:
        print("[document_closure] ✓ 結案存查下載完成 (非 zip，原檔保留)")

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
