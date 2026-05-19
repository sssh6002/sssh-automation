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

    流程（第一版）：
        1. 確認 current_url 在 edoc.gov.taipei
        2. 點「催辦訊息」
        3. sleep 2 等頁面反應，印當前 URL/title 觀察
    回傳 True 表示流程跑完；False 表示前置檢查或點擊失敗。
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

    print("[document_system] 點選「催辦訊息」...")
    if not _click_urgent_message(driver):
        return False

    # 等頁面反應，觀察點完去到哪
    time.sleep(2)
    try:
        print(f"[document_system] 點完後 URL：{driver.current_url}")
        print(f"[document_system] 點完後標題：{driver.title}")
    except Exception as e:
        print(f"[document_system] 讀狀態失敗：{type(e).__name__}: {e}")

    # TODO: 後續工作（讀催辦清單、逐筆點進公文等）在此擴充
    print("[完成] 公文系統處理流程結束。")
    return True


if __name__ == "__main__":
    print("[ERROR] standalone 入口尚未實作（Task 4 才加）")
    sys.exit(1)
