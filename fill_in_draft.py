"""4-2:承辦中公文擬寫承辦文字。

依 docs/superpowers/specs/2026-05-27-fill-in-draft-design.md。
讀 summarize_doc 產出的總結檔取標記 → 查 fill_in_draft.yaml 對應表得
「承辦文字 + 動作」→ 於公文閱覽器分頁填字、儲存、依動作決定不動作/陳會。
"""

import pathlib
import re

import time

import yaml
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

_BASE_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = _BASE_DIR / "fill_in_draft.yaml"
DOWNLOAD_DIR = _BASE_DIR / "document_download"

# 公文閱覽器分頁的 URL 特徵(實測 2026-05-31):
#   https://edoc.gov.taipei/tcqb/oa/index.html?app=editor&doSno=<10碼>&...
_VIEWER_URL_PREFIX = "https://edoc.gov.taipei/tcqb/oa/index.html?app=editor"
_DOSNO_RE = re.compile(r"[?&]doSno=(\d+)")


_DEFAULT_TEMPLATE = "擬:\n<承辦文字>陳閱後文存查。"


def _read_action(extract_dir):
    """從 *總結*.md 讀「承辦文字」+「動作」。回 (fragment_or_None, action_or_None)。

    依 summarize_doc.md spec,總結檔頂端格式:
      第 1 行 `#存查分類:<類別> <檔號>`  (單 `#`)
      第 2 行 `## <承辦文字>`              (雙 `#`)
      第 3 行 `### <動作/承辦方式>`        (三 `#`)

    本函式逐行掃描,找以 `##` 開頭的行(`##` / `###` 都算),由上而下取前兩個:
      第 1 個命中 → 「承辦文字」(strip 開頭所有 # 與空白)
      第 2 個命中 → 「動作」
    任一欄不存在或空字串 → 該欄回 None,由 caller 套 default。

    找不到總結檔 → 回 (None, None)。
    """
    extract_dir = pathlib.Path(extract_dir)
    summaries = sorted(extract_dir.glob("*總結*.md"))
    if not summaries:
        return None, None
    hash_lines = []
    for raw in summaries[0].read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("##"):
            content = line.lstrip("#").strip()
            hash_lines.append(content or None)
            if len(hash_lines) >= 2:
                break
    fragment = hash_lines[0] if len(hash_lines) >= 1 else None
    action = hash_lines[1] if len(hash_lines) >= 2 else None
    return fragment, action


def _load_config(config_path=CONFIG_PATH):
    """讀 yaml 設定,回 (default, template)。

    default:dict(承辦文字/動作) — 當 summary 第二/三行缺欄時的 fallback。
    template:str — 含 `<承辦文字>` placeholder。
    舊架構的 rules 已棄用(承辦文字+動作改成由 *總結*.md 直接決定)。
    """
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    default = cfg.get("default") or {"承辦文字": "", "動作": "none"}
    template = cfg.get("template") or _DEFAULT_TEMPLATE
    return default, template


def _render(template, fragment):
    """套用模板:把 `<承辦文字>` placeholder 換成 fragment。"""
    return template.replace("<承辦文字>", fragment)


# 公文閱覽器 ExtJS 元素選擇器(2026-05-31 實機 dump 鎖定):
#   - 「我的意見」textarea:textarea.x-input-el.x-form-field — 同層有兩個,
#     一個 visible(我的意見,~268x150),一個 hidden(0x0 模板)。取 visible 的。
#   - 動作鈕(決行/陳會/退回/清稿/變更流程):div.x-button 內含
#     span.x-button-label 文字命中。ExtJS 用 div 模擬 button,點 div 即可。
#   - id 不能寫死(每次載入 ext-button-XX/ext-element-XX 編號會變)。
_TEXTAREA_CSS = "textarea.x-input-el.x-form-field"
_BUTTON_BY_LABEL_XPATH = (
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' x-button ')]"
    "[.//span[contains(@class,'x-button-label') and normalize-space(.)='{label}']]"
)
# 左上 toolbar 磁碟片儲存鈕:.x-button 內含 .x-button-icon.fa-floppy-o (Font Awesome)。
_SAVE_BUTTON_XPATH = (
    "//div[contains(concat(' ', normalize-space(@class), ' '), ' x-button ')]"
    "[.//span[contains(@class,'fa-floppy-o')]]"
)


def _find_visible_textarea(driver):
    """找「我的意見」textarea:同 css 有 visible + hidden 兩個,取第一個 visible 的。"""
    for ta in driver.find_elements(By.CSS_SELECTOR, _TEXTAREA_CSS):
        try:
            if ta.is_displayed() and ta.size["width"] > 0 and ta.size["height"] > 0:
                return ta
        except Exception:
            continue
    return None


def _fill_text(driver, text):
    """在公文閱覽器「我的意見」textarea 填入 text,並讀回驗證。回 True/False。"""
    try:
        # 確保在主 frame(dump 確認 textarea 在主 frame,不在 iframe 內)
        driver.switch_to.default_content()
        ta = WebDriverWait(driver, 15).until(lambda d: _find_visible_textarea(d))
        ta.click()
        ta.clear()
        ta.send_keys(text)
        actual = driver.execute_script("return arguments[0].value;", ta) or ""
        if actual != text:
            print(f"[fill_in_draft] _fill_text 寫入後驗證失敗:期望={text!r},實際={actual!r}")
            return False
        return True
    except Exception as e:
        print(f"[fill_in_draft] _fill_text 失敗:{type(e).__name__}: {e}")
        return False


def _save(driver):
    """點左上 toolbar 磁碟片儲存鈕(.x-button 內含 .fa-floppy-o icon)。回 True/False。

    填完承辦文字後一定要按儲存,否則 textarea 內容只在前端 viewmodel,
    重新整理 / 切分頁就會掉。
    """
    try:
        driver.switch_to.default_content()
        btn = WebDriverWait(driver, 15).until(
            lambda d: next((b for b in d.find_elements(By.XPATH, _SAVE_BUTTON_XPATH)
                            if b.is_displayed()), False)
        )
        btn.click()
        return True
    except Exception as e:
        print(f"[fill_in_draft] _save 失敗:{type(e).__name__}: {e}")
        return False


def _click_chen_hui(driver, timeout=15):
    """點「陳會」鈕(div.x-button 內含 span.x-button-label='陳會')。回 True/False。

    陳會緊接在 _save 後點 — 儲存會觸發 ExtJS 重繪(gbDocflowId 重新分配),
    若 cached element ref 在重繪後 click,會拋 StaleElementReferenceException。
    這裡每輪都 fresh find + click,catch stale 後重試,直到 timeout 才放棄。
    """
    try:
        driver.switch_to.default_content()
        xp = _BUTTON_BY_LABEL_XPATH.format(label="陳會")
        deadline = time.monotonic() + timeout
        last_err = None
        while time.monotonic() < deadline:
            try:
                btn = next((b for b in driver.find_elements(By.XPATH, xp)
                            if b.is_displayed()), None)
                if btn is None:
                    time.sleep(0.3)
                    continue
                btn.click()
                return True
            except StaleElementReferenceException as e:
                last_err = e
                time.sleep(0.2)
        print(f"[fill_in_draft] _click_chen_hui 超時({timeout}s),最後例外:{last_err!r}")
        return False
    except Exception as e:
        print(f"[fill_in_draft] _click_chen_hui 失敗:{type(e).__name__}: {e}")
        return False


def _dump_candidates(driver, label="root"):
    """印出當前 frame 內可能的承辦文字輸入框/按鈕候選,供實機鎖定選擇器。

    篩選條件:textarea/input/button/[role=button]/.x-button,以及含關鍵字
    (如擬、決行、陳會、退回、清稿、儲存、清除意見、還原意見) 的元素。
    """
    try:
        rows = driver.execute_script(
            """
            const out = [];
            const sel = 'textarea, input, button, [role=button], [class*=button], [class*=btn]';
            const KW = ['如擬','決行','陳會','退回','清稿','變更流程','儲存',
                        '清除意見','還原意見','意見彙整','我的意見','承辦','會辦','核決'];
            document.querySelectorAll(sel).forEach(el => {
                const text = (el.innerText || el.value || el.placeholder || '').trim();
                const aria = el.getAttribute('aria-label') || '';
                const title = el.getAttribute('title') || '';
                const blob = text + ' ' + aria + ' ' + title;
                const hit = KW.some(k => blob.indexOf(k) >= 0);
                if (hit || el.tagName === 'TEXTAREA') {
                    const rect = el.getBoundingClientRect();
                    out.push({
                        tag: el.tagName,
                        id: el.id || '',
                        cls: (el.className || '').toString().slice(0, 120),
                        text: text.slice(0, 40),
                        ph:   (el.placeholder || '').slice(0, 40),
                        aria: aria.slice(0, 40),
                        title: title.slice(0, 40),
                        x: Math.round(rect.x), y: Math.round(rect.y),
                        w: Math.round(rect.width), h: Math.round(rect.height),
                        vis: rect.width > 0 && rect.height > 0,
                    });
                }
            });
            return out;
            """) or []
        print(f"[fill_in_draft] _dump_candidates({label}) — {len(rows)} 個候選:")
        for r in rows:
            print(f"    <{r['tag']}> id={r['id']!r} cls={r['cls']!r}")
            print(f"        text={r['text']!r} ph={r['ph']!r} "
                  f"aria={r['aria']!r} title={r['title']!r}")
            print(f"        rect=({r['x']},{r['y']},{r['w']}x{r['h']}) vis={r['vis']}")
    except Exception as e:
        print(f"[fill_in_draft] _dump_candidates({label}) 失敗:{type(e).__name__}: {e}")


def _dump_all_frames(driver):
    """對主 frame + 每個 iframe 各跑一次 _dump_candidates。"""
    print(f"[fill_in_draft] === dump 主 frame ===")
    driver.switch_to.default_content()
    _dump_candidates(driver, label="main")
    try:
        frames = driver.find_elements("css selector", "iframe, frame")
    except Exception as e:
        print(f"[fill_in_draft] 找 iframe 失敗:{type(e).__name__}: {e}")
        return
    print(f"[fill_in_draft] 主 frame 內共 {len(frames)} 個 iframe")
    for i, fr in enumerate(frames):
        try:
            fr_id = fr.get_attribute("id") or ""
            fr_src = (fr.get_attribute("src") or "")[:80]
            fr_cls = (fr.get_attribute("class") or "")[:60]
        except Exception:
            fr_id = fr_src = fr_cls = "?"
        print(f"[fill_in_draft] === dump iframe[{i}] id={fr_id!r} cls={fr_cls!r} src={fr_src!r} ===")
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
            _dump_candidates(driver, label=f"iframe[{i}]:{fr_id or fr_cls or i}")
        except Exception as e:
            print(f"[fill_in_draft] 切 iframe[{i}] 失敗:{type(e).__name__}: {e}")
        finally:
            driver.switch_to.default_content()


def _standalone_dump():
    """standalone dump 模式:attach Chrome → 找閱覽器分頁 → 對所有 frame dump 候選。"""
    driver = _attach_existing_chrome()
    if driver is None:
        return False
    handle, doSno = _find_viewer_window(driver)
    if handle is None:
        return False
    _dump_all_frames(driver)
    return True


def fill_in_draft(driver, extract_dir, config_path=CONFIG_PATH):
    """4-2 進入點:讀標記→查表→填承辦文字→儲存→依動作不動作/陳會。

    全程不 raise:任何例外都記 log 並回 False,不影響 4-1 已完成的下載/總結。
    """
    try:
        fragment, action = _read_action(extract_dir)
        default, template = _load_config(config_path)
        if fragment is None:
            fragment = default.get("承辦文字", "")
        if action is None:
            action = default.get("動作", "none")
        text = _render(template, fragment)
        print(f"[fill_in_draft] 承辦文字={fragment!r} 動作={action!r} → text={text!r}")

        if not _fill_text(driver, text):
            print("[fill_in_draft] 填承辦文字失敗,中止(不儲存、不動作)。")
            return False
        if not _save(driver):
            print("[fill_in_draft] 儲存失敗,中止(不動作)。")
            return False

        if action == "陳會":
            if not _click_chen_hui(driver):
                print("[fill_in_draft] 陳會失敗;狀態停在『已儲存未送』,可人工接手。")
                return False
        elif action == "none":
            pass
        else:
            print(f"[fill_in_draft] 動作 {action!r} 目前未實作,僅儲存不執行後續。")
        return True
    except Exception as e:
        print(f"[fill_in_draft] 例外(不影響 4-1):{type(e).__name__}: {e}")
        return False


def _attach_existing_chrome(debugger_address="127.0.0.1:9222"):
    """Attach 既有 Chrome session(taipeion_login_selenium 啟動時開的 :9222)。

    回 driver 或 None。前提:Chrome 啟動時帶 --remote-debugging-port=9222
    (已寫進 taipeion_login_selenium.py)。沒開 port 會 attach 失敗,提示重跑 main.py。
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.add_experimental_option("debuggerAddress", debugger_address)
        return webdriver.Chrome(options=opts)
    except Exception as e:
        print(f"[fill_in_draft] attach 既有 Chrome 失敗:{type(e).__name__}: {e}")
        print(f"[fill_in_draft] 確認:1) Chrome 正在跑 2) 啟動時帶 "
              f"--remote-debugging-port=9222(新版 taipeion_login_selenium 已預設加)")
        print("[fill_in_draft] 修補方式:跑 `python main.py 3` 重登 Chrome,新 session 會帶 port。")
        return None


def _find_viewer_window(driver):
    """在現有 window_handles 中找公文閱覽器分頁,switch 過去並回 (handle, doSno) 或 (None, None)。

    識別:URL 開頭 `https://edoc.gov.taipei/tcqb/oa/index.html?app=editor` + 含 doSno=。
    若有多個閱覽器分頁,取第一個。
    """
    try:
        handles = driver.window_handles
    except Exception as e:
        print(f"[fill_in_draft] 讀 window_handles 失敗:{type(e).__name__}: {e}")
        return None, None
    for h in handles:
        try:
            driver.switch_to.window(h)
            url = driver.current_url or ""
        except Exception as e:
            print(f"[fill_in_draft] switch {h} 失敗:{type(e).__name__}: {e}")
            continue
        if url.startswith(_VIEWER_URL_PREFIX):
            m = _DOSNO_RE.search(url)
            if m:
                doSno = m.group(1)
                print(f"[fill_in_draft] 找到公文閱覽器分頁,doSno={doSno},URL={url}")
                return h, doSno
            print(f"[fill_in_draft] URL 是閱覽器但找不到 doSno:{url}")
    print(f"[fill_in_draft] 沒找到公文閱覽器分頁(共 {len(handles)} 個 window)。")
    return None, None


def _resolve_extract_dir(doSno, download_dir=DOWNLOAD_DIR):
    """以 doSno 在 download_dir 內找對應目錄(尾碼匹配,如 MWAA<doSno>)。"""
    download_dir = pathlib.Path(download_dir)
    if not download_dir.is_dir():
        print(f"[fill_in_draft] 下載目錄不存在:{download_dir}")
        return None
    candidates = sorted(d for d in download_dir.iterdir()
                        if d.is_dir() and d.name.endswith(doSno))
    if not candidates:
        print(f"[fill_in_draft] {download_dir} 內沒有以 {doSno} 結尾的目錄。")
        return None
    if len(candidates) > 1:
        print(f"[fill_in_draft] 找到多個尾碼匹配的目錄,取第一個:{[c.name for c in candidates]}")
    return candidates[0]


def _standalone_attach_and_run():
    """standalone 入口:attach 既有 Chrome → 找閱覽器分頁 → 推 extract_dir → 跑 4-2。

    回 True / False(整體成功與否)。
    """
    driver = _attach_existing_chrome()
    if driver is None:
        return False
    handle, doSno = _find_viewer_window(driver)
    if handle is None:
        return False
    extract_dir = _resolve_extract_dir(doSno)
    if extract_dir is None:
        return False
    print(f"[fill_in_draft] extract_dir={extract_dir}")
    return fill_in_draft(driver, extract_dir)


if __name__ == "__main__":
    # standalone:attach 既有 Chrome(:9222)→ 找停在公文閱覽器分頁的 window →
    # 從 URL 抽 doSno → 推 document_download/<MW+doSno>/ → 跑 fill_in_draft。
    # 前提:Chrome 已由 main.py 啟動(會自動開 :9222),且 main.py 跑過後 Chrome
    # 仍停在閱覽器分頁(detach=True 預設留著)。
    #
    # 子命令:
    #   (無)   執行 4-2(讀標記+查表+填字+儲存+依動作)
    #   dump   只 dump 閱覽器各 frame 的辦理面板候選元素,供鎖定選擇器
    import sys

    from taipeion_login_selenium import _setup_stdout_logging
    _setup_stdout_logging()
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        ok = _standalone_dump()
    else:
        ok = _standalone_attach_and_run()
    sys.exit(0 if ok else 1)
