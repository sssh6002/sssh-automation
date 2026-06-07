# sssh-automation

臺北市松山高中資訊組自動化工具集。

## 這個工具在做什麼（大方向）

把資訊組每天在 **edoc 公文系統** 的例行操作全自動化。執行一次 `main.py`，它會依序：

1. **登入** — 用自然人憑證（讀卡機 + PIN）登入台北市單一身分驗證 → TAIPEION → edoc 公文系統。
2. **簽收** — 自動處理催辦訊息、把「待簽收」公文全選簽收。
3. **承辦中公文** — 點開公文 → 下載 zip → 解壓 → 餵 LLM 產出「總結」（含存查分類、承辦文字、動作）
   → 依總結自動填「擬辦文字」並存檔（動作=陳會才送陳會、none 則留在承辦中）。
4. **結案存查** — 迴圈處理「待結案」清單：確認主管已「如擬」核可 → 下載歸檔 →
   填存查表單（檔號／案次號）→ PIN 簽章 → 寫存查標記，直到清空。
5. **上網公告** — 結案後若該公文總結判定要「於官網公告」，自動登入校網、發佈到
   **圖書館公告**（標題=主旨、內容=條列摘要、附上公文 `*ATTCH*` 附件、置頂、
   依分類同步至「最新消息」研習資訊／處室公告）。

> 全程盡量無人值守；只有 **插卡、輸入 PIN、螢幕解鎖** 這類實體動作需要你本人。
> 下載專案後只要做兩件事就能用：**裝相依套件** + **建立 `env.env` 帳密檔**（見下方「環境需求」）。

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
│                ├─[4-1-1]─ summarize_doc.py — 解壓後鏈式呼叫,讀 summarize_doc.md 規格
│                │           餵 LLM (gemini / claude -p / anthropic SDK 依序嘗試),
│                │           對主檔 PDF 抽文字後產出「公文主檔名內容.txt」總結
│                │
│                └─[4-2]─ fill_in_draft.py — 讀總結標記 → 套 fill_in_draft.yaml 模板填辦理文字 →
│                          儲存 → 依標記決定不動作/陳會(備選動作預留)
│
├─[5]─ document_closure/document_closure.py — 結案存查:迴圈處理「待結案」清單直到清空
│       │ 確認「如擬」核可 → 下載 zip → 複製總結/內容 → 勾列 → 點存查 → 讀檔號/填表單 → 確定存檔 →
│       │ pinCode 視窗(自動填,失敗給 60s 讓使用者手動完成)→ 驗 doc_no 消失 → 寫存查標記檔
│       │ 可單獨執行:python document_closure\document_closure.py
│       │
│       ├─[5-1]─ pending_doc_handler._download_and_extract 重用(download_dir=CLOSURE_DOWNLOAD_DIR,
│       │        summarize=False),解壓到 document_download_closure/&lt;公文文號&gt;/
│       │
│       └─[5-2]─ document_closure_post_web.py — 上網公告:歸檔後若總結承辦文字含「於官網公告」,
│                登入校網→圖書館頁進「模組」編輯模式→「新增公告」填標題(主旨)/內容(條列摘要)/
│                發布者(env.env)/置頂/附件(*ATTCH*)/同步分類(研習→研習資訊,其餘→處室公告)→發布。
│                寫 <主檔名>已公告.txt 防重複。可單獨執行:python document_closure\document_closure_post_web.py <公文目錄>
│
└─[不使用]─ doc_classifier/ — 公文處置動作分類器 (獨立模組,可獨立執行)(先不用此分類器，先用summarize_doc就夠了260527)
        ├ collect_training.py — 把 # action: 已標公文同步進 training_data/
        ├ classifier.py        — 對新公文組 prompt → LLM → 寫回 # suggested_action:
        └ classifier.md        — LLM 業務規格 (改規格只動此檔)
```

其他資源:

```
scripts/close-profile2-chrome.ps1 — 關閉所有 Selenium 相關 chrome.exe + 清 lock 檔
env.env                           — 帳密設定 (PIN／校網帳密,key=value,gitignored,須手動建立)
document_download/                — 承辦中公文 zip 解壓後內容落地 (gitignored)
document_download_closure/        — 結案存查公文 zip 解壓 + 標記檔落地 (gitignored)
fill_in_draft.yaml                — 標記→辦理文字模板+動作 對應表 (人工維護,改規則只動此檔)
run.log                           — **主 LOG**:Python 端 stdout/stderr Tee 落地 (每行 ISO 8601 時間戳,>10MB 自動 rotate,保留 7 份)
chrome_browser.log                — Chrome 端 JS console.log 落地 (由 click_document.py 寫入,內容性質與 run.log 不同,故分檔)
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

### 建立 env.env(帳密設定檔)

`env.env` 為 key=value 一行一筆,`#` 開頭整行為註解。範例:

```
# 帳密設定:# 開頭整行忽略
pin=你的6位數PIN
sssh_account=校網帳號
sssh_password=校網密碼
sssh_publisher=系管師R
```

- `pin`:自然人憑證 PIN(edoc 登入用)
- `sssh_account` / `sssh_password`:松高校網帳密登入用(上網公告 document_closure_post_web)
- `sssh_publisher`:上網公告表單「發布者」欄要填的名稱(校網該欄必填、自動化開表單時為空)

> env.env 已在 .gitignore,**絕對不會推上 GitHub**。下載專案後**必須自己手動建立這個檔**並填入上述值。

## 執行方式

```powershell
C:\Python314\python.exe main.py          # 預設跑 FEATURES[0](Selenium + 點公文 + 公文系統處理)
C:\Python314\python.exe main.py 2        # 跑 FEATURES[1](pyautogui 像素版備援,只到登入)
C:\Python314\python.exe main.py 3        # 跑 FEATURES[2](Selenium + 點公文 + 結案存查)
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
6. 從 `env.env` 讀 PIN 自動填入
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

### FEATURES[2] — 自然人憑證登入 + 點公文 + 結案存查迴圈

(A)~(C) 同 FEATURES[0],只是 (C) 階段的 processor 換成 `process_document_closure`。
這是一個 **迴圈處理器**:讀左側 sidebar「待結案(N)」,N>0 就處理第一筆,
處理完重新讀 N、再處理,直到 N=0 或單筆失敗才停(`max_iterations=30` 是 runaway 保險)。

**(D′) 單筆結案存查流程(document_closure.py)**
1. 點 sidebar「待結案」+ 點清單第一筆,記下 `doc_no`
2. 切到公文閱覽器新分頁,偵測核決區是否含「如擬」— **沒有就保守跳過不下載**(可能還在簽核/意見有異),有才往下做
3. 下載 zip → 解壓 → flatten(重用 `pending_doc_handler._download_and_extract`,
   落到 `document_download_closure/<doc_no>/`,不跑 summarize)
4. 從 `document_download/<doc_no>/` 複製 `*總結*.md` + `*內容.txt` 過來
5. 若複製成功,刪掉承辦中那份重複目錄
6. 關閉公文閱覽器新分頁,切回主分頁
7. 回「待結案」清單,JS 用 column-index 精準勾選同 `doc_no` 列
8. 點清單上方「存查」按鈕,等存查表單載入(`sentinel = 「確定存檔」`)
9. 從步驟 4 複製過來的 `*總結*.md` 讀 `#存查分類` 標題下 8 位檔號
10. 把檔號填進表單「檔號」第二格
11. 選「案次號」下拉第一個 option,等系統自動填「保存年限」(只 log 供參考,不驗證)
12. verify「檔號」+「案次號」已正確填入 → 點「確定存檔」
13. KdApp `127.0.0.1:16888` 跳 pinCode popup:
    - 從 `env.env` 讀 PIN,多策略嘗試填入(JS focus / click+send_keys / 純 JS setter)
    - 自動填成功 → 點「確定」+ 等 popup 關閉
    - 自動填失敗 → 印 WARN、不中止,切回主 window 走步驟 14(給使用者手動操作的時間)
14. 驗證 `doc_no` 從「可見 `<tr>`」消失(timeout 60s,容許手動完成 PIN)
15. 寫存查標記檔 `<公文主檔名>已存查.txt` 到 `document_download_closure/<doc_no>/`,
    內容 `<ISO 8601 datetime>_存查`
16. **上網公告**(document_closure_post_web):若總結承辦文字含「於官網公告」→ 登入校網、
    發佈到圖書館公告(失敗只印 STOP、不影響已完成的歸檔;寫 `已公告.txt` 防重複)
17. 回主迴圈重新讀 待結案(N),繼續下一筆

> standalone 跑:`python document_closure\document_closure.py`,前提是 Chrome 已停在
> edoc 公文系統頁(session 過期會中止並提示去 `python main.py 3` 重登)

## 故障排除

| 症狀 | 解法 |
|------|------|
| `session not created: Chrome instance exited` | 跑 `scripts\close-profile2-chrome.ps1` 清殘留與 lock 檔 |
| Chrome 跳「要儲存密碼嗎」對話框 | 按「不用了,謝謝」一次,Chrome 會永久記住 |
| 鎖屏下執行失敗 | **正常** — Windows 鎖屏會阻擋 HiCOS 讀卡,必須未鎖定 |
| PIN 一直填不進去 | 確認 env.env 存在且含 `pin=六位數字` |
| 公文下載對話框沒被自動填路徑 | 確認 pywin32 已裝;若手動操作正常但程式失敗,先確認沒切到列印預覽分頁(會搶焦點) |
| 解壓後內容中文檔名亂碼 | Python < 3.11 不支援 `metadata_encoding`,升級 Python 到 3.11+ |
| zip 內頂層出現新的殼層資料夾名 | 把新名稱加進 `pending_doc_handler.py` 的 `_FLATTEN_CANDIDATE_NAMES` |
| 結案存查 pinCode 填不進去 | 不會中止 — 自己手動把 PIN 填完按確定,verify 階段給 60s 仍會自動寫標記檔 |
| 結案存查迴圈跑不停 | `process_document_closure` 有 `max_iterations=30` 保險;正常情況 待結案=0 就會結束 |
| 找不到 `*總結*.md` 可讀檔號 | 確認承辦中那輪 `summarize_doc` 有跑成功(`document_download/<doc_no>/` 內要有總結檔) |
| 4-2 沒填辦理文字 / 沒按鈕 | 跑 `fill_in_draft._dump_candidates` 重新鎖定選擇器;確認公文閱覽器分頁已載入辦理區 |
| 4-2 填了字但動作不對 | 檢查 `fill_in_draft.yaml` 標記與優先序;確認 `summarize_doc` 已產出總結檔(無 API key 會無標記走 default) |

## 新增功能

在 `main.py` 的 `FEATURES` 清單加入一列（4-tuple 格式）:

```python
FEATURES = [
    ("名稱", login_fn, post_login_fn, processor_fn),
    # post_login_fn: driver 轉跳目標頁（如 click_document_card），回 True 後接 processor
    # processor_fn:  實際業務邏輯（如 process_document_system、process_document_closure）
    # 第三/四欄填 None 表示跳過對應階段
]
```

也可單獨執行各模組（session 過期會提示去跑 main.py 重登）:

```powershell
C:\Python314\python.exe document_system.py                        # 公文系統全流程
C:\Python314\python.exe document_closure/document_closure.py     # 結案存查
```
