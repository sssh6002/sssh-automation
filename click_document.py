"""
click_document.py
TAIPEION 入口網儀表板 — 檢查「公文(學校)」方塊上方的待辦數字，依結果決定動作。

設計：
  - 不論待辦數為何（0、>0、判讀失敗）都點方塊進入 edoc：即使「公文(學校)=0」，
    進去後右上「催辦訊息」仍可能 > 0，需進催辦通知頁簽收（催辦與公文待辦數獨立）。
  - 點方塊 → 進入公文系統 → 呼叫 click_document() 做點選之後的後續工作

呼叫方式：
  1) 從 main.py 自然人憑證登入成功後串接：
       from click_document import click_document_card
       click_document_card(driver)   # 用既有 Selenium driver 繼續操作
  2) 單獨執行：
       C:\\Python314\\python.exe click_document.py
       → 會先呼叫 login_taipeion_selenium() 重新登入拿到 driver，再判讀數字
"""

import os
import re
import sys
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

sys.stdout.reconfigure(encoding='utf-8')

# 「公文(學校)」方塊。實測該方塊由 div 包標籤文字 + 數字計數構成，整塊都可點。
DOCUMENT_XPATHS = [
    "//a[contains(normalize-space(), '公文(學校)')]",
    "//*[normalize-space()='公文(學校)']/ancestor::a[1]",
    "//*[normalize-space()='公文(學校)']/ancestor::*[@role='link' or @role='button'][1]",
    "//*[normalize-space()='公文(學校)']/ancestor::div[contains(@class, 'card') or contains(@class, 'tile') or contains(@class, 'block')][1]",
    "//*[normalize-space()='公文(學校)']",
    "//*[contains(normalize-space(), '公文(學校)')]",
]

# 「公文(學校)」label 元素本身（用來定位後再從附近找數字）。
DOCUMENT_LABEL_XPATH = "//*[normalize-space()='公文(學校)']"


def _ensure_driver(driver):
    """若 driver=None，自動呼叫 login_taipeion_selenium 重新登入取得 driver。
    回傳 driver 或 None（登入失敗）。"""
    if driver is not None:
        return driver
    print("[click_document] 未提供 driver，先呼叫 login_taipeion_selenium 取得登入 session...")
    from taipeion_login_selenium import login_taipeion_selenium
    driver = login_taipeion_selenium(return_driver=True)
    if driver is None:
        print("[ERROR] 登入失敗，無法處理公文")
    return driver


def _get_document_count(driver, timeout=10):
    """讀『公文(學校)』方塊上方的待辦數字。
    回傳：
        int >= 0 → 判讀成功
        -1       → 找不到 label 或無法 parse 數字（保守視為「不確定」）
    """
    wait = WebDriverWait(driver, timeout)
    try:
        label_el = wait.until(EC.presence_of_element_located((By.XPATH, DOCUMENT_LABEL_XPATH)))
    except TimeoutException:
        print("[WARN] 找不到『公文(學校)』label，無法判讀數字")
        return -1

    # 數字可能在：label 的父容器內的兄弟節點、祖父容器內、或同 card 內某個 span/div。
    # 由近到遠掃描，找第一個純數字（容許千分位逗號）的可見元素。
    relative_xpaths = [
        "./parent::*/*[self::span or self::div or self::strong or self::b or self::p]",
        "./parent::*/parent::*//*[self::span or self::div or self::strong or self::b or self::p]",
        "./ancestor::*[self::a or self::div][1]//*[self::span or self::div or self::strong or self::b or self::p]",
    ]
    seen_ids = set()
    for rel_xp in relative_xpaths:
        try:
            els = label_el.find_elements(By.XPATH, rel_xp)
        except Exception:
            continue
        for el in els:
            try:
                # 避開把 label 本身當數字
                el_id = el.id if hasattr(el, "id") else id(el)
                if el_id in seen_ids:
                    continue
                seen_ids.add(el_id)
                if not el.is_displayed():
                    continue
                txt = (el.text or "").strip()
                if not txt or txt == "公文(學校)":
                    continue
                m = re.fullmatch(r"[\d,]+", txt)
                if m:
                    n = int(txt.replace(",", ""))
                    print(f"      OK：讀到公文(學校) 待辦數 = {n}（來源文字「{txt}」）")
                    return n
            except Exception:
                continue

    print("[WARN] 找到『公文(學校)』label 但附近沒有純數字元素，無法判讀")
    return -1


def _click_document_card(driver, timeout=8):
    """點『公文(學校)』方塊：取 <a> 的 href 後用 driver.get 同分頁導航。

    為何不直接 .click() / execute_script click()：實測那會觸發 JS window.open
    開新分頁，而 chromedriver 對 JS 觸發的新分頁有 bug——之後任何 W3C window
    端點（window_handles / switch_to.window）都會永遠不返回（read timeout=120s）。
    改成抓 href 後 driver.get(href) 在同分頁導航，整個 W3C window bug 繞掉。
    若元素沒有 href（純 JS 處理），fallback 回原本 click 行為（並警告）。
    回傳是否成功觸發導航。
    """
    wait = WebDriverWait(driver, timeout)
    for xp in DOCUMENT_XPATHS:
        # 先找元素並抓 href（這部分的 TimeoutException 才是「找不到元素」）
        try:
            el = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
            if not el.is_displayed():
                continue
            href = el.get_attribute("href")
        except TimeoutException:
            continue
        except Exception as e:
            print(f"      x  公文(學校) 方塊元素例外：{type(e).__name__}: {e}")
            continue

        if href:
            print(f"      OK：找到 公文(學校) 方塊（XPath: {xp}），href={href[:80]}...")
            # 同分頁導航。edoc 載入時會跳 Chrome 站台權限對話框擋住 DOMContentLoaded，
            # eager strategy 也等不到，會在 page_load_timeout（預設 15s）後丟 TimeoutException。
            # 這是預期行為——導航已經觸發，後面 click_document() 會點允許解鎖頁面，所以
            # 把這個 timeout 視為成功，繼續走流程。
            try:
                driver.get(href)
                print(f"      OK：同分頁導航至公文系統")
            except TimeoutException:
                print(f"      OK：導航已觸發，eager 載入被允許對話框擋住（預期）— 交給後續 click_document 點允許")
            return True
        # 沒 href 才 fallback click — 會踩 W3C window bug，但至少有作動
        print(f"      [WARN] {xp} 元素無 href，fallback 用 JS click（可能會踩到 chromedriver 新分頁 bug）")
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)
            print(f"      OK：點到 公文(學校) 方塊（XPath: {xp}）")
            return True
        except Exception as e:
            print(f"      x  公文(學校) 方塊 click 例外：{type(e).__name__}: {e}")
            continue
    print("[ERROR] 公文(學校) 方塊 全部 XPath 都失敗")
    return False


def click_document(driver):
    """『公文(學校)』方塊點選之後的後續工作。

    目前：等 edoc 載入 → 用 pyautogui 點 Chrome 站台權限「允許」按鈕 → 印 URL/標題。
    未來：在此函式內擴充後續流程（瀏覽未讀公文、批次處理等）。

    流程設計：_click_document_card 已改成 driver.get(href) 同分頁導航，因此這裡
    不需切窗。但 edoc.gov.taipei 載入時還是會跳 Chrome 站台權限對話框（要求存取
    本地簽章元件），仍需用 pyautogui 點掉。
    """
    # driver.get 已返回（page_load_strategy=eager 等到 DOMContentLoaded），給點時間
    # 讓對話框出現。_click_chrome_allow_button 內部還有 0.5s anchor，所以這裡 1s 即可
    time.sleep(1)

    # 點 Chrome 站台權限對話框「允許」。對話框錨點為 URL bar 左下；座標與登入頁
    # 那顆相同，直接重用 _click_chrome_allow_button。授權後 Chrome 把同 origin
    # 記在 profile 內，下次不再跳，動作冪等（沒對話框時點空地也無傷）。
    print("[click_document] 點 Chrome 站台權限對話框「允許」按鈕（edoc 公文系統需要存取本地簽章元件）...")
    from taipeion_login_selenium import _click_chrome_allow_button
    # 傳入 driver — _click_chrome_allow_button 內 _bring_chrome_to_foreground 要靠
    # driver.service.process.pid 走 process tree 過濾出 Selenium 控制的 Chrome；
    # 不傳的話會誤抓 VSCode（Electron app，class 同為 Chrome_WidgetWin_1）。
    _click_chrome_allow_button(driver)

    # 允許按掉後讓頁面收尾（後續只 print URL/title + dump console log，不需太久）
    time.sleep(0.5)
    try:
        print(f"[click_document] 當前 URL：{driver.current_url}")
        print(f"[click_document] 當前標題：{driver.title}")
    except Exception as e:
        print(f"[click_document] 讀狀態失敗：{type(e).__name__}: {e}")

    # dump 瀏覽器 console log — 簽章元件 (TCGServiSign on https://127.0.0.1:5642x)
    # 若被 Chrome PNA / mixed content / CORS 擋會在 console 印錯誤；存檔下次方便比對。
    # 需要 Selenium options 內有 set_capability("goog:loggingPrefs", {"browser":"ALL"})
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_browser.log")
    try:
        entries = driver.get_log("browser")
        with open(log_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(f"{e.get('level','?')}\t{e.get('source','?')}\t{e.get('message','')}\n")
        print(f"[click_document] 瀏覽器 console → {log_path}（共 {len(entries)} 則）")
    except Exception as e:
        print(f"[click_document] dump console log 失敗：{type(e).__name__}: {e}")

    # TODO: 點選之後的後續工作在此擴充
    print("[完成] 公文後續工作流程結束。")
    return True


def click_document_card(driver=None):
    """主入口：讀『公文(學校)』方塊上方的待辦數（僅供 log），不論結果都點方塊進入 edoc。

    即使待辦數 = 0 仍要進去 —— 進 edoc 後右上「催辦訊息」可能 > 0，需進催辦通知頁
    簽收（催辦與公文待辦數獨立計算）。判讀失敗（-1）一樣嘗試進入，把實際狀態交給
    下游 process_document_system 判斷。

    參數：
        driver: 既有 Selenium WebDriver；若為 None 則自動呼叫 login_taipeion_selenium 重新登入。
    回傳：
        True 表示已點方塊並進入後續工作；False 表示點擊失敗（沒進到 edoc）。
    """
    driver = _ensure_driver(driver)
    if driver is None:
        return False

    print("[click_document_card] 等儀表板載入後讀『公文(學校)』待辦數...")
    count = _get_document_count(driver)

    if count > 0:
        print(f"[click_document_card] 公文(學校) 待辦數 = {count}，點方塊進入公文系統...")
    elif count == 0:
        print("[click_document_card] 公文(學校) 待辦數 = 0，仍點方塊進入 edoc 檢查催辦訊息...")
    else:
        print("[click_document_card] 無法判讀公文(學校) 待辦數，仍嘗試點方塊進入 edoc...")

    if not _click_document_card(driver):
        print("[click_document_card] 點擊失敗 — 列印目前頁面狀態以利除錯：")
        try:
            print(f"      URL：{driver.current_url}")
            print(f"      標題：{driver.title}")
        except Exception:
            pass
        return False

    # 點選之後才呼叫 click_document 做後續工作
    return click_document(driver)


if __name__ == "__main__":
    from ime_utils import ensure_english_ime
    ensure_english_ime()  # 起手式:把輸入法切回英文
    click_document_card()
