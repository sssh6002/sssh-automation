# -*- coding: utf-8 -*-
"""archive_batch.py
結案存查（fork 版）——**只歸檔，不貼校網**。

    python archive_batch.py         → 只預覽:待結案有哪幾筆、各自會歸到什麼檔號
    python archive_batch.py --go    → 真的歸檔

## 為什麼不直接用 `main.py 3`

系管師那條路（`document_closure.process_document_closure`）歸檔成功後會
**緊接著自動貼校網** —— 尾端那句 `maybe_post_announcement(driver, closure_target)`
中間沒有任何人工確認。2026-07-28 曾因此把一份他人業務的內部公文送上校網。

承辦人要的是半自動:歸檔可以自動（機械動作、規則明確），但**貼什麼上校網要
自己決定**，走「張貼」那條 `post_web_review` 的逐筆確認。

`document_closure.py` 是系管師的檔案，一行不改。這支在呼叫前把
`document_closure_post_web.maybe_post_announcement` 換成不做事的版本 ——
原程式那行是**函式內 import**，換掉模組屬性就會生效。系管師自己跑
`main.py 3` 時完全不受影響（那個 process 裡沒有人動過這個屬性）。

## 順帶補上的一道防護

`process_document_closure` 每輪都用 `_get_sidebar_paren_count(driver, "待結案")`
判斷還剩幾筆，讀不到（回 -1）就「無法判讀待結案數,中止迴圈」。而那支函式在
**選單收合時讀不到**（2026-08-05 收文就是這樣停住的，詳見 `edoc_sidebar.py`）。
這裡把它包一層:原版讀不到才退回 JS 版。方向是收緊，不是放寬。

## 危險程度

歸檔**無 admin 介入無法復原**，所以預設只預覽，`--go` 才動。
讀不到 8 位檔號的公文，原程式會安全停下不硬填 —— 那道閘門保留，不要繞過。
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

GATE_LABEL = "待結案"
MARKER_SUFFIX = "已存查.txt"

# 與 document_closure 同一條規格（summarize_doc.md）:#存查分類:<分類> <8位檔號>
_CATEGORY_RE = re.compile(r"#\s*存查分類\s*[:：]\s*(\S+)(?:\s+(\d{8}))?")


# ── 把「自動貼校網」關掉 ────────────────────────────────────────────────────

def disable_auto_post():
    """把 `maybe_post_announcement` 換成不做事的版本。回原本那支（供還原）。

    只影響目前這個 process。**不改 document_closure.py**。
    """
    import document_closure.document_closure_post_web as pw

    original = pw.maybe_post_announcement
    if getattr(original, "_fork_disabled", False):
        return original

    def _skip(driver, extract_dir):
        print("[archive_batch] 略過自動貼校網（這條路只歸檔）—— "
              "要公告請走「張貼」那條逐筆確認的流程")
        return False

    _skip._fork_disabled = True
    pw.maybe_post_announcement = _skip
    return original


def install_sidebar_fallback():
    """讓 `_get_sidebar_paren_count` 在讀不到時退回 JS 版（選單收合的情況）。

    包一層而不是改那支 —— 它是全自動路徑也在用的共用函式。
    """
    import document_system as ds
    from edoc_sidebar import sidebar_count

    original = ds._get_sidebar_paren_count
    if getattr(original, "_fork_patched", False):
        return original

    def patched(driver, label, timeout=10):
        n = original(driver, label, timeout=timeout)
        if n < 0:
            n = sidebar_count(driver, label)
        return n

    patched._fork_patched = True
    ds._get_sidebar_paren_count = patched
    return original


# ── 預覽 ───────────────────────────────────────────────────────────────────

def read_category(doc_no):
    """讀這份公文的存查分類與檔號。回 (分類文字, 8位檔號 or None, 來源說明)。

    優先讀結案目錄 `document_download_closure/<doc_no>`（歸檔時實際會用的那份），
    沒有才退回承辦中目錄 `document_download/`。

    **兩處都有總結時可能不一致** —— 交接檔記過 MWAA1156007051 就是
    「研習+研習資訊」對上「競賽+只有課外活動」。所以來源要講出來，
    不能讓人以為預覽看到的一定是歸檔會用的那個。
    """
    import glob

    for base, src in (("document_download_closure", "結案目錄（歸檔會用這份）"),
                      ("document_download", "承辦中目錄（歸檔時會改用結案目錄那份）")):
        d = os.path.join(_BASE_DIR, base, doc_no)
        if not os.path.isdir(d):
            continue
        for md in sorted(glob.glob(os.path.join(d, "*總結*.md"))):
            try:
                head = open(md, encoding="utf-8").read(400)
            except OSError:
                continue
            m = _CATEGORY_RE.search(head)
            if m:
                return m.group(1), m.group(2), src
    return None, None, None


def evaluate(doc_no):
    """單筆能不能歸檔。回 dict —— 不能的帶「擋下原因」。"""
    cat, num, src = read_category(doc_no)
    item = {"文號": doc_no, "分類": cat or "", "檔號": num or "", "來源": src or ""}
    closure_dir = os.path.join(_BASE_DIR, "document_download_closure", doc_no)
    if any(n.endswith(MARKER_SUFFIX)
           for n in (os.listdir(closure_dir) if os.path.isdir(closure_dir) else [])):
        item["擋下原因"] = "已經存查過（結案目錄有 已存查.txt）"
    elif cat is None:
        item["擋下原因"] = "找不到總結檔的 #存查分類 那一行 —— 先補跑摘要"
    elif not num:
        item["擋下原因"] = (f"分類判成「{cat}」但沒有 8 位檔號 —— "
                          f"承辦人要先查出檔號填進總結檔，程式不會猜")
    return item


def preview(doc_nos):
    """把每一筆的判定算出來。回 (可歸檔, 擋下)。"""
    ready, blocked = [], []
    for no in doc_nos:
        it = evaluate(no)
        (blocked if it.get("擋下原因") else ready).append(it)
    return ready, blocked


def pending_doc_nos(driver):
    """讀 edoc「待結案」清單上的文號。回 list（讀不到回空）。"""
    from post_draft_batch import focus_list, focus_main_window, looks_logged_out

    if not focus_main_window(driver):
        urls = []
        for h in list(driver.window_handles):
            try:
                driver.switch_to.window(h)
                urls.append(driver.current_url or "")
            except Exception:
                continue
        if looks_logged_out(urls):
            print("[archive_batch] edoc 已經登出了（閒置太久或按過登出）——"
                  "請在介面按「收新公文」重新登入，跑完不要關掉那個 Chrome 視窗。")
        else:
            print("[archive_batch] 找不到公文系統主畫面（有左側選單那個分頁）")
        return []
    if not focus_list(driver, label=GATE_LABEL):
        print("[archive_batch] 切不到「待結案」清單")
        return []
    try:
        txt = driver.find_element("tag name", "body").text
    except Exception as e:
        print(f"[archive_batch] 讀清單失敗:{type(e).__name__}: {e}")
        return []
    seen, out = set(), []
    for m in re.finditer(r"MWAA\d{10}", txt):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            out.append(m.group(0))
    return out


def _print_plan(ready, blocked):
    print(f"\n【會歸檔】{len(ready)} 筆")
    for i, it in enumerate(ready, 1):
        print(f"  {i}. {it['文號']}  分類「{it['分類']}」→ 檔號 {it['檔號']}")
        print(f"     （檔號來源:{it['來源']}）")
    print(f"\n【不會歸檔】{len(blocked)} 筆")
    for it in blocked:
        print(f"  - {it['文號']}  {it['擋下原因']}")
    print("\n★ 這條路**不會貼校網**。要公告的走「張貼」那條逐筆確認。")


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="結案存查（fork 版，只歸檔不貼校網）")
    ap.add_argument("--go", action="store_true", help="真的歸檔（預設只預覽）")
    a = ap.parse_args()

    from fill_in_draft import _attach_existing_chrome

    driver = _attach_existing_chrome()
    if driver is None:
        print("=" * 70)
        print("[archive_batch] attach 不到 Chrome — 什麼都沒有做。")
        print("  ⚠ 不要照上面那行「跑 python main.py 3」做 —— 那正是會自動貼校網的那條。")
        print("  正確做法:在介面按「收新公文」重新登入，跑完不要關掉那個 Chrome 視窗。")
        print("=" * 70)
        raise SystemExit(1)

    install_sidebar_fallback()
    nos = pending_doc_nos(driver)
    if not nos:
        print("[archive_batch] 待結案清單是空的（或讀不到）。")
        return
    print(f"\n待結案清單:{len(nos)} 筆 — {'、'.join(nos)}")
    ready, blocked = preview(nos)
    _print_plan(ready, blocked)

    if not a.go:
        print("\n這是預覽，什麼都沒做。確認無誤後加 --go 才會真的歸檔。")
        return
    if not ready:
        print("\n沒有可歸檔的公文。")
        return

    print(f"\n===== 開始歸檔（無 admin 介入無法復原）=====")
    disable_auto_post()
    from document_closure.document_closure import process_document_closure
    ok = process_document_closure(driver)
    print(f"\n===== 結案存查流程{'完成' if ok else '中止'} =====")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
