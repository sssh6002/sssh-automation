"""
post_web_review.py
校網張貼(帶審核)模式 — FEATURES[4] / py main.py 5。

完全不碰 edoc / 自然人憑證(不用讀卡機)。流程:
  1. 掃描 document_download/ 與 document_download_closure/ 內
     「有總結、承辦文字含『於官網公告』、且尚未公告」的公文目錄。
  2. 逐筆在終端機顯示 標題/內容/同步分類/附件,由使用者確認:
       y = 貼 / n = 跳過 / e = 開檔編輯 *總結*.md 後再問一次
  3. 有確認要貼的才開一個乾淨 Chrome、用校網帳密登入、發佈公告(寫已公告標記防重複)。

可單獨執行:py post_web_review.py
"""

import glob
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

# 候選來源:承辦中備料目錄 + 結案存查目錄
SCAN_DIRS = [
    os.path.join(_BASE_DIR, "document_download"),
    os.path.join(_BASE_DIR, "document_download_closure"),
]


def _iter_candidate_dirs():
    """回 SCAN_DIRS 下第一層所有子目錄(去重、依名稱排序)。"""
    seen = set()
    out = []
    for base in SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if os.path.isdir(d) and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def _scan_candidates():
    """掃出「該公告且尚未公告」的公文目錄。回 list of (dir, summary)。"""
    from document_closure.document_closure_post_web import (
        _parse_summary, _should_post, _already_announced)
    cands = []
    for d in _iter_candidate_dirs():
        summary = _parse_summary(d)
        if not _should_post(summary):
            continue
        if _already_announced(d):
            continue
        cands.append((d, summary))
    return cands


def _print_candidate(idx, total, extract_dir, summary):
    """把一筆候選公告內容印給使用者過目。"""
    from document_closure.document_closure_post_web import _find_attachments, _doc_no_of
    atts = [os.path.basename(a) for a in _find_attachments(extract_dir)]
    print("=" * 64)
    print(f"[{idx}/{total}] {_doc_no_of(extract_dir)}")
    print(f"  標題(主旨): {summary['title']}")
    print(f"  同步分類  : {'+'.join(summary.get('sync_categories') or []) or '(無)'}")
    print(f"  附件      : {atts or '(無)'}")
    print("  內容(條列摘要):")
    for line in summary["body"].split("\n"):
        print(f"    {line}")
    print("=" * 64)


def _open_summary_for_edit(extract_dir):
    """用系統預設程式開啟該目錄的 *總結*.md 供編輯。"""
    md = sorted(glob.glob(os.path.join(extract_dir, "*總結*.md")))
    if not md:
        print("  [WARN] 找不到 *總結*.md,無法編輯。")
        return
    path = md[0]
    print(f"  開啟編輯:{path}")
    try:
        os.startfile(path)  # Windows:用預設編輯器開
    except Exception as e:
        print(f"  [WARN] 開檔失敗:{type(e).__name__}: {e};請手動編輯 {path}")


def _launch_bare_chrome():
    """開一個乾淨 Selenium Chrome(沿用同一個 profile,但不做憑證登入)。回 driver 或 None。

    校網張貼只需要瀏覽器 + 校網帳密登入(post_web 自己做),不需要自然人憑證,
    故不呼叫 login_taipeion_selenium。啟動前照全自動路徑清一次已存帳密,避免
    校網登入被 profile 自動填入干擾(commit 4e9a84f 的三層防護之一)。
    """
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException

    from taipeion_login_selenium import (
        _build_chrome_options, _purge_saved_passwords,
        _mark_profile_clean_exit, USER_DATA_DIR, PROFILE_DIR)

    os.makedirs(USER_DATA_DIR, exist_ok=True)
    if os.path.isdir(os.path.join(USER_DATA_DIR, PROFILE_DIR)):
        _mark_profile_clean_exit()
        _purge_saved_passwords()
    try:
        driver = webdriver.Chrome(options=_build_chrome_options())
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(30)
        return driver
    except WebDriverException as e:
        print(f"[post_web_review] 無法啟動 Chrome:{str(e)[:300]}")
        return None


def run_post_web_review():
    """主入口(無參數 — 配合 main.py FEATURES 中 post_login=None 的呼叫方式)。"""
    from ime_utils import ensure_english_ime
    from taipeion_login_selenium import _setup_stdout_logging

    ensure_english_ime()
    _setup_stdout_logging()

    print("[post_web_review] 掃描待公告公文...")
    cands = _scan_candidates()
    if not cands:
        print("[post_web_review] 沒有待公告的公文"
              "(條件:有總結、承辦文字含「於官網公告」、尚未公告)。")
        return

    print(f"[post_web_review] 找到 {len(cands)} 筆待公告公文,逐筆確認:")
    from document_closure.document_closure_post_web import _parse_summary
    confirmed = []
    total = len(cands)
    for i, (d, summary) in enumerate(cands, 1):
        while True:
            _print_candidate(i, total, d, summary)
            ans = input("  這篇要貼校網嗎? [y=貼 / n=跳過 / e=開檔編輯後再問]: ").strip().lower()
            if ans == "e":
                _open_summary_for_edit(d)
                input("  編輯存檔後按 Enter 重新讀取...")
                summary = _parse_summary(d) or summary
                continue
            if ans == "y":
                confirmed.append((d, summary))
            else:
                print("  → 跳過。")
            break

    if not confirmed:
        print("[post_web_review] 沒有確認要貼的公文,結束(不開 Chrome)。")
        return

    print(f"[post_web_review] 準備發佈 {len(confirmed)} 筆 → 開啟 Chrome、登入校網...")
    driver = _launch_bare_chrome()
    if driver is None:
        return
    try:
        from document_closure.document_closure_post_web import maybe_post_announcement
        ok_count = 0
        for d, _summary in confirmed:
            if maybe_post_announcement(driver, d):
                ok_count += 1
        print(f"[post_web_review] 完成,成功發佈 {ok_count}/{len(confirmed)} 筆。")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run_post_web_review()
