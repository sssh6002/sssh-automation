# document_closure_post_web 設計

日期：2026-06-03
分支：`feat/closure-post-web`

## 目標

結案存查流程下載並解壓公文後，若該公文目錄的 `*總結.*.md`「承辦文字」含「於官網公告」，
則自動把公文發佈到松高校網（www.sssh.tp.edu.tw）：以總結的**主旨**為標題、主旨下方的**條列摘要**為內容。

## 觸發鏈位置

```
document_closure.py  _process_one_pending_closure_doc()
  └─ _download_and_extract() + _copy_meta_files_from_pending()   ← 既有
       └─[新]─ document_closure_post_web.maybe_post_announcement(driver, extract_dir)
```

`maybe_post_announcement` **不 raise**；任何失敗只印 STOP banner、回 `False`，
**絕不影響已完成的存查歸檔**（與 `fill_in_draft` 容錯哲學一致）。觸發條件不符回 `False`（skipped，非錯誤）。

## 模組結構：document_closure/document_closure_post_web.py

```
maybe_post_announcement(driver, extract_dir)
  ├─[1] _parse_summary(extract_dir)        讀 *總結.*.md → {承辦文字, 主旨, body}
  ├─[2] 觸發判定：承辦文字 含「於官網公告」？否→return False(skipped)
  ├─[3] 防重複：extract_dir 已有 <主檔名>已公告.txt？是→skip 回 True
  ├─[4] title/body 取自 [1]
  ├─[5] _open_and_login_sssh(driver)       開新分頁 + SSO 登入（已登入則跳過）
  ├─[6] _submit_announcement(driver, title, body)  到 library 頁填表送出
  ├─[7] 驗證發佈成功
  ├─[8] _write_posted_marker(extract_dir)  寫 <主檔名>已公告.txt（ISO 8601）
  └─[9] 關新分頁、切回 edoc 主分頁
```

### 單獨執行
`python document_closure/document_closure_post_web.py <公文目錄>`
只對**指定的那一個公文目錄**跑（讀該目錄 `*總結.*.md`、判觸發、發佈）；**不做全目錄 scan**。
未給目錄參數 → 印用法後結束。standalone 時自己 attach Chrome（沿用 `_standalone_open_chrome_at_edoc` 套路）。

## §2 總結.md 解析與觸發

實際格式（`document_download_closure/MWAA*/...總結.*.md`）：
```
#存查分類:研習 03750401      ← 第1行
##不參加                      ← ## 承辦文字（觸發判定看這行）
*發文日期：…
*發文字號：…
*主旨：本協會訂於…請查照。     ← 標題來源
1. …                          ← body 來源（主旨下方條列摘要）
2. …
```

- **承辦文字**：第一個 `^##`（非 `###`）開頭的行，去 `##` 與前後空白。
- **觸發**：承辦文字含子字串「於官網公告」(`in` 比對，容忍「於官網公告，並轉知各處室及師生」「於官網公告，不參加研習課程」等變體)。
- **標題 title**：找 `主旨[:：]` 的行，取冒號後文字 `strip()`（兼容有無 `*` 前綴、半/全形冒號）。
- **內容 body**：主旨行之後、檔尾之前，取 `^\s*\d+[.、]` 開頭的條列行，換行串接。
- **取不到時**：找不到 `*總結.*.md`、解析不到主旨/body → 印 STOP banner、回 False、不發佈（寧缺勿殘）。
  解析不到 `##` 承辦文字 → 視為不觸發（skipped）。

## §3 登入／發佈／帳密

### (a) 帳密檔：env.env（已完成）
`id.txt` 已改名為 `env.env`，升級為 key=value、`#` 註解：
```
# 帳密設定：# 開頭整行忽略
pin=自然人憑證PIN
sssh_account=校網帳號
sssh_password=校網密碼
```
- `taipeion_login_selenium._read_config(key)`：逐行 key=value 解析（# 註解、空行略過）。
- `_read_pin()`：先找 `pin=`；若整檔無 key=value（舊格式）則整檔當 PIN（向後相容）。
- `env.env` 已在 .gitignore。**真實 `sssh_account` / `sssh_password` 由使用者自行填入。**

### (b) SSO 登入 `_open_and_login_sssh(driver)`
1. 開新分頁、導到 `https://www.sssh.tp.edu.tw/passport/tpeEntrance`。
2. 先偵測是否已登入（同 session 第 2 篇起跳過）：導 library 頁，若沒被導回登入頁 → 已登入回 True。
3. 否則用 XPath 候選 + JS fallback 找 帳號框 / 密碼框 / 登入鈕，填 `sssh_account` / `sssh_password` 送出。
4. 驗證登入成功（URL 離開 passport / 能開 library 頁）。失敗→STOP banner 回 False。
- 真實欄位 selector **實作階段以使用者帳密實機探索 DOM 後再定**。

### (c) 發佈 `_submit_announcement(driver, title, body)`
- 導到 `https://www.sssh.tp.edu.tw/nss/s/main/p/library` → 找「新增公告」入口 → 填標題框、
  內容編輯器（可能為 rich-text/CKEditor，需 JS 注入或切 iframe）→ 視需要選分類 → 送出。
- 真實表單結構 **實機探索後再定 selector 與送出流程**。
- **送出是對真實校網的不可逆外部動作**：送出前印 banner 列 title；自動化跑通前先在使用者監看下實測一篇確認無誤。

## §4 防重複／log／測試

- **防重複**：`_write_posted_marker(extract_dir)` 寫 `<主檔名>已公告.txt`，內容為 `ISO 8601_已公告`
  （比照 `_write_archive_marker` / `已存查.txt`）。`maybe_post_announcement` 開頭偵測到此檔即 skip。
- **log**：所有輸出走既有 run.log（stdout Tee）。每階段印 OK / STOP banner，遵守 ISO 8601 開頭規則由既有 logging 機制處理。
- **測試**：
  - 純函式單元測（不需瀏覽器）：`_parse_summary` 對多種 `*總結.*.md`（含/不含「於官網公告」、有無 `###`、半/全形冒號）解析出正確 title/body/觸發判定。用 `document_download_closure/` 既有檔 + 構造 fixture。
  - selector / 登入 / 發佈：實機跑（提交前依規則實測填字點擊驗證，不只 import）。

## 不做（YAGNI）
- 不做全目錄 scan 的批次發佈。
- 不做公告分類/標籤的智慧推斷（除非實機表單必填，屆時取最簡預設）。
- 不做圖片/附件上傳。
