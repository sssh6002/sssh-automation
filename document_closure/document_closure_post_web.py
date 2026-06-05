"""
document_closure_post_web.py
結案存查下載後，若總結「承辦文字」含「於官網公告」，把公文發佈到松高校網
(www.sssh.tp.edu.tw)：以總結的「主旨」為標題、主旨下方的「條列摘要」為內容。

設計見 docs/superpowers/specs/2026-06-03-closure-post-web-design.md。

呼叫方式：
  1) 結案存查流程中自動串接（document_closure._process_one_pending_closure_doc）：
       maybe_post_announcement(driver, extract_dir)
  2) 單獨執行（只對指定的「一個」公文目錄）：
       C:\\Python314\\python.exe document_closure/document_closure_post_web.py <公文目錄>
"""

import glob
import html
import os
import re
import time
from datetime import datetime

# 校網首頁(判登入/登出狀態)與發佈板。
# 發佈板=圖書館的「公告文件」(NetworkCenterAnnoucement):library 本頁的「新增公告」
# 只在 NSS CMS 編輯模式才有,但 library 的「公告文件」連結就是這個 URL、直接導覽即有
# 「新增公告」鈕、可自動化(2026-06-06 實機驗證)。
HOME_URL = "https://www.sssh.tp.edu.tw/nss/p/index"
ANNO_URL = "https://www.sssh.tp.edu.tw/nss/s/main/p/NetworkCenterAnnoucement"

# 觸發關鍵字：總結「承辦文字」(## 行) 含此字串才發佈到校網。
ANNOUNCE_KEYWORD = "於官網公告"

# 主旨行：兼容有無 * 前綴、半/全形冒號、冒號前後空白。
_SUBJECT_RE = re.compile(r'^\*?\s*主旨\s*[:：]\s*(.+)$')
# 條列行：1. / 1、 / 1.（後接內容，可無空白）。
_BODY_ITEM_RE = re.compile(r'^\s*\d+\s*[.、]\s*.+$')


def _parse_summary_text(text):
    """解析總結.md 文字，回 {'handling', 'title', 'body'} 或 None。

    - handling（承辦文字）：第一個以 `##` 開頭的行（## 或 ### 皆以 ## 開頭，取最先出現的，
      即承辦文字那行），去掉開頭所有 # 與前後空白；無此行則為 None。
    - title（主旨）：第一個 `主旨[:：]` 行冒號後的文字。
    - body（條列摘要）：主旨行之後、檔尾之前，所有 `數字.`／`數字、` 開頭的條列行，
      原樣（strip 過）以換行串接。

    主旨或 body 任一缺 → 回 None（寧缺勿發殘缺公告）。
    """
    handling = None
    title = None
    body_lines = []
    subject_seen = False

    for raw in text.splitlines():
        line = raw.strip()
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
    return {"handling": handling, "title": title, "body": "\n".join(body_lines)}


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


def _posted_marker_path(extract_dir):
    """回 <公文主檔名>已公告.txt 完整路徑;找不到主檔名回 None。"""
    from document_closure.document_closure import _find_main_doc_basename  # 避免循環 import
    base = _find_main_doc_basename(extract_dir)
    if not base:
        return None
    return os.path.join(extract_dir, f"{base}已公告.txt")


def _already_posted(extract_dir):
    """extract_dir 是否已有 <主檔名>已公告.txt(已公告過)。"""
    p = _posted_marker_path(extract_dir)
    return bool(p) and os.path.isfile(p)


def _write_posted_marker(extract_dir):
    """寫 <主檔名>已公告.txt(內容 ISO8601_已公告)。成功回路徑,否則 None。"""
    p = _posted_marker_path(extract_dir)
    if not p:
        print("      [WARN] 找不到公文主檔名,無法寫已公告標記")
        return None
    content = datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + "_已公告"
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"      OK:已寫已公告標記檔 {os.path.basename(p)}(內容: {content!r})")
        return p
    except OSError as e:
        print(f"      [WARN] 寫已公告標記失敗:{type(e).__name__}: {e}")
        return None


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


def _submit_announcement(driver, title, body):
    """到 圖書館(/nss/s/main/p/library) 的圖書館公告模組點「新增公告」,
    填標題+內容,點「發布」。

    完成(成功或失敗)都會關掉校網分頁、切回 edoc。成功 → True。
    """
    from selenium.webdriver.common.by import By
    try:
        driver.get(ANNO_URL)
        time.sleep(2)

        # 輪詢等「新增公告」鈕(圖書館公告模組可能 async 載入),最多 ~10s
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
            _stop_banner("找不到「新增公告」按鈕(此帳號可能無權限,或圖書館公告模組未載入)")
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
        return True
    except Exception as e:
        _stop_banner(f"_submit_announcement 例外:{type(e).__name__}: {e}")
        return False
    finally:
        _close_school_tab(driver)


def maybe_post_announcement(driver, extract_dir):
    """結案存查歸檔後的發佈入口(掛在 document_closure 歸檔成功之後)。

    - 觸發判定不符(總結承辦文字不含「於官網公告」)→ 回 False(skipped,非錯誤)
    - 已有「已公告」標記 → 回 True(skip)
    - 登入或發佈失敗 → 印 STOP banner 回 False(不 raise,絕不影響已完成的存查歸檔)
    - 成功 → 寫已公告標記、回 True
    """
    try:
        summary = _parse_summary(extract_dir)
        if not _should_post(summary):
            return False
        if _already_posted(extract_dir):
            print(f"[post_web] {extract_dir} 已有已公告標記,跳過。")
            return True
        title, body = summary["title"], summary["body"]
        print("=" * 60)
        print("[post_web] 準備發佈公告 → 圖書館/公告文件")
        print(f"[post_web]   標題: {title}")
        print("=" * 60)
        if not _open_and_login_sssh(driver):
            _stop_banner("校網登入失敗,不發佈")
            return False
        if not _submit_announcement(driver, title, body):
            _stop_banner("公告發佈失敗")
            return False
        _write_posted_marker(extract_dir)
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
