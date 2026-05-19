"""
document_system.py
公文系統內的後續處理流程 — 假設 driver 已導航到 edoc.gov.taipei 公文首頁（已登入）。

呼叫方式：
1) 從 main.py 串接：click_document_card 回 True 後 main() 直接呼叫
     process_document_system(driver)
2) 單獨執行（測試用，跳過登入流程）：
     C:\\Python314\\python.exe document_system.py
   會用同一個 Selenium profile 開 Chrome、直接導航到 edoc 首頁；session 過期就
   提示去跑 main.py 重登。

第一版只做：點選 edoc 首頁右上方的「催辦訊息」badge。後續擴充寫進對應的
helper（_open_first_document、_handle_document_list 等）。
"""

import re
import sys
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

sys.stdout.reconfigure(encoding='utf-8')

# edoc 公文首頁。standalone 模式會直接 driver.get 這個 URL。
EDOC_HOME_URL = "https://edoc.gov.taipei/tcqb/home/default.jsp?inLine=Y"

# 「催辦訊息」badge 的 XPath 候選。實測 DOM 結構未明，由窄到寬列幾個 fallback，
# 邏輯同 click_document.py 的 DOCUMENT_XPATHS。
URGENT_MSG_XPATHS = [
    "//a[contains(normalize-space(), '催辦訊息')]",
    "//*[normalize-space()='催辦訊息']/ancestor::a[1]",
    "//*[normalize-space()='催辦訊息']/ancestor::*[@role='link' or @role='button'][1]",
    "//*[normalize-space()='催辦訊息']/ancestor::div[contains(@class, 'badge') or contains(@class, 'btn') or contains(@class, 'tag') or contains(@class, 'pill')][1]",
    "//*[normalize-space()='催辦訊息']",
    "//*[contains(normalize-space(), '催辦訊息')]",
]

# （左側 sidebar 各項 menu item 「<label>(N)」格式的 XPath 由
#  _click_sidebar_item / _get_sidebar_paren_count 內部動態組裝，不再用 hardcoded
#  常數。原 PENDING_SIGNOFF_XPATHS 移除）

# 待簽收清單表頭的「全選 checkbox」XPath 候選（緊鄰「序號」欄位的 input）。
SELECT_ALL_CHECKBOX_XPATHS = [
    "//tr[.//th[contains(normalize-space(), '序號')]]//input[@type='checkbox']",
    "//th[contains(normalize-space(), '序號')]/preceding-sibling::th[1]//input[@type='checkbox']",
    "//th[contains(normalize-space(), '序號')]//input[@type='checkbox']",
    "//thead//input[@type='checkbox']",
    # 最後 fallback：頁面上第一個 checkbox（風險較高，最後才試）
    "(//input[@type='checkbox'])[1]",
]

# 表格上方亮青色「簽收」按鈕 XPath 候選。
# 實測 (2026-05-19 dTreeContent dump)：edoc 的簽收按鈕是
#   <input value="簽收" class="toolbar_default list-btn-success">
# （type 屬性沒寫或不是 button/submit），且 <input> 沒有 textContent，所以
# normalize-space() 找不到 — 必須用 @value='簽收' 才命中。
SIGNOFF_BUTTON_XPATHS = [
    "//input[@value='簽收']",                                # ← 實測命中 (Important)
    "//*[@value='簽收']",                                    # 其他 form element 也用 value
    "//button[normalize-space()='簽收']",
    "//a[normalize-space()='簽收']",
    "//input[@type='button' and @value='簽收']",
    "//input[@type='submit' and @value='簽收']",
    "//*[@alt='簽收']",                                       # 若按鈕是 <img alt="簽收">
    "//*[normalize-space()='簽收' and (self::button or @role='button')]",
    "//*[normalize-space()='簽收']/ancestor::button[1]",
    "//*[normalize-space()='簽收']/ancestor::a[1]",
    "//span[normalize-space()='簽收']",
    "//div[normalize-space()='簽收']",
    "//*[normalize-space()='簽收' "
    "and not(contains(normalize-space(), '待簽收')) "
    "and not(contains(normalize-space(), '對方未簽收')) "
    "and not(contains(normalize-space(), '未簽收'))]",
]


def _get_urgent_message_count(driver, timeout=10):
    """讀「催辦訊息」badge 後面的數字。

    DOM 不確定是「催辦訊息」與「N」分在兩個 span 還是同一個 text node，所以雙策略：
    1. 找含「催辦訊息」的最內層元素 → 用 regex 抓自身 text 裡「催辦訊息」後面的數字
       （能 cover「催辦訊息0」同 text node 與 a/span 包子 span 的兩種情況）
    2. 策略 1 抓不到 → 學 click_document._get_document_count 在 label 周邊找純數字元素

    回傳：
        int >= 0 → 判讀成功
        -1       → 找不到 label / 無法 parse（呼叫端保守不點）
    """
    wait = WebDriverWait(driver, timeout)
    label_xpath = "//*[contains(normalize-space(), '催辦訊息')]"
    try:
        candidates = wait.until(EC.presence_of_all_elements_located((By.XPATH, label_xpath)))
    except TimeoutException:
        print("[WARN] 找不到「催辦訊息」label，無法判讀數字")
        return -1

    # 取最內層元素：自身含「催辦訊息」字串、但沒有後代元素也含此字串。
    # 用 contains(normalize-space()) 抓會把所有外層 ancestor 也抓進來，要過濾。
    label_el = None
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            inner = el.find_elements(By.XPATH, ".//*[contains(normalize-space(), '催辦訊息')]")
            if not inner:
                label_el = el
                break
        except Exception:
            continue
    if label_el is None:
        # 沒找到「葉子」元素，退一步用第一個 candidate
        label_el = candidates[0] if candidates else None
    if label_el is None:
        print("[WARN] 找不到可用的「催辦訊息」label 元素")
        return -1

    # 策略 1：label 自身 text 內 regex 抓「催辦訊息」後面的數字（容許千分位逗號）
    try:
        txt = (label_el.text or "").strip()
        m = re.search(r'催辦訊息\s*([\d,]+)', txt)
        if m:
            n = int(m.group(1).replace(",", ""))
            print(f"      OK：讀到催辦訊息數 = {n}（來源文字「{txt}」）")
            return n
    except Exception:
        pass

    # 策略 2：label 周邊找純數字元素（同 click_document._get_document_count 邏輯）
    relative_xpaths = [
        "./following-sibling::*[1]",
        "./parent::*/*[self::span or self::div or self::strong or self::b or self::p]",
        "./parent::*/parent::*//*[self::span or self::div or self::strong or self::b or self::p]",
    ]
    seen_ids = set()
    for rel_xp in relative_xpaths:
        try:
            els = label_el.find_elements(By.XPATH, rel_xp)
        except Exception:
            continue
        for el in els:
            try:
                el_id = el.id if hasattr(el, "id") else id(el)
                if el_id in seen_ids:
                    continue
                seen_ids.add(el_id)
                if not el.is_displayed():
                    continue
                txt = (el.text or "").strip()
                if not txt or txt == "催辦訊息":
                    continue
                m = re.fullmatch(r"[\d,]+", txt)
                if m:
                    n = int(txt.replace(",", ""))
                    print(f"      OK：讀到催辦訊息數 = {n}（來源文字「{txt}」）")
                    return n
            except Exception:
                continue

    print("[WARN] 找到「催辦訊息」label 但無法 parse 數字")
    return -1


def _get_sidebar_paren_count(driver, label, timeout=10):
    """讀左側 sidebar「<label>(N)」格式的數字，例如「待簽收(1)」、「承辦中(2)」、
    「受會案件(0)」、「待結案(0)」。

    Generic 版本，給所有 paren-format 的 sidebar item 用。雙策略：
    1. 找含 <label> 字串的最內層元素 → regex 抓「<label>\\s*(\\s*([\\d,]+)\\s*)」
    2. 策略 1 抓不到 → 在 sibling/parent 找鄰近的純數字元素

    回傳：
        int >= 0 → 判讀成功
        -1       → 找不到 label / 無法 parse（呼叫端保守不點）
    """
    wait = WebDriverWait(driver, timeout)
    # XPath 用 string concat 而非 f-string 避免中文 label 內含單引號的風險
    # (本專案 cascade 用的 label「待簽收/承辦中/受會案件/待結案」皆無)
    label_xpath = "//*[contains(normalize-space(), '" + label + "')]"
    try:
        candidates = wait.until(EC.presence_of_all_elements_located((By.XPATH, label_xpath)))
    except TimeoutException:
        print(f"[WARN] 找不到「{label}」label，無法判讀數字")
        return -1

    label_el = None
    for el in candidates:
        try:
            if not el.is_displayed():
                continue
            inner = el.find_elements(
                By.XPATH, ".//*[contains(normalize-space(), '" + label + "')]")
            if not inner:
                label_el = el
                break
        except Exception:
            continue
    if label_el is None:
        label_el = candidates[0] if candidates else None
    if label_el is None:
        print(f"[WARN] 找不到可用的「{label}」label 元素")
        return -1

    # 策略 1：regex 抓「<label>(N)」括號內數字
    try:
        txt = (label_el.text or "").strip()
        m = re.search(re.escape(label) + r'\s*\(\s*([\d,]+)\s*\)', txt)
        if m:
            n = int(m.group(1).replace(",", ""))
            print(f"      OK：讀到{label}數 = {n}（來源文字「{txt}」）")
            return n
    except Exception:
        pass

    # 策略 2：fallback 找鄰近純數字元素
    relative_xpaths = [
        "./following-sibling::*[1]",
        "./parent::*/*[self::span or self::div or self::strong or self::b or self::p]",
        "./parent::*/parent::*//*[self::span or self::div or self::strong or self::b or self::p]",
    ]
    seen_ids = set()
    for rel_xp in relative_xpaths:
        try:
            els = label_el.find_elements(By.XPATH, rel_xp)
        except Exception:
            continue
        for el in els:
            try:
                el_id = el.id if hasattr(el, "id") else id(el)
                if el_id in seen_ids:
                    continue
                seen_ids.add(el_id)
                if not el.is_displayed():
                    continue
                txt = (el.text or "").strip()
                if not txt or txt == label:
                    continue
                m = re.fullmatch(r"\(?\s*([\d,]+)\s*\)?", txt)
                if m:
                    n = int(m.group(1).replace(",", ""))
                    print(f"      OK：讀到{label}數 = {n}（來源文字「{txt}」）")
                    return n
            except Exception:
                continue

    print(f"[WARN] 找到「{label}」label 但無法 parse 數字")
    return -1


def _click_sidebar_item(driver, label, timeout=10):
    """點 sidebar menu item，文字含 <label>。Generic 版本，給 sidebar cascade 用。

    XPath 由窄到寬：<a> → 含 label 的元素 ancestor::a → role=link/menuitem/button →
    ancestor::li → 直接命中含 label 的元素。JS click 繞遮罩。
    """
    wait = WebDriverWait(driver, timeout)
    xpaths = [
        "//a[contains(normalize-space(), '" + label + "')]",
        "//*[contains(normalize-space(), '" + label + "')]/ancestor::a[1]",
        "//*[contains(normalize-space(), '" + label + "')]/ancestor::*[@role='link' or @role='menuitem' or @role='button'][1]",
        "//*[contains(normalize-space(), '" + label + "')]/ancestor::li[1]",
        "//*[contains(normalize-space(), '" + label + "')]",
    ]
    for xp in xpaths:
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            if not el.is_displayed():
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)
            print(f"      OK：點到「{label}」（XPath: {xp}）")
            return True
        except TimeoutException:
            continue
        except Exception as e:
            print(f"      x  「{label}」XPath {xp} 例外：{type(e).__name__}: {e}")
            continue
    print(f"[ERROR] 「{label}」全部 XPath 都失敗")
    return False


# 既有 specific helper 改 delegate generic，保留呼叫端的 import 接口
def _get_pending_signoff_count(driver, timeout=10):
    """讀「待簽收(N)」的 N。Delegate 給 _get_sidebar_paren_count。"""
    return _get_sidebar_paren_count(driver, "待簽收", timeout=timeout)


def _click_pending_signoff(driver, timeout=10):
    """點「待簽收」sidebar menu item。Delegate 給 _click_sidebar_item。"""
    return _click_sidebar_item(driver, "待簽收", timeout=timeout)


def _switch_to_frame_with_xpath(driver, target_xpath, label, timeout=15):
    """切到含 target_xpath 元素的 frame。Generic 版，給各階段（簽收、承辦中、
    受會案件、待結案等）共用。

    edoc 公文系統採 frame-based 排版：sidebar 在主文件，內容區在名為 `dTreeContent`
    的 iframe。點 sidebar item 後 frame 內容會切換，但主文件 URL 不變；要操作
    frame 內元素必須先 `driver.switch_to.frame(...)`。

    策略：先試 name=dTreeContent（最常見），不行就遍歷所有 iframe/frame 找含
    target_xpath 元素的那個。每輪等 0.5s 直到 timeout。

    label: 用於 print 訊息，例如「簽收按鈕」/「公文文號表頭」。
    成功 → driver 焦點留在正確 frame，回 True；失敗 → 切回 default_content，回 False。

    呼叫端要自己包 diagnostic（_switch_to_signoff_frame 有大型 dump，本函式只
    回 False，呼叫端決定要不要再印更多）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        driver.switch_to.default_content()
        try:
            frame = driver.find_element(By.NAME, "dTreeContent")
            driver.switch_to.frame(frame)
            if driver.find_elements(By.XPATH, target_xpath):
                print(f"      OK：切到 frame name='dTreeContent' 且找到「{label}」")
                return True
            driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()

        try:
            iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
            for ifr in iframes:
                try:
                    driver.switch_to.default_content()
                    driver.switch_to.frame(ifr)
                    if driver.find_elements(By.XPATH, target_xpath):
                        name = ifr.get_attribute("name") or ifr.get_attribute("id") or "<unnamed>"
                        print(f"      OK：切到 frame {name} 且找到「{label}」")
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        driver.switch_to.default_content()
        time.sleep(0.5)

    driver.switch_to.default_content()
    return False


def _switch_to_signoff_frame(driver, timeout=15):
    """切換 driver 焦點到含「簽收」內容的 frame。

    edoc 公文系統採 frame-based 排版：左側 sidebar 在主文件，內容區（表格、checkbox、
    簽收按鈕）在名為 `dTreeContent` 的 iframe 裡。sidebar menu item 的 onclick 帶
    `target="dTreeContent"`，點完 frame 內容更新但主文件 URL 不變。

    操作 checkbox / 簽收按鈕前必須先呼叫 `driver.switch_to.frame(...)`，否則 XPath
    全部找不到（這就是先前「2/2 checkbox 勾選成功」假象的成因：那 2 個 checkbox
    是主文件其他地方的，不是表格內可見的那兩個）。

    策略：先試 name=dTreeContent，再 fallback 遍歷所有 iframe/frame 找「簽收」字串
    （排除「待簽收」避免誤判 sidebar）。等至多 timeout 秒讓 frame 內容載入。

    成功 → driver 焦點留在正確 frame，回 True；失敗 → 切回 default_content，回 False。
    """
    # 切 frame 邏輯共用 _switch_to_frame_with_xpath；diagnostic 在這保留專屬版本
    # （因為「簽收」失敗時要 dump 大量 button-like 元素 + body innerText，是這個
    # 階段獨有的細節）
    target_xpath = (
        "//input[@value='簽收'] "
        "| //*[@value='簽收'] "
        "| //*[normalize-space()='簽收' "
        "and not(contains(normalize-space(), '待簽收')) "
        "and not(contains(normalize-space(), '對方未簽收')) "
        "and not(contains(normalize-space(), '未簽收'))]"
    )
    if _switch_to_frame_with_xpath(driver, target_xpath, "簽收按鈕", timeout=timeout):
        return True

    print(f"[ERROR] 等 {timeout}s 仍找不到含「簽收」按鈕的 frame，診斷：")
    try:
        iframes = driver.find_elements(By.XPATH, "//iframe | //frame")
        # 先在 outer doc 把每個 iframe 的 metadata 抓完（避免 switch 後 stale）
        frame_meta = []
        for i, ifr in enumerate(iframes):
            try:
                frame_meta.append({
                    "idx": i,
                    "el": ifr,
                    "tag": ifr.tag_name,
                    "name": ifr.get_attribute("name") or "",
                    "id": ifr.get_attribute("id") or "",
                    "src": ifr.get_attribute("src") or "",
                })
            except Exception as e:
                frame_meta.append({"idx": i, "el": ifr, "err": str(e)})

        print(f"      主文件有 {len(iframes)} 個 frame/iframe：")
        for m in frame_meta:
            if "err" in m:
                print(f"      [{m['idx']+1}] metadata 抓不到：{m['err']}")
                continue
            print(f"      [{m['idx']+1}] <{m['tag']}> name='{m['name']}' id='{m['id']}'")
            print(f"           src={m['src'][:200]}")

        # 進每個 iframe 看內容
        for m in frame_meta:
            if "err" in m:
                continue
            label = m['name'] or m['id'] or f"#{m['idx']+1}"
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(m['el'])
                # 在 frame 內讀 URL、title、body preview、「簽收」相關元素
                inner_url = driver.execute_script("return document.URL;") or "?"
                inner_title = driver.execute_script("return document.title;") or "?"
                body_preview = driver.execute_script(
                    "return document.body ? document.body.innerText.substring(0, 200) : 'no body';"
                ) or ""
                hits = driver.find_elements(By.XPATH, "//*[contains(normalize-space(), '簽收')]")
                btn_hits = driver.find_elements(
                    By.XPATH,
                    "//*[normalize-space()='簽收' "
                    "and not(contains(normalize-space(), '待簽收')) "
                    "and not(contains(normalize-space(), '對方未簽收')) "
                    "and not(contains(normalize-space(), '未簽收'))]"
                )
                print(f"      [{label}] inner_url={inner_url[:120]}")
                print(f"           inner_title={inner_title}")
                print(f"           body_preview={body_preview!r}")
                print(f"           「簽收」字串元素 = {len(hits)}，純「簽收」按鈕 = {len(btn_hits)}")
                for j, b in enumerate(btn_hits[:3]):
                    try:
                        outer = driver.execute_script(
                            "return arguments[0].outerHTML;", b) or ""
                        print(f"           [btn{j+1}] {outer[:200]}")
                    except Exception:
                        pass
                # 在 dTreeContent (有 row data 的 frame) 內擴大 dump：列 button-like 元素
                if label == "dTreeContent" or "dTreeContent" in (m.get('id') or ""):
                    print(f"      [{label}] 額外診斷 — 列 button-like 元素：")
                    button_likes = driver.find_elements(
                        By.XPATH,
                        "//button | //a[@class] | //input[@type='button' or @type='submit' or @type='image'] "
                        "| //*[@role='button'] | //img[@alt and not(@alt='')] "
                        "| //*[contains(@class, 'btn')] | //*[contains(@class, 'button')]"
                    )
                    # 去重 (Selenium 可能有重複 reference)
                    seen_ids = set()
                    unique_btns = []
                    for bl in button_likes:
                        if id(bl) not in seen_ids:
                            seen_ids.add(id(bl))
                            unique_btns.append(bl)
                    print(f"           找到 {len(unique_btns)} 個 button-like 元素")
                    # 篩出 text/alt/value 含「簽」或在頂部的
                    for j, bl in enumerate(unique_btns[:30]):
                        try:
                            info = driver.execute_script("""
                                var el = arguments[0];
                                return {
                                    tag: el.tagName.toLowerCase(),
                                    text: (el.textContent || '').trim().substring(0, 50),
                                    alt: el.getAttribute('alt') || '',
                                    value: el.getAttribute('value') || '',
                                    title: el.getAttribute('title') || '',
                                    cls: el.className || '',
                                    visible: el.offsetWidth > 0 && el.offsetHeight > 0,
                                };
                            """, bl) or {}
                            # 只列 visible + (text含簽 or alt含簽 or value含簽 or title含簽)
                            joined = f"{info.get('text','')} {info.get('alt','')} {info.get('value','')} {info.get('title','')}"
                            if info.get('visible') and '簽' in joined:
                                print(f"           [bl{j+1}] <{info.get('tag')}> text=「{info.get('text')}」 alt=「{info.get('alt')}」 value=「{info.get('value')}」 title=「{info.get('title')}」 class=「{info.get('cls')[:60]}」")
                        except Exception:
                            pass
                    # 也 dump body 完整 innerText (前 2000 字)
                    full_body = driver.execute_script(
                        "return document.body ? document.body.innerText.substring(0, 2000) : '';"
                    ) or ""
                    print(f"      [{label}] body innerText (前 2000 字)：")
                    print(f"           {full_body!r}")
            except Exception as e:
                print(f"      [{label}] 進 frame 失敗：{type(e).__name__}: {str(e)[:150]}")
        driver.switch_to.default_content()
    except Exception as e:
        print(f"      diagnostic 失敗：{type(e).__name__}: {e}")
    return False


def _try_check_checkbox(driver, el):
    """確保單一 checkbox 為勾選狀態，回傳是否成功（是否被真實 click event 勾起來）。

    為何不直接 JS 設 `el.checked = true`：實測會說謊。Vue / React / Ant Design 等
    客製 checkbox 的點擊 handler 綁在 wrapper（label / 外層 span / 外層 div），
    不在隱藏的 <input> 上。直接設 input.checked 只更新 DOM property，框架的內部
    state 完全沒變，畫面 ✓ 不出現，下一步點「簽收」會被當「沒選任何 row」拒絕。
    is_selected() 仍然回 True 因為它讀的是 DOM .checked — 報告也跟著說謊。

    本函式只用「真實 click event」策略，依序試六種點擊目標：
      1. input 元素 — Selenium 原生 click
      2. input 元素 — JS click（繞 opacity:0 / 遮罩）
      3. 最近的 <label> ancestor — JS click（HTML 規定 label click 會 forward 到 input）
      4. parent[1] — JS click（cover wrapper 綁在直接父元素的客製 checkbox）
      5. parent[2] — JS click
      6. parent[3] — JS click

    任一策略後 is_selected() 為 True 就回 True。全部失敗回 False（不再嘗試 JS 設
    checked = true 蒙混過關）。
    """
    try:
        if el.is_selected():
            return True
    except Exception:
        return False

    targets = [("input native", el, "native"), ("input JS", el, "js")]

    # iCheck plugin API（最 reliable for iCheck-based UIs，例如 edoc 公文系統）。
    # iCheck 把 input 設 opacity:0 + <ins class="iCheck-helper"> 覆蓋，handler 綁在
    # helper 上，直接點 input 反而被 handler 把 state 改回去。用官方 jQuery API
    # 才能正確觸發 iCheck 內部 state machine。
    targets.append(("iCheck API", el, "icheck_api"))

    # iCheck helper sibling — jQuery 不可用時的 fallback，直接點視覺覆蓋層
    try:
        helper = el.find_element(
            By.XPATH, "./following-sibling::ins[contains(@class, 'iCheck-helper')]")
        targets.append(("iCheck helper sibling", helper, "js"))
    except Exception:
        pass

    try:
        label = el.find_element(By.XPATH, "./ancestor::label[1]")
        targets.append(("label", label, "js"))
    except Exception:
        pass
    for level in range(1, 4):
        try:
            anc = el.find_element(By.XPATH, "/".join([".."] * level))
            targets.append((f"parent[{level}]", anc, "js"))
        except Exception:
            break

    for name, target, method in targets:
        try:
            if method == "native":
                target.click()
            elif method == "icheck_api":
                # 跑 jQuery + iCheck API；jQuery 不在或 iCheck plugin 沒載入 → return False，跳下一策略
                ok = driver.execute_script("""
                    var el = arguments[0];
                    if (window.jQuery && typeof jQuery(el).iCheck === 'function') {
                        jQuery(el).iCheck('check');
                        return true;
                    }
                    return false;
                """, target)
                if not ok:
                    continue
            else:
                driver.execute_script("arguments[0].click();", target)
            time.sleep(0.2)
            if el.is_selected():
                print(f"        勾選成功 (策略: {name})")
                return True
        except Exception:
            continue
    return False


def _check_select_all(driver, timeout=10):
    """確保「待簽收」清單上所有 checkbox 都呈勾選狀態。

    策略：蒐集頁面上所有相關的 input[type=checkbox]（先 SELECT_ALL_CHECKBOX_XPATHS
    精確定位，找不到再退讓到「table 內所有」與「頁面所有」），對每個未勾的呼叫
    _try_check_checkbox 試三種 click strategy。

    為何不只點 header「全選」：實測 header checkbox 行為不確定（custom CSS 把 input
    設 opacity:0、framework binding 不 cascade、單一 XPath 命不中）。把每個 row 都
    勾起來是最 robust 的做法，end state 一致為「全部已勾」，不受 header 行為影響。

    回傳 True 表示最終至少一個 checkbox 為勾選狀態。
    """
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='checkbox']"))
        )
    except TimeoutException:
        print("[ERROR] 頁面上找不到任何 input[type=checkbox]")
        return False

    # 蒐集候選：精確 XPaths 優先，再 table 內，再全頁
    candidate_xpaths = SELECT_ALL_CHECKBOX_XPATHS + [
        "//table//input[@type='checkbox']",
        "//input[@type='checkbox']",
    ]
    seen_ids = set()
    targets = []
    for xp in candidate_xpaths:
        try:
            for el in driver.find_elements(By.XPATH, xp):
                el_id = id(el)
                if el_id not in seen_ids:
                    seen_ids.add(el_id)
                    targets.append(el)
        except Exception:
            continue

    print(f"      蒐集到 {len(targets)} 個 checkbox 候選，逐個確認/勾選...")
    successful = 0
    for el in targets:
        if _try_check_checkbox(driver, el):
            successful += 1

    if successful == 0:
        print("[ERROR] 沒有任何 checkbox 能被勾選。診斷前 5 個元素 + parent wrapper：")
        for i, el in enumerate(targets[:5]):
            try:
                info = driver.execute_script("""
                    var el = arguments[0];
                    var p1 = el.parentElement;
                    var p2 = p1 ? p1.parentElement : null;
                    return {
                        input: el.outerHTML,
                        parent1: p1 ? p1.outerHTML : null,
                        parent2: p2 ? p2.outerHTML : null,
                    };
                """, el) or {}
                print(f"      [{i+1}] input  : {(info.get('input') or '')[:200]}")
                print(f"           parent1: {(info.get('parent1') or '')[:300]}")
                print(f"           parent2: {(info.get('parent2') or '')[:400]}")
            except Exception as e:
                print(f"      [{i+1}] dump 失敗：{type(e).__name__}: {e}")
        return False

    print(f"      OK：{successful}/{len(targets)} 個 checkbox 已為勾選狀態")
    return True


def _click_signoff_button(driver, timeout=10):
    """點選表格上方亮青色的「簽收」按鈕。

    **重要**：這個動作會改變公文狀態（待簽收 → 承辦中），沒有 admin 介入無法復原。
    呼叫端應於本函式呼叫前印明顯警告。

    回傳 True 表示點到，False 表示所有 XPath 都失敗。
    """
    wait = WebDriverWait(driver, timeout)
    for xp in SIGNOFF_BUTTON_XPATHS:
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            if not el.is_displayed():
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)
            print(f"      OK：點到「簽收」按鈕（XPath: {xp}）")
            return True
        except TimeoutException:
            continue
        except Exception as e:
            print(f"      x  「簽收」XPath {xp} 例外：{type(e).__name__}: {e}")
            continue
    print("[ERROR] 「簽收」按鈕全部 XPath 都失敗。診斷：頁面上含「簽收」文字的元素：")
    try:
        candidates = driver.find_elements(By.XPATH, "//*[contains(normalize-space(), '簽收')]")
        # 篩掉「待簽收」(sidebar 那個 menu item, 不是按鈕)
        candidates = [el for el in candidates if "待簽收" not in (el.text or "")]
        # 篩掉外層包很多的 ancestor: 只留沒有後代也含「簽收」字串的「葉子」
        leaves = []
        for el in candidates:
            try:
                inner = el.find_elements(By.XPATH, ".//*[contains(normalize-space(), '簽收')]")
                inner = [i for i in inner if "待簽收" not in (i.text or "")]
                if not inner:
                    leaves.append(el)
            except Exception:
                continue
        print(f"      找到 {len(leaves)} 個葉子元素含「簽收」（已濾掉「待簽收」）")
        for i, el in enumerate(leaves[:10]):
            try:
                info = driver.execute_script("""
                    var el = arguments[0];
                    var p1 = el.parentElement;
                    var p2 = p1 ? p1.parentElement : null;
                    return {
                        tag: el.tagName.toLowerCase(),
                        text: (el.textContent || '').trim().substring(0, 50),
                        outer: el.outerHTML,
                        parent1: p1 ? p1.outerHTML : null,
                        parent2: p2 ? p2.outerHTML : null,
                    };
                """, el) or {}
                print(f"      [{i+1}] tag=<{info.get('tag')}> text=「{info.get('text')}」")
                print(f"           self   : {(info.get('outer') or '')[:250]}")
                print(f"           parent1: {(info.get('parent1') or '')[:300]}")
                print(f"           parent2: {(info.get('parent2') or '')[:400]}")
            except Exception as e:
                print(f"      [{i+1}] dump 失敗：{type(e).__name__}: {e}")
    except Exception as e:
        print(f"      diagnostic dump 失敗：{type(e).__name__}: {e}")
    return False


def _click_first_document_in_pending(driver, timeout=15):
    """點承辦中清單最上方的公文（公文文號 column 第一筆連結）。

    edoc 承辦中頁顯示一個表格，欄位序號/簽核/收文/狀別/速別/公文文號/送件時間/...
    每列「公文文號」欄是藍色超連結文字（例如 MWAA1156004874），點下去開公文內容。
    本函式定位該 link 並點。

    策略（每個 candidate 命中後都做 text verify：>=8 字、A-Z/0-9 混合）：
    1. 主：<a> 元素的 textContent 符合 `^[A-Z][A-Z0-9]*\\d{4,}$`
    2. Fallback 1：任何可見元素（a/span/td/div/input/button）的 textContent 或
       value 符合 pattern。實測命中 — MWAA1156004874 不是 <a>，而是其他 tag
       做 link 樣式偽裝。
    3. Fallback 2：JS column-index — 找含「公文文號」th 的 column，往對應 td 內
       找含 pattern 的子元素。萬一 text pattern 不適用時兜底。
    4. JS click 繞遮罩（同 _click_sidebar_item 套路）。
    5. 全部失敗 → diagnostic dump：含「公文文號」table 第一筆 tr outerHTML、frame
       內所有可見 a 的 text/href、含公文號 pattern 的葉子元素 tag+outerHTML、
       body innerText 前 1500 字 — 看 MWAA 實際是什麼 tag、結構。

    回 True 表示已點到，False 表示沒找到（呼叫端應改手動處理）。
    """
    deadline = time.time() + timeout
    target = None
    strategy = None

    def _looks_like_doc_no(text):
        # 公文號特徵：>=8 字、全 A-Z/0-9、字母+數字混合
        if not text or len(text) < 8:
            return False
        if not all(ord(c) < 128 and (c.isupper() or c.isdigit()) for c in text):
            return False
        return any(c.isalpha() for c in text) and any(c.isdigit() for c in text)

    # 主策略：text pattern in <a>
    while time.time() < deadline:
        try:
            target = driver.execute_script("""
                var anchors = document.querySelectorAll('a');
                var pat = /^[A-Z][A-Z0-9]*\\d{4,}$/;
                for (var i = 0; i < anchors.length; i++) {
                    var a = anchors[i];
                    var text = (a.textContent || '').trim();
                    if (text.length < 8) continue;
                    if (!pat.test(text)) continue;
                    if (a.offsetParent === null) continue;
                    return a;
                }
                return null;
            """)
            if target:
                strategy = "text pattern (a)"
                break
        except Exception as e:
            print(f"      x  JS text-pattern (a) find 例外：{type(e).__name__}: {e}")
        time.sleep(0.5)

    # Fallback 1：text pattern in any element — MWAA 可能不是 <a>，而是 <span>/
    # <input>/<td> 等做成 link 樣式的元素。掃所有元素的 textContent 或 value。
    if not target:
        print("      x  <a> 內找不到公文號，掃所有元素 textContent / value...")
        try:
            target = driver.execute_script("""
                var all = document.querySelectorAll('a, span, td, div, input, button');
                var pat = /^[A-Z][A-Z0-9]*\\d{4,}$/;
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    if (el.offsetParent === null) continue;
                    var text;
                    if (el.tagName === 'INPUT') {
                        text = (el.value || '').trim();
                    } else {
                        text = (el.textContent || '').trim();
                    }
                    if (text.length < 8) continue;
                    if (!pat.test(text)) continue;
                    // 葉子節點優先（避免命中包含同 text 的外殼 div / td）
                    var hasSameTextChild = false;
                    var kids = el.querySelectorAll('*');
                    for (var k = 0; k < kids.length; k++) {
                        var ct = (kids[k].textContent || kids[k].value || '').trim();
                        if (ct === text) { hasSameTextChild = true; break; }
                    }
                    if (hasSameTextChild) continue;
                    return el;
                }
                return null;
            """)
            if target:
                strategy = "text pattern (any element)"
        except Exception as e:
            print(f"      x  JS text-pattern (any) find 例外：{type(e).__name__}: {e}")

    # Fallback 2：JS column-index，cell 內找含公文號 pattern 的子元素
    if not target:
        print("      x  text pattern 全部沒命中，試 column-index fallback...")
        try:
            target = driver.execute_script("""
                var ths = document.querySelectorAll('th');
                for (var i = 0; i < ths.length; i++) {
                    var thText = (ths[i].textContent || '').trim();
                    if (thText.indexOf('公文文號') === -1) continue;
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
                    var pat = /^[A-Z][A-Z0-9]*\\d{4,}$/;
                    for (var k = 0; k < rows.length; k++) {
                        var cells = rows[k].children;
                        if (idx >= cells.length) continue;
                        var cell = cells[idx];
                        if (cell.tagName !== 'TD') continue;
                        var kids = cell.querySelectorAll('*');
                        for (var m = 0; m < kids.length; m++) {
                            var ck = kids[m];
                            if (ck.offsetParent === null) continue;
                            var ckt = (ck.textContent || ck.value || '').trim();
                            if (pat.test(ckt)) return ck;
                        }
                        var ct = (cell.textContent || '').trim();
                        if (pat.test(ct)) return cell;
                    }
                }
                return null;
            """)
            if target:
                strategy = "column-index"
        except Exception as e:
            print(f"      x  JS column-index find 例外：{type(e).__name__}: {e}")

    # 命中後 verify + click
    if target:
        try:
            txt = driver.execute_script(
                "return (arguments[0].textContent || arguments[0].value || '').trim();",
                target) or ""
            if not _looks_like_doc_no(txt):
                print(f"      x  {strategy} 找到的 text「{txt}」不像公文號，拒絕點。")
                target = None
            else:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", target)
                print(f"      OK：點到承辦中最上方公文「{txt}」（{strategy}）")
                return True
        except Exception as e:
            print(f"      x  click 公文號失敗：{type(e).__name__}: {e}")
            target = None

    # 全部失敗 → diagnostic（含所有可見 a 與含公文號 pattern 的葉子元素，看 MWAA
    # 實際是什麼 tag、structure）
    print("[ERROR] 找不到承辦中最上方公文連結。診斷：")
    try:
        ths = driver.find_elements(By.XPATH, "//th[contains(normalize-space(), '公文文號')]")
        print(f"      頁面含「公文文號」表頭 th = {len(ths)} 個")
        tables = driver.find_elements(
            By.XPATH, "//table[.//th[contains(normalize-space(), '公文文號')]]")
        print(f"      含「公文文號」表頭的 table = {len(tables)} 個")
        for i, t in enumerate(tables[:2]):
            try:
                rows = t.find_elements(By.XPATH, ".//tbody//tr")
                print(f"      table[{i+1}] tbody tr = {len(rows)} 個")
                if rows:
                    outer = driver.execute_script(
                        "return arguments[0].outerHTML;", rows[0]) or ""
                    print(f"      table[{i+1}] 第一筆 tr outerHTML (前 800 字)：{outer[:800]}")
            except Exception as e:
                print(f"      table[{i+1}] dump 失敗：{type(e).__name__}: {e}")

        anchors_info = driver.execute_script("""
            var anchors = document.querySelectorAll('a');
            var out = [];
            for (var i = 0; i < anchors.length && out.length < 30; i++) {
                var a = anchors[i];
                if (a.offsetParent === null) continue;
                out.push({
                    text: (a.textContent || '').trim().substring(0, 60),
                    href: (a.getAttribute('href') || '').substring(0, 80),
                });
            }
            return out;
        """) or []
        print(f"      frame 內可見 a 元素（前 {len(anchors_info)} 個）：")
        for i, info in enumerate(anchors_info):
            print(f"      [a{i+1}] text=「{info.get('text')}」 href={info.get('href')}")

        doc_like_hits = driver.execute_script("""
            var all = document.querySelectorAll('*');
            var pat = /[A-Z][A-Z0-9]*\\d{4,}/;
            var out = [];
            for (var i = 0; i < all.length && out.length < 20; i++) {
                var el = all[i];
                if (el.offsetParent === null) continue;
                var text = (el.textContent || el.value || '').trim();
                if (!pat.test(text)) continue;
                var hasSameTextChild = false;
                var kids = el.querySelectorAll('*');
                for (var k = 0; k < kids.length; k++) {
                    var ct = (kids[k].textContent || kids[k].value || '').trim();
                    if (ct === text) { hasSameTextChild = true; break; }
                }
                if (hasSameTextChild) continue;
                out.push({
                    tag: el.tagName,
                    text: text.substring(0, 60),
                    outer: el.outerHTML.substring(0, 300),
                });
            }
            return out;
        """) or []
        print(f"      含公文號 pattern 的葉子元素（{len(doc_like_hits)} 個）：")
        for i, h in enumerate(doc_like_hits):
            print(f"      [hit{i+1}] tag={h.get('tag')} text=「{h.get('text')}」")
            print(f"             outer={h.get('outer')}")

        body_preview = driver.execute_script(
            "return document.body ? document.body.innerText.substring(0, 1500) : '';"
        ) or ""
        print(f"      body innerText (前 1500 字)：{body_preview!r}")
    except Exception as e:
        print(f"      diagnostic 失敗：{type(e).__name__}: {e}")
    return False


def _click_urgent_message(driver, timeout=10):
    """點選 edoc 公文首頁的「催辦訊息」badge。

    回傳 True 表示點到，False 表示所有 XPath 都失敗。用 JS click 繞遮罩，與
    click_document._click_document_card 同套路；不抓 href 同分頁導航，因為催辦
    可能是 modal / 同頁切換而不是新分頁，目前先讓它走元素的原生行為觀察結果。
    """
    wait = WebDriverWait(driver, timeout)
    for xp in URGENT_MSG_XPATHS:
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            if not el.is_displayed():
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)
            print(f"      OK：點到「催辦訊息」（XPath: {xp}）")
            return True
        except TimeoutException:
            continue
        except Exception as e:
            print(f"      x  「催辦訊息」XPath {xp} 例外：{type(e).__name__}: {e}")
            continue
    print("[ERROR] 「催辦訊息」全部 XPath 都失敗")
    return False


def process_document_system(driver):
    """公文系統處理主入口。driver 必須已導航到 edoc 公文首頁。

    流程：
        1. 確認 current_url 在 edoc.gov.taipei
        2. 讀右上「催辦訊息N」數字
           - > 0：點進催辦頁，sleep 2 印 URL/title 觀察
           - = 0：跳過
           - 判讀失敗 (-1)：保守不點，印警告繼續
        3. 讀左側 sidebar「待簽收(N)」數字（不管催辦結果如何都檢查 — sidebar
           是常駐元件，導航到催辦頁之後仍然看得到）
           - > 0：點進待簽收清單
           - = 0：跳過
           - 判讀失敗 (-1)：保守不點，印警告繼續
    回傳 True 表示流程跑完（即使部分項目跳過或保守不點）；False 表示前置檢查失敗
    或任一點擊行動失敗。
    """
    print("[document_system] 開始處理公文系統...")

    try:
        current = driver.current_url
    except Exception as e:
        print(f"[ERROR] 讀 current_url 失敗：{type(e).__name__}: {e}")
        return False

    if "edoc.gov.taipei" not in current:
        print(f"[ERROR] 當前 URL 不在 edoc：{current}")
        return False

    # ── 催辦訊息 ────────────────────────────────────────────────────────────
    print("[document_system] 讀「催辦訊息」待辦數...")
    urgent_count = _get_urgent_message_count(driver)
    if urgent_count < 0:
        print("[document_system] 無法判讀催辦訊息數，保守不點，繼續下一步。")
    elif urgent_count == 0:
        print("[document_system] 催辦訊息 = 0，無待辦催辦，跳過點擊。")
    else:
        print(f"[document_system] 催辦訊息 = {urgent_count}，點選進入催辦頁...")
        if not _click_urgent_message(driver):
            return False
        time.sleep(2)
        try:
            print(f"[document_system] 催辦頁 URL：{driver.current_url}")
            print(f"[document_system] 催辦頁標題：{driver.title}")
        except Exception as e:
            print(f"[document_system] 讀狀態失敗：{type(e).__name__}: {e}")

    # ── 待簽收 ─────────────────────────────────────────────────────────────
    print("[document_system] 讀左側 sidebar「待簽收」數...")
    signoff_count = _get_pending_signoff_count(driver)
    if signoff_count < 0:
        print("[document_system] 無法判讀待簽收數，保守不點，繼續下一步。")
    elif signoff_count == 0:
        print("[document_system] 待簽收 = 0，無待簽收公文，跳過點擊。")
    else:
        print(f"[document_system] 待簽收 = {signoff_count}，點選進入待簽收清單...")
        if not _click_pending_signoff(driver):
            return False
        time.sleep(2)
        try:
            print(f"[document_system] 待簽收頁 URL：{driver.current_url}")
            print(f"[document_system] 待簽收頁標題：{driver.title}")
        except Exception as e:
            print(f"[document_system] 讀狀態失敗：{type(e).__name__}: {e}")

        # 待簽收清單載入後：先切換到內容 frame 才能找到 checkbox + 簽收按鈕
        # （edoc 是 frame-based 排版，內容區在名為 dTreeContent 的 iframe）
        # **警告**：簽收會改變公文狀態（待簽收 → 承辦中），無 admin 介入無法復原
        print(f"[WARN] 即將自動執行：勾選 {signoff_count} 筆待簽收 + 點「簽收」按鈕")
        print(f"[WARN] 簽收會把公文從「待簽收」狀態改為「承辦中」，無法復原")
        print("[document_system] 切換到內容 frame...")
        if not _switch_to_signoff_frame(driver):
            print("[document_system] 切不到內容 frame，跳過簽收動作。請手動處理。")
        elif not _check_select_all(driver):
            print("[document_system] 全選 checkbox 失敗，不執行簽收。請手動處理。")
            driver.switch_to.default_content()
        elif not _click_signoff_button(driver):
            print("[document_system] 找不到「簽收」按鈕，請手動處理。")
            driver.switch_to.default_content()
        else:
            # 簽收後等系統回應（可能跳 JS confirm 由 unhandledPromptBehavior=accept
            # 自動接受、或跳轉到下一頁、或就地刷新清單）
            time.sleep(3)
            try:
                # 注意：driver 此時還在 frame 內，current_url/title 讀的是主文件
                # （driver.current_url 永遠是主文件 URL，不受 frame switch 影響）
                print(f"[document_system] 簽收後 URL：{driver.current_url}")
                print(f"[document_system] 簽收後標題：{driver.title}")
            except Exception as e:
                print(f"[document_system] 讀狀態失敗：{type(e).__name__}: {e}")
            # 切回主文件，後續操作（若有）才正常
            driver.switch_to.default_content()

    # ── Sidebar cascade：承辦中 → 受會案件 → 待結案 ──────────────────────
    # 走到這代表 待簽收 階段已處理完畢（signoff 或 skip）。接下來檢查 sidebar 的
    # 其他三個項目，依序看哪個有待辦：第一個 count > 0 的項目就點入並呼叫對應的
    # 處理函式（pending_doc / circulate_doc / pending_closeout_doc）。三個都 0
    # 就印「無公文待處理」當終點。
    if _run_sidebar_cascade(driver):
        # cascade 點入某項目並呼叫了 handler；流程到此完成
        print("[完成] 公文系統處理流程結束。")
    # else: cascade 印「無公文待處理」當最後一行，不再多印 [完成]
    return True


def _run_sidebar_cascade(driver):
    """處理 sidebar 的「承辦中 → 受會案件 → 待結案」三段串聯。

    依序檢查每個項目的 count：
    - > 0：點入該項目，呼叫對應 handler（pending_doc / circulate_doc /
           pending_closeout_doc），return True
    - == 0：印「= 0，跳過」，看下一個
    - 判讀失敗 (-1)：印警告，看下一個

    三項都 0 / 判讀失敗 → 印「無公文待處理」（最後一行），return False。
    """
    cascade = [
        ("承辦中", pending_doc),
        ("受會案件", circulate_doc),
        ("待結案", pending_closeout_doc),
    ]
    for label, handler_fn in cascade:
        print(f"[document_system] 讀左側 sidebar「{label}」數...")
        count = _get_sidebar_paren_count(driver, label)
        if count > 0:
            print(f"[document_system] {label} = {count}，點選進入...")
            if not _click_sidebar_item(driver, label):
                print(f"[document_system] 點「{label}」失敗，請手動處理。")
                return True  # 點失敗仍視為「有處理過」，主流程走 [完成]
            time.sleep(2)
            try:
                print(f"[document_system] {label}頁 URL：{driver.current_url}")
                print(f"[document_system] {label}頁標題：{driver.title}")
            except Exception as e:
                print(f"[document_system] 讀狀態失敗：{type(e).__name__}: {e}")
            handler_fn(driver)
            return True
        if count == 0:
            print(f"[document_system] {label} = 0，跳過。")
        else:
            print(f"[document_system] 無法判讀{label}數，繼續下一個。")
    # 三個都沒事 → 工作完成
    print("無公文待處理")
    return False


def pending_doc(driver):
    """承辦中公文處理流程。第一階段：點承辦中清單最上方的公文（公文文號 column
    第一筆 link）。後續（檢視內容、簽辦、送件等）依 DOM 結構再擴充。

    呼叫時機：cascade 點完 sidebar「承辦中」之後（driver 此時 focus 在主文件，
    內容區 dTreeContent iframe 內已載入承辦中清單）。

    流程：
    1. 切到含「公文文號」表頭的 frame（通常是 dTreeContent）
    2. 點公文文號 column 第一筆連結
    3. sleep 3s 等系統回應，印導航後的 URL/title 觀察狀態
    4. 切回主文件供後續操作

    回 True 表示順利點到、False 表示中途失敗（呼叫端會收到，但目前 cascade 不
    依賴回傳值決定下一步）。
    """
    print("[pending_doc] 承辦中公文處理流程開始")
    print("[pending_doc] 切到內容 frame（找「公文文號」表頭）...")
    target_xpath = "//th[contains(normalize-space(), '公文文號')]"
    if not _switch_to_frame_with_xpath(driver, target_xpath, "公文文號表頭"):
        print("[pending_doc] 切不到含承辦中清單的 frame，請手動處理。")
        return False

    print("[pending_doc] 點承辦中最上方的公文...")
    if not _click_first_document_in_pending(driver):
        print("[pending_doc] 點公文失敗，請手動處理。")
        driver.switch_to.default_content()
        return False

    # 點公文後系統可能就地 frame 切到公文內容、或開新分頁、或彈 modal。
    # 給足夠時間觀察，再 print 主文件狀態。driver.current_url 永遠是主文件 URL，
    # 不受 frame switch 影響 — 若 frame 內容換了但主文件沒動，URL 不會變。
    time.sleep(3)
    try:
        print(f"[pending_doc] 點開後 URL：{driver.current_url}")
        print(f"[pending_doc] 點開後標題：{driver.title}")
        handles = driver.window_handles
        if len(handles) > 1:
            print(f"[pending_doc] 偵測到 {len(handles)} 個 window — 公文內容可能開在新分頁")
    except Exception as e:
        print(f"[pending_doc] 讀狀態失敗：{type(e).__name__}: {e}")

    # 切回主文件供後續可能的操作
    driver.switch_to.default_content()
    return True


def circulate_doc(driver):
    """受會案件處理流程。第一版只印 TODO。"""
    _ = driver  # 同 pending_doc
    print("[circulate_doc] 受會案件處理流程開始（尚未實作）")
    print("[circulate_doc] TODO: 切到內容 frame、讀受會案件清單、逐筆處理")
    return True


def pending_closeout_doc(driver):
    """待結案處理流程。第一版只印 TODO。"""
    _ = driver  # 同 pending_doc
    print("[pending_closeout_doc] 待結案處理流程開始（尚未實作）")
    print("[pending_closeout_doc] TODO: 切到內容 frame、讀待結案清單、逐筆結案")
    return True


def _standalone_open_chrome_at_edoc():
    """單獨執行時開 Chrome 並導航到 edoc 公文首頁。

    流程：
    1. 預清理 Selenium Chrome（避免 profile 被前一次 detach 的 Chrome 鎖住）
    2. 用 _build_chrome_options() 建 options，與 main.py 完全一致
    3. driver.get(EDOC_HOME_URL)，sleep 2 後檢查 current_url
    4. 若被導去 login.gov.taipei / sso → session 過期，印提示後回 None
    回傳 driver 或 None。

    注意：失敗路徑 return None 時故意不呼叫 driver.quit() — options 帶 detach=True，
    Chrome 會留著；下次跑時 _close_selenium_chrome_only 會清掉。與
    login_taipeion_selenium 的 lifecycle 模式一致。
    """
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException

    from taipeion_login_selenium import _build_chrome_options, _close_selenium_chrome_only

    print("[standalone] 預清理上一次 Selenium Chrome (若有)...")
    _close_selenium_chrome_only()

    print("[standalone 1/2] 啟動 Chrome（用 Selenium profile）...")
    options = _build_chrome_options()
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
    except WebDriverException as e:
        print(f"[FATAL] 無法啟動 Chrome：{str(e)[:300]}")
        return None

    print(f"[standalone 2/2] 導航到 {EDOC_HOME_URL}")
    try:
        driver.get(EDOC_HOME_URL)
    except TimeoutException:
        print("      [警告] 頁面載入超時，繼續執行")

    # 給 redirect 一點時間（session 過期會被導去 login.gov.taipei 或 sso）
    time.sleep(2)
    try:
        current = driver.current_url
    except Exception as e:
        print(f"[FATAL] 讀 current_url 失敗：{type(e).__name__}: {e}")
        return None

    if "edoc.gov.taipei" not in current:
        print(f"[ERROR] 沒進到 edoc，被導向：{current}")
        print("        session 可能過期，請先執行 main.py 重新登入")
        return None

    print(f"      OK：已在 edoc — {current}")
    return driver


if __name__ == "__main__":
    # 把 stdout/stderr 同步落地到 run.log — entry point 開頭就 setup，確保 Chrome
    # 預清理 / 啟動 / 導航等每行 print 都進 log
    from taipeion_login_selenium import _setup_stdout_logging
    _setup_stdout_logging()
    driver = _standalone_open_chrome_at_edoc()
    if driver is None:
        sys.exit(1)
    ok = process_document_system(driver)
    sys.exit(0 if ok else 1)
