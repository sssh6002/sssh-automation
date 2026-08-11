# -*- coding: utf-8 -*-
"""
post_draft_batch.py
批次貼擬辦 + 送陳核（2/4 批次）。

讀桌面審核表「公告彙整.xlsx」中**「陳會」欄已打 OK** 的公文，逐筆回 edoc：
    用文號定位公文 → 開閱覽器分頁 → 填擬辦 → 儲存 → 按陳會 → PIN 簽章

擬辦文字**以審核表為準**（你在 Excel 改過的版本），不是總結檔裡機器產的那份。

安全設計（送陳核收不回來，所以預設什麼都不做）:
  python post_draft_batch.py         → 只預覽:列出會送哪幾筆、實際要送的文字
  python post_draft_batch.py --go    → 真的送

  - 「陳會」欄留白的一律不碰（代理公文、陳核路徑特殊的那些）。
  - 任一筆失敗即整批中止，不硬著頭皮往下跑。
  - 送成功的在公文目錄寫 <文號>已陳核.txt，再跑一次會自動跳過，不重複送簽。
  - 需要先跑過 main.py（Chrome 已開在 edoc、讀卡機插著），本程式 attach 既有 session。
"""

import os
import re
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import review_sheet as rs  # noqa: E402

GATE = "陳會"
MARKER_SUFFIX = "已陳核.txt"
ROUTING_YAML = os.path.join(_BASE_DIR, "routing_flags.yaml")


_DOC_PREFIX_RE = re.compile(r"發文字號[:：]\s*(\S+?)字第")


def routing_flags():
    """讀 routing_flags.yaml。回 dict（讀不到就回全空，等於不做任何攔阻）。"""
    cfg = {}
    try:
        import yaml
        with open(ROUTING_YAML, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        pass

    def lst(k):
        return [str(x).strip() for x in (cfg.get(k) or []) if str(x).strip()]

    return {"本人字別": lst("本人字別"), "他人字別": lst("他人字別"),
            "關鍵字": lst("關鍵字"),
            "未知字別": str(cfg.get("未知字別") or "pass").strip().lower(),
            "說明": str(cfg.get("說明") or "").strip()}


def doc_prefix(content_txt):
    """從 內容.txt 抽發文字別（「○○字第…號」的○○）。抽不到回 None。"""
    m = _DOC_PREFIX_RE.search(content_txt or "")
    return m.group(1) if m else None


def routing_hit(text, flags=None, prefix=None):
    """判斷這份公文該不該擋。回 (要擋嗎, 原因) — 不擋時原因為 None。

    判斷順序（主題優先於字別 —— 業務是按主題分的，不是按來文機關分的）:
      1. 主題關鍵字命中 → 擋。實例:「數位學生證結合圖書館借閱證」發文字別是
         本人的「北市教資」，但學生證業務屬他人，只看字別會判錯。
      2. 發文字別在「他人字別」→ 擋
      3. 發文字別在「本人字別」→ 放行
      4. 字別抓不到或不在兩份清單 → 依「未知字別」設定（預設放行）
    """
    f = routing_flags() if flags is None else flags
    s = str(text or "")
    for k in f["關鍵字"]:
        if k in s:
            return True, f"主題含「{k}」屬他人業務"
    if prefix:
        for p in f["他人字別"]:
            if prefix.startswith(p) or p in prefix:
                return True, f"發文字別「{prefix}」屬他人業務"
        for p in f["本人字別"]:
            if prefix.startswith(p) or p in prefix:
                return False, None
        if f["未知字別"] == "block":
            return True, f"發文字別「{prefix}」不在清單上"
    return False, None

# 擬辦欄可能帶的前綴與註記 — 送進 edoc 前要清掉
_LEAD_RE = re.compile(r"^\s*擬\s*[:：]\s*")
_HINT_RE = re.compile(r"[（(]建議轉知[:：][^）)]*[）)]\s*$")
_TODO_RE = re.compile(r"[（(]請自行填寫[:：]?[^）)]*[）)]")


def clean_fragment(text):
    """把審核表的擬辦欄整理成可送出的承辦文字。

    - 去掉開頭的「擬：」（fill_in_draft 的模板會自己加）
    - 去掉結尾的「（建議轉知：○○教師）」— 那是給承辦人看的提示，不是公文內容
    回 (承辦文字, 被拿掉的提示 or None)。
    """
    if not text:
        return "", None
    s = str(text).strip()
    s = _LEAD_RE.sub("", s)
    hint = None
    m = _HINT_RE.search(s)
    if m:
        hint = m.group(0).strip()
        s = _HINT_RE.sub("", s).strip()
    return s, hint


def needs_human(text):
    """擬辦欄還留著「（請自行填寫…）」→ 不可送出。"""
    return bool(_TODO_RE.search(str(text or "")))


def marker_path(extract_dir, doc_no):
    return os.path.join(extract_dir, f"{doc_no}{MARKER_SUFFIX}")


def already_sent(extract_dir, doc_no):
    """該目錄內出現任何 *已陳核.txt → 視為已送簽，不重送。"""
    try:
        return any(n.endswith(MARKER_SUFFIX) for n in os.listdir(extract_dir))
    except OSError:
        return False


_RETURNED_RE = re.compile(r"^\s*\d+\.松山高中.*?\s退文\s", re.M)


def was_returned(doc_dir):
    """這份公文的簽核單裡有沒有「退文」紀錄。有 → 曾被打回，不可自動送。

    退文代表**有人對擬辦有意見**，而那個意見不會出現在任何規格檔裡（是人跟人
    之間的判斷）。同一份擬辦再送一次多半會再被退，反而更花時間。
    實測 1433 份公文中僅 14 份有退文紀錄（<1%），其中 11 份是圖書館主任退的。

    回 (是否退過, 退文那一行) — 沒退過回 (False, None)。
    """
    import glob
    for f in sorted(glob.glob(os.path.join(doc_dir, "**", "*opinion*.pdf"),
                              recursive=True)):
        try:
            from pypdf import PdfReader
            t = "\n".join(p.extract_text() or "" for p in PdfReader(f).pages)
        except Exception:
            continue
        m = _RETURNED_RE.search(t)
        if m:
            return True, re.sub(r"\s+", " ", m.group(0).strip())
    return False, None


def _read_content(doc_dir):
    """讀該公文目錄的 *內容.txt（抽發文字別用）。讀不到回空字串。"""
    import glob
    hits = sorted(glob.glob(os.path.join(doc_dir, "*內容.txt")))
    if not hits:
        return ""
    try:
        return open(hits[0], encoding="utf-8").read()
    except OSError:
        return ""


def _resolve_dir(doc_no):
    """以文號找工作區公文目錄。找不到回 None。"""
    for base in rs.SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for n in sorted(os.listdir(base)):
            if doc_no in n and os.path.isdir(os.path.join(base, n)):
                return os.path.join(base, n)
    return None


def evaluate(row, flags=None):
    """單筆能不能送。回 item dict —— 不能送的帶「擋下原因」，能送的帶「送出文字」。

    抽出來是為了讓 `ui.py` 的陳核頁跟這支 CLI 用**同一套判定**。介面上另寫一份
    規則的話，遲早出現「畫面說會送、實際被擋」或更糟的反過來。
    """
    f = routing_flags() if flags is None else flags
    doc_no = str(row["文號"]).strip()
    d = _resolve_dir(doc_no)
    item = {"文號": doc_no, "主旨": row.get("主旨") or "", "目錄": d,
            "原文": row.get("擬辦")}
    prefix = doc_prefix(_read_content(d)) if d else None
    stop, why = routing_hit(f"{row.get('主旨') or ''}\n{row.get('摘要') or ''}",
                            f, prefix)
    returned, when = (was_returned(d) if d else (False, None))
    if d is None:
        item["擋下原因"] = "找不到公文目錄（可能已結案移走）"
    elif returned:
        item["擋下原因"] = f"這份公文被退過（{when}）— 擬辦有人有意見，請自行處理"
    elif stop:
        item["擋下原因"] = f"{why} — {f['說明'] or '陳核路徑可能不同，請自行處理'}"
    elif already_sent(d, doc_no):
        item["擋下原因"] = "已有 已陳核.txt，先前送過"
    elif rs.is_archived(row, d):
        # 整份已經辦完（多數是目錄裡有 *已存查.txt）卻還留著「陳會 OK」——
        # 因為 upsert 只填空白格，承辦人當初打的 OK（=請程式去做）做完之後
        # 不會被改寫，永遠停在 OK（交接檔坑 #1）。少了這一條，這些早就結案
        # 的公文每次都會被算成「可送」，再送一次會在 edoc 多開一輪簽核流程。
        # 2026-08-04 實測:審核表裡 MWAA1156006895、MWAA1156007057 就是這個狀態。
        item["擋下原因"] = "這份已經辦完（存查／公告完成），不需要再送陳核"
    elif not (row.get("擬辦") or "").strip():
        item["擋下原因"] = "擬辦欄空白"
    elif needs_human(row.get("擬辦")):
        item["擋下原因"] = "擬辦欄還留著「請自行填寫」，需你先寫"
    else:
        frag, hint = clean_fragment(row["擬辦"])
        item.update({"送出文字": frag, "移除的提示": hint})
    return item


def plan(path=None):
    """回 (可送清單, 擋下清單)。每筆為 dict。只看「陳會」欄已放行的列。"""
    flags = routing_flags()
    ready, blocked = [], []
    for row in rs.approved(GATE, path):
        item = evaluate(row, flags)
        (blocked if item.get("擋下原因") else ready).append(item)
    return ready, blocked


def _print_plan(ready, blocked):
    print(f"\n【會送出】{len(ready)} 筆")
    for i, it in enumerate(ready, 1):
        print(f"\n  {i}. {it['文號']}  {it['主旨'][:40]}")
        print(f"     實際送進 edoc 的文字：")
        print(f"       擬:")
        print(f"       {it['送出文字']}")
        if it["移除的提示"]:
            print(f"     （已移除給你看的提示：{it['移除的提示']}）")
    print(f"\n【不會送】{len(blocked)} 筆")
    for it in blocked:
        print(f"  - {it['文號']}  {it['擋下原因']}")


# 承辦中清單 frame 的特徵。**不可以用「簽收」按鈕當特徵** —— 那是待簽收清單的
# 東西，承辦中清單沒有。2026-08-05 實測:借用 _switch_to_signoff_frame 的結果是
# 第一筆就停在「切不到清單 frame」（0/5，沒有送出任何東西）。
# 這條 xpath 與全自動路徑 pending_doc 用的同一條，不是另立標準。
_LIST_XPATH = "//th[contains(normalize-space(), '公文文號')]"


# 公文閱覽器分頁的網址特徵。它是 edoc 網域,但沒有 sidebar、沒有清單 frame。
# 公文閱覽器分頁的網址特徵。**不能只認 `app=editor`**（2026-08-10 實機踩到）:
# 送陳核開的是 `app=editor`，結案存查開的是 `app=check`，按下載那步還會多一個
# `app=genpages`。原本這裡寫死 editor，於是存查那條路開跑前的殘留分頁檢查
# 形同虛設 —— 8/6 送陳核留下的 `app=check&doSno=1156007710` 分頁一路過關，
# `document_closure` 切公文閱覽器時抓到它（**不是**剛點開的那一份 7696），
# 在錯的公文上判「如擬」、按下載。認 `oa/index.html?app=` 三種都涵蓋。
#
# 放寬這個常數是安全的:另外兩個用到它的地方（wait_viewer／close_viewer）都
# **同時**要求網址帶 `doSno=<這份公文>`，認得更寬也只會認到同一份。
_VIEWER_MARK = "oa/index.html?app="

# 登出／回到登入頁的網址特徵。2026-08-06:閒置一陣子後 Chrome 只剩
# `index.jsp?logout=Y` 一個分頁,這時說「找不到公文系統主畫面」太含糊 ——
# 講「已經登出」才知道下一步是重新登入。
# 「操作時間逾期，請您重新登入」那個警告視窗。三件事讓它特別會騙人:
#   1. 它是 edoc 自己 window.open 開的**獨立視窗**，不是 JS alert ——
#      taipeion_login_selenium 那段「自動接受所有 JS dialog」按不掉它，
#      它會一直堆在那裡。
#   2. 它的網址住在 **/tcqb/home/** 底下，跟公文系統主畫面同一個路徑。
#   3. 它自己是 edoc 網域，所以「有沒有在 edoc」那種檢查一律放行（坑 #9 同型）。
# 2026-08-10 實測:承辦人收文時它一直跳，而 ui.chrome_state 那兩道
# 「有沒有 /tcqb/home/」的檢查被它冒充成主畫面 —— 整個已登出的狀態亮綠燈，
# 陳核頁與存查頁的送出鈕照樣可以按。
_TIMEOUT_MARK = "/tcqb/home/sessiontimeout.jsp"
_HOME_MARK = "/tcqb/home/"
_LOGOUT_MARKS = ("logout=y", "/tcqb/index.jsp", _TIMEOUT_MARK)


def looks_logged_out(urls):
    """從分頁網址判斷 edoc 是不是已經登出（含逾期被踢出來）。"""
    low = [str(u or "").lower() for u in urls]
    return bool(low) and any(any(m in u for m in _LOGOUT_MARKS) for u in low)


def looks_timed_out(urls):
    """有沒有那個「操作時間逾期」的警告視窗。

    跟 looks_logged_out 分開是因為**下一步不一樣**:逾期時畫面上還多一個
    關不掉的小視窗，得先請人關掉它，不然重新登入之後它還杵在那裡繼續騙
    後面的檢查。
    """
    return any(_TIMEOUT_MARK in str(u or "").lower() for u in urls)


def has_home(urls):
    """有沒有停在公文系統主畫面（左側有選單那個頁面）。

    **sessionTimeout.jsp 不算** —— 它也住在 /tcqb/home/ 底下，但那是
    「你已經被踢出去了」的告示，不是主畫面。只比對 /tcqb/home/ 會把它當成
    主畫面，於是「已經登出」這件事永遠檢查不出來。
    共用同一個特徵字串前先確認另一條路的長相真的一樣 —— 坑 #17 的教訓。
    """
    low = [str(u or "").lower() for u in urls]
    return any(_HOME_MARK in u and _TIMEOUT_MARK not in u for u in low)


def viewer_tabs(driver):
    """回目前開著的公文閱覽器分頁 handle list。**會切換分頁焦點**。

    為什麼要管它:run() 是靠「window_handles 有沒有多一個」判斷公文有沒有開起來。
    同一份公文的閱覽器已經開著時，點清單不會再開新的 → 程式會誤判成「沒開出
    公文閱覽器分頁」而中止。所以開跑前有殘留就要先請使用者關掉。
    """
    out = []
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            if _VIEWER_MARK in (driver.current_url or ""):
                out.append(h)
        except Exception:
            continue
    return out


def focus_main_window(driver):
    """切到公文系統主畫面（有左側選單那個分頁）。回 True/False。

    **不能直接用 attach 時的當前分頁**:Chrome 常常還開著公文閱覽器分頁，而它同樣
    是 edoc 網域、卻沒有 sidebar 也沒有清單 frame。2026-08-05 實測就是這樣卡住的
    ——原本只檢查「current_url 含 edoc.gov.taipei」，閱覽器分頁照樣過關，
    第一筆就停在「左側選單找不到承辦中」。
    """
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            url = driver.current_url or ""
            if "edoc.gov.taipei" not in url or _VIEWER_MARK in url:
                continue
            driver.switch_to.default_content()
            if driver.execute_script(
                    "return !!document.querySelector('#leftSideBar, .menuArea')"):
                print(f"      OK:切到公文系統主畫面 — {url[:70]}")
                return True
        except Exception:
            continue
    return False


def _list_has(driver, doc_no):
    """目前這個 frame 的清單裡看不看得到這個文號。doc_no 為 None 一律 True。"""
    if not doc_no:
        return True
    try:
        return bool(driver.execute_script(
            "return (document.body.innerText || '').indexOf(arguments[0]) >= 0;",
            doc_no))
    except Exception:
        return False


def focus_list(driver, doc_no=None, label="承辦中"):
    """把焦點切到 `label` 的清單 frame。給了 doc_no 就要那一筆真的在畫面上。

    `label` 預設「承辦中」（送陳核用）；存查那條路傳「待結案」。

    **光看「有沒有公文文號表頭」不夠**:待結案清單、搜尋結果、催辦清單都有那個
    表頭。2026-08-06 實測 —— sidebar 明明寫「承辦中(5)」，frame 裡卻是只有 1 筆
    的**待結案**清單（那筆狀態「陳核決行(待結案)」，已經陳核完回來等存查的）。
    程式在那份清單裡找要送的公文，當然找不到，於是停在那筆要存查的公文上。

    所以判準改成「要送的那一筆在不在畫面上」；不在就點左側「承辦中」重叫一次
    完整清單。送陳核前 Chrome 停在哪一頁沒人保證得了，自己叫比要求使用者切對頁
    可靠。
    """
    from document_system import _switch_to_frame_with_xpath
    from edoc_sidebar import click_sidebar

    driver.switch_to.default_content()
    if _switch_to_frame_with_xpath(driver, _LIST_XPATH, "公文文號表頭", timeout=5) \
            and _list_has(driver, doc_no):
        return True

    print(f"      畫面上的清單裡沒有這一筆 — 點左側「{label}」重叫完整清單")
    driver.switch_to.default_content()
    if not click_sidebar(driver, label):
        return False
    time.sleep(2.0)
    if not _switch_to_frame_with_xpath(driver, _LIST_XPATH, "公文文號表頭", timeout=10):
        return False
    if not _list_has(driver, doc_no):
        print(f"      x  「{label}」清單裡找不到 {doc_no} —— 這份可能已經送走，"
              f"或已經不在{label}（例如陳核完回來等存查）")
        return False
    return True


# 閱覽器渲染好了沒:ExtJS 跑起來才會有 textarea，被當成純文字丟出來時一個都沒有。
_VIEWER_READY_JS = (
    "return document.contentType === 'text/html'"
    " && document.querySelectorAll('textarea').length > 0;")


def ensure_viewer_ready(driver, tries=2, wait=8.0):
    """等公文閱覽器真的渲染出來；沒渲染就重新載入。回 True/False。

    2026-08-06 實測:Selenium 點開的閱覽器分頁，第一次載入常常拿到
    `contentType = text/css` —— 整份 HTML 被當成純文字丟出來（body 只有一個
    `<pre>`、script 0 支、textarea 0 個），於是 `fill_in_draft` 永遠找不到
    「我的意見」欄，停在「填承辦文字失敗」。**重新整理一次就正常**
    （text/html、47 支 script、3 個 textarea）。

    根因在伺服器/快取那邊（那頁還帶著 Chrome 早就移除的 appcache manifest），
    不是我們改得動的;這裡只做偵測與重載。fill_in_draft 是系管師的檔案不動，
    所以這關擺在呼叫它之前。
    """
    for attempt in range(tries + 1):
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                if driver.execute_script(_VIEWER_READY_JS):
                    if attempt:
                        print(f"      OK:重新載入 {attempt} 次後閱覽器正常了")
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        if attempt < tries:
            print(f"      閱覽器沒有正常載入（整頁被當成純文字）— 重新整理第 "
                  f"{attempt + 1} 次…")
            try:
                driver.refresh()
            except Exception as e:
                print(f"      x  重新整理失敗:{type(e).__name__}: {e}")
                return False
    return False


def doc_sno(doc_no):
    """MWAA1156007725 → 1156007725（閱覽器網址裡的 doSno）。抽不到回 None。"""
    m = re.search(r"(\d{10})\s*$", str(doc_no or ""))
    return m.group(1) if m else None


def wait_viewer(driver, doc_no, timeout=12.0):
    """等這份公文的閱覽器分頁出現，並把焦點切過去。回 True/False。

    **不能只看「window_handles 有沒有多一個」**:edoc 有時會把公文塞進既有的
    閱覽器分頁（換掉內容）而不是開新的，這時 handle 數不變，程式就誤判成
    「沒開出公文閱覽器分頁」而中止整批。2026-08-06 實測:一批 3 筆，前兩筆送完
    分頁沒關，第 3 筆就被塞進既有分頁，卡在這裡（前 2 筆已經真的送出去了）。

    改成認網址裡的 `doSno` —— 那是這份公文專屬的，新開或重用都認得出來。
    """
    sno = doc_sno(doc_no)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for h in list(driver.window_handles):
            try:
                driver.switch_to.window(h)
                url = driver.current_url or ""
                if _VIEWER_MARK in url and (not sno or f"doSno={sno}" in url):
                    return True
            except Exception:
                continue
        time.sleep(0.5)
    return False


def close_viewer(driver, doc_no):
    """關掉這份公文的閱覽器分頁（送出成功後才呼叫）。best-effort，失敗不影響。

    為什麼要關:留著會累積，下一筆就可能被塞進舊分頁（見 wait_viewer），
    而且下次開跑會被 viewer_tabs 那道檢查擋下來，變成每次都要人工清。
    公文這時已經送走，那個分頁沒有用途了。
    """
    sno = doc_sno(doc_no)
    if not sno:
        return
    for h in list(driver.window_handles):
        try:
            driver.switch_to.window(h)
            if _VIEWER_MARK in (driver.current_url or "") \
                    and f"doSno={sno}" in (driver.current_url or ""):
                driver.close()
        except Exception:
            continue


def run(ready, driver):
    """逐筆送陳核。任一筆失敗即中止，回 (成功數, 失敗的那筆 or None)。"""
    from document_system import _click_doc_by_no
    from fill_in_draft import fill_in_draft

    if not focus_main_window(driver):
        print("      x  找不到公文系統主畫面（有左側選單那個分頁）")
        return 0, (ready[0] if ready else None)
    main_handle = driver.current_window_handle
    done = 0
    for it in ready:
        doc_no, d = it["文號"], it["目錄"]
        print(f"\n[{done + 1}/{len(ready)}] {doc_no} — {it['主旨'][:36]}")

        driver.switch_to.window(main_handle)
        driver.switch_to.default_content()
        if not focus_list(driver, doc_no):
            print("      x  切不到含這一筆的「承辦中」清單")
            return done, it
        if not _click_doc_by_no(driver, doc_no):
            print("      x  清單裡找不到這份公文（可能已被送走或不在承辦中）")
            return done, it

        # 認 doSno，不是認「多了一個分頁」—— edoc 會重用既有分頁（見 wait_viewer）
        if not wait_viewer(driver, doc_no):
            print("      x  等不到這份公文的閱覽器分頁")
            return done, it
        time.sleep(1.5)
        if not ensure_viewer_ready(driver):
            print("      x  公文閱覽器一直沒有正常載入 — 整批中止（沒有送出這一筆）")
            return done, it

        ok = fill_in_draft(driver, d, fragment=it["送出文字"], action="陳會")
        if not ok:
            print("      x  填字/儲存/陳會/簽章失敗 — 整批中止")
            return done, it

        with open(marker_path(d, doc_no), "w", encoding="utf-8") as f:
            f.write(f"已陳核。擬辦：{it['送出文字']}\n")
        print(f"      OK 已送陳核，寫入標記 {doc_no}{MARKER_SUFFIX}")
        done += 1
        # 送走了,把那個閱覽器分頁收掉,免得下一筆被塞進這裡
        close_viewer(driver, doc_no)
        driver.switch_to.window(main_handle)
    return done, None


def main():
    import argparse
    ap = argparse.ArgumentParser(description="批次貼擬辦 + 送陳核（讀審核表「陳會」欄）")
    ap.add_argument("--go", action="store_true", help="真的送出（預設只預覽）")
    ap.add_argument("--path", help="審核表路徑")
    a = ap.parse_args()

    ready, blocked = plan(a.path)
    _print_plan(ready, blocked)

    if not a.go:
        print(f"\n這是預覽，什麼都沒做。確認無誤後加 --go 才會真的送出。")
        return
    if not ready:
        print("\n沒有可送的公文。")
        return

    print(f"\n===== 開始送出 {len(ready)} 筆（送陳核收不回來）=====")
    from fill_in_draft import _attach_existing_chrome
    driver = _attach_existing_chrome()
    if driver is None:
        # 上面 fill_in_draft 會印「修補方式:跑 python main.py 3 重登 Chrome」——
        # 那是**結案存查**（歸檔完自動貼校網、無人工確認），跟送陳核無關，照做會出事。
        # 那支是系管師的檔案不動，所以在這裡補一段正確的蓋過去。
        print("=" * 70)
        print("[post_draft_batch] attach 不到 Chrome — 什麼都沒有送出。")
        print("  ⚠ 不要照上面那行「跑 python main.py 3」做 —— 那是結案存查，會貼校網。")
        print("  正確做法:在介面按「收新公文」(= python main.py 4) 重新登入，")
        print("  跑完**不要關掉那個 Chrome 視窗**，停在 edoc「承辦中」清單頁再送一次。")
        print("=" * 70)
        raise SystemExit(1)
    # 殘留的閱覽器分頁會害「等新分頁開出來」誤判(見 viewer_tabs)。有就停下來 ——
    # 不自動關,那裡面可能有你手動填的東西。
    vt = viewer_tabs(driver)
    if vt:
        print("=" * 70)
        print(f"[post_draft_batch] 有 {len(vt)} 個公文閱覽器分頁還開著 — 什麼都沒有送出。")
        print("  請先把那些「公文閱覽器」分頁關掉（只留公文系統主畫面），再送一次。")
        print("  原因:程式靠「有沒有多開一個分頁」判斷公文開起來了沒，")
        print("  已經開著的話它會誤判成失敗而中止。")
        print("=" * 70)
        raise SystemExit(1)
    if not focus_main_window(driver):
        print("=" * 70)
        print("[post_draft_batch] 找不到公文系統主畫面 — 什麼都沒有送出。")
        print("  請在介面按「收新公文」重新登入，跑完不要關掉那個 Chrome 視窗。")
        print("=" * 70)
        raise SystemExit(1)

    done, failed = run(ready, driver)
    print(f"\n===== 完成 {done}/{len(ready)} 筆 =====")
    if failed:
        print(f"停在 {failed['文號']}，其後未處理。修好後重跑，已送出的會自動跳過。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
