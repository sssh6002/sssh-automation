# document_system.py — 公文系統處理模組設計

**日期**：2026-05-19
**狀態**：草案（待使用者審閱）

## 目的

把 edoc.gov.taipei 公文系統內的操作獨立成一個模組，與「登入 / 點公文方塊 / 點允許對話框」這些 bootstrap 流程分離。

第一版只做一個動作：點選 edoc 首頁右上方的「催辦訊息」badge。後續可在同一模組擴充「點催辦清單進公文」、「逐筆檢視」、「分類統計」等。

同時要做到「日後可單獨跑 `python document_system.py` 測試，不用每次都重跑登入 + 點公文方塊的長流程」。

## 非目標（YAGNI）

- 不做簽收 / 送件等會改變公文系統狀態的動作
- 不做下載 PDF / 附件
- 不做 session 過期時自動重登（過期就提示去跑 main.py）
- 不對接 DevTools port hybrid bootstrap

## 架構

```
main.py
 ├─ _close_selenium_chrome_only()       既有
 ├─ login_taipeion_selenium()           既有
 └─ click_document_card(driver) → bool  既有，回 True 表示已點進公文系統
    └─ True 時才接：
       document_system.process_document_system(driver)   新增
```

`document_system.py` 同時可獨立執行：

```
python document_system.py
 ├─ _close_selenium_chrome_only()           reuse from main.py
 ├─ _standalone_open_chrome_at_edoc()       新增
 │  ├─ 用 taipeion_login_selenium._build_chrome_options() 拿 options
 │  ├─ 開 Chrome 用同一個 Selenium profile
 │  └─ driver.get(EDOC_HOME_URL)；若被導去 login → 印「session 過期請跑 main.py」並 exit
 └─ process_document_system(driver)
```

## 元件介面

### `document_system.py`

| 函式 | 簽章 | 用途 |
|---|---|---|
| `process_document_system(driver)` | `(WebDriver) -> bool` | 主入口。假設 driver 已在 edoc 首頁。回 True 表示流程跑完 |
| `_click_urgent_message(driver, timeout=10)` | `(WebDriver, int) -> bool` | 內部 helper：找「催辦訊息」並 JS click |
| `_standalone_open_chrome_at_edoc()` | `() -> WebDriver \| None` | 僅 `__main__` 用：開 Chrome → 導航 edoc → 確認沒被踢回 login |

常數：
- `EDOC_HOME_URL = "https://edoc.gov.taipei/tcqb/home/default.jsp?inLine=Y"`
- `URGENT_MSG_XPATHS = [...]` — 數個 fallback XPath（容錯設計同 click_document）

### `taipeion_login_selenium.py` 改動

新增 `_build_chrome_options() -> Options`：把原本散落在 `login_taipeion_selenium()` 內的 options 設定（user-data-dir、PNA/LNA disable-features、allow-running-insecure-content、eager、detach、excludeSwitches、unhandledPromptBehavior、goog:loggingPrefs 等）抽出來。原 `login_taipeion_selenium()` 改呼叫此函式。`document_system._standalone_open_chrome_at_edoc()` 也呼叫此函式，保證兩邊 options 一致。

### `main.py` 改動

`main()` 內：

```python
else:
    driver = func(return_driver=True)
    if driver is None:
        print("[ERROR] 登入未完成，跳過後續動作。")
    else:
        cont = post_login(driver)  # click_document_card：True 表示已點進公文系統
        if cont:
            from document_system import process_document_system
            process_document_system(driver)
```

`FEATURES[0]` 第三欄維持 `click_document_card`（不改）。document_system 的呼叫寫死在 main()，因為它是 click_document_card 之後固定的下一步，不需要再做成可配置的 post_post_login。

## 資料流

```
登入完成 → driver at TAIPEION dashboard
   ↓
click_document_card：判讀待辦 → > 0 → driver.get(href) 同分頁導航 → click_document：點允許 → dump console log → return True
   ↓
process_document_system：
   1. 確認 current_url 包含 edoc.gov.taipei（否則回 False）
   2. _click_urgent_message：JS click「催辦訊息」
   3. sleep 2 → 印點完後的 URL / title
   4. return True
```

Standalone 路徑差異：跳過前兩步，直接從 driver.get(EDOC_HOME_URL) 開始；若 redirect 到 login.gov.taipei → 印錯誤訊息退出。

## 錯誤處理

| 情境 | 行為 |
|---|---|
| 串接：click_document_card 回 False（待辦=0、判讀失敗、點擊失敗） | main.py 不呼叫 process_document_system |
| 串接：process_document_system 內 current_url 不是 edoc | 印 `[ERROR] 當前 URL 不在 edoc：...`，回 False |
| 串接：找不到「催辦訊息」XPath | 印 `[ERROR] 「催辦訊息」全部 XPath 都失敗`，回 False |
| Standalone：Chrome 啟動失敗（profile 被其他 Chrome 鎖、chromedriver 版本不合） | `_close_selenium_chrome_only` 先預清理；仍失敗則印 `[FATAL]` + `exit(1)` |
| Standalone：session 過期，edoc 把我們踢回 login | 印「session 過期，請先跑 main.py 重新登入」+ `exit(1)` |

## 測試策略

無 unit test framework（既有專案沒導入）。驗證手段：

1. **語法** `python -m py_compile document_system.py taipeion_login_selenium.py main.py`
2. **串接** `python main.py` 跑完整流程，看是否點到催辦訊息 + 印對 URL
3. **Standalone** `python document_system.py` 在剛跑完 main.py（session 還新鮮）後執行，看是否直接進 edoc + 點到催辦訊息
4. **Standalone session 過期** 等久一點再跑，看是否正確印「session 過期」並退出（不要卡死）

## 風險與未知

- 「催辦訊息」的真實 DOM 結構未知 — XPath 寫好幾個 fallback，但若全失敗需要使用者打開 DevTools 確認實際結構並回報，再補 XPath。
- TAIPEION session 過期時間未知 — 觀察後可能要在文件補上「session 大約 X 小時內有效」。
- 點完催辦訊息會跳到哪個頁面未知（新分頁？同分頁？modal？）— 第一版只印 URL/title 觀察，不嘗試後續動作。

## 後續迭代候選（不在本次範圍）

- 解析催辦清單，列出每筆催辦的標題 / 期限
- 逐筆點進催辦的公文
- 抓公文正文 / 附件
- 標記已讀 / 簽收
