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
"""

import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import review_sheet as rs  # noqa: E402

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
    if pdb.looks_logged_out(urls) and not any("/tcqb/home/" in u for u in urls):
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
    viewers = [u for u in urls if "app=editor" in u]
    if viewers:
        return {"ok": False,
                "說明": f"有 {len(viewers)} 個「公文閱覽器」分頁還開著。請先把它們關掉，"
                        f"只留公文系統的主畫面 —— 程式靠「有沒有多開一個分頁」判斷"
                        f"公文開起來了沒，已經開著的話會誤判成失敗而中止。"}
    if not any("/tcqb/home/" in u for u in urls):
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
    ready, blocked, cand = [], [], []

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
        it = pdb.evaluate(r, flags)
        if rs.is_approved(r.get("陳會")):
            (blocked if it.get("擋下原因") else ready).append(slim(it))
        else:
            cand.append(slim(it))
    return {"可送": ready, "擋下": blocked, "候選": cand,
            "chrome": chrome_state()}, note


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


PREP = Job("收新公文", ["main.py", "4"], hud="prep")
# 送陳核也開浮窗:這支同樣在操作 Chrome，Chrome 一到前景就把這個網頁蓋掉，
# 而這是**會真的送出**的一段，看不到進度最讓人心慌（2026-08-03 收文那邊同因）。
SEND = Job("送陳核", ["post_draft_batch.py", "--go"],
           hud="send", hud_label="送陳核中…請不要動滑鼠")


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
        if path in ("/api/prep/status", "/api/send/status"):
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            since = int((q.get("since") or ["0"])[0])
            job = SEND if path.startswith("/api/send") else PREP
            return self._json({"ok": True, **job.status(since)})
        if path == "/api/send/plan":
            try:
                data, note = send_payload()
                return self._json({"ok": True, **data, "訊息": note})
            except FileNotFoundError:
                return self._json({"ok": False,
                                   "錯誤": "還沒有任何公文。請先按「收新公文」。"})
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
            for col in ("擬辦", "陳會", "張貼"):
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

        if path == "/api/prep/stop":
            ok, err = PREP.stop()
            return self._json({"ok": ok, "錯誤": err} if not ok else {"ok": True})

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

        if path == "/api/send/stop":
            ok, err = SEND.stop()
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


def main():
    if not os.path.isfile(PAGE):
        print(f"[ui] 找不到頁面檔 {PAGE}")
        raise SystemExit(1)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"[ui] 介面已啟動：{url}")
    print(f"[ui] 只有這台電腦連得到。要關掉請按 Ctrl+C。")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[ui] 已關閉。")
        srv.shutdown()


if __name__ == "__main__":
    main()
