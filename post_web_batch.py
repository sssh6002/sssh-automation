# -*- coding: utf-8 -*-
"""
post_web_batch.py
校網張貼（fork 版執行器）。跟 `post_draft_batch.py`（陳核）、`archive_batch.py`
（存查）同一層 —— UI 的「張貼」頁按下去就是跑這一支。

    python post_web_batch.py                只預覽:會貼哪幾筆、標題與分類長什麼樣
    python post_web_batch.py --json         印一行 JSON 給 UI 用（同樣不貼）
    python post_web_batch.py --go --expect=<文號,文號…>   真的貼上校網

⚠️ **這是整套流程裡唯一真的對外的動作**。貼出去全校師生家長都看得到
（可事後刪，但已經被看到就是被看到了）。所以關卡比其他頁多一道:
**文案裡有「待查核」網址的一律擋下，不給貼**。那種網址可能是 AI 幻覺，
也可能是來文 PDF 裡夾帶的指令 —— 貼上校網就等於幫它散布出去。

跟 `post_web_review.py`（原作者那支、`main.py 5`）的兩個根本差別:

1. **貼的內容不一樣**。那支貼的是 `*總結*.md` 解析出來的「主旨＋數字條列」;
   這支貼的是**審核表「公告」欄**那份給人讀的文案（承辦人可以逐字改的那份）。
   2026-08-11 發現兩者一直是斷開的 —— 公告頁辛苦產的文案根本沒被拿去貼。
2. **不用 `input()`**。那支是終端機逐筆問 y/n，包進 subprocess 會卡死;
   這支照 fork 的慣例走「畫面確認 → `--expect` 帶清單下來 → 動手前再比對一次」。

`document_closure/` 一行都沒改:發佈本體仍是系管師的 `maybe_post_announcement`，
只在呼叫前把 `_parse_summary` 與版型常數換掉，用完還原。
"""

import argparse
import contextlib
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import announce_doc as ad  # noqa: E402
import review_sheet as rs  # noqa: E402

PLAN_MARK = "##PLAN#"

# 校網版型:區塊標題靠左、字大一級。承辦人 2026-08-11 看過實物後定的。
# 預設值（置中 1.15em）是資媒組長模板的規格，**不改 sssh_style.py**，
# 只在這條路呼叫前覆寫模組屬性 —— 系管師跑 main.py 3 的版面一個字都不會變。
HEAD_ALIGN = "left"
HEAD_SIZE = "1.3em"


def split_announcement(text):
    """公告文案 → (標題, 內文)。

    第一行就是【類別】標題那行 —— announce_doc.md 的規格就這樣設計的。
    校網布告欄本身有標題列，所以標題抽出來單獨給，內文不重複印。
    """
    lines = str(text or "").strip().split("\n")
    if not lines or not lines[0].strip():
        return "", ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()


def _doc_dir(doc_no):
    for base in rs.SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for n in sorted(os.listdir(base)):
            if doc_no in n and os.path.isdir(os.path.join(base, n)):
                return os.path.join(base, n)
    return None


# ── 開跑前的環境檢查 ───────────────────────────────────────────────────────

DEVTOOLS_LIST = "http://127.0.0.1:9222/json/list"


def chrome_in_the_way():
    """收文開的那個 Chrome 還開著就先別貼。回說明字串;沒問題回 None。

    這條路對 Chrome 的要求跟陳核／存查**正好相反**。那兩支是 attach 進「已經
    登入好的」edoc Chrome，所以要求它開著;張貼完全不碰 edoc，是自己開一個新的
    （`post_web_review._launch_bare_chrome`）—— 而兩邊用的是**同一個 Selenium
    設定檔、同一個 9222 埠**（`taipeion_login_selenium._build_chrome_options`:
    `--user-data-dir=…\\Chrome-Selenium` ＋ `--remote-debugging-port=9222`）。
    同一個設定檔被兩個 Chrome 佔住時只有兩種結果，都不是我們要的:

      · 新開的 chrome.exe 把命令交給既有的實例就自己退場 → chromedriver 接到的
        是**收文那個 Chrome**，於是把 edoc 那個視窗開去校網，收尾的
        `driver.quit()` 還會把它整個關掉。
      · 或者 chromedriver 等不到瀏覽器，直接拋 WebDriverException（一片英文）。

    原作者那條路不會撞到:`main.py` 一被 import 就 `_close_selenium_chrome_only()`
    把 Selenium Chrome 殺掉，才輪到 `main.py 5` 開乾淨的。這裡**故意不照做** ——
    那個 Chrome 裡有插卡登入好的 edoc session，重建要插卡、輸 PIN、還要過雙因子
    （交接檔坑 #20 卡了整個上午）。所以只擋下來把話講清楚，關不關由人決定。
    """
    import urllib.request
    try:
        with urllib.request.urlopen(DEVTOOLS_LIST, timeout=1.5):
            pass
    except Exception:
        return None
    return ("收新公文開的那個 Chrome 還開著（127.0.0.1:9222）。張貼會自己開一個新的"
            "Chrome，跟它用同一個 Selenium 設定檔 —— 兩個一起開會互相搶，可能反而"
            "把 edoc 那個視窗開去校網，收尾時再把它關掉。請先把那個 Chrome 關掉"
            "再貼。（陳核／存查要它開著，張貼相反 —— 張貼不碰 edoc、不用插卡。）")


# ── 判定 ───────────────────────────────────────────────────────────────────

def evaluate(row):
    """這一筆能不能貼。回 dict，`擋下原因` 為 None 代表可以貼。

    判定來源一律沿用既有的那幾支（`_should_post` / `_already_announced`），
    不另立標準 —— 兩套規則遲早分岔，而最壞的方向是「畫面說會擋、其實貼出去了」。
    """
    from document_closure.document_closure_post_web import (
        _already_announced, _parse_summary, _should_post)

    no = str(row["文號"]).strip()
    text = str(row.get("公告") or "").strip()
    title, body = split_announcement(text)
    d = _doc_dir(no)
    out = {"文號": no, "主旨": row.get("主旨") or "", "目錄": d or "",
           "標題": title, "內文": body, "分類": [], "附件": [],
           "擋下原因": None}

    def stop(why):
        out["擋下原因"] = why
        return out

    if not d:
        return stop("找不到公文資料夾")
    if not text:
        return stop("公告欄是空的 —— 先去「公告」頁產文案")
    # ⚠️ 這一道是張貼專屬的。文案裡的「待查核」= 來文找不到的網址，
    # 可能是 AI 幻覺，也可能是來文 PDF 夾帶的指令。貼上校網就是幫它散布。
    # 公告頁只是標紅提醒（那時還沒對外），到這裡必須**擋下**。
    if ad.STRAY_MARK in text:
        return stop("文案裡有「待查核」網址 —— 請先到公告頁確認那幾個網址，"
                    "確認過就把那段標註刪掉")
    if not title or not body:
        return stop("文案格式不對（第一行要是【類別】標題，底下才是內文）")

    summary = _parse_summary(d)
    if not _should_post(summary):
        return stop("擬辦不是要公告（承辦文字沒有「公佈周知／於官網公告」）")
    if _already_announced(d):
        return stop("已經公告過了（資料夾有 *已公告.txt，或清冊裡already有）")
    out["分類"] = (summary or {}).get("sync_categories") or []
    # 附件名稱會**出現在校網上**（家長看得到的就是這幾個檔名），所以預覽就要
    # 看得到「實際會傳哪幾個」。判定不另立標準:承辦人在摘要頁勾過的話，
    # `_find_attachments` 自己會讀那份「附件選擇.json」以他的勾選為準，
    # 這裡只是把同一支算出來的答案印出來。
    # 只在其他關卡都過了才算 —— 那支會走整個目錄還讀檔算雜湊，貼不出去的
    # 沒必要花這個時間（清單一次算幾十筆）。
    from document_closure.document_closure_post_web import _find_attachments
    try:
        out["附件"] = [os.path.basename(p) for p in _find_attachments(d)]
    except Exception as e:
        print(f"[post_web_batch] {no} 附件列不出來（不影響貼出去的內容）:"
              f"{type(e).__name__}: {e}")
    return out


def fingerprint(it):
    """把「承辦人確認過的東西」壓成一個短字串:標題＋內文＋分類＋附件。

    2026-08-12 審出來的洞:送出前**只比文號**。文號一樣、文案被換掉了，比對照樣
    過關 —— 而他在確認框裡看的是那幾百字，不是那 14 個字元。最現實的情境:
    確認框開著的時候有人在 Excel 動了「公告」欄，或另一邊剛跑完產文案。
    **這是唯一對外的動作**，貼出去的字一定要是他看過的那份。

    只取前 10 碼:這不是防篡改，是防「畫面過期」，夠短才塞得進 `--expect`。
    """
    import hashlib
    raw = "\n".join([str(it.get("標題") or ""), str(it.get("內文") or ""),
                     "+".join(it.get("分類") or []),
                     "|".join(it.get("附件") or [])])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def parse_expect(expect):
    """`--expect` 拆成 {文號: 指紋 or None}。

    允許只給文號（手動跑 CLI 時打得出來），那種就只比清單、不比內容。
    介面一律會帶指紋。
    """
    out = {}
    for s in expect or []:
        s = str(s).strip()
        if not s:
            continue
        no, _, mark = s.partition(":")
        if no.strip():
            out[no.strip()] = mark.strip() or None
    return out


def plan(path=None):
    """回 (可貼, 擋下, 候選, 警告)。分堆規則跟陳核頁一樣。

    「張貼」欄打 OK = 承辦人說這筆可以貼（跟陳核的「陳會」欄對稱）。
    """
    ready, blocked, cand = [], [], []
    for row in rs.rows(path):
        no = str(row["文號"]).strip()
        d = _doc_dir(no)
        # 辦完的（已存查＋已張貼/不用公告）不該再出現。
        if rs.is_archived(row, d) or rs.is_done(row.get("張貼")):
            continue
        it = evaluate(row)
        if rs.is_approved(row.get("張貼")):
            (blocked if it["擋下原因"] else ready).append(it)
        else:
            cand.append(it)

    warn = []
    from taipeion_login_selenium import _read_config
    if not _read_config("sssh_publish_unit"):
        warn.append("env.env 缺 sssh_publish_unit —— 真的按下去會在發佈那步停住。"
                    "請先填「要發佈到哪個單位/群組」。")
    return ready, blocked, cand, warn


# ── 呼叫前的替換（都用完就還原）─────────────────────────────────────────

@contextlib.contextmanager
def announcement_as_summary(texts):
    """把 `_parse_summary` 換成「title/body 吃審核表公告欄」的版本。

    texts — {文號: 公告文案}。沒有文案的公文一律走回原本的解析結果。

    **只換 title 與 body**，其餘欄位（承辦文字、同步分類、存查分類）原樣保留 ——
    `_should_post` 與分類選擇都還要靠它們，換掉會出鬼故事。
    """
    from document_closure import document_closure_post_web as pw
    real = pw._parse_summary

    def patched(extract_dir):
        s = real(extract_dir)
        if not s:
            return s
        text = texts.get(pw._doc_no_of(extract_dir))
        if not text:
            return s
        title, body = split_announcement(text)
        if not title or not body:
            return s
        return {**s, "title": title, "body": body}

    pw._parse_summary = patched
    try:
        yield
    finally:
        pw._parse_summary = real


@contextlib.contextmanager
def sssh_heading_style(align=None, size=None):
    """把校網版型的區塊標題改成靠左、字大一級。用完還原。

    `sssh_style.py` 在 `document_closure/` 底下（系管師那條路也在用），
    所以**不改檔案**，只在這條路呼叫期間換模組屬性。
    """
    from document_closure import sssh_style
    old = sssh_style._S["h3"]
    new = (old.replace("text-align:center", f"text-align:{align or HEAD_ALIGN}")
              .replace("font-size:1.15em", f"font-size:{size or HEAD_SIZE}"))
    sssh_style._S["h3"] = new
    try:
        yield
    finally:
        sssh_style._S["h3"] = old


# ── 發布單位的診斷（2026-08-12）───────────────────────────────────────────
#
# 為什麼要這段:當天貼三篇，校網「單位」欄兩篇對、**一篇是圖書館**，而 log 上
# 兩筆都印 `OK:發布單位(自訂下拉)已選「資訊媒體組」` —— **那個 OK 是假的**。
# 已知那個欄位不是原生 `<select>`（策略 1 從沒中過，一律走 fallback），
# 而同一批第 1 筆失敗、第 2 筆成功，聞起來是時間差。
#
# ⚠️ 這段**只印，不改任何行為**。真正的修法是「設完讀回來確認、不對就停下不發」，
# 但那要先知道「目前值顯示在哪個元素」—— 猜著寫會變成另一個假的檢查（坑 #2:
# 改判定基準前先查清楚）。所以先讓它在**真的壞掉的那個時機**把長相印出來。
# 讀完就把這段換成真的檢查。

_UNIT_STATE_JS = r"""
var unit = (arguments[0] || '').trim();
var out = {found: false};
var lab = null;
for (var e of document.querySelectorAll('*')) {
    if (e.children.length === 0 && (e.textContent || '').trim() === '發布單位') {
        lab = e; break;
    }
}
if (!lab) return out;
out.found = true;
out.labelTag = lab.tagName + '.' + String(lab.className || '').slice(0, 40);
var box = lab;
for (var i = 0; i < 4 && box.parentElement; i++) box = box.parentElement;
out.boxTag = box.tagName + '.' + String(box.className || '').slice(0, 40);
out.boxLines = (box.innerText || '').split('\n')
    .map(function (s) { return s.trim(); }).filter(Boolean).slice(0, 12);
out.selects = [];
box.querySelectorAll('select').forEach(function (s) {
    var o = s.options[s.selectedIndex] || {};
    out.selects.push({id: s.id || s.name || '', value: String(s.value || ''),
                      selected: (o.text || '').trim(), n: s.options.length});
});
out.inputs = [];
box.querySelectorAll('input').forEach(function (s) {
    out.inputs.push({id: s.id || s.name || '', type: s.type,
                     value: String(s.value || '').slice(0, 40)});
});
out.leaves = [];
box.querySelectorAll('*').forEach(function (n) {
    var t = (n.innerText || '').trim();
    if (n.children.length === 0 && t && t.length <= 14) {
        out.leaves.push({tag: n.tagName,
                         cls: String(n.className || '').slice(0, 30),
                         role: n.getAttribute('role') || '',
                         text: t, hit: t === unit});
    }
});
out.leaves = out.leaves.slice(0, 18);
return out;
"""


# 2026-08-12 從實物讀到的:發布單位**不是 `<select>`**，是一個 `input type=text`
# （id 開頭 `ct-etAnnoGroup-`，後面接亂數，所以只能用前綴選）。
# 旁邊還有一個 hidden 欄位，那才是真正送出去的「群組 id」——
# ⚠️ **所以光把文字塞進 input 沒有用**，一定要真的去點那個下拉讓 hidden 一起被填。
# 這也是這裡只做「讀回來確認 + 重試原本那支」、不自己塞值的原因。
UNIT_INPUT_PREFIX = "ct-etAnnoGroup-"
# 沒選單位時那一欄的字。這種公告會被校網掛成**那一頁所屬的單位**（圖書館）——
# 2026-08-12 第一批第 1 筆就是這樣變成圖書館的。
UNIT_UNSET = "無群組"

_UNIT_READ_JS = r"""
var el = document.querySelector('input[id^="ct-etAnnoGroup-"]');
if (!el) return null;
var hid = '';
var box = el.parentElement;
for (var i = 0; i < 3 && box && !hid; i++) {
    var h = box.querySelector('input[type=hidden]');
    if (h) hid = String(h.value || '');
    box = box.parentElement;
}
return {value: String(el.value || ''), id: el.id, hidden: hid};
"""

# 找「真的可以點的那個選項」。原本那支失敗的原因就在這裡（2026-08-12 實跑診斷）:
# 它在**整份 document** 裡找文字相符的元素就點，於是
#   · 看不見的元素（下拉還沒 render 完）也算 —— 點了等於沒點，但它回報成功
#   · 圖書館那頁側邊選單有一個 `<a href="info">資訊媒體組</a>` —— 點下去會**離開表單**
# 所以這裡加三道:只認葉節點、**必須看得見**、**排除會導覽的連結**;
# 而且把長得像下拉選單的（祖先 class/role 含 dropdown/menu/listbox）排在前面。
_UNIT_CANDS_JS = r"""
function cands(unit) {
    var inp = document.querySelector('input[id^="ct-etAnnoGroup-"]');
    var hit = [];
    document.querySelectorAll('li,option,a,span,div,td,p,button').forEach(
        function (n) {
            if (n === inp || n.children.length) return;
            if ((n.textContent || '').trim() !== unit) return;
            if (!n.getClientRects().length) return;          // 看不見的不點
            if (n.tagName === 'A') {                          // 會導覽的不點
                var h = n.getAttribute('href') || '';
                if (h && h !== '#' && h.indexOf('javascript:') !== 0) return;
            }
            var score = 0, p = n;
            for (var i = 0; i < 5 && p; i++) {
                var c = (String(p.className || '') + ' '
                         + (p.getAttribute && (p.getAttribute('role') || '')))
                        .toLowerCase();
                if (/dropdown|menu|listbox|select|option/.test(c)) { score = 1; break; }
                p = p.parentElement;
            }
            hit.push({n: n, score: score});
        });
    hit.sort(function (a, b) { return b.score - a.score; });   // 像下拉的排前面
    return hit.map(function (h) { return h.n; });
}
var unit = (arguments[0] || '').trim();
var mode = arguments[1];
var idx = arguments[2];
if (mode === 'count') return cands(unit).length;
var list = cands(unit);
if (idx >= list.length) return false;
list[idx].click();
return true;
"""

# 真的失敗時才 dump:那一列的 HTML。讀了它就能寫出精準的點法，
# 不必再靠「找文字相符的元素」這種瞎猜（2026-08-12 的教訓）。
_UNIT_HTML_JS = r"""
var el = document.querySelector('input[id^="ct-etAnnoGroup-"]');
if (!el) return '';
var box = el;
for (var i = 0; i < 3 && box.parentElement; i++) box = box.parentElement;
return box.outerHTML.replace(/\s+/g, ' ');
"""

_UNIT_OPEN_JS = r"""
var el = document.querySelector('input[id^="ct-etAnnoGroup-"]');
if (!el) return false;
try { el.scrollIntoView({block: 'center'}); } catch (e) {}
el.focus();
el.click();
return true;
"""


def _js(driver, script, *args):
    try:
        return driver.execute_script(script, *args)
    except Exception as e:
        print(f"[post_web_batch] 操作發布單位時出錯:{type(e).__name__}: {e}")
        return None


def read_publish_unit(driver):
    """讀「發布單位」現在實際是什麼。回 dict 或 None（讀不到）。

    `{'value': 顯示的字, 'hidden': 旁邊那個 hidden 的值, 'id': 那個 input 的 id}`

    ⚠️ `hidden` **只印出來參考，不當判斷依據**。當初以為它是送出去的群組 id，
    但那是猜的（那一列附近不只一個 hidden，也可能是別的欄位的）。
    拿沒證實的東西當閘門，最後會變成「明明選對了卻整批不給貼」。
    真正證實過的判斷是**那個 input 的值要等於要的單位** —— 2026-08-12 實跑時它
    停在「圖書館」，而貼出去的公告就真的掛成圖書館。
    """
    return _js(driver, _UNIT_READ_JS)


def open_unit_dropdown(driver):
    """點那個 input 把下拉叫出來。"""
    return bool(_js(driver, _UNIT_OPEN_JS))


def unit_option_count(driver, unit):
    """畫面上「看得見、可以點、文字剛好是這個單位」的候選有幾個。"""
    return int(_js(driver, _UNIT_CANDS_JS, unit, "count", 0) or 0)


def click_unit_option(driver, unit, idx):
    """點第 idx 個候選。回是否點到。"""
    return bool(_js(driver, _UNIT_CANDS_JS, unit, "click", idx))


@contextlib.contextmanager
def verified_publish_unit(tries=3, wait=1.5):
    """發布單位設完**讀回來確認**;確認不了就讓那一筆停下不發。

    為什麼要這道（2026-08-12 實跑證據）:當天貼三篇，校網「單位」欄兩篇對、
    **一篇是圖書館**，而 log 上兩筆都印 `OK:發布單位(自訂下拉)已選「資訊媒體組」`
    —— **那個 OK 是假的**。同日第二次實跑（4 筆）第 1 筆就被這道擋下，診斷讀到
    `input[id^=ct-etAnnoGroup-]` 停在「圖書館」，而那一列的容器裡**另有**一個文字
    是「資訊媒體組」的元素 —— 也就是原本那支點到的不是下拉選項。它在**整份
    document** 裡找文字相符的元素就點，於是看不見的元素、甚至圖書館那頁側邊選單的
    `<a href="info">資訊媒體組</a>` 都算（後者點下去會離開表單）。

    流程:原本那支跑完 → 讀回來 → 對了就放行;不對就**自己點**（點那個 input 開下拉，
    逐個「看得見、非導覽連結」的候選點下去，**每點一次就讀回來驗**）。

    做法刻意保守:
      · **驗證是唯一的閘門** —— 候選挑錯只是白點一次，不會貼錯。所以候選寧可寬。
      · **不自己塞值** —— 直接改 input 的字只會讓畫面好看（該欄位背後還有站台自己的
        狀態），一定要讓站台的 JS 自己跑過。
      · **只在「證明是錯的」時候擋** —— 讀不到那個欄位（例如站台改版換了 id）
        一律放行並大聲警告。假警告會讓真警告一起被當成裝飾（坑 #13）;
        而這道擋下去的代價是整批停下，不能靠猜。
      · 擋下的方式是回 False —— `_submit_announcement` 會印 STOP banner 不發佈，
        `run()` 逐筆失敗即中止，結束碼 1。**寧可不貼，也不要貼成別的單位。**
      · 真的失敗才 dump 診斷（含那一列的 HTML），而且**分段印** ——
        一行印到底會被截掉，2026-08-12 就剛好截在最需要的那一段。
    """
    from document_closure import document_closure_post_web as pw
    real = pw._select_publish_unit

    def patched(driver, unit):
        def now():
            st = read_publish_unit(driver)
            return None if st is None else (st.get("value") or "").strip()

        real(driver, unit)
        got = now()
        if got is None:
            print(f"[post_web_batch] ⚠️ 讀不到發布單位欄位"
                  f"（找不到 id 開頭 {UNIT_INPUT_PREFIX} 的欄位，站台可能改版）"
                  f"—— 這一筆的單位**沒辦法確認**，照原本的結果繼續。"
                  f"貼完請自己去校網看那筆的「單位」欄。")
            return True
        if got == unit:
            print(f"[post_web_batch] ✓ 發布單位讀回來確認:{got}")
            return True

        # 原本那支沒設進去（它會回報成功，見 docstring）。自己來:點那個 input
        # 把下拉叫出來，逐個「看得見的候選」點下去，**每點一次就讀回來驗**。
        # 驗證是唯一的閘門 —— 所以就算候選挑錯了也只是白點一次，不會貼錯。
        print(f"[post_web_batch] ⚠️ 發布單位還是「{got or '空的'}」"
              f"（原本那支回報成功，實際沒設進去）—— 自己重點一次。")
        for n in range(1, tries + 1):
            if not open_unit_dropdown(driver):
                break
            time.sleep(0.6)
            total = unit_option_count(driver, unit)
            print(f"[post_web_batch]    第 {n}/{tries} 輪:看得見的「{unit}」"
                  f"候選 {total} 個")
            for i in range(min(total, 6)):
                if not click_unit_option(driver, unit, i):
                    continue
                time.sleep(0.4)
                if now() == unit:
                    print(f"[post_web_batch] ✓ 發布單位設定成功"
                          f"（第 {n} 輪、第 {i + 1} 個候選）:{unit}")
                    return True
            time.sleep(wait)

        print(f"[post_web_batch] ⛔ 發布單位設不進去（現在是「{now() or '空的'}」，"
              f"要的是「{unit}」）—— 這一筆**不發佈**。")
        print(f"[post_web_batch]    沒選單位的公告會被校網掛成該頁所屬單位"
              f"（圖書館），寧可不貼也不要掛錯單位。")
        # 失敗才 dump，而且分段印 —— 一行印到底會被截掉（2026-08-12 就被截在
        # leaves 中間，最需要的那段剛好沒看到）。
        try:
            st = driver.execute_script(_UNIT_STATE_JS, unit)
            raw = json.dumps(st, ensure_ascii=False)
            for i in range(0, min(len(raw), 4000), 800):
                print(f"[post_web_batch][診斷{i // 800 + 1}] {raw[i:i + 800]}")
            html = driver.execute_script(_UNIT_HTML_JS) or ""
            for i in range(0, min(len(html), 3200), 800):
                print(f"[post_web_batch][診斷HTML{i // 800 + 1}] {html[i:i + 800]}")
        except Exception:
            pass
        return False

    pw._select_publish_unit = patched
    try:
        yield
    finally:
        pw._select_publish_unit = real


# ── 真的貼 ─────────────────────────────────────────────────────────────────

def run(expect, path=None):
    """真的貼上校網。回 (成功數, 總數)。

    `expect` — 畫面上看到的那幾筆，每筆是 `文號` 或 `文號:指紋`。
    **動手前自己再算一次**，跟它比對:介面那道只擋得住畫面過期，擋不住這半秒內
    審核表又被改了。帶了指紋就連**內容**一起比 —— 只比文號的話，文案被換掉
    照樣會貼出去（見 `fingerprint`）。
    逐筆失敗即中止 —— 跟陳核、存查同一條規矩。
    """
    ready, _, _, warn = plan(path)
    want = parse_expect(expect)
    now = {it["文號"]: fingerprint(it) for it in ready}
    if sorted(now) != sorted(want):
        print(f"[post_web_batch] ⛔ 清單變了（你看到 {len(want)} 筆，"
              f"現在算出來 {len(now)} 筆）—— 什麼都沒貼，請重新整理再確認。")
        return 0, 0
    changed = sorted(no for no, mark in want.items() if mark and now[no] != mark)
    if changed:
        print(f"[post_web_batch] ⛔ 這幾筆的文案跟你確認過的不一樣了:"
              f"{'、'.join(changed)}")
        print("[post_web_batch]    （公告欄被改過，或剛剛重產過）"
              "—— 什麼都沒貼，請重新整理、再看一次要貼的字。")
        return 0, 0
    if warn:
        for w in warn:
            print(f"[post_web_batch] ⛔ {w}")
        return 0, 0
    if not ready:
        print("[post_web_batch] 沒有要貼的公文。")
        return 0, 0
    # 在開 Chrome **之前**擋 —— 這一道要是漏了，最壞的下場是把收文那個
    # 已經登入好的 edoc Chrome 開去校網，然後收尾把它關掉。
    busy = chrome_in_the_way()
    if busy:
        print(f"[post_web_batch] ⛔ {busy}")
        return 0, 0

    texts = {}
    for row in rs.rows(path):
        no = str(row["文號"]).strip()
        if no in now:
            texts[no] = str(row.get("公告") or "")

    from post_web_review import _launch_bare_chrome
    print(f"[post_web_batch] 準備貼 {len(ready)} 筆 → 開 Chrome、登入校網…")
    driver = _launch_bare_chrome()
    if driver is None:
        return 0, len(ready)

    ok = 0
    try:
        from document_closure.document_closure_post_web import maybe_post_announcement
        with announcement_as_summary(texts), sssh_heading_style(), \
                verified_publish_unit():
            for i, it in enumerate(ready, 1):
                print(f"\n[{i}/{len(ready)}] {it['文號']} {it['標題'][:40]}")
                if maybe_post_announcement(driver, it["目錄"]):
                    ok += 1
                    continue
                # 逐筆失敗即中止:後面那幾筆的狀態已經不確定了，硬跑下去
                # 只會把問題擴大（跟陳核、存查同一條規矩）。
                print("[post_web_batch] ⛔ 這一筆沒有貼成功，整批停在這裡。")
                print(f"[post_web_batch]    已貼出 {ok} 筆，剩下的沒有動。")
                break
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    print(f"\n[post_web_batch] 完成 {ok}/{len(ready)} 筆。")
    return ok, len(ready)


# ── CLI ───────────────────────────────────────────────────────────────────

def _slim(it):
    """給 UI 用的欄位。**內文要一起帶** —— 張貼頁核對的對象是「全校會看到的
    那幾百字」，不是一行主旨，只給標題等於讓人閉著眼睛按確定。

    順便附上指紋:畫面按下確定時把它帶回來，兩邊才能確認「要貼的字沒被換過」。
    """
    out = {k: it[k] for k in ("文號", "主旨", "標題", "分類", "附件",
                              "內文", "擋下原因", "目錄")}
    out["指紋"] = fingerprint(it)
    return out


def main():
    ap = argparse.ArgumentParser(description="校網張貼（貼審核表「公告」欄那份文案）")
    ap.add_argument("--go", action="store_true", help="真的貼（要搭配 --expect）")
    ap.add_argument("--expect", help="畫面上看到的文號清單，逗號分隔")
    ap.add_argument("--json", action="store_true", help="印一行 JSON 給 UI 用")
    ap.add_argument("--path", help="審核表路徑")
    a = ap.parse_args()

    ready, blocked, cand, warn = plan(a.path)

    if a.json:
        print(PLAN_MARK + json.dumps(
            {"可貼": [_slim(i) for i in ready],
             "擋下": [_slim(i) for i in blocked],
             "候選": [_slim(i) for i in cand],
             "警告": warn}, ensure_ascii=False))
        return

    if not a.go:
        # 預覽也講 Chrome 的事:直接跑 CLI 的人同樣會撞到，而且症狀（一片英文，
        # 或者莫名把 edoc 那個視窗開走）完全看不出根因。
        busy = chrome_in_the_way()
        if busy:
            warn = [*warn, busy]

    if a.go:
        if not a.expect:
            print("[post_web_batch] --go 一定要帶 --expect=<文號清單> ——")
            print("[post_web_batch] 這是唯一對外的動作，不接受「就照你算的貼」。")
            raise SystemExit(2)
        ok, total = run([s.strip() for s in a.expect.split(",") if s.strip()],
                        a.path)
        # **沒貼完一定要用非 0 結束碼離開。** UI 判斷成敗只看結束碼
        # （`tick()`:0 → 寫「張貼結束」、3 秒自動收起面板、報成功）。
        # 陳核（post_draft_batch）與存查（archive_batch）失敗都 SystemExit(1)，
        # 原本只有這支沒有 —— 於是「一筆都沒貼」跟「全部貼完」在畫面上長得
        # 一模一樣，而這是唯一對外的動作，最不能讓人誤以為做完了。
        # 同坑 #13、#19 的家族:不要讓畫面自己收起來去替人宣告成功。
        if not total or ok != total:
            raise SystemExit(1)
        return

    for w in warn:
        print(f"⚠️ {w}\n")
    print(f"【會貼】{len(ready)} 筆")
    for it in ready:
        print(f"  - {it['文號']}  {it['標題'][:44]}")
        print(f"      分類:{'+'.join(it['分類']) or '(無)'}　內文 {len(it['內文'])} 字")
        print(f"      附件:{'、'.join(it['附件']) or '(無)'}")
    print(f"\n【勾了要貼，但貼不出去】{len(blocked)} 筆")
    for it in blocked:
        print(f"  - {it['文號']}  {it['擋下原因']}")
    print(f"\n【還沒勾】{len(cand)} 筆")
    for reason in sorted({it["擋下原因"] or "（可以貼，在審核表「張貼」欄打 OK）"
                          for it in cand}):
        n = sum(1 for it in cand
                if (it["擋下原因"] or "（可以貼，在審核表「張貼」欄打 OK）") == reason)
        print(f"  {n:>3} 筆  {reason}")
    print("\n（這只是預覽，什麼都沒貼。）")


if __name__ == "__main__":
    main()
