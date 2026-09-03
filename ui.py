# -*- coding: utf-8 -*-
"""
ui.py
自動辦文工具的本機介面。跑起來會自動開瀏覽器。

    python ui.py

只用 Python 標準庫（http.server），不裝任何套件 —— 之後打包給其他處室時，
對方不需要安裝環境。只綁 127.0.0.1，外面連不進來。

目前實作:
  **摘要頁**  左邊公文清單、右邊主旨／摘要／擬辦。擬辦可以直接改，改完自動存回
              桌面的「公告彙整.xlsx」—— 所以既有的 post_draft_batch.py 照樣讀得到。
              這一頁**不會送出任何東西**，改壞了頂多是表格內容要重來。
  **舊文頁**  辦完的公文。
  **陳核頁**  ⚠️ 這一頁**會真的送出**（呼叫 post_draft_batch.py --go）。
              送陳核收不回來，所以介面照 CLI 的設計走兩段:先看清單、再按送出。
              判定完全交給 post_draft_batch.evaluate()，介面不另立標準。
  **存查頁**  ⚠️ 這一頁**會真的歸檔**（呼叫 archive_batch.py --go），
              而歸檔無 admin 介入無法復原。同樣兩段，且清單要先去 edoc 讀
              （待結案清單只存在 edoc 上，磁碟推不出來），所以多一顆
              「讀待結案清單」。判定交給 archive_batch.plan()。
  **公告頁**  只產文案（呼叫 announce_doc.py），**不送出任何東西**、不碰 Chrome、
              不用讀卡機。文案可以直接改，改完自動存回審核表「公告」欄。
              唯一的破壞性動作是「重產」（會蓋掉你改過的字），所以那顆是逐筆、
              而且要再確認一次。判定交給 announce_doc.plan()。
  **找舊文頁**
              關鍵字找承辦人自己的檔案櫃（`D:\01-公文`，見 find_doc.py）。
              **只讀不寫**:不搬檔、不改名、不刪除，唯一的動作是開檔案總管。
              比對的是資料夾名（日期／文號／【標籤】／標題），不是 PDF 內文。
  **張貼頁**  ⚠️ 這一頁**會真的貼上校網**（呼叫 post_web_batch.py --go），
              而那是全站**唯一真的對外**的動作 —— 貼出去全校師生家長都看得到。
              形狀照陳核頁（三堆＋二段確認），判定交給 post_web_batch.plan()。
              ⚠️ 它對 Chrome 的要求跟陳核／存查**相反**:那兩頁要收文開的 Chrome
              開著，張貼要它**關掉**（張貼自己開一個新的，兩邊同一個 Selenium
              設定檔，見 post_web_batch.chrome_in_the_way）。
"""

import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import review_sheet as rs  # noqa: E402
import find_doc  # noqa: E402
import file_doc  # noqa: E402

PAGE = os.path.join(_BASE_DIR, "ui_page.html")
HOST, PORT = "127.0.0.1", 8760


# ── 資料 ───────────────────────────────────────────────────────────────────

def _doc_dir(doc_no):
    for base in rs.SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for n in sorted(os.listdir(base)):
            if doc_no in n and os.path.isdir(os.path.join(base, n)):
                return os.path.join(base, n)
    return None


def _status(row, doc_dir):
    """回這筆公文的狀態標籤 list。每個是 {'類型','文字'}。

    只做**便宜**的判斷 —— 清單一次要算幾十筆,不在這裡讀 PDF。
    讀 PDF 的（退文偵測）留到點開單筆時才算。
    """
    import glob
    out = []
    for gate in rs.GATES:
        v = row.get(gate)
        if rs.is_done(v):
            out.append({"類型": "done", "文字": f"{gate}已辦"})
        elif rs.is_approved(v):
            out.append({"類型": "ok", "文字": f"{gate} OK"})
    if doc_dir and glob.glob(os.path.join(doc_dir, "*含個資.txt")):
        out.append({"類型": "warn", "文字": "含個資，未送 AI"})
    if not (row.get("擬辦") or "").strip():
        out.append({"類型": "wait", "文字": "無擬辦"})
    elif "請自行填寫" in str(row.get("擬辦")):
        out.append({"類型": "warn", "文字": "擬辦待你填"})
    return out


def rows_payload():
    """回清單。**每次都先把資料夾同步進審核表** —— 使用者不該為了看到新公文
    去記得跑 review_sheet.py（2026-07-29:收完文畫面沒變,就是卡在這）。

    同步寫得進去就寫；Excel 開著寫不進去也不要擋住畫面,照樣把現有資料顯示出來,
    另外用 note 告訴使用者。
    """
    note = None
    try:
        added, updated, _, skipped = rs.sync()
        if added:
            note = f"帶入 {added} 筆新公文"
        if skipped:
            note = (note + "；" if note else "") + \
                   f"{skipped} 筆還沒摘要（跑 python review_sheet.py --prepare 補）"
    except PermissionError:
        note = "公告彙整.xlsx 正被 Excel 開著，新公文暫時進不來（關掉 Excel 再按重新整理）"
    except Exception as e:
        note = f"同步時出錯:{type(e).__name__}: {e}"

    out = []
    for r in rs.rows():
        d = _doc_dir(str(r["文號"]).strip())
        out.append({
            "文號": r["文號"],
            "主旨": r.get("主旨") or "",
            "摘要": r.get("摘要") or "",
            "擬辦": r.get("擬辦") or "",
            "公告": r.get("公告") or "",
            "陳會": r.get("陳會") or "",
            "張貼": r.get("張貼") or "",
            "目錄": d or "",
            "狀態": _status(r, d),
            # 辦完的公文歸「舊文」頁,不再佔著待辦清單。判斷規則見
            # review_sheet.is_archived(已存查 ＋ 不用公告或已張貼)。
            # 這裡只標記不過濾 —— 兩個分頁共用同一次 /api/rows,切分頁不必重讀。
            "舊文": rs.is_archived(r, d),
        })
    return out, note


ATTACH_CHOICE = "附件選擇.json"

# 以下勾選相關的程式留給**「公告」頁**（第 5 步）用，摘要頁不出現勾選框。
# 2026-07-29 承辦人指正:摘要階段只需要「看得到資料夾裡有什麼、檔名對不對」,
# 決定傳哪幾個是公告那一步的事,兩件事不該混在同一頁。


def attach_choice(doc_dir):
    """讀使用者在介面上勾選的附件（檔名 list）。沒選過回 None（= 用預設判斷）。

    「是附件」不等於「要上傳」—— 一份公文常附三、四個檔，實際要放上校網的
    可能只有一兩個。預設值只是起點，最終由承辦人勾。
    """
    p = os.path.join(doc_dir, ATTACH_CHOICE)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            v = json.load(f)
        return set(v) if isinstance(v, list) else None
    except Exception:
        return None


def save_attach_choice(doc_dir, names):
    with open(os.path.join(doc_dir, ATTACH_CHOICE), "w", encoding="utf-8") as f:
        json.dump(sorted(names), f, ensure_ascii=False, indent=1)


def _files_of(doc_dir):
    """列出公文資料夾裡的檔案，標出類型與「是否上傳」。

    看得到檔案列表比「開啟檔案總管」有用 —— 承辦人多數時候只是想確認
    裡面有什麼、附件會不會傳對，不需要真的離開這個畫面。
    上傳與否可逐檔勾選，勾了才算數；沒勾過就用程式的預設判斷當起點。
    """
    from document_closure.document_closure_post_web import (
        _MAIN_DOC_FILE_RE, _is_attachment, _find_attachments)
    chosen = attach_choice(doc_dir)
    # 預設勾選 = 程式的自動判斷。注意要在「沒有選擇檔」的前提下算,否則
    # _find_attachments 會直接回選擇結果,拿它當預設就變成循環。
    if chosen is None:
        try:
            auto = {os.path.abspath(p) for p in _find_attachments(doc_dir)}
        except Exception:
            auto = set()
    else:
        auto = set()

    def classify(name):
        """這個檔**是什麼** —— 與「要不要上傳」無關。

        2026-07-29 bug:原本用「有沒有被選上傳」決定標籤,承辦人一改附件檔名,
        選擇檔對不上 → 該檔掉出附件集合 → 掉進 fallthrough 被標成「來文主檔」。
        標籤要看檔案本身,不能看勾選狀態。
        """
        low = name.lower()
        if "opinion" in low:
            return "簽核意見"
        if name.startswith("合併版"):
            return "合併版"
        if name.endswith(HIDE) or "總結" in name or name.endswith(".bak"):
            return "產出"
        if _MAIN_DOC_FILE_RE.match(name):
            return "來文主檔"
        if _is_attachment(name):
            return "附件"
        return "其他"

    HIDE = ("內容.txt", "已公告.txt", "已存查.txt", "已陳核.txt", "含個資.txt")
    out = []
    for dp, _, names in os.walk(doc_dir):
        for n in sorted(names):
            p = os.path.join(dp, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            if n == ATTACH_CHOICE:
                continue
            out.append({
                "檔名": n, "大小": size, "類型": classify(n),
                "上傳": (n in chosen) if chosen is not None
                        else (os.path.abspath(p) in auto),
                "子資料夾": os.path.relpath(dp, doc_dir).replace(".", ""),
            })
    return out


def detail_payload(doc_no):
    """點開單筆時才算的東西（會讀 PDF，比較慢）。"""
    import post_draft_batch as pdb
    d = _doc_dir(doc_no)
    info = {"文號": doc_no, "目錄": d or "", "提醒": []}
    if not d:
        info["提醒"].append({"類型": "warn", "文字": "找不到公文資料夾"})
        return info

    returned, when = pdb.was_returned(d)
    if returned:
        info["提醒"].append({"類型": "warn",
                            "文字": f"這份公文被退過 — {when}"})

    prefix = pdb.doc_prefix(pdb._read_content(d))
    info["發文字別"] = prefix or ""
    info["檔案"] = _files_of(d)

    # 改過檔名的話,選擇檔裡的舊名字會對不上 → 那個檔會默默變成沒勾。
    # 而改名多半正是因為「要放上校網所以取個好名字」,默默取消最糟。講出來。
    chosen = attach_choice(d)
    if chosen:
        here = {f["檔名"] for f in info["檔案"]}
        gone = sorted(chosen - here)
        if gone:
            info["提醒"].append({
                "類型": "warn",
                "文字": f"附件勾選有 {len(gone)} 個對不上（{'、'.join(gone[:3])}"
                        f"{'…' if len(gone) > 3 else ''}）—— 檔名可能改過，"
                        f"請重新勾一次要上傳的檔。"})
    row = next((r for r in rs.rows() if str(r["文號"]).strip() == doc_no), {})
    stop, why = pdb.routing_hit(f"{row.get('主旨') or ''}\n{row.get('摘要') or ''}",
                                None, prefix)
    if stop:
        info["提醒"].append({"類型": "warn", "文字": f"{why}，不會自動送陳核"})
    return info


# ── 陳核頁 ─────────────────────────────────────────────────────────────────
#
# 這一頁是**唯一會送出東西**的分頁。設計原則跟 post_draft_batch.py 的 CLI 一樣:
#
#   1. 判定不在這裡寫。能不能送一律問 post_draft_batch.evaluate()，介面只負責
#      把結果畫出來 —— 兩套規則遲早會不一致，而不一致的方向可能是「畫面說擋、
#      其實送出去了」。
#   2. 勾選 = 寫審核表「陳會」欄。介面勾的跟 Excel 打的 OK 是同一件事，
#      所以承辦人想用 Excel 或 CLI 也照樣通。
#   3. 送出走 subprocess `post_draft_batch.py --go`，不在這支行程裡開 Selenium。
#      實跑的是那支已經寫好安全關卡（逐筆失敗即中止、寫已陳核.txt 不重送）的程式。

DEVTOOLS_LIST = "http://127.0.0.1:9222/json/list"


def chrome_state():
    """送陳核前的連線檢查。回 {"ok": bool, "說明": str or None}。

    為什麼要在介面先擋:attach 不到 Chrome 時，畫面會吐一整片 chromedriver 的
    英文堆疊，而 `fill_in_draft` 印的修補建議是「跑 `python main.py 3`」——
    那是**結案存查**（歸檔完會自動貼校網、沒有人工關卡），跟送陳核完全無關，
    照做會出事。那支是系管師的檔案不能改，所以改成不要讓人走到那一步。
    2026-08-05 承辦人就是撞上這個:收文跑完把 Chrome 關掉，回來按送出。

    只讀 DevTools 的分頁清單，不動任何東西。連不上就是沒開，很明確。
    """
    import urllib.request
    try:
        with urllib.request.urlopen(DEVTOOLS_LIST, timeout=1.5) as r:
            tabs = json.load(r)
    except Exception:
        return {"ok": False,
                "說明": "自動化用的 Chrome 沒有開著。請先按右上角「收新公文」把它開起來，"
                        "而且跑完之後不要關掉那個 Chrome 視窗 —— 送陳核要接著用它。"}
    urls = [t.get("url") or "" for t in tabs if t.get("type") == "page"]
    import post_draft_batch as pdb
    # 「操作時間逾期」的警告視窗先講 —— 它是確診（其他訊號都只是推測），
    # 而且下一步多一件事:那個小視窗要先關掉。
    #
    # ⚠️ 這道檢查一定要在下面兩道「有沒有主畫面」之前。它的網址是
    # /tcqb/home/sessionTimeout.jsp，**含 /tcqb/home/** —— 2026-08-10 承辦人
    # 收文時它一直跳，而那時這裡是用 `any("/tcqb/home/" in u ...)` 認主畫面，
    # 被它冒充過去，chrome_state 對一個已經被踢出去的 Chrome 回 ok=True。
    # 陳核頁與存查頁共用這盞燈，等於兩頁都亮綠燈讓人按下去。
    if pdb.looks_timed_out(urls):
        return {"ok": False,
                "說明": "edoc 跳出「操作時間逾期，請您重新登入」—— 閒置太久被踢出來了"
                        "（收文時每份公文要等 AI 寫摘要 1～2 分鐘，edoc 那邊就是在那時候"
                        "閒置的）。請先關掉那個灰色的警告小視窗，再按右上角「收新公文」"
                        "重新登入。"}
    if pdb.looks_logged_out(urls) and not pdb.has_home(urls):
        return {"ok": False,
                "說明": "edoc 已經登出了（閒置太久，或按過登出）。請按右上角"
                        "「收新公文」重新登入，跑完不要關掉那個 Chrome 視窗。"}
    if not any("edoc.gov.taipei" in u for u in urls):
        return {"ok": False,
                "說明": "Chrome 開著，但沒有停在公文系統。請把那個視窗切回 edoc 的"
                        "「承辦中」清單頁再送。"}
    # 殘留的「公文閱覽器」分頁會害送出流程誤判（它靠「有沒有多開一個分頁」判斷
    # 公文開起來了沒），而且它同樣是 edoc 網域，光看網域檢查不出來。
    # 2026-08-05 實測:一個開著的 7696 閱覽器分頁就讓整批停在第一筆。
    #
    # ⚠️ 這裡原本寫死 `app=editor`（送陳核那種閱覽器），**存查那種是 `app=check`**，
    # 於是 2026-08-10 存查第一次實跑時，8/6 送陳核留下的 7710 閱覽器分頁照樣過關，
    # document_closure 切閱覽器時抓到它而不是剛點開的 7696 —— 在錯的公文上判「如擬」
    # 並按下載。改吃 post_draft_batch 那個涵蓋三種的特徵，兩頁共用同一份定義。
    viewers = [u for u in urls if pdb._VIEWER_MARK in u]
    if viewers:
        return {"ok": False,
                "說明": f"有 {len(viewers)} 個「公文閱覽器」分頁還開著。請先把它們關掉，"
                        f"只留公文系統的主畫面 —— 程式靠分頁判斷公文開起來了沒，"
                        f"已經開著的話會抓錯分頁（可能因此對到別份公文）而中止。"}
    # 用 pdb.has_home 而不是自己比對 "/tcqb/home/" —— 那個路徑底下不是只有
    # 主畫面（sessionTimeout.jsp 也在），定義只寫一份，兩邊不會分岔。
    if not pdb.has_home(urls):
        return {"ok": False,
                "說明": "找不到公文系統的主畫面（左側有選單那個頁面）。"
                        "請把 Chrome 切回去，或按「收新公文」重新登入。"}
    return {"ok": True, "說明": None}


def send_payload():
    """陳核頁的清單。回 (dict, 提醒字串 or None)。

    分三堆給畫面:
      可送   —— 「陳會」已 OK 且判定過關。按送出就是送這幾筆。
      擋下   —— 「陳會」已 OK 但判定不給送（退過文、他人業務、擬辦沒寫…）。
      候選   —— 還沒 OK 的待辦。順手把「就算勾了也會被擋」先算出來標紅，
                 免得承辦人勾完才發現送不了。
    """
    import post_draft_batch as pdb
    note = None
    try:
        rs.sync()
    except PermissionError:
        note = "公告彙整.xlsx 正被 Excel 開著，勾選會存不進去（先關掉 Excel）"
    except Exception as e:
        note = f"同步時出錯:{type(e).__name__}: {e}"

    flags = pdb.routing_flags()
    ready, blocked, cand, sent = [], [], [], []

    def slim(it, extra=None):
        out = {k: it.get(k) for k in
               ("文號", "主旨", "送出文字", "移除的提示", "擋下原因")}
        out["目錄"] = it.get("目錄") or ""
        if extra:
            out.update(extra)
        return out

    for r in rs.rows():
        no = str(r["文號"]).strip()
        d = _doc_dir(no)
        # 辦完的、以及自己宣告「陳會已辦」的都不該再出現在這一頁。
        if rs.is_archived(r, d) or rs.is_done(r.get("陳會")):
            continue
        # 磁碟上有 *已陳核.txt = 陳核這一關做完了，下一站是存查，不該再佔這頁。
        #
        # 「陳會」欄這時可能還停在 OK ——「upsert 只填空白格」，承辦人當初打的
        # OK（=請程式去送）在送完之後不會被改寫（交接檔坑 #1）。原本這種筆會
        # 一直列在「勾了要送，但送不出去」，理由寫「已有 已陳核.txt，先前送過」，
        # 佔滿畫面又要人一筆一筆取消勾選（2026-08-06 承辦人回報:「我取消勾選後
        # 怎麼避免下次又選到」）。以磁碟痕跡為準直接跳過，不必他動手。
        if d and pdb.already_sent(d, no):
            sent.append(no)
            continue
        it = pdb.evaluate(r, flags)
        if rs.is_approved(r.get("陳會")):
            (blocked if it.get("擋下原因") else ready).append(slim(it))
        else:
            cand.append(slim(it))
    return {"可送": ready, "擋下": blocked, "候選": cand, "已送過": sent,
            # 你標成「自己辦掉了」的那幾筆。標了就從上面三堆消失（程式不該再碰），
            # 但**消失之後要有地方反悔** —— 標錯一筆的代價是那份公文從此沒人辦。
            "自辦": [{"文號": str(r["文號"]).strip(), "主旨": r.get("主旨") or ""}
                     for r in rs.marked_done("陳會")],
            "chrome": chrome_state()}, note


# ── 存查頁 ─────────────────────────────────────────────────────────────────
#
# 跟陳核頁差在**清單從哪裡來**。陳核的候選算得出來（審核表 ＋ 磁碟），存查的
# 「待結案有哪幾筆」只存在 edoc 上，非得去讀不可 —— 而讀它要點左側選單，等於
# 在操作 Chrome。所以這一頁不是打開就自動算，而是要按「讀待結案清單」，走
# 跟收文／送陳核同一把互斥鎖（`SCAN` 這支 Job）。
#
# 判定一樣不在這裡寫:`archive_batch.py --json` 印一行 JSON，這裡只負責撈出來。
# 兩套規則遲早分岔，而最壞的方向是「畫面說會停下、其實歸檔了」。

def scan_plan():
    """從 SCAN 那支印出來的東西裡撈計畫。沒跑過或撈不到回 None。

    往回找而不是取最後一行 —— selenium／urllib3 隨時可能在 JSON 之後再吐東西。
    """
    import archive_batch as ab
    for line in reversed(SCAN.lines):
        if line.startswith(ab.PLAN_MARK):
            try:
                return json.loads(line[len(ab.PLAN_MARK):])
            except json.JSONDecodeError:
                return None
    return None


# 上一批送出去歸檔的是哪幾筆。
#
# 為什麼要記:歸檔一按下去，待結案清單就作廢（`SCAN.lines = []`），所以跑完之後
# 這一頁會退回「還沒讀過清單」—— 跟從來沒跑過長得一模一樣。進度面板那句「歸檔
# 結束」3 秒後自己收起來，收掉就什麼痕跡都不剩。2026-08-31 承辦人回報「存查完
# 沒有訊息告知已經存查完畢」講的就是這件事:**歸檔是不可復原的動作，做完了卻要
# 自己去 edoc 對才知道有沒有成**。
ARCH_SENT = None            # {"時間": "08/31 14:05", "預期": [{"文號","主旨"}…]}


def remember_arch_batch(p):
    """記下這一批送出去的是哪幾筆。p=None 代表把上一批的結果清掉。

    只記 `會歸檔` 那幾筆 —— 停止點後面的公文這一批根本輪不到，把它們算成
    「沒做到」會讓人以為出了事。
    """
    global ARCH_SENT
    if p is None:
        ARCH_SENT = None
        return
    subj = {it["文號"]: (it.get("主旨") or "") for it in (p.get("清單") or [])}
    ARCH_SENT = {
        "時間": datetime.now().strftime("%m/%d %H:%M"),
        "預期": [{"文號": no, "主旨": subj.get(no, "")}
                for no in (p.get("會歸檔") or [])],
    }


def archive_result():
    """上一批歸檔的結果。沒跑過、或還在跑，回 None。

    「哪幾筆真的歸檔了」**不解析程式印出來的字**，看磁碟上的存查標記檔 ——
    那是驗證「文號已從待結案清單消失」之後才寫的那一份，也是全站其他地方
    （舊文、`plan()` 的 danger 判定）認的同一個痕跡。自己另外解析輸出等於
    第二套標準，而最壞的分岔方向是「畫面說歸檔了、其實沒有」。

    送出前這幾筆一定**沒有**標記（有的話 plan() 會判成 danger，整批不給按），
    所以現在有標記就是這一批寫上去的。
    """
    if ARCH_SENT is None or ARCH.running or ARCH.exit is None:
        return None
    import archive_batch as ab
    done, miss = [], []
    for it in ARCH_SENT["預期"]:
        try:
            marked = bool(ab.evaluate(it["文號"]).get("已存查標記"))
        except Exception:
            marked = False          # 讀不到就當沒做到 —— 往「請自己去看」的方向錯
        (done if marked else miss).append(it)
    return {"時間": ARCH_SENT["時間"], "結束碼": ARCH.exit,
            "完成": done, "沒做到": miss}


def archive_payload():
    """存查頁要的東西。回 dict。

    「掃過沒」與「掃出什麼」分開講 —— 沒掃過跟掃出來是空的，下一步完全不同
    （前者要按讀清單，後者是真的沒有待結案公文）。
    """
    p = scan_plan()
    return {
        "chrome": chrome_state(),
        "掃過": p is not None,
        "掃描中": SCAN.running,
        "計畫": p,
        # 上一批歸檔的結果留在這一頁上，直到下次重讀清單為止（見 ARCH_SENT）。
        "上批": archive_result(),
    }


# ── 公告頁 ─────────────────────────────────────────────────────────────────
#
# 全站唯一**不會送出任何東西**的動作頁:只叫 LLM 產文案、寫進審核表「公告」欄。
# 不用 Chrome、不用讀卡機、不碰 edoc。真的貼上校網是「張貼」那一頁的事 ——
# 2026-07-28 出事的正是「歸檔完自動貼校網」，所以「這一步會不會送出去」
# 必須在畫面上一眼看得出來，而不是靠人記得。
#
# 判定一樣不在這裡寫，一律吃 announce_doc.plan():
#   會產     = plan(overwrite=False) 的 todo
#   已有文案 = 公告欄有字的（能不能重產 = 它在不在 plan(overwrite=True) 的 todo 裡）
#   不會產   = 其餘的 skip，附 plan() 給的原因原文
# 「打過 OK 的定稿即使 overwrite 也不動」這條護欄（2026-07-28 把承辦人手寫的
# 公告蓋掉三次）就長在 plan() 裡，介面照抄它的答案就自動有這道保護。

def announce_payload(sent_only=True):
    """公告頁的清單。回 (dict, 提醒字串 or None)。

    sent_only 預設**開著**（CLI 預設是關的）:走到這一頁時公文多半已經陳核，
    而還沒陳核的擬辦還可能被改，先產文案容易白做、白花 token。
    這不是另立判定標準 —— 它就是 CLI 的 --sent-only，畫面上有勾選框可以關掉，
    被它濾掉的公文會出現在「不會產」並寫明原因，不會默默消失。
    """
    import announce_doc as ad
    note = None
    try:
        rs.sync()
    except PermissionError:
        note = "公告彙整.xlsx 正被 Excel 開著，產出的文案會存不進去（先關掉 Excel）"
    except Exception as e:
        note = f"同步時出錯:{type(e).__name__}: {e}"

    sheet = {str(r["文號"]).strip(): r for r in rs.rows()}
    todo, skip = ad.plan(overwrite=False, sent_only=sent_only)
    # 再問一次「如果准覆寫，這幾筆做不做得到」—— 這就是每一筆的「可不可以重產」。
    # 自己判「有沒有打 OK」會變成第二套標準，遲早跟 plan() 分岔。
    ow_todo, ow_skip = ad.plan(overwrite=True, sent_only=sent_only)
    can_redo = {it["文號"] for it in ow_todo}
    ow_why = {it["文號"]: it["略過"] for it in ow_skip}

    def archived(no):
        """辦完的公文歸「舊文」，不佔這一頁 —— 跟陳核頁同一條規則。

        這些公文在 plan() 裡本來就一定落在 skip（張貼已辦／不用公告／
        公告欄已有內容），所以濾掉它們不會讓「畫面說不產、CLI 卻會產」。
        """
        return rs.is_archived(sheet.get(no) or {}, _doc_dir(no))

    def base(it):
        return {"文號": it["文號"], "主旨": it["主旨"],
                "目錄": it.get("目錄") or ""}

    ready = [base(it) for it in todo if not archived(it["文號"])]
    have, other = [], []
    for it in skip:
        no = it["文號"]
        if archived(no):
            continue
        text = str((sheet.get(no) or {}).get("公告") or "")
        if text.strip():
            have.append({
                **base(it), "文案": text,
                "可重產": no in can_redo,
                "不可重產原因": ow_why.get(no) or "",
                # 文案裡有「來文沒有的網址」—— announce_doc 標了但沒刪，
                # 那是貼上校網前一定要人看的東西，不能只躺在文字裡。
                "待查核": ad.STRAY_MARK in text,
                "張貼": (sheet.get(no) or {}).get("張貼") or "",
            })
        else:
            other.append({**base(it), "略過": it["略過"]})
    return {"會產": ready, "已有文案": have, "不會產": other,
            # 這一頁的「我自己辦掉了」寫的是「張貼」欄（公告文案與張貼是同一條軌:
            # announce_doc.plan() 看到那一欄已辦就整筆略過）。所以放回的入口
            # 跟張貼頁是同一份清單，兩頁都看得到、都能放回。
            "自辦": [{"文號": str(r["文號"]).strip(), "主旨": r.get("主旨") or ""}
                     for r in rs.marked_done("張貼")],
            "只看已陳核": bool(sent_only)}, note


# ── 張貼頁 ─────────────────────────────────────────────────────────────────
#
# **全站唯一真的對外的動作。** 陳核送錯還在校內、歸檔歸錯是找 admin 的事，
# 貼錯是全校師生家長都看到了（可事後刪，但已經被看到就是被看到了）。
# 2026-07-28 出事的正是這一步:`main.py 3` 歸完檔自動貼校網，沒有人工確認。
#
# 形狀照**陳核頁**（會貼／擋下／待辦 三堆 ＋ 二段確認），**不照存查頁** ——
# 存查那頁「順序是資訊」是因為 process_document_closure 只做清單第一筆，
# 張貼沒有這回事，逐筆各自獨立。
#
# 判定一律吃 post_web_batch.plan()，介面不另立標準（同陳核頁的理由:兩套規則
# 遲早分岔，而最壞的方向是「畫面說會擋、其實貼出去了」）。
#
# ⚠️ Chrome 的要求跟陳核／存查**相反**。那兩頁 attach 收文開的那個 Chrome，
# 所以要它開著;張貼不碰 edoc，是自己開一個新的，而兩邊用同一個 Selenium 設定檔
# ＋同一個 9222 埠 —— 所以這一頁要它**關掉**。定義只寫在
# post_web_batch.chrome_in_the_way() 一處，那支動手前也會自己再檢查一次。

def web_payload():
    """張貼頁的清單。回 (dict, 提醒字串 or None)。

    分三堆，跟陳核頁同一組意思:
      可貼 —— 「張貼」欄已 OK 且判定過關。按下去就是貼這幾筆。
      擋下 —— 「張貼」欄已 OK 但貼不出去（公告欄空的、文案有待查核網址、
               已經貼過…）。
      候選 —— 還沒 OK 的。順手把「就算勾了也貼不出去」先算好標紅。
    """
    import post_web_batch as pwb
    note = None
    try:
        rs.sync()
    except PermissionError:
        note = "公告彙整.xlsx 正被 Excel 開著，勾選會存不進去（先關掉 Excel）"
    except Exception as e:
        note = f"同步時出錯:{type(e).__name__}: {e}"

    ready, blocked, cand, warn = pwb.plan()
    busy = pwb.chrome_in_the_way()
    return {"可貼": [pwb._slim(i) for i in ready],
            "擋下": [pwb._slim(i) for i in blocked],
            "候選": [pwb._slim(i) for i in cand],
            "警告": warn,
            # 同陳核頁:標成「自己辦掉了」的要有地方看見、有地方反悔。
            # 這一頁也是「張貼」那一關的反悔處 —— 公告頁標的是同一欄。
            "自辦": [{"文號": str(r["文號"]).strip(), "主旨": r.get("主旨") or ""}
                     for r in rs.marked_done("張貼")],
            # 這盞燈跟陳核／存查那盞（chrome_state）**不是同一件事**，
            # 條件正好相反 —— 不要共用，共用就一定有一頁是錯的。
            "chrome": {"ok": busy is None, "說明": busy}}, note


# ── 設定頁 ─────────────────────────────────────────────────────────────────
#
# 分兩區:**個人**（PIN、校網帳密、API key）與**共用**（發布單位、發布者、摘要
# 要叫哪些 AI…）。承辦人 2026-08-12 的原話:「KEY 不是應該個人填個人的」——
# 這套工具會交給下一個人，密鑰跟著人不跟著工具。所以:
#
#   · **密鑰的值不回傳前端**（`env_config.state()` 只給「有沒有填」）。看得到就會
#     被截圖、被貼進交接檔，而這個 repo 是 public。
#   · 存檔的回應只回**鍵名**，不回值;log 也不印值。
#   · `pin` 最敏感 —— 打錯會在下次收文時鎖卡（要跑戶政事務所），所以介面那顆
#     要再問一次，而且只收數字。
#
# 規格檔（判斷都寫在那幾份裡）也放這一頁的入口 —— 現在得自己去翻檔案。

SPEC_FILES = ["summarize_doc.md", "announce_doc.md", "routing_flags.yaml",
              "file_doc.md"]


def find_payload():
    """找舊文頁要的東西:整個檔案櫃的清單一次送過去，之後打字都在瀏覽器裡篩。

    為什麼一次全送:1500 筆的名字大約 200KB，本機讀完不到一秒；換成每打一個字
    就往後端問一次，反而卡。**這一頁只讀不寫** —— 沒有任何按鈕會動到櫃子裡的檔案，
    唯一的動作是「在檔案總管開啟」。

    順便算「工作區有、櫃子裡還沒有」的筆數:那幾筆在這裡**搜不到**，
    畫面要講清楚，不然承辦人會以為公文不見了。
    """
    root = find_doc.archive_root()
    rows = find_doc.scan(root)
    miss = find_doc.not_archived(rows)
    note = ""
    if not os.path.isdir(root):
        note = f"找不到檔案櫃:{root}。要改路徑就在 env.env 加一行 archive_root=..."
    elif miss:
        note = (f"工作區還有 {len(miss)} 筆沒複製進櫃子，那幾筆在這裡搜不到。")
    return {"櫃子": root, "rows": rows, "標籤": find_doc.tags(rows),
            "未歸檔": miss, "訊息": note}


def file_payload():
    """歸檔（複製進檔案櫃）要的計畫。**只計算，不動任何檔案。**

    ⚠️ 這裡的「歸檔」是複製進承辦人自己的 `D:\01-公文`，
    跟 edoc 的「結案存查」（存查頁）不是同一件事，畫面上要講清楚。
    """
    return file_doc.plan()


def settings_payload():
    """設定頁要的東西。**不含任何密鑰的值。**

    規格檔那兩份判斷（他人業務、存查分類對應表）一併變成**表單**送出去 ——
    承辦人 2026-08-12 的問題:「MD 檔沒在寫程式的同仁會不知道怎麼處理」。
    而 `routing_flags.yaml` 改壞的後果是**靜靜失效**（`routing_flags()` 讀不到就
    回全空 = 不攔任何東西），所以那份尤其不能叫人去改原始檔。
    """
    import env_config as ec
    import spec_forms as sf
    specs = []
    for n in SPEC_FILES:
        p = os.path.join(_BASE_DIR, n)
        specs.append({"檔名": n, "路徑": p, "在": os.path.isfile(p)})
    out = {**ec.state(), "規格檔": specs}
    try:
        out["他人業務"] = sf.routing_state()
    except Exception as e:
        out["他人業務"] = {"錯誤": f"{type(e).__name__}: {e}"}
    try:
        out["對應表"] = sf.table_state()
    except Exception as e:
        out["對應表"] = {"錯誤": f"{type(e).__name__}: {e}"}
    return out


# ── 備料（呼叫 py main.py 4）────────────────────────────────────────────────
#
# 用 subprocess 跑，**完全不改系管師的程式碼** —— 呼叫不等於修改。
# 只接它印出來的東西轉給畫面。
#
# 為什麼要背景跑:9 份公文大約 3～5 分鐘（每份下載 + 一次 LLM），
# 瀏覽器的請求撐不了那麼久。

def _launch_hud(job="prep", label=None):
    """開右下角的進度小浮窗（prep_hud.py）。

    為什麼需要:`main.py` 登入時會把 Chrome 最大化,整片蓋掉這個網頁,承辦人
    就看不到下面那個 #preplog 面板在跑什麼(2026-08-03 回報)。浮窗擺右下角、
    置頂,蓋不住也不會擋到畫面中央的 KdApp 下載對話框。

    **best-effort**:浮窗只是輔助,開不起來一律吞掉 —— 絕不能因為它而讓
    收文工作起不來。用 pythonw 才不會多跳一個黑色主控台視窗。
    """
    try:
        exe = sys.executable
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            exe = w
        cmd = [exe, os.path.join(_BASE_DIR, "prep_hud.py"),
               f"http://{HOST}:{PORT}", "0", f"--job={job}"]
        if label:
            cmd.append(f"--label={label}")
        subprocess.Popen(
            cmd, cwd=_BASE_DIR,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        print(f"[ui] 進度浮窗開不起來(不影響工作):{type(e).__name__}: {e}")


class Job:
    """跑一支外部程式，把它印的東西接給畫面。

    **互斥是跨 Job 的，不是每個 Job 各一把鎖** —— 收新公文與送陳核都在操作
    同一個 Chrome，兩個 Selenium 搶同一個瀏覽器一定出事。所以任一支在跑，
    另一支就不准起來。
    """

    _lock = threading.Lock()
    _busy = None                            # 目前占著 Chrome 的 Job

    def __init__(self, name, argv, hud=None, hud_label=None):
        self.name = name
        self.argv = argv
        self.hud = hud                      # 要開浮窗的話填 prep_hud 的 --job 值
        self.hud_label = hud_label
        self.proc = None
        self.lines = []
        self.exit = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        with Job._lock:
            busy = Job._busy
            if busy is not None and busy.running:
                return False, ("已經在跑了" if busy is self else
                               f"「{busy.name}」正在跑，等它結束再來"
                               f"（兩件事會搶同一個 Chrome）")
            self.lines, self.exit = [], None
            try:
                self.proc = subprocess.Popen(
                    [sys.executable, *self.argv], cwd=_BASE_DIR,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:
                return False, f"啟動失敗:{type(e).__name__}: {e}"
            Job._busy = self
        threading.Thread(target=self._pump, daemon=True).start()
        if self.hud:
            _launch_hud(self.hud, self.hud_label)
        return True, None

    def _pump(self):
        p = self.proc
        for line in p.stdout:
            s = line.rstrip()
            if s:
                self.lines.append(s)
                del self.lines[:-400]          # 只留最後 400 行,不要吃記憶體
        self.exit = p.wait()
        self.lines.append(f"── 結束（代碼 {self.exit}）──")

    def stop(self):
        if not self.running:
            return False, "沒有正在執行的工作"
        self.proc.terminate()
        self.lines.append("── 已由使用者停止 ──")
        return True, None

    def status(self, since=0):
        return {"執行中": self.running, "起點": since,
                "訊息": self.lines[since:], "總行數": len(self.lines),
                "結束碼": self.exit}


# 收新公文走 fork 的 prep_batch.py，**不是** `main.py 4`。
# 差別:prep_batch 先把公文全部下載完，再離線補摘要。原本那條是每下載一份就
# 等 AI 寫 1～2.5 分鐘的摘要，edoc 在那段閒置到跳「操作時間逾期」，後面的公文
# 就變成「切不到清單 frame,跳過」——而且結束碼 0，看起來像正常跑完（坑 #19）。
# `main.py 4` 本身一行沒改，系管師照樣跑得動。
PREP = Job("收新公文", ["prep_batch.py"], hud="prep")
# 送陳核也開浮窗:這支同樣在操作 Chrome，Chrome 一到前景就把這個網頁蓋掉，
# 而這是**會真的送出**的一段，看不到進度最讓人心慌（2026-08-03 收文那邊同因）。
SEND = Job("送陳核", ["post_draft_batch.py", "--go"],
           hud="send", hud_label="送陳核中…請不要動滑鼠")
# 讀待結案清單。**不歸檔**，但會點左側「待結案」把清單叫出來 —— 那是在操作
# 同一個 Chrome，所以照樣要走互斥鎖（收文中按這顆會被擋下並說是誰在跑）。
SCAN = Job("讀待結案清單", ["archive_batch.py", "--json"])
# 真的歸檔。argv 每次按送出前重寫（要帶 --expect=<畫面上那幾筆>）。
ARCH = Job("結案存查", ["archive_batch.py", "--go"],
           hud="archive", hud_label="存查歸檔中…請不要動滑鼠")
# 產公告文案。**不碰 Chrome、不用讀卡機**，所以不開浮窗 —— 那顆浮窗的作用是
# 「現在不能動滑鼠」，這一段可以動，開了反而是誤導（坑 #13 的反面）。
# 但它照樣走同一把互斥鎖:跟收文一樣會寫桌面那份審核表，兩支同時 load→save
# 同一個 xlsx 會互相蓋掉，先存的那邊直接消失。
# argv 每次按下去前重寫（要帶 --only=<畫面上那幾筆>，**絕不用 --limit**）。
ANNC = Job("產生公告文案", ["announce_doc.py"])
# 貼上校網。**唯一真的對外**的一支。argv 每次按送出前重寫（要帶 --expect）。
# 開浮窗:它會自己開一個 Chrome 去填校網的表單，那段不能動滑鼠。
# 但浮窗的字只講該講的 —— 這一段**不碰 edoc、不用插卡、螢幕鎖不鎖無所謂**，
# 多寫一句假的警告會讓真的警告也被當成裝飾（坑 #13）。
WEB = Job("貼上校網", ["post_web_batch.py", "--go"],
          hud="web", hud_label="貼上校網中…請不要動滑鼠")

# 進度面板／停止鈕共用同一組路由。名字就是網址裡的那一段:/api/<名字>/status。
JOBS = {"prep": PREP, "send": SEND, "scan": SCAN, "archive": ARCH,
        "announce": ANNC, "web": WEB}


# ── HTTP ───────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                    # 不要洗版

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(PAGE, encoding="utf-8") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                return self._send(500, f"讀不到 {PAGE}：{e}", "text/plain; charset=utf-8")
        if path == "/api/rows":
            try:
                rows, note = rows_payload()
                return self._json({"ok": True, "rows": rows, "訊息": note})
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "錯誤": "還沒有任何公文。請先按「收新公文」。"})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path.startswith("/api/detail/"):
            return self._json({"ok": True, "detail": detail_payload(path.rsplit("/", 1)[-1])})
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[2] == "status" \
                and parts[1] in JOBS:
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            since = int((q.get("since") or ["0"])[0])
            return self._json({"ok": True, **JOBS[parts[1]].status(since)})
        if path == "/api/send/plan":
            try:
                data, note = send_payload()
                return self._json({"ok": True, **data, "訊息": note})
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "錯誤": "還沒有任何公文。請先按「收新公文」。"})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path == "/api/archive/plan":
            try:
                return self._json({"ok": True, **archive_payload()})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path == "/api/announce/plan":
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            sent = (q.get("sent") or ["1"])[0] != "0"
            try:
                data, note = announce_payload(sent_only=sent)
                return self._json({"ok": True, **data, "訊息": note})
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "錯誤": "還沒有任何公文。請先按「收新公文」。"})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path == "/api/web/plan":
            try:
                data, note = web_payload()
                return self._json({"ok": True, **data, "訊息": note})
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "錯誤": "還沒有任何公文。請先按「收新公文」。"})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path == "/api/file/plan":
            try:
                return self._json({"ok": True, **file_payload()})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path == "/api/find/plan":
            try:
                return self._json({"ok": True, **find_payload()})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path == "/api/settings/plan":
            try:
                return self._json({"ok": True, **settings_payload()})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
        if path == "/api/open":
            return self._json({"ok": False, "錯誤": "缺少參數"})
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"ok": False, "錯誤": "資料格式錯誤"}, 400)

        if path == "/api/save":
            doc_no = str(body.get("文號") or "").strip()
            if not doc_no:
                return self._json({"ok": False, "錯誤": "缺少文號"}, 400)
            rec = {"文號": doc_no}
            # 「公告」= 公告頁那個文案框。跟擬辦一樣可以直接改、停 1 秒自動存,
            # 所以要允許覆寫（overwrite=True 見下面）—— 那是承辦人自己在打字,
            # 不是程式擅自蓋。程式那條路（announce_doc）另有 plan() 的定稿護欄。
            for col in ("擬辦", "陳會", "張貼", "公告"):
                if col in body:
                    rec[col] = body[col]
            try:
                rs.upsert(rec, overwrite=True)
            except PermissionError:
                return self._json({"ok": False,
                                   "錯誤": "存不進去 — 公告彙整.xlsx 正被 Excel 開著，"
                                           "請先關閉 Excel 再改。"})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            return self._json({"ok": True})

        if path == "/api/prep/start":
            ok, err = PREP.start()
            return self._json({"ok": ok, "錯誤": err} if not ok else {"ok": True})

        # 停止鈕（/api/<工作>/stop）由下面那段泛用路由處理，四支共用一條。

        if path == "/api/send/start":
            # 送陳核收不回來。這裡是唯一會真送的入口，兩道關卡:
            #
            #  1. 畫面要明確帶「確認」。少了它一律不動 —— 誰不小心 POST 到這個
            #     位址（重整、書籤、寫錯的程式）都不會送出東西。
            #  2. **畫面看到的那幾筆，要跟現在算出來的完全一樣**。中間有人動了
            #     Excel、或多收了幾份公文，清單就變了；那不是承辦人按下確定時
            #     看到的東西，寧可退回去讓他重看一次。
            if not body.get("確認"):
                return self._json({"ok": False, "錯誤": "缺少確認"}, 400)
            seen = [str(x).strip() for x in (body.get("文號") or [])]
            if not seen:
                return self._json({"ok": False, "錯誤": "沒有要送的公文"})
            # Chrome 沒開就別跑了 —— 跑下去只會吐一片英文堆疊，還附一個
            # 「跑 main.py 3」的危險建議（見 chrome_state 的說明）。
            cs = chrome_state()
            if not cs["ok"]:
                return self._json({"ok": False, "錯誤": cs["說明"]})
            try:
                import post_draft_batch as pdb
                now = [it["文號"] for it in pdb.plan()[0]]
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            if sorted(seen) != sorted(now):
                return self._json({"ok": False,
                                   "錯誤": f"清單變了（你看到 {len(seen)} 筆，現在是 "
                                           f"{len(now)} 筆）—— 為安全起見沒有送出，"
                                           f"請按「重新整理」再確認一次。"})
            ok, err = SEND.start()
            return self._json({"ok": ok, "錯誤": err} if not ok
                              else {"ok": True, "筆數": len(now)})

        if path == "/api/scan/start":
            # 讀清單本身不歸檔，但會點左側「待結案」—— 是在操作 Chrome，
            # 所以 Chrome 沒接上就別跑（不然只會吐一片英文堆疊）。
            cs = chrome_state()
            if not cs["ok"]:
                return self._json({"ok": False, "錯誤": cs["說明"]})
            ok, err = SCAN.start()
            if ok:
                # 重讀清單＝要看的是「現在還剩哪幾筆」，上一批的結果讓位。
                remember_arch_batch(None)
            return self._json({"ok": ok, "錯誤": err} if not ok else {"ok": True})

        if path == "/api/archive/start":
            # 歸檔**無 admin 介入無法復原**。三道關卡，缺一不動:
            #
            #  1. 沒帶「確認」一律不動 —— 誰誤 POST 到這個位址都不會歸檔。
            #  2. 畫面看到的整份清單（含順序）要跟剛才讀到的完全一樣。順序有意義:
            #     歸檔只做第一筆，前面卡住後面就輪不到，順序一變「會做到哪裡」
            #     就跟人看過的不是同一回事。
            #  3. archive_batch 說這批不能跑（例如清單裡有已存查標記卻還在待結案
            #     的公文）就不給按。
            #
            # 另外真正動手的那支還會**自己再讀一次 edoc 清單**跟 --expect 比對 ——
            # 這裡的比對只擋得住畫面過期，擋不住這半秒內 edoc 那邊又變了。
            if not body.get("確認"):
                return self._json({"ok": False, "錯誤": "缺少確認"}, 400)
            seen = [str(x).strip() for x in (body.get("文號") or [])]
            if not seen:
                return self._json({"ok": False, "錯誤": "沒有要歸檔的公文"})
            cs = chrome_state()
            if not cs["ok"]:
                return self._json({"ok": False, "錯誤": cs["說明"]})
            p = scan_plan()
            if p is None:
                return self._json({"ok": False,
                                   "錯誤": "還沒讀過待結案清單 —— 請先按「讀待結案清單」。"})
            now = [it["文號"] for it in p["清單"]]
            if seen != now:
                return self._json({"ok": False,
                                   "錯誤": f"清單變了（你看到 {len(seen)} 筆，現在是 "
                                           f"{len(now)} 筆，或順序不同）—— 為安全起見"
                                           f"沒有歸檔，請重新讀一次清單再確認。"})
            if not p["可跑"]:
                return self._json({"ok": False, "錯誤": p["不可跑原因"]})
            ARCH.argv = ["archive_batch.py", "--go", "--expect=" + ",".join(now)]
            ok, err = ARCH.start()
            if not ok:
                return self._json({"ok": False, "錯誤": err})
            # 記下送出去的是哪幾筆,跑完才有東西可以回頭對（見 archive_result）。
            remember_arch_batch(p)
            # 跑完清單一定不一樣了。舊的留著會讓人拿過期的清單再按一次送出，
            # 而下一道 --expect 雖然擋得住，但那時人已經按下去了。直接作廢。
            SCAN.lines = []
            return self._json({"ok": True, "筆數": len(p["會歸檔"])})

        if path == "/api/announce/start":
            # 這一頁**不會把任何東西送出去**，但會花 token、會寫審核表，
            # 而「重產」會蓋掉承辦人自己改過的字。兩道關卡:
            #
            #  1. 沒帶「確認」一律不動 —— 誰誤 POST 到這個位址都不會花錢。
            #  2. 要產的那幾筆，一律再問一次 announce_doc.plan()，**而且用的是
            #     待會真的帶下去的同一組旗標**。plan() 不給的（打過 OK 的定稿、
            #     含個資、擬辦不是要公告、還沒陳核…）就不放行，介面不另立標準。
            #     少一筆就整批退回並說明原因，不會默默少產一份。
            #
            # 帶下去的是 --only <文號…>，**不是 --limit**:2026-07-28 就是
            # `--overwrite --limit 1` 挑錯對象，把手寫的公告蓋掉三次。
            if not body.get("確認"):
                return self._json({"ok": False, "錯誤": "缺少確認"}, 400)
            seen = [str(x).strip() for x in (body.get("文號") or [])]
            if not seen:
                return self._json({"ok": False, "錯誤": "沒有要產文案的公文"})
            overwrite = bool(body.get("重產"))
            sent_only = bool(body.get("只看已陳核"))
            try:
                import announce_doc as ad
                todo, skip = ad.plan(overwrite=overwrite, only=seen,
                                     sent_only=sent_only)
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "錯誤": "還沒有任何公文。請先按「收新公文」。"})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            now = [it["文號"] for it in todo]
            if sorted(now) != sorted(seen):
                why = "；".join(f"{it['文號']} — {it['略過']}" for it in skip[:3]) \
                      or "在審核表裡找不到那幾筆"
                return self._json({
                    "ok": False,
                    "錯誤": f"有 {len(seen) - len(now)} 筆現在不能產（{why}）"
                            f"—— 什麼都沒做，請按「重新整理」再看一次。"})
            ANNC.argv = (["announce_doc.py"]
                         + (["--overwrite"] if overwrite else [])
                         + (["--sent-only"] if sent_only else [])
                         + ["--only", *now])
            ok, err = ANNC.start()
            return self._json({"ok": ok, "錯誤": err} if not ok
                              else {"ok": True, "筆數": len(now)})

        if path == "/api/web/start":
            # **唯一真的對外的入口。** 貼出去全校師生家長都看得到，所以五道關卡:
            #
            #  1. 沒帶「確認」一律不動 —— 誰誤 POST 到這個位址都不會貼出東西。
            #  2. 畫面看到的那幾筆要跟現算的完全一樣。
            #  3. **那幾筆的內容也要跟他確認過的一樣**（比 post_web_batch 算的
            #     指紋:標題＋內文＋分類＋附件）。只比文號擋不住「文案被換掉」——
            #     文號一樣、字全變了照樣過關，而他確認的是那幾百字。2026-08-12
            #     審出來的洞。沒帶指紋也不放行:那代表畫面是舊版本，寧可重看一次。
            #  4. plan() 的警告沒清掉不放行（例如缺 sssh_publish_unit）——
            #     跑下去會在發佈那步停住，而那時 Chrome 已經開了、校網也登入了。
            #  5. 收文那個 Chrome 還開著就不放行。**跟陳核／存查相反**，
            #     理由見 post_web_batch.chrome_in_the_way()。
            #
            # 動手的那支還會自己再算一次（含指紋）跟 --expect 比對，也自己再檢查
            # 一次 Chrome —— 這裡這幾道只擋得住「畫面過期」。
            if not body.get("確認"):
                return self._json({"ok": False, "錯誤": "缺少確認"}, 400)
            seen = [str(x).strip() for x in (body.get("文號") or [])]
            if not seen:
                return self._json({"ok": False, "錯誤": "沒有要貼的公文"})
            try:
                import post_web_batch as pwb
                ready, _, _, warn = pwb.plan()
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "錯誤": "還沒有任何公文。請先按「收新公文」。"})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            now = {it["文號"]: pwb.fingerprint(it) for it in ready}
            if sorted(seen) != sorted(now):
                return self._json({"ok": False,
                                   "錯誤": f"清單變了（你看到 {len(seen)} 筆，現在是 "
                                           f"{len(now)} 筆）—— 為安全起見沒有貼出去，"
                                           f"請按「重新整理」再確認一次。"})
            marks = body.get("指紋") or {}
            bad = sorted(no for no in now
                         if str(marks.get(no) or "") != now[no])
            if bad:
                return self._json({
                    "ok": False,
                    "錯誤": f"這幾筆要貼的字跟你剛才確認的不一樣了:{'、'.join(bad)}"
                            f"（公告欄被改過，或剛剛重產過，也可能是這個畫面太舊）"
                            f"—— 為安全起見沒有貼出去，請按「重新整理」再看一次。"})
            if warn:
                return self._json({"ok": False, "錯誤": warn[0]})
            busy = pwb.chrome_in_the_way()
            if busy:
                return self._json({"ok": False, "錯誤": busy})
            # 帶下去的是「文號:指紋」—— 那支動手前會自己再算一次比對。
            WEB.argv = ["post_web_batch.py", "--go",
                        "--expect=" + ",".join(f"{no}:{now[no]}"
                                               for no in sorted(now))]
            ok, err = WEB.start()
            return self._json({"ok": ok, "錯誤": err} if not ok
                              else {"ok": True, "筆數": len(now)})

        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[2] == "stop" \
                and parts[1] in JOBS:
            ok, err = JOBS[parts[1]].stop()
            return self._json({"ok": ok, "錯誤": err} if not ok else {"ok": True})

        if path == "/api/attach":
            doc_no = str(body.get("文號") or "").strip()
            names = body.get("上傳")
            d = _doc_dir(doc_no)
            if not d:
                return self._json({"ok": False, "錯誤": "找不到公文資料夾"})
            if not isinstance(names, list):
                return self._json({"ok": False, "錯誤": "資料格式錯誤"}, 400)
            try:
                save_attach_choice(d, names)
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            return self._json({"ok": True, "數量": len(names)})

        if path == "/api/archive/clear-marker":
            # 刪掉一個**說謊的**存查完成標記。
            #
            # 為什麼需要這顆（2026-08-14 實跑撞到）:歸檔第 1 筆時 pinCode 視窗
            # 15 秒沒出現、簽章根本沒完成，但 `document_closure` 那道
            # 「文號從待結案可見列消失」的驗證在**存查表單還開著**時必然成立
            # → 誤判成功 → 寫了 `已存查.txt`。於是磁碟說辦完了、edoc 說還在待結案。
            # 那筆從此被 `plan()` 判成 danger（坑 #16 的閘門，防重複簽章），
            # **整批不跑** —— 而承辦人沒有任何辦法在介面上解開。
            #
            # ⚠️ 這一頁不能「跳過第一筆」:`process_document_closure` 每輪只做清單
            # 最上面那筆，不能指定。所以解法不是跳過，是**把那個假標記清掉**，
            # 讓它變回一般的待歸檔公文，整批就跑得動了。
            #
            # 三道關卡（刪檔是不可逆的，雖然真的歸檔後會重寫一份）:
            #  1. 沒帶「確認」不動。
            #  2. **只准刪剛才讀到的待結案清單裡、而且狀態是 danger 的那幾筆** ——
            #     不是任意文號。標記檔還在待結案清單裡才叫「說謊」;不在清單裡的
            #     標記是正常的存查痕跡，刪掉會讓那份公文重跑一次歸檔。
            #  3. 路徑一律用後端自己算的（`evaluate` 給的），不吃畫面傳來的路徑。
            if not body.get("確認"):
                return self._json({"ok": False, "錯誤": "缺少確認"}, 400)
            no = str(body.get("文號") or "").strip()
            if not no:
                return self._json({"ok": False, "錯誤": "缺少文號"}, 400)
            p = scan_plan()
            if p is None:
                return self._json({"ok": False,
                                   "錯誤": "還沒讀過待結案清單 —— 請先按「讀待結案清單」。"})
            hit = next((it for it in p["清單"]
                        if it["文號"] == no and it.get("狀態") == "danger"), None)
            if hit is None:
                return self._json({
                    "ok": False,
                    "錯誤": f"{no} 不在剛才讀到的待結案清單裡，或它的狀態不是"
                            f"「要人工確認」—— 只有那種才是說謊的標記，其餘的"
                            f"標記是正常的存查痕跡，刪掉會讓那份公文再歸檔一次。"})
            try:
                import archive_batch as ab
                paths = ab.evaluate(no).get("標記檔") or []
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            if not paths:
                return self._json({"ok": False, "錯誤": f"{no} 現在沒有存查標記檔。"})
            gone, failed = [], []
            for fp in paths:
                try:
                    os.remove(fp)
                    gone.append(os.path.basename(fp))
                except OSError as e:
                    failed.append(f"{os.path.basename(fp)}（{e}）")
            if failed:
                return self._json({"ok": False,
                                   "錯誤": "刪不掉:" + "、".join(failed)})
            print(f"[ui] 已刪掉假的存查標記:{no} → {'、'.join(gone)}")
            # 清單的判定變了，快取那份作廢 —— 不然畫面還是舊的 danger 狀態，
            # 而送出那道關卡會拿它比對。要人重讀一次，兩邊才是同一份。
            SCAN.lines = []
            return self._json({"ok": True, "刪了": gone})

        if path == "/api/settings/save":
            # 這一頁不送出任何東西，但寫的是**下次跑文會吃的設定**（含 PIN）。
            # 兩道:
            #  1. 只收設定頁自己那張表裡的鍵（`env_config._check` 會擋）——
            #     誰亂 POST 一個鍵進來都不會被寫進 env.env。
            #  2. 回應**只回鍵名，不回值**;錯誤訊息也不帶值。密鑰不出畫面、
            #     不進 log（這個 repo 是 public，畫面會被截圖）。
            import env_config as ec
            vals = body.get("值")
            if not isinstance(vals, dict) or not vals:
                return self._json({"ok": False, "錯誤": "沒有要改的設定"}, 400)
            try:
                changed = ec.write({str(k): str(v) for k, v in vals.items()})
            except ec.Rejected as e:
                return self._json({"ok": False, "錯誤": str(e)})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            if changed:
                print(f"[ui] 設定已更新:{'、'.join(changed)}")   # 只印鍵名
            return self._json({"ok": True, "改了": changed})

        if path == "/api/spec/save":
            # 規格檔的表單存檔。判斷本身還是留在那兩份檔案裡（改一行行為就變一片，
            # 比寫進 Python 好），這裡只是讓不寫程式的人也改得動。
            #
            # ⚠️ `spec_forms` 會**在寫出去之前自己驗一次**（yaml 解得開、而且解出來
            # 跟填的一樣）。驗不過就整批不寫 —— `routing_flags.yaml` 讀壞的後果是
            # 那道「他人業務不自動送陳核」的保護靜靜失效，寧可存不進去。
            import spec_forms as sf
            changed = []
            try:
                if isinstance(body.get("他人業務"), dict):
                    changed += sf.routing_write(body["他人業務"])
                if isinstance(body.get("對應表"), list):
                    if sf.table_write(body["對應表"]):
                        changed.append("存查分類對應表")
            except sf.Rejected as e:
                return self._json({"ok": False, "錯誤": str(e)})
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            if changed:
                print(f"[ui] 規格檔已更新:{'、'.join(changed)}")
            return self._json({"ok": True, "改了": changed})

        if path == "/api/settings/open":
            # 規格檔用系統預設程式開。**白名單** —— 不接受任意路徑，
            # 不然這個位址就變成「用瀏覽器叫本機開任何檔案」。
            name = str(body.get("檔名") or "")
            if name not in SPEC_FILES:
                return self._json({"ok": False, "錯誤": "不是規格檔"}, 400)
            p = os.path.join(_BASE_DIR, name)
            if not os.path.isfile(p):
                return self._json({"ok": False, "錯誤": f"找不到 {name}"})
            try:
                os.startfile(p)                 # noqa: S606 — Windows 開預設編輯器
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"開不起來:{e}"})
            return self._json({"ok": True})

        if path == "/api/file/go":
            # 真的複製。**畫面只送文號與那兩格文字** —— 來源路徑、目標路徑一律
            # 由後端自己重算,不接畫面給的路徑（不然這個位址就變成「用瀏覽器叫
            # 本機把任意資料夾複製到任意地方」）。
            want = body.get("項目") or []
            overrides, nos = {}, []
            for it in want:
                no = str(it.get("文號") or "").strip().upper()
                if not no:
                    continue
                nos.append(no)
                overrides[no] = {"標籤": str(it.get("標籤") or "").strip(),
                                 "標題": str(it.get("標題") or "").strip()}
            if not nos:
                return self._json({"ok": False, "錯誤": "一筆都沒勾"})
            try:
                p2 = file_doc.plan(only=nos, overrides=overrides)
                go = [i for i in p2["項目"] if i["狀態"] == "go"]
                # 勾了卻不能歸的（狀態在你按下去之前變了）要明講，不能安靜跳過。
                blocked = [{"文號": i["文號"], "狀態": i["狀態"]}
                           for i in p2["項目"] if i["狀態"] != "go"]
                done = file_doc.run(go)
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"{type(e).__name__}: {e}"})
            ok_n = sum(1 for r in done if r.get("ok"))
            print(f"[ui] 歸檔到檔案櫃:成功 {ok_n}／{len(done)} 筆")
            return self._json({"ok": True, "結果": done, "擋下": blocked})

        if path == "/api/open-folder":
            d = str(body.get("目錄") or "")
            if not os.path.isdir(d):
                return self._json({"ok": False, "錯誤": "資料夾不存在"})
            try:
                os.startfile(d)                 # noqa: S606 — Windows 開檔案總管
            except Exception as e:
                return self._json({"ok": False, "錯誤": f"開不起來:{e}"})
            return self._json({"ok": True})

        self._json({"ok": False, "錯誤": "未知的位址"}, 404)


def already_running():
    """`127.0.0.1:PORT` 上已經有一個介面在服務了嗎。

    ⚠️ **不能靠「bind 會失敗」來判斷。** `HTTPServer.allow_reuse_address = 1`，
    而 Windows 的 SO_REUSEADDR 允許**兩個 socket 綁同一個埠**（跟 Linux 相反）——
    所以第二次啟動不但不報錯，還會兩個服務搶同一個埠，誰收到請求是看運氣。
    2026-08-14 實測就是這樣默默起了第二個。所以改成**啟動前先連連看**。
    """
    import socket
    try:
        with socket.socket() as s:
            s.settimeout(0.5)
            return s.connect_ex((HOST, PORT)) == 0
    except OSError:
        return False


def main():
    if not os.path.isfile(PAGE):
        print(f"[ui] 找不到頁面檔 {PAGE}")
        raise SystemExit(1)
    url = f"http://{HOST}:{PORT}/"
    if already_running():
        # 最常見的原因:**已經有一個介面在跑**（點了兩次捷徑）。
        # 他要的其實只是「把那一頁打開」，不是再開一個服務。
        print()
        print("=" * 60)
        print(f" 已經有一個介面在跑了（{HOST}:{PORT} 有人在服務）。")
        print(f" 不用再開一個 —— 直接用這一頁就好:{url}")
        print(" 幫你打開了。")
        print(" 如果打開的是別的東西，那就是別的程式佔用了這個埠，")
        print(" 把那個程式關掉再點一次。")
        print("=" * 60)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        raise SystemExit(0)         # 不是錯誤，別讓啟動.bat 跳紅字
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print()
    print("=" * 60)
    print(" 介面已啟動 —— 瀏覽器會自己打開，沒開的話手動輸入這個網址:")
    print(f"   {url}")
    print()
    print(" ⚠️ 這個黑色視窗**就是程式本體，不要關掉**。")
    print("    關掉它，網頁那一頁就跟著死了（畫面上不會有任何提示）。")
    print("    要收工請在這個視窗按 Ctrl+C，或直接關掉也行 —— 但要記得")
    print("    下次還是回來點「啟動.bat」。")
    print(" 只有這台電腦連得到，別人連不進來。")
    print("=" * 60)
    # 這兩句在這裡講，不寫進 啟動.bat —— 那個檔只能放 ASCII（cmd 用系統
    # 字碼頁讀 .bat，中文會把整個檔解析壞;2026-08-12 實測 @echo off 被吃成 cho）。
    print(f"[ui] 提醒：審核表（{rs.sheet_path()}）如果正用 Excel 開著，"
          f"勾選與擬辦會存不進去 —— 請先關掉 Excel。")
    print(f"[ui] 第一次使用請先到「設定」頁填自己的 PIN 與校網帳密。")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[ui] 已關閉。")
        srv.shutdown()


if __name__ == "__main__":
    main()
