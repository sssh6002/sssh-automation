# -*- coding: utf-8 -*-
"""archive_batch.py
結案存查（fork 版）——**只歸檔，不貼校網**。

    python archive_batch.py         → 只預覽:待結案有哪幾筆、各自會歸到什麼檔號
    python archive_batch.py --go    → 真的歸檔
    python archive_batch.py --json  → 同預覽，但多印一行 JSON 給介面讀
    python archive_batch.py --go --expect=A,B,C
                                    → 動手前重讀清單，跟 A,B,C 不一樣就什麼都不做

## 順序有意義 —— 這條路不是「挑幾筆來做」

`process_document_closure` 每輪都處理**待結案清單的第一筆**，成功才進下一輪；
任何一筆失敗就 `return False` 中止整批。所以「哪幾筆做得到」不是各自獨立的
判定，而是**從第一筆開始連續做到第一個過不了的地方為止** —— 排在被擋那筆
後面的公文，這一批一定輪不到。預覽因此照清單順序印，並標出會停在哪裡。

⚠️ 還有一種停法預覽看不出來:主管還沒核決「如擬」的公文會被跳過、清單順序
不變，連兩輪之後整批停下（`_NO_APPROVAL_STREAK_LIMIT`）。那是 edoc 上的狀態，
磁碟上沒有痕跡，只能等真的跑下去才知道。

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

import contextlib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

GATE_LABEL = "待結案"
MARKER_SUFFIX = "已存查.txt"

# `--json` 那一行的開頭。ui.py 從整片 log 裡靠這個記號把 JSON 撈出來 ——
# selenium／urllib3 隨時可能往 stdout 吐東西，不能假設「最後一行就是 JSON」。
PLAN_MARK = "##PLAN##"

# 與 document_closure 同一條規格（summarize_doc.md）:#存查分類:<分類> <8位檔號>
_CATEGORY_RE = re.compile(r"#\s*存查分類\s*[:：]\s*(\S+)(?:\s+(\d{8}))?")


# ── 把「自動貼校網」關掉 ────────────────────────────────────────────────────

# ── 2026-08 edoc 版更:存查前一定要先點【附件歸檔】────────────────────────
#
# 來源:市府「8月公文系統版更【附件資訊自動帶入附件編目欄位】操作手冊」——
#   「過往操作是直接點選確定存檔或確定送發即可，現修改為**需先點選【附件歸檔】**，
#     方能存查或發文，否則會跳出提示視窗。**若公文無附件，也需要先點選【附件歸檔】**。」
#   「點【附件歸檔】→ 公文內的附件將自動帶入…**無須儲存，直接關閉此視窗即可**
#     → 再次點選確定存檔」
#
# 這一條害慘 2026-08-14 那批:按「確定存檔」被提示視窗擋住 → **簽章根本沒開始**
# → 15 秒等不到 pinCode 視窗。而 `document_closure` 那道「文號從待結案可見列消失」
# 的驗證，在存查表單還開著時必然成立 → 誤判成功 → **寫了假的已存查標記** →
# 那筆之後被 `plan()` 判成 danger，整批鎖死（坑 #26）。
#
# 修法長在 fork 側:換掉 `_click_confirm_save_button` 這個模組屬性，讓它在按下
# 「確定存檔」之前先做完附件歸檔。`document_closure.py` 一行沒改 ——
# ⚠️ 但**系管師那條路（`main.py 3`）沒有這段，一樣會撞到**，要跟他說。

_ATTACH_BTN_XPATHS = [
    "//input[@value='附件歸檔']",
    "//*[@value='附件歸檔']",
    "//button[normalize-space()='附件歸檔']",
    "//a[normalize-space()='附件歸檔']",
    "//*[normalize-space()='附件歸檔' and (self::button or @role='button')]",
    "//*[normalize-space()='附件歸檔']/ancestor::button[1]",
    "//*[normalize-space()='附件歸檔']/ancestor::a[1]",
]
_CONFIRM_XPATH = "//*[@value='確定存檔' or normalize-space()='確定存檔']"


def _click_first_visible(driver, xpaths):
    """照順序找第一個看得見的元素點下去。回用到的 XPath 或 None。"""
    from selenium.webdriver.common.by import By
    for xp in xpaths:
        try:
            els = driver.find_elements(By.XPATH, xp)
        except Exception:
            continue
        for el in els:
            try:
                if not el.is_displayed():
                    continue
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});"
                    "arguments[0].click();", el)
                return xp
            except Exception:
                continue
    return None


def _focus_frame_with(driver, xpath, depth=2):
    """把 driver 切回「看得到這個元素」的那層 frame。回 True/False。

    為什麼需要:切到別的視窗再切回來，frame 會被重設成最上層 ——
    而存查表單住在 frame 裡。不切回去的話，接著要按的「確定存檔」就找不到。
    """
    from selenium.webdriver.common.by import By

    def here():
        try:
            return bool(driver.find_elements(By.XPATH, xpath))
        except Exception:
            return False

    def walk(level):
        if here():
            return True
        if level <= 0:
            return False
        try:
            frames = driver.find_elements(By.TAG_NAME, "iframe") + \
                driver.find_elements(By.TAG_NAME, "frame")
        except Exception:
            return False
        for i in range(len(frames)):
            try:
                fr = (driver.find_elements(By.TAG_NAME, "iframe") +
                      driver.find_elements(By.TAG_NAME, "frame"))[i]
                driver.switch_to.frame(fr)
            except Exception:
                continue
            if walk(level - 1):
                return True
            try:
                driver.switch_to.parent_frame()
            except Exception:
                driver.switch_to.default_content()
                return False
        return False

    driver.switch_to.default_content()
    return walk(depth)


def do_attachment_archive(driver, wait_popup=8.0):
    """點【附件歸檔】，把跳出來的視窗關掉。回 True/False。

    手冊說「無須儲存，直接關閉此視窗即可」—— 所以這裡**只開再關**，
    不去動裡面的附件（附件是自動帶入的;要增修刪是人的判斷，不是程式的）。
    """
    import time
    from selenium.webdriver.common.by import By

    before = set(driver.window_handles)
    main_handle = driver.current_window_handle
    xp = _click_first_visible(driver, _ATTACH_BTN_XPATHS)
    if not xp:
        print("[archive_batch] ⛔ 找不到【附件歸檔】按鈕 —— 這一筆不送出。")
        print("[archive_batch]    2026-08 版更之後，沒先按它就按「確定存檔」會被"
              "提示視窗擋住，而程式會誤判成功、寫下假的存查標記（坑 #26）。")
        print("[archive_batch]    請到 edoc 看一下那張表單上按鈕的名字是不是又改了。")
        return False
    print(f"      OK:點到【附件歸檔】(XPath: {xp})")

    # 等它跳出來 —— 可能是新視窗，也可能是頁面內的對話框。
    deadline = time.time() + wait_popup
    new = None
    while time.time() < deadline:
        extra = set(driver.window_handles) - before
        if extra:
            new = extra.pop()
            break
        time.sleep(0.3)

    if new:
        try:
            driver.switch_to.window(new)
            time.sleep(1.2)                 # 讓附件自動帶入跑完
            print("      OK:附件歸檔視窗已開（附件自動帶入），直接關掉")
            driver.close()
        except Exception as e:
            print(f"[archive_batch] ⚠️ 關附件歸檔視窗時出錯:{type(e).__name__}: {e}")
        finally:
            driver.switch_to.window(main_handle)
        # ⚠️ 切視窗會把 frame 重設到最上層，一定要切回存查表單那層。
        if not _focus_frame_with(driver, _CONFIRM_XPATH):
            print("[archive_batch] ⛔ 關掉附件歸檔視窗後找不回存查表單 —— 這一筆不送出。")
            return False
        return True

    # 沒有新視窗 → 當成頁面內的對話框，找關閉鈕;找不到就按 ESC。
    time.sleep(1.2)
    closed = _click_first_visible(driver, [
        "//*[normalize-space()='關閉' and (self::button or self::a or @role='button')]",
        "//input[@value='關閉']",
        "//*[contains(@class,'ui-dialog-titlebar-close')]",
        "//*[@aria-label='Close' or @title='關閉']",
    ])
    if closed:
        print(f"      OK:附件歸檔對話框已關（{closed}）")
    else:
        try:
            from selenium.webdriver.common.keys import Keys
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            print("      OK:附件歸檔對話框以 ESC 關掉")
        except Exception:
            print("[archive_batch] ⚠️ 沒偵測到附件歸檔視窗（可能本來就沒跳）—— 繼續。")
    return True


@contextlib.contextmanager
def attachment_archive_first():
    """讓每一次「確定存檔」之前都先做完【附件歸檔】。用完還原。

    做不到就**回 False 不按確定存檔** —— 呼叫端看到 False 會
    `return False` 收工，那是在 `last_signed_doc_no = doc_no` 與寫標記**之前**，
    所以不會簽章、也不會留下假標記。寧可這一輪失敗，也不要再製造一個
    「磁碟說辦完、edoc 說還在」的公文。
    """
    from document_closure import document_closure as dc
    real = dc._click_confirm_save_button

    def patched(driver, timeout=10):
        print("[archive_batch] 2026-08 版更:先點【附件歸檔】才能存查…")
        if not do_attachment_archive(driver):
            return False
        return real(driver, timeout=timeout)

    dc._click_confirm_save_button = patched
    try:
        yield
    finally:
        dc._click_confirm_save_button = real


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


def read_subject(doc_no):
    """讀主旨（只讀磁碟，不開 Excel）。讀不到回空字串。

    介面上光看 MWAA1156007544 認不出是哪一份公文，而這一頁按下去不可復原 ——
    要核對的人得看得懂自己在核對什麼。
    """
    import review_sheet as rs
    for base in ("document_download_closure", "document_download"):
        d = os.path.join(_BASE_DIR, base, doc_no)
        if not os.path.isdir(d):
            continue
        try:
            rec = rs.collect(d)
        except Exception:
            continue
        if rec and rec.get("主旨"):
            return rec["主旨"]
    return ""


def evaluate(doc_no):
    """單筆能不能歸檔。回 dict —— 不能的帶「擋下原因」。"""
    cat, num, src = read_category(doc_no)
    item = {"文號": doc_no, "分類": cat or "", "檔號": num or "", "來源": src or "",
            "已存查標記": False}
    closure_dir = os.path.join(_BASE_DIR, "document_download_closure", doc_no)
    marks = [n for n in (os.listdir(closure_dir) if os.path.isdir(closure_dir) else [])
             if n.endswith(MARKER_SUFFIX)]
    if marks:
        item["已存查標記"] = True
        # 標記檔的完整路徑一起帶出去 —— 這種公文卡住時，人唯一能做的動作就是
        # 「確認 edoc 上其實沒歸檔 → 把這個假標記刪掉」，而要刪就得知道刪哪個。
        item["標記檔"] = [os.path.join(closure_dir, n) for n in marks]
        item["擋下原因"] = "已經存查過（結案目錄有 已存查.txt）"
    elif cat is None:
        item["擋下原因"] = "找不到總結檔的 #存查分類 那一行 —— 先補跑摘要"
    elif not num:
        item["擋下原因"] = (f"分類判成「{cat}」但沒有 8 位檔號 —— "
                          f"承辦人要先查出檔號填進總結檔，程式不會猜")
    return item


def preview(doc_nos):
    """把每一筆的判定算出來。回 (可歸檔, 擋下)。

    **不含順序** —— 只回答「這一筆本身過不過得了」。整批實際做得到哪裡要看
    `plan()`（歸檔迴圈只做清單第一筆，前面卡住後面就輪不到）。
    """
    ready, blocked = [], []
    for no in doc_nos:
        it = evaluate(no)
        (blocked if it.get("擋下原因") else ready).append(it)
    return ready, blocked


def plan(doc_nos):
    """依待結案清單的順序，算出整批**實際**會做到哪裡。回 dict。

    每筆掛一個「狀態」:
      go     排在停止點之前、判定過得了 —— 這一批真的會歸檔的就這些
      stop   第一個過不了的 —— 整批會停在這裡
      after  排在停止點後面 —— 判定過不過得了都一樣，這批輪不到
      danger 磁碟上已經有存查完成標記，卻還在待結案清單裡

    danger 要單獨講:`process_document_closure` **不看**那個標記檔，會照樣把它
    當一般待結案公文再送一次「確定存檔」簽章 —— 對同一份公文重複簽章正是
    2026-07-16 事故（清單沒更新被誤判成功，隔輪重簽，直到伺服器回 6005 才曝光）。
    所以只要清單裡有這種公文，這支就整批不跑，請人先確認。
    """
    items, stop_at = [], None
    for i, no in enumerate(doc_nos):
        it = evaluate(no)
        it["主旨"] = read_subject(no)
        if it.get("已存查標記"):
            it["狀態"] = "danger"
            it["擋下原因"] = (
                "磁碟上已經有存查完成標記，卻還在待結案清單裡 —— "
                "歸檔程式不會跳過它，會再送一次簽章。請先到 edoc 確認這份到底"
                "存查完成了沒，再決定要不要跑這一批。")
        elif stop_at is not None:
            it["狀態"] = "after"
        elif it.get("擋下原因"):
            it["狀態"] = "stop"
            stop_at = i
        else:
            it["狀態"] = "go"
        items.append(it)

    danger = [it for it in items if it["狀態"] == "danger"]
    go = [it for it in items if it["狀態"] == "go"]
    blocked = "、".join(it["文號"] for it in danger)
    return {
        "清單": items,
        "會歸檔": [it["文號"] for it in go],
        # 讀清單時的雜訊（分頁、對不上…）由 main() 填。這裡先給空的，
        # 讓拿到這份 dict 的人不必先判斷有沒有這個欄位。
        "警告": [],
        "可跑": bool(go) and not danger,
        "不可跑原因": (f"清單裡有 {len(danger)} 筆狀態對不上（{blocked}），"
                       f"整批不跑" if danger
                       else None if go else "沒有一筆做得到"),
    }


def expect_mismatch(expect, nos):
    """`--expect` 那串跟現在的清單對不對得上。對得上回 None，對不上回說明。

    **連順序一起比** —— 歸檔只做第一筆、前面卡住後面就輪不到，順序一變
    「會做到哪裡」就跟人確認過的不是同一回事了。
    """
    want = [s.strip() for s in (expect or "").split(",") if s.strip()]
    if want == list(nos):
        return None
    return ("待結案清單跟你確認時看到的不一樣\n"
            f"  你確認的:{'、'.join(want) or '（空）'}\n"
            f"  現在的  :{'、'.join(nos) or '（空）'}\n"
            "  請重新整理、再看一次清單。")


def sidebar_pending_count(driver):
    """讀左側「待結案(N)」的 N。原版讀不到（選單收合）才退回 JS 版。回 -1 表示讀不到。"""
    from document_system import _get_sidebar_paren_count
    from edoc_sidebar import sidebar_count

    try:
        n = _get_sidebar_paren_count(driver, GATE_LABEL, timeout=5)
    except Exception:
        n = -1
    return sidebar_count(driver, GATE_LABEL) if n < 0 else n


def pending_doc_nos(driver):
    """讀 edoc「待結案」清單上的文號，**依畫面順序**。回 (文號 list, 警告 list)。

    ⚠️ **不採用畫面上現成的清單** —— 一律先點左側「待結案」把它重新叫出來。

    原本這裡走 `post_draft_batch.focus_list(driver, label="待結案")`，但那支只在
    「有給 doc_no」時才驗清單內容（`_list_has(driver, None)` 一律回 True）。
    存查這條路沒有特定文號可驗，於是只要畫面上停著任何一份有「公文文號」表頭的
    清單就直接採用 —— 而**送陳核跑完剛好停在「承辦中」清單**。那就會把承辦中的
    公文當成待結案印給人看。坑 #11 記的是同一個洞（待結案清單被當成承辦中），
    這裡是反方向。預覽錯了，在一個不可復原的頁面上最要命，所以寧可多點一下。

    另外拿 sidebar 的「待結案(N)」交叉核對:讀到的筆數比 N 多 = 這根本不是待結案
    清單，直接拒絕；比 N 少多半是分頁，照樣給看但要講明白。
    """
    from document_system import _switch_to_frame_with_xpath
    from edoc_sidebar import click_sidebar
    from post_draft_batch import _LIST_XPATH, focus_main_window, looks_logged_out

    if not focus_main_window(driver):
        urls = []
        for h in list(driver.window_handles):
            try:
                driver.switch_to.window(h)
                urls.append(driver.current_url or "")
            except Exception:
                continue
        if looks_logged_out(urls):
            return [], ["edoc 已經登出了（閒置太久或按過登出）——"
                        "請在介面按「收新公文」重新登入，跑完不要關掉那個 Chrome 視窗。"]
        return [], ["找不到公文系統主畫面（有左側選單那個分頁）"]

    n = sidebar_pending_count(driver)
    if n < 0:
        return [], ["讀不到左側的「待結案(N)」—— 不確定現在畫面上是什麼，不給預覽。"
                    "請把 Chrome 切回公文系統主畫面再試。"]
    if n == 0:
        return [], []

    driver.switch_to.default_content()
    if not click_sidebar(driver, GATE_LABEL):
        return [], ["點不到左側的「待結案」"]
    import time
    time.sleep(2.0)
    if not _switch_to_frame_with_xpath(driver, _LIST_XPATH, "公文文號表頭", timeout=10):
        return [], ["點了「待結案」，但切不到清單 frame"]
    try:
        txt = driver.find_element("tag name", "body").text
    except Exception as e:
        return [], [f"讀清單失敗:{type(e).__name__}: {e}"]

    seen, out = set(), []
    for m in re.finditer(r"MWAA\d{10}", txt):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            out.append(m.group(0))

    warn = []
    if len(out) > n:
        return [], [f"畫面上讀到 {len(out)} 筆，但左側寫「待結案({n})」—— 對不上，"
                    f"這多半不是待結案清單。不給預覽，請把 Chrome 切回主畫面再試。"]
    if len(out) < n:
        warn.append(f"左側寫「待結案({n})」，畫面上只看得到 {len(out)} 筆"
                    f"（清單可能分頁了）。歸檔從第一筆開始做，看不到的那幾筆"
                    f"這次的預覽也沒算進去。")
    return out, warn


def _print_plan(p, warn=()):
    for w in warn:
        print(f"\n[!] {w}")
    items = p["清單"]
    print(f"\n【待結案清單】{len(items)} 筆 —— 歸檔從第 1 筆開始，逐筆往下")
    mark = {"go": "✓ 會歸檔", "stop": "⛔ 停在這裡", "after": "— 這批輪不到",
            "danger": "⚠ 狀態對不上"}
    for i, it in enumerate(items, 1):
        print(f"  {i}. [{mark[it['狀態']]}] {it['文號']}  {it.get('主旨') or ''}")
        if it["狀態"] == "go":
            print(f"       分類「{it['分類']}」→ 檔號 {it['檔號']}")
            print(f"       （檔號來源:{it['來源']}）")
        elif it.get("擋下原因"):
            print(f"       {it['擋下原因']}")
        else:
            print(f"       前面那筆會讓整批停下來，所以這次做不到它")
    print(f"\n這一批實際會歸檔 {len(p['會歸檔'])} 筆。")
    if p["不可跑原因"]:
        print(f"⛔ {p['不可跑原因']}")
    print("★ 這條路**不會貼校網**。要公告的走「張貼」那條逐筆確認。")
    print("★ 主管還沒核決「如擬」的公文預覽看不出來 —— 那種會被跳過、"
          "連兩輪之後整批停下。")


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="結案存查（fork 版，只歸檔不貼校網）")
    ap.add_argument("--go", action="store_true", help="真的歸檔（預設只預覽）")
    ap.add_argument("--json", action="store_true",
                    help=f"多印一行「{PLAN_MARK} {{…}}」給介面讀")
    ap.add_argument("--expect", default=None,
                    help="動手前重讀清單，跟這串（逗號分隔的文號）不一樣就什麼都不做")
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

    # 殘留的公文閱覽器分頁要先清掉才動手。2026-08-10 第一次實跑就是栽在這:
    # 8/6 送陳核留下的 `app=check&doSno=1156007710` 分頁還開著，
    # `document_closure` 點了 7696 之後切公文閱覽器，切到的是那個 7710 的舊分頁，
    # 於是在**別份公文**上判「如擬」、按下載。那支是系管師的檔案不改，
    # 所以閘門長在這裡:有殘留就停下請人關掉（不自動關 —— 裡面可能有手動填的東西）。
    from post_draft_batch import viewer_tabs
    stale = viewer_tabs(driver)
    if stale:
        print("=" * 70)
        print(f"[archive_batch] 有 {len(stale)} 個「公文閱覽器」分頁還開著 — 什麼都沒有做。")
        print("  請先把它們關掉，只留公文系統的主畫面。")
        print("  原因:程式切公文閱覽器時可能切到舊的那個分頁，就會對著**別份公文**"
              "判「如擬」、按下載。")
        print("=" * 70)
        raise SystemExit(1)

    nos, warn = pending_doc_nos(driver)
    p = plan(nos)
    p["警告"] = list(warn)
    if a.json:
        print(f"{PLAN_MARK} {json.dumps(p, ensure_ascii=False)}")
    if not nos:
        for w in warn:
            print(f"[archive_batch] {w}")
        if not warn:
            print("[archive_batch] 待結案清單是空的。")
        return
    print(f"\n待結案清單:{len(nos)} 筆 — {'、'.join(nos)}")
    _print_plan(p, warn)

    if not a.go:
        print("\n這是預覽，什麼都沒做。確認無誤後加 --go 才會真的歸檔。")
        return

    # 「畫面上看到的那幾筆」與「現在清單上的那幾筆」不一樣就不動 —— 中間可能
    # 又有公文陳核完回到待結案，順序一變，會做到哪裡就跟人看過的不是同一回事。
    # 這道關卡放在真正動手的這一支，介面與 CLI 才是同一套規則。
    if a.expect is not None:
        why = expect_mismatch(a.expect, nos)
        if why:
            print(f"\n[STOP] {why}\n  —— 什麼都沒做。")
            raise SystemExit(1)

    if not p["可跑"]:
        print(f"\n[STOP] {p['不可跑原因']} —— 什麼都沒做。")
        raise SystemExit(1)

    print(f"\n===== 開始歸檔（無 admin 介入無法復原）=====")
    print(f"預期會歸檔 {len(p['會歸檔'])} 筆:{'、'.join(p['會歸檔'])}")
    disable_auto_post()
    from document_closure.document_closure import process_document_closure
    # 2026-08 版更:每一筆按「確定存檔」之前要先點【附件歸檔】，見上面那段。
    with attachment_archive_first():
        ok = process_document_closure(driver)
    print(f"\n===== 結案存查流程{'完成' if ok else '中止'} =====")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
