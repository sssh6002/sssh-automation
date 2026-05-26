# sssh-automation

臺北市松山高中資訊組自動化工具集。

## 專案結構

```
main.py — 主程式入口,依 FEATURES 清單跑指定功能;跑完即退出回 shell
│
├─[1]─ taipeion_login_selenium.py — 自然人憑證登入 (Selenium DOM 版,主力)
│       └ 同時是其他模組的工具庫 (_build_chrome_options / _setup_stdout_logging /
│         _close_selenium_chrome_only / _grant_clipboard_permission 等)
│
├─[2]─ taipeion_login.py — 自然人憑證登入 (pyautogui 像素點擊版,備援)
│       └─[2-1]─ browser_utils.py — Chrome 視窗找位 + 螢幕像素群集偵測
│
├─[3]─ click_document.py — TAIPEION 入口網點「公文」方塊,跳轉到 edoc 公文系統
│
├─[4]─ document_system.py — edoc 公文系統入口:催辦/待簽收/承辦中/受會案件/待結案 cascade
│       │
│       └─[4-1]─ pending_doc_handler.py — 承辦中公文點開、公文閱覽器新分頁開啟後的處理:
│                │ 點下載 → Win32 接管 KdApp JFileChooser → 解壓 zip (cp950 中文編碼) →
│                │ flatten 內層「公文/來文」殼層 → 刪 zip 原檔
│                │
│                └─[4-1-1]─ summarize_doc.py — 解壓後鏈式呼叫,讀 summarize_doc.md 規格
│                            餵 LLM (gemini / claude -p / anthropic SDK 依序嘗試),
│                            對主檔 PDF 抽文字後產出「公文主檔名內容.txt」總結
│
└─[5]─ doc_classifier/ — 公文處置動作分類器 (獨立模組,可獨立執行)
        ├ collect_training.py — 把 # action: 已標公文同步進 training_data/
        ├ classifier.py        — 對新公文組 prompt → LLM → 寫回 # suggested_action:
        └ classifier.md        — LLM 業務規格 (改規格只動此檔)
```

其他資源:

```
scripts/close-profile2-chrome.ps1 — 關閉所有 Selenium 相關 chrome.exe + 清 lock 檔
id.txt                            — 自然人憑證 PIN (gitignored,須手動建立)
document_download/                — 公文 zip 解壓後內容落地 (gitignored)
run.log                           — 主程式 stdout/stderr 落地 (每行 ISO 8601 時間戳,>10MB 自動 rotate)
```

## 環境需求

- Windows 10(**必須在螢幕未鎖定狀態執行** — Windows 鎖屏會阻擋 HiCOS 讀卡)
- Python 3.14(路徑:`C:\Python314\`)
- Google Chrome
- HiCOS 自然人憑證跨平台元件 + 讀卡機 + 卡片
- 公文系統 KdApp 本地元件(已隨 edoc 安裝;監聽 `http://127.0.0.1:16888`)

### 安裝相依套件

```powershell
C:\Python314\python.exe -m pip install pillow pyautogui selenium pywin32 pyyaml
```

- `pillow` / `pyautogui`:備援像素版登入 (FEATURES[1]) 用
- `selenium`:主力 DOM 版登入 + 公文系統流程
- `pywin32`:公文閱覽器下載 zip 時,接管 KdApp 的「匯出公文資料」對話框 (Java Swing JFileChooser)
- `pyyaml`:doc_classifier 讀 actions.yaml 動作清單

Selenium Manager 會自動下載匹配的 ChromeDriver,不需手動安裝。

### 建立 id.txt(PIN 檔)

```powershell
"你的6位數PIN" | Out-File -FilePath id.txt -Encoding utf8 -NoNewline
```

> id.txt 已在 .gitignore,**絕對不會推上 GitHub**。

## 執行方式

```powershell
C:\Python314\python.exe main.py          # 預設跑 FEATURES[0](Selenium + 點公文 + 公文系統處理)
C:\Python314\python.exe main.py 2        # 跑 FEATURES[1](pyautogui 像素版備援,只到登入)
```

執行前會 `taskkill /F /IM chrome.exe` 清掉所有 Chrome(含使用者個人 Chrome),跑完即退出回 shell。

### 階段測試入口

各階段也可獨立跑,在 Chrome 已就位的狀態下從中段開始(`session 過期會提示去跑 main.py 重登`):

```powershell
C:\Python314\python.exe document_system.py        # 從 edoc 公文系統入口往下跑
C:\Python314\python.exe pending_doc_handler.py    # 同 document_system 路徑,只是訊號燈擺在閱覽器分頁
```

## 功能說明

### FEATURES[0] — 自然人憑證登入 + 點公文 + 公文系統處理

完整流程(約 15 秒 + 公文系統處理時間):

**(A) 登入階段(taipeion_login_selenium.py)**
1. 啟動 Chrome 專用 profile(`%LOCALAPPDATA%\Chrome-Selenium`)
2. 開 `https://login.gov.taipei/login.php`
3. 預先點 Chrome 站台權限對話框「允許」(首次使用需此步,Chrome 會記住)
4. 點 自然人憑證 分頁
5. 若卡片偵測未完成(出現「重新檢測 / 重新偵測卡片」),自動點重試直到「登入」出現
6. 從 `id.txt` 讀 PIN 自動填入
7. 點「登入」送出
8. 跳轉到 `https://taipeion.gov.taipei/`(TAIPEION 入口網)

**(B) 點公文(click_document.py)**
9. 在 TAIPEION 入口網找「公文系統」方塊,JS click 跳轉到 `https://edoc.gov.taipei/...`

**(C) 公文系統 cascade(document_system.py)**
10. 讀「催辦訊息」數字 > 0 則進入催辦頁
11. 讀左側 sidebar「待簽收(N)」> 0 則點入 + 自動全選 checkbox + 點「簽收」按鈕
12. 依序檢查「承辦中 → 受會案件 → 待結案」,第一個有待辦的就點入並呼叫對應 handler
13. 承辦中流程點承辦清單最上方公文後,新分頁開啟 → 交給 pending_doc_handler

**(D) 公文閱覽器內動作(pending_doc_handler.py)**
14. 切到公文閱覽器新分頁
15. 點 toolbar 下載按鈕(`#packageBtn`,Sencha Touch 用 `Ext.fireAction('tap')`)
16. 等 KdApp 開「匯出公文資料」對話框出現
17. Win32 `EnumWindows` 找對話框 → `SetForegroundWindow` → `VkKeyScan` 模擬鍵盤填路徑 + Enter
18. 等 zip 落到 `document_download/` (size 穩定 1s+,排除 `.tmp`/`.part`/無副檔名)
19. 用 `zipfile(metadata_encoding='cp950')` 解中文檔名亂碼 → 解到 `document_download/<公文文號>/`
20. Flatten 內層「公文」/「來文」殼層 → 刪 zip 原檔

### FEATURES[1] — 自然人憑證登入(pyautogui 像素版,舊版)

保留作為備援。透過 Chrome 視窗截圖 + 青綠色像素群集偵測點擊位置。只跑到登入完成,不接後續公文流程。

## 故障排除

| 症狀 | 解法 |
|------|------|
| `session not created: Chrome instance exited` | 跑 `scripts\close-profile2-chrome.ps1` 清殘留與 lock 檔 |
| Chrome 跳「要儲存密碼嗎」對話框 | 按「不用了,謝謝」一次,Chrome 會永久記住 |
| 鎖屏下執行失敗 | **正常** — Windows 鎖屏會阻擋 HiCOS 讀卡,必須未鎖定 |
| PIN 一直填不進去 | 確認 id.txt 存在且內容為純 PIN 數字(無換行) |
| 公文下載對話框沒被自動填路徑 | 確認 pywin32 已裝;若手動操作正常但程式失敗,先確認沒切到列印預覽分頁(會搶焦點) |
| 解壓後內容中文檔名亂碼 | Python < 3.11 不支援 `metadata_encoding`,升級 Python 到 3.11+ |
| zip 內頂層出現新的殼層資料夾名 | 把新名稱加進 `pending_doc_handler.py` 的 `_FLATTEN_CANDIDATE_NAMES` |

## 新增功能

在 `main.py` 的 `FEATURES` 清單加入一列:

```python
FEATURES = [
    ("自然人憑證登入 + 點公文(Selenium 版)", login_taipeion_selenium, click_document_card),
    ("自然人憑證登入(pyautogui 像素版)", login_taipeion, None),
    ("新功能名稱", 新功能函式, 後續動作 or None),   # ← 加在這裡
]
```

第三欄填 None 表示「只跑主函式」;填函式則「主函式回 driver,再 chain 呼叫後續動作」。
