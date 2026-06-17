"""
document_closure_post_web.py
結案存查下載後，若總結「承辦文字」含「於官網公告」，把公文發佈到松高校網
(www.sssh.tp.edu.tw)：以總結的「主旨」為標題、主旨下方的「條列摘要」為內容。

設計見 docs/superpowers/specs/2026-06-03-closure-post-web-design.md。

呼叫方式：
  1) 結案存查流程中自動串接（document_closure._process_one_pending_closure_doc）：
       maybe_post_announcement(driver, extract_dir)
  2) 單獨執行（只對指定的「一個」公文目錄）：
       py document_closure/document_closure_post_web.py <公文目錄>
"""

import csv
import glob
import html
import os
import re
import time
from datetime import datetime

# 校網首頁(判登入/登出狀態)與發佈板。
# 發佈板=圖書館頁(/nss/s/main/p/library)的「圖書館公告」模組。需先點上方 admin bar
# 的「模組」進編輯模式,該模組才會出現「新增公告」鈕(2026-06-06 實機驗證)。
HOME_URL = "https://www.sssh.tp.edu.tw/nss/p/index"
ANNO_URL = "https://www.sssh.tp.edu.tw/nss/s/main/p/library"

# 觸發關鍵字：總結「承辦文字」(## 行) 含此字串才發佈到校網。
ANNOUNCE_KEYWORD = "於官網公告"

# 主旨行：兼容有無 * 前綴、半/全形冒號、冒號前後空白。
_SUBJECT_RE = re.compile(r'^\*?\s*主旨\s*[:：]\s*(.+)$')
# 條列行：1. / 1、 / 1.（後接內容，可無空白）。
_BODY_ITEM_RE = re.compile(r'^\s*\d+\s*[.、]\s*.+$')
# 存查分類行：首行 `#存查分類:研習 03750401` → 取類別名(研習/資安…)。
_CATEGORY_RE = re.compile(r'^#\s*存查分類\s*[:：]\s*(\S+)')


def _normalize_cat(s):
    """正規化分類字串以容忍異體字 — 站上下拉用「研」、總結 spec 用異體字「硏」。
    把「硏」統一成「研」後再做子字串比對。"""
    return (s or "").replace("硏", "研").strip()


def _parse_summary_text(text):
    """解析總結.md 文字，回 {'handling','title','body','category','sync_categories'} 或 None。

    - handling（承辦文字）：第一個以 `##` 開頭的行（取最先出現者，即承辦文字那行），
      去掉開頭所有 # 與前後空白；無此行則為 None。
    - title（主旨）：第一個 `主旨[:：]` 行冒號後的文字。
    - body（條列摘要）：主旨行之後、檔尾之前，所有 `數字.`／`數字、` 開頭的條列行，
      原樣（strip 過）以換行串接。
    - category（存查分類）：`#存查分類:` 後的類別名。
    - sync_categories（校網同步顯示）：`####` 行內容以「+」分隔的分類清單；該行空白 / 缺 → []。

    主旨或 body 任一缺 → 回 None（寧缺勿發殘缺公告）。
    """
    handling = None
    title = None
    category = None
    sync_raw = None
    body_lines = []
    subject_seen = False

    for raw in text.splitlines():
        line = raw.strip()
        if category is None:
            mc = _CATEGORY_RE.match(line)
            if mc:
                category = mc.group(1)
                continue
        # 「校網同步顯示」#### 行 — 必須在 ## handling 判斷前先攔（#### 也 startswith ##）。
        # 後面內容可為空字串（空白＝不選任何同步分類）。
        if sync_raw is None and line.startswith("####"):
            sync_raw = line[4:].strip()
            continue
        if handling is None and line.startswith("##"):
            handling = line.lstrip("#").strip() or None
            continue
        if title is None:
            m = _SUBJECT_RE.match(line)
            if m:
                title = m.group(1).strip()
                subject_seen = True
                continue
        if subject_seen and _BODY_ITEM_RE.match(line):
            body_lines.append(line)

    if title is None or not body_lines:
        return None
    sync_categories = [c.strip() for c in sync_raw.split("+")] if sync_raw else []
    sync_categories = [c for c in sync_categories if c]
    return {"handling": handling, "title": title, "body": "\n".join(body_lines),
            "category": category, "sync_categories": sync_categories}


def _parse_summary(extract_dir):
    """從 extract_dir 內 *總結.*.md 讀檔並解析。回 dict 或 None。"""
    summaries = sorted(glob.glob(os.path.join(extract_dir, "*總結.*.md")))
    if not summaries:
        return None
    try:
        text = open(summaries[0], encoding="utf-8").read()
    except OSError:
        return None
    return _parse_summary_text(text)


def _should_post(summary):
    """承辦文字是否含「於官網公告」→ 該不該發佈到校網。summary 為 None / handling
    為 None / 不含關鍵字 → False。"""
    if not summary:
        return False
    handling = summary.get("handling")
    return bool(handling) and ANNOUNCE_KEYWORD in handling


def _body_to_html(body):
    """把條列摘要(每行一條)轉成 CKEditor 可吃的 HTML 段落,逐行一個 <p>。

    跳過空行;對每行做 HTML escape,避免內容含 < & 破版。
    """
    lines = [ln for ln in body.split("\n") if ln.strip()]
    return "".join(f"<p>{html.escape(ln)}</p>" for ln in lines)


def _find_attachments(extract_dir):
    """回 extract_dir 內所有檔名含 ATTCH 的檔案絕對路徑(公文附件 *ATTCH*),排序後回傳。"""
    try:
        names = os.listdir(extract_dir)
    except OSError:
        return []
    return [os.path.abspath(os.path.join(extract_dir, n))
            for n in sorted(names) if "attch" in n.lower()]


def _posted_marker_path(extract_dir):
    """回 <公文主檔名>已公告.txt 完整路徑;找不到主檔名回 None。"""
    from document_closure.document_closure import _find_main_doc_basename  # 避免循環 import
    base = _find_main_doc_basename(extract_dir)
    if not base:
        return None
    return os.path.join(extract_dir, f"{base}已公告.txt")


def _already_posted(extract_dir):
    """extract_dir 是否已有任何 *已公告.txt → 視為已公告、免再發佈。

    使用者規則(2026-06-17):只要公文夾內出現任何結尾為「已公告.txt」的檔案,就
    視為已公告、該公文免公告。比舊版只認 <主檔名>已公告.txt 寬鬆:即使抓不到主檔名
    (_posted_marker_path 回 None)、或標記檔名與主檔名對不上,只要有 *已公告.txt
    就跳過,避免重複公告;也讓人工手動放一個 *已公告.txt 就能標記「此公文免公告」。
    """
    try:
        names = os.listdir(extract_dir)
    except OSError:
        return False
    return any(n.endswith("已公告.txt") for n in names)


def _write_posted_marker(extract_dir, title, board_name, selected_cats):
    """寫 <主檔名>已公告.txt（三行格式）。成功回路徑,否則 None。

    內容（使用者指定格式）:
        <ISO8601>_已公告。
        主旨:<title>
        公告於:<board_name>。同步顯示於:<selected_cats 以 + 串接;空則「無」>。

    board_name 由發佈當下從頁面實抓（_read_board_name）;selected_cats 為實際點選成功
    的分類（對不到被略過者不列入,標記才誠實）。本函式只在發佈確認成功後才被呼叫。
    """
    p = _posted_marker_path(extract_dir)
    if not p:
        print("      [WARN] 找不到公文主檔名,無法寫已公告標記")
        return None
    cats_str = "+".join(selected_cats) if selected_cats else "無"
    iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    content = (f"{iso}_已公告。\n"
               f"主旨:{title}\n"
               f"公告於:{board_name}。同步顯示於:{cats_str}。")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"      OK:已寫已公告標記檔 {os.path.basename(p)}")
        print(f"          內容:\n{content}")
        return p
    except OSError as e:
        print(f"      [WARN] 寫已公告標記失敗:{type(e).__name__}: {e}")
        return None


# ── 已公告持久清冊(CSV)────────────────────────────────────────────────────
# 問題:結案流程每跑一次就把公文重新下載解壓成「全新」資料夾(見
# document_closure._process_one_pending_closure_doc),所以寫在公文夾內的
# *已公告.txt 在下次公告判斷時根本不存在 → 重複公告(使用者 2026-06-17 回報)。
# 解法:在結案根目錄(各公文夾的上層)維護一份持久 CSV 清冊,記錄每筆已發佈公告。
# 清冊不在任何公文夾內,重新下載/解壓/刪某個公文夾都不會動到它,故能可靠防止
# 同一公文重複公告;CSV 格式方便日後用 Excel 調閱。
# 欄位(內容比照 *已公告.txt,每則開頭加公文檔號):
#   公文檔號(如 MWAA1156005980)、時間、主旨、公告於、同步顯示於
_LEDGER_FILENAME = "_已公告清單.csv"
_LEDGER_HEADER = ["公文檔號", "時間", "主旨", "公告於", "同步顯示於"]


def _ledger_path(extract_dir):
    """回已公告清冊完整路徑 = extract_dir 上層(結案根目錄)/_已公告清單.csv。"""
    closure_root = os.path.dirname(os.path.normpath(os.path.abspath(extract_dir)))
    return os.path.join(closure_root, _LEDGER_FILENAME)


def _doc_no_of(extract_dir):
    """公文檔號 = 公文夾名(例 MWAA1156005980)。"""
    return os.path.basename(os.path.normpath(os.path.abspath(extract_dir)))


def _ledger_doc_nos(extract_dir):
    """讀 CSV 清冊回「已公告的公文檔號」set;清冊不存在回空 set。只取第 1 欄。"""
    out = set()
    try:
        with open(_ledger_path(extract_dir), encoding="utf-8-sig", newline="") as f:
            for row in csv.reader(f):
                if not row or not row[0] or row[0] == _LEDGER_HEADER[0]:
                    continue  # 空列或表頭
                out.add(row[0])
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"      [WARN] 讀已公告清冊失敗:{type(e).__name__}: {e}")
    return out


def _append_ledger_row(extract_dir, doc_no, iso, title, board_name, cats_str):
    """把一列 append 到 CSV 清冊(欄位用 csv 模組正確跳脫,標題含逗號/引號也安全)。

    清冊不存在/為空時先寫表頭(並以 utf-8-sig 寫 BOM,讓 Excel 正確辨識中文);
    之後追加用 utf-8 不重覆寫 BOM。寫失敗只印 WARN、不丟例外。
    """
    path = _ledger_path(extract_dir)
    try:
        need_header = (not os.path.exists(path)) or os.path.getsize(path) == 0
        enc = "utf-8-sig" if need_header else "utf-8"
        with open(path, "a", encoding=enc, newline="") as f:
            w = csv.writer(f)
            if need_header:
                w.writerow(_LEDGER_HEADER)
            w.writerow([doc_no, iso, title or "", board_name or "", cats_str or ""])
        print(f"      OK:已登錄已公告清冊 {doc_no} → {_LEDGER_FILENAME}")
    except OSError as e:
        print(f"      [WARN] 寫已公告清冊失敗:{type(e).__name__}: {e}")


def _append_to_ledger(extract_dir, doc_no, title, board_name, selected_cats):
    """成功公告後登錄一列:時間=現在,同步顯示於=selected_cats 以 + 串接(空則「無」)。

    內容與 *已公告.txt(_write_posted_marker)一致,只是改成 CSV 並在最前面加公文檔號。
    """
    cats_str = "+".join(selected_cats) if selected_cats else "無"
    iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _append_ledger_row(extract_dir, doc_no, iso, title, board_name, cats_str)


def _read_marker_fields(marker_path):
    """從一個 *已公告.txt(_write_posted_marker 三行格式)讀回
    (iso, title, board_name, cats_str);讀不到的欄回空字串。供補登清冊用。

    格式:
        <iso>_已公告。
        主旨:<title>
        公告於:<board>。同步顯示於:<cats>。
    """
    iso = title = board = cats = ""
    try:
        lines = open(marker_path, encoding="utf-8").read().splitlines()
    except OSError:
        return iso, title, board, cats
    for s in (ln.strip() for ln in lines):
        if not iso and "_已公告" in s:
            iso = s.split("_已公告", 1)[0]
        elif s.startswith("主旨:"):
            title = s[len("主旨:"):]
        elif s.startswith("公告於:"):
            rest = s[len("公告於:"):]
            if "。同步顯示於:" in rest:
                board, cats = rest.split("。同步顯示於:", 1)
                cats = cats.rstrip("。")
            else:
                board = rest.rstrip("。")
    return iso, title, board, cats


def _backfill_ledger_from_markers(extract_dir):
    """一次性補登:結案根目錄下凡有 *已公告.txt 的公文夾,把它補進 CSV 清冊。

    讓「清冊機制上線前就已公告(只留 per-folder *已公告.txt)」的公文也納入防重覆。
    會解析 *已公告.txt 取回主旨/公告於/同步顯示於,使補登列內容與新列一致。
    冪等:已在清冊內的公文檔號不重覆寫。任何 I/O 失敗都安靜略過。
    """
    root = os.path.dirname(_ledger_path(extract_dir))
    known = _ledger_doc_nos(extract_dir)
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in sorted(names):
        sub = os.path.join(root, name)
        if name in known or not os.path.isdir(sub):
            continue
        marker = None
        try:
            for f in sorted(os.listdir(sub)):
                if f.endswith("已公告.txt"):
                    marker = os.path.join(sub, f)
                    break
        except OSError:
            continue
        if not marker:
            continue
        iso, title, board, cats = _read_marker_fields(marker)
        _append_ledger_row(extract_dir, name, iso, title, board, cats)


def _already_announced(extract_dir):
    """此公文是否已公告過 → 免再公告。判定來源(任一成立即視為已公告):

    1. 公文夾內有 *已公告.txt(per-folder 標記;使用者也可手動丟一個來標「免公告」)
    2. 持久清冊內已登錄此公文文號(重新下載成全新資料夾、標記消失時的可靠後盾)

    查清冊前先 _backfill_ledger_from_markers,把舊有只剩 per-folder 標記的公文補進清冊。
    """
    _backfill_ledger_from_markers(extract_dir)
    if _already_posted(extract_dir):
        return True
    return _doc_no_of(extract_dir) in _ledger_doc_nos(extract_dir)


def _stop_banner(reason, hint=""):
    """印 STOP banner(發佈段失敗時用,讓 run.log 醒目)。"""
    print("\n" + "!" * 60)
    print(f"[post_web][STOP] {reason}")
    if hint:
        print(f"[post_web][STOP] 建議:{hint}")
    print("!" * 60 + "\n")


def _close_school_tab(driver):
    """關掉目前(校網)分頁、切回剩下的第一個分頁(edoc 主分頁)。"""
    try:
        if len(driver.window_handles) <= 1:
            return
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
    except Exception as e:
        print(f"      [WARN] 關校網分頁失敗:{type(e).__name__}: {e}")


def _open_and_login_sssh(driver):
    """開新分頁到校網首頁:右上「登出」=已登入(跳過);「登入」=點它進登入頁、用
    env.env 帳密登入(使用者指定流程)。

    成功 → True(校網分頁留在前景供發佈);失敗 → 關掉新分頁、回 False。
    """
    from selenium.webdriver.common.by import By
    from taipeion_login_selenium import _read_config

    driver.switch_to.new_window("tab")
    driver.get(HOME_URL)
    time.sleep(2)

    # 首頁右上:有「登出」連結 = 已登入
    state = driver.execute_script("""
        const links = [...document.querySelectorAll('a')];
        if (links.some(a => (a.innerText||'').trim() === '登出')) return 'logged_in';
        if (links.some(a => (a.innerText||'').trim() === '登入')) return 'need_login';
        return 'unknown';
    """)
    if state == 'logged_in':
        print("[post_web] 首頁顯示「登出」→ 已登入。")
        return True

    # 點「登入」→ 轉到 /passport/tpeEntrance 登入頁
    driver.execute_script("""
        for (const a of document.querySelectorAll('a')) {
            if ((a.innerText||'').trim() === '登入') { a.click(); return; }
        }
    """)
    time.sleep(2.5)

    if not driver.find_elements(By.ID, "login-user-name"):
        if "passport" not in driver.current_url:
            print("[post_web] 點登入後未見帳密框且已離開 passport → 視為已登入。")
            return True
        _stop_banner("點登入後找不到帳密框", "實機檢查登入頁 DOM")
        _close_school_tab(driver)
        return False

    acc, pw_ = _read_config("sssh_account"), _read_config("sssh_password")
    if not acc or not pw_:
        _stop_banner("env.env 缺 sssh_account / sssh_password", "在 env.env 填入校網帳密")
        _close_school_tab(driver)
        return False

    u = driver.find_element(By.ID, "login-user-name"); u.clear(); u.send_keys(acc)
    p = driver.find_element(By.ID, "login-password"); p.clear(); p.send_keys(pw_)
    btn = driver.find_element(By.CSS_SELECTOR, "button.btn-primary[type=submit]")
    driver.execute_script("arguments[0].click();", btn)  # 直接 click 被覆蓋層攔截,用 JS click
    time.sleep(3)

    if driver.find_elements(By.ID, "login-user-name"):
        _stop_banner("登入後仍見帳密框(帳密錯或有 2FA/驗證碼)", "手動登入後重跑")
        _close_school_tab(driver)
        return False
    print("[post_web] ✓ 校網登入成功。")
    return True


# ── 發布單位 / 板名 helpers ───────────────────────────────────────────────
# 發布單位:掃所有 <select>,找 option 文字含目標單位者,設 selectedIndex + fire change。
_SELECT_UNIT_JS = r"""
var unit = (arguments[0] || '').trim();
var sels = document.querySelectorAll('select');
for (var i = 0; i < sels.length; i++) {
    var sel = sels[i];
    for (var j = 0; j < sel.options.length; j++) {
        if ((sel.options[j].text || '').trim().indexOf(unit) !== -1) {
            sel.selectedIndex = j;
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true, text: (sel.options[j].text || '').trim()};
        }
    }
}
var diag = [];
for (var k = 0; k < sels.length; k++) {
    var opts = [];
    for (var m = 0; m < sels[k].options.length; m++) {
        opts.push((sels[k].options[m].text || '').trim());
    }
    diag.push(opts);
}
return {ok: false, diag: diag};
"""


def _select_publish_unit(driver, unit):
    """選「發布單位」下拉 = unit(子字串比對 option 文字)。

    策略 1:原生 <select> — 掃所有 select 找 option 文字含 unit 者設定之。
    策略 2(fallback):自訂下拉 — 點開「發布單位」label 區塊的控制項,點文字 == unit 的選項。

    成功 → True;找不到 → 印診斷(各 select 的 options)回 False。
    """
    try:
        r = driver.execute_script(_SELECT_UNIT_JS, unit)
    except Exception as e:
        print(f"      [WARN] 選發布單位 JS 例外:{type(e).__name__}: {e}")
        r = None
    if r and r.get('ok'):
        print(f"      OK:發布單位已選「{r.get('text')}」")
        return True
    # fallback:自訂下拉(非原生 select)
    try:
        opened = driver.execute_script(r"""
            var lab = null;
            for (var e of document.querySelectorAll('*')) {
                if ((e.textContent || '').trim() === '發布單位') { lab = e; break; }
            }
            if (!lab) return false;
            var node = lab.parentElement;
            for (var d = 0; d < 4 && node; d++) {
                var ctrl = node.querySelector(
                    '[role=combobox], .dropdown-toggle, button, [tabindex]');
                if (ctrl) { ctrl.click(); return true; }
                node = node.parentElement;
            }
            return false;
        """)
        if opened:
            time.sleep(0.8)
            picked = driver.execute_script(r"""
                var unit = (arguments[0] || '').trim();
                for (var e of document.querySelectorAll('li,option,div,span,a')) {
                    if ((e.textContent || '').trim() === unit) { e.click(); return true; }
                }
                return false;
            """, unit)
            if picked:
                print(f"      OK:發布單位(自訂下拉)已選「{unit}」")
                return True
    except Exception as e:
        print(f"      [WARN] 發布單位 fallback 例外:{type(e).__name__}: {e}")
    print(f"      [ERROR] 找不到發布單位選項「{unit}」;"
          f"頁面 select options 診斷={(r or {}).get('diag')!r}")
    return False


def _read_board_name(driver, default="圖書館公告"):
    """從發佈頁實抓「公告於」的板名(模組標題,如『圖書館公告』)。

    使用者要求:此名稱依當時頁面實際狀況抓、不寫死。找不到 → 回 default 並印 WARN。
    策略:找文字以「告」結尾、含「公告」、長度 <= 8 的最小(葉子)元素。
    """
    try:
        name = driver.execute_script(r"""
            var els = document.querySelectorAll(
                'h1,h2,h3,h4,h5,h6,span,div,a,li,strong,b');
            for (var i = 0; i < els.length; i++) {
                var t = (els[i].textContent || '').trim();
                if (t.length === 0 || t.length > 8) continue;
                if (t.charAt(t.length - 1) !== '告') continue;
                if (t.indexOf('公告') === -1) continue;
                var childHit = false;
                var kids = els[i].querySelectorAll('*');
                for (var j = 0; j < kids.length; j++) {
                    var kt = (kids[j].textContent || '').trim();
                    if (kt.indexOf('公告') !== -1 && kt.length <= 8) { childHit = true; break; }
                }
                if (childHit) continue;
                return t;
            }
            return null;
        """)
        if name:
            print(f"      OK:抓到公告板名「{name}」")
            return name
    except Exception as e:
        print(f"      [WARN] 讀板名失敗:{type(e).__name__}: {e}")
    print(f"      [WARN] 抓不到板名,用預設「{default}」")
    return default


def _submit_announcement(driver, title, body, attachments=None,
                         sync_categories=None, publish_unit=None):
    """到 圖書館(/nss/s/main/p/library) 圖書館公告模組(先進「模組」編輯模式)點「新增公告」,
    填標題+內容+發布者、選發布單位、勾置頂、上傳附件、選同步分類,點「發布」。

    參數:
        attachments      附件檔案絕對路徑 list(公文 *ATTCH* 檔);None/空則不傳附件。
        sync_categories  「同步顯示至」要點選的分類清單(來自總結 #### 行,如
                         ['課外活動','硏習資訊']);對不到的略過、能選的照選。
        publish_unit     「發布單位」下拉要選的單位(env.env sssh_publish_unit);
                         找不到該選項 → STOP 回 False(不發、不寫標記)。
    完成(成功或失敗)都會關掉校網分頁、切回 edoc。
    成功 → 回 {'selected_cats': [...], 'board_name': '...'};失敗 → False。
    """
    from selenium.webdriver.common.by import By
    try:
        driver.get(ANNO_URL)
        time.sleep(2)

        # /library 的「圖書館公告」模組要先進「模組」編輯模式才會出現「新增公告」。
        # 若目前沒有「新增公告」鈕,點上方 admin bar 的「模組」切換編輯模式。
        has_add = driver.execute_script(
            "return [...document.querySelectorAll('button,a')]"
            ".some(b => (b.innerText||'').trim() === '新增公告');")
        if not has_add:
            driver.execute_script("""
                for (const e of document.querySelectorAll('a,button,span,i')) {
                    if ((e.innerText||'').trim() === '模組') { e.click(); return; }
                }""")
            time.sleep(2)

        # 輪詢等「新增公告」鈕出現(編輯模式 + 模組 async),最多 ~10s,點它
        clicked = False
        for _ in range(20):
            clicked = driver.execute_script("""
                for (const b of document.querySelectorAll('button,a')) {
                    if ((b.innerText||'').trim() === '新增公告') { b.click(); return true; }
                } return false;
            """)
            if clicked:
                break
            time.sleep(0.5)
        if not clicked:
            _stop_banner("找不到「新增公告」按鈕(圖書館公告模組/模組編輯模式未就緒)")
            return False
        time.sleep(2.5)

        # 標題(id 帶隨機後綴,前綴比對)
        title_el = driver.find_element(By.CSS_SELECTOR, '[id^="ct-title-"]')
        title_el.clear(); title_el.send_keys(title)

        # 內容:CKEditor 5。優先 instance API setData,fallback innerHTML。
        set_mode = driver.execute_script("""
            const root = document.querySelector('.ck-editor__editable.ck-content');
            if (!root) return 'none';
            if (root.ckeditorInstance) { root.ckeditorInstance.setData(arguments[0]); return 'api'; }
            root.focus(); root.innerHTML = arguments[0]; return 'innerHTML';
        """, _body_to_html(body))
        if set_mode == 'none':
            _stop_banner("找不到 CKEditor 內容區(.ck-content)")
            return False
        print(f"[post_web] 內容已填入(模式={set_mode})")

        # 發布者(ct-person,required,自動化開表單時為空)→ 從 env.env 讀 sssh_publisher 填入
        from taipeion_login_selenium import _read_config
        publisher = _read_config("sssh_publisher")
        if publisher:
            person = driver.find_elements(By.CSS_SELECTOR, '[id^="ct-person-"]')
            if person:
                person[0].clear(); person[0].send_keys(publisher)
                print(f"[post_web] 發布者已填:{publisher}")

        # 發布單位(下拉):選 env.env 的 sssh_publish_unit;找不到該選項 → STOP 不發。
        if publish_unit:
            if not _select_publish_unit(driver, publish_unit):
                _stop_banner(f"發布單位下拉找不到「{publish_unit}」",
                             "確認 env.env 的 sssh_publish_unit 與站上選項文字一致")
                return False

        # 勾「置頂」(使用者要求):勾後「置頂結束日期/時間」會自動帶預設值(+5天),不需另填。
        pinned = driver.execute_script("""
            const c = document.querySelector('[id^="ct-top-"]');
            if (c && !c.checked) { c.click(); return true; }
            return c ? c.checked : false;
        """)
        print(f"[post_web] 置頂={pinned}")
        time.sleep(0.5)

        # 附件:點「附件」→「新增檔案」,把 *ATTCH* 檔 send_keys 給 file input(multiple)。
        if attachments:
            driver.execute_script("""
                for (const b of document.querySelectorAll('button,a')) {
                    if ((b.innerText||'').trim() === '附件') { b.click(); return; }
                }""")
            time.sleep(0.8)
            driver.execute_script("""
                for (const b of document.querySelectorAll('button,a,li,div,span')) {
                    if ((b.innerText||'').trim() === '新增檔案') { b.click(); return; }
                }""")
            time.sleep(1)
            file_inputs = driver.find_elements(
                By.CSS_SELECTOR,
                'input[type=file].customfile, input[type=file][id^="attachUpload_"]')
            if file_inputs:
                file_inputs[0].send_keys("\n".join(attachments))
                print(f"[post_web] 已上傳附件 {len(attachments)} 個:"
                      f"{[os.path.basename(a) for a in attachments]}")
                time.sleep(2)
                # 填一列後表單會自動長出「空的 required file 列」,會擋發布 → 移除空列。
                removed = driver.execute_script("""
                    const empties = [...document.querySelectorAll('input[type=file][id^=attachUpload_]')]
                        .filter(f => f.files.length === 0);
                    let n = 0;
                    for (const f of empties) {
                        let node = f;
                        for (let i = 0; i < 6 && node; i++) {
                            const btn = node.querySelector && node.querySelector('.customfile-remove');
                            const ins = node.querySelectorAll &&
                                node.querySelectorAll('input[type=file][id^=attachUpload_]');
                            if (btn && ins && ins.length === 1) { btn.click(); n++; break; }
                            node = node.parentElement;
                        }
                    }
                    return n;""")
                print(f"[post_web] 移除空附件列 {removed} 個")
                time.sleep(1)
            else:
                print("[post_web] [WARN] 找不到附件 file input,跳過附件上傳")

        # 同步顯示至:依總結 #### 的分類清單,逐一點開「分類」下拉、點 最新消息-<分類>。
        # 異體字正規化(硏↔研);對不到的分類略過、能選的照選(使用者指定策略)。
        selected_cats = []
        for cat in (sync_categories or []):
            driver.execute_script("""
                for (const b of document.querySelectorAll('button,a,div,span')) {
                    if ((b.innerText||'').trim() === '分類') { b.click(); return; }
                }""")
            time.sleep(1.0)
            picked = driver.execute_script(r"""
                var want = arguments[0];   // 已正規化目標(如 研習資訊)
                function norm(s){ return (s||'').replace(/硏/g,'研').trim(); }
                for (var li of document.querySelectorAll('li')) {
                    if (norm(li.innerText).indexOf(want) !== -1) {
                        li.click(); return li.innerText.trim();
                    }
                }
                return null;""", _normalize_cat(cat))
            if picked:
                print(f"[post_web] 同步分類點選「{picked}」(對應 {cat})")
                selected_cats.append(cat)
            else:
                print(f"[post_web] [WARN] 同步分類找不到對應「{cat}」的選項,略過")
            time.sleep(0.5)

        # 「公告於」板名:發佈前從頁面實抓(模組標題,如「圖書館公告」),寫進已公告標記。
        board_name = _read_board_name(driver)

        print("!" * 60)
        print(f"[post_web][發佈] 即將對真實校網發佈公告:{title}")
        print("!" * 60)

        published = driver.execute_script("""
            for (const b of document.querySelectorAll('button')) {
                if ((b.innerText||'').trim() === '發布') { b.click(); return true; }
            } return false;
        """)
        if not published:
            _stop_banner("找不到「發布」按鈕")
            return False
        time.sleep(3)

        # 驗證:表單(標題框)消失 = 送出成功;仍在 = 必填欄未過/被擋
        if driver.find_elements(By.CSS_SELECTOR, '[id^="ct-title-"]'):
            _stop_banner("發布後表單仍在,可能必填欄未填或被擋", "實機檢查表單必填欄(發布單位/發布者/日期)")
            return False
        print(f"[post_web] ✓ 已送出發布:{title}")
        return {"selected_cats": selected_cats, "board_name": board_name}
    except Exception as e:
        _stop_banner(f"_submit_announcement 例外:{type(e).__name__}: {e}")
        return False
    finally:
        _close_school_tab(driver)


def maybe_post_announcement(driver, extract_dir):
    """結案存查歸檔後的發佈入口(掛在 document_closure 歸檔成功之後)。

    - 觸發判定不符(總結承辦文字不含「於官網公告」)→ 回 False(skipped,非錯誤)
    - 已有「已公告」標記 → 回 True(skip)
    - env.env 缺 sssh_publish_unit / 登入 / 發佈失敗 → 印 STOP banner 回 False
      (不 raise,絕不影響已完成的存查歸檔)
    - 成功 → 寫已公告標記(三行格式,含板名 + 實際選到的分類)、回 True
    """
    from taipeion_login_selenium import _read_config
    try:
        summary = _parse_summary(extract_dir)
        if not _should_post(summary):
            return False
        if _already_announced(extract_dir):
            print(f"[post_web] {_doc_no_of(extract_dir)} 已公告過(清冊或 *已公告.txt),跳過。")
            return True
        title, body = summary["title"], summary["body"]
        sync_categories = summary.get("sync_categories") or []
        attachments = _find_attachments(extract_dir)
        publish_unit = _read_config("sssh_publish_unit")
        if not publish_unit:
            _stop_banner("env.env 缺 sssh_publish_unit",
                         "在 env.env 填 sssh_publish_unit=系管師群組(或你的單位)")
            return False
        print("=" * 60)
        print("[post_web] 準備發佈公告 → 圖書館公告")
        print(f"[post_web]   標題: {title}")
        print(f"[post_web]   發布單位: {publish_unit}")
        print(f"[post_web]   同步分類(####): {sync_categories}")
        print(f"[post_web]   附件: {[os.path.basename(a) for a in attachments]}")
        print("=" * 60)
        if not _open_and_login_sssh(driver):
            return False  # 內層失敗已印具體 STOP banner
        result = _submit_announcement(driver, title, body, attachments,
                                      sync_categories, publish_unit)
        if not result:
            return False  # 內層失敗已印具體 STOP banner
        _write_posted_marker(extract_dir, title,
                             result["board_name"], result["selected_cats"])
        # 同步登錄持久 CSV 清冊:即使日後此公文夾被重新下載成全新目錄(per-folder 標記
        # 消失),清冊仍能擋下重複公告(2026-06-17 修:結案每次重下載 → 標記消失 → 重複公告)。
        _append_to_ledger(extract_dir, _doc_no_of(extract_dir), title,
                          result["board_name"], result["selected_cats"])
        print(f"[post_web] ✓ 公告已發佈:{title}")
        return True
    except Exception as e:
        _stop_banner(f"maybe_post_announcement 例外:{type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    import sys

    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

    from ime_utils import ensure_english_ime
    from taipeion_login_selenium import _setup_stdout_logging
    from fill_in_draft import _attach_existing_chrome

    ensure_english_ime()  # 起手式:把輸入法切回英文
    _setup_stdout_logging()

    if len(sys.argv) < 2:
        print("用法: python document_closure/document_closure_post_web.py <公文目錄>")
        sys.exit(1)
    extract_dir = sys.argv[1]
    driver = _attach_existing_chrome()
    if driver is None:
        sys.exit(1)
    ok = maybe_post_announcement(driver, extract_dir)
    sys.exit(0 if ok else 1)
