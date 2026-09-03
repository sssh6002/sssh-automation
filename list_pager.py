# -*- coding: utf-8 -*-
"""list_pager.py
edoc 清單**分頁**（第 2 頁、第 3 頁…）的處理。fork 側新增，原作者那條路不碰。

## 為什麼需要這支（2026-08-20 同事回報）

edoc 的公文清單一頁只放 10 筆。承辦中累積到 11 筆以上時：

* **收新公文（備料）** —— `document_system._collect_pending_doc_nos` 只讀
  「目前畫面上那張表」，也就是**只有第 1 頁的 10 筆**。第 11 筆之後的公文
  一份都不會被下載，而畫面上印的是「承辦中清單共 10 筆」+「共處理 10/10 筆」
  —— **跟全部收完長得一模一樣**，看不出漏了。
* **送陳核** —— `post_draft_batch.focus_list` 靠「這一筆在不在畫面上」找公文，
  在第 2 頁的公文一律判定「清單裡找不到 XXX —— 這份可能已經送走」，
  訊息還是錯的（它根本沒走，只是在下一頁）。

備料這條路特別容易中：備料**不陳會**，公文會留在承辦中清單裡，
所以筆數只會越積越多，一過 10 筆就開始漏。

## 兩條翻頁策略（先試第一條）

1. **把「每頁筆數」開到最大** —— 清單下方若有那個下拉（10／20／50…），
   直接選最大值，全部公文就會在同一頁，後面完全不用翻頁，也不會有
   「處理到一半頁碼被打回第 1 頁」的問題。**最穩，優先用。**
   認定條件很嚴：選項必須全是數字，而且**目前選的值要等於畫面上的筆數**
   （現在顯示 10 筆、下拉也選著 10 → 這才是每頁筆數，不是別的篩選器）。
2. **點「下一頁」** —— 找不到那個下拉時才走這條。

## 這支不自己亂點東西

清單頁上同時有「送出」「刪除」「簽收」這種**按下去不可復原**的按鈕。
所以候選元素過三關才點：

* 文字要**完全等於**「下一頁／次頁／>／»」這類翻頁字樣，或等於目標頁碼數字；
* 文字含黑名單字（送出、刪除、簽收、結案、陳核、退回…）一律不碰；
* 點完要**驗**：畫面上的公文號真的換了一批才算翻頁成功，沒換就當作沒翻到。

## 漏了就要看得見

`walk_pages(expect=N)` 的 `expect` 傳左側「承辦中(N)」的 N。
翻完所有頁之後**總數對不上 N 就大聲喊** —— 這次的病就是「漏了但畫面很正常」，
所以寧可吵，也不要再安靜地少收公文。
"""

import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

# 一頁最多翻幾次，純防呆（翻頁鈕壞掉時不要無限迴圈）。
MAX_PAGES = 30

# 點完之後等畫面換一批公文號，最多等這麼久。
_CHANGE_TIMEOUT = 8.0


# ── 讀目前這一頁的公文號 ────────────────────────────────────────────────────
#
# 與 document_system._collect_pending_doc_nos 同一套 column-index 策略：
# 找含「公文文號」的 th → 算出它是第幾欄 → 把同一欄的 td 全撈出來。

_COLLECT_JS = r"""
    var pat = /^[A-Z][A-Z0-9]*\d{4,}$/;
    var out = [];
    var ths = document.querySelectorAll('th');
    for (var i = 0; i < ths.length; i++) {
        if ((ths[i].textContent || '').trim().indexOf('公文文號') === -1) continue;
        var headerRow = ths[i].parentElement;
        if (!headerRow) continue;
        var idx = -1;
        for (var j = 0; j < headerRow.children.length; j++) {
            if (headerRow.children[j] === ths[i]) { idx = j; break; }
        }
        if (idx === -1) continue;
        var table = ths[i].closest('table');
        if (!table) continue;
        var rows = table.querySelectorAll('tbody tr');
        for (var k = 0; k < rows.length; k++) {
            var cells = rows[k].children;
            if (idx >= cells.length) continue;
            var t = (cells[idx].textContent || '').trim();
            if (pat.test(t) && out.indexOf(t) === -1) out.push(t);
        }
        break;
    }
    return out;
"""


# ── 順手記下每一列的「簽核」欄:線 = 線上簽核, 紙 = 紙本轉線上 ────────────────
#
# 紙本公文進 edoc 時清單「簽核」欄是「紙」。這種公文的**來文本文還沒掛上去** ——
# 要承辦人拿到實體公文、自己掃描,在 edoc 勾那一列按「轉線上」把來文 pdf 上傳,
# 它才會變成一般電子公文。所以備料點進去只會找不到「下載」按鈕、等 20 秒然後失敗
# (2026-09-03 實測 MWAA1156008767)。
#
# 讀清單的時候一起把這一欄記下來,失敗時才講得出**真正的原因**,而不是丟一句
# 「下載/解壓縮失敗」讓人自己猜。這一步不上網、不多跑一次 query,附在原本那次
# column-index 掃描裡。
SIGN_KINDS = {}


def reset_sign_kinds():
    SIGN_KINDS.clear()


def sign_kind(doc_no):
    """回這筆公文在清單「簽核」欄的字（'線'/'紙'）。沒讀到回 ''。"""
    return SIGN_KINDS.get(doc_no, "")


def is_paper(doc_no):
    """是不是紙本轉線上(尚未上傳來文 pdf)的公文。"""
    return sign_kind(doc_no).startswith("紙")


# 同一次掃描順便撈「簽核」欄。找不到那個表頭(欄位改名/別張清單)時 idxSign = -1,
# 公文號照樣讀得到 —— 記不到簽核別只是少了原因提示,不能讓它害整個備料讀不到清單。
_COLLECT_SIGN_JS = r"""
    var pat = /^[A-Z][A-Z0-9]*\d{4,}$/;
    var out = [];
    var ths = document.querySelectorAll('th');
    for (var i = 0; i < ths.length; i++) {
        if ((ths[i].textContent || '').trim().indexOf('公文文號') === -1) continue;
        var headerRow = ths[i].parentElement;
        if (!headerRow) continue;
        var idx = -1, idxSign = -1;
        for (var j = 0; j < headerRow.children.length; j++) {
            if (headerRow.children[j] === ths[i]) idx = j;
            var h = (headerRow.children[j].textContent || '').replace(/\s/g, '');
            if (h === '簽核') idxSign = j;
        }
        if (idx === -1) continue;
        var table = ths[i].closest('table');
        if (!table) continue;
        var rows = table.querySelectorAll('tbody tr');
        var seen = {};
        for (var k = 0; k < rows.length; k++) {
            var cells = rows[k].children;
            if (idx >= cells.length) continue;
            var t = (cells[idx].textContent || '').trim();
            if (!pat.test(t) || seen[t]) continue;
            seen[t] = 1;
            var sign = '';
            if (idxSign !== -1 && idxSign < cells.length) {
                sign = (cells[idxSign].textContent || '').replace(/\s/g, '');
            }
            out.push({no: t, sign: sign});
        }
        break;
    }
    return out;
"""


def page_doc_nos(driver):
    """讀**目前這一頁**清單上的公文號（依畫面順序、去重）。讀不到回 []。

    順便把每一列的「簽核」欄記進 SIGN_KINDS（見上面那段）。

    呼叫前要先切到含「公文文號」表頭的 frame。
    """
    try:
        rows = driver.execute_script(_COLLECT_SIGN_JS) or []
    except Exception as e:
        print(f"[pager] 讀簽核欄失敗（改用只讀公文號）:{type(e).__name__}: {e}")
        rows = None
    # 形狀不對(假 driver 回 True、頁面回怪東西)就別硬吃 —— 退回舊路,
    # 舊路自己有 try/except,壞掉最多讀不到公文號,不會把整個備料炸掉。
    if isinstance(rows, (list, tuple)) and rows:
        nos = []
        for r in rows:
            # dict = 有讀到「簽核」欄;字串 = 只有公文號(舊 JS 的形狀,測試的假
            # driver 也是這個形狀)。兩種都要收,否則清單會整個讀成空的。
            if isinstance(r, dict):
                no = (r.get("no") or "").strip()
                kind = (r.get("sign") or "").strip()
            else:
                no, kind = str(r or "").strip(), ""
            if not no:
                continue
            nos.append(no)
            if kind:
                SIGN_KINDS[no] = kind
        return nos
    try:
        return list(driver.execute_script(_COLLECT_JS) or [])
    except Exception as e:
        print(f"[pager] 讀公文號失敗:{type(e).__name__}: {e}")
        return []


# ── 策略 1:每頁筆數開到最大 ────────────────────────────────────────────────

_PAGESIZE_JS = r"""
    var rows = arguments[0];
    var sels = document.querySelectorAll('select');
    for (var i = 0; i < sels.length; i++) {
        var s = sels[i];
        if (s.offsetParent === null || s.disabled) continue;
        var vals = [], ok = true;
        for (var j = 0; j < s.options.length; j++) {
            var t = (s.options[j].value || s.options[j].textContent || '').trim();
            if (!/^\d+$/.test(t)) { ok = false; break; }
            vals.push(parseInt(t, 10));
        }
        if (!ok || vals.length < 2) continue;
        var cur = parseInt((s.value || '').trim(), 10);
        // 目前選的值要等於畫面上的筆數,才確定這個下拉是「每頁筆數」。
        if (isNaN(cur) || cur !== rows) continue;
        var best = -1, bestIdx = -1;
        for (var k = 0; k < vals.length; k++) {
            if (vals[k] > best) { best = vals[k]; bestIdx = k; }
        }
        if (best <= cur) continue;
        s.selectedIndex = bestIdx;
        s.dispatchEvent(new Event('change', {bubbles: true}));
        if (typeof s.onchange === 'function') { try { s.onchange(); } catch (e) {} }
        return {value: best, id: s.id || '', name: s.name || ''};
    }
    return null;
"""


def set_page_size_max(driver, rows):
    """把「每頁筆數」下拉改成最大值。成功回新的每頁筆數，沒有那個下拉回 0。

    rows — 目前畫面上讀到幾筆（用來認出哪個 select 才是每頁筆數，見模組說明）。
    """
    try:
        got = driver.execute_script(_PAGESIZE_JS, rows)
    except Exception as e:
        print(f"[pager] 找「每頁筆數」失敗:{type(e).__name__}: {e}")
        return 0
    if not got:
        return 0
    print(f"      OK:把「每頁筆數」從 {rows} 改成 {got['value']}"
          f"（下拉 id={got.get('id') or '無'} name={got.get('name') or '無'}）"
          f" —— 這樣全部公文會在同一頁，不用翻頁")
    return int(got["value"])


# ── 策略 2:點「下一頁」 ────────────────────────────────────────────────────

_NEXT_JS = r"""
    var target = String(arguments[0]);
    var NEXT = ['下一頁', '次頁', '下頁', '下一項', 'next', '>', '>>', '›',
                '»', '▶', '►', '→'];
    var BAD = ['送出', '刪除', '簽收', '結案', '儲存', '確定', '送件', '陳核',
               '退回', '取消', '列印', '歸檔', '抽回', '存查', '登出'];
    var PAGER = /(go_?to_?page|gopage|setpage|changepage|nextpage|topage|pageno|page_?index|querypage|_page\()/i;

    function label(el) {
        var t = (el.tagName === 'INPUT' ? (el.value || '') : (el.textContent || '')).trim();
        if (!t) t = (el.getAttribute('title') || el.getAttribute('alt') || '').trim();
        return t;
    }
    function hooks(el) {
        return (el.getAttribute('onclick') || '') + ' ' +
               (el.getAttribute('href') || '') + ' ' +
               (el.getAttribute('src') || '');
    }
    function usable(el) {
        if (el.offsetParent === null) return false;
        if (el.disabled === true) return false;
        var cls = (el.className || '') + ' ' + (el.getAttribute('aria-disabled') || '');
        if (/disable|disabled|true/i.test(cls) && /disable/i.test(cls)) return false;
        var t = label(el);
        for (var i = 0; i < BAD.length; i++) if (t.indexOf(BAD[i]) !== -1) return false;
        // 只點葉子:避免點到「包住下一頁字樣的那一整塊」。
        var kids = el.querySelectorAll('*');
        for (var k = 0; k < kids.length; k++) {
            var ct = (kids[k].textContent || kids[k].value || '').trim();
            if (ct && ct === t) return false;
        }
        return true;
    }
    function fire(el, how) {
        el.scrollIntoView({block: 'center'});
        el.click();
        return {how: how, tag: el.tagName,
                text: label(el).slice(0, 20),
                html: (el.outerHTML || '').slice(0, 180)};
    }

    var all = document.querySelectorAll('a, button, input, img, span, div, td, li');

    // 第一輪:文字就是「下一頁」那一類。
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (!usable(el)) continue;
        var t = label(el).toLowerCase();
        for (var n = 0; n < NEXT.length; n++) {
            if (t === NEXT[n].toLowerCase()) return fire(el, '下一頁鈕(' + label(el) + ')');
        }
    }
    // 第二輪:圖片鈕(alt/title 沒寫字,只能看檔名)。
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (el.tagName !== 'IMG' && el.tagName !== 'INPUT') continue;
        if (!usable(el)) continue;
        var src = (el.getAttribute('src') || '').toLowerCase();
        if (!src) continue;
        if (/next|forward|arrow_?r|right|_r\.(gif|png)/.test(src) && !/first|last|prev/.test(src)) {
            return fire(el, '下一頁圖示(' + src.split('/').pop() + ')');
        }
    }
    // 第三輪:頁碼連結,文字剛好是目標頁碼。
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (!usable(el)) continue;
        if (label(el) !== target) continue;
        if (el.tagName === 'A' || PAGER.test(hooks(el))) {
            return fire(el, '頁碼連結(' + target + ')');
        }
    }
    // 第四輪:onclick/href 直接寫著跳到目標頁。
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        if (!usable(el)) continue;
        var h = hooks(el);
        if (!PAGER.test(h)) continue;
        var m = h.match(/(\d+)/g);
        if (m && m.indexOf(target) !== -1) return fire(el, '翻頁函式(' + h.slice(0, 60) + ')');
    }
    return null;
"""


def click_next_page(driver, target_page):
    """點「下一頁」（或頁碼 target_page）。點到回描述 dict，找不到回 None。

    **只點，不驗**。有沒有真的翻過去由 `wait_page_change` 負責。
    """
    try:
        got = driver.execute_script(_NEXT_JS, int(target_page))
    except Exception as e:
        print(f"[pager] 找翻頁鈕失敗:{type(e).__name__}: {e}")
        return None
    if got:
        print(f"      點了 {got['how']}，等第 {target_page} 頁載入…")
    return got


def wait_page_change(driver, before, timeout=None):
    """等清單換一批公文號。換了回新的 list，等到 timeout 還沒換回 None。

    timeout 預設讀模組層的 `_CHANGE_TIMEOUT`（測試會把它調小）。
    """
    deadline = time.time() + (_CHANGE_TIMEOUT if timeout is None else timeout)
    while time.time() < deadline:
        time.sleep(0.4)
        now = page_doc_nos(driver)
        if now and now != list(before):
            return now
    return None


# ── 對外主力:把所有頁都走過一遍 ────────────────────────────────────────────

def walk_pages(driver, expect=-1, max_pages=MAX_PAGES):
    """從目前這一頁開始往後翻，回 (每頁的公文號 list, 警告 list)。

    expect — 左側 sidebar 寫的筆數（例如「承辦中(13)」的 13）。傳 -1 = 不知道。
             知道的話最後會拿總數跟它對，**對不上就大聲喊**。

    回來時 driver 停在**最後一頁**。要回第 1 頁請呼叫端自己重點左側選單。
    """
    warn = []
    # 每次重走清單都從乾淨的簽核別開始 —— 上一輪的殘留會讓失敗原因報到別筆去。
    reset_sign_kinds()
    first = page_doc_nos(driver)
    if not first:
        return [], ["清單上一筆公文號都沒讀到 —— 現在畫面上可能不是公文清單。"]

    # 策略 1:每頁筆數開到最大。成功的話通常一頁就裝得下，下面的迴圈自然只跑一輪。
    if expect < 0 or len(first) < expect:
        if set_page_size_max(driver, len(first)):
            bigger = wait_page_change(driver, first)
            if bigger:
                first = bigger
            else:
                print("      （每頁筆數改了但清單沒變 —— 照樣用翻頁的方式處理）")

    pages = [first]
    seen = set(first)
    quiet = []
    while len(pages) < max_pages:
        if expect >= 0 and len(seen) >= expect:
            break                      # 已經收齊了，不必再翻
        target = len(pages) + 1
        got = click_next_page(driver, target)
        if not got:
            quiet.append(f"找不到往第 {target} 頁的翻頁鈕")
            break
        nos = wait_page_change(driver, pages[-1])
        if nos is None:
            quiet.append(f"點了「{got['text']}」但清單沒有換頁"
                         f"（{got['html']}）")
            break
        fresh = [n for n in nos if n not in seen]
        if not fresh:
            quiet.append(f"第 {target} 頁的公文跟前面重複，停止翻頁")
            break
        print(f"      OK:第 {target} 頁讀到 {len(nos)} 筆")
        pages.append(nos)
        seen.update(nos)
    else:
        warn.append(f"翻超過 {max_pages} 頁還沒完 —— 停在這裡，剩下的沒處理。")

    total = sum(len(p) for p in pages)
    if expect >= 0 and total < expect:
        warn.append(
            f"左側寫「{expect}」筆，翻完所有頁只讀到 {total} 筆 —— "
            f"少了 {expect - total} 筆，這次不會處理到它們。"
            + ("原因:" + "；".join(quiet) if quiet else ""))
    elif expect < 0 and quiet:
        # 不知道應該有幾筆，翻頁又中途停下 —— 只能提醒，不能斷定漏了。
        warn.append("翻頁提早停下（" + "；".join(quiet) +
                    "）。如果清單超過一頁，後面幾頁這次沒處理到。")
    return pages, warn


def find_doc_across_pages(driver, doc_no, refocus=None, max_pages=MAX_PAGES):
    """一頁一頁往後找 doc_no，找到就停在那一頁回 True。

    refocus — 可傳一個「把清單叫回第 1 頁」的函式（回 True/False）。有傳就先叫，
              確保是從第 1 頁開始找（畫面停在第 3 頁時往後找會漏掉前面的）。
    """
    if refocus is not None and not refocus():
        return False
    nos = page_doc_nos(driver)
    if doc_no in nos:
        return True
    seen = set(nos)
    for page in range(2, max_pages + 1):
        if not click_next_page(driver, page):
            return False
        nos = wait_page_change(driver, nos)
        if nos is None:
            return False
        if doc_no in nos:
            print(f"      OK:{doc_no} 在第 {page} 頁")
            return True
        if not [n for n in nos if n not in seen]:
            return False
        seen.update(nos)
    return False


def ensure_page_has(driver, doc_no, refocus=None):
    """確認畫面上這一頁看得到 doc_no；看不到就翻頁去找。回 True/False。

    處理途中頁碼常被系統打回第 1 頁（點開公文、關閉閱覽器之後），
    所以每一筆動手之前都要問一次「它現在在不在畫面上」。
    """
    if doc_no in page_doc_nos(driver):
        return True
    print(f"      {doc_no} 不在目前這一頁，往後翻找…")
    return find_doc_across_pages(driver, doc_no, refocus=refocus)


# ── 診斷:把翻頁區長什麼樣子印出來（唯讀，不點任何東西）────────────────────

_PROBE_JS = r"""
    var out = {rows: 0, candidates: [], selects: []};
    var pat = /^[A-Z][A-Z0-9]*\d{4,}$/;
    var body = document.body ? (document.body.innerText || '') : '';
    var m = body.match(/[A-Z][A-Z0-9]*\d{4,}/g);
    out.rows = m ? m.length : 0;
    var all = document.querySelectorAll('a, button, input, img, span, div, td, li');
    for (var i = 0; i < all.length && out.candidates.length < 60; i++) {
        var el = all[i];
        var t = (el.tagName === 'INPUT' ? (el.value || '') : (el.textContent || '')).trim();
        var h = (el.getAttribute('onclick') || '') + ' ' + (el.getAttribute('href') || '');
        var src = el.getAttribute('src') || '';
        var looks = (t.length > 0 && t.length <= 8 &&
                     /^([0-9]+|下一頁|上一頁|第一頁|最後一頁|次頁|末頁|首頁|>|<|>>|<<|›|«|»)$/.test(t))
                    || /page|pg=|翻頁/i.test(h)
                    || /next|prev|first|last|arrow/i.test(src)
                    || /頁/.test(t);
        if (!looks) continue;
        if (pat.test(t)) continue;
        out.candidates.push({tag: el.tagName, text: t.slice(0, 20),
                             visible: el.offsetParent !== null,
                             onclick: (el.getAttribute('onclick') || '').slice(0, 120),
                             href: (el.getAttribute('href') || '').slice(0, 120),
                             src: src.slice(0, 80),
                             cls: (el.className || '').toString().slice(0, 60),
                             html: (el.outerHTML || '').slice(0, 200)});
    }
    var sels = document.querySelectorAll('select');
    for (var i = 0; i < sels.length; i++) {
        var s = sels[i], opts = [];
        for (var j = 0; j < s.options.length && j < 12; j++) {
            opts.push((s.options[j].value || '') + '/' +
                      (s.options[j].textContent || '').trim());
        }
        out.selects.push({id: s.id || '', name: s.name || '', value: s.value,
                          visible: s.offsetParent !== null, options: opts});
    }
    return out;
"""


def probe(driver):
    """印出目前清單頁的翻頁區長相。唯讀 —— 一個東西都不會點。"""
    try:
        got = driver.execute_script(_PROBE_JS)
    except Exception as e:
        print(f"[pager] 探測失敗:{type(e).__name__}: {e}")
        return None
    print(f"\n畫面上看得到 {got['rows']} 個公文號")
    print(f"\n【下拉選單】{len(got['selects'])} 個")
    for s in got["selects"]:
        print(f"  id={s['id'] or '無'} name={s['name'] or '無'} "
              f"目前={s['value']} 可見={s['visible']}")
        print(f"    選項:{s['options']}")
    print(f"\n【翻頁候選】{len(got['candidates'])} 個")
    for c in got["candidates"]:
        print(f"  <{c['tag']}> 「{c['text']}」 可見={c['visible']} class={c['cls']}")
        if c["onclick"]:
            print(f"      onclick={c['onclick']}")
        if c["href"]:
            print(f"      href={c['href']}")
        if c["src"]:
            print(f"      src={c['src']}")
        print(f"      {c['html']}")
    return got


if __name__ == "__main__":
    # 診斷用:接上已經開著的 Chrome，把清單的翻頁區印出來。**唯讀，不點任何東西。**
    #
    #     1. 先用介面「收新公文」或 py main.py 4 把 Chrome 開到公文系統主畫面
    #     2. 左側點「承辦中」，讓清單出現（超過 10 筆才看得到翻頁區）
    #     3. python list_pager.py
    from document_system import _switch_to_frame_with_xpath
    from fill_in_draft import _attach_existing_chrome

    d = _attach_existing_chrome()
    if d is None:
        raise SystemExit(1)
    print(f"目前頁面:{d.current_url}")
    d.switch_to.default_content()
    if not _switch_to_frame_with_xpath(
            d, "//th[contains(normalize-space(), '公文文號')]", "公文文號表頭",
            timeout=8):
        print("切不到清單 frame —— 請先在左側點「承辦中」把清單叫出來再跑一次。")
        raise SystemExit(1)
    print(f"這一頁的公文號:{page_doc_nos(d)}")
    probe(d)
