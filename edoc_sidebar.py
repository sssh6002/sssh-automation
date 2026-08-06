# -*- coding: utf-8 -*-
"""edoc_sidebar.py
讀／點 edoc 左側 sidebar 的項目，**不管那個項目在畫面上看不看得見**。
給備料流程（`main.py 4`）當 fallback 用。

## 為什麼需要這支（2026-08-05 實機查出來的）

`document_system._get_sidebar_paren_count` 讀「承辦中(4)」靠 Selenium 的
`element.text`，而**那只回可見文字**。edoc 的子選單收合方式是
`<ul class="submenu">` 高度設成 0 再 `overflow:hidden`（CSS `display` 仍是
`block`），所以收合時：

    <a id="menu76">承辦中(4)</a>    is_displayed() = False，.text = ''

函式的迴圈因此挑不到任何「可見且無內層」的元素，fallback 到 `<html>`，而
`<html>.text` 的可見文字裡沒有「承辦中(4)」→ regex 失配 → 回 -1。
呼叫端 `if count > 0` 不成立 → **整段下載＋摘要被跳過，流程看起來像簽收完就停了**。

實測那次（run.log 2026-08-05 08:37）就是這樣：催辦訊息 4 讀到了（那顆是
`<span class="urge">催辦訊息4</span>`，一直可見），簽收成功；接著待簽收／承辦中／
受會案件全部 -1，於是什麼都沒下載 —— 畫面上看起來就是「簽收完就停了」。

**為什麼 8/3 測的時候好好的**：那次沒有催辦訊息，沒進催辦頁。
簽收催辦通知之後，edoc 會把選單焦點切到「已處理公文」那一組
（它拿到 class `nav-show`、高度撐開），順手把「待辦公文」收起來。
也就是說:**只要有催辦訊息要簽收，收文就會停在這裡**。

只讀不到數字還不是全部 —— `_click_sidebar_item` 也檢查 `is_displayed()`，
所以就算把數字讀出來，點「承辦中」照樣會失敗。兩件事都要繞過。

## 怎麼繞

sidebar 每個項目都是 `<a onclick="return _doGoTreeAction('…jsp?id=76…', '承辦人>
待辦公文>承辦中', this);">承辦中(4)</a>` —— 導航是頁面自己的 JS 函式在做。
所以:

* 讀數字 → 用 `textContent`（收合時 `element.text` 是空的，`textContent` 讀得到）
* 點項目 → 用 JS `a.click()`，會照樣觸發 inline `onclick`，**不需要元素可見**，
  也不必去跟選單的展開動畫搏鬥（實測點 `dropdown-toggle` 展不開，機制不明）。

直接 `driver.get()` 那個 jsp 網址是**不行的** —— 清單要載進 `dTreeContent`
frame，跳出 frameset 之後 `_switch_to_signoff_frame` 就找不到東西了。
一定要走頁面自己的導航函式。

## 為什麼修在這裡，不修那支函式

`_get_sidebar_paren_count` 與 `_click_sidebar_item` 是**全自動路徑共用的**
（`pending_doc` / `main.py 1`、`2`、`3` 都在用）。動它們等於動系管師那條路，
違反 `CLAUDE.md`。這支只做「把頁面回到那些函式原本預期的狀態」，
不碰任何判定邏輯。
"""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 只在左側選單裡找 —— 全頁面掃 <a> 可能撈到麵包屑、內容區的同名連結。
_SCOPE = "#leftSideBar a, .menuArea a, #minSideBar a"

_FIND_JS = r"""
const lab = arguments[0], scope = arguments[1];
const a = [...document.querySelectorAll(scope)]
  .find(x => (x.textContent || '').trim().startsWith(lab));
if (!a) return null;
return {text: (a.textContent || '').trim(), id: a.id || '',
        onclick: (a.getAttribute('onclick') || '').slice(0, 200)};
"""

_CLICK_JS = r"""
const lab = arguments[0], scope = arguments[1];
const a = [...document.querySelectorAll(scope)]
  .find(x => (x.textContent || '').trim().startsWith(lab));
if (!a) return false;
a.click();          // inline onclick 的 _doGoTreeAction 會照樣執行
return true;
"""


def sidebar_count(driver, label):
    """讀「<label>(N)」的 N，用 textContent —— 選單收合時照樣讀得到。

    回 int >= 0；找不到或沒有數字回 -1（與 `_get_sidebar_paren_count` 同慣例，
    呼叫端維持「讀不到就保守不動」）。
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        info = driver.execute_script(_FIND_JS, label, _SCOPE)
    except Exception as e:
        print(f"[sidebar] 讀「{label}」失敗:{type(e).__name__}: {e}")
        return -1
    if not info:
        print(f"[sidebar] 左側選單找不到「{label}」")
        return -1
    m = re.search(re.escape(label) + r"\s*\(\s*([\d,]+)\s*\)", info["text"])
    if not m:
        print(f"[sidebar] 「{label}」沒有括號數字（文字:{info['text']!r}）")
        return -1
    n = int(m.group(1).replace(",", ""))
    print(f"      OK:JS 讀到{label}數 = {n}（文字「{info['text']}」，"
          f"id={info['id'] or '無'}）")
    return n


def click_sidebar(driver, label):
    """點左側選單的「<label>」。用 JS click，元素被收在收合選單裡也點得到。"""
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        ok = driver.execute_script(_CLICK_JS, label, _SCOPE)
    except Exception as e:
        print(f"[sidebar] 點「{label}」例外:{type(e).__name__}: {e}")
        return False
    if not ok:
        print(f"[sidebar] 左側選單找不到「{label}」，點不了")
        return False
    print(f"      OK:JS 點到「{label}」（繞過收合選單）")
    return True


if __name__ == "__main__":
    # 診斷用:attach 既有 Chrome，比較原版與 JS 版讀到的數字。唯讀，不點任何東西。
    from document_system import _get_sidebar_paren_count
    from fill_in_draft import _attach_existing_chrome

    d = _attach_existing_chrome()
    if d is None:
        raise SystemExit(1)
    print(f"目前頁面:{d.current_url}\n")
    for lab in ("待簽收", "承辦中", "受會案件", "待結案"):
        d.switch_to.default_content()
        a = _get_sidebar_paren_count(d, lab, timeout=3)
        b = sidebar_count(d, lab)
        flag = "  ← 原版讀不到，JS 版救回來" if a < 0 <= b else ""
        print(f"  {lab:5} 原版={a:3}  JS版={b:3}{flag}\n")
