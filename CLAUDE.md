# sssh-automation — Claude Code 規則與架構

## 對話輸出格式

對話的輸出完後，換行加上「引言區塊（Markdown `>` 語法，左側有色條）」，內容為粗體：

**輸出結束 LDC 此輸出設定於 C:\Users\ldc\Documents\GitHub\sssh-automation\CLAUDE.md**

## 操作規則

- 程式產生的截圖（`result.png` / `final_state.png` / `*.png`），確認不再使用後**立即刪除**
- 主動使用 superpowers plugin 中可用的 SKILL
- 新功能加完優先用 standalone 入口驗證：`python document_system.py` 或 `python pending_doc_handler.py`（跳過 login，直接從 edoc 開始）。改動 login 流程才需要跑 `main.py` 完整測

## 機密

- `id.txt` 是自然人憑證 PIN，**嚴禁推上 GitHub**（已 .gitignore）
- `*.log`、`*.png` 已 .gitignore，不要 `git add`

## 目前架構

```
main.py                                    ← 進入點（預設 FEATURES[0]，Selenium 版）
│
├─[1]─ taipeion_login_selenium.py           自然人憑證登入（Selenium 主版本）
│       └─ 讀 id.txt 取 PIN
│
├─[2]─ click_document.py                    儀表板讀「公文(學校)」待辦數 + 點方塊進 edoc
│
└─[3]─ document_system.py                   edoc 公文系統處理
        process_document_system(driver)：
        ├─ 催辦訊息  → > 0 才點入
        ├─ 待簽收    → > 0 才點入 + 全選 + 簽收
        └─ _run_sidebar_cascade（承辦中 → 受會案件 → 待結案，取第一個 > 0）
              ├─ 承辦中 > 0：pending_doc(driver)
              │     ├─ 切到清單 frame + 點公文文號最上方 link（三策略合一 JS）
              │     └─ pending_doc_handler.handle_opened_document(driver)
              │           └─ 切到公文閱覽器新分頁 → TODO 後續動作
              ├─ 受會案件 > 0：circulate_doc(driver)         ← stub
              ├─ 待結案 > 0：pending_closeout_doc(driver)    ← stub
              └─ 三項都 0 → 印「無公文待處理」

輔助 / 替代：
  browser_utils.py                    共用瀏覽器 helper
  taipeion_login.py                   pyautogui 像素點擊版（FEATURES[1]，少用）
  scripts/close-profile2-chrome.ps1   Chrome 預清理

自動產出（已 .gitignore）：
  run.log                  stdout/stderr 落地（每次跑會覆寫）
  chrome_browser.log       click_document 結束時 dump 瀏覽器 console
  final_state.png          login 流程結束截圖
```
