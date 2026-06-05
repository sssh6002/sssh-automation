# 上網公告（closure-post-web 發佈段）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 結案存查歸檔後，若總結承辦文字含「於官網公告」，自動登入松高校網、把公文發佈到「圖書館 → 公告文件」板。

**Architecture:** 在既有 `document_closure/document_closure_post_web.py`（第一階段已完成解析＋觸發判定）補上：防重複標記、HTML 轉換、登入、發佈、orchestrator，並在 `document_closure._process_one_pending_closure_doc` 歸檔成功後掛 `maybe_post_announcement`。純邏輯走 TDD；Selenium DOM 互動走實機驗證（專案規則：selector 一定實機跑過）。發佈為不可逆外部動作，第一篇在使用者監看下實測。

**Tech Stack:** Python 3.14、Selenium（attach 既有 Chrome :9222）、pytest、CKEditor 5 DOM。

設計依據：`docs/superpowers/specs/2026-06-03-closure-post-web-design.md`

---

## File Structure

- **Modify** `document_closure/document_closure_post_web.py` — 新增 `_body_to_html`、`_posted_marker_path`/`_already_posted`/`_write_posted_marker`、`_stop_banner`、`_open_and_login_sssh`、`_close_school_tab`、`_submit_announcement`、`maybe_post_announcement`、`__main__`。
- **Modify** `document_closure/document_closure.py:1556` — 歸檔成功後呼叫 `maybe_post_announcement`（function-level import 避免循環）。
- **Test** `tests/test_closure_post_web.py` — 加 `_body_to_html`、marker、orchestrator 控制流測試（既有 12 純函式測試保留）。

關鍵事實（實機已驗證 2026-06-05）：
- 登入（使用者指定）：從首頁 `https://www.sssh.tp.edu.tw/nss/p/index` 看右上角，「登出」=已登入、「登入」=點它進 `/passport/tpeEntrance` 登入頁（`#login-user-name`/`#login-password`/`button.btn-primary[type=submit]`，**JS click**）。
- 公告板（使用者指定）：`https://www.sssh.tp.edu.tw/nss/s/main/p/library` 的圖書館公告模組，登入後出現「新增公告」鈕 → CKEditor 5 表單。
- 表單欄位 id 帶**隨機後綴** → 一律前綴比對 `[id^="ct-title-"]`；內容區 `.ck-editor__editable.ck-content`（contenteditable，非 iframe）；送出鈕文字「發布」。
- 帳密讀 `taipeion_login_selenium._read_config("sssh_account" / "sssh_password")`，已確認 env.env 填好。

---

## Task 1: `_body_to_html` 把條列摘要轉 CKEditor HTML

**Files:**
- Modify: `document_closure/document_closure_post_web.py`
- Test: `tests/test_closure_post_web.py`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_closure_post_web.py` 結尾加：

```python
from document_closure.document_closure_post_web import _body_to_html


def test_body_to_html_one_paragraph_per_line():
    assert _body_to_html("1. 甲\n2. 乙") == "<p>1. 甲</p><p>2. 乙</p>"


def test_body_to_html_skips_blank_lines():
    assert _body_to_html("1. 甲\n\n2. 乙\n") == "<p>1. 甲</p><p>2. 乙</p>"


def test_body_to_html_escapes_html_chars():
    assert _body_to_html("a < b & c") == "<p>a &lt; b &amp; c</p>"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `C:/Python314/python.exe -m pytest tests/test_closure_post_web.py::test_body_to_html_one_paragraph_per_line -v`
Expected: FAIL（`ImportError: cannot import name '_body_to_html'`）

- [ ] **Step 3: 最小實作**

在 `document_closure/document_closure_post_web.py` import 區（`import re` 之後）加 `import html`，並在 `_should_post` 之後加：

```python
def _body_to_html(body):
    """把條列摘要(每行一條)轉成 CKEditor 可吃的 HTML 段落,逐行一個 <p>。

    跳過空行;對每行做 HTML escape,避免內容含 < & 破版。
    """
    lines = [ln for ln in body.split("\n") if ln.strip()]
    return "".join(f"<p>{html.escape(ln)}</p>" for ln in lines)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `C:/Python314/python.exe -m pytest tests/test_closure_post_web.py -k body_to_html -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add document_closure/document_closure_post_web.py tests/test_closure_post_web.py
git commit -m "post-web: _body_to_html 把條列摘要轉 CKEditor HTML 段落(TDD)"
```

---

## Task 2: 防重複標記 `_posted_marker_path` / `_already_posted` / `_write_posted_marker`

**Files:**
- Modify: `document_closure/document_closure_post_web.py`
- Test: `tests/test_closure_post_web.py`

標記檔比照既有 `已存查.txt`：`<公文主檔名>已公告.txt`，內容 `ISO8601_已公告`。主檔名用 `document_closure._find_main_doc_basename`（function-level import 避免循環）。

- [ ] **Step 1: 寫失敗測試**

加到 `tests/test_closure_post_web.py`：

```python
import document_closure.document_closure_post_web as pw


def _make_doc_dir(tmp_path, base="12345_678"):
    d = tmp_path / "MWAA_x"
    d.mkdir()
    (d / f"{base}內容.txt").write_text("x", encoding="utf-8")  # 供 _find_main_doc_basename
    return d, base


def test_posted_marker_path_uses_main_basename(tmp_path):
    d, base = _make_doc_dir(tmp_path)
    assert pw._posted_marker_path(str(d)).endswith(f"{base}已公告.txt")


def test_posted_marker_path_none_when_no_main_doc(tmp_path):
    d = tmp_path / "empty"; d.mkdir()
    assert pw._posted_marker_path(str(d)) is None


def test_write_and_detect_posted_marker(tmp_path):
    d, base = _make_doc_dir(tmp_path)
    assert pw._already_posted(str(d)) is False
    p = pw._write_posted_marker(str(d))
    assert p is not None and (d / f"{base}已公告.txt").is_file()
    assert (d / f"{base}已公告.txt").read_text(encoding="utf-8").endswith("_已公告")
    assert pw._already_posted(str(d)) is True
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `C:/Python314/python.exe -m pytest tests/test_closure_post_web.py -k posted_marker -v`
Expected: FAIL（`AttributeError: ... has no attribute '_posted_marker_path'`）

- [ ] **Step 3: 最小實作**

import 區頂端加 `from datetime import datetime`。在 `_body_to_html` 之後加：

```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `C:/Python314/python.exe -m pytest tests/test_closure_post_web.py -k posted_marker -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add document_closure/document_closure_post_web.py tests/test_closure_post_web.py
git commit -m "post-web: 防重複標記 <主檔名>已公告.txt(TDD,比照已存查.txt)"
```

---

## Task 3: orchestrator `maybe_post_announcement` + `_stop_banner` + 登入/發佈 stub

**Files:**
- Modify: `document_closure/document_closure_post_web.py`
- Test: `tests/test_closure_post_web.py`

orchestrator 不直接碰 driver（只把 driver 傳給 login/submit），故控制流可用 monkeypatch login/submit 測試。先放 `_open_and_login_sssh`/`_submit_announcement` stub（raise NotImplementedError），Task 4/5 再換實作。

- [ ] **Step 1: 寫失敗測試**

加到 `tests/test_closure_post_web.py`（沿用 Task 2 的 `_make_doc_dir`、檔頭的 `NEW_FORMAT`/`OLD_FORMAT`）：

```python
def _doc_dir_with_summary(tmp_path, summary_text, base="12345_678"):
    d = tmp_path / "MWAA_s"; d.mkdir()
    (d / f"{base}總結.gemini.md").write_text(summary_text, encoding="utf-8")
    (d / f"{base}內容.txt").write_text("x", encoding="utf-8")
    return d, base


def test_maybe_post_skips_when_not_triggered(tmp_path, monkeypatch):
    d, _ = _doc_dir_with_summary(tmp_path, OLD_FORMAT)  # 承辦文字「不參加」
    called = {"login": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: called.__setitem__("login", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is False
    assert called["login"] is False  # 沒觸發 → 連登入都不做


def test_maybe_post_publishes_when_triggered(tmp_path, monkeypatch):
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)  # 含「於官網公告」
    rec = {}
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: rec.__setitem__("login", True) or True)
    monkeypatch.setattr(pw, "_submit_announcement",
                        lambda drv, t, b: rec.update(title=t, body=b) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is True
    assert rec["login"] is True
    assert rec["title"].startswith("請貴校加強")
    assert rec["body"].startswith("1. 近期發現")
    assert (d / f"{base}已公告.txt").is_file()  # 成功後寫標記


def test_maybe_post_skips_when_already_posted(tmp_path, monkeypatch):
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    (d / f"{base}已公告.txt").write_text("2026-06-05T00:00:00_已公告", encoding="utf-8")
    called = {"login": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: called.__setitem__("login", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is True
    assert called["login"] is False


def test_maybe_post_returns_false_and_no_marker_when_submit_fails(tmp_path, monkeypatch):
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: True)
    monkeypatch.setattr(pw, "_submit_announcement", lambda drv, t, b: False)
    assert pw.maybe_post_announcement(object(), str(d)) is False
    assert not (d / f"{base}已公告.txt").exists()


def test_maybe_post_returns_false_when_login_fails(tmp_path, monkeypatch):
    d, base = _doc_dir_with_summary(tmp_path, NEW_FORMAT)
    submit_called = {"v": False}
    monkeypatch.setattr(pw, "_open_and_login_sssh", lambda drv: False)
    monkeypatch.setattr(pw, "_submit_announcement",
                        lambda drv, t, b: submit_called.__setitem__("v", True) or True)
    assert pw.maybe_post_announcement(object(), str(d)) is False
    assert submit_called["v"] is False
    assert not (d / f"{base}已公告.txt").exists()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `C:/Python314/python.exe -m pytest tests/test_closure_post_web.py -k maybe_post -v`
Expected: FAIL（`AttributeError: ... '_open_and_login_sssh'` 或 `maybe_post_announcement`）

- [ ] **Step 3: 最小實作**

在 `_write_posted_marker` 之後加（stub + banner + orchestrator）：

```python
def _stop_banner(reason, hint=""):
    """印 STOP banner(發佈段失敗時用,讓 run.log 醒目)。"""
    print("\n" + "!" * 60)
    print(f"[post_web][STOP] {reason}")
    if hint:
        print(f"[post_web][STOP] 建議:{hint}")
    print("!" * 60 + "\n")


def _open_and_login_sssh(driver):
    raise NotImplementedError  # Task 4 實作


def _submit_announcement(driver, title, body):
    raise NotImplementedError  # Task 5 實作


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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `C:/Python314/python.exe -m pytest tests/test_closure_post_web.py -v`
Expected: 全部 passed（含既有 12 + Task1/2/3 新增）

- [ ] **Step 5: Commit**

```bash
git add document_closure/document_closure_post_web.py tests/test_closure_post_web.py
git commit -m "post-web: maybe_post_announcement orchestrator + STOP banner(TDD,登入/發佈先 stub)"
```

---

## Task 4: `_open_and_login_sssh` 帳密登入（實作 + 實機驗證）

**Files:**
- Modify: `document_closure/document_closure_post_web.py`

Selenium DOM 互動，不寫 mock 單元測試（會變成測 mock）；依實機 selector 實作後**實機跑驗證**。

- [ ] **Step 1: 實作（取代 Task 3 的 stub）**

把 `_open_and_login_sssh` stub 換成：

```python
HOME_URL = "https://www.sssh.tp.edu.tw/nss/p/index"


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
```

需要 `import time` 在 import 區（若尚無則加）。

- [ ] **Step 2: import smoke test**

Run: `C:/Python314/python.exe -c "import document_closure.document_closure_post_web as p; print('OK', callable(p._open_and_login_sssh))"`
Expected: `OK True`

- [ ] **Step 3: 實機驗證登入（前提：Chrome :9222 在跑，先登出校網）**

寫暫存 `_t_login.py`：

```python
import sys; sys.path.insert(0, ".")
from fill_in_draft import _attach_existing_chrome
import document_closure.document_closure_post_web as p
d = _attach_existing_chrome()
print("login ->", p._open_and_login_sssh(d))
print("URL after:", d.current_url)
```

Run: `C:/Python314/python.exe _t_login.py`
Expected: `login -> True`，URL 不在 `/passport`（已登入）。觀察 Chrome 校網右上角變「登出」。

- [ ] **Step 4: 清掉暫存腳本**

```bash
rm -f _t_login.py
```

- [ ] **Step 5: Commit**

```bash
git add document_closure/document_closure_post_web.py
git commit -m "post-web: _open_and_login_sssh 帳密登入(實機驗證,JS click 送出+分頁管理)"
```

---

## Task 5: `_submit_announcement` 填表發佈（實作 + 監看下實測）

**Files:**
- Modify: `document_closure/document_closure_post_web.py`

**⚠️「發布」是對真實校網的不可逆動作 — 第一篇務必在使用者監看下實測。**

- [ ] **Step 1: 實作（取代 Task 3 的 stub）**

把 `_submit_announcement` stub 換成：

```python
ANNO_URL = "https://www.sssh.tp.edu.tw/nss/s/main/p/library"


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
```

- [ ] **Step 2: import smoke test**

Run: `C:/Python314/python.exe -c "import document_closure.document_closure_post_web as p; print('OK', callable(p._submit_announcement))"`
Expected: `OK True`

- [ ] **Step 3: 監看下實測一篇（使用者在場）**

寫暫存 `_t_submit.py`（用一個真實的結案目錄；先請使用者確認可發佈一篇測試）：

```python
import sys; sys.path.insert(0, ".")
from fill_in_draft import _attach_existing_chrome
import document_closure.document_closure_post_web as p
d = _attach_existing_chrome()
assert p._open_and_login_sssh(d)
title = "（自動化測試）資安宣導公告"
body = "1. 這是自動化發佈測試第一行。\n2. 第二行。"
print("submit ->", p._submit_announcement(d, title, body))
```

Run（**使用者監看**）: `C:/Python314/python.exe _t_submit.py`
Expected: `submit -> True`；到 `NetworkCenterAnnoucement` 頁確認新公告出現、標題/內容正確。若必填欄擋住，依 STOP 提示在實機看哪欄、補對應 `[id^="ct-..."]` 填值後重測。

- [ ] **Step 4: 清掉暫存腳本 + 視需要在校網把測試公告下架**

```bash
rm -f _t_submit.py
```
（測試公告若不要保留，請於校網手動刪除/下架。）

- [ ] **Step 5: Commit**

```bash
git add document_closure/document_closure_post_web.py
git commit -m "post-web: _submit_announcement 填 CKEditor+發布(監看下實機驗證一篇)"
```

---

## Task 6: 串入結案流程 + standalone `__main__`（實機端到端）

**Files:**
- Modify: `document_closure/document_closure.py:1556`
- Modify: `document_closure/document_closure_post_web.py`（加 `__main__`）

- [ ] **Step 1: 掛入 `_process_one_pending_closure_doc`**

在 `document_closure/document_closure.py` 將：

```python
    marker_path = _write_archive_marker(closure_target)
    if not marker_path:
        print("[WARN] 寫標記檔失敗,但存查已成功(歸檔不受影響)")

    print(f"[document_closure] ✓ 已完成存查(公文 {doc_no} 歸檔到檔號 {archived_category})")
```

改為（在兩段之間插入發佈呼叫）：

```python
    marker_path = _write_archive_marker(closure_target)
    if not marker_path:
        print("[WARN] 寫標記檔失敗,但存查已成功(歸檔不受影響)")

    # 歸檔成功後:若總結承辦文字含「於官網公告」→ 發佈到校網圖書館/公告文件。
    # 此處公文必為「如擬」(前面 _has_approval_text 已過閘),滿足「等如擬再公告」。
    # maybe_post_announcement 不 raise、失敗只印 STOP,不影響已完成的歸檔。
    from document_closure.document_closure_post_web import maybe_post_announcement
    maybe_post_announcement(driver, closure_target)

    print(f"[document_closure] ✓ 已完成存查(公文 {doc_no} 歸檔到檔號 {archived_category})")
```

- [ ] **Step 2: 加 standalone `__main__`**

在 `document_closure/document_closure_post_web.py` 結尾加：

```python
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
```

- [ ] **Step 3: 全測試 + import smoke**

Run: `C:/Python314/python.exe -m pytest -q`
Expected: 全 passed（含既有全部 + post-web 新增）。

Run: `C:/Python314/python.exe -c "import document_closure.document_closure as c; print('hook import OK')"`
Expected: `hook import OK`（確認循環 import 沒問題）。

- [ ] **Step 4: 實機端到端（standalone，使用者監看）**

挑一個含「於官網公告」總結、且尚未公告的結案目錄 `<dir>`（`document_download_closure/<doc_no>/`）：

Run（**使用者監看**）: `C:/Python314/python.exe document_closure/document_closure_post_web.py <dir>`
Expected: 登入 → 發佈 → 寫 `<主檔名>已公告.txt`，exit 0。再跑一次同目錄 → 印「已有已公告標記,跳過」、exit 0（防重複生效）。

- [ ] **Step 5: Commit**

```bash
git add document_closure/document_closure.py document_closure/document_closure_post_web.py
git commit -m "post-web: 串入結案流程(歸檔後發佈)+ standalone 入口(實機端到端驗證)"
```

---

## Self-Review（對照 spec）

- **觸發鏈/如擬**：Task 6 掛在 `_write_archive_marker` 後、`return True` 前，位於 `_has_approval_text`「如擬」閘門之後 → 符合 spec §觸發鏈。✓
- **解析(§2)**：第一階段已完成（`_parse_summary*`/`_should_post`，12 測試綠），本計畫不重做。✓
- **登入(§3b)**：Task 4 用 `#login-user-name`/`#login-password`/`button.btn-primary[type=submit]`(JS click)、已登入偵測、失敗 STOP。✓
- **發佈(§3c)**：Task 5 用 NetworkCenterAnnoucement「新增公告」、`[id^="ct-title-"]`、CKEditor `.ck-content`、點「發布」、表單消失驗證、送出前 banner、監看下實測。✓
- **防重複/log(§4)**：Task 2 `<主檔名>已公告.txt`；輸出走既有 run.log；Task 3 orchestrator dedup。✓
- **不做(YAGNI)**：無批次 scan、無智慧分類、無附件 — 計畫未越界。✓
- **型別一致**：`maybe_post_announcement(driver, extract_dir)`、`_open_and_login_sssh(driver)→bool`、`_submit_announcement(driver, title, body)→bool`、`_body_to_html(body)→str`、`_posted_marker_path/_already_posted/_write_posted_marker(extract_dir)` 全程一致。✓
- **placeholder 掃描**：無 TBD/TODO；每個 code step 都有完整程式碼。✓
